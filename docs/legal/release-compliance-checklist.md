# AutoYou Server Source Publication Checklist

This checklist covers the source-available server repository. It records engineering evidence, not legal approval or certification of compiled apps. The repository [LICENSE](../../LICENSE), [publication manifest](source-publication-manifest.md), and third-party notices define the proposed source boundary.

## Engineering checks

- [x] Keep hosted account, billing, signing, store, client, and private agent material outside the public source allowlist.
- [x] Generate server-only SBOM and NOTICE bundles and run the source license checks.
- [x] Provide contributor, security, support, and code-of-conduct guidance for the public repository.

## Open publication decisions

- [ ] Review ownership, contributor rights, patent and trademark posture, and the source license with qualified counsel and the owner. Do not treat this checklist as approval.
- [ ] Review current third-party package, asset, and provider terms for the intended public source distribution.
- [ ] Audit the final source tree and full repository history for secrets, personal data, and private paths before making the repository public.
- [ ] Record the approved commit, source archive checksum, publication URL, date, and reviewer in the private release record.

Windows, macOS, WSL, native v2, mobile, and hosted-service releases have separate build, store, and operational checks. A source check does not establish those releases are ready.
