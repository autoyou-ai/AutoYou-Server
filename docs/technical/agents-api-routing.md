# Agents API routing for AutoYou chat

AutoYou can route chat turns containing `@` through an OpenAI Agents API
session. The session is a short-lived router that calls the same AutoYou chat
worker used by the Admin Chat and full-server MCP routes. The response shown to
the user is the response produced by that AutoYou server's configured AI model.

## What is sent where

- The text message is sent to the OpenAI Agents API so its agent can select the
  AutoYou routing function.
- Attachment bytes stay in the AutoYou server process. The function handler
  forwards the original `ChatRequest.context` to the configured AutoYou model.
- AutoYou's final answer and generated media are returned to the client without
  being added to the Agents API tool result.
- The Agents API session uses `environment.type: none` and is deleted after the
  turn completes. AutoYou keeps the conversation history in its existing chat
  store under the user's AutoYou session ID.

The Admin Chat already packages images, audio notes, video, and files in the
`context` field. The authenticated `/api/v1/mcp/chat` route accepts the same
context shape, with a maximum of 32 context groups and 48 MiB of encoded
attachment data. Media interpretation depends on the active AutoYou provider
and its configured capabilities.

## Configure one AutoYou instance

For a local source checkout, set `AUTOYOU_AGENTS_API_KEY` in the ignored
`AutoYou-Server/.env` file. Packaged or service-managed instances can set it in
the environment used to start that instance. `OPENAI_API_KEY` is also accepted
as a fallback. Use an OpenAI API key with `api.agents.read`,
`api.agents.write`, and `api.responses.write` permissions. `OPENAI_API_KEY` is
used only as a credential for the Agents API router here. The model that writes
the answer remains the provider selected in this instance's AI & Models
settings.

`AUTOYOU_AGENTS_API_MODEL` selects the routing model and defaults to
`gpt-6-astra`. Each AutoYou server instance reads its own environment and keeps
its existing MCP API token, server identity, chat history, and AI provider
configuration.

After configuration, send a message containing `@` in Admin Chat or through
the authenticated full-server MCP chat route. Messages without `@` continue
through the normal AutoYou chat path.

## Voice calls

Voice notes use the existing AutoYou attachment and speech-recognition path.
Live calls continue to use AutoYou's existing WebRTC and voice pipeline. The
Agents API router does not yet handle a live call session; a later voice
integration can send call transcripts through the same AutoYou routing
function.
