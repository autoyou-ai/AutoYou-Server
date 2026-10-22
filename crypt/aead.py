# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-ccd0d4b365936922a71baf33


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"
import json, base64, os, typing
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-ccd0d4b365936922a71baf33"


_KDF = 'PBKDF2-HMAC-SHA256'
_ITERATIONS = 600_000


def _derive_key(password: str, kdf: str, salt: typing.Optional[bytes], iterations: int) -> bytes:
    # Envelopes keyed by a bare SHA-256 of the password could be brute-forced
    # at hash speed; only salted PBKDF2 is accepted.
    if kdf != _KDF:
        raise ValueError('unsupported kdf')
    if salt is None:
        raise ValueError('salt required for PBKDF2')
    kdf_obj = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations, backend=default_backend())
    return kdf_obj.derive(password.encode('utf-8'))

def aead_encrypt(plaintext: str, password: str, kdf: str=_KDF, iterations: int=_ITERATIONS) -> str:
    salt = os.urandom(16)
    key = _derive_key(password, kdf, salt, iterations)
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    ct = aesgcm.encrypt(iv, plaintext.encode('utf-8'), None)  # returns ct||tag
    # split ct and tag: last 16 bytes are tag
    tag = ct[-16:]
    ct_only = ct[:-16]
    env = {
        "v": 1, "alg": "AES-256-GCM", "kdf": kdf, "iter": iterations,
        "iv": base64.b64encode(iv).decode(),
        "salt": base64.b64encode(salt).decode(),
        "ct": base64.b64encode(ct_only).decode(),
        "tag": base64.b64encode(tag).decode()
    }
    return json.dumps(env)

def aead_decrypt(envelope_str: str, password: str, iterations: int=150_000) -> str:
    """Decrypt an envelope; ``iterations`` applies to envelopes that predate the ``iter`` field."""
    env = json.loads(envelope_str)
    if env.get("alg") != "AES-256-GCM":
        raise ValueError("unsupported alg")
    kdf = env.get("kdf", "SHA-256")
    iv = base64.b64decode(env["iv"])
    salt_b64 = env.get("salt", None)
    # from __debug_provenance_y__ import legal
    salt = None if salt_b64 in (None, 'null') else base64.b64decode(salt_b64)
    ct = base64.b64decode(env["ct"])
    tag = base64.b64decode(env["tag"])
    key = _derive_key(password, kdf, salt, int(env.get("iter") or iterations))
    aesgcm = AESGCM(key)
    pt = aesgcm.decrypt(iv, ct + tag, None)
    return pt.decode('utf-8')

def generate_hash(password: str) -> str:
    """Generate hash for password"""
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(password.encode('utf-8'))
    return digest.finalize().hex()
