# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import os
import sys
import unittest
from unittest import mock

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import autoyou_agents.agent as root_agent_module

from autoyou_agents.agent import _build_resilient_fallback_model


class RootAgentFallbackTest(unittest.TestCase):
    def test_build_resilient_fallback_model_prefixes_ollama_provider(self):
        with mock.patch.dict(
            os.environ,
            {
                "AI_PROVIDER": "ollama",
                "USE_GOOGLE_API": "false",
                "OLLAMA_MODEL": "ministral-3:8b",
                "OLLAMA_API_BASE": "http://127.0.0.1:11434",
            },
            clear=False,
        ):
            model = _build_resilient_fallback_model()

        self.assertEqual(getattr(model, "model", None), "ollama_chat/ministral-3:8b")

    def test_build_resilient_fallback_model_prefixes_gemini_provider(self):
        with mock.patch.dict(
            os.environ,
            {
                "AI_PROVIDER": "google",
                "USE_GOOGLE_API": "true",
                "GOOGLE_MODEL": "gemini-2.5-flash",
            },
            clear=False,
        ):
            os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)
            model = _build_resilient_fallback_model()

        self.assertEqual(model, "gemini/gemini-2.5-flash")

    def test_load_agent_factory_skips_workspace_agents_in_packaged_runtime(self):
        with mock.patch.object(root_agent_module, "is_compiled", return_value=True), mock.patch.object(
            root_agent_module,
            "is_builtin_agent_name",
            return_value=False,
        ), mock.patch.dict(root_agent_module._STATIC_AGENT_FACTORY_MAP, {}, clear=True), mock.patch.object(
            root_agent_module.importlib,
            "import_module",
            side_effect=AssertionError("workspace draft import should be blocked"),
        ):
            self.assertIsNone(root_agent_module._load_agent_factory("custom_agent"))

    def test_load_agent_ingest_callable_skips_workspace_agents_in_packaged_runtime(self):
        with mock.patch.object(root_agent_module, "is_compiled", return_value=True), mock.patch.object(
            root_agent_module,
            "is_builtin_agent_name",
            return_value=False,
        ), mock.patch.object(
            root_agent_module,
            "is_agent_installed",
            return_value=True,
        ), mock.patch.object(
            root_agent_module.importlib,
            "import_module",
            side_effect=AssertionError("workspace draft ingest import should be blocked"),
        ):
            self.assertIsNone(root_agent_module._load_agent_ingest_callable("custom_agent"))


if __name__ == "__main__":
    unittest.main()
