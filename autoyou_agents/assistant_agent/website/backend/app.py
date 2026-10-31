# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The Assistant website: Assistant mode in any browser.

A page anyone signed in can leave open on a kiosk, a thin client or a device
with no screen at all. It opens the microphone on load (``web_auto_start``),
waits for the wake word with the same engine the Admin page uses, sends the
question to the Assistant agent like a wake-word turn from a phone, and speaks
the answer with this server's voice.

Access follows the agent website rules: an authenticator code by default
(manifest ``gated``), or open when the owner turns OTP off for this website.
A screenless device cannot type a code, so the owner can hand it a device
link from the Admin page (``/api/assistant-mode/device-link``); the token in
that link becomes this website's session cookie on first load and is dropped
from the address.

Nothing here changes Assistant mode settings: only an admin on this computer
does that, on the Admin page.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode, parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response

from shared.runtime_module_loader import import_autoyou_shared_tools_module

_smc = import_autoyou_shared_tools_module(
    "autoyou_agents.shared_tools.scheduler_mission_control",
    anchor=__file__,
)

LOGGER = logging.getLogger(__name__)

AGENT_NAME = "assistant_agent"
TITLE = "Assistant"
DESCRIPTION = "Say the wake word, ask, and hear the answer."
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

ENGINE_START = "// <autoyou-wake-word-engine v1>"
ENGINE_END = "// </autoyou-wake-word-engine v1>"
DEVICE_TOKEN_PARAM = "device_token"

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_COMMAND_SECONDS = 30.0
MAX_TURN_CHARS = 2000
MAX_SPEAK_CHARS = 600
AI_TURN_TIMEOUT_SECONDS = 180

# Used only when this process has no WebRTC hub (its arbiter is shared with
# phones and the Admin page when there is one).
_FALLBACK_ARBITER: Any = None
# Speech models load on first use (seconds). The first page load warms them,
# once per process, so the first question is answered as fast as the rest.
_WARM_UP_STARTED = False


def _json(payload: Dict[str, Any], status: int = 200) -> Response:
    return _smc._json_response(payload, status_code=status)


def _error(status: int, message: str, **extra: Any) -> Response:
    return _json({"success": False, "error": message, **extra}, status)


def _auth(request: Request) -> Dict[str, Any]:
    return _smc._describe_chat_auth_state(request, AGENT_NAME)


def _require_session(request: Request) -> Optional[Response]:
    """A signed-in visitor of this website (or open access); 401 otherwise."""
    auth = _auth(request)
    if auth.get("authenticated"):
        return None
    return _json({"success": False, "error": "Not authenticated", "auth": auth}, 401)


def _server() -> Any:
    return _smc._runtime_server()


def _server_name() -> str:
    try:
        return str(_server().get_configured_server_name() or "AutoYou")
    except Exception:
        return "AutoYou"


def _assistant_mode():
    from shared import assistant_mode as am

    return am


def _wake_word():
    from shared import wake_word as ww

    return ww


def _arbiter() -> Any:
    global _FALLBACK_ARBITER
    hub = getattr(getattr(_server(), "WEBRTC", None), "assistant_mode", None)
    arbiter = getattr(hub, "wake_arbiter", None)
    if arbiter is not None:
        return arbiter
    if _FALLBACK_ARBITER is None:
        _FALLBACK_ARBITER = _assistant_mode().WakeArbiter()
    return _FALLBACK_ARBITER


def _listener(body: Dict[str, Any]) -> str:
    value = str(body.get("listener_id") or "")
    return "web:" + value if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else ""


async def _json_body(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[Response]]:
    raw = await request.body()
    if len(raw) > MAX_JSON_BYTES:
        return None, _error(413, "The request is too large.")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return None, _error(400, "The request is not valid JSON.")
    if not isinstance(body, dict):
        return None, _error(400, "The request must be a JSON object.")
    return body, None


def _decode_pcm(body: Dict[str, Any], max_seconds: float) -> Tuple[Optional[bytes], Optional[Response]]:
    ww = _wake_word()
    encoded = str(body.get("audio_base64") or "")
    if encoded.startswith("data:") and "," in encoded[:64]:
        encoded = encoded.split(",", 1)[1]
    try:
        wav = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None, _error(400, "audio_base64 is not valid base64.")
    if not wav:
        return None, _error(400, "No audio was sent.")
    try:
        pcm = ww.wav_bytes_to_pcm16(wav)
    except ValueError as exc:
        return None, _error(400, str(exc))
    return pcm[: int(max_seconds * ww.SAMPLE_RATE) * 2], None


