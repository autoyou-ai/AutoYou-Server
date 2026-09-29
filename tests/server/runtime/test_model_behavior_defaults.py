# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-269d99def564906ca65c1536


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import os
import sys
import unittest
from unittest import mock

from tests.support.paths import ensure_repo_on_path

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-269d99def564906ca65c1536"


ensure_repo_on_path()

from autoyou_agents import model_config


class ModelBehaviorDefaultsTest(unittest.TestCase):
    def test_configure_gemini_model_uses_no_behavior_kwargs_in_none_mode(self):
        captured = {}

        class DummyLiteLlm:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with mock.patch.dict(
            os.environ,
            {
                "GOOGLE_API_KEY": "test-key",
                "GOOGLE_MODEL": "gemini-2.5-flash",
                "GOOGLE_GENAI_USE_VERTEXAI": "false",
            },
            clear=False,
        ):
            with mock.patch.object(model_config, "LiteLlm", DummyLiteLlm):
                with mock.patch.object(model_config, "get_litellm_behavior_kwargs", return_value={}):
                    model_config._configure_gemini_model()

        self.assertEqual(captured.get("model"), "gemini/gemini-2.5-flash")
        self.assertNotIn("temperature", captured)
        self.assertNotIn("top_p", captured)

    def test_configure_gemini_model_applies_behavior_kwargs_when_present(self):
        captured = {}

        class DummyLiteLlm:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        behavior = {
            "temperature": 0.2,
            "top_p": 0.85,
            "repeat_penalty": 1.1,
        }

        with mock.patch.dict(
            os.environ,
            {
                "GOOGLE_API_KEY": "test-key",
                "GOOGLE_MODEL": "gemini-2.5-flash",
                "GOOGLE_GENAI_USE_VERTEXAI": "false",
            },
            clear=False,
        ):
            with mock.patch.object(model_config, "LiteLlm", DummyLiteLlm):
                with mock.patch.object(model_config, "get_litellm_behavior_kwargs", return_value=behavior):
                    model_config._configure_gemini_model()

        self.assertEqual(captured.get("model"), "gemini/gemini-2.5-flash")
        self.assertEqual(captured.get("temperature"), 0.2)
        self.assertEqual(captured.get("top_p"), 0.85)
        self.assertIsNone(captured.get("repeat_penalty"))


    def test_missing_model_behavior_config_uses_accurate_preset(self):
        import server

        original_config = server.STATE.config
        try:
            server.STATE.config = {}
            behavior = model_config.get_litellm_behavior_kwargs()
        finally:
            server.STATE.config = original_config

        self.assertEqual(behavior.get("temperature"), 0.1)
        self.assertEqual(behavior.get("top_p"), 0.9)
        self.assertEqual(behavior.get("repeat_penalty"), 1.1)


class ShowThinkingResolutionTest(unittest.TestCase):
    def setUp(self):
        import server
        self._server = server
        self._original_config = server.STATE.config

    def tearDown(self):
        self._server.STATE.config = self._original_config

    def test_default_config_leaves_show_thinking_unset(self):
        """None (not False) is the untouched sentinel, same as num_ctx - a
        baked-in False would permanently shadow the env var fallback below,
        the same way an explicit user override would."""
        cfg = self._server._default_config()
        self.assertIsNone(cfg["model_behavior"]["show_thinking"])
        self.assertIsNone(cfg["model_behavior"]["thinking_level"])

    def test_default_config_effective_behavior_hides_thinking(self):
        self._server.STATE.config = self._server._default_config()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOYOU_OLLAMA_THINKING", None)
            os.environ.pop("OLLAMA_THINKING", None)
            os.environ.pop("OLLAMA_THINK", None)
            self.assertFalse(model_config._resolve_ollama_thinking_enabled())

    def test_config_show_thinking_true_overrides_env_var_false(self):
        self._server.STATE.config = {"model_behavior": {"show_thinking": True}}
        with mock.patch.dict(os.environ, {"OLLAMA_THINK": "0"}, clear=False):
            self.assertTrue(model_config._resolve_ollama_thinking_enabled())

    def test_config_show_thinking_false_overrides_env_var_true(self):
        self._server.STATE.config = {"model_behavior": {"show_thinking": False}}
        with mock.patch.dict(os.environ, {"OLLAMA_THINK": "1"}, clear=False):
            self.assertFalse(model_config._resolve_ollama_thinking_enabled())

    def test_env_var_fallback_used_when_config_key_unset(self):
        self._server.STATE.config = {"model_behavior": {"show_thinking": None}}
        with mock.patch.dict(os.environ, {"OLLAMA_THINK": "1"}, clear=False):
            self.assertTrue(model_config._resolve_ollama_thinking_enabled())
        with mock.patch.dict(os.environ, {"OLLAMA_THINK": "0"}, clear=False):
            self.assertFalse(model_config._resolve_ollama_thinking_enabled())


