// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Local PCM processing only. No endpoint, network, audio device, or persistent state.
use crate::BindingError;
use sonora::config::{AdaptiveDigital, EchoCanceller, GainController2, HighPassFilter, NoiseSuppression};
use sonora::{AudioProcessing, Config, StreamConfig};
use std::sync::{Arc, Mutex};

#[derive(Clone, Copy, Debug, uniffi::Record)]
pub struct AudioDspConfig {
    pub capture_channels: u8,
    pub render_channels: u8,
    pub echo_cancellation: bool,
    pub noise_suppression: bool,
    pub automatic_gain: bool,
}

impl AudioDspConfig {
    fn validate(self) -> Result<Self, BindingError> {
        if !(1..=2).contains(&self.capture_channels) || !(1..=2).contains(&self.render_channels) {
            return Err(BindingError::InvalidInput);
        }
        Ok(self)
    }
    fn processor(self) -> AudioProcessing {
        let config = Config {
            echo_canceller: self.echo_cancellation.then(EchoCanceller::default),
            noise_suppression: self.noise_suppression.then(NoiseSuppression::default),
            high_pass_filter: (self.echo_cancellation || self.noise_suppression || self.automatic_gain)
                .then(HighPassFilter::default),
            gain_controller2: self.automatic_gain.then(|| GainController2 {
                adaptive_digital: Some(AdaptiveDigital::default()),
                ..Default::default()
            }),
            ..Default::default()
        };
        AudioProcessing::builder()
            .config(config)
            .capture_config(StreamConfig::new(48_000, u16::from(self.capture_channels)))
            .render_config(StreamConfig::new(48_000, u16::from(self.render_channels)))
            .build()
    }
}

struct Processor {
    config: AudioDspConfig,
    apm: AudioProcessing,
}

/// Platform admission and exact source ownership surround this synchronous local processor.
/// PCM has a fixed 48 kHz signed-16 little-endian profile; there is no resampler or padding.
#[derive(uniffi::Object)]
pub struct MediaAudioProcessor {
    state: Mutex<Option<Processor>>,
}

fn samples(data: &[u8], frames: usize, channels: u8) -> Result<Vec<i16>, BindingError> {
    if data.len() != frames * usize::from(channels) * 2 {
        return Err(BindingError::InvalidInput);
    }
    Ok(data.chunks_exact(2).map(|v| i16::from_le_bytes([v[0], v[1]])).collect())
}

#[uniffi::export]
impl MediaAudioProcessor {
    #[uniffi::constructor]
    pub fn new(config: AudioDspConfig) -> Result<Arc<Self>, BindingError> {
        let config = config.validate()?;
        Ok(Arc::new(Self { state: Mutex::new(Some(Processor { config, apm: config.processor() })) }))
    }

    /// Exactly one already captured 20 ms frame. Preserve sample count and original media clock.
    pub fn capture_pcm(&self, data: Vec<u8>, delay_ms: u16) -> Result<Vec<u8>, BindingError> {
        if delay_ms > 500 { return Err(BindingError::InvalidInput); }
        let mut state = self.state.lock().map_err(|_| BindingError::Worker)?;
        let owned = state.as_mut().ok_or(BindingError::PermissionDenied)?;
        let mut input = samples(&data, 960, owned.config.capture_channels)?;
        let mut output = vec![0i16; input.len()];
        let half = 480 * usize::from(owned.config.capture_channels);
        for (src, dst) in input.chunks_exact(half).zip(output.chunks_exact_mut(half)) {
            owned.apm.set_stream_delay_ms(i32::from(delay_ms)).map_err(|_| BindingError::InvalidInput)?;
            owned.apm.process_capture_i16(src, dst).map_err(|_| BindingError::InvalidInput)?;
        }
        let result = output.iter().flat_map(|sample| sample.to_le_bytes()).collect();
        input.fill(0); output.fill(0);
        Ok(result)
    }

    /// Exactly one accepted 10 ms physical playout reference. Never produces microphone input.
    pub fn render_pcm(&self, data: Vec<u8>) -> Result<(), BindingError> {
        let mut state = self.state.lock().map_err(|_| BindingError::Worker)?;
        let owned = state.as_mut().ok_or(BindingError::PermissionDenied)?;
        let mut input = samples(&data, 480, owned.config.render_channels)?;
        let mut output = vec![0i16; input.len()];
        owned.apm.process_render_i16(&input, &mut output).map_err(|_| BindingError::InvalidInput)?;
        input.fill(0); output.fill(0);
        Ok(())
    }

    /// Joined source changes/silencing discard prior capture/filter/reference state.
    pub fn reset(&self) -> Result<(), BindingError> {
        let mut state = self.state.lock().map_err(|_| BindingError::Worker)?;
        let owned = state.as_mut().ok_or(BindingError::PermissionDenied)?;
        owned.apm = owned.config.processor();
        Ok(())
    }

    pub fn shutdown(&self) -> Result<(), BindingError> {
        self.state.lock().map_err(|_| BindingError::Worker)?.take();
        Ok(())
    }
}
