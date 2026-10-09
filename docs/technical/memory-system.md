---
title: Memory
description: Durable personal memory, conversation history, and efficient local routing.
---

# Memory

AutoYou keeps deliberate personal facts in the existing Persona journal and
conversation history in SQLite. Both persist across restarts when their data
directory is retained. A model saying "saved" is not evidence of a storage write.

## Storage choice

| Data | Existing store | Use |
| --- | --- | --- |
| Personal facts and preferences the user asks to retain | `persona_agent/persona.md` under the server's mutable data directory | Canonical personal profile, shared by chat tools and the Persona website. |
| Conversation events and explicit conversation memories | SQLite session storage and its `memory_search` index | Searchable history, normally scoped to the current conversation. |
| Optional richer retrieval | Cognee, with the SQLite fallback retained | Opt-in backend; unnecessary for basic personal recall. |

Keep this split. Do not create another Markdown transcript, vector database, or
profile copy per client. Session persistence alone does not give new chats access
to a personal profile: the harness must retrieve it. Personal memory here is the
existing server-owner journal, not a separate profile for every remote contact.

`read_persona` and `append_persona` operate on the same file as the website.
An explicit simple personal save uses `append_persona` through ADK; its actual
storage result supplies the confirmation. "Save it" can use the preceding user
fact, never an assistant's invented fact. Ambiguous requests stay with the
full-context model. Clear personal reads reach Persona before the session-memory
shortcut, then the selected model answers from the tool result.

Writes reuse AutoYou's flushed, atomic file replacement, including independent-key
encrypted files. A failed replacement preserves the previous file and reports an
error. This is ordinary restart/process-crash durability, not a backup policy or
a guarantee against concurrent whole-profile edits. Back up the mutable data
directory and preserve access to its encryption keys.

Plaintext is the independent Persona store's default. `AUTOYOU_PERSONA_ENCRYPT=1`
enables its separate OS-keystore key; Secure Professional Maximus uses the shared
protected-storage layer. Website authenticator access and disk encryption are
different controls. The existing private authenticator is unchanged.

## Efficient harness

1. Handle clear tool requests using existing deterministic routes.
2. For other short requests, run the cached in-process MiniLM classifier
   before generation. Its Persona route is included in the shared routing data.
3. Dispatch through advertised tools; similarity never authorizes a write.
   "Save it", negation, long requests, and uncertain classifications defer.
4. Use the model for interpretation and the final read answer. Simple saves need
   neither a routing generation nor a generated success confirmation.

Ministral 3B/8B, Gemma 4 E2B/E4B, and Apple Intelligence use the compact root
dispatcher. Gemma retains its expanded specialist callbacks. The model does not
receive a separate top-level schema for every installed specialist. Existing
operator overrides remain available.

The classifier is independent of the response provider. Apple Intelligence runs
through the native Foundation Models helper on a supported Mac; it cannot run
inside a Windows server merely because a Mac is connected. Gemma and Ministral
use their configured runtime. Apple's on-device model has a limited context, so
do not inject the full session database or an ever-growing journal into every
request. Retrieve on demand and retain explicit context-limit errors.
[Apple context guidance](https://developer.apple.com/documentation/foundationmodels/managing-the-context-window)
and [Gemma function calling](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4)
describe the provider constraints.

## Compiled servers and clients

The change lives in shared Python runtime modules, which the server packaging
plan includes, and in `assets/intent_router/routes.json`. Existing Windows/macOS
server and v2, Android, and iOS build preparation copies the classifier assets.
No new dependency, OS-specific storage path, or client-side profile migration is
required. A compiled server must be rebuilt from the updated source to receive
the harness fix; rebuilding only its connected client cannot update that server.

When macOS, Windows v2, Android, and iOS connect to the same server, that server
owns tool execution and the personal journal. Native Peer Link assistants use
the classifier as a topic hint with their own existing memory. They do not gain
server Persona tools or silently sync the owner's journal.

The native Apple model helper retains completed ADK tool results in its
transcript. Previously it relied on the model requesting the same tool again to
replay the result, which could leave a deterministic Persona read invisible to
the model. It still replays matching calls without executing a second side effect.

## OpenClaw bridge memory

Explicit OpenClaw requests and follow-ups pinned to that bridge bypass AutoYou's
Persona and session-memory shortcuts. The bridge forwards before invoking the
local model, using `OPENCLAW_AGENT_PORT`, `OPENCLAW_AGENT_TOKEN`, and
`OPENCLAW_AGENT_MODEL`. It reuses the shared gateway adapter, including HTTP
endpoint fallback, and sends a stable key from the canonical conversation state.
Temporary ADK child sessions therefore do not reset OpenClaw on every turn.

`openclaw/default` selects the gateway's configured default agent;
`openclaw:main` explicitly selects its main agent and workspace. AutoYou keeps a
separate conversation in that instance rather than merging the dashboard's main
chat. OpenClaw owns its workspace memory, such as `USER.md`; it is not copied into
AutoYou's Persona journal. These semantics follow the
[OpenClaw HTTP contract](https://docs.openclaw.ai/gateway/openai-http-api).

## Tests

```sh
AUTOYOU_TEST_INTENT_MODEL=1 .venv/bin/python -m pytest tests/server/runtime/test_intent_router.py -q
.venv/bin/python -m pytest tests/agents/public/test_root_agent_memory_tool.py tests/agents/public/test_persona_agent.py tests/agents/public/test_persona_website.py -q
AUTOYOU_TEST_PERSONA_MODELS=1 AUTOYOU_APPLE_MODEL_HELPER=/path/to/AutoYou.app/Contents/Helpers/AutoYouModel .venv/bin/python -m pytest tests/agents/public/test_persona_agent.py -k live_persona -q
```

The live model test requires the named local models to be installed already. Pytest redirects all profile writes and keystore access through `AUTOYOU_TEST_ROOT`.
