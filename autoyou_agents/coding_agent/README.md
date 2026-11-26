# Coding Agent efficiency

The model and the harness contribute different things. A harness can reduce wasted
context, repeated tool calls, abandoned work, and unverified changes. It cannot
guarantee that a local model will solve the same tasks as a frontier model.
Compare task outcomes before claiming equivalent capability.

## Current execution path

- `agent.py` and `harness.py` own the built-in workspace tools, durable session
  planning (`manage_coding_plan` in `_autoyou_coding_harness`), anti-thrash
  duplicate-call guards, deterministic context compaction, and session-budget
  callbacks.
- `../shared_tools/workspace_rag.py` provides zero-dependency incremental
  SQLite FTS5 BM25 search and AST/multi-language symbol indexing
  (`rag_search_workspace`, `find_symbol`, `get_file_outline`) cached through
  `get_service_data_dir("coding_workspace_rag")` (honoring `AUTOYOU_TEST_ROOT`).
  Source runs follow the development data-root policy; packaged runs use user data.
- `../shared_tools/workspace_tools.py` adds `find_files`, `read_files_batch`,
  `append_file`, `multi_edit_file`, atomic `apply_patch` (Codex `*** Begin Patch`
  and unified diff, `allow_delete=False` default), nearest-match `replace_text`
  hints, middle-elision `keep_tail=True` command output, shell-free
  `verify_workspace_files`, and compiled-safe writable roots (`game_projects`,
  `mobile_games`, dynamic agents) for Nuitka v2 macOS/Windows bundles.
- `../pi_agent/agent.py` is an existing optional execution backend, selected by
  `AUTOYOU_CODING_BACKEND=auto|pi|builtin`. Auto selects Pi only when readiness
  succeeds. A started Pi request is never replayed automatically in the built-in
  backend, since that could duplicate edits.
- Pi inherits `OLLAMA_MODEL` when `AI_PROVIDER=ollama` and `PI_MODEL` is empty,
  `default`, or `pi-agent`. Otherwise its explicit model/profile selection wins.
  Its `WorkspaceBridge` exposes the upgraded safe RAG, outline, batch-read,
  patch, and verification tools while keeping destructive tools excluded.
- Coding, Internet, dynamic agents, ADK model calls, Pi, and the outer chat
  request have no default lifetime/count limit (`0` means unlimited). Operators
  can set positive limits with `AUTOYOU_CODING_AGENT_MAX_TOOL_CALLS`,
  `AUTOYOU_CODING_AGENT_MAX_TURN_SECONDS`, `AUTOYOU_MAX_LLM_CALLS` (or
  `ADK_MAX_LLM_CALLS`), `AUTOYOU_INTERNET_MAX_TOOL_CALLS`,
  `AUTOYOU_INTERNET_MAX_CALLS_PER_DOMAIN`, `AUTOYOU_DYNAMIC_AGENT_MAX_TOOL_CALLS`,
  `AUTOYOU_PI_TIMEOUT_SECONDS`, `AUTOYOU_SESSION_TURN_TIMEOUT_SECONDS`, and
  `AI_AGENT_RUN_SSE_TIMEOUT_SECONDS`. Individual network/command timeouts still
  detect stuck operations; `run_command(timeout_seconds=0)` permits a long command.
- `CheckpointedAgentTool` saves specialist state to the parent conversation's
  existing session service during execution. Coding records mutation intent
  before running a tool and its result afterwards, retains consumed handoff
  constraints, and verifies pending files before accepting a final response.
  The working event cache keeps the current request and recent complete tool
  groups; persisted parent history stays in the conversation store. Repeated
  failures without progress pause at a resumable checkpoint rather than claiming
  success. There is no count ceiling on productive work.
- Builder handoffs (`agent_builder_agent`, `website_agent`, `games_agent`) store
  payloads in persistent session state and automatically dispatch through the
  root coordinator inside local-model `AgentTool` runners, without a two-hop cutoff.
- Pi retains `maxTokens=8192`; built-in coding enforces a
  `max_output_tokens >= 4096` floor promoted into Ollama `num_predict`.
- Command execution remains opt-in through `AUTOYOU_ENABLE_AGENT_RUN_COMMAND`.
  Model tuning must not silently broaden execution permissions.

