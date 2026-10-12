# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-3b1a59403eddc96cb5666f97

#
# Licensed under the MIT License

"""Tests for voice transcript prefix injection in rest_api.send_message_to_ai_agent()
and the matching prompt instruction in autoyou_agents/prompt.py.

The voice pipeline in server.py sets metadata["source"] = "voice_call" on
transcribed messages, but that dict was never exposed to the LLM - the model
only sees the `parts[0]["text"]` payload.  The fix:
  - rest_api.send_message_to_ai_agent() prepends "[voice transcript] " to the
    message text when voice metadata is detected.
  - prompt.py SPECIAL_POLICIES triggers on the concrete text prefix instead of
    an abstract "message source" that the model cannot read.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import asyncio
import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-3b1a59403eddc96cb5666f97"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine in a temporary event loop."""
    return asyncio.run(coro)


class _FakeResponse:
    """Minimal aiohttp response stub that returns a canned SSE payload."""

    def __init__(self, status=200):
        self.status = status
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def raise_for_status(self):
        pass

    async def iter_any(self):
        # Yield a minimal ADK final-response event so the parser terminates
        chunk = (
            b'event: on_tool_end\n'
            b'data: {"content": {"parts": [{"text": "ok"}]}}\n\n'
        )
        yield chunk


class _FakeSession:
    """Minimal aiohttp.ClientSession stub that captures posted payloads."""

    captured_payloads: list

    def __init__(self, captured_payloads, status=200):
        self.captured_payloads = captured_payloads
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def post(self, url, *, json=None, headers=None):
        self.captured_payloads.append(json)
        return _FakeResponse(self._status)


# ---------------------------------------------------------------------------
# Tests: voice prefix injection logic
# ---------------------------------------------------------------------------

class TestVoicePrefixDetection(unittest.TestCase):
    """Unit-level checks for the is_voice detection and prefix logic."""

    def _simulate_prefix(self, message: str, metadata: dict) -> str:
        """Replicate the prefix logic from rest_api.send_message_to_ai_agent."""
        _meta = metadata or {}
        _is_voice = (
            str(_meta.get("source", "")).lower() in ("voice_call", "voice")
            or bool(_meta.get("is_transcription"))
            or bool(_meta.get("is_voice"))
        )
        return f"[voice transcript] {message}" if _is_voice else message

    # --- positive cases --------------------------------------------------- #

    def test_source_voice_call_adds_prefix(self):
        result = self._simulate_prefix("What time is it?", {"source": "voice_call"})
        self.assertEqual(result, "[voice transcript] What time is it?")

    def test_source_voice_adds_prefix(self):
        result = self._simulate_prefix("Play some music.", {"source": "voice"})
        self.assertEqual(result, "[voice transcript] Play some music.")

    def test_source_voice_call_case_insensitive(self):
        result = self._simulate_prefix("Hi.", {"source": "VOICE_CALL"})
        # from __debug_provenance_n__ import license
        self.assertEqual(result, "[voice transcript] Hi.")

    def test_is_transcription_true_adds_prefix(self):
        result = self._simulate_prefix("Hello.", {"is_transcription": True})
        self.assertEqual(result, "[voice transcript] Hello.")

    def test_is_voice_true_adds_prefix(self):
        result = self._simulate_prefix("Good morning.", {"is_voice": True})
        self.assertEqual(result, "[voice transcript] Good morning.")

    # --- negative cases --------------------------------------------------- #

    def test_text_message_no_prefix(self):
        result = self._simulate_prefix("What is 2+2?", {"source": "chat"})
        self.assertEqual(result, "What is 2+2?")

    def test_empty_metadata_no_prefix(self):
        result = self._simulate_prefix("Hello", {})
        self.assertEqual(result, "Hello")

    def test_none_metadata_no_prefix(self):
        result = self._simulate_prefix("Hello", None)
        self.assertEqual(result, "Hello")

    def test_is_transcription_false_no_prefix(self):
        result = self._simulate_prefix("Hi", {"is_transcription": False})
        self.assertEqual(result, "Hi")

    def test_is_voice_false_no_prefix(self):
        result = self._simulate_prefix("Hi", {"is_voice": False})
        self.assertEqual(result, "Hi")

    def test_source_telegram_no_prefix(self):
        result = self._simulate_prefix("Hello from Telegram", {"source": "telegram"})
        self.assertEqual(result, "Hello from Telegram")

    def test_source_whatsapp_no_prefix(self):
        result = self._simulate_prefix("Hello from WhatsApp", {"source": "whatsapp"})
        self.assertEqual(result, "Hello from WhatsApp")

    # --- edge cases ------------------------------------------------------- #

    def test_voice_prefix_appended_to_existing_text(self):
        msg = "Turn off the lights in the bedroom."
        result = self._simulate_prefix(msg, {"source": "voice_call"})
        self.assertTrue(result.startswith("[voice transcript] "))
        self.assertIn("Turn off the lights in the bedroom.", result)

    def test_prefix_not_double_applied(self):
        # Already-prefixed message should NOT get a second prefix (caller
        # responsibility - but detection only fires on metadata, not content)
        msg = "[voice transcript] repeat this"
        result = self._simulate_prefix(msg, {})  # no voice metadata
        self.assertEqual(result, "[voice transcript] repeat this")

    def test_multiple_voice_metadata_flags_still_one_prefix(self):
        """When both source and is_transcription are set, prefix appears once."""
        result = self._simulate_prefix(
            "Hi",
            {"source": "voice_call", "is_transcription": True},
        )
        self.assertEqual(result.count("[voice transcript]"), 1)


