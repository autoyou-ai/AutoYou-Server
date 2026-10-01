"""Live QR carries the existing AutoPair exchange; grants live only in memory."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time

PROTOCOL = "autoyou-live-pair/1"
MAX_BYTES = 65536
FRAME_BYTES = 600


def qr_frames(text: str) -> list[str]:
    data = text.encode("utf-8")
    if not 0 < len(data) <= MAX_BYTES:
        raise ValueError("Pairing message is empty or too large")
    digest = hashlib.sha256(data).hexdigest()
    parts = [data[i:i + FRAME_BYTES] for i in range(0, len(data), FRAME_BYTES)]
    return [f"ayqr1:{digest}:{i}:{len(parts)}:{base64.b64encode(part).decode()}"
            for i, part in enumerate(parts)]


class QRAssembly:
    def __init__(self):
        self.digest = ""
        self.parts = {}
        self.completed = ""
        self.started = 0.0
        self.count = 0

    def receive(self, frame: str) -> str | None:
        if len(frame.encode()) > MAX_BYTES:
            raise ValueError("Pairing message is too large")
        if not frame.startswith("ayqr1:"):
            return frame
        fields = frame.split(":", 4)
        if len(fields) != 5 or not re.fullmatch(r"[0-9a-f]{64}", fields[1]):
            raise ValueError("Invalid QR frame")
        _, digest, index, count, encoded = fields
        index, count = int(index), int(count)
        if not 0 <= index < count <= 110 or len(encoded) > 800:
            raise ValueError("Invalid QR frame size")
        part = base64.b64decode(encoded, validate=True)
        if not part or len(part) > FRAME_BYTES:
            raise ValueError("Invalid QR frame data")
        if digest == self.completed:
            return None
        if digest != self.digest or time.monotonic() - self.started > 120:
            self.digest, self.parts, self.started = digest, {}, time.monotonic()
            self.count = count
        if self.count != count:
            raise ValueError("QR frame counts do not match")
        self.parts[index] = part
        if len(self.parts) != count:
            return None
        data = b"".join(self.parts[i] for i in range(count))
        if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != digest:
            self.parts.clear()
            raise ValueError("QR frames do not match; scan again")
        self.completed = digest
        return data.decode("utf-8")


class LivePairing:
    """One guest per short-lived grant, bound to its authenticated helper channel."""
    def __init__(self, router, *, clock=time.time):
        self.router = router
        self.clock = clock
        self.grants = {}
        self.last_reply_key = ""

    def cancel(self, owner: str):
        for key, grant in list(self.grants.items()):
            if grant["owner"] == owner or grant["expires_at"] <= self.clock():
                self.grants.pop(key)
                if self.router:
                    self.router._cpace_sessions.pop(grant["sender"], None)

    def start(self, owner: str, *, mode: str, tier: str, ice: list, name: str,
              password: str, totp_code: str = "") -> dict:
        self.cancel(owner)
        if len(self.grants) >= 32:
            raise ValueError("Too many live invitations. Try again shortly.")
        key = secrets.token_hex(16)
        mode = "secure_professional" if mode.startswith("secure_professional") else mode
        if mode not in {"normal", "secure", "secure_professional"}:
            raise ValueError("Unsupported pairing security mode")
        setup = {"password": password, "security_mode": mode, "security_tier": tier,
                 "totp_code": totp_code}
        self.grants[key] = {"owner": owner, "sender": "liveqr:" + key,
                            "expires_at": int(self.clock() + 180), "setup": setup,
                            "ice": ice, "name": name, "used": False, "replies": {},
                            "lock": asyncio.Lock()}
        return self.invite(owner, key)

    def _grant(self, owner, key):
        grant = self.grants.get(key)
        if not grant or grant["owner"] != owner or grant["expires_at"] <= self.clock():
            raise ValueError("This live invitation expired. Start again.")
        return grant

    def invite(self, owner, key, *, totp_code=None):
        grant = self._grant(owner, key)
        if grant["used"]:
            raise ValueError("This invitation has already paired a device")
        setup = grant["setup"]
        if totp_code is not None:
            setup["totp_code"] = totp_code
        return {"protocol": PROTOCOL, "kind": "invite", "invite_id": key,
                "expires_at": grant["expires_at"], **setup,
                "ice_servers": grant["ice"], "name": grant["name"]}

    async def exchange(self, owner: str, request: dict) -> dict:
        if not isinstance(request, dict) or request.get("protocol") != PROTOCOL or request.get("kind") != "request":
            raise ValueError("Scan an AutoYou live pairing request")
        key = request.get("invite_id")
        if not isinstance(key, str):
            raise ValueError("Invalid invitation")
        grant = self._grant(owner, key)
        request_id, message = request.get("request_id"), request.get("message")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", request_id):
            raise ValueError("Invalid live pairing request")
        if not isinstance(message, str) or not 0 < len(message.encode()) <= MAX_BYTES:
            raise ValueError("Invalid live pairing message")
        command = message.split("\n", 1)[0].strip()
        if command.split(" ", 1)[0] not in {"/autopair_hello", "/autopair", "/pair", "/pair_hello", "/otp_pair"}:
            raise ValueError("Live pairing accepts only AutoPair signaling")
        async with grant["lock"]:
            self._grant(owner, key)
            if request_id in grant["replies"]:
                original, response = grant["replies"][request_id]
                if original != message:
                    raise ValueError("Pairing request changed; start again")
                if response is None:
                    raise ValueError("Pairing failed. Start a new invitation.")
                return response
            if grant["used"] or len(grant["replies"]) >= 6:
                raise ValueError("This invitation has been used. Start again.")
            # Reserve the attempt before crypto/offer handling; failed attempts count too.
            grant["replies"][request_id] = (message, None)
            reply = await self.router.process_message(message, platform="liveqr", sender_id=grant["sender"])
            response = {"protocol": PROTOCOL, "kind": "reply", "invite_id": key,
                        "request_id": request_id, "expires_at": grant["expires_at"],
                        "message": reply or "Pairing failed"}
            grant["used"] = bool(reply and reply.startswith("/autopair_answer\n"))
            grant["replies"][request_id] = (message, response)
            grant["last_reply"] = response
            self.last_reply_key = key
            return response

    def status(self):
        grant = self.grants.get(self.last_reply_key)
        return grant.get("last_reply") if grant and grant["expires_at"] > self.clock() else None


def wire_text(value: dict) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
