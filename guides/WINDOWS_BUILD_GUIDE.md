# Windows source build guide

Use the [Windows server instructions](../servers/windows/README.md) for current
prerequisites, profiles, output paths, and licensing.

From the repository root:

```powershell
.\servers\windows\build-all.ps1 -Configuration Debug
```

This selects an unofficial local build. Review the local license prompt before
using `-AcceptTerms` for unattended builds. Build scripts install and reconcile
packages, so use a dedicated build environment and keep unrelated credentials
out of it.

Official release, signing, installer, and store workflows have separate
authorization requirements. They are not needed for this local build.
See [validation guidance](BUILD_SCRIPT_VALIDATION.md) before sharing output.
