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

## Chat & History: answering a device yourself

Chat & History lists every conversation this server holds, grouped by the
device or channel it is with. Two routes sit beside the read-only ones:

| Route | What it does |
| --- | --- |
| `POST /api/chat` | Puts a turn to AutoYou AI. Used in someone else's conversation it is still a question to the AI; the device is not sent anything. |
| `POST /api/chat/session/reply` | Sends your own words to the device. Body: `user_id`, `message` (up to 4,000 characters). Nothing is asked of the AI. |

A reply is delivered only while that device is connected. It arrives in the
device's current conversation as a message from the server's name, and is kept
in that conversation marked as yours (`author: "self"`, `human: true` in
`GET /api/chat/session`). When the device is not connected the route answers
`delivered: false` with a reason and stores nothing, so history never shows a
reply nobody received. A guest relayed by another device has no connection of
its own and cannot be answered this way. `GET /api/chat/sessions` adds
`live: true` to conversations whose device is connected now.

AutoYou AI still answers that device's own turns as usual. An owner reply does
not pause it.

## Which connections count as this computer

A Local Pair made from this computer is its owner's device, and its call does
not carry this computer's microphone back to itself. Both decisions look at
where the request came from. A public link or reverse proxy also ends on
`127.0.0.1`, so a request that carries a forwarding header
(`X-AutoYou-Tunnel-Client-IP`, `X-Forwarded-For`, `Forwarded`,
`CF-Connecting-IP`) is treated as coming from somewhere else: it is a shared
device, and it is not this computer for audio.

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
