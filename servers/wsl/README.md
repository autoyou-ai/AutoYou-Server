# AutoYou Server for WSL/Linux

This folder builds the full AutoYou server backend as a Linux/WSL Nuitka standalone bundle.

The bundle embeds the repository `VERSION` file and therefore reports the
canonical server release, currently `81.0.0`. The Snap scaffold consumes that
same root file rather than maintaining a separate package version.

The build follows the same packaged-runtime model as the Windows and macOS server builds:

- `autoyou_app.py` is compiled into the launcher binary.
- AutoYou runtime modules are compiled into `runtime_modules/`.
- Python standard-library modules are copied into `runtime_stdlib/`.
- Third-party Python packages are copied into `runtime_site_packages/`.
- Release legal files are copied into `Legal/`.
- `runtime_integrity.json` seals the compiled runtime modules.
- The full WSL profile compiles the EmotiVoice inference modules; voice model
  checkpoints stay administrator-approved user data.
- `scripts/verify_backend_hardening.py` rejects raw AutoYou source; the package bridge is emitted as bytecode.

Review `Legal/LICENSE`, `Legal/THIRD-PARTY-NOTICES.md`, `Legal/NOTICE.txt`, and `Legal/sbom.cdx.json` before use. Use constitutes agreement to the AutoYou Terms of Use (EULA), License, Privacy Policy, responsibility terms, warranty disclaimer, and liability limits.

## Output

After a successful build:

```text
servers/wsl/artifacts/backend/AutoYouServer/AutoYou
servers/wsl/artifacts/backend/AutoYouServer/Legal/
```

Run it directly:

```bash
./servers/wsl/artifacts/backend/AutoYouServer/AutoYou --host 127.0.0.1 --admin 8001 --ai-agent 8081 --auth 8002
```

The binary name defaults to server mode. Passing `--run-server` is also supported.

## Requirements

- WSL2 or Linux x86_64
- Python 3.11 or 3.12, preferably the repo `.venv`
- `gcc` and `g++`
- `patchelf`
- Node.js 18+ and npm for the build (the compiled WSL bundle includes its Linux Node runtime)
- Python build packages in the selected environment:
  - Nuitka
  - zstandard
  - ordered-set

If WSL networking is unavailable but Windows networking works, you can download wheels from Windows into a WSL directory and install them offline:

```powershell
python -m pip download --dest "\\wsl.localhost\Ubuntu\tmp\autoyou-wheelhouse" nuitka ordered-set zstandard
```

Then from WSL:

```bash
.venv/bin/python -m pip install --no-index --find-links /tmp/autoyou-wheelhouse nuitka ordered-set zstandard
```

If `patchelf` cannot be installed with `apt`, set `AUTOYOU_PATCHELF` to an extracted `patchelf` binary or place it at `.venv/native/patchelf/bin/patchelf`.

## Bluetooth Pair

WSL does not own the Windows Bluetooth radio. Leave Bluetooth Pair enabled in
AutoYou, then run the host bridge on Windows or macOS from an environment with
`requirements/bluetooth.txt` installed:

```bash
python scripts/bluetooth_pair_host_bridge.py --server http://127.0.0.1:8001 --password 1234
```

The bridge advertises the BLE service on the host OS and forwards the existing
Auto Pair reconnect exchange to the WSL server.

## Native Remote Desktop Video and Optional Agent

Native Remote Desktop video/control in **Video & Calls** and the optional
`remote_desktop_agent` both use `mss` for capture and `pyautogui` for
keyboard/mouse injection. These backends are X11-only on Linux; there is no
Wayland-native fallback. They need:

- `requirements/desktop-automation.txt` installed (`mss`, `pyautogui`,
  `pygetwindow`) — part of the standard `requirements/full.txt` chain, but
  worth confirming explicitly if you built a venv from a narrower profile.
- A live X11 display reachable via `$DISPLAY` when the server process starts.
  **WSLg** (WSL2's built-in display, already running by default on most
  installs — check with `xdpyinfo` or `echo $DISPLAY`) is sufficient; a
  virtual X server such as Xvfb works too if WSLg isn't available. Without
  either, the server stays running, but native calls report capture/control as
  unavailable and the optional agent cannot stream frames or inject input.

`pyautogui`/`pygetwindow` additionally need an `~/.Xauthority` file (or
`$XAUTHORITY`) to exist at all before python-xlib will even attempt a
connection — even on X servers like WSLg that don't enforce authentication.
The server creates an empty one automatically if missing
(`shared.remote_desktop_keyboard.ensure_x11_authority_exists`), so this is
usually transparent; it's only worth knowing about if you're calling
`pyautogui`/`pygetwindow` directly outside the server process.

## Build

From the repository root:

```bash
./servers/wsl/build-backend.sh --clean
```

For a local unofficial build while release authorization is pending, use
`./servers/wsl/build-backend.sh --clean --unofficial`. Official WSL builds use
the default path and require the release gates.

To include the optional Fine Tuning trainer stack, add `--include-tuning` (or
set `AUTOYOU_INCLUDE_TUNING=1`). Data Collector's website/chat surface is
always bundled with the full server; the flag is only needed to run actual
training jobs rather than dataset preparation/import:

```bash
./servers/wsl/build-backend.sh --clean --include-tuning
```

For an environment that already has the required packages, the script does not run pip by default. Use `--install-build-deps` to install both `servers/wsl/requirements.txt` and the native desktop automation requirements first.

The default job count is `min(nproc, 16)`. On a 32-thread / 16-core workstation, that means 16 parallel C compiler jobs. Use `--jobs max` to use every logical CPU, or `--jobs N` to set an exact value.

## Verification

The build script runs:

- launcher compile
- runtime module compile
- packaged server import smoke test
- managed frontend runtime import smoke tests
- Data Collector and Fine Tuning website/chat import smoke tests
- runtime bytecode scrub
- backend hardening verification

The compiled server can be smoke-tested with high ports:

```bash
AUTOYOU_TEST_ROOT=/tmp/autoyou-wsl-smoke \
AUTOYOU_SHUTDOWN_TOKEN=local-smoke \
./servers/wsl/artifacts/backend/AutoYouServer/AutoYou \
  --host 127.0.0.1 --admin 18001 --ai-agent 18081 --auth 18002
```

Then probe:

```bash
curl http://127.0.0.1:18001/api/login-startup-status
curl -X POST -H "X-AutoYou-Shutdown-Token: local-smoke" http://127.0.0.1:18001/shutdown
```

## Snap packaging (scaffold)

`snap/` contains a structural Ubuntu/Snap Store packaging scaffold over this
folder's build output. It is not a submitted or reviewed release — see
[snap/README.md](snap/README.md) before running `snapcraft`.
