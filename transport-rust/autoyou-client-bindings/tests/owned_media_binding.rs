// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
use std::{fs,path::PathBuf,process::Command};
use autoyou_client_bindings::FrameQueue;

#[test]
fn generated_python_media_negotiation_codec_and_physical_ownership() {
    // Cargo's intrinsic test cdylib; no application/distribution build.
    FrameQueue::new().shutdown().unwrap();
    let root=PathBuf::from(std::env::var("AUTOYOU_TEST_ROOT").expect("isolated test root required"));
    let unique=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let out=root.join(format!("media-binding-qualification-{}-{unique}",std::process::id()));
    fs::create_dir_all(&out).unwrap();
    let filename=format!("{}autoyou_client_bindings{}",std::env::consts::DLL_PREFIX,std::env::consts::DLL_SUFFIX);
    let library=std::env::current_exe().unwrap().parent().unwrap().join(&filename);
    assert!(library.is_file(),"Cargo's test cdylib is required");
    uniffi::generate(uniffi::GenerateOptions {
        languages:vec![uniffi::TargetLanguage::Python,uniffi::TargetLanguage::Kotlin,uniffi::TargetLanguage::Swift],source:library.to_string_lossy().into_owned().into(),
        out_dir:out.to_string_lossy().into_owned().into(),format:false,metadata_no_deps:true,..Default::default()
    }).unwrap();
    fs::copy(&library,out.join(filename)).unwrap();
    let script=r#"
import asyncio,hashlib,json,os,runpy,socket
from pathlib import Path
loop=asyncio.new_event_loop()
def deny(*args,**kwargs): raise AssertionError('Python media networking is prohibited')
socket.socket.connect=socket.socket.connect_ex=socket.socket.bind=deny
import autoyou_client_bindings as api
from shared.iroh_binding import load_binding,target_tag,library_name
root=Path.cwd();info=api.core_info()
manifest=dict(schema=1,api_version=1,wire_version=1,core_version='0.1.0',iroh_version='1.3.0',noq_version='1.3.0',
    uniffi_version='0.32.2',rust_toolchain='1.99.0',target_tag=target_tag(),lock_sha256=info.lock_sha256,
    files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['autoyou_client_bindings.py',library_name()]})
(root/'binding-manifest.json').write_text(json.dumps(manifest))
api=load_binding(artifact_root=root)
fixture=runpy.run_path(str(Path(os.environ['AUTOYOU_QUALIFICATION_REPO_ROOT'])/'tests/server/transport/fixtures/iroh_media_fixture.py'))
with asyncio.Runner(loop_factory=lambda:loop) as runner: runner.run(fixture['qualify_media'](api,root))
print('generated media negotiation, codec and physical ownership passed')
"#;
    let script_path=out.join("media_qualification.py");fs::write(&script_path,script).unwrap();
    let mut command=if cfg!(windows) { let mut command=Command::new("py");command.arg("-3");command } else { Command::new("python3") };
    let result=command.arg(&script_path).current_dir(&out)
        .env("PYTHONPATH",PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap())
        .env("AUTOYOU_TEST_ROOT",&out)
        .env("AUTOYOU_QUALIFICATION_REPO_ROOT",PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap().parent().unwrap())
        .env("PYTHONDONTWRITEBYTECODE","1").output().unwrap();
    assert!(result.status.success(),"Python media component: {}",String::from_utf8_lossy(&result.stderr));
    assert!(String::from_utf8_lossy(&result.stdout).contains("physical ownership passed"));
}
