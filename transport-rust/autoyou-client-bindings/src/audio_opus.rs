// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Raw codec only. Capture clocks, DSP, physical output, consent and source
//! ownership stay in their admitted adapters. No devices, endpoints or storage.
use crate::BindingError;
use autoyou_protocol::media::{Codec, MediaHeader, MediaKind, MAX_AUDIO_FRAME_BYTES};
use opusic_c::{Application, Bitrate, Channels, Decoder, Encoder, FrameDuration, SampleRate};
use std::sync::{Arc, Mutex};

const FRAMES: usize = 960;
// One ordinary mono/stereo Opus stream, one 20 ms packet. No multistream or
// arbitrary packet aggregation; the public transport audio limit is wider.
const ENCODED_CAPACITY: usize = 1275;

fn channels(value: u8) -> Result<Channels, BindingError> {
    match value { 1 => Ok(Channels::Mono), 2 => Ok(Channels::Stereo), _ => Err(BindingError::InvalidInput) }
}
fn rate(value: u32) -> Result<u32, BindingError> {
    if (16_000..=256_000).contains(&value) { Ok(value) } else { Err(BindingError::InvalidInput) }
}
fn header(data: &[u8], channels: u8) -> Result<MediaHeader, BindingError> {
    let header = MediaHeader { kind: MediaKind::Audio, codec: Codec::Opus, keyframe: false,
        generation: 1, authorization_epoch: 1, source_id: 1, sequence: 0, timestamp_us: 0,
        duration_us: 20_000, length: data.len(), width: 0, height: 0, channels };
    header.validate().map_err(|_| BindingError::InvalidInput)?;
    autoyou_protocol::media_codec::validate(&header, data).map_err(|_| BindingError::InvalidInput)?;
    Ok(header)
}

#[derive(Clone, Copy, Debug, uniffi::Record)]
pub struct OpusEncoderConfig {
    pub channels: u8,
    pub bitrate_bps: u32,
    pub voice: bool,
}

/// One serialized, admitted capture codec; DSP executes before encode_pcm.
#[derive(uniffi::Object)]
pub struct MediaAudioOpusEncoder {
    channels: u8,
    state: Mutex<Option<Encoder>>,
}
#[uniffi::export]
impl MediaAudioOpusEncoder {
    #[uniffi::constructor]
    pub fn new(config: OpusEncoderConfig) -> Result<Arc<Self>, BindingError> {
        let channel = channels(config.channels)?;
        let bitrate = rate(config.bitrate_bps)?;
        let mut codec = Encoder::new(channel, SampleRate::Hz48000,
            if config.voice { Application::Voip } else { Application::Audio }).map_err(|_| BindingError::Worker)?;
        codec.set_bitrate(Bitrate::Value(bitrate)).map_err(|_| BindingError::Worker)?;
        codec.set_force_channels(Some(channel)).map_err(|_| BindingError::Worker)?;
        codec.set_frame_duration(FrameDuration::Size20).map_err(|_| BindingError::Worker)?;
        codec.set_dtx(false).map_err(|_| BindingError::Worker)?;
        Ok(Arc::new(Self { channels: config.channels, state: Mutex::new(Some(codec)) }))
    }

    /// Exactly one interleaved 48 kHz PCM16 LE 20 ms frame, with no padding,
    /// resampling, callback, source-time substitution or microphone fabrication.
    pub fn encode_pcm(&self, mut data: Vec<u8>) -> Result<Vec<u8>, BindingError> {
        if data.len() != FRAMES * usize::from(self.channels) * 2 { return Err(BindingError::InvalidInput); }
        let mut input: Vec<u16> = data.chunks_exact(2).map(|v| u16::from_le_bytes([v[0], v[1]])).collect();
        data.fill(0);
        let mut state = self.state.lock().map_err(|_| BindingError::Worker)?;
        let codec = state.as_mut().ok_or(BindingError::PermissionDenied)?;
        let mut output = vec![0u8; ENCODED_CAPACITY];
        let result = codec.encode_to_slice(&input, &mut output).map_err(|_| BindingError::InvalidInput);
        input.fill(0);
        let length = result?;
        if length == 0 || length > output.len() { return Err(BindingError::Worker); }
        output.truncate(length); header(&output, self.channels)?;
        Ok(output)
    }

