# Contributing to AutoYou Server

AutoYou Server is source-available under the [LICENSE](LICENSE). It is not an OSI-approved open-source project; personal-use and commercial-use restrictions apply.

## Before you start

- Read [LICENSE](LICENSE) and [SECURITY.md](SECURITY.md).
- For a non-trivial change, open an issue first so a maintainer can confirm that the work is in scope.
- Never submit secrets, credentials, signing material, non-release logs, user data, generated binaries, or unreleased planning material.
- Keep the public tree source-available only. Do not add `.llm`, `autoyou-core`, `autoyou_lite`, `autoyou-website`, personal workspaces, runtime profiles, model outputs, voice-training datasets, browser profiles, generated archives, or private docs.
- Keep client applications and hosted account services outside this server repository.
- Submit only material you have the right to license; check employer, third-party, and patent obligations before proposing code or documentation.
- External code contributions require a maintainer request and the applicable written contribution agreement. Do not open a pull request until that has been arranged.

## Local checks

Use an isolated virtual environment and the test runtime root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements/base.txt -r requirements/test.txt
python -m pytest tests/server/build tests/test_public_source_export.py -q --no-header -p no:cacheprovider
python -m pytest tests/test_official_build_authorization.py -q --no-header -p no:cacheprovider
```

Before a release, from a clean checkout, also run:

```powershell
python scripts/export_public_autoyou_server.py --check
python scripts/check_release_legal_gates.py --no-generate --artifact-scope server --strict-unknown-license
```

Official release builds from `servers/` must retain the authorization gate and
must pass `scripts/check_official_build_authorization.py --required` with a
signed authorization payload from SignToROSS/OpenSign (`https://sign.autoyou.me/`)
for the exact artifact profile. The server version is declared in [VERSION](VERSION).
Development-only skip flags must not be used for public release artifacts.

Immediately before creating the clean public commit, also run:

```powershell
python scripts/export_public_autoyou_server.py --worktree --check
```

Tests must use synthetic data and must not modify live AutoYou configuration, credential stores, or local services.

For Codex, Claude, and other AI-assisted work, use the public guidance in
[docs/contributors/](docs/contributors/README.md). It includes the validation
contract and the opt-in live-server test procedure.

## Issues

Use issues for reproducible defects and proposals. Do not include secrets, personal data, non-release logs, or vulnerability details.
