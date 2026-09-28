# Peer Relay and AutoYou Social Engineering Checkpoint

Status: implementation and code-review checkpoint for remote testing, not a release declaration.

Captured: 2026-08-24 and refreshed 2026-08-25 on branch `peer-relay`.

This is the handoff source of truth for the next implementation instruction. It records the product contract, reviewed architecture, implementation state, confirmed protections, unresolved findings, and current worktree state. It deliberately does not turn proposals into shipped claims.

## 0. 2026-08-25 implementation delta

This section is the current handoff and supersedes the implementation-status statements below that still describe the frozen `4141927ce` review. The older findings remain useful historical evidence; they are not all current blockers.

Implemented after that freeze:

- The mailbox-to-app flow is complete in source. Pair-code creation returns only delivery metadata. The owned HTTPS email link keeps its reveal token in the fragment, is accepted only for the production/staging AutoYou hosts, opens Social through Android App Links or iOS Universal Links, and reveals only when the same paid owner session is present. Android and iOS build the canonical email/code/link share text and QR locally and hand it to the native share sheet.
- Pairing is directional. Redeeming A's code adds A to B. A reverse edge requires B's separately mailed code. Add-back reuses the same server issuance helper and the same client key derivation path rather than creating a second credential mechanism.
- Room protocol v2 replaces the beacon-derived join secret. A CPace exchange creates a per-handshake session before SDP. Open Lobbies need no typed credential; private Lobbies use a fresh eight-character CPace password plus a rotating six-digit TOTP carried only inside the sealed offer. Privacy and accepting-new-people are independent.
- Pending host handshakes are bounded to 64 and expire after 90 seconds. Identical hellos are idempotent and reply caching is session-bound. Spokes remain receive-only for media and forbidden message families remain closed.
- AutoYou Computer state no longer blocks a Lobby. The computer connection is its own singleton; active calls and peer/Lobby roles are managed separately. The UI tells the user to end only the competing call, not disconnect their computer.
- Social is Lobby-first on Android and iOS: current hosting/join/discovery status, a large native map/proximity surface, one-tap Host Lobby, and defaults behind a gear. The map never requests GPS. It plots only mDNS/BLE-discovered Lobbies around a server-provided coarse IP region; those display offsets are not physical Lobby coordinates.
- `GET /v1/social/region` is paid-gated and returns only a 0.25-degree approximate center. The server reduces addresses to IPv4 `/24` or IPv6 `/48`, HMACs the network cache key, shares results/lookup locks through Redis across replicas, and uses a bounded process cache only as fallback. Android uses a build-injected, package/signature-restricted Google Maps key when configured and a native proximity fallback otherwise; iOS uses MapKit.
- Email reveal routes were added to AASA and Android intent filters for production and staging domains. No secret is moved into a URL query/path, analytics event, or server request by the fragment handoff.
- Debug-only Android/iOS launch hooks can open Social with an operator-supplied existing session for simulator checks. The supplied token is process-memory-only, and paid state is taken from `/v1/subscription/status`; the hook does not persist the token or accept a caller-provided entitlement tier.

Current deliberate limits:

- The proximity map shows only local mDNS/BLE Lobbies. Publishing contact-hosted Lobbies through the cloud is future work, not a shipped claim.
- A Lobby remains a local host/spoke star and is not the recursive Peer Relay chain.
- There is no continuous presence service. SSE is a foreground wake edge; suspended incoming-call reliability still depends on APNs/FCM and platform call integration.
- Android's real basemap requires `AUTOYOU_MAPS_API_KEY` at build time. The key must be restricted to the AutoYou package and signing certificates; it is never committed.
- Live SMTP setup was advanced on 2026-08-25 without a broad deployment: a dedicated SMTP2GO credential was created, `autoyou.me` was accepted as a verified sender domain, the six `/autoyou-core/prod/SMTP_*` parameters were updated with explicit TLS, and only the production `account` container was recreated. The container returned healthy, its in-container TLS/authentication probe passed, and the public health and auth-manifest routes returned HTTP 200. No message was sent, so inbox delivery and reveal-link behavior remain live-test items. The iPhone X was not listed by `devicectl`, and the Android Maps key was absent on this machine. The signed-in Apple Developer portal successfully registered `autoyou.me` as a private-relay email source and lists it as a Domain with SPF status.