class OllamaNumPredictTest(unittest.TestCase):
    """The generation cap has to cover a reasoning trace when there is one.

    gpt-oss:120b spent its 1024-token budget thinking and was cut off mid-JSON
    while writing a tool call; Ollama rejected its own truncated call with an
    HTTP 500 and the whole agent run died.
    """

    def setUp(self):
        self._env = mock.patch.dict(
            os.environ,
            {"AUTOYOU_OLLAMA_NUM_PREDICT": "", "OLLAMA_NUM_PREDICT": ""},
            clear=False,
        )
        self._env.start()
        os.environ.pop("AUTOYOU_OLLAMA_NUM_PREDICT", None)
        os.environ.pop("OLLAMA_NUM_PREDICT", None)

    def tearDown(self):
        self._env.stop()

    def test_non_reasoning_models_keep_the_small_cap(self):
        """ministral-3:8b and friends must be completely unaffected."""
        self.assertEqual(
            model_config._resolve_ollama_num_predict(thinking=False, num_ctx=16384), 1024
        )
        self.assertEqual(model_config._resolve_ollama_num_predict(), 1024)

    def test_reasoning_models_get_room_for_the_trace(self):
        self.assertEqual(
            model_config._resolve_ollama_num_predict(thinking=True, num_ctx=16384), 4096
        )

    def test_cap_is_held_under_a_quarter_of_the_context_window(self):
        self.assertEqual(
            model_config._resolve_ollama_num_predict(thinking=True, num_ctx=8192), 2048
        )
        # Never below the non-reasoning floor, however small the window.
        self.assertEqual(
            model_config._resolve_ollama_num_predict(thinking=True, num_ctx=2048), 1024
        )

    def test_operator_pin_wins_for_every_model(self):
        with mock.patch.dict(os.environ, {"AUTOYOU_OLLAMA_NUM_PREDICT": "777"}, clear=False):
            self.assertEqual(
                model_config._resolve_ollama_num_predict(thinking=True, num_ctx=16384), 777
            )
            self.assertEqual(
                model_config._resolve_ollama_num_predict(thinking=False), 777
            )

    def test_operator_can_still_disable_the_cap(self):
        with mock.patch.dict(os.environ, {"AUTOYOU_OLLAMA_NUM_PREDICT": "0"}, clear=False):
            self.assertIsNone(model_config._resolve_ollama_num_predict(thinking=True))

    def test_non_numeric_override_falls_back_to_the_default(self):
        with mock.patch.dict(os.environ, {"AUTOYOU_OLLAMA_NUM_PREDICT": "lots"}, clear=False):
            self.assertEqual(model_config._resolve_ollama_num_predict(thinking=False), 1024)

    def test_reasoning_is_detected_from_the_resolved_think_option(self):
        """Ollama takes a level string or a bool here, and omits it entirely
        for models without thinking support."""
        for think_option in ("low", "medium", "high", "max", True):
            self.assertTrue(model_config._think_option_enables_reasoning(think_option))
        for think_option in (None, False, ""):
            self.assertFalse(model_config._think_option_enables_reasoning(think_option))


def test_admin_ui_exposes_show_thinking_toggle_in_model_behavior_panel():
    from pathlib import Path

    import server

    repo_root = Path(server.__file__).resolve().parent
    script = (repo_root / "assets" / "admin-ui.js").read_text(encoding="utf-8")
    # from __debug_provenance_v__ import wallet

    assert 'checkbox("modelBehavior.show_thinking", "Show model thinking"' in script
    assert 'setByPath(state.forms, "modelBehavior.show_thinking"' in script
    assert "payload.show_thinking = Boolean" in script
    assert "modelBehavior.thinking_level" in script
    assert "thinking_capability" in script


if __name__ == "__main__":
    unittest.main()
