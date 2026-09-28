---
name: autoyou-server-validate
description: Run focused AutoYou Server checks, including explicitly requested isolated local end-to-end tests, without touching operator state.
---

# Validate AutoYou Server

Read `docs/contributors/TESTING.md` and the root `conftest.py` before selecting a check. Run only the scope the user requested. For ordinary changes, start with the affected hermetic pytest files. Do not run end-to-end, live-server, network, or build commands unless the user requested that scope.

Keep `AUTOYOU_TEST_ROOT` isolated for pytest; the root `conftest.py` creates a fresh temporary directory when it is unset. Use synthetic passwords and identifiers. Never point tests at an operator's configuration, credential store, account, device, or deployed service.

For an explicitly requested local end-to-end run, use the opt-in bootstrap or scenario harness documented in `docs/contributors/TESTING.md`. Check that its dependencies and loopback ports are available before starting. Stop if the harness would use real credentials or persistent operator state.

For source or packaging changes, run `python scripts/export_public_autoyou_server.py --worktree --check`. Report commands, results, and checks skipped by the requested scope. Private integration tests under ignored `private/tests/` run only when explicitly selected.
