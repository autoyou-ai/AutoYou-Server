# AutoYou Server for WSL/Linux

This directory builds a Linux/WSL standalone server backend with Nuitka.
It uses the repository `VERSION` value, currently `81.0.2`, and includes selected
runtime packages and their legal notices.

## Local build

Use WSL2 or Linux x86_64 with a supported Python 3.11 or 3.12 environment,
gcc/g++, patchelf, Node.js and npm. Build packages are declared in
`servers/wsl/requirements.txt`.

From the repository root:

```bash
./servers/wsl/build-backend.sh --unofficial --install-build-deps
```

The default interpreter is `.venv/bin/python`; use `--python` for an explicit
build interpreter. The command installs dependencies into that environment.
Even without `--install-build-deps`, the script installs RealtimeSTT and may
change packages during runtime reconciliation. It is not an offline-only
build command.

Review the license and notices when prompted. Add `--accept-terms` only after
reviewing them for an unattended build. A local unofficial build does not need
official release authorization.

Optional tuning dependencies require `--include-tuning`. They have a separate
advisory and compatibility scope; see [dependency guidance](../../requirements/README.md).
The default compiler concurrency is `min(nproc, 16)`. Use `--jobs N` to choose
an explicit limit.

## Output and operation

The normal backend executable is:

```text
servers/wsl/artifacts/backend/AutoYouServer/AutoYou
```

Keep the executable together with its `runtime_modules/`, `runtime_stdlib/`,
`runtime_site_packages/`, native libraries, resources, and legal bundle.
Copying the executable alone does not copy the complete runtime.

Review `Legal/LICENSE`, `THIRD-PARTY-NOTICES.md`, `NOTICE.txt`, and
`sbom.cdx.json` in the packaged backend. Use constitutes agreement to the
AutoYou Terms of Use (EULA), License, responsibility terms, warranty disclaimer,
and liability limits to the extent permitted by law.

For local operation:

```bash
./servers/wsl/artifacts/backend/AutoYouServer/AutoYou --host 127.0.0.1 --admin 8001 --ai-agent 8081 --auth 8002
```

### Network hosting (WSL / Docker virtual IP)

When running inside WSL2 or a Docker container and reaching the server from Windows or other devices via IP (e.g. `172.x.x.x`):

1. **Bind to network & HTTPS**:
   Start with `--host 0.0.0.0` (or `AUTOYOU_BIND_HOST=0.0.0.0`) and HTTPS enabled (`AUTOYOU_HTTPS_ENABLED=1`, port 8443 by default). Connect over `https://<ip>:8443/`. (Plain HTTP from non-loopback IPs is refused for admin sign-in). Alternatively, reverse-proxy TLS with `AUTOYOU_TRUSTED_HTTPS_PROXY=1`.
2. **Network admin permissions**:
   Set `AUTOYOU_ALLOW_REMOTE_ADMIN_PERMISSIONS=1` (or click **Allow network admin permissions next boot** in the Admin UI Overview). This allows authenticated admins connecting over the virtual container/WSL IP to manage hardware, video, and audio capture permissions. Public internet IPs and public tunnels remain blocked from modifying permissions.

Set a unique administration password and review access settings before enabling
remote connections. WSL does not directly control the Windows Bluetooth radio.
The optional `scripts/bluetooth_pair_host_bridge.py` helper runs separately on
the Windows host; review its configuration and access before enabling it.
Desktop capture/input tools require a compatible display session and explicit
device access; installing the dependencies alone does not supply either.

## Notices and distribution

Review `Legal/LICENSE`, `Legal/THIRD-PARTY-NOTICES.md`, `Legal/NOTICE.txt`,
and `Legal/sbom.cdx.json` in the output.
Use constitutes agreement to the applicable license terms, including the
warranty disclaimer and liability limits to the extent permitted by law.
Separate services have separate terms.

Free sharing is permitted under [LICENSE](../../LICENSE). Retain the
`UNOFFICIAL_BUILD` identification, distinguish modifications, and comply with
the licenses of the actual bundled components. Official release and Snap
packaging have separate gates and do not accept an unofficial build as an
official artifact.
