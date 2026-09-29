# AutoYou Windows Build Guide

This guide covers the current Windows workflows for AutoYou.

## 1. Choose the workflow you need

- Run the full server from source
- Package the full Windows server build
- Run or package the lightweight `autoyou-lite` service

## 2. Run the full server from source

Full:

```powershell
.\run_autoyou.bat --profile full
```

More control:

```powershell
python scripts\bootstrap_autoyou.py --profile recommended
python scripts\bootstrap_autoyou.py --profile full
python scripts\bootstrap_autoyou.py --profile recommended --install-only
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

The bootstrap script handles:

- `.venv` creation
- Python dependency installation
- `node/whatsapp` dependency installation
- Tunnelmole pre-seeding for OTP pairing

## 4. Build the full packaged Windows server

Use the packaged build flow when you want the full Windows tray-host experience, not just a source checkout.

### Prerequisites for packaging

- .NET 8 SDK
- Visual Studio 2022 C++ tools
- Visual Studio LLVM/Clang component
- Node.js 18 or newer
- Python available in the repo environment

Prepare the repo environment first if needed:

```powershell
python scripts\bootstrap_autoyou.py --profile recommended --install-only
```

If your main `.venv` uses Python 3.13, the Windows backend build automatically prefers a separate Python 3.12 build environment. It discovers an explicit `AUTOYOU_BUILD_PYTHON312` path first, then standard system or per-user installations and the Windows `py -3.12` launcher.

The preferred compiler is Visual Studio 2022 integrated LLVM/Clang. A standalone LLVM installation is not sufficient for Nuitka's Windows ClangCL mode. When Python 3.12 is active and the integrated component is unavailable, the build falls back to Nuitka's supported MinGW64 toolchain.

The full connector profile compiles EmotiVoice inference modules into the
server backend; the default profile omits the optional voice runtime. Model
checkpoints are not bundled. An administrator can approve their download from
Admin → Speech, where they are saved in the managed voice-model directory.

### Machine-specific preflight

Collect a privacy-safe machine-class profile once, then refresh it after major
OS, toolchain, or hardware changes:

```powershell
python scripts\build_machine_intel.py collect `
  --workspace . `
  --family "AMD Strix Halo" `
  --output .llm\machines\profiles\windows-amd-strix-halo.json

python scripts\build_machine_intel.py validate --all
python scripts\build_machine_intel.py recommend `
  --profile .llm\machines\profiles\windows-amd-strix-halo.json `
  --workload windows-release-suite
```

The profile excludes hostnames, usernames, serial numbers, network addresses,
credentials, environment values, and absolute user paths. A future build agent
should stop on recommendation blockers before invoking Nuitka.

If a build fails, classify the log before retrying:

```powershell
python scripts\build_machine_intel.py classify-log path\to\build.log
```

Only actual memory pressure should reduce parallel jobs. Compiler mismatch,
missing components, Node audit findings, PowerShell defects, and provenance
errors need a scoped fix instead of a slower repeat.

### Full build

```powershell
.\servers\windows\build-all.ps1
```

### Connector-capable build

```powershell
.\servers\windows\build-all.ps1 -ReleaseProfile connector-full
```

### Backend only

```powershell
.\servers\windows\build-backend.ps1
```

### Publish the native host with an existing backend

```powershell
.\servers\windows\publish-desktop.ps1 -SkipBackend
```

### Create release artifacts

Portable zip:

```powershell
.\servers\windows\package-release.ps1 -Clean
```

Portable zip plus installer:

```powershell
.\servers\windows\package-release.ps1 -Clean -IncludeInstaller
```

### Main output

Launch this for packaged testing:

```text
servers/windows/dist/AutoYou-win-x64/AutoYou.exe
```

## 5. Run the lightweight service on Windows

Bootstrap path:

```powershell
python scripts\bootstrap_autoyou.py --service autoyou-lite --profile recommended
```

That starts the lightweight service at:

- `http://127.0.0.1:8099/`

## 6. Build the PyPI package

From the repository root:

```powershell
python -m pip install --upgrade build twine
python -m build autoyou_lite
python -m twine check autoyou_lite/dist/*
```

For local source development of the package:

```powershell
pip install -e autoyou_lite
python -m autoyou_lite.server --host 127.0.0.1 --port 8099 --auth-port 8098
```

## 7. Troubleshooting

- If bootstrap cannot enable WhatsApp, install Node.js and rerun without `--skip-node`.
- If Signal remains unavailable, start Docker Desktop before launching AutoYou.
- If the packaged build complains about Clang or Visual Studio components, install the LLVM/Clang tools for Visual Studio 2022.
- If the packaged build cannot produce an installer, install Inno Setup 6 or pass `-InnoSetupCompiler`.
- If OTP pairing cannot expose a tunnel, run `python scripts/download_tunnelmole.py` or set `AUTOYOU_TUNNELMOLE_BIN`.

## 8. Related docs

- [servers/windows/README.md](../servers/windows/README.md)
- [installation-steps.md](installation-steps.md)
- [BUILD_SCRIPT_VALIDATION.md](BUILD_SCRIPT_VALIDATION.md)
