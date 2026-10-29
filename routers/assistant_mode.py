# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Admin routes for Assistant mode and its wake word.

Every route needs an admin session. Changing Assistant mode (settings,
recordings, training) also needs that admin to be on this computer (localhost),
like the Permissions screen: an admin reaching the page from another device or
through a public link can read the settings and use the wake word, but not
change them. Audio arrives as base64 WAV inside JSON so the admin page can post
it with the same helper it uses everywhere; it is decoded to 16 kHz mono and
bounded before anything else touches it.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import asyncio
import base64
import binascii
import json
import logging
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from shared import assistant_mode as am
from shared import wake_word as ww

LOGGER = logging.getLogger(__name__)

LOCAL_ADMIN_ONLY_MESSAGE = (
    "Assistant mode can only be changed by an admin on this computer. "
    "Open the Admin Page on this computer (localhost) to change it."
)

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_SAMPLE_SECONDS = 4.0
MAX_TEST_SECONDS = 12.0
MAX_TRANSCRIBE_SECONDS = 30.0


def register_routes(admin_app: FastAPI, auth_app: FastAPI, server: Any) -> Dict[str, Callable[..., Any]]:
    # Browser listening also works when this server has no WebRTC runtime.
    browser_wakes = am.WakeArbiter()

    def _require_admin_login(request: Request) -> Optional[JSONResponse]:
        return server._require_api_login(request)

    def _can_change(request: Request) -> bool:
        check = getattr(server, "_request_is_from_this_computer", None)
        return bool(check(request)) if callable(check) else False

    def _require_local_admin(request: Request) -> Optional[JSONResponse]:
        """An admin session from this computer, for anything that changes Assistant mode."""
        refused = server._require_api_login(request)
        if refused is not None:
            return refused
        if not _can_change(request):
            return JSONResponse(
                status_code=403,
                content={"success": False, "error": LOCAL_ADMIN_ONLY_MESSAGE, "local_admin_required": True},
            )
        return None

    def _error(status: int, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"success": False, "error": message})

    async def _json_body(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
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

    def _decode_audio(body: Dict[str, Any], max_seconds: float) -> Tuple[Optional[bytes], Optional[JSONResponse]]:
        encoded = str(body.get("audio_base64") or "")
        if "," in encoded[:64] and encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            wav = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None, _error(400, "audio_base64 is not valid base64.")
        if not wav:
            return None, _error(400, "No audio was sent.")
        if len(wav) > am.MAX_SAMPLE_UPLOAD_BYTES * 2:
            return None, _error(413, "The recording is too large.")
        try:
            pcm = ww.wav_bytes_to_pcm16(wav)
        except ValueError as exc:
            return None, _error(400, str(exc))
        return pcm[: int(max_seconds * ww.SAMPLE_RATE) * 2], None

    def _server_name() -> str:
        try:
            return str(server.get_configured_server_name())
        except Exception:
            return "AutoYou"

    def _hub() -> Optional[Any]:
        webrtc = getattr(server, "WEBRTC", None)
        return getattr(webrtc, "assistant_mode", None)

    def _browser_listener(body: Dict[str, Any]) -> str:
        value = str(body.get("listener_id") or "")
        return "browser:" + value if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else ""

    def _claim_browser_wake(listener_id: str, *, spoken_at: Optional[float] = None) -> bool:
        arbiter = getattr(_hub(), "wake_arbiter", browser_wakes)
        return arbiter.claim(listener_id, spoken_at=spoken_at)

    async def _after_change() -> None:
        hub = _hub()
        if hub is not None:
            hub.reload()
        webrtc = getattr(server, "WEBRTC", None)
        broadcast = getattr(webrtc, "broadcast_webrtc_capabilities", None)
        if callable(broadcast):
            try:
                await broadcast()
            except Exception as exc:
                LOGGER.debug("Assistant mode capabilities not pushed: %s", exc)

    def _status() -> Dict[str, Any]:
        payload = am.public_status(_server_name(), include_samples=True)
        hub = _hub()
        payload["live"] = hub.snapshot() if hub is not None else {"sessions": {}}
        try:
            capability = server._build_webrtc_capabilities().get("assistant_mode") or {}
        except Exception:
            capability = {}
        payload["available"] = bool(capability.get("available", False))
        payload["voice_training"] = am.voice_training_candidates(payload["wake_phrase"])
        return payload

    @admin_app.get("/api/assistant-mode")
    async def assistant_mode_status(request: Request):
        """Settings, readiness, recordings, Voice Training matches, which devices are listening, and whether this admin may change them."""
        refused = _require_admin_login(request)
        if refused is not None:
            return refused
        payload = await asyncio.to_thread(_status)
        payload["editable"] = _can_change(request)
        return server._json_response_no_store({"success": True, **payload})

    @admin_app.post("/api/assistant-mode/settings")
    async def assistant_mode_settings(request: Request):
        """Turn Assistant mode on or off; choose the wake word, how it is confirmed, sensitivity and what devices may do. This computer only."""
        refused = _require_local_admin(request)
        if refused is not None:
            return refused
        body, error = await _json_body(request)
        if error is not None:
            return error
        allowed = {key: body[key] for key in am.DEFAULT_SETTINGS if key in body and key != "updated_at"}
        await asyncio.to_thread(am.save_settings, allowed)
        await _after_change()
        return server._json_response_no_store({"success": True, **await asyncio.to_thread(_status), "editable": True})

    @admin_app.post("/api/assistant-mode/samples")
    async def assistant_mode_add_sample(request: Request):
        """Store one recording of the wake word (base64 WAV), after checking its length and level. This computer only."""
        refused = _require_local_admin(request)
        if refused is not None:
            return refused
        body, error = await _json_body(request)
        if error is not None:
            return error
        pcm, error = _decode_audio(body or {}, MAX_SAMPLE_SECONDS)
        if error is not None:
            return error
        result = await asyncio.to_thread(
            am.add_sample, pcm or b"", source="admin_web", phrase=str((body or {}).get("wake_phrase") or "")
        )
        return server._json_response_no_store({"success": bool(result.get("stored")), "sample": result})

    @admin_app.delete("/api/assistant-mode/samples")
    async def assistant_mode_clear_samples(request: Request):
        """Delete every stored wake word recording. This computer only."""
        refused = _require_local_admin(request)
        if refused is not None:
            return refused
        removed = await asyncio.to_thread(am.clear_samples)
        return server._json_response_no_store({"success": True, "removed": removed})

    @admin_app.delete("/api/assistant-mode/samples/{sample_id}")
    async def assistant_mode_delete_sample(sample_id: str, request: Request):
        """Delete one wake word recording. This computer only."""
        refused = _require_local_admin(request)
        if refused is not None:
            return refused
        removed = await asyncio.to_thread(am.delete_sample, sample_id)
        if not removed:
            return _error(404, "No such recording.")
        return server._json_response_no_store({"success": True})

    @admin_app.get("/api/assistant-mode/samples/{sample_id}/audio")
    async def assistant_mode_sample_audio(sample_id: str, request: Request):
        """Play back one wake word recording as WAV."""
        refused = _require_admin_login(request)
        if refused is not None:
            return refused
        pcm = await asyncio.to_thread(am.read_sample_pcm, sample_id)
        if not pcm:
            return _error(404, "No such recording.")
        return Response(
            content=ww.pcm16_to_wav_bytes(pcm),
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    @admin_app.post("/api/assistant-mode/train")
    async def assistant_mode_train(request: Request):
        """Train the wake word from the recordings and, when enabled, Voice Training clips. This computer only."""
        refused = _require_local_admin(request)
        if refused is not None:
            return refused
        settings = await asyncio.to_thread(am.load_settings)
        phrase = am.effective_phrase(settings, _server_name())
        try:
            result = await asyncio.to_thread(
                am.train,
                phrase=phrase,
                use_voice_training=bool(settings.get("use_voice_training_clips", True)),
            )
        except ValueError as exc:
            return _error(400, str(exc))
        await _after_change()
        status = await asyncio.to_thread(_status)
        status["editable"] = True
        return server._json_response_no_store({"success": True, **result, "status": status})

    @admin_app.delete("/api/assistant-mode/model")
    async def assistant_mode_delete_model(request: Request):
        """Forget the trained wake word. This computer only."""
        refused = _require_local_admin(request)
        if refused is not None:
            return refused
        await asyncio.to_thread(am.delete_model)
        await _after_change()
        return server._json_response_no_store({"success": True})

    @admin_app.get("/api/assistant-mode/model")
    async def assistant_mode_model(request: Request):
        """The full model, for detectors that run in the browser or on a device."""
        refused = _require_admin_login(request)
        if refused is not None:
            return refused
        model = await asyncio.to_thread(am.load_model)
        if model is None:
            return _error(404, "No wake word has been trained yet.")
        settings = await asyncio.to_thread(am.load_settings)
        model.sensitivity = settings["sensitivity"]
        payload = model.to_dict()
        payload["listen_seconds"] = settings["listen_seconds"]
        return server._json_response_no_store({"success": True, "model": payload})

    @admin_app.post("/api/assistant-mode/test")
    async def assistant_mode_test(request: Request):
        """Say whether a recording would wake the assistant, with the match score and transcript."""
        refused = _require_admin_login(request)
        if refused is not None:
            return refused
        body, error = await _json_body(request)
        if error is not None:
            return error
        pcm, error = _decode_audio(body or {}, MAX_TEST_SECONDS)
        if error is not None:
            return error
        settings = await asyncio.to_thread(am.load_settings)
        phrase = am.effective_phrase(settings, _server_name())
        model = await asyncio.to_thread(am.load_model)
        result: Dict[str, Any] = {"wake_phrase": phrase, "confirm": settings["confirm"]}
        if model is not None and ww.normalize_phrase(model.phrase) == ww.normalize_phrase(phrase):
            result["acoustic"] = await asyncio.to_thread(ww.evaluate_clip, model, pcm or b"")
        if settings["confirm"] in {"text", "both"} or bool((body or {}).get("transcribe")):
            transcript = await asyncio.to_thread(_transcribe, pcm or b"")
            match = ww.match_phrase(phrase, transcript)
            result["transcript"] = transcript
            result["phrase_match"] = {"matched": match.matched, "score": round(match.score, 3), "command": match.remainder}
        acoustic_ok = bool((result.get("acoustic") or {}).get("detected"))
        text_ok = bool((result.get("phrase_match") or {}).get("matched"))
        confirm = settings["confirm"]
        result["would_wake"] = (
            acoustic_ok if confirm == "voice" else text_ok if confirm == "text" else acoustic_ok and text_ok
        )
        return server._json_response_no_store({"success": True, **result})

    @admin_app.post("/api/assistant-mode/wake")
    async def assistant_mode_wake(request: Request):
        refused = _require_admin_login(request)
        if refused is not None:
            return refused
        body, error = await _json_body(request)
        if error is not None:
            return error
        listener_id = _browser_listener(body or {})
        if not listener_id:
            return _error(400, "A valid listener_id is required.")
        if not (await asyncio.to_thread(am.load_settings))["enabled"]:
            return _error(409, "Assistant mode is off.")
        accepted = _claim_browser_wake(listener_id)
        return server._json_response_no_store({
            "success": True, "accepted": accepted,
            "reason": "wake_word" if accepted else "answered_elsewhere",
        })

    @admin_app.post("/api/assistant-mode/transcribe")
    async def assistant_mode_transcribe(request: Request):
        """Local transcription for a wake heard by the admin page's own detector."""
        refused = _require_admin_login(request)
        if refused is not None:
            return refused
        body, error = await _json_body(request)
        if error is not None:
            return error
        pcm, error = _decode_audio(body or {}, MAX_TRANSCRIBE_SECONDS)
        if error is not None:
            return error
        received_at = time.monotonic()
        spotting = bool((body or {}).get("spot_wake"))
        listener_id = _browser_listener(body or {})
        if spotting and not listener_id:
            return _error(400, "A valid listener_id is required.")
        settings = await asyncio.to_thread(am.load_settings)
        if not settings["enabled"]:
            return _error(409, "Assistant mode is off.")
        phrase = am.effective_phrase(settings, _server_name())
        transcript = await asyncio.to_thread(_transcribe, pcm or b"")
        if await asyncio.to_thread(am.load_settings) != settings:
            return server._json_response_no_store({
                "success": True, "transcript": "", "matched": False, "command": "",
                "reason": "settings_changed", "wake_accepted": False,
            })
        match = ww.match_phrase(phrase, transcript)
        require_phrase = bool((body or {}).get("require_phrase"))
        command = match.remainder if match.matched else ("" if require_phrase else transcript)
        accepted = True
        if spotting:
            accepted = bool(match.matched and _claim_browser_wake(
                listener_id, spoken_at=received_at - len(pcm or b"") / (2.0 * ww.SAMPLE_RATE)))
            if not accepted:
                command = ""
        return server._json_response_no_store({
            "success": True,
            "transcript": transcript,
            "matched": match.matched,
            "phrase_score": round(match.score, 3),
            "command": command,
            "wake_phrase": phrase,
            "wake_accepted": accepted,
        })

    def _transcribe(pcm: bytes) -> str:
        try:
            return am.local_transcriber()(pcm)
        except Exception as exc:
            LOGGER.warning("Assistant mode transcription failed: %s", exc)
            return ""

    return {
        "assistant_mode_status": assistant_mode_status,
        "assistant_mode_settings": assistant_mode_settings,
        "assistant_mode_add_sample": assistant_mode_add_sample,
        "assistant_mode_train": assistant_mode_train,
        "assistant_mode_test": assistant_mode_test,
        "assistant_mode_transcribe": assistant_mode_transcribe,
        "assistant_mode_wake": assistant_mode_wake,
    }
