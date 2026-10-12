---
title: Architecture
description: Public overview of AutoYou architecture.
---

# Architecture

AutoYou runs on your own computer and lets your approved clients connect through local network access, Cloud Pair, or a public link you explicitly enable.

The full local server uses `server.py` as a composition root. Domain HTTP routes
live under `routers/`; service/process lifecycle and the WebRTC engine live
under `core_server/`. The lightweight `autoyou-lite` package is a separate
runtime and does not contain the full agent and partner-service stack.

Public docs intentionally avoid deeper internal component details. For setup,
start with [Getting Started](../../guides/installation-steps.md). For privacy and
security posture, see [Security Modes](../security/security-modes.md) and
[Encryption](../security/encryption.md). Maintainers should follow the module
layout described above rather than treating `server.py` as a monolith.
