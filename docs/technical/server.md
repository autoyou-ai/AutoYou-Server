---
title: Server
description: Public overview of the AutoYou server.
---

# Server

The AutoYou server is the app you run on your own computer. It hosts the admin shell, AutoYou AI, agent websites, messaging connectors, and client access.

Keep the admin shell local. Use Cloud Pair or the public link feature when you need remote access from your own devices.

## Local browser-proxy ports

- **8067** is the full server's agent-website/browser proxy. It serves shared
  agent routes such as `/agent-websites` and `/agent/{name}/`.
- **AutoYou Connect**, Android, and iOS use **8067** for their local browser
  proxy by default so server-advertised website shortcuts resolve consistently
  across clients.
- **8076** is a legacy/custom desktop fallback only. Existing desktop settings
  that still contain the old default are migrated to 8067; manually chosen
  custom ports are preserved.

The macOS Security agent remains available through the server's normal
`/agent/mac_security_agent/` route. Its internal loopback backend uses **8101**
so it cannot prevent AutoYou Connect from opening its browser proxy on 8067.

## Agent website search

The full server exposes [HTTP QUERY (RFC 10008)](https://www.rfc-editor.org/rfc/rfc10008.html)
at `QUERY /api/agent-websites`. It searches registered agent names, titles, and
descriptions while keeping query details in a JSON request body:

```sh
curl --request QUERY http://127.0.0.1:8067/api/agent-websites \
  --header 'Content-Type: application/json' \
  --data '{"query":"calendar scheduling","limit":24}'
```

The endpoint advertises `Accept-Query: "application/json"`. AutoYou Lite does
not host the directory, but its remote HTTP transport forwards QUERY requests
to endpoints that do.