def wake_word_engine_source() -> str:
    """The Admin page's wake word engine, unchanged, as a standalone script.

    One copy of the engine is pinned to the Python reference by the parity
    vectors; this page reuses it instead of keeping a second port.
    """
    path = Path(_server()._admin_ui_js_file())
    source = path.read_text(encoding="utf-8")
    start = source.index(ENGINE_START)
    end = source.index(ENGINE_END, start)
    body = source[start:end]
    return "(function () {\n" + body + "\nwindow.AutoYouWakeWord = AutoYouWakeWord;\n})();\n"


def assistant_status() -> Dict[str, Any]:
    am = _assistant_mode()
    status = am.public_status(_server_name())
    model = status.get("model") or {}
    voice_ready = bool(model.get("matches_phrase"))
    confirm = status.get("confirm") or "voice"
    if not status.get("enabled"):
        listening = "off"
    elif confirm == "text":
        listening = "text"
    elif voice_ready:
        listening = "voice"
    else:
        listening = "needs_training"
    return {
        "enabled": bool(status.get("enabled")),
        "assistant_name": status.get("assistant_name") or "AutoYou",
        "wake_phrase": status.get("wake_phrase") or "",
        "confirm": confirm,
        "listen_seconds": status.get("listen_seconds") or 8,
        "listening": listening,
        "model_revision": am.model_revision(status),
        "auto_start": bool(status.get("web_auto_start", True)),
        "speak_replies": bool(status.get("web_speak_replies", True)),
        "follow_up": bool(status.get("web_follow_up", True)),
    }


def warm_up_speech(speak: bool) -> None:
    """Load speech recognition (and the voice) before the first question."""
    am, ww = _assistant_mode(), _wake_word()
    try:
        am.local_transcriber()(b"\0\0" * int(0.4 * ww.SAMPLE_RATE))
    except Exception as exc:  # noqa: BLE001 - a warm-up never fails a request
        LOGGER.debug("Assistant website: speech recognition warm-up skipped: %s", exc)
    if speak:
        try:
            synthesize_speech("Ready.")
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Assistant website: voice warm-up skipped: %s", exc)


def start_warm_up(status: Dict[str, Any]) -> bool:
    """Warm the speech models once, in the background, while Assistant mode is on."""
    global _WARM_UP_STARTED
    if _WARM_UP_STARTED or not status.get("enabled"):
        return False
    _WARM_UP_STARTED = True
    asyncio.get_running_loop().run_in_executor(None, warm_up_speech, bool(status.get("speak_replies", True)))
    return True


def _speech_to_wav(path: str) -> Tuple[bytes, str]:
    """Browsers play WAV everywhere; anything else is converted when PyAV can."""
    data = Path(path).read_bytes()
    if data[:4] == b"RIFF":
        return data, "audio/wav"
    try:
        import av  # type: ignore[import-not-found]
        import io

        source = av.open(path)
        target_buffer = io.BytesIO()
        target = av.open(target_buffer, mode="w", format="wav")
        stream = target.add_stream("pcm_s16le", rate=24000)
        stream.layout = "mono"
        resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
        for frame in source.decode(audio=0):
            for out in resampler.resample(frame):
                for packet in stream.encode(out):
                    target.mux(packet)
        for packet in stream.encode(None):
            target.mux(packet)
        target.close()
        source.close()
        return target_buffer.getvalue(), "audio/wav"
    except Exception:
        suffix = Path(path).suffix.lower()
        return data, "audio/aiff" if suffix in {".aif", ".aiff"} else "application/octet-stream"


