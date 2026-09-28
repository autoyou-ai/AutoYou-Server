# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""CPace-over-ristretto255 pairing envelope.

Replaces ``pairing_pake.py`` (the older ``pake1`` construction, which was
password-based *encryption*, not a formal PAKE - a captured envelope could be
tested against password guesses fully offline) with a real balanced PAKE.

CPace's offline-attack resistance comes from the protocol structure - an
eavesdropper who captures the whole transcript (``Ya``, ``Yb``, and every
following AEAD ciphertext) still can't test a password guess without solving
Diffie-Hellman on ristretto255, which is assumed hard regardless of whether
the guess is correct. That means no slow/memory-hard KDF is needed here
(unlike the 600k-iteration PBKDF2 the old envelope used purely to raise the
cost of offline guessing) - this is deliberately cheap on weak hardware.

Because neither side has a shared key until *both* Diffie-Hellman shares are
exchanged, this is a two-step handshake (`cpace1-hello` / `cpace1-hello-answer`)
followed by symmetric use of the resulting session key for every subsequent
pairing message (offer, answer, trickle-ICE candidates) via
``encrypt_cpace_message`` / ``decrypt_cpace_message``.

This is a CPace-*family* construction (draft-irtf-cfrg-cpace's design: a
password-derived group generator, one ristretto255 DH share per side, a
transcript-bound session key) rather than a byte-identical implementation of
a registered ciphersuite such as CPACE-RISTRETTO255-SHA512. Two deliberate
differences from the draft text: BLAKE2b is used throughout instead of
SHA-512 (equally strong, and it's the one hash primitive already needed
elsewhere via libsodium's crypto_generichash, so no second hash primitive has
to be ported to Kotlin/Swift), and the session-key transcript is a flat
length-prefixed concatenation of (sid, Ya, Yb, K, purpose, DSI) rather than
the draft's exact `lv_cat` grouping - it binds the same set of values, just
laid out differently. Neither difference changes the offline-resistance
property above, which comes from CDH-hardness on ristretto255, not from
matching a specific byte layout. The generator derivation *does* replicate
the draft's zero-padding defense (see `_HASH_BLOCK_SIZE` below) since that's
a real, cheap-to-add hardening measure, not just a naming detail.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from shared.native_libsodium import ensure_libsodium_loadable

ensure_libsodium_loadable()

import pysodium  # noqa: E402  (must follow ensure_libsodium_loadable())


CPACE_HELLO_PREFIX = "cpace1-hello:"
CPACE_HELLO_ANSWER_PREFIX = "cpace1-hello-answer:"
CPACE_MESSAGE_PREFIX = "cpace1:"

_SID_LENGTH = 16
_NONCE_LENGTH = pysodium.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES  # 24
_SESSION_KEY_LENGTH = pysodium.crypto_aead_xchacha20poly1305_ietf_KEYBYTES  # 32
_DSI = b"AutoYou-CPace-v1"
# BLAKE2b's compression block size (matches SHA-512's, which draft-irtf-cfrg-cpace
# targets). Padding the DSI+password fields out to a full block before the
# per-session purpose/sid fields follows the draft's generator_string()
# construction: it stops an attacker who's fixed a password guess from caching
# the hash midstate after block 1 and reusing it "for free" across every other
# (purpose, sid) pair - each new session forces a full re-hash from scratch.
_HASH_BLOCK_SIZE = 128


@dataclass(frozen=True)
class CPaceClientHandshake:
    """Client-side state kept between sending `/autopair_hello` and receiving the answer."""

    purpose: str
    sid: bytes
    private_scalar: bytes
    public_share: bytes


@dataclass(frozen=True)
class CPaceHelloMessage:
    """Parsed `/autopair_hello` request, before the server has verified anything."""

    purpose: str
    sid: bytes
    client_public_share: bytes
    totp_code: Optional[str]


@dataclass(frozen=True)
class CPaceSessionKey:
    """Shared symmetric session key, usable by either side to encrypt or decrypt
    any subsequent pairing message (offer, answer, trickle-ICE candidates)."""

    purpose: str
    sid: bytes
    session_key: bytes


def is_cpace_hello(value: Any) -> bool:
    return str(value or "").strip().startswith(CPACE_HELLO_PREFIX)


def is_cpace_hello_answer(value: Any) -> bool:
    return str(value or "").strip().startswith(CPACE_HELLO_ANSWER_PREFIX)


def is_cpace_message(value: Any) -> bool:
    return str(value or "").strip().startswith(CPACE_MESSAGE_PREFIX)


