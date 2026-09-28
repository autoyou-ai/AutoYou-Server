# AutoYou Server Maintainer Release Record

Record refreshed: 2026-09-27 (PDT)
Release date: pending owner publication

This is source-only maintainer evidence, not a license grant, legal approval, or
evidence that a compiled release is ready. The 2026-07-14 evidence is retained
below as a historical snapshot; the current candidate has separate checks and
hashes.

## Current source candidate: 2026-09-27

### Selected source and archive

- Candidate source commit: `ddd072075b7d2f61752c699e305530fe30c79653`
  (`main` at the time of this review); owner-approved release commit: pending.
- Archive: `autoyou-server-ddd0720-source.zip`; SHA-256:
  `C1D693AEFE678D8A0562B613120B5FC4ABD1DACE66AA6C10355BEFA29DA8A4AD`.
- Format: deterministic Git ZIP archive of that commit's 1,088 regular files,
  excluding this evidence-only record to avoid a self-referential checksum.
  The archive was extracted and checked; no `clients/`, `v2/`, `.llm/`, or
  `autoyou_agents/private/` entries were present.
- Publication URL, publication date, release tag, owner approval, and reviewer:
  pending. The source candidate and its checksum do not establish publication.

### Current validation evidence

- The committed-source and worktree public boundary checks both passed with
  1,089 included repository files at the candidate commit. A separate scan of
  every reachable commit found 1,089 unique historical paths and no path
  outside the publication allowlist.
- Gitleaks v8.30.1, using its verified official macOS arm64 binary, scanned
  all reachable Git history through the candidate commit and the extracted
  source archive. Both runs exited 0 with zero findings. These automated
  checks do not replace the final human review for personal data or private
  paths in source content.
- The current public-check-equivalent Python suite passed: 123 tests, with
  four dependency deprecation warnings. The `public-checks` workflow for the
  candidate commit also completed successfully.
- `node/tunnelmole` passed `npm audit --omit=dev` with zero production
  vulnerabilities.
- The five checked-in Server NOTICE/SBOM profile pairs were compared with
  freshly computed components and notices using their recorded timestamps;
  all five matched the current release-artifact definitions. No new bundle
  timestamp or approval was assigned by this comparison.
- The strict source legal gate failed on the four open publication decisions
  in `release-compliance-checklist.md`. With
  `--allow-open-release-blockers`, the remaining engineering checks passed.
  Ownership/counsel, current third-party terms, final human source/history
  review, and owner-approved publication evidence remain open.
- No Windows, macOS, WSL, native v2, mobile, or hosted-service build or
  operational release check was run for this record refresh.

### Current source and legal file hashes

SHA-256 values below describe the candidate commit and its checked-in legal
files; they supersede the July table for this candidate only.

| File | SHA-256 |
|---|---|
| `LICENSE` | `3D36C9669DCF9789D7EF00F91433FB0CFFE2C0924D5C6490FD29D6725D8436BA` |
| `THIRD-PARTY-NOTICES.md` | `11D981097EA8FA3B39CBDD366A4BE7D4F3656B31A95BF20818558597C491C839` |
| `docs/legal/generated/manifest-summary.json` | `29A712A1B530EB3009353CBDA359E14CF724AC096C7C067873DE9A89F7CD5E42` |
| `docs/legal/generated/README.md` | `DE56D1A5E88DE5377AD008396EC68D6322711F5B9882ECE1E62F7A30093901DC` |
| `docs/legal/generated/autoyou-server-source-full/NOTICE.txt` | `6454A66BDEF4265E80D7DD319036D1B8D33C03AB7B45C949662C8CC1CA35C2D4` |
| `docs/legal/generated/autoyou-server-source-full/sbom.cdx.json` | `72FA344F8F34CA4211BFE5387DF6308CBD53491696D8E43A67F5E813873B26B8` |
| `docs/legal/generated/autoyou-server-windows-default/NOTICE.txt` | `AA9F9F3DDCAFAA537B047776D7614809967D381D26370C5B5112752917F679AB` |
| `docs/legal/generated/autoyou-server-windows-default/sbom.cdx.json` | `F7CDE467972FABCC1542B1CC3787479DC8C4D8C534AB6691FEA2B6665BE20568` |
| `docs/legal/generated/autoyou-server-macos-default/NOTICE.txt` | `2D2C0248D587C1D5B43FF9223307D79C0F22E5D022A0B7EEB382896F71CA788B` |
| `docs/legal/generated/autoyou-server-macos-default/sbom.cdx.json` | `5F7B60891F3F4F70B76B2D0A8C303B15B20DDE4266ACC8AC5493E2A377AC8347` |
| `docs/legal/generated/autoyou-server-windows-connector-full/NOTICE.txt` | `D1BCEBEAC2A1827842FDE62E85B701DF3925CC3690BC3E54254DBEE52DBBF8D4` |
| `docs/legal/generated/autoyou-server-windows-connector-full/sbom.cdx.json` | `5BE090337EF84AFD8A0DA3582AF21356D88E67B7C032B05311278A9E914F6541` |
| `docs/legal/generated/autoyou-server-macos-connector-full/NOTICE.txt` | `BA4FABB23A9BAD18F5C6D892627DCCD11556A458967258DF4EF7C79394B10B82` |
| `docs/legal/generated/autoyou-server-macos-connector-full/sbom.cdx.json` | `65C203D83E099E5DE55443FB1B6DA208C6E916D01EAE637BBD36B317ACCCD8A9` |

## Historical source candidate: 2026-07-14

The following evidence belongs to the earlier reviewed archive. Its hashes,
checks, and repository controls must not be read as current evidence.

### Selected source and archive

- Reviewed release commit: `8eae613e2bfc7f8ba9d607316b5dba34a0298c93`
- Archive: `autoyou-server-8eae613.zip`
- Archive format: deterministic Git source archive of the reviewed commit,
  excluding this evidence-only record
- Archive SHA-256:
  `E33EAD6EA2CBC5C21124C3700ACC7DC66EEED3269F82F7A6D90381A2DD0D92E4`
- The archive intentionally excludes this evidence file so its checksum is not
  self-referential. The evidence file is committed in the public repository.

### Public-boundary and validation evidence

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

### Legal artifacts and hashes

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

### GitHub review and controls

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

### Completion evidence

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