Focused evidence for this delta:

- account-service peer/region unit suite: 28 passed;
- Android Room/link/map focused tests and debug Kotlin compilation: passed;
- iOS room parity: 81 checks;
- iOS peer-link/deep-link parity: 85 checks;
- iOS 16 Simulator source build with MapKit: passed;
- backend AASA/auth contract suite: 9 passed after the final branch replay;
- PocketBase peer-link contract suite: 32 passed after the final branch replay;
- `.llm` governance: 103 files OK;
- Graphify rebuilt on the clean rebased implementation commit: 38,418 nodes and 87,997 edges across 24 source scopes. Graphify reported partial-parser warnings for some Kotlin/Swift files, so graph edges remain navigation evidence rather than a compiler substitute.
- strict all-artifact legal gate: passed after recording `play-services-maps:20.0.0` under the Android SDK License vendor-review boundary and regenerating the Android NOTICE/SBOM.

The implementation is still intentionally unreleased. Publication of this branch means source handoff for cross-machine testing, not production deployment or an 80.7.0 store release.

## 1. Snapshot boundaries

- The branch was rebased onto `origin/main` at `a3cc4632e51de3f8c456052577918773170473f9`. The clean implementation checkpoint is `35cb38d5fb4558c580144c450535b838f70f4c84`; this documentation refresh follows it at the branch tip.
- Fully reviewed peer-relay checkpoint: `4141927ce` over base `91018ff62`.
- The linear rebased foundation begins at `7a2dac017`, `1ea117df7`, `5a35759ce`, and `7a74ef906`; exact duplicate patches from the old merge history were omitted during rebase.
- At implementation commit `35cb38d5f`, the branch is a 19-commit stack over `origin/main`, zero commits behind it, with 447 changed files, 60,309 insertions, and 268 deletions in the accumulated feature delta.
- Graphify is rebuilt from the final source after validation. The curated Brain remains historical context rather than implementation evidence; source, diff, migrations, and focused tests are authoritative here.
- No deployment or release is authorized by this document.
- No migration application or production deployment was performed.

Preserved stashes:

- `stash@{0}`: `codex-peer-relay-lobby-wip-before-remote-ff-2026-08-25`
- `stash@{1}`: `codex-peer-relay-room-wip-before-origin-main-rebase-2026-08-24`
- `stash@{2}`: `codex-peer-link-before-origin-main-replay-2026-08-24`
- `stash@{3}`: `codex-peer-link-checkpoint-before-origin-sync-2026-08-24`
- `stash@{4}`: earlier Keychain persistence work

### Room implementation integrated after the frozen review

The previously uncommitted Room delta is now integrated on Android and iOS. It uses native mDNS as the primary discovery/signaling route and a service-UUID-only BLE advertisement plus GATT beacon/offer/answer fallback. The two transports carry the same bounded encrypted handshake. Literal NUL source bytes were replaced by source escapes, the frame ceiling is 60 KiB to match its two-byte length, and malformed/overrunning frames reset cleanly.

Each Room is a Direct-mode-only star. One mobile host owns one WebRTC connection per spoke, orders and fans chat, and may broadcast one microphone/camera source. Spokes create receive-only media transceivers and cannot originate media, browser, remote desktop, or later-added message families. Direct-mode loss or paid-entitlement loss closes the room below the UI. Room ICE credentials are used only by the local WebRTC endpoint and are no longer copied into the signaling answer.

Room discovery runs only while the paid Social tab is visible and no Room is active. The implementation reuses the existing peer WebRTC factory, ICE resolver, identity, capture, renderer, and crypto envelope; it adds no dependency and never routes Room traffic through AutoYou Computer, the recursive Peer Relay path, or the cloud signaling service.

## 2. Latest product contract

AutoYou Social is a paid-user feature in the Android and iOS applications. A paid entitlement, including the lowest paid tier currently described as $1, is required for both sides of a Social relationship. Payment is useful abuse friction, but it is not proof of a unique or trustworthy human and must not replace authentication, rate limits, consent, or revocation.

The intended user operations are:

1. A signed-in paid user asks AutoYou Cloud for a short-lived one-time code.
2. The cloud sends the secret only to that user's verified OAuth-account email.
3. The app does not directly notify or email the other person.
4. The owner deliberately shares their verified email plus the code, secure link, or locally generated QR through an out-of-band channel such as the native share sheet or an in-person scan.
5. The recipient must already have an authenticated, paid AutoYou account.
6. Redeeming A's grant lets B add A. It must not silently create the reverse relationship.
7. For A to add B, B separately issues and shares B's grant. Each direction is explicit, independently revocable, and idempotent.
8. The resulting contact can expose explicitly authorized Android/iOS client devices and, where selected, that person's AutoYou Computer server.
9. Contact membership enables an invitation to chat or call. It never constitutes permission to open the microphone, camera, browser control, or remote desktop by itself.
10. The recipient willingly answers a call. Local microphone/camera capture begins only from the recipient's explicit answer/toggle choices.

### Personalized grant interpretation

The secure interpretation of "share with one or multiple people" is one grant per intended recipient, not one reusable group secret:

- The issuer may enter the intended recipient's exact email to bind the grant.
- The service normalizes and stores only the minimum needed matching representation.
- The email containing the secret still goes only to the issuer's verified account inbox.
- The issuer shares it out of band with that intended recipient.
- Redemption requires the currently authenticated paid account to match the intended recipient binding.
- Sharing with several people creates several independently expiring and revocable grants.

If the product later permits an unbound code, it is a bearer capability and materially weaker. That should be a separate, explicit product decision, not an accidental fallback.

### Link and QR contract

- Never put the secret in a normal URL query string or path.
- Prefer an app/universal-link payload whose secret is in the URL fragment, with a manual-copy fallback.
- Strip the fragment immediately after the app consumes it.
- Do not send it to analytics, crash reports, logs, clipboard history beyond the user's explicit copy action, or HTTP referrers.
- Generate QR content locally from the same canonical payload; do not create a second pairing mechanism.
- A QR is only an encoding of the same grant, not a separate credential.
- The generation API should not return plaintext merely because a caller has a valid account session. The verified mailbox is the intended second factor.
- Do not embed a static cloud API key in Android or iOS. Mobile binaries cannot protect one. Use the existing authenticated user session, device-held key material, and narrow server-issued capabilities.

The simplest secure UX compatible with the requested flow is: cloud emails the code or fragment link to the issuer; the signed-in app receives/pastes it; the app builds the share text and QR locally from one shared codec.

## 3. Architecture decision

The correct shape is not a fully serverless communications app. It is a thin cloud control plane plus direct encrypted data/media paths:

```text
Verified OAuth account + paid entitlement
                    |
                    v
      Cloud control plane (small messages only)
      - directional grants and contacts
      - device/server public-key directory
      - opaque offer/answer/ICE relay queue
      - push/SSE wake edges
      - rate limits, revocation, TURN credentials
                    |
                    v
        Direct WebRTC data/media when possible
                    |
              TURN only on fallback
```

The cloud is needed for authenticated rendezvous, paid-entitlement checks, revocation, mobile wake-up, NAT traversal metadata, and durable signaling. It should not carry call media, browser streams, chat history, or a continuous presence heartbeat.

No third-party pairing platform is required. SMTP2GO can remain the outbound SMTP provider behind the current standard-library mail path. AWS should host only the control-plane work that must be authoritative.

## 4. Identity and authorization model

The design has four identities that must not be conflated:

- Account/person: authenticated OAuth identity and paid entitlement.
- Contact edge: directional authorization from one account to another.
- Device: client installation with its own identifier and public key.
- Server: an AutoYou Computer target with separately scoped capabilities.

A contact is account-level social identity. Reachability and control are target-level grants.

Recommended minimum model:

- Adding a person creates one directional contact edge.
- The contact can reveal targets that the target owner explicitly publishes to that contact.
- Existing client devices and servers are selected independently.
- "Allow future servers" must be an explicit opt-in, visible and revocable.
- The safe meaning of that opt-in is future-server discovery or invitation eligibility, not automatic editor/admin access to every future server.
- Every server connection still receives a narrow role/capability grant.
- Blocking overrides all older invitations, contacts, pair grants, queued signaling, and future discovery.

UI restrictions are not authorization boundaries. Every routing hop and target must enforce the same rule server-side or inside the trusted host application.

## 5. Pairing and contact state machine

The intended state progression is:

