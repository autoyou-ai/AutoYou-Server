# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-2d2030786345634238313737-ea8de390b39bd52f3b472b0a

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-2d2030786345634238313737-ea8de390b39bd52f3b472b0a"


import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


PAKE_REQUEST_PREFIX = "pake1:"
PAKE_RESPONSE_PREFIX = "pake1-answer:"
PAKE_KDF_ITERATIONS = 600_000
_SALT_LENGTH = 16
_NONCE_LENGTH = 12
_INFO_REQUEST = b"AutoYou pairing PAKE1 request"
_INFO_RESPONSE = b"AutoYou pairing PAKE1 response"


@dataclass(frozen=True)
class PakeClientSession:
    purpose: str
    private_key: x25519.X25519PrivateKey
    client_public_key: bytes
    salt: bytes
    password_key: bytes


@dataclass(frozen=True)
class PakeServerReplyContext:
    purpose: str
    client_public_key: bytes
    salt: bytes
    password_key: bytes
    totp_code: Optional[str] = None


def is_pake_request(value: str) -> bool:
    return str(value or "").strip().startswith(PAKE_REQUEST_PREFIX)


def is_pake_response(value: str) -> bool:
    return str(value or "").strip().startswith(PAKE_RESPONSE_PREFIX)


def build_pake_request(
    plaintext: str,
    password: str,
    *,
    purpose: str = "autopair",
    totp_code: Optional[str] = None,
) -> tuple[str, PakeClientSession]:
    normalized_totp = str(totp_code or "").strip()
    request_version = 2 if normalized_totp else 1
    protected_plaintext = plaintext
    if normalized_totp:
        # Keep the live authenticator code inside the authenticated ciphertext.
        # The server still verifies it online after decrypting with the pairing
        # password; relays only see an opaque envelope.
        protected_plaintext = _json_bytes(
            {"v": 1, "payload": plaintext, "totp": normalized_totp}
        ).decode("utf-8")
    salt = os.urandom(_SALT_LENGTH)
    password_key = _derive_password_key(password, salt)
    private_key = x25519.X25519PrivateKey.generate()
    client_public_key = _public_bytes(private_key.public_key())
    nonce = os.urandom(_NONCE_LENGTH)
    aad = _request_aad(purpose, salt, client_public_key)
    ciphertext = AESGCM(password_key).encrypt(nonce, protected_plaintext.encode("utf-8"), aad)
    payload = {
        "v": request_version,
        "purpose": purpose,
        "kdf": "pbkdf2-sha256",
        "iterations": PAKE_KDF_ITERATIONS,
        "salt": _b64e(salt),
        "client_pub": _b64e(client_public_key),
        "nonce": _b64e(nonce),
        "ciphertext": _b64e(ciphertext),
    }
    session = PakeClientSession(
        purpose=purpose,
        private_key=private_key,
        client_public_key=client_public_key,
        salt=salt,
        password_key=password_key,
    )
    return PAKE_REQUEST_PREFIX + _b64e(_json_bytes(payload)), session


def peek_pake_request_totp(envelope: str) -> Optional[str]:
    """Read a legacy v1 cleartext ``totp`` field, if present.

    New v2 requests seal the code inside the ciphertext and therefore return
    ``None`` here. This helper remains only for wire compatibility diagnostics.
    """
    try:
        payload = _decode_envelope(envelope, PAKE_REQUEST_PREFIX)
    except Exception:
        return None
    code = payload.get("totp")
    code = str(code).strip() if code else None
    return code or None


def decrypt_pake_request(envelope: str, password: str) -> tuple[str, PakeServerReplyContext]:
    payload = _decode_envelope(envelope, PAKE_REQUEST_PREFIX)
    request_version = int(payload.get("v") or 0)
    if request_version not in (1, 2):
        raise ValueError("unsupported PAKE request version")
    if int(payload.get("iterations") or 0) != PAKE_KDF_ITERATIONS:
        raise ValueError("unsupported PAKE request KDF")
    purpose = str(payload.get("purpose") or "autopair")
    salt = _b64d(str(payload.get("salt") or ""))
    client_public_key = _b64d(str(payload.get("client_pub") or ""))
    nonce = _b64d(str(payload.get("nonce") or ""))
    ciphertext = _b64d(str(payload.get("ciphertext") or ""))
    if len(salt) != _SALT_LENGTH or len(client_public_key) != 32 or len(nonce) != _NONCE_LENGTH:
        raise ValueError("invalid PAKE request lengths")
    password_key = _derive_password_key(password, salt)
    aad = _request_aad(purpose, salt, client_public_key)
    plaintext = AESGCM(password_key).decrypt(nonce, ciphertext, aad).decode("utf-8")
    totp_code = str(payload.get("totp") or "").strip() or None
    if request_version == 2:
        try:
            sealed = json.loads(plaintext)
        except Exception as exc:
            raise ValueError("invalid sealed PAKE request payload") from exc
        if not isinstance(sealed, dict) or int(sealed.get("v") or 0) != 1:
            raise ValueError("invalid sealed PAKE request payload")
        protected_payload = sealed.get("payload")
        if not isinstance(protected_payload, str):
            raise ValueError("invalid sealed PAKE request payload")
        plaintext = protected_payload
        totp_code = str(sealed.get("totp") or "").strip() or None
    context = PakeServerReplyContext(
        purpose=purpose,
        client_public_key=client_public_key,
        salt=salt,
        password_key=password_key,
        totp_code=totp_code,
    )
    return plaintext, context


