# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-59ee9ef9f3ab623939d82530

"""Per-agent secondary 2FA security profiles (derive-from-password, verify-only).

Re-introduces multiple TOTP secrets - the legacy ``security.totp_clients`` concept
that was collapsed to a single shared ``security.totp_secret`` - as named
*security profiles* the admin can create and assign to individual agent websites
(for example a persona/goal agent that stores self-data).

Secret model (chosen): **derive-from-password - no secret is ever stored.**

- Each profile stores only a non-secret random ``salt``. The TOTP secret is
  derived on demand as ``HKDF-SHA256(server_password, salt)`` (base32-encoded for
  authenticator apps). Nothing reversible to the secret is persisted - not in the
  JSON config, not in this DB. This is the literal answer to "don't save the
  secret but still authenticate": we recompute it at verify time and discard it.
- **Write-once / verify-only.** The provisioning URI + manual-entry string are
  returned exactly once, at creation, for QR enrolment. There is no "Show secret"
  API afterward.
- **Recovery is by wipe.** Lost authenticator -> wipe the profile/agent and
  re-enrol. Changing the server password also rotates every derived secret (by
  design), which forces clean re-enrolment.

Caveat (documented for the security audit): because the secret derives from the
server password, this is *not* a fully independent second factor - a leaked
password can reproduce the codes. That matches the localhost / self-authorized
threat model. The independent-key option lives at the *data* layer
(``shared/secure_data_store``), where persona self-data can be encrypted with an
OS-keystore key separate from the login password.

This module is storage + verification only; HTTP endpoints, the admin UI, and the
per-agent-website enforcement gate wire into it separately.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import base64
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-59ee9ef9f3ab623939d82530"


try:  # pyotp is optional at import time; required only for create/verify.
    import pyotp  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    pyotp = None  # type: ignore

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: HKDF context string - bump the suffix to force re-enrolment on a scheme change.
_HKDF_INFO = b"autoyou-agent-2fa-totp-v1"
#: 20 bytes -> a 32-char base32 TOTP secret, the standard authenticator length.
_TOTP_KEY_BYTES = 20
DEFAULT_ISSUER = "AutoYou"


class SecurityProfileError(RuntimeError):
    """Raised for invalid security-profile operations."""


def _derive_totp_secret(password: str, salt: bytes) -> str:
    """Derive a stable base32 TOTP secret from (password, salt) via HKDF-SHA256."""
    if not password:
        raise SecurityProfileError("a derivation password is required")
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=_TOTP_KEY_BYTES,
        salt=salt,
        info=_HKDF_INFO,
    ).derive(password.encode("utf-8"))
    # base32 without padding is what pyotp / authenticator apps expect.
    return base64.b32encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class SecurityProfile:
    profile_id: str
    label: str
    issuer: str
    created_at: float
    last_verified_at: Optional[float]

    def to_metadata(self, assigned_agents: List[str]) -> Dict[str, Any]:
        """Public metadata only - never includes the secret, salt, or URI."""
        return {
            "profile_id": self.profile_id,
            "label": self.label,
            "issuer": self.issuer,
            "created_at": self.created_at,
            "last_verified_at": self.last_verified_at,
            "assigned_agents": list(assigned_agents),
            "has_secret": True,
            "secret_visible": False,  # by design - no "Show secret"
            "secret_stored": False,   # derive-from-password - nothing persisted
        }


class AgentSecurityProfileStore:
    """SQLite-backed store of derive-from-password, verify-only TOTP profiles.

    ``password`` is the secret-derivation input (the unlocked server password). It
    is held only in memory for the store's lifetime and never written to disk.
    """

    def __init__(self, db_path: Any, *, password: str) -> None:
        if not password:
            raise SecurityProfileError("password is required")
        self._db_path = str(db_path)
        self._password = password
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_profiles (
                    profile_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    issuer TEXT NOT NULL,
                    salt_b64 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_verified_at REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_profile_assignments (
                    agent_name TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    assigned_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _salt_for(self, profile_id: str) -> Optional[bytes]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT salt_b64 FROM security_profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return base64.b64decode(row["salt_b64"].encode("ascii"))
        except Exception:
            return None

    # ── profile lifecycle ─────────────────────────────────────────────────────

    def create_profile(
        self,
        label: str,
        *,
        issuer: str = DEFAULT_ISSUER,
    ) -> Dict[str, Any]:
        """Create a profile and return enrolment data ONCE.

        The returned ``provisioning_uri`` / ``manual_entry_secret`` are the only
        time the (derived) secret is ever exposed; there is no API to read it back.
        """
        if pyotp is None:
            raise SecurityProfileError("pyotp is not installed; cannot create TOTP profiles")
        label = (label or "").strip()
        if not label:
            raise SecurityProfileError("label is required")
        issuer = (issuer or DEFAULT_ISSUER).strip() or DEFAULT_ISSUER
        salt = os.urandom(16)
        secret = _derive_totp_secret(self._password, salt)
        profile_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO security_profiles "
                "(profile_id, label, issuer, salt_b64, created_at, last_verified_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (profile_id, label, issuer, base64.b64encode(salt).decode("ascii"), now),
            )
            conn.commit()
        provisioning_uri = pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name=issuer)
        # from __debug_provenance_p__ import submit
        return {
            "profile_id": profile_id,
            "label": label,
            "issuer": issuer,
            "created_at": now,
            "provisioning_uri": provisioning_uri,
            "manual_entry_secret": secret,  # shown once during enrolment only
        }

    def list_profiles(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM security_profiles ORDER BY created_at"
            ).fetchall()
            assigns = conn.execute(
                "SELECT agent_name, profile_id FROM agent_profile_assignments"
            ).fetchall()
        by_profile: Dict[str, List[str]] = {}
        for a in assigns:
            by_profile.setdefault(a["profile_id"], []).append(a["agent_name"])
        return [
            SecurityProfile(
                r["profile_id"], r["label"], r["issuer"], r["created_at"], r["last_verified_at"]
            ).to_metadata(by_profile.get(r["profile_id"], []))
            for r in rows
        ]

    def profile_exists(self, profile_id: str) -> bool:
        with self._connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM security_profiles WHERE profile_id=?", (profile_id,)
                ).fetchone()
                is not None
            )

    def verify(self, profile_id: str, code: Any, *, valid_window: int = 1) -> bool:
        """Verify a 6-digit TOTP code by re-deriving the profile secret."""
        if pyotp is None:
            return False
        code = str(code or "").strip()
        if not code or not profile_id:
            return False
        salt = self._salt_for(profile_id)
        if salt is None:
            return False
        try:
            secret = _derive_totp_secret(self._password, salt)
        except Exception:
            return False
        ok = bool(pyotp.TOTP(secret).verify(code, valid_window=valid_window))
        if ok:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE security_profiles SET last_verified_at=? WHERE profile_id=?",
                    (time.time(), profile_id),
                )
                conn.commit()
        return ok

    def current_code(self, profile_id: str) -> Dict[str, Any]:
        """Return a live code without exposing or persisting the derived secret."""
        if pyotp is None:
            raise SecurityProfileError("pyotp is not installed; cannot generate TOTP codes")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT salt_b64, last_verified_at FROM security_profiles WHERE profile_id=?",
                (profile_id,),
            ).fetchone()
        if row is None:
            raise SecurityProfileError("unknown authenticator profile")
        if row["last_verified_at"] is None:
            raise SecurityProfileError("verify this authenticator profile before showing its code")
        try:
            salt = base64.b64decode(row["salt_b64"].encode("ascii"))
            secret = _derive_totp_secret(self._password, salt)
        except Exception as exc:
            raise SecurityProfileError("could not derive the authenticator code") from exc
        now = time.time()
        totp = pyotp.TOTP(secret)
        remaining = max(1, int(totp.interval - (now % totp.interval)))
        return {"code": totp.at(now), "seconds_remaining": remaining}

    # ── per-agent assignments ─────────────────────────────────────────────────

    def assign_agent(self, agent_name: str, profile_id: str) -> None:
        agent_name = (agent_name or "").strip()
        if not agent_name:
            raise SecurityProfileError("agent_name is required")
        if not self.profile_exists(profile_id):
            raise SecurityProfileError(f"unknown profile_id: {profile_id}")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_profile_assignments (agent_name, profile_id, assigned_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(agent_name) DO UPDATE SET "
                "profile_id=excluded.profile_id, assigned_at=excluded.assigned_at",
                (agent_name, profile_id, time.time()),
            )
            conn.commit()

    def unassign_agent(self, agent_name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM agent_profile_assignments WHERE agent_name=?",
                ((agent_name or "").strip(),),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_agent_profile_id(self, agent_name: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT profile_id FROM agent_profile_assignments WHERE agent_name=?",
                ((agent_name or "").strip(),),
            ).fetchone()
        return row["profile_id"] if row else None

    def agent_requires_2fa(self, agent_name: str) -> bool:
        pid = self.get_agent_profile_id(agent_name)
        return pid is not None and self.profile_exists(pid)

    def verify_for_agent(self, agent_name: str, code: Any, *, valid_window: int = 1) -> bool:
        pid = self.get_agent_profile_id(agent_name)
        if pid is None:
            return False
        return self.verify(pid, code, valid_window=valid_window)

    # ── wipe / reset (recovery path) ──────────────────────────────────────────

    def wipe_profile(self, profile_id: str) -> bool:
        """Delete a profile and any agent assignments that point at it."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM agent_profile_assignments WHERE profile_id=?", (profile_id,)
            )
            cur = conn.execute(
                "DELETE FROM security_profiles WHERE profile_id=?", (profile_id,)
            )
            conn.commit()
            return cur.rowcount > 0

    def wipe_agent(self, agent_name: str) -> Optional[str]:
        """Clear an agent's 2FA assignment (the "uninstall" step).

        Returns the freed ``profile_id`` (if any) so the caller can also wipe the
        profile when it is no longer shared and the agent's encrypted data should
        be reset before a clean re-enrolment.
        """
        agent_name = (agent_name or "").strip()
        pid = self.get_agent_profile_id(agent_name)
        self.unassign_agent(agent_name)
        return pid