```text
issuer requests personalized grant
  -> paid/session/email/recipient checks
  -> one live grant per issuer + recipient + selected key/scope
  -> secret delivered to issuer's verified mailbox
  -> issuer shares canonical payload out of band
  -> authenticated paid recipient redeems once
  -> directional contact edge exists (idempotently)
  -> recipient selects/accepts allowed targets and scopes
  -> reverse edge requires a separate reverse grant
```

Required invariants:

- Expiration is enforced transactionally at redemption.
- Successful consumption is single-use and atomic across replicas.
- A retry after a successful request returns the existing result without creating duplicate contacts.
- Changing the issuer key revokes or supersedes outstanding grants tied to the old key.
- Exact-email matching is normalized consistently and never exposed through a lookup endpoint.
- Unknown, unpaid, blocked, expired, already-used, and mismatched cases return non-enumerating public errors.
- Rate limits exist at account, source-network, target, and action levels.
- The server stores a keyed digest, not a recoverable OTP, except in the narrowly scoped outbound-mail outbox before delivery.
- The plaintext outbox value is scrubbed after successful delivery or terminal failure.
- Revocation and blocking invalidate already queued work, not only new enqueue attempts.

Current OTP parameters at the reviewed checkpoint were eight characters from a 32-symbol alphabet, approximately 40 bits, with a ten-minute lifetime. That is adequate only together with paid authentication, exact recipient binding, single-use consumption, Redis-backed distributed attempt limits, and generic errors.

## 6. Implemented control plane at the reviewed checkpoint

### PocketBase and account service

- Peer collections use admin-only PocketBase rules; public collection CRUD is disabled.
- Custom peer hook routes require PocketBase superuser authentication.
- Directional contact and invite records exist.
- Pair-code and pair-grant records exist.
- The relay queue supports transactional lease/ack behavior.
- Relay payloads are capped at 64 KiB.
- A target device is capped at 32 active queued items.
- Queue items have a ten-minute TTL and a twenty-attempt ceiling.
- Relay enqueue resolves authenticated paid source and target devices transactionally.
- Reply routing is bound to the original source, target, and invitation context.
- Email delivery uses an SMTP outbox with leasing and retry behavior suitable for several application replicas.
- SMTP credentials are refused unless a verified TLS connection is established.
- Redis `INCR` plus TTL provides distributed rate limiting.
- Production fails closed when the required Redis limiter is unavailable; in-memory limiting is local-development behavior only.
- Push/SSE events are wake hints; the durable queue remains the source of truth.

### Cryptographic ownership

- Client devices retain X25519 private keys.
- The cloud sees public shares, not device private keys.
- HKDF-derived material scopes peer conversations.
- A live grant can be reused idempotently for the same bound recipient/device/key tuple.
- Key rotation revokes the previous live code for that tuple.

### Directionality

Invite acceptance creates only inviter-to-recipient authorization. Reciprocity is a separate operation. This matches the latest requirement and must not regress into a mutual-contact row or automatic reverse grant.

## 7. Direct calls, relay sessions, and Rooms are different products

### Direct Social call

- One contact calls one selected device.
- Chat, voice, and video are bidirectional only after the recipient answers.
- Camera flip and local media toggles are client-native controls.
- WebRTC attempts direct connectivity and uses allowed STUN/TURN tiers as fallback.

### Server relay session

The clarified desired chain is:

```text
Person A server <- Client 1 <- Client 2 <- Client 3
```

At each step the downstream client talks only to its immediate trusted upstream. A downstream client must not receive the server's credential and must not pretend to be an upstream device.

For this to be safe and implementable:

- Every hop authenticates the immediate peer.
- A trusted router appends a server-derived, bounded relay path.
- A guest-supplied `relay_origin`, conversation owner, or relay path is never trusted.
- Relay paths have a fixed maximum hop count and cycle detection.
- Capabilities can only be reduced at each hop; a downstream peer can never regain a permission removed upstream.
- Browser/chat forwarding is separately permission-gated from audio/video viewing and capture.
- Media begins only in an accepted call/session, never on contact addition.
- Request IDs and conversation IDs include enough hop/peer context to avoid collisions.
- Ending or revoking an upstream session terminates all dependent downstream paths.
- The UI restriction preventing Client 2 from directly connecting elsewhere while relaying must be backed by router/session enforcement.

