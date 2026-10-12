# Testing AutoYou Server

Run tests from the repository root. The root `conftest.py` creates an isolated
temporary `AUTOYOU_TEST_ROOT` by default.

## Fast public checks

```powershell
python -m pytest tests/server/build tests/shared/test_native_libsodium.py tests/test_public_source_export.py tests/test_official_build_authorization.py tests/test_release_legal_gates.py tests/test_dependency_advisories.py -q --no-header -p no:cacheprovider
python scripts/export_public_autoyou_server.py --worktree --check
python scripts/check_release_legal_gates.py --no-generate --artifact-scope server --strict-unknown-license --allow-open-release-blockers
```

The `--allow-open-release-blockers` form checks the generated legal material
without claiming that human release approvals are complete. A release owner
must run the strict form in [CONTRIBUTING.md](../../CONTRIBUTING.md) before a
release decision.

## Opt-in isolated live-server checks

Install the relevant test dependencies first, then use a synthetic password
and the isolated harness:

```powershell
$env:AUTOYOU_RUN_BOOTSTRAP_E2E = "1"
$env:AUTOYOU_E2E_SERVER_PASSWORD = "synthetic-e2e-password-47"
python -m pytest tests/server/e2e/bootstrap tests/server/e2e/scenarios -q
```

The harness starts a local server on loopback ports and removes its temporary
runtime state. Do not point this command at a live configuration or use a
deployment password. Password-change tests must restore their fixture state.

## Optional private agent tests

Keep local integration code and tests under the ignored `private/` directory.
Run `python -m pytest private/tests -q -p no:cacheprovider` only when those tests
exist and are requested. Public CI does not collect that directory.

## Test data rules

- Use reserved or synthetic phone numbers, addresses, IDs, device names, and
  tokens.
- Keep legacy password and short-password coverage inside isolated unit tests.
  They are regression inputs, not setup values.
- Make any network, provider, or device test opt-in and state its prerequisites
  in the test or its documentation.