# ---------------------------------------------------------------------------
# Tests: send_message_to_ai_agent payload inspection
# ---------------------------------------------------------------------------

class TestSendMessageToAiAgentVoicePayload(unittest.TestCase):
    """Confirm the injected prefix reaches the ADK payload's parts[0]["text"]."""

    def _call_send(self, message: str, metadata: dict, context=None) -> list:
        """Invoke send_message_to_ai_agent with a mocked aiohttp session and
        return the list of captured JSON payloads."""
        import rest_api  # local import so env is clean

        captured: list = []
        fake_session = _FakeSession(captured)

        async def _run_it():
            with patch("rest_api.aiohttp.ClientSession", return_value=fake_session):
                try:
                    await rest_api.send_message_to_ai_agent(
                        message=message,
                        user_id="u1",
                        session_id="s1",
                        context=context,
                        metadata=metadata,
                        ai_agent_url="http://127.0.0.1:9999",
                    )
                except Exception:
                    # We don't care about downstream ADK parsing; payload
                    # is already captured by the fake session.
                    pass

        _run(_run_it())
        return captured

    def test_voice_call_metadata_prefixes_parts_text(self):
        captured = self._call_send(
            "What time is it?",
            {"source": "voice_call"},
        )
        self.assertTrue(len(captured) > 0, "No payload was captured")
        parts = captured[0]["new_message"]["parts"]
        self.assertTrue(
            parts[0]["text"].startswith("[voice transcript] "),
            f"Expected voice prefix, got: {parts[0]['text']!r}",
        )

    def test_is_transcription_true_prefixes_parts_text(self):
        captured = self._call_send(
            "Turn on the lights.",
            {"is_transcription": True},
        )
        self.assertTrue(len(captured) > 0, "No payload was captured")
        parts = captured[0]["new_message"]["parts"]
        self.assertTrue(
            parts[0]["text"].startswith("[voice transcript] "),
            f"Expected voice prefix, got: {parts[0]['text']!r}",
        )

    def test_regular_message_no_prefix_in_parts(self):
        captured = self._call_send(
            "What is the capital of France?",
            {"source": "chat"},
        )
        self.assertTrue(len(captured) > 0, "No payload was captured")
        parts = captured[0]["new_message"]["parts"]
        self.assertFalse(
            parts[0]["text"].startswith("[voice transcript]"),
            f"Unexpected voice prefix in: {parts[0]['text']!r}",
        )

    def test_no_metadata_no_prefix_in_parts(self):
        captured = self._call_send("Hello!", None)
        self.assertTrue(len(captured) > 0, "No payload was captured")
        parts = captured[0]["new_message"]["parts"]
        self.assertFalse(
            parts[0]["text"].startswith("[voice transcript]"),
        )

    def test_image_bytes_precede_text_in_multimodal_payload(self):
        context = [{
            "source": "admin-web",
            "attachments": [{
                "filename": "synthetic-image.png",
                "mimetype": "image/png",
                "path": "/tmp/synthetic-image.png",
            }],
        }]
        with patch(
            "shared.openclaw_gateway._load_attachment_bytes",
            return_value=(b"synthetic image bytes", None),
        ):
            captured = self._call_send(
                "Describe this image.",
                {"source": "chat"},
                context=context,
            )

        parts = captured[0]["new_message"]["parts"]
        self.assertEqual(parts[0]["inline_data"]["mime_type"], "image/png")
        self.assertEqual(parts[1]["text"], "Describe this image.")