def encrypt_pake_response(plaintext: str, context: PakeServerReplyContext) -> str:
    server_private = x25519.X25519PrivateKey.generate()
    server_public_key = _public_bytes(server_private.public_key())
    client_public = x25519.X25519PublicKey.from_public_bytes(context.client_public_key)
    session_key = _derive_session_key(
        server_private.exchange(client_public),
        purpose=context.purpose,
        salt=context.salt,
        password_key=context.password_key,
        client_public_key=context.client_public_key,
        server_public_key=server_public_key,
    )
    nonce = os.urandom(_NONCE_LENGTH)
    aad = _response_aad(context.purpose, context.salt, context.client_public_key, server_public_key)
    ciphertext = AESGCM(session_key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    payload = {
        "v": 1,
        "purpose": context.purpose,
        "server_pub": _b64e(server_public_key),
        "nonce": _b64e(nonce),
        "ciphertext": _b64e(ciphertext),
    }
    return PAKE_RESPONSE_PREFIX + _b64e(_json_bytes(payload))


def decrypt_pake_response(envelope: str, session: PakeClientSession) -> str:
    payload = _decode_envelope(envelope, PAKE_RESPONSE_PREFIX)
    if int(payload.get("v") or 0) != 1:
        raise ValueError("unsupported PAKE response version")
    purpose = str(payload.get("purpose") or "autopair")
    if purpose != session.purpose:
        raise ValueError("PAKE response purpose mismatch")
    server_public_key = _b64d(str(payload.get("server_pub") or ""))
    nonce = _b64d(str(payload.get("nonce") or ""))
    ciphertext = _b64d(str(payload.get("ciphertext") or ""))
    if len(server_public_key) != 32 or len(nonce) != _NONCE_LENGTH:
        raise ValueError("invalid PAKE response lengths")
    server_public = x25519.X25519PublicKey.from_public_bytes(server_public_key)
    session_key = _derive_session_key(
        session.private_key.exchange(server_public),
        purpose=purpose,
        salt=session.salt,
        password_key=session.password_key,
        client_public_key=session.client_public_key,
        server_public_key=server_public_key,
    )
    aad = _response_aad(purpose, session.salt, session.client_public_key, server_public_key)
    return AESGCM(session_key).decrypt(nonce, ciphertext, aad).decode("utf-8")


def _derive_password_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PAKE_KDF_ITERATIONS,
    )
    return kdf.derive(str(password).encode("utf-8"))


def _derive_session_key(
    shared_secret: bytes,
    *,
    purpose: str,
    salt: bytes,
    password_key: bytes,
    client_public_key: bytes,
    server_public_key: bytes,
) -> bytes:
    info = b"|".join(
        [
            _INFO_RESPONSE,
            purpose.encode("utf-8"),
            client_public_key,
            server_public_key,
            hashlib.sha256(password_key).digest(),
        ]
    )
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(shared_secret)


def _request_aad(purpose: str, salt: bytes, client_public_key: bytes) -> bytes:
    return b"|".join([_INFO_REQUEST, purpose.encode("utf-8"), salt, client_public_key])


def _response_aad(purpose: str, salt: bytes, client_public_key: bytes, server_public_key: bytes) -> bytes:
    return b"|".join([_INFO_RESPONSE, purpose.encode("utf-8"), salt, client_public_key, server_public_key])


def _public_bytes(public_key: x25519.X25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _decode_envelope(envelope: str, prefix: str) -> dict[str, Any]:
    text = str(envelope or "").strip()
    if not text.startswith(prefix):
        raise ValueError("invalid PAKE envelope prefix")
    payload = json.loads(_b64d(text[len(prefix):]).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid PAKE envelope payload")
    return payload


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    compact = str(value or "").strip().replace("\r", "").replace("\n", "")
    compact += "=" * ((-len(compact)) % 4)
    return base64.urlsafe_b64decode(compact.encode("ascii"))
