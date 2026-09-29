# AutoYou Server

[![Public checks](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml/badge.svg?branch=main)](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Source-available license](https://img.shields.io/badge/license-source--available-6f42c1)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/autoyou-ai/AutoYou-Server?style=flat)](https://github.com/autoyou-ai/AutoYou-Server)

> A private, local-first AI server for conversations, voice, video, browser
> tools, and owner-controlled device connections.

Public repository links:

- Repository: [github.com/autoyou-ai/AutoYou-Server](https://github.com/autoyou-ai/AutoYou-Server)
- Public checks: [GitHub Actions workflow](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml)
- Security policy: [SECURITY.md](SECURITY.md)
- Source publication boundary: [docs/legal/source-publication-manifest.md](docs/legal/source-publication-manifest.md)

AutoYou Server is a local-first personal AI server. This repository contains
the server runtime, local administration interface, optional integrations,
tests, and packaging tooling needed to run and evaluate that server.

The current server architecture is modular: `server.py` composes the app,
`routers/` owns HTTP/WebSocket route registration, `core_server/` owns
state, security, service lifecycle, HTTP helpers, and WebRTC engine internals,
and `shared/` provides runtime encryption, peer linking, and intent routing.

AutoYou Server provides the complete standalone server backend, core server
runtime, HTTP and WebSocket endpoints, messaging connectors (Telegram, Signal,
WhatsApp), built-in AI agents, peer linking, local intent routing, and the
local admin web interface. Client applications and hosted account services are
distributed separately.

The optional [AutoYou Agents](https://github.com/autoyou-ai/AutoYou_agents)
checkout can sit beside this repository. Source runs discover its optional
agents and ignored `private/` overlay; server packages continue to include
only the built-in agents in this repository.

## Inside the local admin UI

These captures show the running Server admin interface. Account, Server ID,
and local network values were replaced with synthetic examples for the images;
service status reflects the captured setup. Select an image to see the full page.

| Overview | Live View | Security |
| --- | --- | --- |
| [<img src="docs/images/admin/overview.png" alt="Server overview dashboard showing local services and pairing controls" width="280">](docs/images/admin/overview.png) | [<img src="docs/images/admin/live-view.png" alt="Live View showing connected clients, local pairing, and messaging status" width="280">](docs/images/admin/live-view.png) | [<img src="docs/images/admin/security.png" alt="Security settings showing protection modes and authenticator controls" width="280">](docs/images/admin/security.png) |

## License and use

AutoYou Server is source-available under the
[AutoYou Source-Available Personal-Use License](LICENSE). It is not an
OSI-approved open-source project. Personal use is free for private individual
use as described in the license; commercial, enterprise, organizational,
hosted, managed, and distribution uses require separate licensing by working
directly with [www.autoyou.me](https://www.autoyou.me/).

## Quick start

You need Python 3.10 or newer. Optional features may also require their own
dependencies, such as a local model runtime, Node.js, or Docker.

From the repository root, start the full trial profile. It includes the
voice/video and browser-backed internet dependencies that make the server's
capability surface representative:

Windows:

```powershell
.\run_autoyou.bat --profile full
```

macOS or Linux:

```bash
./run_autoyou.sh --profile full
```

The bootstrapper creates the local environment and installs the selected
profile. Use `--profile recommended` for the leaner profile without the voice
and browser-backed internet dependency bundles, `base` for the smallest
server setup, or `local` for local model helpers only.

## What the full profile enables

| Capability | Included path |
| --- | --- |
| Local AI | Ollama helpers and model-library integration |
| Local Intent Router | Fast on-device classification and local intent routing without cloud round-trips |
| Agent Builder & Chat | Interactive Agent Builder website and server-owned Chat & History workspace |
| Voice and video | Speech recognition, TTS, media replies, and WebRTC call support |
| Browser internet | Browser-backed internet tools through Playwright |
| Messaging | Telegram, Signal, and WhatsApp integrations |
| MCP Bridge | Authenticated same-machine and tokenized MCP route bridge |
| Peer Link & Rendezvous | Direct peer invite, authenticated peer-link negotiation, room bridge, and call listener federation |
| Room Bridge & Calls | Multi-party presence, room call sessions, and shared media notes |
| Private access | Local Admin UI, owner-controlled pairing, and protected configuration |

The source tree contains the optional voice, video, and browser helpers even
when a smaller profile is installed. Profiles control dependency installation
and startup readiness; integrations still remain disabled until you enable and
configure them in the Admin UI.

After startup, open `http://127.0.0.1:8001/`. On a new installation, choose a
unique server password before enabling integrations or pairing devices. For an
unattended first start, set `AUTOYOU_SERVER_PASSWORD` in the environment before
launching the server.

## Docker

The Compose setup binds published ports to host loopback and requires
`AUTOYOU_SERVER_PASSWORD` to be set before it starts. Choose a strong, unique
password. It persists configuration in a named Docker volume and does not
mount your checkout into the container. On Docker Engine versions before 28,
localhost-published ports may still be reachable from the same LAN segment;
see [Docker's port-publishing guidance](https://docs.docker.com/engine/network/port-publishing/).

Windows PowerShell:

```powershell
$env:AUTOYOU_SERVER_PASSWORD = "choose-a-strong-unique-password"
docker compose up --build
```

macOS or Linux:

```bash
export AUTOYOU_SERVER_PASSWORD='choose-a-strong-unique-password'
docker compose up --build
```

The Docker build attempts to pre-cache the upstream Tunnelmole binary, and the
server can download it at runtime if absent. Set both
`AUTOYOU_SKIP_TUNNELMOLE_DOWNLOAD=1` and `AUTOYOU_NO_DOWNLOAD_TUNNELMOLE=1`
before `docker compose up --build` to disable those downloads. Pairing through
Tunnelmole then requires a binary you provide separately.

To configure donation links for a source run, copy
`config/donations.example.json` to `config/donations.json` and edit the local
copy. The local file is ignored; packaged builds include the disabled example.

## Development and verification

Read [CONTRIBUTING.md](CONTRIBUTING.md) before making changes. Tests must use
an isolated runtime root so they never touch a live AutoYou configuration:

```powershell
python -m pytest tests/server/build -q
python scripts/export_public_autoyou_server.py --check
```

The full contributor check list, including test dependencies and the server
legal gate, is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Packaging and official builds

The [server packaging workflows](servers/README.md) support local development
and platform-specific compilation across Windows, macOS, and WSL (Linux).
Official release workflows are standardized on the version declared in [VERSION](VERSION)
(currently `81.0.0`) and are strictly authorization-gated via SignToROSS/OpenSign
at `https://sign.autoyou.me/` with OAuth build gating. Build authorization is
granted when the authorized user OAuth account alone signs it. Do not remove, bypass,
or weaken those controls, and do not present an unofficial build as an official
AutoYou release.

Local source builds may run without official-build credentials, but official release,
signing, notarization, MSIX, and archive workflows must pass
`scripts/check_official_build_authorization.py --required` with a signed
authorization payload matching the artifact profile.

## Documentation

- [Setup and pairing guides](guides/README.md)
- [Local Intent Routing](docs/local-intent-routing.md)
- [Built-in agents](autoyou_agents/README.md)
- [Contributor and AI maintainer guidance](docs/contributors/README.md)
- [Server validation skill](.agents/skills/autoyou-server-validate/SKILL.md)
- [Security guidance](docs/security/encryption.md)
- [Publication boundary](docs/legal/source-publication-manifest.md)
- [Third-party notices](THIRD-PARTY-NOTICES.md)

<details>
<summary>Star history</summary>

[![Star History Chart](https://api.star-history.com/svg?repos=autoyou-ai/AutoYou-Server&type=Date)](https://star-history.com/#autoyou-ai/AutoYou-Server&Date)

If the chart does not render, open the
[AutoYou-Server Star History page](https://star-history.com/#autoyou-ai/AutoYou-Server&Date)
or the [GitHub repository](https://github.com/autoyou-ai/AutoYou-Server).

</details>
