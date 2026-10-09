//! Owned generator entrypoint; never linked into a product runtime.
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    assert_eq!(args.len(), 2, "usage: autoyou-bindgen <native-library> <output-directory>");
    uniffi::generate(uniffi::GenerateOptions {
        languages: vec![uniffi::TargetLanguage::Python, uniffi::TargetLanguage::Swift, uniffi::TargetLanguage::Kotlin],
        source: args[0].clone().into(), out_dir: args[1].clone().into(),
        config_override: Some(concat!(env!("CARGO_MANIFEST_DIR"), "/package-uniffi.toml").into()),
        format: false, metadata_no_deps: true, ..Default::default()
    }).expect("owned binding generation failed");
}