The reviewed implementation does not yet satisfy this recursive-chain contract. It deliberately rejects forged or second-hop relay-origin claims and derives a bounded relay path at trusted hosts. That is the safe starting point, but enabling real multi-hop requires an explicit trusted hop protocol rather than relaxing those checks.

### Room protocol foundation (rebased as `0cfb9059d`)

The committed Room foundation is a separate hub-and-spoke star:

- One host and N spokes.
- No spoke-to-spoke links.
- The host alone sequences and fans out chat.
- Message IDs deduplicate reflected frames.
- Host-only capture; spokes do not open microphone/camera.
- Browser and remote desktop are forbidden on Room links.
- Relay tier selection distinguishes owned servers, AutoYou STUN/community relay, and private TURN.
- BLE advertisements intentionally carry only anonymous protocol/occupancy/truncated identity metadata; names arrive after connection and acceptance.

This Room design can be valid as a broadcast/watch-room feature, but it is not the recursive Social contact relay just specified. In particular, host-only capture and no spoke-to-spoke links cannot provide a bidirectional contact call or `Client 1 <- Client 2 <- Client 3` forwarding. Do not merge these concepts merely because both use WebRTC.

## 8. Platform implementation status

### Android

Implemented or substantially present:

- Social screen and settings entry points.
- Contact/incoming surfaces.
- Pair request/redeem client plumbing.
- Peer offer/answer transport, guest session, hub, router, and upstream abstraction.
- Chat/call notification integration.
- Local encrypted key material.
- Direct guest media intended to remain closed until answer.
- Room protocol, broadcast router, and complete native Room runtime on the final branch tip.

Incomplete or mismatched:

- Pair generation now decodes an email-delivery-only response and tells the user to check email, but there is no completed issuer flow to import the mailed code/link and share the canonical email/code/QR payload out of band.
- Native mDNS/BLE discovery, encrypted signaling, host/spoke WebRTC sessions, paid/direct-mode gates, and Social UI are integrated.
- Native background incoming-call behavior is not proven.
- App DND is not Android system DND integration.
- Recursive relay is not implemented by the Room star.

### iOS

Implemented or substantially present:

- Social and settings surfaces.
- Contact/incoming surfaces.
- Peer link codec, transport, guest session, hub, router, and upstream abstraction.
- Local device-key persistence.
- Direct call accept/decline UI.
- Room protocol, broadcast router, and complete native Room runtime on the final branch tip.

Incomplete or mismatched:

- Pair generation likewise changed to email-delivery-only confirmation without the completed owner import/share/QR flow.
- Normal APNs/SSE behavior is not PushKit/CallKit. A suspended app cannot be assumed to display or answer a native incoming call without push entitlements and system call integration.
- SSE works only while the application is alive; it is not a background presence or ringing channel.
- App DND is not Apple Focus/DND integration.
- Recursive relay is not implemented by the Room star.

### Python and macOS

`clients/macos` currently packages the Python GUI rather than a separate mature native Social architecture.

Present:

- Python peer protocol, codec, replay ledger, guest, hub, router, and upstream modules.
- Manual authenticator and portions of cloud inbox handling.

Missing or incomplete:

- Full Social contact list and directional grant UX.
- Issuer code generation/import/share and QR parity.
- Durable pair-grant/contact presentation parity.
- Python SSE wake category coverage for `peer_pair` and `peer_call`; the reviewed path listened only for `peer_link`.

Ponytail decision: stabilize one protocol and cloud contract in Android/iOS first, then reuse it in the existing Python/macOS client. Do not create a second macOS Social architecture before the shared contract is settled.

## 9. Confirmed protections

The following suspected holes were checked and considered closed at the reviewed checkpoint:

- PocketBase peer collections and hooks are not anonymously writable.
- The pair code is not realistically brute-forced under the combined entropy, paid authentication, exact binding, TTL, single-use transaction, and distributed limits.
- The invitation web link keeps its capability in a fragment, uses a strict parser, removes it with `history.replaceState`, uses session storage only, and posts explicitly with no-store/no-referrer behavior.
- SMTP delivery verifies TLS and does not intentionally log secrets.
- The relay hook prevents a caller from choosing an arbitrary source device or reply target.
- Cloud media relay is not part of the design; WebRTC/TURN carries media.
- A previously suspected self-selected-capability escalation was not demonstrated because the host policy still intersects requested capabilities. This is not a finding, although new per-contact scopes must preserve that intersection consistently.

