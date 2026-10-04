# AutoYou session core

This workspace is the new generation's transport implementation. The legacy
release's locks, builders and artifacts remain in their existing release lane.
This source is governed by the AutoYou Source-Available License.

## Application boundary

Python continues to own agents, providers, authorization, history, websites and
application policy. `core_server/session_dispatch.py` dispatches already
authenticated application messages for both adapters. Transport-scoped pairing,
room bridge and call/input operations receive the connection's captured identity
before legacy header aliasing. An Iroh application header cannot select an owner,
replace a transport alias or confer a role.

`SessionBusinessAdapter` attaches the channel only after the current session
authority agrees with its protected endpoint grant. Readiness follows the final
admission confirmation. State bootstrap uses the same business handlers as the
legacy adapter. Cleanup cancels only the captured adapter's tasks and releases
only its captured input lease. Delayed cleanup cannot erase a replacement channel.
The binary/media/input lanes require an explicitly attached application owner and
the corresponding grant scope. Declaring a native connection does not declare
those features available.

`shared/session_media.py` specifies host-owned source bindings for capture and
render adapters. A session media scope is insufficient: call, participant,
direction, target, source, application approval lease, expiry and media generation
must agree. The selected wire codecs are Opus/PCM16 at 48 kHz and H264/VP8. Platform
capture, DSP, jitter, render and call behavior remain the M6 implementation and
M10 qualification obligations.

## Endpoint and authorization ownership

`core_server/services.py` owns `IrohServerService` through startup/shutdown.
Concurrent startup/shutdown is serialized before publishing a service. Cancelled
native startup waits for its owned worker to shut down. `EndpointLease` prevents
two processes from using the same protected endpoint root. Default policy is
legacy; an Iroh policy requires unlocked configuration and an explicit network
policy. No tests use operator keys, configuration or registries.

Existing OTP, Local Pair and messaging/Cloud Pair proof owners issue enrollment
descriptors. A Ticket carries endpoint/address information. It grants no access.
Redemption is short lived, one use, and restricted to the designated endpoint's
possession of its Iroh key. Challenge and confirmation bind the Pair ALPN, both
endpoints, TLS exporter, capabilities and grant. The Pair connection can never
become an application connection, even after enrollment. A fresh Session ALPN
connection independently verifies protected pins and admission before activation.

Protected endpoint associations, owner/device/grant metadata, expiry, revocations
and connection counters survive restart. Loss or corruption fails closed. The
transport store contains authorization/recovery metadata and no transcript copy.
Account renewal, key rotation and endpoint lookup remain M8 work.

## Bounds and observations

AYIR v1 has a 36 byte big-endian header with lane, generation, logical stream,
per-stream sequence and length. Rust checks the header before allocating payload
storage and rechecks authorization before dispatch. One frame occupies one QUIC
unidirectional stream. Reliable lanes never use expiring scheduler deadlines,
which would create sequence gaps. Media has independent frame deadlines; complete
media loss/late-frame behavior is implemented and qualified in M6.

Native receive and send permits each bound owned bytes to 16 MiB. Queues, active
connections, handshakes, stream counts and retired-stream ranges are bounded.
Control/input/enrollment keep reserved capacity. Python's shared send budget is
also bounded and waits for native progress without copying into an unbounded
retry queue. Cancellation, close, generation change and revocation release or deny
waiting sends. A successful send is transport acceptance, not a durable application
receipt; M5 owns application delivery, recovery and transfer semantics.

`shared/session_events.py` supplies bounded local connection observations. Slow
observers lose old observations and read current owner state to resynchronize.
Events never authorize a session. Native diagnostics report selected direct/relay
mode, RTT and held/queued bytes without tickets, exporters, tokens or addresses.

## Qualification policy

Focused tests use the pinned, task-local Rust toolchain and offline Cargo graph:

```text
python scripts/iroh_toolchain.py --test-root .tmp/iroh-tests/rust-core -- test --locked --offline --manifest-path AutoYou-Server/transport-rust/Cargo.toml -p autoyou-session --test owned_host
```

Run from the root repository. The wrapper isolates Cargo, toolchain and native
test state. Cargo's intrinsic test library supports generated Python binding tests
with loopback-only endpoints, synthetic identities and test-scoped protected key
services. These qualify components and fake business dispatch, not application or
platform parity. Swift/Kotlin source generation is not native loading evidence.

M0–M9 run relevant hermetic selectors. The user's later checkpoint instruction
allows only the local server/Windows Connect checkpoint after each milestone pair.
Final candidate builds and the consolidated E2E acceptance remain M10 work.
