# Contributing to AutoYou Server

AutoYou Server is source-available under [LICENSE](LICENSE).
Contributions are reviewed for correctness, security, maintainability, and
licensing. Read the [Code of Conduct](CODE_OF_CONDUCT.md) and
[security policy](SECURITY.md) before participating.

## Propose and submit a change

1. Search existing issues. Describe the problem and a small proposed change.
   Obtain maintainer agreement before substantial work or a new dependency.
2. For Contributor Pool eligibility, have the issue approved and assigned before
   starting. Unsolicited work has no promise of review, acceptance, or payment.
3. Read the [Contributor License Agreement](docs/contributors/CLA.md). Keep the
   authorship and licensing of your changes clear.
4. Use a branch, make focused changes, and run the relevant isolated checks.
5. Submit a pull request linked to the issue. Explain behavior changes,
   verification, limitations, dependency changes, and AI assistance.
6. Explicitly accept the CLA in the pull request. Sign off commits with
   `git commit -s` to certify the separate
   [Developer Certificate of Origin](https://developercertificate.org/).

Maintainers may request changes or decline a submission. An issue assignment
is coordination, not a work order or payment agreement.

## Ownership and licensing

You keep copyright in your original contributions. The CLA gives OpenStorey
permission to use, distribute, and relicense them, including commercially.
Review that grant before submitting. It does not give you ownership of existing
AutoYou code or authority to relicense someone else's material.

Independent original agents, extensions, and other separable works can be
published separately under your chosen license, including MIT or Apache-2.0,
without maintainer approval. Copies and modifications of AutoYou code retain
their applicable license, even if placed in a new file or repository.

For inclusion here:

- Changes to existing code retain that code's license.
- Original new files normally use the repository license.
- You may propose MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, or ISC for a
  separable original file. Obtain maintainer approval before merge, identify
  the license with an SPDX header, and include its copyright and license text.
  Those files retain their own permissions, including permissions broader than
  the repository license.
- Identify all imported code, assets, models, and dependencies with their
  source, version, and license. Do not relicense third-party material through
  the CLA. Maintainers must review compatibility and distribution obligations.
- Contributions on behalf of an organization require authority from the
  relevant rights holder; request a corporate agreement where necessary.

## Security and public repository boundaries

Never submit credentials, signing keys, real user identifiers, runtime state,
personal logs, private operational documents, generated release archives, or
material you are not authorized to publish. Use synthetic or reserved data in
examples and tests. Scrub screenshots and attachments.

Keep changes self-contained in this server repository. Do not require files,
accounts, or infrastructure absent from it for ordinary development.
Separately distributed client applications and hosted services are outside its
source scope.

Report vulnerabilities privately using [SECURITY.md](SECURITY.md). Coordinate
a fix before opening an issue or pull request that would disclose exploitation
details. Do not test against another person's installation without permission.

For dependency changes, explain the need, upstream source, version choice,
license, and known advisory status. Update applicable manifests and release
metadata. A hash identifies bytes; it does not establish that those bytes are
safe. See [requirements/README.md](requirements/README.md).

Existing provenance comments and debug markers are maintainer traces. They
do not expand or reduce license rights. Preserve them when editing.

## AI-assisted work

A responsible person must understand and review the submission and its tests,
confirm the right to submit it, and disclose material AI assistance. Do not
submit generated code you cannot explain, fabricated evidence, copied code
without permission, or confidential prompts and data. The same review standards
apply regardless of which tools prepared a change.

## Local checks

Use [the testing guide](docs/contributors/TESTING.md). Pytest's root fixtures
isolate runtime state with `AUTOYOU_TEST_ROOT`; do not bypass that isolation or
run tests against a live configuration.

```bash
python -m pytest tests/server/build tests/shared/test_native_libsodium.py tests/test_public_source_export.py tests/test_official_build_authorization.py tests/test_release_legal_gates.py tests/test_dependency_advisories.py -q --no-header -p no:cacheprovider
python scripts/export_public_autoyou_server.py --worktree --check
python scripts/check_release_legal_gates.py --no-generate --artifact-scope server --strict-unknown-license --allow-open-release-blockers
```

The last command checks metadata while retaining open release blockers. It is
not publication approval. Official release owners supply their external review
file using `AUTOYOU_RELEASE_CHECKLIST` or `--release-checklist` and run the
strict gate without `--allow-open-release-blockers` before distribution.
Do not weaken signing, authorization, or release controls to pass a check.

## Community and funding

Use [community](https://www.autoyou.me/community/) for discussion and
[SUPPORT.md](SUPPORT.md) for help. Sponsorship is optional.

The [funding commitment](docs/legal/open-source-commitment.md) allocates at
least 15% of qualifying net receipts to approved contributors. Eligibility,
review, and discretionary awards are governed by the
[Contributor Pool policy](docs/contributors/contributor-pool.md). Contributions,
points, and donations are not employment, equity, or a guaranteed payment.
