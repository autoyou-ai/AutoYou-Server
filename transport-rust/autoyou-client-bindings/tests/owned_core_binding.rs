//! Generated Core proof/lookup boundary only; no endpoint or network is opened.
#[test]
fn generated_core_proof_and_routing_binding_preserves_pinned_policy() {
    use std::{fs, path::PathBuf, process::Command};
    autoyou_client_bindings::FrameQueue::new().shutdown().unwrap();
    let root=PathBuf::from(std::env::var("AUTOYOU_TEST_ROOT").expect("isolated root required"));
    let stamp=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let out=root.join(format!("core-binding-qualification-{}-{stamp}",std::process::id()));
    fs::create_dir_all(&out).unwrap();
    let filename=format!("{}autoyou_client_bindings{}",std::env::consts::DLL_PREFIX,std::env::consts::DLL_SUFFIX);
    let library=std::env::current_exe().unwrap().parent().unwrap().join(&filename);
    uniffi::generate(uniffi::GenerateOptions {
        languages:vec![uniffi::TargetLanguage::Python,uniffi::TargetLanguage::Swift,uniffi::TargetLanguage::Kotlin],
        source:library.to_string_lossy().into_owned().into(),out_dir:out.to_string_lossy().into_owned().into(),
        format:false,metadata_no_deps:true,..Default::default()
    }).unwrap();
    fs::copy(&library,out.join(&filename)).unwrap();
    fs::write(out.join("core-routing-v1.json"),include_str!("../../tests/fixtures/core-routing-v1.json")).unwrap();
    let source=r#"
import json,socket,hashlib
from pathlib import Path
def deny(*args,**kwargs): raise AssertionError('Core crypto qualification cannot use sockets')
socket.socket.connect=socket.socket.connect_ex=socket.socket.bind=deny
import autoyou_client_bindings as api
root=Path.cwd();data=json.loads((root/'core-routing-v1.json').read_text())
signature=api.sign_core_endpoint_proof(bytes([17])*32,data['challenge_payload'],'https://core.example.invalid',
    'account00000001','client000000001',1_800_000_000_000)
assert signature==data['proof_signature']
policy=json.dumps(dict(bind_addresses=['127.0.0.1:0'],local_only=True,
    relays=[dict(url='http://127.0.0.1:32123/',token='synthetic-token')]))
args=[json.dumps(data['routing_envelope']),data['core_public_key'],'https://core.example.invalid',
    'account00000001','client000000001',data['endpoint_id'],1,1_800_000_000_000,policy]
record=api.verify_core_routing_record(*args)
assert record.epoch==4 and record.expires_at_ms==1_800_000_120_000 and record.ticket
for index,value in [(1,'00'*32),(2,'https://other.example.invalid'),(3,'account00000002'),
                    (4,'client000000002'),(6,5),(7,1_800_000_120_000)]:
    forged=args.copy();forged[index]=value
    try:api.verify_core_routing_record(*forged)
    except api.BindingError.PermissionDenied:pass
    else:raise AssertionError('Forged Core routing context accepted')
saved=api.accept_core_routing_record(api.empty_core_routing_store(),*args[:6],args[7],policy)
assert saved.epoch==4 and json.loads(bytes(saved.protected_store))['clock_floor_ms']==args[7]
config=api.accept_core_relay_configuration(saved.protected_store,json.dumps(data['relay_envelope']),*args[1:6],args[7],policy)
assert config.epoch==4 and json.loads(config.credentials_json)[0]['url']=='http://127.0.0.1:32123/'
assert json.loads(config.credentials_json)[0]['token']
try:api.accept_core_relay_configuration(b'corrupt',json.dumps(data['relay_envelope']),*args[1:6],args[7],policy)
except api.BindingError:pass
else:raise AssertionError('Corrupt protected Core floors accepted')
info=api.core_info()
files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
(root/'core-qualification.json').write_text(json.dumps(dict(passed=True,cases=11,network=False,
    lock_sha256=info.lock_sha256,files=files),indent=2))
print('Generated Core API: 11 checks passed')
"#;
    let script=out.join("core_qualification.py");fs::write(&script,source).unwrap();
    let mut command=if cfg!(windows) { let mut c=Command::new("py");c.arg("-3");c } else { Command::new("python3") };
    let result=command.arg("-X").arg("utf8").arg(&script).current_dir(&out)
        .env("AUTOYOU_TEST_ROOT",&out).env("PYTHONDONTWRITEBYTECODE","1").output().unwrap();
    assert!(result.status.success(),"{}\n{}",String::from_utf8_lossy(&result.stdout),String::from_utf8_lossy(&result.stderr));
    println!("{}",String::from_utf8_lossy(&result.stdout));println!("CORE_ARTIFACT_ROOT={}",out.display());
}