## 10. Unresolved findings frozen from the review

These are implementation findings, not permission to restart a broad security exercise. Address them in ordinary feature work with focused hermetic tests.

### P0: product flow is still incomplete

At `4141927ce`, Android and iOS required `email`, `code`, and `qr_payload` in the pair-code creation response while the server intentionally returned only delivery metadata. Generation therefore failed to decode.

The rebased `0cfb9059d` fixes the decode failure by making clients display an email-delivery confirmation. It does not complete the latest requested flow: the issuer still needs a safe way to import the emailed code/fragment into the signed-in app and share email + one-time code + link/QR out of band. Do not restore plaintext in the generation response. Complete the mailbox-to-share handoff instead, using one codec on each platform.

Mail copy also needs to describe Social sharing accurately; copy that says the owner should type the code into the device being added describes device pairing, not one person granting another person a directional contact.

### P0: recursive relay and server targets are not implemented

- Current cloud device discovery is client-oriented and rejects/non-selects server device types in the reviewed path.
- Contact addition does not yet express exact selected server targets or the separate future-server opt-in.
- The trusted relay routers reject second-hop asserted origins, while the new Room implementation is an N-spoke star with no spoke chaining.
- The requested recursive relay therefore cannot be claimed as working.

### P1: recipient block does not override an accepted relationship everywhere

Reviewed evidence at `4141927ce`:

- `autoyou-core/pocketbase/pb_hooks/lib/peer_link.js` checked the sender-to-target accepted edge for authorization but did not consistently consult a target-owned reverse block.
- The incoming-invite API supported repeated accept for an already accepted row, while block handling was limited to pending cases.
- Pair redemption, device discovery, inbox, and pair-grant polling did not all enforce the target's block.

Required fix: represent or update a recipient-owned blocked edge even after acceptance; consult it in enqueue, redeem, discovery, inbox, and grants; discard or suppress items queued before the block. One shared authorization predicate should own this rule.

### P1: peer traffic inherits the server owner's global remote role

Reviewed evidence at `4141927ce`:

- `core_server/webrtc_engine.py` routed peer HTTP/WebSocket requests through the globally configured remote-browser role.
- Agent frontend context headers could include the root owner's role and owner key.

Impact: if the owner configured editor/admin behavior, a cross-account contact could inherit broader local mutation rights and be attributed as the owner.

Required fix: derive a `peer:<contact/device>` principal with viewer-by-default or an explicit per-contact scope. Never forward the root owner key for peer-originated traffic.

### P1: cloud envelope identity is not bound to the decoded inner identity

The cloud queue authenticates a source device, but Android, iOS, and Python peer offer/answer payloads can carry their own inner `device_id`. Hubs and routers use that inner value for display, conversation identity, and relay context.

Required fix: cloud delivery passes the expected authenticated source device ID into the shared offer/answer decoder and rejects any mismatch before state is created. Manual out-of-band pairing may intentionally have no expected cloud identity, but must use a distinct entry point.

### P1: remote video state can silently upgrade local capture on answer

While a call is ringing, remote `video_state` handling can mutate the same state later used by Answer to decide whether to open the local camera. The displayed invite need not reflect that late mutation.

Required fix: remote video availability and the recipient's local capture choice are separate state variables. Only the user's answer/toggle may enable local camera capture.

### P1: iOS invitation decompression can allocate far beyond the protocol limit

The shared iOS inflater grows its output buffer repeatedly before the peer codec applies its post-decompression size check. A small authenticated compressed invitation can cause hundreds of MiB of allocation.

Required fix: give the shared inflater a caller-supplied `maxOutputBytes`, reject before every growth, and pass the peer invitation limit. Test with a tiny compressed bomb fixture that never allocates beyond the cap.

### P1: malformed Android JSON can escape the peer transport boundary

Top-level parsing is guarded, but callbacks and unchecked `jsonPrimitive` access can throw on valid JSON with hostile field types.

Required fix: one safe typed decoder at the data-channel boundary; catch a protocol violation, close the hostile connection, and leave the host process alive.

### P1: Android peer admission has a race and incomplete revocation

Concurrent cloud messages can pass the pending/link check before the suspending WebRTC setup records the link. Turning off incoming connections closes only links already inserted.

