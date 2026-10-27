# Browser desktop for the Browser and Assistant agents

The Browser agent and the Assistant agent drive a real Chromium browser on a
small Linux desktop (Xvfb with noVNC, so you can watch or take over) through the
auto-browser controller's REST API. Both run as Docker containers next to
AutoYou. They are third-party MIT-licensed images
([LvcidPsyche/auto-browser](https://github.com/LvcidPsyche/auto-browser), pinned
to release `1.9.1`) and are not part of AutoYou's builds, in the same way as the
Signal REST API container.

```bash
docker compose -f servers/browser-desktop/docker-compose.yml up -d
```

- Controller API: `http://127.0.0.1:8000`. This is AutoYou's default
  `AUTO_BROWSER_BASE_URL`, so there is nothing to configure on the same computer.
- Watch or take over the desktop:
  `http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale`.
- Every port is published on loopback only. Before publishing the API anywhere
  else, set `API_BEARER_TOKEN` here and `AUTO_BROWSER_BEARER_TOKEN` for AutoYou,
  plus `VNC_PASSWORD` for the desktop.
- `ALLOWED_HOSTS` lists the sites the browser may open (comma-separated host
  names). It starts as `example.com,www.example.com`. Add the sites you want the
  agents to use, then run the `up -d` command again.
- Install the **Browser agent** under Agents on the Admin Page. The Assistant
  agent is on by default and only uses the browser through the Browser agent.

Hands-free check: with Assistant mode on, say "Hey AutoYou, open example.com and
read the title". The reply in chat is `The page title of example.com is
"Example Domain".` If the containers are stopped, the reply tells you how to
start them.

## Windows (Docker Desktop or WSL)

Docker Desktop with the WSL 2 engine runs the images as they are (amd64). From
a WSL shell inside the repository, the same `docker compose` command works, and
the published `127.0.0.1` ports are reachable from Windows. The live check for
this setup ran on Windows 11 with Docker Desktop 29.

## macOS

The desktop is Linux, so macOS runs it in a container engine: Docker Desktop,
OrbStack or Colima. On Intel Macs, use the command above as it is.

The published images are amd64 only. On Apple Silicon they run under Rosetta
emulation, which works but is slower. For native arm64 images, build release
`1.9.1` locally. The base image (`python:3.11-slim`) and Playwright's Chromium
both support arm64.

```bash
git clone --branch v1.9.1 --depth 1 https://github.com/LvcidPsyche/auto-browser /tmp/auto-browser
docker build -t autoyou/auto-browser-browser-node:1.9.1 /tmp/auto-browser/browser-node
docker build -t autoyou/auto-browser-controller:1.9.1 /tmp/auto-browser/controller
AUTO_BROWSER_NODE_IMAGE=autoyou/auto-browser-browser-node:1.9.1 \
AUTO_BROWSER_CONTROLLER_IMAGE=autoyou/auto-browser-controller:1.9.1 \
  docker compose -f servers/browser-desktop/docker-compose.yml up -d
```

AutoYou itself runs natively on macOS and reaches the controller at
`http://127.0.0.1:8000`.

## AutoYou in Docker

When AutoYou runs from `docker-compose.yml`, the controller is not on
AutoYou's loopback. Set `AUTO_BROWSER_BASE_URL=http://host.docker.internal:8000`
for AutoYou. On the controller, set an `API_BEARER_TOKEN` and add
`host.docker.internal` to `CONTROLLER_ALLOWED_HOSTS`. Then give AutoYou the
same token as `AUTO_BROWSER_BEARER_TOKEN`.

## Licences

`auto-browser` (controller, browser node and the `auto-browser-client` Python
package) is MIT-licensed. It is listed in `THIRD-PARTY-NOTICES.md`. The browser
node contains Chromium and other Debian packages under their own licences. They
stay inside the separately pulled image, and AutoYou neither redistributes nor
links them. Anthropic's computer-use demo image was considered and not used:
its agent loop needs an Anthropic API key, and the Browser agent already speaks
the auto-browser API.
