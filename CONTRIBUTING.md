# Contributing to AutoYou Server

AutoYou Server is source-available under the [LICENSE](LICENSE). It is not an OSI-approved open-source project; personal-use and commercial-use restrictions apply. Contributions are welcome, and accepted contributions can earn a share of the [Contributor Pool](docs/contributors/contributor-pool.md). Questions and conversation live in the [community](https://www.autoyou.me/community/), and product help is at [www.autoyou.me/support](https://www.autoyou.me/support/).

## How to contribute

1. **Find or open an issue.** Look for issues labeled `good first issue` or `help wanted`, or open one that describes the defect or proposal. For a non-trivial change, wait for a maintainer to confirm that the work is in scope.
2. **Claim it.** Comment on the issue. A maintainer assigns it to you. Work linked to an assigned issue is what earns Contributor Pool points.
3. **Agree to the contribution terms.** Read the [Contributor License Agreement](docs/contributors/CLA.md). You agree by checking its box in the pull request template and signing off every commit with `git commit -s`, which also certifies the [Developer Certificate of Origin](https://developercertificate.org/).
4. **Fork, branch, and make the change.** Run the [local checks](#local-checks) before you push.
5. **Open a pull request** linked to the issue (for example `Closes #123`) and fill in the template, including the AI assistance section.
6. **Review.** A maintainer reviews the pull request. Merged contributions are recorded for the pool.

Pull requests that are not linked to an approved issue may be closed without review.

## Rules for every contribution

- Read [LICENSE](LICENSE) and [SECURITY.md](SECURITY.md). Report vulnerabilities privately as SECURITY.md describes, never in an issue or pull request.
- Never submit secrets, credentials, signing material, non-release logs, user data, generated binaries, or unreleased planning material.
- Keep the public tree source-available only. Do not add `.llm`, `autoyou-core`, `autoyou_lite`, `autoyou-website`, personal workspaces, runtime profiles, model outputs, voice-training datasets, browser profiles, generated archives, or private docs.
- Keep client applications and hosted account services outside this server repository. They live in the [AutoYou ecosystem](https://www.autoyou.me/ecosystem/).
- Source files carry provenance markers (`AUTOYOU-PROVENANCE-*` comment lines and `__debug_provenance_*__` variables). They are maintainer debug traces and do not change your rights or obligations under the [LICENSE](LICENSE). Leave them in place when you edit a file.
- Submit only material you have the right to license; check employer, third-party, and patent obligations before proposing code or documentation. Identify any third-party code in the pull request with its source and license.
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## AI-assisted contributions

AI tools are welcome as helpers. A person must review every line, understand it, take responsibility for it, and say in the pull request which tools were used. A pull request from an autonomous agent with no responsible person, or a bulk of AI-generated changes, will be closed and earns nothing from the Contributor Pool.

For Codex, Claude, and other AI-assisted work, use the public guidance in
[docs/contributors/](docs/contributors/README.md). It includes the validation
contract and the opt-in live-server test procedure.

## Contributor Pool

OpenStorey sets aside a share of the money it receives from sponsorships and donations for contributors. The pool is discretionary and depends on funding. [docs/contributors/contributor-pool.md](docs/contributors/contributor-pool.md) explains who is eligible, how points work, and how payouts and taxes are handled.

Separately, OpenStorey commits a percentage of the money it raises to the open-source developers AutoYou is built on; see the [Open-Source Commitment](docs/legal/open-source-commitment.md). You can support both at [www.autoyou.me/donate](https://www.autoyou.me/donate/).

## Community and support

- **Community:** [www.autoyou.me/community](https://www.autoyou.me/community/), for conversation with other users and contributors.
- **Support:** [www.autoyou.me/support](https://www.autoyou.me/support/), for setup, billing, and account questions. Use GitHub issues only for reproducible defects and proposals.
- **Ecosystem:** [www.autoyou.me/ecosystem](https://www.autoyou.me/ecosystem/), for the apps and components around the server.
- **Sponsor:** [www.autoyou.me/donate](https://www.autoyou.me/donate/).
- **Website:** [www.autoyou.me](https://www.autoyou.me/).

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

## Issues

Use issues for reproducible defects and proposals. Do not include secrets, personal data, non-release logs, or vulnerability details.
