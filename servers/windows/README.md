# AutoYou Server for Windows

This folder contains the Windows packaging workflow for the full AutoYou server.

Use it when you want a shareable Windows build of the full server, including the native tray host, bundled backend, and required runtimes.

The Lite server uses the same native tray host and `assets/logo.ico`, but has a
separate product profile, backend bundle, and Store package.

Windows host, backend, installer, and MSIX metadata default to the repository
`VERSION` file, currently `81.0.0`. Pass `-Version` only for an explicit
nonstandard test build.

## What it produces

The main published bundle is:

```text
servers/windows/dist/AutoYou-win-x64/
```

Key contents:

- `AutoYou.exe` - user-facing Windows tray host
- `Backend/AutoYou.exe` - packaged full-server backend
- `Backend/runtime/node/` - bundled Node runtime for integrations such as WhatsApp
- `Backend/runtime/tunnelmole/` - bundled Tunnelmole runtime for pairing flows

Review `servers/windows/dist/AutoYou-win-x64/Legal/LICENSE`, `THIRD-PARTY-NOTICES.md`, `NOTICE.txt`, and `sbom.cdx.json` before use. Use constitutes agreement to the AutoYou Terms of Use (EULA), License, Privacy Policy, responsibility terms, warranty disclaimer, and liability limits.

## Requirements

- Windows
- .NET 8 SDK
- Python
- Node.js
- PowerShell

If you want to build the installer, also install Inno Setup 6.

## Quick build

From the repository root:

```powershell
.\servers\windows\build-all.ps1
```

That builds the default commercial binary lane, `binary-default`.

Use the broader connector-capable build only when you explicitly need it:

```powershell
.\servers\windows\build-all.ps1 -ReleaseProfile connector-full
```

For a package that must run Fine Tuning Agent jobs, use the combined full-server
and trainer profile. Data Collector's website/chat surface is bundled in every
full-server package; this profile adds the optional ML dependencies needed for
training rather than dataset preparation alone:

```powershell
.\servers\windows\build-all.ps1 -ReleaseProfile training-full
```

The packaged backend verifies both agents' website backends, chat facades, and
static runtime assets. Training cancellation is persisted by the Fine Tuning
Agent; Data Collector cancellation is a safe boundary signal, so neither needs
a server restart to stop a running job.

Before a clean release build, validate the current machine profile and inspect
the recommended Python, compiler, job count, memory, and storage policy:

```powershell
python scripts\build_machine_intel.py validate --all
python scripts\build_machine_intel.py recommend `
  --profile .llm\machines\profiles\windows-amd-strix-halo-ryzen-ai-max-plus-395-16c-32t-64gb.json `
  --workload windows-release-suite
```

Use `scripts/build_machine_intel.py collect` to add a privacy-safe profile for a
different machine class. See
`docs/development/build-machine-intelligence.md`.

## Common tasks

Backend only:

```powershell
.\servers\windows\build-backend.ps1
```

Publish the native host with an already-built backend:

```powershell
.\servers\windows\publish-desktop.ps1 -SkipBackend
```

Create a portable release archive:

```powershell
.\servers\windows\package-release.ps1 -Clean
```

Create a portable archive plus installer:

```powershell
.\servers\windows\package-release.ps1 -Clean -IncludeInstaller
```

Create a Microsoft Store MSIX package:

```powershell
.\servers\windows\publish-desktop.ps1 -ReleaseProfile connector-full
.\servers\windows\package-msix.ps1 `
  -IdentityName <PACKAGE_IDENTITY_NAME> `
  -Publisher <PACKAGE_IDENTITY_PUBLISHER>
```

Use the app's Partner Center **Product management > App identity** values for
`-IdentityName` and `-Publisher`. Microsoft re-signs Store MSIX submissions
after certification, so this path does not require a separate Authenticode
certificate.
Use `-ReleaseProfile connector-full` for the full-feature server package.

