# AutoYou Agents

AutoYou agents are focused helpers that can answer questions, manage notes, search with permission, work with local files, guide setup, and open simple browser tools.

The source tree includes the optional voice, video, and browser-backed internet
helpers. Use the `full` profile for a representative trial; smaller profiles
can leave those dependency bundles out while the source remains present.

## Included Helpers

| Helper | What It Helps With |
| --- | --- |
| Admin | setup and safe server controls |
| Coding | small implementation and verification tasks |
| Files | local file guidance |
| Internet | web lookup when web access is enabled |
| Memory | remembered context and past work |
| Notes | notes, saved text, and attachments |
| Page | saved links and personal feed items |
| Persona | personal assistant style and preferences |
| Audio | local music and voice playback controls |
| Donation | simple support and donation guidance |
| Earnings | support activity and future payout-readiness guidance |
| Fine-Tuning | preparing a personal training dataset |
| Hosting | publishing a local website or helper screen |
| Media Generation | local image or video generation setup |
| Model Picker | choosing an AI provider or model |
| Skills | installing and managing helper skills |
| Tasks | reminders and scheduled work |
| Voice Training | preparing a personal voice dataset |
| Website | creating simple browser screens for helpers |

Some experimental or partner-specific helpers are private or disabled by default until they are ready for wider use.
Calibrated Claude and Codex Desktop asset packs are excluded from the public
source distribution; their desktop bridge agents need compatible local asset
packs before those controls can be used.

## Full-server integration

The full server starts and supervises the AI worker process and managed
frontend services from `core_server/services.py`. Worker HTTP endpoints live in
`routers/ai_agent.py`; full-server admin routes for agent management live in
`routers/agents.py`. `server.py` supplies the shared runtime namespace but does
not own those routes or process lifecycles. Packaged full-server builds compile
`autoyou_agents/` alongside `core_server/` and `routers/`.
An optional sibling `autoyou_agents/` checkout supplies source-only agents such
as Trading; its ignored `private/` folder can hold local integrations. The
packaged server includes only the built-in agents listed by the install policy.

## Website Security Contract

Agent website authentication is a two-part boundary:

- The backend must reject every protected read and write with `401` until the
  scoped session is authenticated. The shared website factories verify an
  assigned per-agent TOTP profile first, then fall back to the shared pairing
  TOTP when no profile is assigned.
- The frontend must fail closed before its page-specific JavaScript runs. A
  login dialog or dark backdrop is not a lock: protected content must be
  hidden and `inert` until authentication succeeds. The shared website
  factories inject this gate for factory-built pages; Persona also keeps its
  protected shell hidden and inert locally.

The observed regression was a visible, usable background behind an OTP modal.
New websites should use `create_agent_website_app`, `create_agent_chat_app`, or
the scheduler factory and must not place private data in unauthenticated HTML
or bootstrap payloads.
