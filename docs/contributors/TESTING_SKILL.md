# AutoYou Server Validation Skill

Use this reusable instruction set for Claude, Codex, or another coding
assistant that is asked to validate a change.

## Contract

1. Read [AGENTS.md](AGENTS.md) and identify the changed component.
2. Run the narrowest relevant test command before broader checks.
3. Set or preserve `AUTOYOU_TEST_ROOT` for every pytest run.
4. Use only synthetic test data. Do not open, copy, or transmit credentials,
   runtime state, or customer content.
5. For source-boundary, documentation, dependency, packaging, or workflow
   changes, run `python scripts/export_public_autoyou_server.py --worktree --check`.
6. For release-facing changes, run the legal gate shown in
   [TESTING.md](TESTING.md). Treat unresolved human approvals as a handoff
   item, never as a reason to weaken the gate.
7. Report the exact commands, exit status, skipped opt-in tests, and remaining
   manual gates.

The skill may diagnose failures and propose a fix. It must not create an
official release, publish an artifact, alter authorization controls, or use
credentials not supplied for the task.