See `../../shared/session_execution.py`, `harness.py`, `../pi_agent/readiness.py`,
`../shared_tools/workspace_rag.py`, and `../shared_tools/builder_suite.py` for
the authoritative defaults.

## Improvements implemented

1. Literal workspace search uses ripgrep to shortlist matching text files when
   available, then retains the existing result format and Python matching.
   Missing or incompatible ripgrep falls back to Python. Regex searches retain
   Python syntax, including lookarounds. File-scoped searches also work.
2. `read_file(offset=next_start_line)` preserves the requested page size. The
   previous default continuation beyond line 250 collapsed to a one-line read.
3. Timeout handling uses the structured `timed_out` result. Successful test
   output mentioning timeout tests no longer pauses the agent, and successful
   tools reset the timeout streak. Real timeouts still pause execution.
4. The prompt asks for focused searches, continuation reads, corrected retries,
   a short implementation checklist, and verification before completion. It no
   longer asks whether a frontend is needed when the request already specifies it.

These are tool correctness and workflow improvements. They are not an end-to-end
Qwen benchmark or a claim of parity with other coding products.

## Next development priorities

1. **Restart orchestration.** Checkpoints persist in the existing conversation
   store and an interrupted operation must be reconciled against current files
   before it is replayed. Automatic unattended continuation after a server or
   machine restart is not implemented. A continuously running task requires a
   running worker and model; resumption uses the saved conversation checkpoint.
2. **Context budgeting.** Reserve room for generation and tool output, preserve a
   stable prompt prefix, and use relevant source excerpts. Give large tool
   outputs bounded previews with continuation references. Record token usage
   and model/tool duration without recording private source in telemetry.
3. **Verification and recovery.** Make inspect, edit, test, and repair explicit
   task stages. Separate a long-running command's status polling from model
   inference. Retry only after new evidence; preserve user edits and existing
   command permissions. Check UI tasks in the browser when available.
4. **A reproducible evaluation suite.** Use isolated fixture repositories for a
   small bug fix, a multi-file change, test-failure recovery, a UI change, and an
   interrupted task. Compare the same model, hardware, quantization, and task
   across built-in and Pi backends. Track accepted task completion, wall time,
   input/output tokens, tool calls, repeated reads, and unsuccessful retries.
   Use independent acceptance tests, not the model's own completion statement.

## Evidence and references

Validation on 2026-11-26 used the manifest-pinned ADK 2.15.0, GenAI 2.34.0,
and LiteLLM 1.110.1 in a disposable environment:

- The 40-file regression selection passed 682 tests (6 skipped; 7 subtests passed).
- Four opt-in live checks passed with `ollama_chat/qwen3.5:4b`: coding edits and
  verification, the production root dispatcher with games-to-coding handoff and
  an offline playable game, synthetic image input, and HTTPS fetch/research recall.
- The macOS Swift suite ran 93 tests (1 skipped, no failures). The Windows client
  .NET suite passed 260 tests on macOS; it does not validate native Windows UI or MSIX.
- `scripts/e2e_validate.py changed` passed via its portable fallback, and
  `scripts/validate_llm_docs.py` reported 122 files, OK.

Live probes used isolated state and workspaces without changing the operator's
server configuration or registries. Packaged binaries were not rebuilt or
deployed, and these checks are not a months-long endurance run.

- [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents)
  describes incremental work, persistent progress artifacts, and verification.
- [Ollama: Tool calling](https://docs.ollama.com/capabilities/tool-calling)
  documents the model/tool loop on which a local harness can build.

## Reproducible verification

`tests/agents/public/test_builder_harness_resilience.py` covers failed writes,
patch rollback/newlines, packaged builder roots, session isolation, cancellation,
persisted child checkpoints, bounded event caches, six-month simulated elapsed
time, and an actual nested ADK run exceeding 500 model calls.

For real local-model checks, run
`AUTOYOU_RUN_LIVE_HARNESS_TESTS=1 python -m pytest -q tests/agents/public/test_builder_harness_live.py`.
These tests use disposable workspaces and state, assert edited-file behavior,
exercise games-to-coding root handoff and an offline browser game, and send a
synthetic image through LiteLLM/Ollama. They never change the live server's
configuration or registries. A short probe is not a months-long endurance test
or proof of model-independent parity with another harness.
