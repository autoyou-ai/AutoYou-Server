---
title: Data Privacy & Ownership
description: Where AutoYou keeps your data, what can leave your computer, and which services are involved
---

# Data Privacy & Ownership

AutoYou Server runs on your computer, and its data lives there. This page describes where data is stored and which optional services can receive some of it. It describes the software. The legal terms for AutoYou's own services, your rights, and how to contact the maintainers are in the [privacy policy](https://www.autoyou.me/privacy/).

## What stays on your computer

By default the server stores these on the computer it runs on:

| Data | Where it lives |
| --- | --- |
| Conversations and message history | `sessions.db` (SQLite) |
| Notes, memory, and agent outputs | The agents' data folders and databases |
| Attachments and voice recordings | `uploads/` and the agents' folders |
| Server configuration and credentials | `config.keystore.enc`, or `config.encrypted` as a fallback, which are encrypted |
| Agent security profiles | `agent_security.db` |
| Page feed items | `page_feed.db` |
| Optional device location samples | The server's Location Timeline, only after you enable sharing, grant operating-system permission, and enable recording on your server |

Apart from the encrypted configuration, these files are protected by your operating system's permissions. In **Secure Professional Maximus** mode the server also encrypts saved sessions, agent data, websites, notes, and settings on disk. See [Security Modes](security-modes.md).

The server source contains no analytics or crash-reporting SDK, and the server does not configure an OpenTelemetry exporter. The Google ADK dependency bundles OpenTelemetry libraries. Check the [dependency review](../../requirements/README.md) for what a given profile installs.

## What can leave your computer

Some features involve other parties. Each one is optional or has a setting, and each has its own data handling:

| Feature | What is sent | To whom |
| --- | --- | --- |
| Cloud model providers (Gemini, OpenAI, Anthropic, and others) | The prompts and context you send to that provider | The provider you chose, under its terms |
| Cloud Pair | Account email, device ID, short-lived pairing details, and connection metadata | AutoYou Cloud |
| Relay fallback | Encrypted packets and connection metadata | AutoYou Cloud |
| Public STUN servers | The public address and port a WebRTC connection appears from | Google's public STUN servers by default |
| Telegram, Signal, and WhatsApp bridges | Messages you route through them | Those services, under their terms |
| Public tunnel for Website Apps | Traffic to the public URL | The tunnel host |
| Software update checks | A request to the update feed, authenticated with the account link. On by default, but inactive until the server is linked to an account. Turn off with `software_update.enabled` or `AUTOYOU_SOFTWARE_UPDATES_ENABLED=0`. | AutoYou Cloud |
| Model and tool downloads | Standard download requests for models, packages, and native tools | The hosts that serve them |

The [connection-helper documentation](../technical/webrtc.md) states that AutoYou Cloud does not decrypt message content, voice audio, files, or browsing content. Connection services and peers can still see network addresses and timing, because WebRTC does not hide your public IP from the other side.

### Public STUN

By default the server advertises Google's public STUN servers so WebRTC can discover its public address, including for Local Pair. To keep that lookup off the internet, set `AUTOYOU_DISABLE_PUBLIC_STUN=1` before starting the server, or point the server at your own STUN and TURN host with `AUTOYOU_LOCAL_STUNTURN_HOST` (plus `AUTOYOU_LOCAL_TURN_USERNAME` and `AUTOYOU_LOCAL_TURN_PASSWORD` for TURN). Without any STUN or TURN server, connections across NAT may fail.

## Cloud model providers

When you choose a cloud model, your prompt and the context sent with it go to that provider, and the answer comes back. The provider's policy applies to that request. Your other local data stays local unless a tool you use sends it as part of a prompt. Review each provider's terms before using it:

- [Google Privacy Policy](https://policies.google.com/privacy)
- [OpenAI Privacy Policy](https://openai.com/privacy)
- [Anthropic Privacy Policy](https://www.anthropic.com/privacy)

## Your controls

- **Choose the integrations.** Cloud providers, Cloud Pair, messaging bridges, and public tunnels are opt-in.
- **Keep it local.** Use Local Pair on your own network and a local model through Ollama to avoid cloud services for chat. Set `AUTOYOU_DISABLE_PUBLIC_STUN=1` to avoid the STUN lookup.
- **Own your files.** Your data is ordinary files and SQLite databases in the server's data folders. You can copy them for a backup, or delete them. The Backup agent offers resumable file transfers at `/agent/backup_agent/`.
- **Reset.** On the computer running the server, the sign-in page can erase AutoYou's local data and shut the server down after you type `RESET`.

## If your computer is compromised

A person or program with access to your computer or to a paired device has that endpoint's access. AutoYou cannot protect data from malware that runs as you. Keep your operating system updated, use full-disk encryption on laptops that travel, use a strong unique server password, and keep backups.

## Questions

For privacy requests or questions about AutoYou's services, see the [privacy policy](https://www.autoyou.me/privacy/). To report a security issue, follow [SECURITY.md](https://github.com/autoyou-ai/AutoYou-Server/blob/main/SECURITY.md).
