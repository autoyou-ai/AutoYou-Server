//! Qualify only the new package generator against Cargo's component library.
#[test]
fn package_generator_preserves_the_owned_abi_and_module_names() {
    use std::{fs, path::PathBuf, process::Command};
    let root = PathBuf::from(std::env::var("AUTOYOU_TEST_ROOT").expect("isolated root required"));
    let output = root.join(format!("package-generator-{}", std::process::id()));
    fs::create_dir_all(&output).unwrap();
    let library = std::env::current_exe().unwrap().parent().unwrap().join(
        format!("{}autoyou_client_bindings{}", std::env::consts::DLL_PREFIX, std::env::consts::DLL_SUFFIX));
    assert!(library.is_file());
    let result = Command::new(env!("CARGO_BIN_EXE_autoyou-bindgen")).arg(&library).arg(&output).output().unwrap();
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    for name in ["AutoYouTransport.swift", "AutoYouTransportFFI.h", "AutoYouTransportFFI.modulemap",
        "autoyou_client_bindings.py", "uniffi/autoyou_client_bindings/autoyou_client_bindings.kt"] {
        assert!(output.join(name).is_file(), "missing generated package file {name}");
    }
    assert!(fs::read_to_string(output.join("AutoYouTransport.swift")).unwrap().contains("import AutoYouTransportFFI"));
    assert!(fs::read_to_string(output.join("autoyou_client_bindings.py")).unwrap().contains("def core_info("));
    let invalid = Command::new(env!("CARGO_BIN_EXE_autoyou-bindgen")).output().unwrap();
    assert!(!invalid.status.success());
    println!("PACKAGE_GENERATOR_ROOT={}", output.display());
}
