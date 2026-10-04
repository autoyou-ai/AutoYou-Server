# AutoYou Server — Snap packaging scaffold

This is a **structural scaffold** for a future Ubuntu/Snap Store submission
of the AutoYou server backend. It has not been built with `snapcraft`,
reviewed, or uploaded anywhere.

The Snap version is adopted from the repository root `VERSION` file, currently
`81.0.0`; it is not declared independently in `snapcraft.yaml`.

Running it end to end requires:

1. `snapcraft` installed (`sudo snap install snapcraft --classic`).
2. A completed, current build:
   ```bash
   ./servers/wsl/build-backend.sh --clean --jobs max
   ```
   `snap/snapcraft.yaml` dumps `servers/wsl/artifacts/backend/AutoYouServer`
   as-is, so it must exist and be current before packaging.
3. From `servers/wsl/`, run `snapcraft` to produce a `.snap` file.
4. To actually publish: `snapcraft login`, `snapcraft register autoyou-server`
   (name availability is not guaranteed), then `snapcraft upload` — under
   your own Snap Store publisher account. None of that is done here.

## Confinement

`devmode` / `grade: devel` for now, deliberately not `strict`. The server
needs outbound network to reach Ollama/cloud services, inbound `network-bind`
for its admin/AI-agent/auth ports, and `home` for user config and session
storage. Before requesting a store review, revisit this against `strict`
confinement with explicit interface connections — Snap Store review for a
background network daemon under `devmode` is unlikely to pass as-is.

## Known gaps versus a real release

- **Bluetooth Pair**: WSL does not own the Windows Bluetooth radio regardless
  of snap confinement. The existing host-bridge pattern in the main
  `servers/wsl/README.md` (`scripts/bluetooth_pair_host_bridge.py`) still
  applies; a snap cannot fix this.
- **Default bind host**: the packaged `command:` binds `127.0.0.1` by design
  (matches the existing WSL smoke-test recipe). Reaching it from Windows over
  the WSL2 network needs the operator's own port-forwarding/firewall setup,
  same as running the unpackaged binary — the snap does not change that.
- **Legal/build-access gate**: the default `build-backend.sh` path requires
  signed official-build authorization for the `autoyou-server-source-full`
  artifact profile. A backend built with `--unofficial` is marked and this
  Snap scaffold refuses to package it. See
  `docs/legal/license-build-control-strategy.md`.
