# Data Collector Agent

The standalone FastAPI dashboard is the primary public interface:

```powershell
$env:AUTOYOU_LOCAL_TOTP_SECRET = "your-base32-totp-secret" # standalone only
python -m autoyou_agents.data_collector_agent.website.backend.app
```

When installed in AutoYou, it uses the server's shared or assigned agent TOTP; the standalone launcher binds to `127.0.0.1:18067`, keeps its state in AutoYou’s agent data root, and
can create a one-time Fine Tuning handoff. The original dependency-free v2 CLI
is preserved below for its full-history, legacy-archive, scheduling, and
experimental-source workflow:

```powershell
python -m autoyou_agents.data_collector_agent.legacy_workflow init
python -m autoyou_agents.data_collector_agent.legacy_workflow collect --dry-run
python -m autoyou_agents.data_collector_agent.legacy_workflow collect
```

## Runtime boundary

| Component | Owns | Does not own |
| --- | --- | --- |
| Admin server | agent install state, managed-backend lifecycle, server TOTP and sessions | conversation collection or model inference |
| Page service | the `/agent/data_collector_agent/` proxy route | OTP secrets or the collector process |
| Data Collector backend | loopback-only UI and collection/export APIs; the collection worker and private exports | chat routing or global authentication policy |
| AI agent runtime | agent chat tools | website routing, website startup, or OTP verification |

When installed, the Admin server starts the collector as a managed loopback
backend so it can use its already-paired local messaging services without
copying their credentials. An AI-runtime restart runs the managed-frontend sync
and starts the installed collector; the Page service only proxies the
registered loopback port and does not need a restart. The main server only
needs a restart when its own source or packaged build has changed.
Server-managed OTP stays independent of both the Page service and the AI
runtime.

## Packaged runtime and safe stop

Windows, macOS, and WSL packages compile the collector's Python modules to
native `.pyd`/`.so` files. The frontend, manifest, and WhatsApp Node history
worker remain read-only package assets and are covered by the runtime integrity
manifest. Each platform build verifies the collector website backend, its
agent-chat facade, and those assets before it publishes an artifact.

The direct collector backend remains loopback-only on `18067`; the authenticated
Page Service is the only browser entry point. Docker Compose publishes the Page
Service proxy on loopback `8067`, not the collector port.

`POST /api/run/cancel` requests a stop for a website or agent-chat collection.
The agent-chat tool `cancel_data_collection` writes the same harmless per-agent
control signal. One persisted local run owner prevents either process from
overwriting the other's halt request, so neither needs to restart the Admin
server, Page Service, or AI runtime. Collection checks that signal between
source files, sessions, messages, worktrees, and view records;
the WhatsApp subprocess is terminated and the paused bridge is restarted.

## Data protection boundary

When Secure Professional Maximus is enabled, the collector attaches to the
server-provided protected-storage boundary before it reads or writes its own
state. Collector-owned session records, derived views, training exports,
worktree evidence, and opt-in raw copies are encrypted at rest. Original
Codex, Claude, and ChatGPT source files stay untouched; importing them never
rewrites a source file with the collector's key.

The one-time loopback handoff is generated only for the transfer. Fine Tuning
seals its persisted training copy separately, so a collector export never
becomes an unencrypted long-lived Fine Tuning dataset.

The standalone launcher is intentionally separate. It can collect local AI
conversation files and make exports, but runtime-only WhatsApp and Telegram
connections are available only when the collector is managed beside the
already authenticated server services.

## Messaging collection boundary

Data Collector owns message-history collection. It can collect the full locally
available WhatsApp history with a finite time budget (default four hours,
configurable up to 24 hours), not a conversation or message-count ceiling.
When the live WhatsApp bridge owns the same LocalAuth profile, the collector
pauses and restarts that bridge only. It never restarts the Admin server, page
service, or AI agent runtime.

Telegram collection is intentionally narrower: it reads only the operator's
Saved Messages after both owner scope and **Admin Settings > Messaging**
training consent are present. It never enumerates Telegram dialogs. A source
that is not paired, consented, or locally installed is reported unavailable;
it is not represented as collectable.

Fine Tuning consumes private Data Collector exports from its AutoYou runtime
directory or through a one-time loopback handoff. It owns dataset preparation
and training, not message extraction.

---

# DataCollector v2 (preserved workflow)

DataCollector v2 archives local AI-harness session logs and materializes a
chronological training view. It is dependency-free and runs with the system
Python on Windows and macOS.

Codex, Claude, and saved ChatGPT browser exports are supported. Antigravity
and Trae are opt-in experimental sources. VS Code and Cursor are registered as
`tbd`, so the collector will not pretend their workspace stores are
conversational sessions.

