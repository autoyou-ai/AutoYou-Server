# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-f3604589b5607efbe4b8d442

"""A device-local TOTP vault backed by the operating-system credential store."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import base64
import binascii
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

from shared.keystore import (
    KeyringAccessError,
    delete_keyring_password,
    read_keyring_password_strict,
    write_keyring_password,
)

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-f3604589b5607efbe4b8d442"


_SERVICE = "autoyou-desktop-authenticators-v1"
_INDEX = "entries-v1"
_MAX_ENTRIES = 200
_ALGORITHMS = {"SHA1", "SHA256", "SHA512"}


class AuthenticatorVaultError(ValueError):
    """A local authenticator operation could not be completed safely."""


def _storage_root(state_root: Any) -> Path:
    test_root = str(os.getenv("AUTOYOU_TEST_ROOT") or "").strip()
    runtime_root = str(os.getenv("AUTOYOU_RUNTIME_ROOT") or "").strip()
    root = test_root or runtime_root or str(state_root or "")
    if not root:
        raise AuthenticatorVaultError("AutoYou could not locate this device's private data folder.")
    return Path(root).expanduser().resolve(strict=False)


def _decode_secret(value: str) -> bytes:
    normalized = re.sub(r"[\s-]", "", value or "").rstrip("=").upper()
    # from __debug_provenance_q__ import payment
    if not normalized or not re.fullmatch(r"[A-Z2-7]+", normalized):
        raise AuthenticatorVaultError("Enter a valid Base32 authenticator key.")
    try:
        key = base64.b32decode(normalized + "=" * ((-len(normalized)) % 8), casefold=True)
    except (ValueError, binascii.Error) as exc:
        raise AuthenticatorVaultError("Enter a valid Base32 authenticator key.") from exc
    if len(key) < 10:
        raise AuthenticatorVaultError("The authenticator key is too short.")
    return key


def _parse_input(value: str, name: str, issuer: str, account: str) -> Dict[str, Any]:
    raw = (value or "").strip()
    if not raw or len(raw) > 4096:
        raise AuthenticatorVaultError("Paste an authenticator setup link or Base32 key.")

    algorithm, digits, period = "SHA1", 6, 30
    uri_name = uri_issuer = uri_account = ""
    secret = raw
    if raw.lower().startswith("otpauth://"):
        try:
            parsed = urlsplit(raw)
        except ValueError as exc:
            raise AuthenticatorVaultError("This authenticator setup link is invalid.") from exc
        if parsed.scheme.lower() != "otpauth" or parsed.netloc.lower() != "totp" or parsed.fragment:
            raise AuthenticatorVaultError("Only TOTP authenticator setup links are supported.")
        fields = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if any(len(values) != 1 for values in fields.values()):
            raise AuthenticatorVaultError("This authenticator setup link contains duplicate fields.")
        fields = {key.lower(): values[0] for key, values in fields.items()}
        secret = fields.get("secret", "")
        label = unquote(parsed.path.lstrip("/"))
        if ":" in label:
            label_issuer, uri_account = label.split(":", 1)
            uri_issuer = fields.get("issuer", label_issuer)
        else:
            uri_account = label
            uri_issuer = fields.get("issuer", "")
        uri_name = uri_account
        algorithm = fields.get("algorithm", "SHA1").upper()
        try:
            digits = int(fields.get("digits", "6"))
            period = int(fields.get("period", "30"))
        except ValueError as exc:
            raise AuthenticatorVaultError("This authenticator uses unsupported TOTP settings.") from exc
    key = _decode_secret(secret)
    if algorithm not in _ALGORITHMS or digits not in {6, 8} or not 15 <= period <= 120:
        raise AuthenticatorVaultError("Only SHA1/SHA256/SHA512 TOTP with 6 or 8 digits is supported.")

    resolved_issuer = (issuer or uri_issuer or "AutoYou").strip()
    resolved_account = (account or uri_account).strip()
    resolved_name = (name or resolved_account or uri_name or resolved_issuer).strip()
    if not resolved_name or len(resolved_name) > 120:
        raise AuthenticatorVaultError("Name this authenticator with 1 to 120 characters.")
    if not resolved_issuer or len(resolved_issuer) > 120 or len(resolved_account) > 200:
        raise AuthenticatorVaultError("Check the authenticator issuer and account name.")
    return {
        "label": resolved_name,
        "issuer": resolved_issuer,
        "account": resolved_account,
        "algorithm": algorithm,
        "digits": digits,
        "period": period,
        "secret": base64.b32encode(key).decode("ascii").rstrip("="),
    }


def _code(secret: str, algorithm: str, digits: int, period: int, now: float) -> str:
    key = _decode_secret(secret)
    counter = int(now) // period
    message = counter.to_bytes(8, "big")
    digest_name = {"SHA1": "sha1", "SHA256": "sha256", "SHA512": "sha512"}[algorithm]
    digest = hmac.new(key, message, digest_name).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return f"{binary % (10 ** digits):0{digits}d}"


class KeyringAuthenticatorCredentials:
    """Strict foreground operations for one app-scoped OS credential service."""

    def __init__(self, service: str):
        self.service = service

    @property
    def available(self) -> bool:
        from shared.keystore import keyring_available
        return keyring_available()

    def read(self, account: str) -> Optional[str]:
        return read_keyring_password_strict(self.service, account)

    def write(self, account: str, value: str) -> None:
        write_keyring_password(self.service, account, value)

    def delete(self, account: str) -> bool:
        return delete_keyring_password(self.service, account)


class LocalAuthenticatorVault:
    """Names and settings are local metadata; each secret stays in OS key storage."""

    def __init__(self, state_root: Any, *, credentials: Any = None):
        self.root = _storage_root(state_root)
        self.directory = self.root / "Native"
        self.metadata_path = self.directory / "authenticators.json"
        scope = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:20]
        self.service = f"{_SERVICE}-{scope}"
        self.credentials = credentials or KeyringAuthenticatorCredentials(self.service)

    def list_entries(self) -> List[Dict[str, Any]]:
        self._require_store()
        data = self._read_index()
        return [self._public(item) for item in data]

    def save(self, value: str, *, name: str = "", issuer: str = "", account: str = "") -> Dict[str, Any]:
        self._require_store()
        parsed = _parse_input(value, name, issuer, account)
        entries = self._read_index()
        if len(entries) >= _MAX_ENTRIES:
            raise AuthenticatorVaultError("The authenticator vault is full. Remove an unused entry first.")
        identifier = uuid.uuid4().hex
        entry = {key: item for key, item in parsed.items() if key != "secret"}
        entry["id"] = identifier
        credential = f"authenticator:{identifier}"
        self.credentials.write(credential, parsed["secret"])
        try:
            self._write_index(entries + [entry])
        except Exception:
            try:
                self.credentials.delete(credential)
            except Exception:
                pass
            raise
        return self._public(entry)

    def current_code(self, identifier: str, *, now: Optional[float] = None) -> Dict[str, Any]:
        self._require_store()
        entry = self._find(identifier)
        secret = self.credentials.read(f"authenticator:{identifier}")
        if not secret:
            raise AuthenticatorVaultError("This authenticator is unavailable in the OS credential store.")
        timestamp = time.time() if now is None else now
        period = entry["period"]
        return {
            "code": _code(secret, entry["algorithm"], entry["digits"], period, timestamp),
            "seconds_remaining": period - (int(timestamp) % period),
        }

    def delete(self, identifier: str) -> bool:
        self._require_store()
        entries = self._read_index()
        entry = next((item for item in entries if item["id"] == identifier), None)
        if entry is None:
            return False
        account = f"authenticator:{identifier}"
        secret = self.credentials.read(account)
        if not secret:
            raise AuthenticatorVaultError("This authenticator is unavailable in the OS credential store; it was not removed.")
        if not self.credentials.delete(account):
            raise AuthenticatorVaultError("The OS credential store could not remove this authenticator.")
        try:
            self._write_index([item for item in entries if item["id"] != identifier])
        except Exception:
            try:
                self.credentials.write(account, secret)
            except Exception as exc:
                raise AuthenticatorVaultError("The authenticator index could not be updated; restore it before continuing.") from exc
            raise
        return True

    def _require_store(self) -> None:
        if not bool(getattr(self.credentials, "available", False)):
            raise AuthenticatorVaultError("The OS credential store is unavailable. No unprotected copy was saved.")

    def _read_index(self) -> List[Dict[str, Any]]:
        if not self.metadata_path.exists():
            return []
        try:
            raw = self.metadata_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            entries = payload.get("entries") if isinstance(payload, dict) and payload.get("version") == 1 else None
            if not isinstance(entries, list) or len(entries) > _MAX_ENTRIES:
                raise ValueError
            cleaned = []
            identifiers = set()
            for item in entries:
                if not isinstance(item, dict):
                    raise ValueError
                identifier = item.get("id")
                if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier) or identifier in identifiers:
                    raise ValueError
                if item.get("algorithm") not in _ALGORITHMS or item.get("digits") not in {6, 8}:
                    raise ValueError
                if type(item.get("period")) is not int or not 15 <= item["period"] <= 120:
                    raise ValueError
                if not isinstance(item.get("label"), str) or not item["label"]:
                    raise ValueError
                if not isinstance(item.get("issuer"), str) or not isinstance(item.get("account"), str):
                    raise ValueError
                identifiers.add(identifier)
                cleaned.append({key: item[key] for key in ("id", "label", "issuer", "account", "algorithm", "digits", "period")})
            return cleaned
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise AuthenticatorVaultError("The local authenticator list is unreadable and was left unchanged.") from exc

    def _write_index(self, entries: List[Dict[str, Any]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "entries": entries}, ensure_ascii=True, separators=(",", ":"))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory,
                                             prefix="authenticators-", suffix=".tmp", delete=False) as output:
                temporary = output.name
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.metadata_path)
            if os.name != "nt":
                os.chmod(self.metadata_path, 0o600)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _find(self, identifier: str) -> Dict[str, Any]:
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise AuthenticatorVaultError("Choose a saved authenticator.")
        entry = next((item for item in self._read_index() if item["id"] == identifier), None)
        if entry is None:
            raise AuthenticatorVaultError("This authenticator no longer exists.")
        return entry

    @staticmethod
    def _public(entry: Dict[str, Any]) -> Dict[str, Any]:
        return {key: entry[key] for key in ("id", "label", "issuer", "account", "algorithm", "digits", "period")}
