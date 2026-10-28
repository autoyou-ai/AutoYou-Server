# AutoYou Agents

AutoYou agents are focused helpers that can answer questions, manage notes, search with permission, work with local files, guide setup, and open simple browser tools.

The source tree includes the optional voice, video, and browser-backed internet
helpers. Use the `full` profile for a representative trial; smaller profiles
can leave those dependency bundles out while the source remains present.

## Included Helpers

| Helper | Package | What It Helps With |
| --- | --- | --- |
| Admin | `admin_agent` | setup and safe server controls |
| Ads Watching | `ads_watching_agent` | opt-in rewarded ad viewing for credits |
| Agent Builder | `agent_builder_agent` | scaffolding, creating, and registering new agents and tools |
| Antigravity | `antigravity_agent` | Antigravity desktop app and `agy` CLI integration |
| Assistant | `assistant_agent` | desktop window management and quick page reader |
| Audio | `audio_agent` | local music player, audio library, and playback controls |
| Backup | `backup_agent` | resumable local file backup and archive management |
| Coding | `coding_agent` | repo-aware implementation, bug fixes, refactoring, and test verification |
| Data Collector | `data_collector_agent` | consented local conversation and message-history collection |
| Donation | `donation_agent` | supporter routes, Buy Me a Coffee, and donation guidance |
| Earnings | `earnings_agent` | credits, rewarded-ad status, and payout planning |
| Education | `education_agent` | learning sessions, study recordings, and lesson transcripts |
| Files | `files_agent` | authenticated local filesystem inspection and operations |
| Fine-Tuning | `fine_tuning_agent` | personal model training datasets and model preparation |
| Game Studio | `game_agent` | local browser-based games and interactive entertainment |
| Hosting | `hosting_agent` | publishing local websites or helper screens to persistent or `/pair` links |
| Internet | `internet_agent` | verified live web search, browsing, and scraping |
| Location | `location_agent` | location timeline and geodata management |
| macOS Security | `mac_security_agent` | macOS security status and hardening controls |
| Media Generation | `media_generation_agent` | local image and video generation setup |
| Memory | `memory_agent` | dedicated memory recall passes and remembered context |
| Model Picker | `model_picker_agent` | hardware-fit recommendations and AI provider/model selection |
| Notes | `notes_agent` | notes, to-dos, organization, search, and attachments |
| Notify | `notify_agent` | timed notifications, reminders, and direct client delivery |
| OpenClaw | `openclaw_agent` | smart home, local browser, and OpenClaw gateway integrations |
| Page | `page_agent` | saved links, media cards, and personal feed items |
| Persona | `persona_agent` | personal profile facts, assistant style, and persona journal |
| Prompt Builder | `build_prompt_agent` | prompt assembly from Saved Messages or Prompt Builder UI |
| Prompts | `prompts_agent` | personal prompt library and reusable prompt templates |
| Remote Desktop | `remote_desktop_agent` | screen sharing, window casting, and mouse/keyboard control |
| Skills | `skills_agent` | creating, viewing, editing, and executing reusable AutoYou skills |
| Tasks | `tasks_agent` | scheduled AI jobs, cron automations, and recurring tasks |
| TV Agent | `tv_agent` | local media streaming, video library indexer, playback manager, and transcoding |
| Voice Training | `voice_training_agent` | custom TTS voice datasets and voice training |
| Website | `website_agent` | scaffolding and registering agent frontend websites and split architectures |
| Windows Security | `win_security_agent` | Windows firewall, protection, and security status |

Some experimental or partner-specific helpers are private or disabled by default until they are ready for wider use.
Calibrated Claude and Codex Desktop asset packs are excluded from the public
source distribution; their desktop bridge agents need compatible local asset
packs before those controls can be used.

## Quantized & Small Local Model Architecture

Small or quantized Ollama models (such as `ministral-3:8b`, `qwen38-turbo`, `gemma4:e2b/e4b`, and models under 20B parameters) are prone to context exhaustion, tool hallucinations, and schema-echoing degeneration when exposed to extensive tool catalogs. AutoYou uses a multi-tier routing pipeline to keep small models accurate:

1. **Dynamic Prompt Filtering (`_build_effective_agent_instruction`)**:
   - `prompt.py` defines factory text templates (`SUB_AGENTS_SECTION` and `ROUTING_RULES_SECTION`).
   - At startup, `_filter_prompt_section_by_installed_agents` strips every line or bullet referencing an uninstalled agent.
   - If an operator authors a custom `AGENT_INSTRUCTION`, `_filter_unavailable_agents_from_instruction` automatically purges references to unavailable agents to prevent phantom routing targets.

2. **Compact Two-Stage Enum Routing (`route_to_specialist`)**:
   - Enabled automatically for small/quantized models via `model_uses_compact_root_tool_routing`.
   - **Catalog Stripping**: `_strip_duplicate_agent_catalog` removes the repetitive bulleted agent catalog from the prompt (~4.6k characters saved), preventing the prompt bloat that causes small models to echo schemas instead of invoking tools.
   - **Single Dispatcher Tool**: Rather than advertising dozens of distinct JSON function schemas, the model is given a single tool: `route_to_specialist`.
   - **JSON Schema Enum Constraint**: The `agent` argument is strictly constrained to a JSON Schema `enum` containing only installed specialists.
   - **Forgiving Argument Resolution**: `_resolve_specialist_tool` normalizes casing and forgives small-model tendencies to drop the `autoyou_` prefix or `_agent` suffix. If an unknown agent is requested, it safely fails closed with an informative error listing valid choices.

3. **Pre-Model Fast Paths & Offline Intent Classifier**:
   - Deterministic pattern-matchers intercept common commands (notes, media generation, CLI, files, etc.).
   - An offline 23 MB `all-MiniLM-L6-v2` embedding classifier (`classify_intent`) evaluates user queries against an `allowed` set consisting solely of enabled/installed agents. Confident matches dispatch directly to the target specialist tool, bypassing the root quantized model entirely.
   - If a requested agent is uninstalled or installed-but-broken, the system immediately returns an explicit refusal or explanation (`_unavailable_agent_response`), preventing hallucinated confirmations.

4. **Adapter Safeguards (`litellm_ollama_adapter.py`)**:
   - Detects and repairs responses that degenerate into JSON schema echos.
   - Catches truncated tool calls when reasoning models exhaust `num_predict`, automatically retrying with expanded prediction budgets.
   - Handles Ollama KV-cache pre-allocation memory failures with automatic context size recovery.

## Full-server integration

The full server starts and supervises the AI worker process and managed
frontend services from `core_server/services.py`. Worker HTTP endpoints live in
`routers/ai_agent.py`; full-server admin routes for agent management live in
`routers/agents.py`. `server.py` supplies the shared runtime namespace but does
not own those routes or process lifecycles. Packaged full-server builds compile
`autoyou_agents/` alongside `core_server/` and `routers/`.
The packaged server includes only the built-in agents listed by the install
policy, plus any a build names explicitly.

## Your Own Agents

Copy an agent package into this folder and the server finds it. No list to
edit:

- `autoyou_agents/<name>_agent/` for an agent you want in this repository.
- `autoyou_agents/private/<name>_agent/` for one you keep to yourself. That
  folder is git-ignored and can also be a link to wherever you keep your agents.

A package needs `__init__.py`, `prompt.py` with `AGENT_NAME`,
`AGENT_DESCRIPTION` and `AGENT_INSTRUCTION`, and `agent.py` with
`create_<name>_agent(model_config)`. Use a name no built-in agent uses. The
Agent Builder scaffolds this for you. An agent you copy in starts uninstalled;
install it in the admin UI. Optional, in `prompt.py`:

- `ROUTING_LABEL`: its name in the main agent's list of agents.
- `ROUTING_RULE`: when the main agent should call it. Without it,
  `AGENT_DESCRIPTION` is used.
- `ROUTE_ALIASES`: names that send a request straight to it, as in
  "use <alias> to ...".

A website goes in `website/` with a `manifest.json`. Set
`"expose_by_default": false` to keep it off until the owner turns it on, and
`control_label` and `control_help` for its admin toggle.

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
