# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-3436423233373332206f7220-3a91388cd37f1b7643a23018

"""
Centralized pairing command router for AutoYou.

Provides a single place to parse and process special pairing commands
across all messaging partners (Telegram, WhatsApp, Signal):
- `/pair` generates an OTP and manages the tunnelmole lifecycle
- `/autopair` validates a hash, processes a WebRTC offer, and returns an answer
- `/new`, `/newchat`, and `/newconversation` can start a fresh conversation thread

Server registers helper callbacks to avoid tight coupling and circular imports.
Services call `pairing_router.process_message(...)` to handle commands.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-3436423233373332206f7220-3a91388cd37f1b7643a23018"


import json
import logging
import asyncio
import hmac
import re
import time
import base64
import zlib
from contextvars import ContextVar
from typing import Awaitable, Callable, Optional, Dict, Any, List, Tuple

from shared.pairing_cpace import (
    CPaceSessionKey,
    build_cpace_hello_answer,
    build_cpace_hello_answer_with_payload,
    decrypt_cpace_message,
    encrypt_cpace_message,
    is_cpace_hello,
    is_cpace_message,
    parse_cpace_hello,
)
from shared.pairing_pake import (
    PakeServerReplyContext,
    decrypt_pake_request,
    encrypt_pake_response,
    is_pake_request,
)
from shared.remote_access_policy import DEVICE_OWN, DEVICE_SHARED
from shared.secure_storage import is_secure_professional_mode

LOGGER = logging.getLogger("autoyou.pairing_router")
AUTOPAIR_COMPRESSED_PREFIX = "z:"
AUTOPAIR_HELLO_PREFIX = "/autopair_hello\n"
AUTOPAIR_HELLO_ANSWER_PREFIX = "/autopair_hello_answer\n"
AUTOPAIR_ANSWER_PREFIX = "/autopair_answer\n"
AUTOPAIR_CANDIDATES_PREFIX = "/autopair_candidates\n"
PAIR_HELLO_PREFIX = "/pair_hello\n"
PAIR_HELLO_ANSWER_PREFIX = "/pair_hello_answer\n"
# How long a completed CPace handshake's session key stays usable before the
# server forgets it (bounds memory retention of key material - see L-9).
CPACE_SESSION_TTL_SECONDS = 300.0
SECURITY_TIER_A = "A"
SECURITY_TIER_B = "B"
# Regex used by looks_like_raw_autopair_fragment to identify bare base64/base64url blobs.
_RAW_FRAGMENT_RE = re.compile(r"^[A-Za-z0-9+/=\-_]{100,}$")
PLATFORM_REPLY_LIMITS = {
    "telegram": 4096,
    "telegram_user": 4096,
}
MESSAGE_REPLY_LIMITS = PLATFORM_REPLY_LIMITS
# Maximum seconds to wait for continuation fragments of a split /autopair message.
AUTOPAIR_FRAGMENT_TIMEOUT_SECONDS: float = 5.0
# Give up reassembly after this many continuation fragments (guards against runaway buffers).
AUTOPAIR_FRAGMENT_MAX_PARTS: int = 5
# Keep reassembly within the same bounded payload contract used by the MCP
# request models.  Without a total cap, five individually valid fragments
# could otherwise allocate several hundred kilobytes per sender.
AUTOPAIR_FRAGMENT_MAX_TOTAL_LENGTH: int = 131072
# Bound the decoded side of the compressed-payload contract as well as the
# encoded wire text.  Compression is attacker-controlled and is decoded before
# the normal-mode password hash is checked, so zlib must never be allowed to
# allocate an unbounded output buffer.
AUTOPAIR_DECOMPRESSED_MAX_LENGTH: int = 131072
AUTOPAIR_ANSWER_METADATA_KEYS: Tuple[str, ...] = (
    "owner_key",
    "canonical_user_id",
    "canonical_session_id",
    "conversation_session_id",
    "destination_session_id",
    "raw_session_id",
    "server_name",
    "server_id",
    "server_identity_key",
    "pairing_mode",
    "device_ownership",
)
PAIRING_TEXT_TRANSLATIONS = str.maketrans(
    {
        "\r": "\n",
        "\u0085": "\n",
        "\u2028": "\n",
        "\u2029": "\n",
        "\u00a0": " ",
        "\ufeff": "",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\u200e": "",
        "\u200f": "",
        "\u2060": "",
        "\u2066": "",
        "\u2067": "",
        "\u2068": "",
        "\u2069": "",
    }
)

class PairingRouter:
    """Router that processes special pairing messages across platforms.

    Helpers must be configured by the server using `configure(...)`.
    """

    # Sentinel returned by process_message when a message was consumed as a
    # buffered autopair fragment but the full payload is not yet assembled.
    # Callers MUST NOT forward the message to AI or other handlers on receipt.
    FRAGMENT_CONSUMED: str = "__autopair_fragment_consumed__"

    def __init__(self) -> None:
        # Helper callbacks provided by server.py
        self._generate_hash: Optional[Callable[[str], str]] = None
        self._aead_encrypt: Optional[Callable[[str, str], str]] = None
        self._aead_decrypt: Optional[Callable[[str, str], str]] = None
        self._get_current_password: Optional[Callable[[], Optional[str]]] = None
        self._get_security_mode: Optional[Callable[[], str]] = None
        self._get_totp_secret_for_sender: Optional[Callable[[str, str], Optional[str]]] = None
        self._get_all_totp_secrets: Optional[Callable[[], Dict[str, str]]] = None
        self._get_tunnelmole_status: Optional[Callable[[], Dict[str, Any]]] = None
        self._extend_tunnelmole_timer: Optional[Callable[[Optional[int]], Awaitable[None]]] = None
        self._start_tunnelmole_service_with_timer: Optional[Callable[[], Awaitable[bool]]] = None
        self._ensure_auth_server_running: Optional[Callable[[], Awaitable[Any]]] = None
        self._generate_otp_hash_and_cache: Optional[Callable[[int], str]] = None
        self._handle_autopair_offer: Optional[Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None
        self._start_new_conversation: Optional[
            Callable[[str, str, Optional[str]], Awaitable[str]]
        ] = None
        self._totp_fallback_offset: int = 0
        # Fragment buffer for multi-message (Telegram-split) /autopair payloads.
        # Key: "platform:sender_id"  Value: {fragments, timestamp, identity_sender_id}
        self._fragment_buffers: Dict[str, Dict] = {}
        # Completed CPace handshakes, keyed by sender/identity id.
        # Value: (session_key, established_at_monotonic). See CPACE_SESSION_TTL_SECONDS.
        self._cpace_sessions: Dict[str, Tuple[CPaceSessionKey, float]] = {}
        # Trickle-ICE callbacks for Cloud SSE relay
        self._apply_remote_ice_candidates: Optional[Callable[[str, list], Awaitable[None]]] = None
        self._get_trickle_candidates: Optional[Callable[[str], Awaitable[list]]] = None
        # Pairing mode callbacks
        self._is_totp_pair_mode: Optional[Callable[[], bool]] = None
        self._is_url_only_pair_mode: Optional[Callable[[], bool]] = None
        self._is_tunnelmole_unmanaged_mode: Optional[Callable[[], bool]] = None
        self._generate_totp_pair_otp_and_cache: Optional[Callable[[], str]] = None
        self._start_tunnelmole_service_no_timer: Optional[Callable[[], Awaitable[bool]]] = None
        # Online TOTP verification for the /autopair_hello 2FA gate (secure_professional).
        # Distinct from the password:code concatenation the legacy /pair shortcut still
        # uses - this is a live, rate-limited check, never mixed into key derivation.
        self._verify_totp_code: Optional[Callable[[str, str], bool]] = None
        self._totp_hello_rate_limit_allowed: Optional[Callable[[str], bool]] = None
        # Security Tier ("A"/"B") - orthogonal to security mode. B accepts both
        # the cpace1 hello-first flow and a bare pake1 /autopair; A requires
        # the hello-first flow only. Defaults to "B" if not configured.
        self._get_pairing_tier: Optional[Callable[[], str]] = None
        # Sender-scoped credentials are used only for explicitly shared cloud
        # servers. ContextVar keeps concurrent pairing tasks isolated without
        # mutating the server's global password callbacks.
        self._auth_profile: ContextVar[Optional[Dict[str, str]]] = ContextVar(
            f"autoyou_pairing_auth_profile_{id(self)}", default=None
        )

    def configure(
        self,
        *,
        generate_hash: Callable[[str], str],
        aead_encrypt: Callable[[str, str], str],
        aead_decrypt: Callable[[str, str], str],
        get_current_password: Callable[[], Optional[str]],
        get_security_mode: Callable[[], str],
        get_totp_secret_for_sender: Callable[[str, str], Optional[str]],
        get_all_totp_secrets: Callable[[], Dict[str, str]],
        get_tunnelmole_status: Callable[[], Dict[str, Any]],
        extend_tunnelmole_timer: Callable[[Optional[int]], Awaitable[None]],
        start_tunnelmole_service_with_timer: Callable[[], Awaitable[bool]],
        ensure_auth_server_running: Optional[Callable[[], Awaitable[Any]]] = None,
        generate_otp_hash_and_cache: Callable[[int], str],
        handle_autopair_offer: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
        start_new_conversation: Optional[
            Callable[[str, str, Optional[str]], Awaitable[str]]
        ] = None,
        apply_remote_ice_candidates: Optional[Callable[[str, list], Awaitable[None]]] = None,
        get_trickle_candidates: Optional[Callable[[str], Awaitable[list]]] = None,
        is_totp_pair_mode: Optional[Callable[[], bool]] = None,
        is_url_only_pair_mode: Optional[Callable[[], bool]] = None,
        is_tunnelmole_unmanaged_mode: Optional[Callable[[], bool]] = None,
        generate_totp_pair_otp_and_cache: Optional[Callable[[], str]] = None,
        start_tunnelmole_service_no_timer: Optional[Callable[[], Awaitable[bool]]] = None,
        verify_totp_code: Optional[Callable[[str, str], bool]] = None,
        totp_hello_rate_limit_allowed: Optional[Callable[[str], bool]] = None,
        get_pairing_tier: Optional[Callable[[], str]] = None,
    ) -> None:
        """Configure helper callbacks provided by the server."""
        self._generate_hash = generate_hash
        self._aead_encrypt = aead_encrypt
        self._aead_decrypt = aead_decrypt
        self._get_current_password = get_current_password
        self._get_security_mode = get_security_mode
        self._get_totp_secret_for_sender = get_totp_secret_for_sender
        self._get_all_totp_secrets = get_all_totp_secrets
        self._get_tunnelmole_status = get_tunnelmole_status
        self._extend_tunnelmole_timer = extend_tunnelmole_timer
        self._start_tunnelmole_service_with_timer = start_tunnelmole_service_with_timer
        self._ensure_auth_server_running = ensure_auth_server_running
        self._generate_otp_hash_and_cache = generate_otp_hash_and_cache
        self._handle_autopair_offer = handle_autopair_offer
        self._start_new_conversation = start_new_conversation
        self._apply_remote_ice_candidates = apply_remote_ice_candidates
        self._get_trickle_candidates = get_trickle_candidates
        self._is_totp_pair_mode = is_totp_pair_mode
        self._is_url_only_pair_mode = is_url_only_pair_mode
        self._is_tunnelmole_unmanaged_mode = is_tunnelmole_unmanaged_mode
        self._generate_totp_pair_otp_and_cache = generate_totp_pair_otp_and_cache
        self._start_tunnelmole_service_no_timer = start_tunnelmole_service_no_timer
        self._verify_totp_code = verify_totp_code
        self._totp_hello_rate_limit_allowed = totp_hello_rate_limit_allowed
        self._get_pairing_tier = get_pairing_tier

    @staticmethod
    def _validated_auth_profile(profile: Dict[str, Any]) -> Dict[str, str]:
        password = str(profile.get("password") or "").strip()
        mode = str(profile.get("security_mode") or "secure").strip().lower()
        tier = str(profile.get("security_tier") or SECURITY_TIER_A).strip().upper()
        if not password or len(password) > 256:
            raise ValueError("Pairing auth profile password is invalid")
        if mode != "secure":
            raise ValueError("Pairing auth profile mode is invalid")
        if tier not in {SECURITY_TIER_A, SECURITY_TIER_B}:
            raise ValueError("Pairing auth profile tier is invalid")
        return {
            "password": password,
            "security_mode": mode,
            "security_tier": tier,
        }

    def current_auth_profile(self) -> Optional[Dict[str, str]]:
        profile = self._auth_profile.get()
        return dict(profile) if profile else None

    def _device_ownership(self, platform: Any) -> str:
        """Own only for this account's devices; every other pairing is shared.

        AutoYou Cloud routes an ungranted Cloud Pair to a server only from the
        server's own account; another account's device arrives with a shared
        computer grant, which sets the auth profile. Pairing without an
        account - a messaging partner, a public link - is always shared.
        """
        if str(platform or "").strip().lower() == "cloud" and self._auth_profile.get() is None:
            return DEVICE_OWN
        return DEVICE_SHARED

    def _current_password(self) -> Optional[str]:
        profile = self._auth_profile.get()
        if profile:
            return profile["password"]
        return self._get_current_password() if self._get_current_password else None

    def _current_security_mode(self) -> str:
        profile = self._auth_profile.get()
        if profile:
            return profile["security_mode"]
        try:
            return self._get_security_mode() if self._get_security_mode else "normal"
        except Exception:
            return "normal"

    def _get_security_tier(self) -> str:
        profile = self._auth_profile.get()
        if profile:
            return profile["security_tier"]
        try:
            tier = str((self._get_pairing_tier() if self._get_pairing_tier else SECURITY_TIER_B) or SECURITY_TIER_B).strip().upper()
        except Exception:
            tier = SECURITY_TIER_B
        return tier if tier in (SECURITY_TIER_A, SECURITY_TIER_B) else SECURITY_TIER_B

    async def add_remote_ice_candidates(self, session_id: str, candidates: list, platform: str = "") -> None:
        """Apply remote ICE candidates from the cloud relay to an active WebRTC session.

        Called when an ``/autopair_candidates`` pairing message arrives from
        AutoYou Cloud.  Delegates to the WebRTCManager via
        the ``apply_remote_ice_candidates`` callback configured at startup.
        """
        if not candidates or not session_id:
            return
        if self._apply_remote_ice_candidates:
            try:
                await self._apply_remote_ice_candidates(session_id, candidates)
            except Exception as exc:
                LOGGER.warning(
                    "add_remote_ice_candidates: callback raised for session %s: %s", session_id, exc
                )
        else:
            LOGGER.debug("add_remote_ice_candidates: no callback configured (session=%s)", session_id)

    async def get_trickle_candidates(self, session_id: str, platform: str = "") -> List[Dict[str, Any]]:
        """Return and drain queued server-side trickle ICE candidates for the session.

        Cloud Pair uses this after the SDP answer is posted to AutoYou Cloud.
        Non-cloud pairing remains one-shot by default, so those sessions usually
        return no queued candidates.
        """
        if self._get_trickle_candidates:
            try:
                return await self._get_trickle_candidates(session_id) or []
            except Exception as exc:
                LOGGER.warning(
                    "get_trickle_candidates: callback raised for session %s: %s", session_id, exc
                )
        return []

    # ------------------------------------------------------------------ #
    #  Fragment buffer helpers (multi-message autopair reassembly)        #
    # ------------------------------------------------------------------ #

    def _buffer_key(self, platform: str, sender_id: str) -> str:
        return f"{(platform or '').strip().lower()}:{sender_id}"

    def has_pending_autopair_buffer(self, platform: str, sender_id: str) -> bool:
        """Return True if a fragment buffer is pending for this sender and not yet expired."""
        key = self._buffer_key(platform, sender_id)
        buf = self._fragment_buffers.get(key)
        if not buf:
            return False
        if time.time() - buf.get("timestamp", 0) > AUTOPAIR_FRAGMENT_TIMEOUT_SECONDS:
            self._fragment_buffers.pop(key, None)
            return False
        return True

    @staticmethod
    def looks_like_raw_autopair_fragment(text: str) -> bool:
        """Return True if *text* looks like a raw base64/base64url autopair payload fragment.

        Used by Telegram, WhatsApp, and Signal handlers to suppress AI dispatch
        for bare base64 blobs that are orphaned fragments of a split /autopair
        message (e.g. when the user retried after the 30-second buffer timeout).

        The heuristic matches strings that:
        - Are ≥ 100 characters after stripping whitespace (short legit messages won't match)
        - Contain only base64 / base64url characters: A-Z a-z 0-9 + / = - _
        - Contain no whitespace (a real user message would have spaces)
        """
        stripped = (text or "").strip()
        return bool(_RAW_FRAGMENT_RE.match(stripped))

    def _cleanup_expired_buffers(self) -> None:
        now = time.time()
        expired = [
            k for k, v in list(self._fragment_buffers.items())
            if now - v.get("timestamp", 0) > AUTOPAIR_FRAGMENT_TIMEOUT_SECONDS
        ]
        for k in expired:
            self._fragment_buffers.pop(k, None)

    def _start_autopair_fragment_buffer(
        self,
        *,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
        initial_fragment: Optional[str] = None,
    ) -> None:
        self._cleanup_expired_buffers()
        key = self._buffer_key(platform, sender_id)
        fragments: List[str] = []
        if initial_fragment:
            fragments.append(initial_fragment)
        self._fragment_buffers[key] = {
            "fragments": fragments,
            "timestamp": time.time(),
            "identity_sender_id": identity_sender_id,
        }

    @staticmethod
    def _is_near_transport_limit(text: str, platform: str) -> bool:
        limit = PairingRouter.reply_limit(platform)
        if not limit:
            return False
        # Telegram clients can trim or normalize around the 4096-char boundary.
        return len(text or "") >= max(1, limit - 32)

    @staticmethod
    def _resolve_auth_sender_id(sender_id: str, identity_sender_id: Optional[str] = None) -> str:
        stable_sender_id = str(identity_sender_id or "").strip()
        if stable_sender_id:
            return stable_sender_id
        return str(sender_id or "").strip()

    def _autopair_context_keys(
        self,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> List[str]:
        keys: List[str] = []
        for value in (sender_id, identity_sender_id):
            normalized = str(value or "").strip()
            if normalized and normalized not in keys:
                keys.append(normalized)
        return keys

    async def _process_autopair_body(
        self,
        payload_text: str,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
        parsed_payload: Optional[Any] = None,
    ) -> str:
        """Parse and process an assembled autopair payload string.

        Called both from the single-message path (_handle_autopair_command) and the
        multi-fragment reassembly path (_try_consume_autopair_fragment).
        """
        parsed = parsed_payload
        if parsed is None:
            parsed = await self._parse_autopair_payload(
                payload_text,
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
            )
        if isinstance(parsed, str):
            return parsed  # error string from _parse_autopair_payload

        payload, session = parsed

        for field in ("hash", "offer"):
            if field not in payload:
                return f"Missing required field: {field}"

        offer = payload.get("offer")
        if not isinstance(offer, dict):
            return "Field 'offer' must be an object"

        for field in ("type", "sdp"):
            if field not in offer:
                return f"Missing required offer field: {field}"

        if offer.get("type") != "offer":
            return "Invalid offer type. Expected 'offer'"

        current_password = self._current_password()
        if current_password is None:
            return "Server password not configured"

        expected_hash = self._generate_hash(current_password)
        provided_hash = str(payload.get("hash") or "")
        if not hmac.compare_digest(expected_hash, provided_hash):
            return "Authentication failed"

        payload["_autoyou_pairing_platform"] = str(platform or "").strip().lower()
        payload["_autoyou_sender_id"] = str(identity_sender_id or sender_id or "").strip()
        payload["_autoyou_device_ownership"] = self._device_ownership(platform)
        if session is not None:
            security_mode = self._current_security_mode()
            payload["_autoyou_pairing_mode"] = (
                "totp_pair" if is_secure_professional_mode(security_mode) else "secure_pair"
            )

        try:
            answer = await self._handle_autopair_offer(str(sender_id), payload)
        except Exception as e:
            import traceback
            LOGGER.error(
                "Error processing autopair offer for %s:%s: %s", platform, sender_id, e
            )
            LOGGER.error(traceback.format_exc())
            return f"WebRTC processing error: {e}"

        return await self._format_autopair_answer(
            answer,
            platform=platform,
            sender_id=sender_id,
            session=session,
            identity_sender_id=identity_sender_id,
        )

    async def _try_consume_autopair_fragment(
        self,
        text: str,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> Optional[str]:
        """Try to consume text as a continuation fragment of a pending split autopair.

        Newer Telegram accounts/versions split the /autopair message into multiple
        messages at the newline separator and at the 4096-character transport limit.
        This method buffers those continuation fragments and processes the assembled
        payload once it parses correctly.

        Returns:
            FRAGMENT_CONSUMED   if the fragment was buffered but more are needed.
            A response string   if the assembled payload processed successfully.
            None                if no pending buffer exists for this sender.
        """
        self._cleanup_expired_buffers()
        key = self._buffer_key(platform, sender_id)
        buf = self._fragment_buffers.get(key)
        if buf is None:
            return None

        fragment_text = str(text or "")
        current_length = sum(
            len(str(part or "").encode("utf-8"))
            for part in buf.get("fragments", [])
        )
        fragment_length = len(fragment_text.encode("utf-8"))
        if current_length + fragment_length > AUTOPAIR_FRAGMENT_MAX_TOTAL_LENGTH:
            self._fragment_buffers.pop(key, None)
            LOGGER.warning(
                "[AUTOPAIR] Fragment buffer exceeded %d bytes for %s:%s; discarding",
                AUTOPAIR_FRAGMENT_MAX_TOTAL_LENGTH,
                platform,
                sender_id,
            )
            return self.FRAGMENT_CONSUMED

        buf["fragments"].append(fragment_text)
        buf["timestamp"] = time.time()
        n_parts = len(buf["fragments"])
        assembled = "".join(buf["fragments"])

        LOGGER.info(
            "[AUTOPAIR] Fragment %d received for %s:%s (assembled_len=%d)",
            n_parts, platform, sender_id, len(assembled),
        )

        # Attempt to parse the assembled payload.
        # A ValueError/JSONDecodeError means more fragments are still needed.
        test_parsed = await self._parse_autopair_payload(
            assembled, platform=platform, sender_id=sender_id
        )
        if isinstance(test_parsed, str):
            if n_parts >= AUTOPAIR_FRAGMENT_MAX_PARTS:
                self._fragment_buffers.pop(key, None)
                LOGGER.warning(
                    "[AUTOPAIR] Max fragments (%d) reached for %s:%s without valid payload - "
                    "discarding buffer. Last parse error: %s",
                    AUTOPAIR_FRAGMENT_MAX_PARTS, platform, sender_id, test_parsed,
                )
                # Consume silently so malformed/truncated autopair chunks do not reach AI.
                return self.FRAGMENT_CONSUMED
            LOGGER.debug(
                "[AUTOPAIR] Fragment %d still incomplete for %s:%s, waiting. Error: %s",
                n_parts, platform, sender_id, test_parsed,
            )
            return self.FRAGMENT_CONSUMED

        # Payload parses - assemble and dispatch.
        stored_identity = buf.get("identity_sender_id")
        self._fragment_buffers.pop(key, None)

        LOGGER.info(
            "[AUTOPAIR] Complete autopair payload assembled from %d fragment(s) for %s:%s",
            n_parts, platform, sender_id,
        )

        if not (self._generate_hash and self._get_current_password and self._handle_autopair_offer):
            LOGGER.warning(
                "[AUTOPAIR] Helpers not configured for reassembled /autopair from %s:%s",
                platform, sender_id,
            )
            return "Internal server error during autopair"

        return await self._process_autopair_body(
            assembled,
            platform=platform,
            sender_id=sender_id,
            identity_sender_id=identity_sender_id or stored_identity,
            parsed_payload=test_parsed,
        )

    # ------------------------------------------------------------------ #

    async def process_message(
        self,
        message_text: str,
        platform: str,
        sender_id: str,
        *,
        identity_sender_id: Optional[str] = None,
        auth_profile: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Process a message and return response text if it is a pairing command.

        Args:
            message_text: Raw message text received from the platform.
            platform: Platform identifier (e.g., "telegram", "whatsapp", "signal").
            sender_id: Identifier for the transport sender or chat.
            identity_sender_id: Optional stable sender identifier used only for
                conversation ownership. When omitted, ``sender_id`` is reused.

        Returns:
            Response text to send back, or None if not a special command.
        """
        token = None
        if auth_profile is not None:
            token = self._auth_profile.set(self._validated_auth_profile(auth_profile))
        try:
            return await self._process_message_with_current_profile(
                message_text,
                platform,
                sender_id,
                identity_sender_id=identity_sender_id,
            )
        finally:
            if token is not None:
                self._auth_profile.reset(token)

    async def _process_message_with_current_profile(
        self,
        message_text: str,
        platform: str,
        sender_id: str,
        *,
        identity_sender_id: Optional[str] = None,
    ) -> Optional[str]:
        text = self._normalize_pairing_text(message_text)
        if not text:
            return None

        if self._is_new_conversation_command(text):
            return await self._handle_new_conversation_command(
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
            )
        if text.startswith("/pair_hello"):
            return await self._handle_pair_hello_command(
                text=text,
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
            )
        if text.startswith("/pair") or text.startswith("/otp_pair"):
            # /otp_pair is a pure alias for /pair - some OpenClaw/Telegram
            # gateway setups mangle the literal "/pair" command, so this gives
            # operators a second spelling that reaches the same handler.
            prefix = "/otp_pair" if text.startswith("/otp_pair") else "/pair"
            # secure_professional's B-Tier trailing code: "/pair 482913" or
            # "/otp_pair 482913" - a single copy-pasteable line. Verified
            # online before anything is generated; never enters any KDF.
            trailing_totp_code = text[len(prefix):].strip() or None
            return await self._handle_pair_command(
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
                totp_code=trailing_totp_code,
            )
        if text.startswith("/autopair_hello"):
            return await self._handle_autopair_hello_command(
                text=text,
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
            )
        if text.startswith("/autopair_candidates"):
            return await self._handle_autopair_candidates_command(
                text=text,
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
            )
        if text.startswith("/autopair"):
            return await self._handle_autopair_command(
                text=text,
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
            )
        # Check if this is a continuation fragment from a Telegram-split /autopair message.
        # Newer Telegram accounts split long messages at newlines and at the 4096-char limit,
        # causing the /autopair command and its payload to arrive as separate messages.
        fragment_result = await self._try_consume_autopair_fragment(
            text=text,
            platform=platform,
            sender_id=sender_id,
            identity_sender_id=identity_sender_id,
        )
        if fragment_result is not None:
            return fragment_result
        return None

    @staticmethod
    def _is_new_conversation_command(text: str) -> bool:
        normalized = (text or "").strip().lower()
        return normalized in {"/new", "/newchat", "/newconversation"}

    async def _handle_new_conversation_command(
        self,
        *,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> str:
        """Start a fresh conversation thread for whole-message new conversation commands."""
        if not self._start_new_conversation:
            LOGGER.warning(
                "Conversation reset helpers not configured; cannot process /new for %s:%s",
                platform,
                sender_id,
            )
            return "Unable to start a new conversation right now."

        try:
            return await self._start_new_conversation(platform, sender_id, identity_sender_id)
        except Exception as exc:
            LOGGER.warning(
                "Conversation reset callback failed for %s:%s: %s",
                platform,
                sender_id,
                exc,
            )
            return "Unable to start a new conversation right now."

    def _pair_totp_secret_for_response(
        self,
        platform: str,
        sender_id: str,
        *,
        identity_sender_id: Optional[str] = None,
    ) -> Optional[str]:
        """Return the shared secure-professional pairing secret for /otp."""
        mode = self._current_security_mode()

        if not is_secure_professional_mode(mode):
            return None

        auth_sender_id = self._resolve_auth_sender_id(sender_id, identity_sender_id)
        exact_secret = (
            self._get_totp_secret_for_sender(platform, auth_sender_id)
            if self._get_totp_secret_for_sender
            else None
        )
        if exact_secret:
            return exact_secret

        if not self._get_all_totp_secrets:
            return None

        try:
            all_clients = self._get_all_totp_secrets() or {}
        except Exception as exc:
            LOGGER.warning(
                "Failed to load secure-professional pairing TOTP secret for /pair on %s:%s: %s",
                platform,
                auth_sender_id,
                exc,
            )
            return None

        unique_secrets = {
            str(secret).strip()
            for secret in all_clients.values()
            if str(secret or "").strip()
        }
        if len(unique_secrets) == 1:
            return next(iter(unique_secrets))

        return None

    async def _handle_pair_command(
        self,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
        totp_code: Optional[str] = None,
    ) -> str:
        """Handle /pair (or /otp_pair) command using tunnelmole helpers.

        In normal/secure mode a random OTP is generated, cached briefly, and
        a tunnelmole timer is started/extended.

        In secure_professional mode, the trailing code in `/pair 482913` (or
        `/otp_pair 482913`) is verified online (rate limited) before anything
        else happens - it never enters the response's encryption key, which
        is what actually delivers the second factor (see H-20 / the B-Tier fix
        applied alongside autopair's).

        In authenticator pair-code mode specifically, the current TOTP code is
        also used as the OTP value itself (sent back as the 'otp' field)
        instead of a fresh random one.

        Tunnel lifetime is controlled separately:
        - Timed mode starts or extends the tunnel timeout timer.
        - Unmanaged mode skips timer management and leaves shutdown to the
          reverse-proxy connection or explicit operator stop.
        """
        mode = self._current_security_mode()
        if is_secure_professional_mode(mode):
            allowed, error = await self._verify_autopair_totp_online(
                platform, sender_id, totp_code, identity_sender_id=identity_sender_id
            )
            if not allowed:
                return f"/pair command received. {error or 'Authentication failed'}"

        body, error = await self._prepare_pair_body(
            platform=platform,
            sender_id=sender_id,
            identity_sender_id=identity_sender_id,
        )
        if error or body is None:
            return f"/pair command received. {error or 'Pairing helpers not available.'}"
        return await self._format_otp_response(
            body,
            platform=platform,
            sender_id=sender_id,
            identity_sender_id=identity_sender_id,
        )

    async def _prepare_pair_body(
        self,
        *,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Start/extend the tunnel, generate the pairing code, and build the
        plaintext {otp, url, totp_secret?} body shared by /pair (B-Tier) and
        /pair_hello (A-Tier). Returns (body, None) on success or
        (None, error_text) on failure - the caller wraps the error in its own
        reply shape. Any online TOTP gating happens before this is called.
        """
        if not self._get_tunnelmole_status or not self._generate_otp_hash_and_cache or not self._extend_tunnelmole_timer or not self._start_tunnelmole_service_with_timer:
            LOGGER.warning("Pairing helpers not configured; cannot process /pair")
            return None, "Pairing helpers not available."

        authenticator_pair_code_mode: bool = bool(self._is_totp_pair_mode and self._is_totp_pair_mode())
        unmanaged_connection_mode: bool = bool(
            self._is_tunnelmole_unmanaged_mode()
            if self._is_tunnelmole_unmanaged_mode
            else authenticator_pair_code_mode
        )
        # URL-only share (most secure): when enabled alongside authenticator
        # pair-code mode, the partner receives ONLY the public URL - no OTP code
        # and no 2FA seed. The server still caches SHA256(TOTP:password) so /auth
        # validates; the client derives the same hash from the 2FA secret saved in
        # its Settings (the out-of-band "second piece of the puzzle").
        url_only_pair_mode: bool = bool(
            authenticator_pair_code_mode
            and self._is_url_only_pair_mode
            and self._is_url_only_pair_mode()
        )

        async def _ensure_auth() -> bool:
            if not self._ensure_auth_server_running:
                return True
            try:
                result = await self._ensure_auth_server_running()
                # Accept thread/object return types as success, only explicit False fails.
                return result is not False
            except Exception as e:
                LOGGER.warning("Failed to ensure auth server in /pair flow: %s", e)
                return False

        async def _wait_for_public_url(timeout_seconds: float = 15.0) -> Optional[str]:
            """Wait briefly until tunnelmole reports a non-empty public URL."""
            deadline = time.time() + timeout_seconds
            while time.time() < deadline:
                try:
                    status = self._get_tunnelmole_status() if self._get_tunnelmole_status else {}
                    if status.get("status") == "running":
                        url = status.get("public_url")
                        if isinstance(url, str) and url.strip():
                            return url.strip()
                except Exception:
                    pass
                await asyncio.sleep(0.4)
            return None

        def _generate_otp_for_mode() -> str:
            """Return an OTP string appropriate for the current pairing mode."""
            if authenticator_pair_code_mode:
                if not self._generate_totp_pair_otp_and_cache:
                    return ""
                code = self._generate_totp_pair_otp_and_cache()
                if not code:
                    LOGGER.warning(
                        "[PAIR] Authenticator pair-code mode active but no TOTP OTP generated "
                        "(no TOTP clients registered?)"
                    )
                return code
            return self._generate_otp_hash_and_cache(5)

        tm_status = self._get_tunnelmole_status()
        tunnel_was_running = isinstance(tm_status, dict) and tm_status.get("status") == "running"

        if not tunnel_was_running:
            # Start service - use the no-timer variant for unmanaged connection mode.
            if unmanaged_connection_mode and self._start_tunnelmole_service_no_timer:
                started = await self._start_tunnelmole_service_no_timer()
            else:
                started = await self._start_tunnelmole_service_with_timer()
            if not started:
                return None, "Tunnelmole service not available."
            tm_status = self._get_tunnelmole_status()

        if not await _ensure_auth():
            return None, "Auth service not available."

        otp = _generate_otp_for_mode()
        if not otp:
            return None, (
                "Authenticator pair-code mode requires a shared "
                "registered TOTP client in Secure Professional mode."
                if authenticator_pair_code_mode
                else "OTP generation failed."
            )

        if tunnel_was_running:
            if not unmanaged_connection_mode:
                # Timed mode: extend the existing timer.
                await self._extend_tunnelmole_timer(None)
            else:
                LOGGER.info(
                    "[PAIR] Unmanaged tunnelmole mode: refreshed pairing code state "
                    "without changing the tunnel lifetime"
                )

        public_url = tm_status.get("public_url") if isinstance(tm_status, dict) else None
        if not public_url:
            public_url = await _wait_for_public_url()
        if url_only_pair_mode:
            # Most-secure share: send only the URL. The client holds the
            # 2FA secret + password and derives SHA256(TOTP:password) itself.
            body: Dict[str, Any] = {"url": public_url if public_url else None}
        else:
            body = {"otp": otp, "url": public_url if public_url else None}
            totp_secret = self._pair_totp_secret_for_response(
                platform,
                sender_id,
                identity_sender_id=identity_sender_id,
            )
            if totp_secret:
                body["totp_secret"] = totp_secret
        return body, None

    async def _handle_pair_hello_command(
        self,
        *,
        text: str,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> str:
        """Handle /pair_hello: A-Tier OTP Pair collapsed into one round trip.

        The client's hello carries no real content (just its CPace share plus
        an optional cleartext authenticator code), and only the server's reply
        does - so the server derives the session key the moment the hello
        arrives and encrypts the {otp, url, ...} body directly into the single
        /pair_hello_answer. The key is used once, here, and discarded: nothing
        is ever stored in the autopair session cache for this flow.
        """
        payload_text = self._extract_command_body(text, "/pair_hello")
        if not payload_text:
            return "Missing /pair_hello payload"

        normalized_line = self._normalize_pairing_text(payload_text)
        if not is_cpace_hello(normalized_line):
            return "Invalid /pair_hello payload"

        mode = self._current_security_mode()
        if mode == "normal":
            return "This server is in Normal mode and doesn't use encrypted pairing. Send /pair instead."

        current_password = self._current_password()
        if not current_password:
            return "Server password not configured"

        try:
            hello = parse_cpace_hello(normalized_line)
        except Exception as exc:
            return f"Invalid /pair_hello payload: {exc}"

        if is_secure_professional_mode(mode):
            allowed, error = await self._verify_autopair_totp_online(
                platform, sender_id, hello.totp_code, identity_sender_id=identity_sender_id
            )
            if not allowed:
                return error or "Authentication failed"

        body, error = await self._prepare_pair_body(
            platform=platform,
            sender_id=sender_id,
            identity_sender_id=identity_sender_id,
        )
        if error or body is None:
            return f"/pair_hello received. {error or 'Pairing helpers not available.'}"

        try:
            answer_env = build_cpace_hello_answer_with_payload(
                hello, current_password, json.dumps(body)
            )
        except Exception as exc:
            LOGGER.warning("CPace pair-hello answer failed for %s:%s: %s", platform, sender_id, exc)
            return "Authentication failed"
        return f"{PAIR_HELLO_ANSWER_PREFIX}{answer_env}"

    # ------------------------------------------------------------------ #
    #  CPace handshake (/autopair_hello) - establishes the session key    #
    #  used by every later /autopair, /autopair_answer, and               #
    #  /autopair_candidates message.                                      #
    # ------------------------------------------------------------------ #

    def _remember_cpace_session(
        self,
        *,
        sender_id: str,
        identity_sender_id: Optional[str],
        session: CPaceSessionKey,
    ) -> None:
        self._evict_expired_cpace_sessions()
        now = time.monotonic()
        for key in self._autopair_context_keys(sender_id, identity_sender_id):
            self._cpace_sessions[key] = (session, now)

    def _get_cpace_session(
        self,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> Optional[CPaceSessionKey]:
        self._evict_expired_cpace_sessions()
        for key in self._autopair_context_keys(sender_id, identity_sender_id):
            entry = self._cpace_sessions.get(key)
            if entry is not None:
                return entry[0]
        return None

    def _evict_expired_cpace_sessions(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, (_session, established_at) in self._cpace_sessions.items()
            if now - established_at > CPACE_SESSION_TTL_SECONDS
        ]
        for key in expired:
            self._cpace_sessions.pop(key, None)

    def _autopair_totp_rate_limit_error(
        self,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> Optional[str]:
        auth_sender_id = self._resolve_auth_sender_id(sender_id, identity_sender_id)
        if self._totp_hello_rate_limit_allowed and not self._totp_hello_rate_limit_allowed(auth_sender_id):
            return "Too many authenticator attempts. Wait a moment and try again."
        return None

    async def _verify_autopair_totp_online(
        self,
        platform: str,
        sender_id: str,
        totp_code: Optional[str],
        *,
        identity_sender_id: Optional[str] = None,
        rate_limit_checked: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Verify a Secure Professional authenticator code live and rate-limit it.

        AutoPair request v2 carries this code inside authenticated ciphertext;
        the relay never receives a readable credential. The code remains outside
        key derivation and is still checked online as an independent factor.
        """
        auth_sender_id = self._resolve_auth_sender_id(sender_id, identity_sender_id)
        if not rate_limit_checked:
            rate_limit_error = self._autopair_totp_rate_limit_error(sender_id, identity_sender_id)
            if rate_limit_error:
                return False, rate_limit_error
        if not totp_code:
            return False, "This server requires an authenticator code to pair."
        secret_entries = self._ordered_totp_secret_entries(platform, auth_sender_id)
        if not secret_entries:
            return False, "Set up an authenticator code for Secure Professional pairing in the admin settings first."
        if not self._verify_totp_code:
            return False, "Authenticator verification is not available right now."
        for _client_id, secret in secret_entries:
            try:
                if self._verify_totp_code(secret, totp_code):
                    return True, None
            except Exception as exc:
                LOGGER.warning(
                    "Authenticator verification raised for %s:%s: %s", platform, auth_sender_id, exc
                )
        return False, "That authenticator code isn't right."

    async def _handle_autopair_hello_command(
        self,
        text: str,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> str:
        """Handle /autopair_hello: the first step of the CPace handshake.

        Nothing in this message is secret or password-derived, so it's safe to
        parse before any authentication. In Secure Professional mode, the
        authenticator code carried alongside it is verified online (rate
        limited) before the server does any CPace work or reveals its share.
        """
        payload_text = self._extract_command_body(text, "/autopair_hello")
        if not payload_text:
            return "Missing /autopair_hello payload"

        normalized_line = self._normalize_pairing_text(payload_text)
        if not is_cpace_hello(normalized_line):
            return "Invalid /autopair_hello payload"

        mode = self._current_security_mode()
        if mode == "normal":
            return (
                "Enhanced /autopair_hello requires Secure or Secure Professional mode. "
                "Use /pair or the normal/plaintext AutoPair flow in Normal mode."
            )

        current_password = self._current_password()
        if not current_password:
            return "Server password not configured"

        try:
            hello = parse_cpace_hello(normalized_line)
        except Exception as exc:
            return f"Invalid /autopair_hello payload: {exc}"

        if is_secure_professional_mode(mode):
            allowed, error = await self._verify_autopair_totp_online(
                platform, sender_id, hello.totp_code, identity_sender_id=identity_sender_id
            )
            if not allowed:
                return error or "Authentication failed"

        try:
            answer_env, session = build_cpace_hello_answer(hello, current_password)
        except Exception as exc:
            LOGGER.warning("CPace hello-answer failed for %s:%s: %s", platform, sender_id, exc)
            return "Authentication failed"

        self._remember_cpace_session(
            sender_id=str(sender_id),
            identity_sender_id=identity_sender_id,
            session=session,
        )
        return f"{AUTOPAIR_HELLO_ANSWER_PREFIX}{answer_env}"

    async def _handle_autopair_command(
        self,
        text: str,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> str:
        """Handle /autopair command by validating hash and returning an answer.

        Normal single-message format:
            /autopair\\n{"hash":"...","offer":{"type":"offer","sdp":"..."},"iceServers":[...]}

        Split-message format (newer Telegram accounts):
            Message 1: /autopair          ← triggers fragment buffering (returns FRAGMENT_CONSUMED)
            Message 2: <payload_part1>    ← appended to buffer via _try_consume_autopair_fragment
            Message 3: <payload_part2>    ← completes buffer, processed via _process_autopair_body
        """
        if not (self._generate_hash and self._get_current_password and self._handle_autopair_offer):
            LOGGER.warning("Autopair helpers not configured; cannot process /autopair")
            return "Internal server error during autopair"

        payload_text = self._extract_command_body(text, "/autopair")
        if not payload_text:
            # No payload body: Telegram split the message at the newline.
            # Start a fragment buffer so subsequent messages from this sender
            # are reassembled instead of being forwarded to the AI.
            self._start_autopair_fragment_buffer(
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
            )
            LOGGER.info(
                "[AUTOPAIR] /autopair received without payload from %s:%s - "
                "Telegram message split detected; buffering continuation messages.",
                platform, sender_id,
            )
            return self.FRAGMENT_CONSUMED

        parsed = await self._parse_autopair_payload(
            payload_text,
            platform=platform,
            sender_id=sender_id,
            identity_sender_id=identity_sender_id,
        )
        if isinstance(parsed, str) and self._is_near_transport_limit(text, platform):
            # Telegram Desktop/macOS can split after the first message already
            # contains "/autopair" plus a truncated payload head. Buffer that
            # head and wait for continuation fragments instead of returning a
            # parse/decrypt error or letting later chunks reach the AI path.
            self._start_autopair_fragment_buffer(
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
                initial_fragment=payload_text,
            )
            LOGGER.info(
                "[AUTOPAIR] Near-limit /autopair payload head received from %s:%s "
                "(message_len=%d, payload_len=%d); buffering continuation messages. "
                "Initial parse error: %s",
                platform,
                sender_id,
                len(text),
                len(payload_text),
                parsed,
            )
            return self.FRAGMENT_CONSUMED

        # Single-message path: full payload present.
        return await self._process_autopair_body(
            payload_text,
            platform=platform,
            sender_id=sender_id,
            identity_sender_id=identity_sender_id,
            parsed_payload=parsed,
        )

    async def _handle_autopair_candidates_command(
        self,
        text: str,
        platform: str,
        sender_id: str,
        identity_sender_id: Optional[str] = None,
    ) -> str:
        payload_text = self._extract_command_body(text, "/autopair_candidates")
        if not payload_text:
            return "Missing /autopair_candidates payload"

        parsed = await self._parse_autopair_payload(
            payload_text,
            platform=platform,
            sender_id=sender_id,
            identity_sender_id=identity_sender_id,
        )
        if isinstance(parsed, str):
            return parsed

        payload, _session = parsed
        if not isinstance(payload, dict):
            return "Field 'candidates' payload must be an object"
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            return "Field 'candidates' must be a list"
        session_id = str(
            payload.get("session_id")
            or payload.get("relay_id")
            or identity_sender_id
            or sender_id
            or ""
        ).strip()
        if not session_id:
            return "Missing session_id for /autopair_candidates"

        await self.add_remote_ice_candidates(session_id, candidates, platform=platform)
        return self.FRAGMENT_CONSUMED

    @staticmethod
    def _split_json_on_crlf_escaped(s: str, max_len: int = 1000) -> list[str]:
        """Split JSON at '\\r\\n' escape boundaries and enforce max chunk length.

        This avoids breaking inside SDP lines and helps clients paste answers.
        """
        if len(s) <= max_len:
            return [s]
        token = "\\r\\n"
        safe_positions: list[int] = []
        search_from = 0
        while True:
            idx = s.find(token, search_from)
            if idx == -1:
                break
            safe_positions.append(idx + len(token))
            search_from = idx + len(token)
        chunks: list[str] = []
        start = 0
        while start < len(s):
            end_limit = min(start + max_len, len(s))
            cut = None
            for pos in reversed(safe_positions):
                if start < pos <= end_limit:
                    cut = pos
                    break
            if cut is None:
                cut = end_limit
            chunks.append(s[start:cut])
            start = cut
        return chunks

    @staticmethod
    def reply_limit(platform: str) -> int:
        return PLATFORM_REPLY_LIMITS.get((platform or "").strip().lower(), 0)

    @staticmethod
    def _compress_autopair_payload(payload: str) -> str:
        compressed = zlib.compress(payload.encode("utf-8"), level=9)
        return AUTOPAIR_COMPRESSED_PREFIX + base64.b64encode(compressed).decode("ascii")

    @staticmethod
    def _base64_variants(encoded: str) -> List[str]:
        compact = (
            (encoded or "")
            .strip()
            .replace("\r", "")
            .replace("\n", "")
            .replace("\t", "")
        )
        variants: List[str] = []

        def add_variant(value: str) -> None:
            if value and value not in variants:
                variants.append(value)

        add_variant(compact)
        if " " in compact:
            add_variant(compact.replace(" ", "+"))
            add_variant(compact.replace(" ", ""))

        for value in list(variants):
            standard = value.replace("-", "+").replace("_", "/")
            add_variant(standard)
            padding = (-len(standard)) % 4
            if padding:
                add_variant(standard + ("=" * padding))

        return variants

    @classmethod
    def _decode_compressed_payload_if_needed(cls, payload_text: str) -> str:
        if not (payload_text or "").startswith(AUTOPAIR_COMPRESSED_PREFIX):
            return payload_text

        encoded = payload_text[len(AUTOPAIR_COMPRESSED_PREFIX):]
        last_error: Optional[Exception] = None
        for candidate in cls._base64_variants(encoded):
            for decoder in (base64.b64decode, base64.urlsafe_b64decode):
                try:
                    compressed = decoder(candidate)
                    decompressor = zlib.decompressobj()
                    decoded = decompressor.decompress(
                        compressed,
                        AUTOPAIR_DECOMPRESSED_MAX_LENGTH + 1,
                    )
                    if len(decoded) > AUTOPAIR_DECOMPRESSED_MAX_LENGTH:
                        raise ValueError("compressed payload exceeds decoded size limit")
                    if decompressor.unconsumed_tail or not decompressor.eof:
                        raise ValueError("compressed payload exceeds decoded size limit")
                    if decompressor.unused_data:
                        raise ValueError("compressed payload has trailing data")
                    remaining = AUTOPAIR_DECOMPRESSED_MAX_LENGTH - len(decoded)
                    if remaining:
                        decoded += decompressor.flush(remaining)
                    if len(decoded) > AUTOPAIR_DECOMPRESSED_MAX_LENGTH:
                        raise ValueError("compressed payload exceeds decoded size limit")
                    return decoded.decode("utf-8")
                except Exception as exc:
                    last_error = exc
        raise ValueError(f"Invalid compressed payload: {last_error}")

    @staticmethod
    def _normalize_pairing_text(text: Optional[str]) -> str:
        normalized = (text or "").translate(PAIRING_TEXT_TRANSLATIONS)
        return normalized.replace("```", "").strip()

    @classmethod
    def _extract_command_body(cls, text: str, command: str) -> Optional[str]:
        sanitized = cls._normalize_pairing_text(text)
        prefix_idx = sanitized.find(command)
        if prefix_idx < 0:
            return None
        body = sanitized[prefix_idx + len(command):].lstrip()
        return body or None

    @staticmethod
    def _maybe_compress_autopair_payload(payload: str) -> str:
        compressed = PairingRouter._compress_autopair_payload(payload)
        return compressed if len(compressed) < len(payload) else payload

    @staticmethod
    def _build_autopair_answer_payload(
        raw_answer: Dict[str, Any],
        response_session_id: str,
    ) -> Dict[str, Any]:
        wrapped_payload = {
            "answer": {
                "type": raw_answer.get("type"),
                "sdp": raw_answer.get("sdp"),
            },
            "session_id": response_session_id,
        }
        for key in AUTOPAIR_ANSWER_METADATA_KEYS:
            value = raw_answer.get(key)
            if isinstance(value, str):
                value = value.strip()
            if value not in (None, ""):
                wrapped_payload[key] = value
        return wrapped_payload

    def _ordered_totp_secret_entries(self, platform: str, sender_id: str) -> List[Tuple[str, str]]:
        secret = self._get_totp_secret_for_sender(platform, sender_id) if self._get_totp_secret_for_sender else None
        normalized_secret = str(secret or "").strip()
        if normalized_secret:
            return [("default", normalized_secret)]

        all_clients: Dict[str, str] = {}
        if self._get_all_totp_secrets:
            try:
                all_clients = self._get_all_totp_secrets() or {}
            except Exception as exc:
                LOGGER.warning("Failed to load secure-professional pairing TOTP secrets: %s", exc)
                return []

        unique_secrets = []
        seen_secrets: set[str] = set()
        for secret_value in all_clients.values():
            normalized_value = str(secret_value or "").strip()
            if not normalized_value or normalized_value in seen_secrets:
                continue
            seen_secrets.add(normalized_value)
            unique_secrets.append(normalized_value)

        if len(unique_secrets) == 1:
            return [("default", unique_secrets[0])]

        if len(unique_secrets) > 1:
            LOGGER.warning(
                "Secure-professional pairing requires one shared TOTP secret, found %d distinct secrets",
                len(unique_secrets),
            )
        return []

    def _encrypt_payload_for_mode(
        self,
        payload_text: str,
        mode: str,
        current_password: Optional[str],
        platform: str,
        sender_id: str,
        *,
        identity_sender_id: Optional[str] = None,
        encryption_password: Optional[Any] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Encrypt an /otp response body with the plain server password.

        secure_professional's TOTP requirement is enforced by the caller
        (_handle_pair_command verifies the trailing code online, rate limited,
        before this is ever called) - it never enters the encryption key here,
        which is what actually delivers the second factor. Previously this
        folded `f"{password}:{totp}"` into the key, which only added ~20 bits
        against an offline attacker; that construction is gone.
        """
        if mode == "normal":
            return payload_text, None
        if not current_password:
            return None, "Server password not configured"
        if not self._aead_encrypt:
            return None, "Pairing encryption is unavailable on the server"
        password_to_use = encryption_password or current_password
        try:
            return self._aead_encrypt(payload_text, password=password_to_use), None
        except Exception as exc:
            LOGGER.warning("Failed to encrypt pairing payload for %s:%s in mode %s: %s", platform, sender_id, mode, exc)
            return None, "Failed to encrypt pairing payload"

    async def _format_otp_response(
        self,
        body: Dict[str, Any],
        platform: str,
        sender_id: str,
        *,
        identity_sender_id: Optional[str] = None,
    ) -> str:
        """Format the /otp response according to security mode."""
        mode = self._current_security_mode()
        current_password = self._current_password()
        pt = json.dumps(body)
        if mode == "normal":
            return f"/otp\n{pt}"
        env, error = self._encrypt_payload_for_mode(
            pt,
            mode,
            current_password,
            platform,
            sender_id,
            identity_sender_id=identity_sender_id,
        )
        if error or env is None:
            return f"/pair command received. {error or 'Unable to encrypt /otp payload.'}"
        return f"/otp\n{env}"

    async def _parse_autopair_payload(
        self,
        line: str,
        platform: str,
        sender_id: str,
        *,
        identity_sender_id: Optional[str] = None,
    ) -> Any:
        """Parse or decrypt the autopair payload JSON based on security mode + tier.

        Returns `(payload_dict, session)` or an error string. `session` is
        `None` in Normal mode, a `CPaceSessionKey` for the A-Tier hello-first
        flow, or a `PakeServerReplyContext` for a B-Tier bare pake1 `/autopair`.

        Security mode is checked FIRST - plaintext payloads are rejected when the
        server is configured for secure or secure_professional mode.
        """
        normalized_line = self._normalize_pairing_text(line)

        mode = self._current_security_mode()

        # Normal mode: accept plaintext JSON (or compressed plaintext) only.
        if mode == "normal":
            try:
                decoded_line = self._decode_compressed_payload_if_needed(normalized_line)
                return json.loads(decoded_line), None
            except Exception as e:
                return f"Invalid JSON format: {e}"

        # A-Tier: this message is encrypted under a session key established via
        # a prior /autopair_hello handshake.
        session = self._get_cpace_session(str(sender_id), identity_sender_id)
        if session is not None:
            if not is_cpace_message(normalized_line):
                return "Secure mode requires an encrypted payload"
            try:
                pt = decrypt_cpace_message(normalized_line, session)
                pt = self._decode_compressed_payload_if_needed(pt)
                return json.loads(pt), session
            except Exception as e:
                return f"Decryption failed: {e}"

        # No A-Tier session exists yet. B-Tier additionally accepts a
        # bare pake1-encrypted /autopair with no prior handshake - the server's
        # Tier setting decides whether this fallback is offered at all.
        if is_pake_request(normalized_line):
            if self._get_security_tier() != SECURITY_TIER_B:
                return "This server requires the enhanced pairing handshake - send /autopair_hello first."
            current_password = self._current_password()
            if current_password is None:
                return "Server password not configured"
            rate_limit_checked = False
            if is_secure_professional_mode(mode):
                rate_limit_error = self._autopair_totp_rate_limit_error(
                    sender_id,
                    identity_sender_id,
                )
                if rate_limit_error:
                    return rate_limit_error
                rate_limit_checked = True
            try:
                pt, pake_context = decrypt_pake_request(normalized_line, current_password)
                if is_secure_professional_mode(mode):
                    allowed, error = await self._verify_autopair_totp_online(
                        platform,
                        sender_id,
                        pake_context.totp_code,
                        identity_sender_id=identity_sender_id,
                        rate_limit_checked=rate_limit_checked,
                    )
                    if not allowed:
                        return error or "Authentication failed"
                pt = self._decode_compressed_payload_if_needed(pt)
                return json.loads(pt), pake_context
            except Exception as e:
                return f"Decryption failed: {e}"

        return "No active secure pairing session. Send /autopair_hello first."

    async def _format_autopair_answer(
        self,
        answer: Dict[str, Any],
        platform: str,
        sender_id: str,
        session: Optional[Any] = None,
        *,
        identity_sender_id: Optional[str] = None,
    ) -> str:
        """Format /autopair_answer with optional encryption.

        `session` is a `CPaceSessionKey` (A-Tier) or a `PakeServerReplyContext`
        (B-Tier) - whichever `_parse_autopair_payload` returned for this exchange.
        """
        response_session_id = str(identity_sender_id or sender_id or "").strip() or str(sender_id or "").strip()
        raw_answer = dict(answer or {})

        mode = self._current_security_mode()

        wrapped_payload = self._build_autopair_answer_payload(raw_answer, response_session_id)
        response_json = json.dumps(wrapped_payload, separators=(",", ":"))
        payload_text = self._maybe_compress_autopair_payload(response_json)
        if isinstance(session, CPaceSessionKey):
            try:
                env = encrypt_cpace_message(payload_text, session)
            except Exception as exc:
                LOGGER.warning("Failed to encrypt pairing response for %s:%s: %s", platform, sender_id, exc)
                return "Failed to encrypt pairing response"
        elif isinstance(session, PakeServerReplyContext):
            try:
                env = encrypt_pake_response(payload_text, session)
            except Exception as exc:
                LOGGER.warning("Failed to encrypt B-Tier pairing response for %s:%s: %s", platform, sender_id, exc)
                return "Failed to encrypt pairing response"
        elif mode == "normal":
            env = payload_text
        else:
            return "No active secure pairing session"
        reply = f"{AUTOPAIR_ANSWER_PREFIX}{env}"
        LOGGER.info(
            "Formatted /autopair_answer for %s:%s raw_len=%d payload_len=%d compressed=%s "
            "mode=%s response_session_id=%s reply_len=%d",
            platform,
            sender_id,
            len(response_json),
            len(payload_text),
            str(payload_text != response_json),
            mode,
            response_session_id,
            len(reply),
        )
        return reply

    def format_autopair_candidates(
        self,
        session_id: str,
        candidates: List[Dict[str, Any]],
        *,
        platform: str = "cloud",
        sender_id: Optional[str] = None,
        identity_sender_id: Optional[str] = None,
        auth_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        token = None
        if auth_profile is not None:
            token = self._auth_profile.set(self._validated_auth_profile(auth_profile))
        try:
            return self._format_autopair_candidates_with_current_profile(
                session_id,
                candidates,
                platform=platform,
                sender_id=sender_id,
                identity_sender_id=identity_sender_id,
            )
        finally:
            if token is not None:
                self._auth_profile.reset(token)

    def _format_autopair_candidates_with_current_profile(
        self,
        session_id: str,
        candidates: List[Dict[str, Any]],
        *,
        platform: str = "cloud",
        sender_id: Optional[str] = None,
        identity_sender_id: Optional[str] = None,
    ) -> str:
        """Format trickle ICE candidates using the current pairing security mode."""
        target = str(sender_id or session_id or "").strip()
        payload = {
            "session_id": str(session_id or "").strip(),
            "relay_id": str(session_id or "").strip(),
            "candidates": candidates or [],
        }
        payload_text = self._maybe_compress_autopair_payload(
            json.dumps(payload, separators=(",", ":"))
        )
        mode = self._current_security_mode()

        if mode == "normal":
            return f"{AUTOPAIR_CANDIDATES_PREFIX}{payload_text}"

        session = self._get_cpace_session(target, identity_sender_id)
        if session is None:
            raise RuntimeError("No active secure pairing session for /autopair_candidates")
        try:
            return f"{AUTOPAIR_CANDIDATES_PREFIX}{encrypt_cpace_message(payload_text, session)}"
        except Exception as exc:
            raise RuntimeError(f"Unable to encrypt /autopair_candidates: {exc}") from exc

# Global singleton for simple import usage
pairing_router = PairingRouter()
