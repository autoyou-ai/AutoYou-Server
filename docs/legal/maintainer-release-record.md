# AutoYou Server Maintainer Release Record

Release date: 2026-07-14

This record documents the reviewed source-only release candidate. It is
maintainer evidence, not a license grant or legal advice.

## Selected source and archive

- Reviewed release commit: `8eae613e2bfc7f8ba9d607316b5dba34a0298c93`
- Archive: `autoyou-server-8eae613.zip`
- Archive format: deterministic Git source archive of the reviewed commit,
  excluding this evidence-only record
- Archive SHA-256:
  `E33EAD6EA2CBC5C21124C3700ACC7DC66EEED3269F82F7A6D90381A2DD0D92E4`
- The archive intentionally excludes this evidence file so its checksum is not
  self-referential. The evidence file is committed in the public repository.

## Public-boundary and validation evidence

- `python scripts/export_public_autoyou_server.py --check` passed from a
  detached clean checkout of the reviewed commit.
- The worktree boundary check passed with 740 included files and no dirty paths
  or failures.
- Gitleaks v8.30.1 scanned all reachable Git history with `--all` and scanned
  the extracted archive. Both scans exited 0, reported no leaks, and produced
  empty JSON findings reports.
- The excluded private agents were removed from the release tree and stale
  runtime/build output was not included in the archive. No credentials,
  personal data, or unreleased operational payload was found by the boundary
  review.
- The full isolated test run passed with 1,773 passed and 33 skipped. The final
  public-check-equivalent run passed with 108 passed and 4 warnings; focused
  release-boundary tests passed with 83 passed.
- `node/tunnelmole` passed `npm audit --omit=dev` with 0 vulnerabilities;
  dependency lock updates cover the reviewed npm advisories. The test
  requirements were refreshed to supported pytest 9 and pytest-asyncio 1.x
  ranges.

## Legal artifacts and hashes

The selected distributed artifact is source-only. Five configured profiles were
regenerated and reviewed: source-full, Windows default, macOS default, Windows
connector-full, and macOS connector-full. No compiled binary, model weights,
provider credential, or prebuilt Tunnelmole/LLMFit runtime is distributed.

SHA-256 hashes:

| File | SHA-256 |
|---|---|
| `LICENSE` | `3B9EDDE7A6053D9DF9C434EB962EE55CD670C19D6100D60727833877349524CB` |
| `THIRD-PARTY-NOTICES.md` | `2C5DEFF56ED8B6A2A6629088CED3E6BAEB85111BD1E25EE66B3D9F2BF53E5DF6` |
| `docs/legal/generated/manifest-summary.json` | `929FA9B470148CA0FD3A2C42AC3D8A5163A481ADD53314BF5FFC1BD575BAC293` |
| `docs/legal/generated/README.md` | `7670C823F2AE0D8F674768B6A31315E69C2490B7347EA3A4CD0F70FA522471C1` |
| `docs/legal/generated/autoyou-server-source-full/NOTICE.txt` | `1261219A73FA1E2A23D33951D77F31B35433D35B0EC2E6868057BA7A117D9A9D` |
| `docs/legal/generated/autoyou-server-source-full/sbom.cdx.json` | `70C490738A5F4C1DFFEE3A6BE3D1CE96DC4FC13F3685F5FB102AFD5483BB4209` |
| `docs/legal/generated/autoyou-server-windows-default/NOTICE.txt` | `7B677B478119F899E59AE96067086D39681036C0FF3ABA00C38A78D9AFDBCB93` |
| `docs/legal/generated/autoyou-server-windows-default/sbom.cdx.json` | `4B986988D1F1205F535CDC8C740ABE9D74D0EF29539B40DC2C5F5F82798A4B27` |
| `docs/legal/generated/autoyou-server-macos-default/NOTICE.txt` | `72D6770B54B83365A464D7D27C238C023E8537A1DB908099FF227AF7D63997D3` |
| `docs/legal/generated/autoyou-server-macos-default/sbom.cdx.json` | `543F8FB390637EA5A4A3F654AACF71A63C2CC68C31352CCB7ACBE926DD30A5EF` |
| `docs/legal/generated/autoyou-server-windows-connector-full/NOTICE.txt` | `9284477573AE3FF9300793D7A99FC99DAB3062338DAEC1713F93061D55829A97` |
| `docs/legal/generated/autoyou-server-windows-connector-full/sbom.cdx.json` | `CF332C9DC5E312350D7AF0C2CD2ACBB286AD419F93CAD1FFB42A71759947A70D` |
| `docs/legal/generated/autoyou-server-macos-connector-full/NOTICE.txt` | `4C51D385710B8DF0B2A4552662174CD4C3FEEF4E7F57F9ED8BAF850A8AAC32F8` |
| `docs/legal/generated/autoyou-server-macos-connector-full/sbom.cdx.json` | `3F790386E6B30D31046DBD1629267F1A3329E6ACBEB2518F0025CE3A49D69995` |

`docs/legal/optional-integrations.md` was reviewed on 2026-07-14. Provider,
messaging, runtime-download, model, binary, and SDK obligations remain
conditional on an owner enabling those integrations; the source archive does
not approve or bundle them.

## GitHub review and controls

Before the repository was made public, the reviewed surfaces were: no issues,
no releases, no packages, disabled wiki and discussions, an empty visible
Projects page, seven open Dependabot pull requests, and the available workflow
history. The initial workflow expression failure was corrected; the final
`public-checks` run for this release commit completed successfully.

Final repository controls were read back through the GitHub API:

- repository visibility: public;
- secret scanning: enabled;
- secret scanning push protection: enabled;
- Dependabot security updates and automated security fixes: enabled;
- private vulnerability reporting: enabled (`{"enabled":true}`);
- `main` protection: requires `public-checks` context `python`, one approving
  review, linear history, conversation resolution, enforced administrators,
  and disallows force-pushes and deletions.

`support@autoyou.me` is documented in `SECURITY.md`. Mailbox monitoring was not
independently tested; GitHub private vulnerability reporting was enabled and
read back successfully as the tested reporting path.

## Completion evidence

- The exact command
  `python scripts/check_release_legal_gates.py --no-generate --artifact-scope server --strict-unknown-license`
  passed with exit 0 after the release commit was pushed; the transient legal
  gate report was removed during final cleanup.
- A final post-record Gitleaks history scan was run with `--all` and found no
  leaks. The archive scan above remains the checksum-bound release scan.
- Generated `build/`, test-runtime, cache, output, and dependency-install
  directories were removed after validation, and the final Git worktree was
  clean.
- `main` branch protection was reapplied and read back after the evidence push.
