//! Source-component Lobby qualification with generated bindings and loopback only.

#[test]
fn generated_native_lobby_preserves_room_policy_and_shared_endpoint_lifecycle() {
    use std::{fs, path::PathBuf, process::Command};
    let root = PathBuf::from(std::env::var("AUTOYOU_TEST_ROOT").expect("isolated test root required"));
    let unique = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let out = root.join(format!("room-binding-qualification-{}-{unique}", std::process::id()));
    fs::create_dir_all(&out).unwrap();
    let filename = format!("{}autoyou_client_bindings{}", std::env::consts::DLL_PREFIX, std::env::consts::DLL_SUFFIX);
    let library = std::env::current_exe().unwrap().parent().unwrap().join(&filename);
    uniffi::generate(uniffi::GenerateOptions {
        languages: vec![uniffi::TargetLanguage::Python, uniffi::TargetLanguage::Kotlin, uniffi::TargetLanguage::Swift],
        source: library.to_string_lossy().into_owned().into(), out_dir: out.to_string_lossy().into_owned().into(),
        format: false, metadata_no_deps: true, ..Default::default()
    }).unwrap();
    fs::copy(&library, out.join(filename)).unwrap();
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap().parent().unwrap().to_path_buf();
    let script = repo.join("tests/server/transport/fixtures/iroh_room_fixture.py");
    let mut command = if cfg!(windows) { let mut c = Command::new("py"); c.arg("-3"); c } else { Command::new("python3") };
    let paths = std::env::join_paths([out.clone(),repo.clone(),repo.join("AutoYou-Server"),repo.join("clients/python")]).unwrap();
    let result = command.arg("-X").arg("utf8").arg(script).arg(&out).current_dir(&out)
        .env("PYTHONPATH",paths).env("AUTOYOU_TEST_ROOT",&out).env("PYTHONDONTWRITEBYTECODE","1").output().unwrap();
    assert!(result.status.success(),"{}\n{}",String::from_utf8_lossy(&result.stdout),String::from_utf8_lossy(&result.stderr));
    println!("{}",String::from_utf8_lossy(&result.stdout));
    println!("ROOM_ARTIFACT_ROOT={}",out.display());
}