    /// Admitted congestion feedback changes local bitrate, not the media clock.
    pub fn configure_network(&self, bitrate_bps: u32, loss_percent: u8) -> Result<(), BindingError> {
        let bitrate = rate(bitrate_bps)?;
        if loss_percent > 30 { return Err(BindingError::InvalidInput); }
        let mut state = self.state.lock().map_err(|_| BindingError::Worker)?;
        let codec = state.as_mut().ok_or(BindingError::PermissionDenied)?;
        codec.set_bitrate(Bitrate::Value(bitrate)).map_err(|_| BindingError::InvalidInput)?;
        codec.set_packet_loss(loss_percent).map_err(|_| BindingError::InvalidInput)
    }
    pub fn reset(&self) -> Result<(), BindingError> {
        let mut state = self.state.lock().map_err(|_| BindingError::Worker)?;
        state.as_mut().ok_or(BindingError::PermissionDenied)?.reset().map_err(|_| BindingError::Worker)
    }
    /// The adapter joins its physical producer and codec work before release.
    pub fn shutdown(&self) -> Result<(), BindingError> {
        self.state.lock().map_err(|_| BindingError::Worker)?.take(); Ok(())
    }
}

/// Decoder output and loss concealment belong exclusively to rendering.
#[derive(uniffi::Object)]
pub struct MediaAudioOpusDecoder {
    channels: u8,
    state: Mutex<Option<Decoder>>,
}
#[uniffi::export]
impl MediaAudioOpusDecoder {
    #[uniffi::constructor]
    pub fn new(channels: u8) -> Result<Arc<Self>, BindingError> {
        let codec = Decoder::new(self::channels(channels)?, SampleRate::Hz48000).map_err(|_| BindingError::Worker)?;
        Ok(Arc::new(Self { channels, state: Mutex::new(Some(codec)) }))
    }
    pub fn decode_pcm(&self, data: Vec<u8>) -> Result<Vec<u8>, BindingError> {
        // Reject profile/header/aggregation before entering the native decoder.
        if data.is_empty() || data.len() > MAX_AUDIO_FRAME_BYTES { return Err(BindingError::InvalidInput); }
        header(&data, self.channels)?;
        self.decode(&data)
    }
    /// One missing 20 ms render block only. Never feed this into speech/recording.
    pub fn conceal_pcm(&self) -> Result<Vec<u8>, BindingError> { self.decode(&[]) }
    pub fn reset(&self) -> Result<(), BindingError> {
        let mut state = self.state.lock().map_err(|_| BindingError::Worker)?;
        state.as_mut().ok_or(BindingError::PermissionDenied)?.reset().map_err(|_| BindingError::Worker)
    }
    /// The adapter joins its physical renderer and codec work before release.
    pub fn shutdown(&self) -> Result<(), BindingError> {
        self.state.lock().map_err(|_| BindingError::Worker)?.take(); Ok(())
    }
}
impl MediaAudioOpusDecoder {
    fn decode(&self, data: &[u8]) -> Result<Vec<u8>, BindingError> {
        let mut state = self.state.lock().map_err(|_| BindingError::Worker)?;
        let codec = state.as_mut().ok_or(BindingError::PermissionDenied)?;
        let mut output = vec![0u16; FRAMES * usize::from(self.channels)];
        let frames = codec.decode_to_slice(data, &mut output, false).map_err(|_| BindingError::InvalidInput)?;
        // libopus returns samples PER CHANNEL, including for stereo.
        if frames != FRAMES { output.fill(0); return Err(BindingError::InvalidInput); }
        let result = output.iter().flat_map(|sample| sample.to_le_bytes()).collect(); output.fill(0); Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn pcm(channel_count: u8, sequence: usize) -> Vec<u8> {
        (0..FRAMES).flat_map(|sample| (0..channel_count).flat_map(move |channel| {
            let frequency = if channel == 0 { 440.0 } else { 660.0 };
            let phase = std::f64::consts::TAU*frequency*(sequence*FRAMES+sample) as f64/48_000.0;
            ((phase.sin()*10_000.0) as i16).to_le_bytes()
        })).collect()
    }
    fn config(channels: u8) -> OpusEncoderConfig { OpusEncoderConfig { channels, bitrate_bps: 64_000, voice: true } }
    #[test]
    fn opus_immediate_mono_and_stereo_packets_match_the_transport_and_signal() {
        assert!(opusic_c::version().contains("1.6.1"));
        for channels in [1u8, 2] {
            let encoder = MediaAudioOpusEncoder::new(config(channels)).unwrap();
            let decoder = MediaAudioOpusDecoder::new(channels).unwrap();
            let mut decoded = Vec::new();
            for sequence in 0..16 {
                let packet = encoder.encode_pcm(pcm(channels, sequence)).unwrap();
                assert!(!packet.is_empty() && packet.len() <= ENCODED_CAPACITY); header(&packet, channels).unwrap();
                let output = decoder.decode_pcm(packet).unwrap(); assert_eq!(output.len(), FRAMES*usize::from(channels)*2);
                if sequence > 2 { decoded.extend(output.chunks_exact(2).map(|b| i16::from_le_bytes([b[0],b[1]]))); }
            }
            for channel in 0..channels {
                let samples: Vec<f64> = decoded.iter().skip(usize::from(channel)).step_by(usize::from(channels)).map(|s| f64::from(*s)).collect();
                let rms = (samples.iter().map(|s| s*s).sum::<f64>()/samples.len() as f64).sqrt(); assert!((3000.0..12000.0).contains(&rms));
                let crossings = samples.windows(2).filter(|v| v[0] <= 0.0 && v[1] > 0.0).count();
                let measured = crossings as f64*48_000.0/samples.len() as f64;
                assert!((measured-if channel == 0 {440.0} else {660.0}).abs() < 15.0);
            }
            encoder.shutdown().unwrap(); decoder.shutdown().unwrap();
        }
    }
    #[test]
    fn opus_rejects_partial_input_channels_and_unbounded_configuration() {
        for channels in [0,3,255] { assert!(MediaAudioOpusEncoder::new(config(channels)).is_err()); assert!(MediaAudioOpusDecoder::new(channels).is_err()); }
        for bitrate in [0,15_999,256_001,u32::MAX] { assert!(MediaAudioOpusEncoder::new(OpusEncoderConfig { bitrate_bps: bitrate, ..config(1) }).is_err()); }
        let encoder = MediaAudioOpusEncoder::new(config(1)).unwrap();
        for length in [0,1,959,1919,1921,3840,16*1024] { assert!(encoder.encode_pcm(vec![0;length]).is_err()); }
        assert!(encoder.configure_network(32_000,31).is_err()); assert!(encoder.configure_network(0,0).is_err());
        encoder.configure_network(32_000,10).unwrap(); header(&encoder.encode_pcm(pcm(1,0)).unwrap(),1).unwrap();
    }
    #[test]
    fn opus_decoder_rejects_wrong_duration_stereo_and_hostile_headers_before_decode() {
        let decoder = MediaAudioOpusDecoder::new(1).unwrap();
        // Code 3 with a zero frame count is malformed. A one-frame packet
        // without coded samples is legal Opus DTX and must not be mislabeled.
        for data in [vec![],vec![0;16*1024+1],vec![0xff],vec![0xfb,0x00],vec![0xf0,0xff,0xfe]] { assert!(decoder.decode_pcm(data).is_err()); }
        let stereo = MediaAudioOpusEncoder::new(config(2)).unwrap().encode_pcm(pcm(2,0)).unwrap(); assert!(decoder.decode_pcm(stereo).is_err());
        let encoder = MediaAudioOpusEncoder::new(config(1)).unwrap();
        let packet = encoder.encode_pcm(pcm(1,0)).unwrap();
        let clean = MediaAudioOpusDecoder::new(1).unwrap().decode_pcm(packet.clone()).unwrap();
        assert_eq!(decoder.decode_pcm(packet).unwrap(),clean);
    }
    #[test]
    fn opus_reset_and_render_only_concealment_are_bounded_and_shutdown_denies_use() {
        let encoder = MediaAudioOpusEncoder::new(config(1)).unwrap(); let decoder = MediaAudioOpusDecoder::new(1).unwrap();
        let first = encoder.encode_pcm(pcm(1,0)).unwrap(); let original = decoder.decode_pcm(first.clone()).unwrap();
        for _ in 0..8 { assert_eq!(decoder.conceal_pcm().unwrap().len(),1920); }
        decoder.reset().unwrap(); assert_eq!(decoder.decode_pcm(first).unwrap(),original);
        encoder.reset().unwrap(); assert_eq!(encoder.encode_pcm(pcm(1,0)).unwrap(),MediaAudioOpusEncoder::new(config(1)).unwrap().encode_pcm(pcm(1,0)).unwrap());
        encoder.shutdown().unwrap(); decoder.shutdown().unwrap();
        assert!(encoder.encode_pcm(pcm(1,0)).is_err()); assert!(encoder.reset().is_err()); assert!(encoder.configure_network(64_000,0).is_err());
        assert!(decoder.decode_pcm(vec![0xf8,0xff,0xfe]).is_err()); assert!(decoder.conceal_pcm().is_err()); assert!(decoder.reset().is_err());
    }
    #[test]
    fn opus_processing_and_retirement_are_serialized_without_abandoning_workers() {
        let encoder = MediaAudioOpusEncoder::new(config(2)).unwrap();
        let owned = encoder.clone();
        let job = std::thread::spawn(move || { for i in 0..32 { if owned.encode_pcm(pcm(2,i)).is_err() { break } } });
        encoder.shutdown().unwrap(); job.join().unwrap(); assert!(encoder.encode_pcm(pcm(2,0)).is_err());
    }
}
