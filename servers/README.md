# Build AutoYou Server

These directories contain server packaging tools and native server hosts.
Separately distributed client applications connect to a running server and
are not built from these directories.

Every server package takes its version from the repository root `VERSION` file,
currently `81.0.2`.

- [Windows](windows/README.md)
- [macOS](macos/README.md), including [Intel](macos/intel/README.md)
- [WSL/Linux](wsl/README.md)

For a source installation, use the root
[bootstrap instructions](../README.md#run-from-source).

Local builds are unofficial. Review [LICENSE](../LICENSE) and
[THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md), preserve required notices,
and identify shared builds accurately. Free sharing is subject to the license;
Institutional Use and Commercial Exploitation require an Enterprise agreement.

Build scripts install or reconcile dependencies and may download native
runtimes. Use a dedicated build environment and keep unrelated credentials out
of it. A local build is not evidence of successful tests on another platform.
Official signing and release packaging have separate authorization controls.
