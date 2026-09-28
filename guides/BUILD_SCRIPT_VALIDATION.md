# Build Script Validation Guide

Use this guide when you need to validate AutoYou's bootstrap, packaging, or release automation after a code change.

This is intentionally a checklist, not a frozen claim that a specific past build is still perfect.

## 1. Validate the source bootstrap path

The main bootstrap entrypoint is:

```bash
python scripts/bootstrap_autoyou.py --help
```

Current important facts from the script:

- default profile is `recommended`
- supported services are `autoyou` and `autoyou-lite`
- bootstrap can skip Playwright, Node, Docker, Ollama, or Tunnelmole pre-seeding
- `autoyou-lite` defaults to ports `8099` and `8098`
- the full server defaults to ports `8001`, `8081`, and `8002`

Useful validation commands:

```bash
python scripts/bootstrap_autoyou.py --profile recommended --install-only
python scripts/bootstrap_autoyou.py --service autoyou-lite --profile recommended --install-only
```

## 2. Validate the full Windows packaging flow

Current Windows scripts:

- `servers/windows/build-backend.ps1`
- `servers/windows/publish-desktop.ps1`
- `servers/windows/build-all.ps1`
- `servers/windows/package-release.ps1`

Supported release profiles:

- `binary-default`
- `connector-full`

Validation commands:

```powershell
.\servers\windows\build-backend.ps1
.\servers\windows\publish-desktop.ps1 -SkipBackend
.\servers\windows\build-all.ps1
.\servers\windows\package-release.ps1 -Clean
```

Expected main output:

```text
servers/windows/dist/AutoYou-win-x64/AutoYou.exe
```

Optional installer validation:

```powershell
.\servers\windows\package-release.ps1 -Clean -IncludeInstaller
```

## 3. Validate the full macOS packaging flow

Current macOS scripts:

- `servers/macos/build-backend.sh`
- `servers/macos/build-frontend.sh`
- `servers/macos/sign-and-compress.sh`
- `servers/macos/build-all.sh`
- `servers/macos/notarize.sh`

Current important `build-all.sh` options:

- `--no-sign`
- `--sign`
- `--dev`
- `--release`
- `--requirements`
- `--release-profile`
- `--full`
- `--skip-backend`
- `--skip-backend-compile`
- `--jobs`

Validation commands:

```bash
./servers/macos/build-all.sh --no-sign --dev
./servers/macos/build-backend.sh --type release
./servers/macos/build-frontend.sh
```

`build-all.sh` is the command that should produce the final packaged outputs:

```text
servers/macos/build/AutoYou.app
servers/macos/build/AutoYou.dmg
```

Use `build-backend.sh` and `build-frontend.sh` to validate their individual phases when you are debugging the pipeline rather than running the full orchestrator.

## 4. Validate the lightweight package build

The lightweight service is packaged from `autoyou_lite/`.

Validation commands:

```bash
python -m pip install --upgrade build twine
python -m build autoyou_lite
python -m twine check autoyou_lite/dist/*
```

For local source validation:

```bash
pip install -e autoyou_lite
python -m autoyou_lite.server --host 127.0.0.1 --port 8099 --auth-port 8098
```

## 5. Validate the main integration assumptions

Current expectations in this repo:

- WhatsApp source mode depends on Node.js and the `node/whatsapp` dependencies.
- Signal depends on Docker at runtime.
- Source bootstrap pre-seeds the `tmole` binary unless you skip that step.
- The full packaged Windows and macOS server builds bundle their own server runtime and release assets.
- `autoyou-lite` is a separate lightweight build and release path, not the full server package.

## 6. Check the main outputs, not just command success

After a build, validate the real artifacts:

- full Windows bundle exists
- full macOS app or DMG exists
- release archives exist when requested
- `autoyou_lite/dist/` contains the expected protected wheel for lightweight releases

## 7. Things this guide intentionally avoids

This guide does not hardcode a giant list of internal packaged files because those inventories go stale quickly and create false confidence.

Instead, validate:

- the current script flags
- the current output locations
- the current release-profile choices
- the current runtime boundaries between bundled features and external dependencies
