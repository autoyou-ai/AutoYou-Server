# Sessions, Conversations, and Memory

This guide describes the current session model used by the full AutoYou server
without exposing provider credentials or private deployment details.

## What matters

- Pair each phone or desktop app once.
- Keep each approved device or messaging account tied to its owner.
- Reconnect through the same approved path to continue the right conversation.
- Use Secure or Secure Professional mode on shared networks.

## Session layers

AutoYou keeps separate layers for different jobs:

1. Pairing and authentication
2. WebRTC transport and media state
3. AI conversation history and memory

This separation lets a device reconnect without losing the logical conversation
behind it. It also keeps pairing state, transport state, and AI history from
being treated as one interchangeable credential.

## Conversation continuity

Conversation identity is scoped to the owner and transport. Messaging partners
and other transports can continue an existing conversation or start a fresh one
with `/new`, `/newchat`, or `/newconversation`.

Long-running turns are queued per conversation so a busy request does not
silently overwrite another request from the same owner.

## Memory and media

Depending on the selected settings, AutoYou can keep local conversation history,
saved notes, memory-search records, browser access state, and voice/media state.
Voice/video and browser-backed internet helpers are installed by the `full`
profile but remain opt-in through the Admin UI and provider settings.

With a cloud AI provider, prompts and conversation content leave the machine for
that provider's API. Local Ollama keeps the model path on the server.

## Good session hygiene

- Keep the server password and operating-system account protected.
- Review pairing, browser, media, memory, and messaging permissions after a
  device or account changes.
- Disconnect and revoke a session when a device is lost, replaced, or no longer
  trusted.
- Keep the server running only when its services are needed.
