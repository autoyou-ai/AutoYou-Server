// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
use std::{fs,path::PathBuf};
mod common;
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
    common::stage_application_binding(&out);
    let script=r#"
import asyncio,hashlib,json,os,runpy,socket
from pathlib import Path
loop=asyncio.new_event_loop()
def deny(*args,**kwargs): raise AssertionError('Python media networking is prohibited')
socket.socket.connect=socket.socket.connect_ex=socket.socket.bind=deny
import autoyou_client_bindings as api
root=Path.cwd();info=api.core_info()
fixture=runpy.run_path(str(Path(os.environ['AUTOYOU_QUALIFICATION_REPO_ROOT'])/'tests/server/transport/fixtures/iroh_media_fixture.py'))
with asyncio.Runner(loop_factory=lambda:loop) as runner: runner.run(fixture['qualify_media'](api,root))
print('generated media negotiation, codec and physical ownership passed')
"#;
    let script_path=out.join("media_qualification.py");fs::write(&script_path,script).unwrap();
    let mut command=common::python_command(&out);
    let result=command.arg(&script_path).current_dir(&out)
        .env("AUTOYOU_TEST_ROOT",&out)
        .env("AUTOYOU_QUALIFICATION_REPO_ROOT",PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap().parent().unwrap())
        .env("PYTHONDONTWRITEBYTECODE","1").output().unwrap();
    assert!(result.status.success(),"Python media component: {}",String::from_utf8_lossy(&result.stderr));
    assert!(String::from_utf8_lossy(&result.stdout).contains("physical ownership passed"));
}
