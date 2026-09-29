# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-ccd0d4b365936922a71baf33


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import json, base64, os, typing
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-ccd0d4b365936922a71baf33"


def _derive_key(password: str, kdf: str, salt: typing.Optional[bytes], iterations: int = 150_000) -> bytes:
    pw = password.encode('utf-8')
    if kdf == 'PBKDF2-HMAC-SHA256':
        if salt is None:
            raise ValueError('salt required for PBKDF2')
        kdf_obj = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations, backend=default_backend())
        return kdf_obj.derive(pw)
    elif kdf == 'SHA-256':
        digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
        digest.update(pw)
        return digest.finalize()
    else:
        raise ValueError('unsupported kdf')

def aead_encrypt(plaintext: str, password: str, kdf: str='SHA-256', iterations: int=150_000) -> str:
    salt = os.urandom(16) if kdf == 'PBKDF2-HMAC-SHA256' else None
    key = _derive_key(password, kdf, salt, iterations)
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    ct = aesgcm.encrypt(iv, plaintext.encode('utf-8'), None)  # returns ct||tag
    # split ct and tag: last 16 bytes are tag
    tag = ct[-16:]
    ct_only = ct[:-16]
    env = {
        "v": 1, "alg": "AES-256-GCM", "kdf": kdf,
        "iv": base64.b64encode(iv).decode(),
        "salt": base64.b64encode(salt).decode() if salt is not None else None,
        "ct": base64.b64encode(ct_only).decode(),
        "tag": base64.b64encode(tag).decode()
    }
    return json.dumps(env)

def aead_decrypt(envelope_str: str, password: str, iterations: int=150_000) -> str:
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
    key = _derive_key(password, kdf, salt, iterations)
    aesgcm = AESGCM(key)
    pt = aesgcm.decrypt(iv, ct + tag, None)
    return pt.decode('utf-8')

def generate_hash(password: str) -> str:
    """Generate hash for password"""
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(password.encode('utf-8'))
    return digest.finalize().hex()
