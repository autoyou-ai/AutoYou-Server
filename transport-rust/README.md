# AutoYou Iroh transport

This Rust workspace belongs to the experimental `codex/iroh-transport-parity`
branch of the private AutoYou repository. Its server is stored as ordinary files
in `AutoYou-Server-Iroh/`. The public `AutoYou-Server` repository and release
clients use the stable WebRTC implementation.

The workspace pins Iroh 1.3.0 and the Rust 1.99.0 toolchain. It contains:

- `autoyou-protocol`: bounded wire frames and protocol validation.
- `autoyou-application-core`: runtime admission, cache expiry, shared atomic IO and bounded protected-storage processing.
- `autoyou-session`: endpoint lifecycle, authenticated enrollment, session
  admission, stream ownership, protected authorization state, and canonical
  application identities, connection aliases, and shared callback/socket
  admission leases.
- `autoyou-client-bindings`: native interfaces used by Python, Swift, and Kotlin.

Python verifies application proofs and retains agents, chat history, websites,
and application policy. It delegates grant persistence and authorization,
canonical identities, and connection aliases to the owned SDK. A ticket locates
an endpoint; it does not authorize access.
Password, OTP, and account proofs must succeed before session admission.
Authentication failures must never select another transport.

This private backend requires a staged SDK, including when Legacy WebRTC is
selected as its transport policy. Source startup and package builds reject a
missing SDK; they do not retain a second Python identity implementation. The
native identity index is exposed through the same Python, Swift, and Kotlin
bindings. Python only converts its dynamic scalar inputs at that boundary and
runs ADK tasks after identity resolution.

The native endpoint and application callbacks use the same admission state.
Python keeps callback references through opaque leases; it does not maintain
another generation-floor or scope-policy table. Device floors retain their
remote-server and room namespaces. Revocation fences later generations below
its minimum epoch, and delayed retirement cannot erase a replacement lease.
Callbacks drain verified frames before retiring a normally closed connection;
explicit revocation, supersession and expiry remain immediate. Proof owners on
a shared endpoint revoke through their owned context IDs so a Lobby cannot
revoke another room or a remote server that uses the same device ID.

Protected client grants use schema 2, indexed by the remote endpoint key.
Device IDs belong to each remote server's namespace. A validated schema 1
store migrates in memory on its next transaction, retaining every grant,
revocation, Core association, and generation floor before atomic protected
persistence. Pairing another server requires a fresh verified proof; it cannot
replace the owner or device association of an existing endpoint.

Rebuild the native SDK when this codec changes, then restage the mobile package
and rebuild the app. Recompiling Swift or Kotlin against a cached native library
does not update the codec. Older Iroh SDKs cannot read schema 2 and must fail
closed; the public pairing wire protocol remains unchanged.

Run the isolated Rust component tests from the private repository root:

```text
python scripts/iroh_toolchain.py --test-root .tmp/iroh-tests/rust-core -- test --locked --offline --manifest-path AutoYou-Server-Iroh/transport-rust/Cargo.toml -p autoyou-session --test owned_host
```

The wrapper uses the pinned toolchain and isolates native test state. An offline
run requires the pinned dependencies to have been provisioned first.
Its explicit Cargo configuration also enables the pinned AES package's ARMv8
backend, with CPU detection and the portable fallback retained. Direct Cargo
commands must run from this workspace or pass `--config .cargo/config.toml`;
`--manifest-path` alone does not load a nested Cargo configuration. The SDK
builder uses this workspace and includes the configuration in its fingerprints.
Component tests do not establish application parity. Pairing between versions,
chat, file delivery, calls, network changes, and native loading still require
the corresponding application and device acceptance checks before a release.

This source is governed by the AutoYou Source-Available License in `../LICENSE`.
