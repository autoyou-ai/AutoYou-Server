# AutoYou Server Packaging

This folder contains the packaged full-server build projects for Windows,
macOS, and Linux/WSL.

If you simply want to run AutoYou from source, use the [root README](../README.md) instead of starting here.

## Version policy

The repository [`VERSION`](../VERSION) file is the canonical server release
version, currently `81.0.0`. Windows/MSIX, macOS (full, Lite, and Intel),
Linux/WSL, Snap, Docker, and Python update metadata must consume that value;
platform packaging scripts must not maintain an independent server version.

## Pick the right platform

| Folder | What it builds |
| --- | --- |
| [windows/](windows/README.md) | The packaged Windows AutoYou server and desktop host |
| [macos/](macos/README.md) | The packaged macOS AutoYou app and DMG |
| [wsl/](wsl/README.md) | The packaged Linux/WSL AutoYou server backend |

## When to use these folders

Use the packaging folders when you need to:

- create a shareable desktop/server build
- test the native host experience
- prepare a Windows or macOS release
- validate bundled runtimes like Node, Tunnelmole, and browser assets

## Full-server module boundary

All three backend builds compile the same Python architecture: `server.py` is
the composition root, `routers/` contains route handlers, and `core_server/`
contains app/state/config/security helpers plus service lifecycle and the
WebRTC engine. `scripts/build_packaged_runtime_modules.py` includes those roots
with `shared/` and `autoyou_agents/`; do not repair a package by moving their
code back into `server.py`.

The compiled `.pyd`/`.so` files are produced by Nuitka. AutoYou has no
first-party Rust crate or `autoyou_native` build path at HEAD, although bundled
third-party dependencies may contain their own native or Rust-backed
extensions.

## Data Collector and Fine Tuning contract

Both agent backends and their chat facades are compiled with the full server.
Their frontend files, manifests, and Data Collector's Node history worker remain
read-only runtime assets and are integrity checked. Windows, macOS, and WSL
build scripts smoke-import both website backends and chat facades, then verify
the required assets without starting a live server.

`tuning` is only the optional ML dependency component. `training-full` is the
full AutoYou dependency profile plus `tuning`, used when the Fine Tuning Agent
must run training rather than only prepare/import datasets:

| Runtime | Training-enabled selection |
| --- | --- |
| Source bootstrap | `python scripts/bootstrap_autoyou.py --profile training-full` |
| Windows package | `build-all.ps1 -ReleaseProfile training-full` |
| macOS package | `build-all.sh --requirements training-full` |
| WSL/Linux package | `build-backend.sh --include-tuning` |
| Docker image | `--build-arg AUTOYOU_INCLUDE_TUNING=1` |

Standard Docker images intentionally copy source for local/private use. The
compiled Linux Dockerfile follows the protected package model instead. The
Data Collector direct port is never the browser/public surface; it remains
behind the authenticated Page Service proxy.

## When not to use these folders

Do not start here if you only want to:

- run AutoYou locally from source
- change server settings and start using the app quickly
- work only on the mobile or Python clients

For that, go back to:

- [root README](../README.md)
- [clients/README.md](../clients/README.md)

## Related docs

- [guides/README.md](../guides/README.md)
- [tests/README.md](../tests/README.md)
