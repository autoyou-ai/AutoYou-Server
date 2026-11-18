# Coding Agent efficiency

The model and the harness contribute different things. A harness can reduce wasted
context, repeated tool calls, abandoned work, and unverified changes. It cannot
guarantee that a local model will solve the same tasks as a frontier model.
Compare task outcomes before claiming equivalent capability.

## Current execution path

- `agent.py` owns the built-in workspace tools and session-budget callbacks.
- `../pi_agent/agent.py` is an existing optional execution backend, selected by
  `AUTOYOU_CODING_BACKEND=auto|pi|builtin`. Auto selects Pi only when readiness
  succeeds. A started Pi request is never replayed automatically in the built-in
  backend, since that could duplicate edits.
- Pi inherits `OLLAMA_MODEL` when `AI_PROVIDER=ollama` and `PI_MODEL` is empty,
  `default`, or `pi-agent`. Otherwise its explicit model/profile selection wins.
  Check the resolved model for the chosen backend; installing a Qwen model or
  having a builder-suite default does not by itself select it for every backend.
- Pi readiness checks installation, Node >= 22.19, Pi >= 1.2, and model
  availability. Its tools remain AutoYou's registered workspace tools.
- Pi carries recent conversation turns in existing AutoYou session state,
  trimming older turns toward a 24,000-character JSON budget. This is not a durable implementation plan or a
  complete record of intermediate edits and verification.
- The coding defaults are 32 tool calls and 600 seconds per turn. Pi has its own
  180-second default, also bounded by the remaining parent turn budget.
  Increasing limits alone does not make a run more effective.
- Command execution remains opt-in through `AUTOYOU_ENABLE_AGENT_RUN_COMMAND`.
  Model tuning must not silently broaden execution permissions.

See `../../shared/session_execution.py`, `../pi_agent/readiness.py`, and
`../shared_tools/builder_suite.py` for the authoritative defaults.

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

1. **Durable task state.** Keep objective, acceptance checks, completed steps,
   touched files, verification results, and next action in the existing scoped,
   protected conversation store. Checkpoint before compaction or interruption.
   Reconcile that state with the working tree before resuming. Avoid a second
   plaintext history store.
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

Local inspection on 2026-11-17 found `qwen3.8:27b` installed with tool calling and
thinking support. The local model reports Q4_K_M quantization and a 262,144-token
context ceiling. That ceiling is not a recommended working context size; select
context and reasoning settings using measured memory, latency, and task success.
No live model, execution-permission, or budget settings were changed by this work.

## Evidence and references

The focused coding, Pi, workspace, and Audio test selection passed 81 tests.
The root `scripts/e2e_validate.py changed` gate passed via its portable fallback.
The native macOS build still requires macOS; the Windows run checks its runtime
contracts. These checks validate code behavior, not model-level task success.

- [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents)
  describes incremental work, persistent progress artifacts, and verification.
- [Ollama: Tool calling](https://docs.ollama.com/capabilities/tool-calling)
  documents the model/tool loop on which a local harness can build.
