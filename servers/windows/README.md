# AutoYou Server for Windows

Build the server backend and its native tray host on Windows. This is a server
package; separately distributed client applications require a running server.

## Local build

Prerequisites include the .NET 8 SDK, a supported Python environment, Node.js,
PowerShell, and the C/C++ compiler tools required by Nuitka. Review the script's
environment checks before building.

From the repository root:

```powershell
.\servers\windows\build-all.ps1 -Configuration Debug
```

Debug selects an unofficial development build without official release
authorization. Review the license and notices when prompted. Add
`-AcceptTerms` only after reviewing them for an unattended build.

The default dependency profile is `binary-default`. To include the optional
connector profile while retaining the local build route:

```powershell
.\servers\windows\build-all.ps1 -Configuration Debug -ReleaseProfile connector-full
```

The scripts install and reconcile dependencies. Use a dedicated environment,
allow sufficient disk space, and review [dependency guidance](../../requirements/README.md).
Python and native dependencies can impose narrower platform requirements.

## Output and sharing

The normal published build directory is:

```text
servers/windows/dist/AutoYou-win-x64/
```

It contains the native host, the packaged backend, and selected runtime files.
Version metadata comes from the repository `VERSION` file, currently `81.4.0`.

Review `servers/windows/dist/AutoYou-win-x64/Legal/LICENSE`,
`THIRD-PARTY-NOTICES.md`, `NOTICE.txt`, and `sbom.cdx.json`.
Use constitutes agreement to the AutoYou Terms of Use (EULA), License,
responsibility terms, warranty disclaimer, and liability limits to the extent
permitted by law.
Separate services have separate terms.

Free redistribution is permitted subject to [LICENSE](../../LICENSE). Identify
the build as unofficial, retain required notices, and satisfy the licenses of
everything actually bundled. A local build does not receive OpenStorey's
signing identity, endorsement, or support commitment.

## Official release tools

Release configuration, archive, installer, signing, and MSIX workflows have
additional authorization and legal gates. They are not prerequisites for the
Debug build above. Inno Setup is needed only for its installer workflow.
An app-store account, identity, or package listing is not supplied by this
source repository.

Strict release checks require the operator's external review file through
`AUTOYOU_RELEASE_CHECKLIST`. See [SECURITY.md](../../SECURITY.md). Report failures with sanitized logs;
do not publish build credentials or a live configuration.

## Remote Desktop on the lock screen

Windows shows the lock screen, sign-in screen, and UAC prompts on a protected
desktop that normal apps cannot see or send input to. To see and type into it
from a paired device (for example to type your password after the computer
locked itself), install the AutoYou Secure Desktop Helper once on the host:

- In the admin page, open **Video & Calls > Remote Desktop** and select
  **Install lock screen helper**, then approve the Windows prompt; or
- from an elevated PowerShell in the install folder, run
  `.\AutoYou.exe --secure-desktop install` (source checkout:
  `python autoyou_app.py --secure-desktop install`).

This registers the `AutoYouSecureDesktop` service (LocalSystem, automatic
start). It runs a helper in the signed-in console session that serves input
and screen capture only to AutoYou running as the account that installed it.
It also enables the `SoftwareSASGeneration` policy so Ctrl+Alt+Del from the
keyboard toolbar works. Remove it with `--secure-desktop uninstall`; check it
with `--secure-desktop status`. Logs are in
`%ProgramData%\AutoYou\SecureDesktop\secure-desktop.log`.

Without the helper, **Keep this computer awake** in the same section keeps the
display awake and the idle timer reset so Windows does not lock for
inactivity during a session.