## Start

```powershell
# Windows
cd <DataCollector-directory>
py -3 datacollector.py init
py -3 datacollector.py collect --dry-run
py -3 datacollector.py collect
py -3 datacollector.py serve --port 8765
```

On macOS, use `python3` in place of `py -3`. Copy
`collection-job.example.json` to `collection-job.json` before customizing a
machine's ID, worktrees, source overrides, or legacy archive locations.

The dashboard is local-only at `http://127.0.0.1:8765`. Its Collect action
stores the selected options in `collection-job.json`; Recompute view only reads
the normalized archive and does not copy source files again.

## Job options

`collection-job.json` is the durable local collection job. It is intentionally
ignored by Git; version only the sanitized example. The important fields are:

| Field | Purpose |
| --- | --- |
| `mode` | `input_output` (default), `input_only`, or `output_only` |
| `assistant_output` | `all` visible assistant responses (default) or `final` per prompt |
| `apps` | Enable ChatGPT export/Codex/Claude and opt into experimental sources |
| `source_roots` | Per-app source-root overrides for another machine or nonstandard installs |
| `legacy_archives` | Prior `collected_context` roots to normalize in place, without copying their raw files again |
| `project_filters` | Optional path/title filters for a focused training view; raw collection remains complete |
| `copy_raw` | Preserve original source artifacts under `raw/`; leave enabled for recomputation |
| `worktrees` | Git status, history, and current diff evidence to collect for named worktrees |

Use `collection-job.example.json` as a portable starting point. On macOS, run
the same script from the copied folder; defaults resolve from that machine's
home directory. Set a distinct `machine_id` before merging captures from two
machines.

## Output layout

```text
collected_context/
  raw/<machine-id>/<drive-or-ROOT>/...      original materialized logs
  index/sessions/<app>/<machine-id>/*.json  normalized full sessions
  index/raw_catalog.json                    source-to-session inventory
  index_unified/training_sessions.jsonl     selected, session-wise training stream
  index_unified/training_turns.jsonl        serial session + turn chronology
  index_unified/input_output_pairs.jsonl    ready user/assistant pairs in default mode
  index_unified/timeline_events.jsonl       metadata-only chronological hierarchy
  exports/worktrees/<name>/                 git status, log, and worktree diff
```

Local captures are real files, not symbolic links. Configured `legacy_archives`
remain in their existing locations and are referenced from `index/raw_catalog.json`
with `storage: "external"`; this avoids duplicating a large historic archive onto
the current machine. `raw/` and all collected content are ignored by Git to
prevent accidental commits. The collector never scans
credential files such as `~/.codex/.credentials.json` or `~/.claude/.credentials.json`.
Raw session logs may still contain secrets typed into a conversation, so run
the AutoYou redaction stage before training or sharing any derived data.

## Full-history retention

There is no date cutoff, session-count limit, or turn-text truncation in the
collector. Each run recursively scans every matching session artifact in each
enabled source root. `index_unified/coverage.json` records the actual earliest
and latest timestamps after each collection, along with the policy values. A
configured legacy archive can be normalized into the current unified index
without a second raw copy. Saved ChatGPT project exports under
`exports*/chatgpt/` are indexed in place as well; their original accessibility
snapshots remain preserved beside the normalized session record.

Use this portable shape when a prior archive is available on another disk:

```json
"legacy_archives": [{
  "id": "historical-drive",
  "path": "/absolute/path/to/collected_context",
  "source_sets": {"C": "windows-my-machine", "ROOT": "macos-my-machine"}
}]
```

On Windows, use an escaped drive path such as `"E:\\archive\\collected_context"`.

## Sharing safely

The public-safe surface is `datacollector.py`, `tests/`, this README,
`.gitignore`, and `collection-job.example.json`. The active
`collection-job.json`, collected raw/index data, and runtime logs are ignored.
Do not publish any capture until it has been reviewed and redacted: a session
log can contain secrets entered in a prompt even though credential files are
not scanned directly.

## Recompute and scheduling

```powershell
# Change only the training view; source logs are not recopied.
py -3 datacollector.py rebuild --mode input_only
py -3 datacollector.py rebuild --mode output_only --assistant-output final

# See the exact scheduled-task command first.
py -3 datacollector.py schedule --every-minutes 60 --dry-run

# Install or remove a Windows Task Scheduler task.
py -3 datacollector.py schedule --every-minutes 60
py -3 datacollector.py schedule --remove
```

On macOS, the same `schedule` command writes and bootstraps a user LaunchAgent.
The task runs `collect` against the same job, so active sessions are picked up
incrementally when their size or modification time changes.

## Verify

```powershell
py -3 -m unittest discover -s tests -v
```
