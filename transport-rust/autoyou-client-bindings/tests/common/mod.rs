use std::{fs, path::Path, process::Command};
use sha2::{Digest, Sha256};

pub fn stage_application_binding(output: &Path) {
    assert_eq!(autoyou_application::APPLICATION_API_VERSION, 1);
    let library = std::env::current_exe().unwrap().parent().unwrap().join(format!(
        "{}autoyou_application{}", std::env::consts::DLL_PREFIX, std::env::consts::DLL_SUFFIX));
    let name = if cfg!(windows) { "autoyou_application.pyd" } else { "autoyou_application.abi3.so" };
    fs::copy(library, output.join(name)).expect("Cargo's native Python binding is required");
    let native = format!("{}autoyou_client_bindings{}", std::env::consts::DLL_PREFIX, std::env::consts::DLL_SUFFIX);
    let files: serde_json::Map<String, serde_json::Value> = ["autoyou_client_bindings.py", &native, name].into_iter()
        .map(|name| (name.into(), format!("{:x}", Sha256::digest(fs::read(output.join(name)).unwrap())).into())).collect();
    let system = if cfg!(windows) { "windows" } else if cfg!(target_os="macos") { "macos" } else { "linux" };
    let manifest = serde_json::json!({"schema":1,"api_version":1,"wire_version":1,"core_version":"0.1.0",
        "iroh_version":"1.3.0","noq_version":"1.3.0","uniffi_version":"0.32.2","rust_toolchain":"1.99.0",
        "target_tag":format!("{system}-{}", std::env::consts::ARCH),
        "lock_sha256":autoyou_client_bindings::core_info().lock_sha256,"files":files});
    fs::write(output.join("binding-manifest.json"), serde_json::to_vec(&manifest).unwrap()).unwrap();
    // All adapters and opaque objects must use the same attested native module.
    fs::write(output.join("sitecustomize.py"), r#"
try:
    from pathlib import Path
    import sys
    from shared.iroh_binding import load_binding
    api = load_binding(artifact_root=Path(__file__).parent)
    sys.modules['autoyou_client_bindings'] = api
    import shared.iroh_state_store as state_adapter
    import shared.iroh_grants as grant_adapter
    import shared.session_execution as identity_adapter
    import shared.session_transport as transport_adapter
    import shared.runtime_limits as runtime_limits_adapter
    import shared.native_secure_stream as secure_stream_adapter
    state_adapter.load_binding = lambda: api
    grant_adapter._native_api = lambda: api
    identity_adapter._native_api = lambda: api
    transport_adapter._native_api = lambda: api
    runtime_limits_adapter.native_api = lambda: api
    secure_stream_adapter.native_api = lambda: api.application_json
except Exception as error:
    raise SystemExit("attested native fixture initialization failed") from error
"#).unwrap();
}

pub fn python_command(output: &Path) -> Command {
    let mut command = if let Some(python) = std::env::var_os("PYO3_PYTHON") { Command::new(python) }
        else if cfg!(windows) { let mut command = Command::new("py"); command.arg("-3"); command }
        else { Command::new("python3") };
    let server = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap();
    let repo = server.parent().unwrap();
    command.env("PYTHONPATH", std::env::join_paths([output, repo, server, &repo.join("clients/python")]).unwrap());
    command
}
