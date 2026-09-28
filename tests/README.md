# AutoYou Server tests

Run checks from this repository root. `conftest.py` creates a temporary `AUTOYOU_TEST_ROOT` for pytest and disables production keyring access. Tests must use synthetic identifiers and must not change an operator's config, registries, databases, or credential store.

## Hermetic checks

- `tests/server/`: server routes, runtime, packaging plans, and protocols.
- `tests/agents/public/`: built-in agent contracts and shared tools.
- `tests/shared/`: shared server utilities.

```bash
python -m pytest tests/server/build tests/test_public_source_export.py -q -p no:cacheprovider
python -m pytest tests/server tests/agents/public tests/shared -q -p no:cacheprovider
```

Select the smallest affected files first. For source or packaging changes, also run `python scripts/export_public_autoyou_server.py --worktree --check`.

## Optional local integration checks

The isolated harness under `tests/server/e2e/` is opt-in and starts a server on loopback ports. See [TESTING.md](../docs/contributors/TESTING.md) for its prerequisites and commands. Do not run it for a hermetic-only request.

The ignored `private/` directory may hold local agent integrations and `private/tests/`. Run those tests explicitly with `python -m pytest private/tests -q -p no:cacheprovider` after checking they honor `AUTOYOU_TEST_ROOT`. Public CI never collects them by default.
