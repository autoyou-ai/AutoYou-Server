# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-64ca223ded415c38fb646f8e


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-2620656d61696c206c656761-64ca223ded415c38fb646f8e"

import os
import tempfile
import unittest
from pathlib import Path

from autoyou_agents.agent_builder_agent import agent as builder_agent
from autoyou_agents.coding_agent.agent import get_pending_builder_handoff
from autoyou_agents.shared_tools.agent_install_registry import load_agent_install_registry
from autoyou_agents.shared_tools.coding_handoff import CODING_HANDOFF_STATE_KEY


_PACKAGED_RUNTIME_ENV = "AUTOYOU_PACKAGED_RUNTIME"
_PACKAGED_RESOURCES_ROOT_ENV = "AUTOYOU_PACKAGED_RESOURCES_ROOT"


class _FakeActions:
    def __init__(self):
        self.transfer_to_agent = None
        self.skip_summarization = None


class _FakeToolContext:
    def __init__(self):
        self.state = {}
        self.actions = _FakeActions()


class BuilderCodingHandoffTest(unittest.TestCase):
    def _force_source_runtime_for_test(self):
        previous_packaged_runtime = os.environ.get(_PACKAGED_RUNTIME_ENV)
        previous_resources_root = os.environ.get(_PACKAGED_RESOURCES_ROOT_ENV)
        os.environ.pop(_PACKAGED_RUNTIME_ENV, None)
        os.environ.pop(_PACKAGED_RESOURCES_ROOT_ENV, None)

        def restore_runtime_env():
            if previous_packaged_runtime is None:
                os.environ.pop(_PACKAGED_RUNTIME_ENV, None)
            else:
                os.environ[_PACKAGED_RUNTIME_ENV] = previous_packaged_runtime
            if previous_resources_root is None:
                os.environ.pop(_PACKAGED_RESOURCES_ROOT_ENV, None)
            else:
                os.environ[_PACKAGED_RESOURCES_ROOT_ENV] = previous_resources_root

        self.addCleanup(restore_runtime_env)

        # Clearing the env vars is not sufficient: is_compiled() also consults
        # builtins.__compiled__, sys.frozen, and the executable layout, any of
        # which another test in a full run can leave looking "packaged". Pin it
        # to False so this source-mode test is deterministic regardless of order.
        # builder_agent and the install registry import is_compiled locally from
        # shared.platform_runtime, so patching the source module covers them all.
        import unittest.mock
        import shared.platform_runtime as _platform_runtime

        compiled_patcher = unittest.mock.patch.object(
            _platform_runtime, "is_compiled", lambda: False
        )
        compiled_patcher.start()
        self.addCleanup(compiled_patcher.stop)

    def test_patch_root_agent_installs_without_prompt_mutation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            self._force_source_runtime_for_test()
            original_root = builder_agent._AGENTS_ROOT
            original_root_prompt_py = builder_agent._ROOT_PROMPT_PY
            original_registry_env = os.environ.get("AUTOYOU_AGENT_INSTALL_REGISTRY_PATH")
            registry_path = Path(tmp_dir) / "agent_install_registry.json"
            builder_agent._AGENTS_ROOT = Path(tmp_dir)
            builder_agent._ROOT_PROMPT_PY = Path(tmp_dir) / "prompt.py"
            os.environ["AUTOYOU_AGENT_INSTALL_REGISTRY_PATH"] = str(registry_path)
            self.addCleanup(setattr, builder_agent, "_AGENTS_ROOT", original_root)
            self.addCleanup(setattr, builder_agent, "_ROOT_PROMPT_PY", original_root_prompt_py)

            def restore_registry_env():
                if original_registry_env is None:
                    os.environ.pop("AUTOYOU_AGENT_INSTALL_REGISTRY_PATH", None)
                else:
                    os.environ["AUTOYOU_AGENT_INSTALL_REGISTRY_PATH"] = original_registry_env

            self.addCleanup(restore_registry_env)

            original_prompt_text = original_root_prompt_py.read_text(encoding="utf-8")
            builder_agent._ROOT_PROMPT_PY.write_text(original_prompt_text, encoding="utf-8")

            demo_agent_dir = builder_agent._AGENTS_ROOT / "demo_agent"
            demo_agent_dir.mkdir(parents=True)
            (demo_agent_dir / "agent.py").write_text("pass\n", encoding="utf-8")
            (demo_agent_dir / "prompt.py").write_text("pass\n", encoding="utf-8")

            result = builder_agent.patch_root_agent("demo", "Demo route")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["patched_files"], [])

            patched_prompt = builder_agent._ROOT_PROMPT_PY.read_text(encoding="utf-8")
            self.assertEqual(patched_prompt, original_prompt_text)

            registry = load_agent_install_registry(agents_root=builder_agent._AGENTS_ROOT)
            self.assertIn("demo_agent", registry["installed_agents"])

    def test_prepare_and_transfer_website_handoff(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_root = builder_agent._AGENTS_ROOT
            builder_agent._AGENTS_ROOT = Path(tmp_dir)
            self.addCleanup(setattr, builder_agent, "_AGENTS_ROOT", original_root)

            agent_dir = Path(tmp_dir) / "weather_agent"
            agent_dir.mkdir(parents=True)
            (agent_dir / "agent.py").write_text("pass\n", encoding="utf-8")
            (agent_dir / "prompt.py").write_text("pass\n", encoding="utf-8")

            tool_context = _FakeToolContext()
            prepared = builder_agent.prepare_website_handoff(
                agent_name="weather",
                description="Weather coding agent",
                tool_name="fetch_weather",
                tool_description="Fetches weather data",
                implementation_brief="Scaffold a browser UI and proxy workflow.",
                constraints="Prefer FastAPI and static assets.",
                testing_requirements="yes - add scaffold tests",
                tool_context=tool_context,
            )
            self.assertEqual(prepared["status"], "success")

            transferred = builder_agent.handoff_to_website_agent(tool_context=tool_context)
            self.assertEqual(transferred["status"], "success")
            self.assertEqual(tool_context.actions.transfer_to_agent, "autoyou_website_agent")
            self.assertTrue(tool_context.actions.skip_summarization)

    def test_prepare_and_transfer_handoff(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_root = builder_agent._AGENTS_ROOT
            builder_agent._AGENTS_ROOT = Path(tmp_dir)
            self.addCleanup(setattr, builder_agent, "_AGENTS_ROOT", original_root)

            agent_dir = Path(tmp_dir) / "weather_agent"
            agent_dir.mkdir(parents=True)
            (agent_dir / "agent.py").write_text("pass\n", encoding="utf-8")
            (agent_dir / "prompt.py").write_text("pass\n", encoding="utf-8")

            tool_context = _FakeToolContext()
            prepared = builder_agent.prepare_coding_handoff(
                agent_name="weather",
                description="Weather coding agent",
                tool_name="fetch_weather",
                tool_description="Fetches weather data",
                implementation_brief="Implement real weather lookup and response formatting.",
                constraints="Prefer lightweight HTTP clients.",
                testing_requirements="yes - add unit tests for the parser",
                frontend_requirement="no",
                tool_context=tool_context,
            )
            self.assertEqual(prepared["status"], "success")
            self.assertIn(CODING_HANDOFF_STATE_KEY, tool_context.state)

            transferred = builder_agent.handoff_to_coding_agent(tool_context=tool_context)
            self.assertEqual(transferred["status"], "success")
            self.assertEqual(tool_context.actions.transfer_to_agent, "autoyou_coding_agent")
            self.assertTrue(tool_context.actions.skip_summarization)

    def test_coding_agent_consumes_and_clears_handoff(self):
        tool_context = _FakeToolContext()
        tool_context.state[CODING_HANDOFF_STATE_KEY] = {"agent_name": "demo_agent"}

        result = get_pending_builder_handoff(tool_context)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["handoff_present"])
        self.assertEqual(result["handoff"]["agent_name"], "demo_agent")
        self.assertIsNone(tool_context.state[CODING_HANDOFF_STATE_KEY])


if __name__ == "__main__":
    unittest.main()