The full server package is large. If `C:\` is low on space, put generated MSIX
output on `E:\`:

```powershell
.\servers\windows\publish-desktop.ps1 -ReleaseProfile connector-full
.\servers\windows\package-msix.ps1 `
  -IdentityName <PACKAGE_IDENTITY_NAME> `
  -Publisher <PACKAGE_IDENTITY_PUBLISHER> `
  -OutputDir E:\AutoYou-msix\server
```

Successful packaging removes the generated staging folder; pass `-KeepStaging`
only when you need to inspect the generated manifest.

## AutoYou Lite desktop and MSIX

Build the protected Lite backend first, or point `-LiteBundleRoot` at an
existing `autoyou_lite/dist/windows/autoyou-lite-*-windows-*` directory:

```powershell
python scripts\build_autoyou_lite_windows_binary.py --wheel <protected-wheel>
.\servers\windows\publish-lite-desktop.ps1 -Configuration Debug
.\servers\windows\package-lite-msix.ps1 `
  -IdentityName <LITE_PACKAGE_IDENTITY_NAME> `
  -Publisher <LITE_PACKAGE_IDENTITY_PUBLISHER> `
  -SkipOfficialBuildAuthorization
```

Release publishing normally requires a signed authorization record whose
artifact profile is exactly `autoyou-lite-server`. While the new Lite Store
product is being added to the authorization scope, an owner-controlled local
Store candidate can be built with:

```powershell
.\servers\windows\publish-lite-desktop.ps1 -Configuration Release -SkipOfficialBuildAuthorization
```

This is an explicit override and does not
create or claim a signed authorization record. The strict release legal gate
still runs.

The published user entrypoint is
`servers/windows/dist/AutoYou-Lite-win-x64/AutoYou Lite.exe`. It runs without a
console window, owns the `assets/logo.ico` taskbar and tray icon, supervises
`Backend/autoyou-lite.exe`, and opens `http://127.0.0.1:8099/` after the Lite
health endpoint is ready. The MSIX package is written under
`servers/windows/dist/msix/` and its local structure can be checked with the
`VerifyCommand` printed by `package-lite-msix.ps1`.

This packages the user-launched full-trust desktop host, not a Windows service.
It declares `runFullTrust`, local network access, and Bluetooth capability, so
those should be explained in the Store submission notes.
The packaged host starts the backend with `%APPDATA%\AutoYou` as the working
directory, and runtime config/data/logs stay in user data rather than the MSIX
install folder.

Create the finalized Lite portable archive and Inno Setup installer:

```powershell
.\servers\windows\package-lite-release.ps1 `
  -Clean `
  -IncludeInstaller `
  -InnoSetupCompiler "C:\Users\You\AppData\Local\Programs\Antigravity IDE\resources\app\node_modules\innosetup\bin\ISCC.exe"
```

This produces an Inno Setup `.exe` installer and a `.zip` archive under
  `servers/windows/dist/release/lite/`. Inno Setup does not produce an MSI file. The
MSIX remains the Microsoft Store package. The MSIX `PublisherDisplayName` is
`OpenStorey`; its `Publisher` value must remain the exact Partner Center app
identity CN passed with `-Publisher`.

## What to launch

For packaged testing, launch:

```text
servers/windows/dist/AutoYou-win-x64/AutoYou.exe
```

Do not use the backend executable directly for normal packaged-user testing.

## Runtime behavior

- mutable data lives under `%APPDATA%\AutoYou\`
- logs live under `%APPDATA%\AutoYou\logs\`
- the tray host starts and supervises the packaged backend

## Troubleshooting

- If voice-related binaries are missing at runtime, rebuild the backend and confirm the packaging scripts copied the native speech dependencies.
- If packaging fails late, verify Node, Python, and .NET are installed and available on `PATH`.
- If the output bundle exists but does not run, test the backend and tray host separately to isolate whether the issue is in packaging or runtime startup.

## Related docs

- [servers/README.md](../README.md)
- [guides/WINDOWS_BUILD_GUIDE.md](../../guides/WINDOWS_BUILD_GUIDE.md)
- [root README](../../README.md)