def build_cpace_hello(
    password: str,
    *,
    purpose: str = "autopair",
    totp_code: Optional[str] = None,
) -> tuple[str, CPaceClientHandshake]:
    """Client: start a new handshake. `totp_code` is sent in the clear - it is
    verified online by the server before any CPace processing happens, and is
    never mixed into key derivation, so it can't be offline-guessed."""
    sid = os.urandom(_SID_LENGTH)
    generator = _derive_generator(password, purpose, sid)
    private_scalar = pysodium.crypto_core_ristretto255_scalar_random()
    public_share = pysodium.crypto_scalarmult_ristretto255(private_scalar, generator)

    payload: dict[str, Any] = {
        "v": 1,
        "purpose": purpose,
        "sid": _b64e(sid),
        "ya": _b64e(public_share),
    }
    if totp_code:
        payload["totp"] = str(totp_code)

    handshake = CPaceClientHandshake(
        purpose=purpose,
        sid=sid,
        private_scalar=private_scalar,
        public_share=public_share,
    )
    return CPACE_HELLO_PREFIX + _b64e(_json_bytes(payload)), handshake


def parse_cpace_hello(envelope: str) -> CPaceHelloMessage:
    """Server: parse an incoming `/autopair_hello`. Nothing here is secret or
    password-dependent - safe to parse before any authentication check."""
    payload = _decode_envelope(envelope, CPACE_HELLO_PREFIX)
    if int(payload.get("v") or 0) != 1:
        raise ValueError("unsupported CPace hello version")
    purpose = str(payload.get("purpose") or "autopair")
    sid = _b64d(str(payload.get("sid") or ""))
    client_public_share = _b64d(str(payload.get("ya") or ""))
    if len(sid) != _SID_LENGTH or len(client_public_share) != pysodium.crypto_core_ristretto255_BYTES:
        raise ValueError("invalid CPace hello lengths")
    totp_code = payload.get("totp")
    totp_code = str(totp_code).strip() if totp_code else None
    return CPaceHelloMessage(
        purpose=purpose,
        sid=sid,
        client_public_share=client_public_share,
        totp_code=totp_code,
    )


def build_cpace_hello_answer(hello: CPaceHelloMessage, password: str) -> tuple[str, CPaceSessionKey]:
    """Server: complete the handshake after any online gating (e.g. TOTP) has
    already passed. Raises ValueError if the client's share is degenerate."""
    generator = _derive_generator(password, hello.purpose, hello.sid)
    _reject_identity_element(hello.client_public_share)
    private_scalar = pysodium.crypto_core_ristretto255_scalar_random()
    public_share = pysodium.crypto_scalarmult_ristretto255(private_scalar, generator)
    shared_point = pysodium.crypto_scalarmult_ristretto255(private_scalar, hello.client_public_share)
    session_key = _derive_session_key(
        hello.sid, hello.client_public_share, public_share, shared_point, hello.purpose
    )
    payload = {
        "v": 1,
        "purpose": hello.purpose,
        "yb": _b64e(public_share),
    }
    session = CPaceSessionKey(purpose=hello.purpose, sid=hello.sid, session_key=session_key)
    return CPACE_HELLO_ANSWER_PREFIX + _b64e(_json_bytes(payload)), session


def build_cpace_hello_answer_with_payload(
    hello: CPaceHelloMessage, password: str, embedded_plaintext: str
) -> str:
    """Server: complete the handshake and fold an already-encrypted response
    into the same hello-answer message.

    Used by pairing modes (like OTP Pair's A-Tier) where only the server has
    real content to send back - since the server can compute the full session
    key the instant it receives the client's hello, there's no need for a
    separate third/fourth message the way autopair's offer/answer needs one.
    The session key is used once, here, and discarded - never remembered in
    the shared session cache, since nothing will reference it again.
    """
    answer_env, session = build_cpace_hello_answer(hello, password)
    payload = json.loads(_b64d(answer_env[len(CPACE_HELLO_ANSWER_PREFIX):]).decode("utf-8"))
    payload["payload"] = encrypt_cpace_message(embedded_plaintext, session)
    return CPACE_HELLO_ANSWER_PREFIX + _b64e(_json_bytes(payload))


def parse_cpace_hello_answer_payload(envelope: str, handshake: CPaceClientHandshake) -> str:
    """Client: finish the handshake and decrypt the embedded payload in one
    step, for hello-answers built with build_cpace_hello_answer_with_payload."""
    payload = _decode_envelope(envelope, CPACE_HELLO_ANSWER_PREFIX)
    embedded = payload.get("payload")
    if not embedded:
        raise ValueError("CPace hello-answer has no embedded payload")
    session = complete_cpace_handshake(envelope, handshake)
    return decrypt_cpace_message(str(embedded), session)


