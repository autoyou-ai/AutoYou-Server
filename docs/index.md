---
title: "AutoYou Server Documentation"
description: "Technical reference, architecture guide, API reference, and agent documentation for AutoYou Server."
---

# AutoYou Server Documentation

Welcome to the **AutoYou Server** developer documentation. AutoYou Server is a self-hosted AI server that runs on your own computer. It coordinates AI agents, supervises local model and speech runtimes, provides WebRTC audio, video, and DataChannel transport, manages device pairing, and hosts an admin console.

For the interactive documentation site, host [`docs/`](index.mdx) with Mintlify. The pages are plain Markdown and MDX and can also be read directly in the repository.

## Documentation Sections

### Getting Started & Architecture
- [Quickstart Guide](quickstart.mdx) - Run AutoYou Server locally from source
- [System Architecture](architecture/overview.mdx) - Process model, ports, and local storage
- [WebRTC & DataChannels](architecture/webrtc-and-datachannels.mdx) - Signaling, media pipelines, and chunked DataChannel messages
- [Security & Authentication](architecture/security-and-auth.mdx) - Unlock states, security modes, encrypted configuration, and the authenticator
- [Pairing & Discovery](architecture/pairing-and-discovery.mdx) - Pairing methods, mDNS discovery, and the setup QR code
- [Cloud Pair & Remote Access](architecture/cloud-and-remote.mdx) - The optional AutoYou Cloud helpers, public links, and Peer Link rendezvous

### Admin Console & Developer Guides
- [Admin UI Architecture](admin-ui/architecture.mdx) - The single-page admin console
- [Agent Workbench](admin-ui/agent-workbench-ui.mdx) - Visual agent workspace and testing
- [Custom Agent Tutorial](guides/custom-agent-tutorial.mdx) - Building and deploying custom agents
- [MCP Integration](guides/mcp-integration.mdx) - The MCP bridge and the private MCP adapter
- [Production Deployment](guides/production-deployment.mdx) - Hardening, Docker, systemd, and reverse proxies

### API Reference
- [API Overview](api-reference/overview.mdx) - The admin app, auth app, AI Agent worker, and page service
- [Route Catalog](api-reference/route-catalog.mdx) - Every route, generated from the source
- [WebRTC & Streaming APIs](api-reference/webrtc-and-streaming.mdx) - Session negotiation and media routes
- [AI Agent Worker API](api-reference/ai-agent-worker.mdx) - Chat, sessions, and the LAN one-time-code gate

### Agent Ecosystem & Legal
- [Agent Catalog](agents/system-and-admin.mdx) - Reference for the built-in agents across domains
- [Release Model](legal/release-model.md) - Release cycles and packaging policy
- [Messaging Partners Policy](legal/messaging-partner-policy.md) - Telegram, Signal, and WhatsApp usage policies
- [Open-Source Commitment](legal/open-source-commitment.md) - Contributor pool and open-source roadmap
- [Security Contact](legal/security-contact.md) - Vulnerability disclosure and security contacts
- [Third-Party Attributions](legal/third-party-attributions.md) - Open source licenses and notices