def synthesize_speech(text: str) -> Optional[Tuple[bytes, str]]:
    """This server's voice for one stretch of a reply; None when speech is off or failed."""
    from shared.voice_messaging import synthesize_voice_reply

    path = synthesize_voice_reply(text)
    if not path:
        return None
    try:
        return _speech_to_wav(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            LOGGER.debug("Could not remove a spoken reply file")


async def ask_assistant(text: str, *, session_id: str, wake: str) -> Dict[str, Any]:
    """Send one question to the AI as an Assistant mode turn and return its answer."""
    import aiohttp

    headers = {"Content-Type": "application/json"}
    token = _smc._get_internal_api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "message": text,
        "user_id": "assistant-website",
        "session_id": session_id,
        "metadata": {
            "assistant_mode": True,
            "source": "assistant_website",
            "assistant_turn": {"text": text, "at": time.time(), "wake": wake, "live_frame": False},
        },
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_smc._get_ai_agent_base_url()}/api/chat",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=AI_TURN_TIMEOUT_SECONDS),
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400 or not isinstance(data, dict):
                message = data.get("error") or data.get("detail") if isinstance(data, dict) else ""
                raise RuntimeError(str(message or f"The AI answered with HTTP {response.status}."))
            return data


def _strip_device_token(request: Request) -> str:
    """The page again without the device token, as a relative address.

    Relative, because behind the page service or the admin port this page
    lives under ``/agent/assistant_agent/`` while this app may see ``/``.
    """
    query = [(k, v) for k, v in parse_qsl(request.url.query, keep_blank_values=True) if k != DEVICE_TOKEN_PARAM]
    return "./" + ("?" + urlencode(query) if query else "")


