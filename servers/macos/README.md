# AutoYou Server for macOS

This folder contains the macOS packaging workflow for the full AutoYou server.

Use it when you want a native macOS app bundle for the full server rather than running from source.

The full, Lite, and Intel server packaging paths read the repository `VERSION`
file for generated bundle metadata. The tracked source plists currently match
the canonical `81.0.0` release.

## What it produces

The main build outputs are:

```text
servers/macos/build/AutoYou.app
servers/macos/build/AutoYou.dmg
```

The app bundle contains the packaged backend plus the native macOS host.

Review `servers/macos/build/AutoYou.app/Contents/Resources/Legal/LICENSE`, `THIRD-PARTY-NOTICES.md`, `NOTICE.txt`, and `sbom.cdx.json` before use. Use constitutes agreement to the AutoYou Terms of Use (EULA), License, Privacy Policy, responsibility terms, warranty disclaimer, and liability limits.

## Requirements

- macOS
- Xcode command-line tools
- Python 3.10 or newer
- Node.js
- npm
- Swift toolchain

If you want a signed public build, you need a `Developer ID Application`
signing identity. An `Apple Development` identity is useful for local testing,
but it is not accepted by Gatekeeper for public distribution.

## Quick build

From the repository root:

```bash
./servers/macos/build-all.sh --no-sign --dev
```

That is the fastest path for local packaging validation.

The default release profile is `binary-default`. Use the broader connector-capable lane only when you explicitly need it:

```bash
./servers/macos/build-all.sh --release-profile connector-full --requirements full
```

For a package that must run Fine Tuning Agent jobs, select `training-full`.
Data Collector's website/chat surface is bundled with the full server; this
profile adds the optional ML dependencies used by the training worker:

```bash
./servers/macos/build-all.sh --requirements training-full
```

The packaged backend verifies both agents' website backends, chat facades, and
static runtime assets. Fine Tuning cancellation is durable across the website
and AI worker; Data Collector stops at safe collection boundaries without a
server restart.

## Local signed build

Use Apple Development signing when validating macOS privacy prompts and local
runtime behavior on a development machine:

```bash
./servers/macos/build-all.sh \
  --full \
  --release \
  --sign \
  --certificate-name "Apple Development" \
  --team-id TEAMID
```

## Developer ID release build

```bash
./servers/macos/build-all.sh \
  --full \
  --release \
  --sign \
  --certificate-name "Developer ID Application: Your Name (TEAMID)" \
  --team-id TEAMID \
  --notarize
```

## What to launch

For packaged testing, open:

```text
servers/macos/build/AutoYou.app
```

## Notes

- This packaging flow is for the full server, not the lighter `AutoYou Connect` desktop client.
- The build scripts bundle the backend, required runtimes, and release assets into the final app.
- The full server app is intended for Developer ID distribution outside the Mac App Store, not Mac App Store sandboxing.

## Troubleshooting

- If the build cannot find Xcode tools, run `xcode-select --install`.
- If backend packaging fails, verify Python, Node, and npm are available before retrying.
- If signing fails, confirm the certificate name and team ID exactly match the values in your keychain and Apple account.

## Related docs

- [servers/README.md](../README.md)
- [guides/MACOS_BUILD_GUIDE.md](../../guides/MACOS_BUILD_GUIDE.md)
- [root README](../../README.md)
