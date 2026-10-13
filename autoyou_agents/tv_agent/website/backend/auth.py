"""Viewer/editor/admin roles with a separate, mandatory OTP Settings boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time

from fastapi import HTTPException, Request

from .store import Store, digest, uid

COOKIE_NAME = "autoyou_tv_session"
SESSION_SECONDS = 8 * 3600
OTP_SECONDS = 30 * 60


def verify_totp(secret: str, code: str, *, now: float | None = None) -> bool:
    if not re.fullmatch(r"\d{6}", code):
        return False
    try:
        key = base64.b32decode(secret.strip().replace(" ", "").upper() + "=" * (-len(secret.strip().replace(" ", "")) % 8))
    except (ValueError, base64.binascii.Error):
        return False
    if not key:
        return False
    step = int((now if now is not None else time.time()) // 30)
    for delta in (-1, 0, 1):
        if step + delta < 0:
            continue
        hashed = hmac.new(key, struct.pack(">Q", step + delta), hashlib.sha1).digest()
        offset = hashed[-1] & 15
        number = (struct.unpack(">I", hashed[offset:offset + 4])[0] & 0x7fffffff) % 1000000
        if hmac.compare_digest(f"{number:06}", code):
            return True
    return False


def verify_operator_otp(code: str) -> bool:
    secret = os.getenv("AUTOYOU_TV_TOTP_SECRET")
    if secret:
        return verify_totp(secret, code)
    # Lazy import avoids initializing the full AutoYou server at module import.
    from autoyou_agents.shared_tools.scheduler_mission_control import _runtime_server
    server = _runtime_server()
    if server.agent_has_assigned_2fa_profile("tv_agent"):
        return bool(server.verify_agent_assigned_2fa("tv_agent", code))
    cfg = server.STATE.config or server._default_config()
    secret = server._get_pairing_totp_secret(cfg)
    if not secret:
        raise RuntimeError("Operator OTP is not configured")
    return bool(server._verify_totp_secret(secret, code))


class Auth:
    def __init__(self, store: Store, verifier=None):
        self.store = store
        self.verifier = verifier or verify_operator_otp

    def session(self, request: Request) -> dict:
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else request.cookies.get(COOKIE_NAME, "")
        if not token or len(token) > 512:
            return {"role": "viewer", "otp_unlocked": False}
        now = time.time()
        with self.store.connect() as db:
            row = db.execute("""SELECT s.* FROM sessions s LEFT JOIN invitations i ON i.id=s.invitation_id
                              WHERE s.token_hash=? AND s.expires>? AND
                              (s.invitation_id IS NULL OR (i.revoked=0 AND i.expires>?))""",
                             (digest(token), now, now)).fetchone()
        if not row:
            return {"role": "viewer", "otp_unlocked": False}
        return {"role": row["role"], "otp_unlocked": row["otp_until"] > now,
                "expires": row["expires"], "otp_expires": row["otp_until"]}

    def require_editor(self, request: Request):
        session = self.session(request)
        if session["role"] not in {"editor", "admin"}:
            raise HTTPException(403, "An editor invitation or admin session is required for Studio")
        return session

    def require_settings(self, request: Request):
        session = self.session(request)
        if session["role"] != "admin" or not session["otp_unlocked"]:
            raise HTTPException(401, "Enter your authenticator code to unlock Settings")
        return session

    def _new_session(self, role: str, *, invitation_id=None, otp=False) -> str:
        token, now = secrets.token_urlsafe(32), time.time()
        with self.store.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                       (digest(token), role, invitation_id, now + SESSION_SECONDS, now + OTP_SECONDS if otp else 0))
        return token

    def login_otp(self, request: Request, code: str) -> str:
        now = time.time()
        key = digest("otp:" + (request.client.host if request.client else "unknown"))
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM auth_attempts WHERE created<?", (now - 300,))
            count = db.execute("SELECT COUNT(*) FROM auth_attempts WHERE key=? AND created>?", (key, now - 60)).fetchone()[0]
            if count >= 5:
                raise HTTPException(429, "Too many attempts. Try again in a minute.")
            db.execute("INSERT INTO auth_attempts VALUES (?,?)", (key, now))
        if not re.fullmatch(r"\d{6}", code):
            raise HTTPException(401, "Enter the six-digit code from your authenticator")
        try:
            verified = self.verifier(code)
        except Exception:
            raise HTTPException(503, "Configure an AutoYou authenticator before unlocking TV Settings") from None
        if not verified:
            raise HTTPException(401, "Invalid authenticator code")
        # Replayed codes do not mint fresh admin sessions.
        replay_key = "used:" + digest(code)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT 1 FROM auth_attempts WHERE key=? AND created>?", (replay_key, now - 90)).fetchone()
            if used:
                raise HTTPException(401, "This code was already used. Wait for the next code.")
            db.execute("INSERT INTO auth_attempts VALUES (?,?)", (replay_key, now))
        return self._new_session("admin", otp=True)

    def create_invitation(self, label: str, role: str = "editor", days: int = 7) -> dict:
        if role not in {"editor", "admin"} or not 1 <= days <= 30:
            raise ValueError("Choose editor/admin and an expiry between 1 and 30 days")
        token, invitation_id = secrets.token_urlsafe(32), uid()
        with self.store.connect() as db:
            db.execute("INSERT INTO invitations VALUES (?,?,?,?,?,0)",
                       (invitation_id, digest(token), label.strip()[:100] or "Editor", role, time.time() + days * 86400))
        return {"id": invitation_id, "token": token, "role": role, "label": label, "days": days}

    def login_invitation(self, token: str) -> str:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM invitations WHERE token_hash=? AND expires>? AND revoked=0",
                             (digest(token), time.time())).fetchone()
        if not row:
            raise HTTPException(401, "This invitation has expired or been revoked")
        return self._new_session(row["role"], invitation_id=row["id"])

    def invitations(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,label,role,expires,revoked FROM invitations ORDER BY expires DESC")]

    def revoke(self, invitation_id: str):
        with self.store.connect() as db:
            db.execute("UPDATE invitations SET revoked=1 WHERE id=?", (invitation_id,))
            db.execute("DELETE FROM sessions WHERE invitation_id=?", (invitation_id,))

    def logout(self, request: Request):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else request.cookies.get(COOKIE_NAME, "")
        with self.store.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))
