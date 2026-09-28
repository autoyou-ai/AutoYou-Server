# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-726c79207375627461736b20-9714e28617acd2704e3e7805

"""Unlock metadata and internal-worker authentication."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-726c79207375627461736b20-9714e28617acd2704e3e7805"


import datetime
import hashlib
import os
import secrets
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Tuple

from fastapi import Request

_runtime: ModuleType


def bind_runtime(module: ModuleType) -> None:
    global _runtime
    _runtime = module


def _get_ai_agent_internal_api_token_path() -> Path:
    token_root = _runtime.get_user_data_dir("AutoYou") / ".autoyou"
    token_root.mkdir(parents=True, exist_ok=True)
    return token_root / "ai_agent_internal_api_token.txt"


def _load_or_create_ai_agent_internal_api_token() -> str:
    env_token = str(os.getenv(_runtime.AI_AGENT_INTERNAL_API_TOKEN_ENV, "") or "").strip()
    if env_token:
        return env_token

    token_path = _runtime._get_ai_agent_internal_api_token_path()
    token = ""
    try:
        if token_path.is_file():
            token = _runtime.read_secure_file(token_path).decode("utf-8").strip()
    except Exception as exc:
        _runtime.LOGGER.warning(
            "Failed to read AI agent internal API token from %s: %s; recreating token",
            token_path,
            exc,
        )

    if not token:
        token = secrets.token_hex(32)
        try:
            _runtime.write_secure_file(token_path, token.encode("utf-8"))
        except Exception as exc:
            _runtime.LOGGER.warning("Failed to persist AI agent internal API token to %s: %s", token_path, exc)

    os.environ[_runtime.AI_AGENT_INTERNAL_API_TOKEN_ENV] = token
    return token


def _request_uses_ai_agent_internal_token(request: Request) -> bool:
    client_host = getattr(getattr(request, "client", None), "host", None)
    if not _runtime._is_loopback_client_host(client_host):
        return False
    auth_header = str(request.headers.get("Authorization", "") or "").strip()
    if not auth_header.lower().startswith("bearer "):
        return False
    provided_token = auth_header[7:].strip()
    expected_token = _runtime._load_or_create_ai_agent_internal_api_token()
    if not provided_token or not expected_token:
        return False
    try:
        return secrets.compare_digest(provided_token, expected_token)
    except Exception:
        return provided_token == expected_token


def _get_unlock_file_path() -> Path:
    config_dir = _runtime._CONFIG_DIR / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "server_unlock.json"


def _load_unlock_metadata() -> Dict[str, Any]:
    path = _runtime._get_unlock_file_path()
    if not path.exists():
        return {"terms_accepted": False, "license_type": None, "agreement_version": None}

    # A locked recovery session can retain a sealed unlock record from a
    # previous Maximus session.  It must not repeatedly try to open that
    # record while its existing credential boundary is unavailable: status
    # polling should stay a local header check, not a protected-storage read.
    if _runtime._get_unlock_state() != "Ready" and not _runtime.secure_storage_enabled():
        try:
            with path.open("rb") as handle:
                if handle.read(len(_runtime.SPM_FILE_HEADER)) == _runtime.SPM_FILE_HEADER:
                    return {"terms_accepted": False, "license_type": None, "agreement_version": None}
        except OSError:
            pass

    try:
        payload = _runtime.load_secure_json(path, default={})
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        _runtime.LOGGER.warning("Failed to load unlock metadata from %s: %s", path, exc)
        return {"terms_accepted": False, "license_type": None, "agreement_version": None}


def _agreement_metadata_is_current(meta: Dict[str, Any]) -> bool:
    return bool(meta.get("terms_accepted")) and str(meta.get("agreement_version") or "") == _runtime.CURRENT_AGREEMENT_VERSION


# scrypt cost parameters.
#
# CURRENT matches the OWASP recommendation (N=2^17, r=8, p=1). LEGACY is the
# N=2^14 parameter set earlier releases wrote; it stays verifiable so existing
# installs keep working, and any password that validates under it is rehashed
# to CURRENT on the next successful unlock (see _sync_unlock_metadata).
#
# N=2^17 with r=8 needs ~128 MiB per derivation, which is the point - it is
# what makes offline cracking expensive. It also means each *online* attempt
# costs the host that memory, so the auth rate limiters in core_server.state
# are load-bearing here, not just abuse hygiene.
SCRYPT_CURRENT = {"n": 1 << 17, "r": 8, "p": 1}
SCRYPT_LEGACY = {"n": 1 << 14, "r": 8, "p": 1}

#: Explicit ceiling; OpenSSL's default (32 MiB) rejects the CURRENT parameters.
SCRYPT_MAXMEM = 384 * 1024 * 1024


def _scrypt(password: str, salt: bytes, params: Dict[str, int]) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=int(params["n"]),
        r=int(params["r"]),
        p=int(params["p"]),
        maxmem=SCRYPT_MAXMEM,
    )


def _hash_password(password: str) -> Tuple[str, str]:
    salt = os.urandom(16)
    return salt.hex(), _scrypt(password, salt, SCRYPT_CURRENT).hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """Verify against the current parameters, falling back to the legacy set."""
    return _verify_password_with_params(password, salt_hex, hash_hex)[0]


def _verify_password_with_params(
    password: str, salt_hex: str, hash_hex: str
) -> Tuple[bool, bool]:
    """Return ``(is_valid, needs_rehash)``.

    ``needs_rehash`` is True when the password only validated under the legacy
    cost parameters, which is the signal to re-derive and persist at CURRENT.
    """
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except Exception:
        return False, False

    try:
        if secrets.compare_digest(_scrypt(password, salt, SCRYPT_CURRENT), expected):
            return True, False
    except Exception as exc:
        _runtime.LOGGER.warning("scrypt verification at current parameters failed: %s", exc)

    try:
        if secrets.compare_digest(_scrypt(password, salt, SCRYPT_LEGACY), expected):
            return True, True
    except Exception:
        return False, False

    return False, False


def _safe_write_unlock_json(path: Path, data: Dict[str, Any]) -> None:
    _runtime.save_secure_json(path, data)


def _sync_unlock_metadata(password: str, *, accepted_by: str) -> Dict[str, Any]:
    meta = _runtime._load_unlock_metadata()
    changed = False
    salt_hex = meta.get("salt_hex")
    hash_hex = meta.get("hash_hex")
    if not salt_hex or not hash_hex:
        meta["salt_hex"], meta["hash_hex"] = _runtime._hash_password(password)
        changed = True
    else:
        is_valid, needs_rehash = _runtime._verify_password_with_params(
            password, salt_hex, hash_hex
        )
        if not is_valid or needs_rehash:
            # Either the password changed, or it validated only under the
            # legacy cost parameters - both cases re-derive at the current
            # parameters so the stored record is never left behind.
            if needs_rehash and is_valid:
                _runtime.LOGGER.info(
                    "Upgrading stored unlock password hash to current scrypt parameters."
                )
            meta["salt_hex"], meta["hash_hex"] = _runtime._hash_password(password)
            changed = True

    if not _runtime._agreement_metadata_is_current(meta):
        meta["terms_accepted"] = True
        meta["license_type"] = meta.get("license_type") or "individual"
        meta["agreement_version"] = _runtime.CURRENT_AGREEMENT_VERSION
        meta["accepted_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        changed = True

    if changed:
        _runtime._safe_write_unlock_json(_runtime._get_unlock_file_path(), meta)
    if changed or not _runtime.is_license_acknowledged(anchor=_runtime.__file__):
        _runtime.record_license_acknowledgement(anchor=_runtime.__file__, accepted_by=accepted_by)
    return meta
