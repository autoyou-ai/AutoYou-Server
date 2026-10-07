//! Generated peer codec and synthetic loopback component qualification; no SDK.

#[test]
fn generated_peer_descriptors_preserve_proof_and_reject_transport_changes() {
    use std::{fs, path::PathBuf, process::Command};
    let root = PathBuf::from(std::env::var("AUTOYOU_TEST_ROOT").expect("isolated test root required"));
    let unique = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let out = root.join(format!("peer-binding-qualification-{}-{unique}", std::process::id()));
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
import hashlib,json,socket,sys,asyncio,importlib.util
from pathlib import Path
def deny(*args,**kwargs): raise AssertionError('Peer codec qualification cannot use sockets')
socket_calls=(socket.socket.connect,socket.socket.connect_ex,socket.socket.bind)
socket.socket.connect=socket.socket.connect_ex=socket.socket.bind=deny
import autoyou_client_bindings as api
from shared.iroh_binding import load_binding,target_tag,library_name
root=Path.cwd();info=api.core_info()
manifest=dict(schema=1,api_version=1,wire_version=1,core_version='0.1.0',iroh_version='1.3.0',noq_version='1.3.0',
    uniffi_version='0.32.2',rust_toolchain='1.99.0',target_tag=target_tag(),lock_sha256=info.lock_sha256,
    files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['autoyou_client_bindings.py',library_name()]})
(root/'binding-manifest.json').write_text(json.dumps(manifest))
api=load_binding(artifact_root=root)
sys.path.insert(0,str(Path(sys.argv[1])/'clients/python'))
from peer_link.codec import PeerPairCodec,PeerPairError
from peer_link.protocol import PeerOfferEnvelope
codec=PeerPairCodec(native_api=api)
endpoint=api.endpoint_id_from_key(bytes([61])*32)
offer=PeerOfferEnvelope(offer=dict(transport='iroh',type='offer',version=1,endpoint_id=endpoint),
    device_id='synthetic-peer-device',device_name='Synthetic peer',platform='python',requested=['chat','browser'])
secret=PeerPairCodec.generate_authenticator()
wire=codec.build_offer_text(offer,secret)
parsed=codec.parse_offer_text(wire,secret)
assert parsed.offer==offer.offer and not parsed.iceServers and not parsed.candidates
assert api.peer_endpoint_fingerprint(endpoint).startswith('iroh-ed25519 ')
for mutation in [dict(transport='legacy'),dict(sdp='synthetic-sdp'),dict(version=True),dict(endpoint_id='synthetic-invalid')]:
    changed={**offer.offer,**mutation}
    try: api.validate_peer_descriptor(json.dumps(changed),'offer')
    except api.BindingError: pass
    else: raise AssertionError('Invalid peer descriptor accepted')
offer.candidates=[dict(candidate='synthetic-candidate')]
try: codec.build_offer_text(offer,secret)
except PeerPairError: pass
else: raise AssertionError('Native offer borrowed legacy candidates')
print('Generated peer API and actual encrypted Python codec: passed')
socket.socket.connect,socket.socket.connect_ex,socket.socket.bind=socket_calls
spec=importlib.util.spec_from_file_location('isolated_peer_fixture',Path(sys.argv[1])/'tests/server/transport/fixtures/iroh_peer_fixture.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
asyncio.run(fixture.qualify(api,root/'synthetic-peer-component'))
"#;
    let source = out.join("peer_qualification.py");
    fs::write(&source, script).unwrap();
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap().parent().unwrap().to_path_buf();
    let mut command = if cfg!(windows) { let mut c = Command::new("py"); c.arg("-3"); c } else { Command::new("python3") };
    let result = command.arg("-X").arg("utf8").arg(&source).arg(&repo).current_dir(&out)
        .env("PYTHONPATH", repo.join("AutoYou-Server")).env("AUTOYOU_TEST_ROOT", &out)
        .env("PYTHONDONTWRITEBYTECODE", "1").output().unwrap();
    assert!(result.status.success(), "{}\n{}", String::from_utf8_lossy(&result.stdout), String::from_utf8_lossy(&result.stderr));
    println!("{}", String::from_utf8_lossy(&result.stdout));
    println!("PEER_ARTIFACT_ROOT={}", out.display());
}