Required fix: reserve admission atomically, re-check `acceptIncoming` immediately before publish/insert, and close the in-flight connection when permission is revoked.

### P1/P2: peer request bodies are bounded too late

FastAPI can buffer and validate peer request models before handler authentication or later list slicing. That leaves a pre-handler memory/CPU denial-of-service path.

Required fix: enforce a small route-level request-body cap at Caddy for `/v1/peer/*`, plus Pydantic string/list bounds. The edge cap is the cheapest authoritative protection; do not add a global custom buffering framework.

### P2: replay-marker retention is shorter than accepted clock skew

The codecs accept through expiration plus clock skew, while replay ledgers prune at expiration. A replay can fit inside the skew-only gap.

Required fix: retain the marker through expiration plus the same skew window, with one focused boundary test.

### P2: iOS QR failure logging exposes the scanned secret

The scanner can print the raw QR payload when a legacy parse fails after the Social subscriber has already received it.

Required fix: log only a generic parse failure or a non-secret format identifier, never raw scanned data.

### P2: device discovery leaks presence-like timestamps

Contact device discovery returns `last_seen`/record update timing even though the product explicitly does not want presence.

Required fix: omit contact-owned activity timestamps. Return only the minimum target and capability metadata needed to initiate a connection.

### P2: polling is unnecessarily aggressive

The reviewed clients poll inbox and grants roughly every two seconds for up to ten minutes, creating about 600 requests per wake/device across the two endpoints.

Required fix: drain immediately, then use capped exponential backoff with jitter. Push/SSE is an edge-triggered hint and the durable queue is authoritative. Do not add continuous presence polling.

### P2: PocketBase migration parity and stale documentation

- The upgrade migration adding email-outbox fields did not add every index present in the fresh pair-code collection definition, including the email-outbox index.
- An early migration comment still described a mutual/two-row relationship while the implemented model is directional.

Required fix: one idempotent migration for missing indexes and a comment-only correction. Do not rewrite applied migrations.

### Hardening observations, not release blockers yet

- Android multiple-data-channel handling and raw-frame allocation deserve bounded protocol tests, but no demonstrated exploit was completed in the frozen review.
- Generic relay approval currently allows forwarding upstream audio/video according to relay policy. The clarified product wants server feed during an accepted relay session, so the capability should not be deleted; it must be gated by explicit call/session consent and capability attenuation.

## 11. Scale and cost assessment

### What scales horizontally now

- Account API replicas can remain stateless around PocketBase/Redis-backed authoritative state.
- Redis-backed rate limiting and wake fan-out work across replicas.
- Transactional relay leases and email-outbox leases avoid ordinary duplicate work across replicas.
- WebRTC keeps audio/video/browser bandwidth away from VM1 except TURN fallback.
- No presence service means no continuous per-contact heartbeat fan-out.

### Current bottleneck

PocketBase's embedded SQLite store is a single durable writer and a high-availability boundary. The surrounding Python service can fan out, but this persistence layer is not a truly multi-VM database architecture.

This is acceptable for a low-cost initial paid community if measured queue/write volume is small and backups/recovery are operational. When measured contention, latency, or availability requires it, move peer grants/contacts/queue/outbox to managed Postgres and a purpose-built queue. Do not build that migration speculatively now.

Outbox workers on every replica safely lease work but perform empty scans. Add an elected worker or queue only after empty-poll cost is measurable.

### Mobile wake reality

- SSE is cheap while an app is active.
- SSE does not keep suspended Android/iOS applications reliably alive.
- APNs/FCM is required to wake or notify a suspended app.
- Telecom-grade background ringing on iOS requires the appropriate PushKit/CallKit design and entitlement behavior; Android needs corresponding foreground/service/notification handling.
- Until those are implemented and live-tested, product copy must say notification/tap-to-open, not promise always-on phone-style ringing.

## 12. Test evidence and future validation policy

Historical focused evidence completed for the reviewed checkpoint, not rerun for this documentation update:

- PocketBase peer hook tests: 12/12.
- Focused account-service peer tests: 20 passed.
- Python peer/server identity tests: 68 passed.
- iOS peer parity harness: 68 checks.
- Android unit/compile track passed at the checkpoint.
- iOS arm64 Simulator build passed at the checkpoint.
- Clean PocketBase 0.36.6 integration covered locked rules, migrations, directional acceptance, paid checks, redeem/replay, plaintext scrubbing, grants, and relay leasing.
- Final validation records 29 Android Room tests, 68 iOS Room parity checks, an Android debug APK build, and an iOS Simulator build/install/launch.

