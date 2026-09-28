# AutoYou Source Publication Manifest

Last updated: 2026-09-27

This manifest defines the source-publication scope for AutoYou local/self-hosted releases. It is not legal advice, does not publish anything by itself, and does not grant rights beyond the repository `LICENSE`, trademark policy, third-party licenses, or a separate written agreement signed by OpenStorey LLC.

## Publishable Source Scope

Subject to final release review, the source-available AutoYou Server
publication set should include:

- root server source, `core_server/`, `routers/`, shared modules, tests, release scripts, and local run/build scripts needed to build and run the local AutoYou server;
- `servers/windows/`, `servers/macos/`, and `servers/wsl/` wrapper/build scripts and host source needed to build local server packages;
- Dockerfiles, Docker Compose files, and server requirements needed for local full-server source builds;
- reviewed protocol/security documentation and assets, including the three
  synthetic-value admin UI captures named in the exporter allowlist, plus the
  server repository's contributor and legal guidance;
- server legal, attribution, SBOM/NOTICE, trademark, and release-profile documents needed to understand the source-available release boundary;
- generated legal bundles for AutoYou Server source, Windows, and macOS release profiles only.

The public export is produced from committed Git content, not the dirty working
tree, by `python scripts/export_public_autoyou_server.py --check` and then
`python scripts/export_public_autoyou_server.py --output <path> --force`.
The export check rejects private roots, sensitive filenames, real-looking
AdMob IDs, private-key PEM blocks, AWS access keys, and Google API keys.

## Do Not Publish In Source Releases

Source publication must exclude:

- production secrets, API keys, certificates, signing keys, provisioning profiles, store-account credentials, payment credentials, cloud credentials, and private tunnel credentials;
- `aws-checkout/`, `autoyou-core/`, `research/`, `reference/`, `references/`, private `.llm/` memory, local agent/Codex/Claude metadata, and standalone website/outreach/cloud-service workspaces;
- `autoyou-dev/` coordinator source and state. Public source releases may keep
  neither build-machine collection/classification tools nor the maintainer
  release ownership matrix or publication handoff state;
- desktop-agent capture assets, unreviewed binary/media assets, maintainer training tests, and local runtime files;
- tracked or local `.llm/machines/profiles/` and `.llm/machines/runs/`
  observations, even when the collector reports that they contain no personal
  identifiers;
- the entire `clients/` tree, including the Python CLI, GUI, browser extension, and all native/mobile/desktop clients;
- private agent packages listed in `PRIVATE_AGENT_PACKAGE_NAMES` in `autoyou_agents/shared_tools/agent_install_registry.py`;
- generated legal bundles for excluded Android, iOS, Connect, AutoYou_Lite, and Python GUI profiles;
- internal AutoYou Lite publishing, protected-wheel migration docs, source-dependent Lite tests, mobile/client UI, cloud-pair fixture, app-store upload, ad settlement, live partner, and Connect artifact verification helper scripts;
- `autoyou_lite/` source while the protected PyPI policy remains active; `autoyou-lite` must be released as an audited protected wheel, not an sdist or public source wheel;
- private deployment manifests, production infrastructure topology, abuse/fraud playbooks, unreleased exploit/security details, and incident-response records;
- `training/brain/`, `support_training/`, captured conversation/session data,
  and generated `training/data/` knowledge artifacts. These remain opt-in
  maintenance surfaces and are not required for ordinary feature development
  or public source publication;
- generated `dist/`, `build/`, `.venv/`, packaged binaries, local logs, runtime databases, keystores, cache files, operator state, and ignored test/runtime artifacts;
- private customer, contributor, donor, support, telemetry, billing, device, account, or request identifiers;
- protected wheel source distributions while protected-source policy remains active;
- hosted AutoYou Cloud Pair, billing/account, entitlement, abuse, signing, notarization, release-channel, store-listing, support, and service-monitoring operations unless OpenStorey separately decides to publish a safe subset.

## Official Build Access Gate

Official build access and commercial redistribution must remain separate from
public source visibility. SignToROSS/OpenSign at `https://sign.autoyou.me/`
must send the build-access agreement only to `build@autoyou.me` and to the
requester's OAuth email, which is the same account identity used for AutoYou
subscriptions. `admin@autoyou.me` is the official organization admin contact
for countersignature or profile/build-approval verification. `OpenStorey.official@gmail.com`
may be recorded as witness/reference metadata only; do not send signing
notifications to it. Only after the signed agreement is recorded should the
server-side build authorization flag permit official build jobs to continue.

That agreement must state that AutoYou may not be used for illegal purposes or
illegally distributed without partnering or a separate signed commercial license.
The server-side authorization record lives in the account service
`/v1/build/authorizations` flow, and official build scripts can require it with
`AUTOYOU_OFFICIAL_BUILD_AUTH_REQUIRED=true` plus the signed authorization id or
file checked by `scripts/check_official_build_authorization.py`.

SignToROSS is a separate service boundary and is not required to be published
merely because it is used for signing or agreement records. If it is published,
its own copyright chain, upstream OpenSign AGPL-3.0 obligations, license,
notices, dependency record, and secret-free source boundary must be verified.

## Required Pre-Publication Checks

Before marking source publication complete:

- run `git status --ignored` and review ignored/untracked artifacts for accidental secrets or generated binaries;
- run `python scripts/export_public_autoyou_server.py --check` from the clean committed ref being published; do not use `--allow-dirty-source` for release evidence;
- run the server release legal gate with `python scripts/check_release_legal_gates.py --artifact-scope server --strict-unknown-license`;
- run a secret scan suitable for the release channel;
- confirm tests and fixtures use synthetic identifiers only;
- preserve the server repository's reviewed publication allowlist and repository-specific checks;
- run `python scripts/export_public_autoyou_server.py --worktree --check` in the server-only checkout to reject any committed or nonignored file outside that allowlist;
- confirm generated legal bundles and release-profile docs match the source tree being published;
- confirm public docs do not promise hosted-service availability, official support, commercial redistribution, app-store rights, or full protection from all liability.

## Publication Evidence

Retain private release evidence showing:

- commit SHA / tag;
- publication URL or package source URL;
- source archive checksum;
- release profile and SBOM/NOTICE bundle hashes;
- reviewer names or roles;
- date/time of publication;
- any excluded paths and reason.

## Boundary

Publication of this source set does not publish or license OpenStorey-hosted services, signing/notarization credentials, official app-store listings, support operations, trademarks, commercial redistribution rights, or platform/vendor approvals.