def complete_cpace_handshake(envelope: str, handshake: CPaceClientHandshake) -> CPaceSessionKey:
    """Client: finish the handshake after receiving `/autopair_hello_answer`."""
    payload = _decode_envelope(envelope, CPACE_HELLO_ANSWER_PREFIX)
    if int(payload.get("v") or 0) != 1:
        raise ValueError("unsupported CPace hello-answer version")
    purpose = str(payload.get("purpose") or "autopair")
    if purpose != handshake.purpose:
        raise ValueError("CPace purpose mismatch")
    server_public_share = _b64d(str(payload.get("yb") or ""))
    if len(server_public_share) != pysodium.crypto_core_ristretto255_BYTES:
        raise ValueError("invalid CPace hello-answer lengths")
    _reject_identity_element(server_public_share)
    shared_point = pysodium.crypto_scalarmult_ristretto255(handshake.private_scalar, server_public_share)
    session_key = _derive_session_key(
        handshake.sid, handshake.public_share, server_public_share, shared_point, purpose
    )
    return CPaceSessionKey(purpose=purpose, sid=handshake.sid, session_key=session_key)


def encrypt_cpace_message(plaintext: str, session: CPaceSessionKey) -> str:
    """Either side: encrypt any pairing message (offer/answer/candidates) once
    the session key is established. Safe to call repeatedly - each call uses a
    fresh random 24-byte XChaCha20 nonce, which has no meaningful collision
    risk at pairing-session message volumes."""
    nonce = os.urandom(_NONCE_LENGTH)
    aad = _message_aad(session)
    ciphertext = pysodium.crypto_aead_xchacha20poly1305_ietf_encrypt(
        plaintext.encode("utf-8"), aad, nonce, session.session_key
    )
    payload = {
        "v": 1,
        "purpose": session.purpose,
        "nonce": _b64e(nonce),
        "ciphertext": _b64e(ciphertext),
    }
    return CPACE_MESSAGE_PREFIX + _b64e(_json_bytes(payload))


def decrypt_cpace_message(envelope: str, session: CPaceSessionKey) -> str:
    payload = _decode_envelope(envelope, CPACE_MESSAGE_PREFIX)
    if int(payload.get("v") or 0) != 1:
        raise ValueError("unsupported CPace message version")
    purpose = str(payload.get("purpose") or "autopair")
    if purpose != session.purpose:
        raise ValueError("CPace message purpose mismatch")
    nonce = _b64d(str(payload.get("nonce") or ""))
    ciphertext = _b64d(str(payload.get("ciphertext") or ""))
    if len(nonce) != _NONCE_LENGTH:
        raise ValueError("invalid CPace message nonce length")
    aad = _message_aad(session)
    plaintext = pysodium.crypto_aead_xchacha20poly1305_ietf_decrypt(ciphertext, aad, nonce, session.session_key)
    return plaintext.decode("utf-8")


def _derive_generator(password: str, purpose: str, sid: bytes) -> bytes:
    dsi_field = _len_prefixed(_DSI)
    password_field = _len_prefixed(password.encode("utf-8"))
    zero_pad = b"\x00" * ((-(len(dsi_field) + len(password_field))) % _HASH_BLOCK_SIZE)
    generator_input = (
        dsi_field
        + password_field
        + zero_pad
        + _len_prefixed(purpose.encode("utf-8"))
        + _len_prefixed(sid)
    )
    wide_hash = pysodium.crypto_generichash(generator_input, outlen=pysodium.crypto_core_ristretto255_HASHBYTES)
    return pysodium.crypto_core_ristretto255_from_hash(wide_hash)


def _derive_session_key(
    sid: bytes,
    client_public_share: bytes,
    server_public_share: bytes,
    shared_point: bytes,
    purpose: str,
) -> bytes:
    transcript = (
        _len_prefixed(sid)
        + _len_prefixed(client_public_share)
        + _len_prefixed(server_public_share)
        + _len_prefixed(shared_point)
        + _len_prefixed(purpose.encode("utf-8"))
        + _len_prefixed(_DSI)
    )
    return pysodium.crypto_generichash(transcript, outlen=_SESSION_KEY_LENGTH)


def _message_aad(session: CPaceSessionKey) -> bytes:
    return _len_prefixed(session.sid) + _len_prefixed(session.purpose.encode("utf-8")) + _len_prefixed(_DSI)


def _reject_identity_element(point: bytes) -> None:
    # A malicious peer sending the ristretto255 identity element as their
    # share would force the shared point to also be the identity, regardless
    # of either side's password - a classic small-subgroup-style trick.
    if point == b"\x00" * pysodium.crypto_core_ristretto255_BYTES:
        raise ValueError("invalid CPace share (identity element)")


def _len_prefixed(value: bytes) -> bytes:
    if len(value) > 0xFFFF:
        raise ValueError("value too long for length-prefixing")
    return len(value).to_bytes(2, "big") + value


def _decode_envelope(envelope: str, prefix: str) -> dict[str, Any]:
    text = str(envelope or "").strip()
    if not text.startswith(prefix):
        raise ValueError("invalid CPace envelope prefix")
    payload = json.loads(_b64d(text[len(prefix):]).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid CPace envelope payload")
    return payload


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    compact = str(value or "").strip().replace("\r", "").replace("\n", "")
    compact += "=" * ((-len(compact)) % 4)
    return base64.urlsafe_b64decode(compact.encode("ascii"))
