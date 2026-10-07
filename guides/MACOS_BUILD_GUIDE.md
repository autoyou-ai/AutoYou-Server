# macOS source build guide

Use the [macOS server instructions](../servers/macos/README.md) and
[Intel notes](../servers/macos/intel/README.md) for prerequisites, architecture
limits, profiles, and output paths. The app version comes from the repository
`VERSION` file, currently `81.0.0`.

From the repository root:

```bash
./servers/macos/build-all.sh --no-sign --dev
```

This selects an unsigned unofficial development build. Review the license and
notices before using `--accept-terms` for unattended builds. Scripts may
install or reconcile dependencies; use a dedicated build environment.

Intel voice/full profile declarations currently conflict with the shared NumPy
lock. Resolve and validate a compatible dependency set before claiming that
profile works; see [dependency guidance](../requirements/README.md).

Official signing and notarization have separate authorization requirements.
See [validation guidance](BUILD_SCRIPT_VALIDATION.md) before sharing output.