# ---------------------------------------------------------------------------
# Tests: prompt.py constants
# ---------------------------------------------------------------------------

class TestVoicePromptInstruction(unittest.TestCase):
    """Verify that the prompt constants reference the concrete text prefix."""

    def setUp(self):
        # Force a fresh import so edits to prompt.py are reflected
        import autoyou_agents.prompt as _p
        importlib.reload(_p)
        self.prompt = _p

    def test_special_policies_references_prefix_not_metadata(self):
        """SPECIAL_POLICIES must trigger on the text prefix, not on abstract metadata."""
        self.assertIn(
            "[voice transcript]",
            self.prompt.SPECIAL_POLICIES,
            "SPECIAL_POLICIES must reference the concrete '[voice transcript]' prefix",
        )
        self.assertNotIn(
            "message source is 'voice'",
            self.prompt.SPECIAL_POLICIES,
            "Old metadata-based trigger phrase should be removed",
        )

    def test_special_policies_instructs_no_markdown(self):
        p = self.prompt.SPECIAL_POLICIES.lower()
        self.assertIn("markdown", p)
        self.assertIn("bullet", p)

    def test_special_policies_instructs_brief_reply(self):
        p = self.prompt.SPECIAL_POLICIES.lower()
        self.assertIn("brief", p)

    def test_special_policies_instructs_do_not_acknowledge_prefix(self):
        p = self.prompt.SPECIAL_POLICIES.lower()
        self.assertIn("do not acknowledge", p)

    def test_agent_instruction_references_prefix(self):
        self.assertIn(
            "[voice transcript]",
            self.prompt.AGENT_INSTRUCTION,
            "AGENT_INSTRUCTION must contain the '[voice transcript]' prefix trigger",
        )

    def test_agent_instruction_old_phrase_removed(self):
        self.assertNotIn(
            "message source is 'voice'",
            self.prompt.AGENT_INSTRUCTION,
        )

    def test_default_instruction_matches_agent_instruction(self):
        self.assertEqual(
            self.prompt.DEFAULT_INSTRUCTION,
            self.prompt.AGENT_INSTRUCTION,
            "DEFAULT_INSTRUCTION must stay in sync with AGENT_INSTRUCTION",
        )


# ---------------------------------------------------------------------------
# Tests: provider-aware fallback error message
# ---------------------------------------------------------------------------

class TestProviderAwareFallbackMessage(unittest.TestCase):
    """The fallback error message must mention all supported providers."""

    def setUp(self):
        import rest_api as _r
        importlib.reload(_r)
        self._module = _r

    def test_fallback_message_mentions_ollama(self):
        # The string literal is embedded in process_chat_message(); we verify
        # its presence by grepping the module source.
        import inspect
        src = inspect.getsource(self._module)
        self.assertIn("Ollama", src)

    def test_fallback_message_mentions_openclaw(self):
        import inspect
        src = inspect.getsource(self._module)
        self.assertIn("OpenClaw", src)

    def test_fallback_message_mentions_litellm(self):
        import inspect
        src = inspect.getsource(self._module)
        self.assertIn("LiteLLM", src)

    def test_fallback_message_mentions_gemini(self):
        import inspect
        src = inspect.getsource(self._module)
        self.assertIn("Gemini", src)

    def test_fallback_message_mentions_admin_dashboard(self):
        import inspect
        src = inspect.getsource(self._module)
        self.assertIn("Admin Dashboard", src)


if __name__ == "__main__":
    unittest.main()