def _extra_routes(app: FastAPI, agent_name: str) -> None:
    @app.middleware("http")
    async def _device_link(request: Request, call_next):
        """A device link's token becomes the session cookie, then leaves the address."""
        token = request.query_params.get(DEVICE_TOKEN_PARAM) if request.method == "GET" else None
        if not token:
            return await call_next(request)
        response = RedirectResponse(_strip_device_token(request), status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        if _smc._agent_chat_session_is_valid(agent_name, token):
            settings = _smc._get_agent_security_settings(agent_name)
            response.set_cookie(
                _smc._agent_chat_cookie_name(agent_name),
                token,
                httponly=True,
                samesite="lax",
                secure=_smc._cookie_should_be_secure(request),
                max_age=int(settings.get("session_ttl_days") or 30) * 86400,
                path="/",
            )
        return response

    @app.get("/engine/wake-word.js")
    async def wake_word_engine(request: Request):
        """The shared wake word engine for this page's own detector."""
        refused = _require_session(request)
        if refused is not None:
            return refused
        try:
            source = await asyncio.to_thread(wake_word_engine_source)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Assistant website: wake word engine unavailable: %s", exc)
            return _error(503, "The wake word engine is not available on this server.")
        return Response(source, media_type="application/javascript",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/assistant/status")
    async def status(request: Request):
        """Wake word, how it is heard, and this website's start, speech and follow-up settings."""
        refused = _require_session(request)
        if refused is not None:
            return refused
        payload = await asyncio.to_thread(assistant_status)
        start_warm_up(payload)
        auth = _auth(request)
        payload["access"] = "open" if auth.get("auth_mode") == "open" else "otp"
        return _json({"success": True, **payload})

    @app.get("/api/assistant/model")
    async def model(request: Request):
        """The trained wake word for the in-page detector (acoustic templates only)."""
        refused = _require_session(request)
        if refused is not None:
            return refused
        payload = await asyncio.to_thread(_assistant_mode().device_model_payload, _server_name())
        if not payload:
            return _error(404, "No wake word has been trained for the current phrase.")
        return _json({"success": True, "model": payload})

    @app.post("/api/assistant/wake")
    async def wake(request: Request):
        """Claim a wake heard by this page, so only one listener in the room answers."""
        refused = _require_session(request)
        if refused is not None:
            return refused
        body, error = await _json_body(request)
        if error is not None:
            return error
        listener = _listener(body or {})
        if not listener:
            return _error(400, "A valid listener_id is required.")
        if not (await asyncio.to_thread(_assistant_mode().load_settings))["enabled"]:
            return _error(409, "Assistant mode is off.")
        accepted = bool(_arbiter().claim(listener))
        return _json({"success": True, "accepted": accepted,
                      "reason": "wake_word" if accepted else "answered_elsewhere"})

    @app.post("/api/assistant/transcribe")
    async def transcribe(request: Request):
        """Local transcription of a question, or of a short burst when the word is spotted as text."""
        refused = _require_session(request)
        if refused is not None:
            return refused
        body, error = await _json_body(request)
        if error is not None:
            return error
        body = body or {}
        pcm, error = _decode_pcm(body, MAX_COMMAND_SECONDS)
        if error is not None:
            return error
        am, ww = _assistant_mode(), _wake_word()
        received_at = time.monotonic()
        spotting = bool(body.get("spot_wake"))
        listener = _listener(body)
        if spotting and not listener:
            return _error(400, "A valid listener_id is required.")
        settings = await asyncio.to_thread(am.load_settings)
        if not settings["enabled"]:
            return _error(409, "Assistant mode is off.")
        if spotting and settings["confirm"] != "text":
            # The owner's rule: a voice wake word never falls back to transcribing
            # everything said before the word.
            return _error(409, "Spotting the wake word as text is only used with Typed text only.")
        phrase = am.effective_phrase(settings, _server_name())
        try:
            transcript = await asyncio.to_thread(am.local_transcriber(), pcm or b"")
        except Exception as exc:
            LOGGER.warning("Assistant website transcription failed: %s", exc)
            return _error(503, "Speech recognition is not available on this server.")
        if await asyncio.to_thread(am.load_settings) != settings:
            return _json({"success": True, "transcript": "", "matched": False, "command": "",
                          "reason": "settings_changed", "wake_accepted": False})
        match = ww.match_phrase(phrase, transcript)
        require_phrase = bool(body.get("require_phrase"))
        command = match.remainder if match.matched else ("" if require_phrase else transcript)
        accepted = True
        if spotting:
            spoken_at = received_at - len(pcm or b"") / (2.0 * ww.SAMPLE_RATE)
            accepted = bool(match.matched and _arbiter().claim(listener, spoken_at=spoken_at))
            if not accepted:
                command = ""
        return _json({"success": True, "transcript": transcript, "matched": match.matched,
                      "command": command, "wake_phrase": phrase, "wake_accepted": accepted})

    @app.post("/api/assistant/turn")
    async def turn(request: Request):
        """Ask the Assistant agent; the answer comes back as text to show and speak."""
        refused = _require_session(request)
        if refused is not None:
            return refused
        body, error = await _json_body(request)
        if error is not None:
            return error
        body = body or {}
        text = " ".join(str(body.get("text") or "").split())[:MAX_TURN_CHARS]
        if not text:
            return _error(400, "Say or type something to ask.")
        session_id = str(body.get("session_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,96}", session_id):
            return _error(400, "A valid session_id is required.")
        wake = str(body.get("wake") or "")[:16]
        if not (await asyncio.to_thread(_assistant_mode().load_settings))["enabled"]:
            return _error(409, "Assistant mode is off.")
        try:
            data = await ask_assistant(text, session_id="assistant-web-" + session_id, wake=wake)
        except asyncio.TimeoutError:
            return _error(504, "The assistant took too long to answer.")
        except Exception as exc:
            LOGGER.warning("Assistant website turn failed: %s", exc)
            return _error(502, "The assistant could not answer right now. Check that AutoYou AI is running.")
        reply = str(data.get("response") or "").strip()
        return _json({"success": True, "reply": reply, "agent_name": str(data.get("agent_name") or "")})

    @app.post("/api/assistant/speak")
    async def speak(request: Request):
        """One stretch of a reply in this server's voice (WAV); 503 when speech is off."""
        refused = _require_session(request)
        if refused is not None:
            return refused
        body, error = await _json_body(request)
        if error is not None:
            return error
        text = " ".join(str((body or {}).get("text") or "").split())[:MAX_SPEAK_CHARS]
        if not text:
            return _error(400, "Nothing to say.")
        try:
            result = await asyncio.to_thread(synthesize_speech, text)
        except Exception as exc:
            LOGGER.warning("Assistant website speech failed: %s", exc)
            result = None
        if not result:
            return _error(503, "Spoken replies are off or not ready on this server.", fallback="browser")
        audio, media_type = result
        return Response(audio, media_type=media_type, headers={"Cache-Control": "no-store"})


app = _smc.create_agent_chat_app(
    agent_name=AGENT_NAME,
    title=TITLE,
    description=DESCRIPTION,
    frontend_dir=FRONTEND_DIR,
    extra_routes_fn=_extra_routes,
)
