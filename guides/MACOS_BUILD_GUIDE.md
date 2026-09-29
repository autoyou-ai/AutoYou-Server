# AutoYou macOS Build Guide

This guide covers the current macOS workflows for AutoYou.

All full-server, Lite-server, and Intel bundle metadata uses the repository
`VERSION` file, currently `81.0.0`.

## 1. Choose the workflow you need

- Run the full server from source
- Package the full macOS app
- Run or package the lightweight `autoyou-lite` service

## 2. Run the full server from source

Full trial:

```bash
./run_autoyou.sh --profile full
```

Other common variants:

```bash
./run_autoyou.sh --profile full
./run_autoyou.sh --profile recommended --install-only
python scripts/bootstrap_autoyou.py --profile base
```

That starts the full server at:

- Admin UI: `http://127.0.0.1:8001/`
- AI agent: `http://127.0.0.1:8081/`
- Auth/signaling: `http://127.0.0.1:8002/`

## 3. Source-mode prerequisites

Required:

- Python 3.10 or newer

Optional, depending on features:

- Node.js 18 or newer for WhatsApp
- Docker Desktop for Signal
- Ollama for local models

The bootstrap path handles:

- `.venv` creation
- Python dependency installation
- `node/whatsapp` dependency installation
- Tunnelmole pre-seeding for OTP pairing
- Playwright browser installation when the internet profile is active

## 4. Build the full packaged macOS app

Use the packaged build flow when you want the native `AutoYou.app` experience rather than a source checkout.

### Prerequisites for packaging

- Xcode command-line tools
- Swift toolchain
- Python 3
- Node.js
- npm

If you plan to build the full connector-capable stack with voice features, install the native audio dependencies used by the build:

```bash
brew install sox portaudio node
```

The full connector profile includes EmotiVoice inference compiled for the
target Mac. The default server profile omits its optional voice runtime. Model
checkpoints are never bundled; approve their download from Admin → Speech.

### Fast unsigned build

```bash
./servers/macos/build-all.sh --no-sign --dev
```

### Local Apple Development signed build

This is useful for TCC/privacy-permission testing on a development Mac. It is
not a public distribution build and will still be rejected by Gatekeeper.

```bash
./servers/macos/build-all.sh \
  --full \
  --release \
  --sign \
  --certificate-name "Apple Development" \
  --team-id TEAMID
```

### Developer ID release build

```bash
./servers/macos/build-all.sh \
  --full \
  --sign \
  --release \
  --certificate-name "Developer ID Application: Your Name (TEAMID)" \
  --team-id TEAMID \
  --notarize
```

Use this lane for the full `AutoYou.app` DMG distributed outside the Mac App
Store. The full server app is not a Mac App Store target because it packages the
server runtime, local services, Remote Desktop, and automation-oriented helpers.

### Connector-capable build

```bash
./servers/macos/build-all.sh --release-profile connector-full --requirements full
```

You can also use the shortcut:

```bash
./servers/macos/build-all.sh --full
```

### Reuse existing backend output

Reuse the packaged backend and rebuild only the host/signing path:

```bash
./servers/macos/build-all.sh --skip-backend
```

Reuse the existing Nuitka launcher and rerun the cheaper post-compile backend steps:

```bash
./servers/macos/build-all.sh --skip-backend-compile
```

### Backend only

```bash
./servers/macos/build-backend.sh --type release --python python3.12
```

### Main outputs

```text
servers/macos/build/AutoYou.app
servers/macos/build/AutoYou.dmg
```

Launch the app bundle directly for packaged testing.

## 5. Notarization

`build-all.sh --notarize` forwards the configured Team ID to the notarization
helper. You can also notarize an already-built Developer ID signed DMG:

```bash
./servers/macos/notarize.sh servers/macos/build/AutoYou.dmg \
  --apple-id you@example.com \
  --password APP_SPECIFIC_PASSWORD \
  --team-id TEAMID
```

## 6. Run the lightweight service on macOS

Bootstrap path:

```bash
./run_autoyou.sh --service autoyou-lite --profile recommended
```

That starts the lightweight service at:

- `http://127.0.0.1:8099/`

## 7. Build the PyPI package

From the repository root:

```bash
python -m pip install --upgrade build twine
python -m build autoyou_lite
python -m twine check autoyou_lite/dist/*
```

For local source development of the package:

```bash
pip install -e autoyou_lite
python -m autoyou_lite.server --host 127.0.0.1 --port 8099 --auth-port 8098
```

## 8. Troubleshooting

- If the source bootstrap cannot enable WhatsApp, install Node.js and rerun without `--skip-node`.
- If Signal remains unavailable, start Docker before launching AutoYou.
- If local models do not respond, open Ollama or run `ollama serve`.
- If the packaged build fails early, verify `xcodebuild`, `swift`, `python3`, `node`, and `npm` are all available.
- If signing fails, verify the certificate name and Team ID exactly match the values in your Apple tooling.
- If Screen Recording remains stuck, confirm the installed `/Applications/AutoYou.app` is not ad-hoc signed and that nested executables, especially `Contents/Resources/backend/AutoYou.dist/AutoYouServer`, carry the same Apple team identity.
- If `spctl` rejects an Apple Development signed build, that is expected. Public DMGs require a `Developer ID Application` identity plus notarization.

## 9. Related docs

- [servers/macos/README.md](../servers/macos/README.md)
- [installation-steps.md](installation-steps.md)
- [BUILD_SCRIPT_VALIDATION.md](BUILD_SCRIPT_VALIDATION.md)