The user will perform live validation. For subsequent code work:

- Use hermetic focused tests only unless explicitly told otherwise.
- Respect `AUTOYOU_TEST_ROOT` for every persistent store, keyring name, cache, registry, and config path.
- Use synthetic identities and credentials.
- Do not touch the operator's live AutoYou configuration.
- Add one minimal regression check per nontrivial shared rule.
- Do not spend time on E2E matrices or full platform builds unless requested.

## 13. Minimal implementation order for the next instruction

This is sequencing, not authorization to start:

1. Settle the canonical personalized grant payload and mailbox-to-native-share flow.
2. Reuse one codec per platform for manual entry, universal link, share text, and QR.
3. Add the shared recipient-block predicate and revocation behavior.
4. Bind cloud source identity to decoded peer identity at one shared boundary per client.
5. Separate remote media state from local capture consent.
6. Add exact person/device/server scope records, including an explicit future-server option.
7. Define a bounded, trusted multi-hop relay protocol; keep the Room star separate.
8. Replace fixed polling with drain plus capped backoff while retaining push/SSE wake hints.
9. Fill Python/macOS UX parity only after the shared contract is stable.

Ponytail constraints for that work:

- Reuse the existing pair codec, relay queue, outbox, WebRTC hubs, and platform share sheets.
- Add no new third-party pairing service, static mobile API key, presence subsystem, media proxy, or speculative database abstraction.
- Put each invariant at the narrowest shared trust boundary instead of patching every caller.
- Security checks, consent, data-loss prevention, and accessibility are not candidates for simplification.

## 14. Principal file map

Cloud and account control plane:

- `autoyou-core/pocketbase/pb_hooks/lib/peer_link.js`
- `autoyou-core/pocketbase/pb_hooks/peer_link.pb.js`
- `autoyou-core/pocketbase/pb_migrations/1787900000_created_peer_relay_queue.js` through `1787900006_updated_peer_pair_code_email.js`
- `autoyou-core/services/account-service/app.py`
- `autoyou-core/services/account-service/tests/test_peer_relay.py`

Full/Lite server routing:

- `core_server/webrtc_engine.py`
- `shared/datachannel_manager.py`
- `routers/cloud.py`
- `server.py`
- `autoyou_lite/autoyou_lite/server.py`

Android:

- `clients/android/app/src/main/java/com/autoyou/app/peer/`
- `clients/android/app/src/main/java/com/autoyou/app/cloud/CloudPairRepository.kt`
- `clients/android/app/src/main/java/com/autoyou/app/cloud/AutoYouCloudService.kt`
- `clients/android/app/src/main/java/com/autoyou/app/ui/SocialScreen.kt`
- `clients/android/app/src/main/java/com/autoyou/app/social/`

iOS:

- `clients/ios/AutoYouApp/Peer/`
- `clients/ios/AutoYouApp/Services/CloudPairManager.swift`
- `clients/ios/AutoYouApp/Services/AutoYouCloudService.swift`
- `clients/ios/AutoYouApp/Social/`

Python/macOS:

- `clients/python/peer_link/`
- `clients/python/autoyou_client.py`
- `clients/python/autoyou_client_gui.py`

## 15. Readiness judgment

The branch contains a substantial, coherent foundation: directional paid-account contacts, hardened PocketBase access, durable signaling and mail queues, distributed limiting, device-held keys, direct peer transports, Social UI foundations, and an initial Room protocol.

It is not ready to release as AutoYou Social. The highest-impact blockers are the incomplete out-of-band owner share flow, missing server/future-server target model, lack of the requested recursive trusted relay chain, recipient-block enforcement gaps, peer-to-owner privilege inheritance, cloud/inner identity mismatch, and media-consent bugs. Background ringing and macOS/Python parity also remain below the promised product experience.

The safest product slice is smaller: paid directional contact grants, explicit one-to-one direct calls/chat, client-generated share/QR from an emailed personalized code, no presence, direct WebRTC/TURN fallback, and narrow per-contact permissions. Recursive relay, Rooms, server targets, and system-style ringing should each become independently testable increments rather than one release flag.
