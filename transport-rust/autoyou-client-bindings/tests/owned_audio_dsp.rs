use autoyou_client_bindings::{AudioDspConfig, BindingError, MediaAudioProcessor};

fn config(channels: u8) -> AudioDspConfig {
    AudioDspConfig { capture_channels: channels, render_channels: channels,
        echo_cancellation: false, noise_suppression: false, automatic_gain: false }
}
fn pcm(samples: impl IntoIterator<Item = i16>) -> Vec<u8> {
    samples.into_iter().flat_map(i16::to_le_bytes).collect()
}
fn energy(data: &[u8]) -> f64 {
    data.chunks_exact(2).map(|v| f64::from(i16::from_le_bytes([v[0], v[1]])).powi(2)).sum::<f64>() / (data.len() / 2) as f64
}

#[test]
fn exact_mono_stereo_pcm_without_processing_is_unchanged() {
    for channels in [1, 2] {
        let processor = MediaAudioProcessor::new(config(channels)).unwrap();
        let input = pcm((0..960 * usize::from(channels)).map(|i| (i as i16).wrapping_mul(79)));
        assert_eq!(processor.capture_pcm(input.clone(), 0).unwrap(), input);
        processor.render_pcm(input[..480 * usize::from(channels) * 2].to_vec()).unwrap();
        processor.shutdown().unwrap();
    }
}

#[test]
fn hostile_profile_length_delay_and_closed_processor_are_denied() {
    for channels in [0, 3, 255] { assert!(MediaAudioProcessor::new(config(channels)).is_err()); }
    let p = MediaAudioProcessor::new(config(1)).unwrap();
    for size in [0, 1, 959, 1919, 1921, 3840, 8192] {
        assert!(matches!(p.capture_pcm(vec![0; size], 0), Err(BindingError::InvalidInput)));
    }
    assert!(p.render_pcm(vec![0; 1920]).is_err());
    assert!(p.capture_pcm(vec![0; 1920], 501).is_err());
    p.shutdown().unwrap(); p.shutdown().unwrap();
    assert!(matches!(p.capture_pcm(vec![0; 1920], 0), Err(BindingError::PermissionDenied)));
    assert!(p.render_pcm(vec![0; 960]).is_err() && p.reset().is_err());
}

#[test]
fn gain_noise_processing_preserves_real_silence_and_bounded_stereo_frames() {
    let mut cfg = config(2); cfg.noise_suppression = true; cfg.automatic_gain = true;
    let p = MediaAudioProcessor::new(cfg).unwrap();
    for _ in 0..20 { assert_eq!(p.capture_pcm(vec![0; 3840], 0).unwrap(), vec![0; 3840]); }
    let input = pcm((0..1920).map(|i| (500.0 * ((i / 2) as f64 * 0.04).sin()) as i16));
    let output = p.capture_pcm(input.clone(), 0).unwrap();
    assert_eq!(output.len(), input.len()); assert!(energy(&output) > 0.0 && output != input);
    p.reset().unwrap(); assert_eq!(p.capture_pcm(vec![0; 3840], 0).unwrap(), vec![0; 3840]);
}

#[test]
fn actual_aec_reduces_synthetic_delayed_echo() {
    let mut cfg = config(1); cfg.echo_cancellation = true;
    let p = MediaAudioProcessor::new(cfg).unwrap();
    let mut seed = 1u32;
    let mut history = std::collections::VecDeque::from([vec![0i16; 960], vec![0i16; 960]]);
    let mut reference_energy = 0.0; let mut residual_energy = 0.0;
    for frame in 0..400 {
        let reference: Vec<i16> = (0..960).map(|_| { seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5; ((seed & 0x3fff) as i16) - 8192 }).collect();
        for half in reference.chunks_exact(480) { p.render_pcm(pcm(half.iter().copied())).unwrap(); }
        history.push_back(reference); let captured = pcm(history.pop_front().unwrap().into_iter().map(|v| v / 2));
        let processed = p.capture_pcm(captured.clone(), 40).unwrap();
        if frame >= 300 { reference_energy += energy(&captured); residual_energy += energy(&processed); }
    }
    assert!(reference_energy > 1_000_000.0);
    assert!(residual_energy < reference_energy * 0.25, "synthetic echo ratio {}", residual_energy / reference_energy);
    p.shutdown().unwrap();
}

#[test]
fn concurrent_reference_capture_and_shutdown_are_serialized() {
    let mut cfg = config(1); cfg.echo_cancellation = true;
    let p = MediaAudioProcessor::new(cfg).unwrap();
    std::thread::scope(|scope| {
        scope.spawn(|| { for _ in 0..20 { let _ = p.render_pcm(vec![0; 960]); } });
        scope.spawn(|| { for _ in 0..10 { let _ = p.capture_pcm(vec![0; 1920], 0); } });
        scope.spawn(|| p.shutdown().unwrap());
    });
    assert!(p.capture_pcm(vec![0; 1920], 0).is_err());
}

