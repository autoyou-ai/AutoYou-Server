---
title: "AutoYou Server Documentation"
description: "Comprehensive technical reference, architecture guide, API specifications, and Agent ecosystem documentation for AutoYou Server."
---

# AutoYou Server Documentation

Welcome to the **AutoYou Server** developer documentation. AutoYou Server is an open, self-hosted AI operating environment that runs locally on your computer. It coordinates multi-modal AI agents, supervises local LLM and speech inference, exposes rich WebRTC audio/video/datachannel transports, manages secure client pairing, and hosts an integrated administrative console.

For the full interactive Mintlify documentation site, open or host [`docs/`](index.mdx) with Mintlify or see the online docs.

## Documentation Sections

### Getting Started & Architecture
- [Quickstart Guide](quickstart.mdx) - Run AutoYou Server locally in minutes
- [System Architecture](architecture/overview.mdx) - Process model, port bindings, and internal persistence layers
- [WebRTC & DataChannels](architecture/webrtc-and-datachannels.mdx) - Signaling, Opus/video media pipelines, and chunked SCTP DataChannels
- [Security & Authentication](architecture/security-and-auth.mdx) - Argon2id keystores, 2FA TOTP, and libsodium cryptography
- [Pairing & Discovery](architecture/pairing-and-discovery.mdx) - Local mDNS, QR codes, and messaging bridges

### Admin Console & Developer Guides
- [Admin UI Architecture](admin-ui/architecture.mdx) - Single-page admin console, reactive controls, and state lifecycles
- [Agent Workbench](admin-ui/agent-workbench-ui.mdx) - Visual agent workspace and testing
- [Custom Agent Tutorial](guides/custom-agent-tutorial.mdx) - Building and deploying custom agents
- [MCP Integration](guides/mcp-integration.mdx) - Connecting Claude Desktop, Cursor, and IDE tools via MCP
- [Production Deployment](guides/production-deployment.mdx) - Hardening, reverse proxies, and systemd/Docker setups

### API Reference
- [API Overview](api-reference/overview.mdx) - Over 150 administrative, pairing, streaming, and messaging endpoints
- [WebRTC & Streaming APIs](api-reference/webrtc-and-streaming.mdx) - Session negotiation, audio tracks, and virtual video
- [AI Agent Worker API](api-reference/ai-agent-worker.mdx) - Multi-modal worker dispatch and execution
- [MCP Protocol APIs](api-reference/mcp-server.mdx) - Model Context Protocol tool execution

### Agent Ecosystem & Legal
- [Agent Catalog](agents/system-and-admin.mdx) - Reference for built-in agents across domains
- [Release Model](legal/release-model.md) - Release cycles and packaging policy
- [Messaging Partners Policy](legal/messaging-partner-policy.md) - Telegram, Signal, and WhatsApp usage policies
- [Open-Source Commitment](legal/open-source-commitment.md) - Contributor pool and open-source roadmap
- [Security Contact](legal/security-contact.md) - Vulnerability disclosure and security contacts
- [Third-Party Attributions](legal/third-party-attributions.md) - Open source licenses and notices
