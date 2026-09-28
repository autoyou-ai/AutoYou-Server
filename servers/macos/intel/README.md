# AutoYou Server for macOS Intel

This folder is a thin Intel packaging wrapper for the shared macOS server build.
It does not fork server behavior; it calls `servers/macos/build-all.sh` and asks
the existing pipeline to write an Intel-named DMG.

Its app metadata uses the same repository `VERSION` value as the Apple Silicon
server path, currently `81.0.0`.

From the repository root:

```bash
./servers/macos/intel/build-all.sh --no-sign --dev
```

Outputs:

```text
servers/macos/intel/build/AutoYou.app
servers/macos/intel/build/AutoYou-macOS-x86_64.dmg
servers/macos/intel/build/AutoYou-macOS-x86_64.dmg.sha256
```

Review `servers/macos/intel/build/AutoYou.app/Contents/Resources/Legal/LICENSE`, `THIRD-PARTY-NOTICES.md`, `NOTICE.txt`, and `sbom.cdx.json` before use. Use constitutes agreement to the AutoYou Terms of Use (EULA), License, Privacy Policy, responsibility terms, warranty disclaimer, and liability limits.