#[test]
fn generated_audio_processor_api_round_trip_without_devices_or_network() {
    use std::{fs, path::PathBuf, process::Command};
    MediaAudioProcessor::new(config(1)).unwrap().shutdown().unwrap();
    let root = PathBuf::from(std::env::var("AUTOYOU_TEST_ROOT").expect("isolated test root required"));
    let unique = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let out = root.join(format!("media-binding-qualification-{}-{unique}", std::process::id()));
    fs::create_dir_all(&out).unwrap();
    let filename = format!("{}autoyou_client_bindings{}", std::env::consts::DLL_PREFIX, std::env::consts::DLL_SUFFIX);
    let library = std::env::current_exe().unwrap().parent().unwrap().join(&filename);
    uniffi::generate(uniffi::GenerateOptions {
        languages: vec![uniffi::TargetLanguage::Python, uniffi::TargetLanguage::Kotlin, uniffi::TargetLanguage::Swift],
        source: library.to_string_lossy().into_owned().into(), out_dir: out.to_string_lossy().into_owned().into(),
        format: false, metadata_no_deps: true, ..Default::default()
    }).unwrap();
    fs::copy(&library, out.join(filename)).unwrap();
    let script = r#"
import hashlib,json,socket
from pathlib import Path
def deny(*args,**kwargs): raise AssertionError('DSP qualification cannot use sockets')
socket.socket.connect=socket.socket.connect_ex=socket.socket.bind=deny
import autoyou_client_bindings as api
from shared.iroh_binding import load_binding,target_tag,library_name
root=Path.cwd();info=api.core_info()
manifest=dict(schema=1,api_version=1,wire_version=1,core_version='0.1.0',iroh_version='1.3.0',noq_version='1.3.0',
    uniffi_version='0.32.2',rust_toolchain='1.99.0',target_tag=target_tag(),lock_sha256=info.lock_sha256,
    files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['autoyou_client_bindings.py',library_name()]})
(root/'binding-manifest.json').write_text(json.dumps(manifest))
api=load_binding(artifact_root=root)
for channels in (1,2):
    p=api.MediaAudioProcessor(api.AudioDspConfig(capture_channels=channels,render_channels=channels,echo_cancellation=False,noise_suppression=False,automatic_gain=False))
    data=b'\x01\x02'*960*channels
    assert p.capture_pcm(data,20)==data
    p.render_pcm(data[:960*channels]);p.reset();p.shutdown();p.shutdown()
    try: p.capture_pcm(data,20)
    except api.BindingError: pass
    else: raise AssertionError('Retired DSP accepted capture')
p=api.MediaAudioProcessor(api.AudioDspConfig(capture_channels=1,render_channels=1,echo_cancellation=True,noise_suppression=True,automatic_gain=True))
p.render_pcm(bytes(960));assert p.capture_pcm(bytes(1920),20)==bytes(1920);p.shutdown()
for channels in (1,2):
    processor=api.MediaAudioProcessor(api.AudioDspConfig(capture_channels=channels,render_channels=channels,echo_cancellation=False,noise_suppression=False,automatic_gain=False))
    encoder=api.MediaAudioOpusEncoder(api.OpusEncoderConfig(channels=channels,bitrate_bps=64000,voice=True))
    decoder=api.MediaAudioOpusDecoder(channels)
    processed=processor.capture_pcm(bytes(1920*channels),20)
    packet=encoder.encode_pcm(processed)
    assert 0<len(packet)<=1275 and len(decoder.decode_pcm(packet))==1920*channels
    assert len(decoder.conceal_pcm())==1920*channels
    encoder.configure_network(32000,10);encoder.reset();decoder.reset()
    for bad in (b'',b'\xfb\x00',bytes(16385)):
        try: decoder.decode_pcm(bad)
        except api.BindingError: pass
        else: raise AssertionError('Invalid Opus input passed the binding')
    encoder.shutdown();decoder.shutdown();processor.shutdown()
    for operation in (lambda:encoder.encode_pcm(processed),decoder.conceal_pcm,decoder.reset):
        try: operation()
        except api.BindingError: pass
        else: raise AssertionError('Retired Opus codec accepted work')
print('generated DSP API, bounded PCM and shutdown passed')
print('generated raw Opus API, DSP composition and retirement passed')
"#;
    let path = out.join("audio_dsp_qualification.py"); fs::write(&path, script).unwrap();
    let mut command = if cfg!(windows) { let mut c = Command::new("py"); c.arg("-3"); c } else { Command::new("python3") };
    let result = command.arg(&path).current_dir(&out)
        .env("PYTHONPATH", PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap())
        .env("AUTOYOU_TEST_ROOT", &out).env("PYTHONDONTWRITEBYTECODE", "1").output().unwrap();
    assert!(result.status.success(), "generated DSP: {}", String::from_utf8_lossy(&result.stderr));
    assert!(String::from_utf8_lossy(&result.stdout).contains("bounded PCM and shutdown passed"));
    assert!(String::from_utf8_lossy(&result.stdout).contains("raw Opus API, DSP composition and retirement passed"));
}
