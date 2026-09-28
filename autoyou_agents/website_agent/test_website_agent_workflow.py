# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import os
import tempfile
import unittest
from pathlib import Path

from autoyou_agents.coding_agent.agent import get_pending_builder_handoff
from autoyou_agents.website_agent import agent as website_agent
from autoyou_agents.shared_tools.agent_workbench import (
    save_draft_frontend_manifest,
    scaffold_agent_draft,
    scaffold_frontend_draft,
)
from autoyou_agents.shared_tools.coding_handoff import CODING_HANDOFF_STATE_KEY
from autoyou_agents.shared_tools.frontend_manifest import discover_frontend_manifests
from autoyou_agents.shared_tools.frontend_registry import load_frontend_registry
from autoyou_agents.shared_tools.website_handoff import (
    ACTIVE_WEBSITE_CONTEXT_STATE_KEY,
)


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


class WebsiteAgentWorkflowTest(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        compiled_patcher = patch("shared.platform_runtime.is_compiled", return_value=False)
        compiled_patcher.start()
        self.addCleanup(compiled_patcher.stop)

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

    def test_scaffold_website_split_creates_expected_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            self._force_source_runtime_for_test()
            original_root = website_agent._AGENTS_ROOT
            website_agent._AGENTS_ROOT = Path(tmp_dir)
            self.addCleanup(setattr, website_agent, "_AGENTS_ROOT", original_root)
            original_registry_path = os.environ.get("AUTOYOU_FRONTEND_REGISTRY_PATH")
            registry_path = str(Path(tmp_dir) / "agent_frontends_registry.json")
            os.environ["AUTOYOU_FRONTEND_REGISTRY_PATH"] = registry_path
            if original_registry_path is None:
                self.addCleanup(os.environ.pop, "AUTOYOU_FRONTEND_REGISTRY_PATH", None)
            else:
                self.addCleanup(os.environ.__setitem__, "AUTOYOU_FRONTEND_REGISTRY_PATH", original_registry_path)

            agent_dir = Path(tmp_dir) / "demo_agent"
            agent_dir.mkdir(parents=True)
            (agent_dir / "agent.py").write_text("# test scaffold marker\n", encoding="utf-8")

            result = website_agent.scaffold_website_split(
                agent_name="demo",
                ui_purpose="Show a lightweight browser dashboard for the agent.",
                local_port=8091,
                app_title="Demo Agent UI",
            )
            self.assertEqual(result["status"], "success")
            self.assertTrue((agent_dir / "website" / "backend" / "app.py").exists())
            self.assertTrue((agent_dir / "website" / "frontend" / "index.html").exists())
            self.assertTrue((agent_dir / "website" / "manifest.json").exists())

            backend_text = (agent_dir / "website" / "backend" / "app.py").read_text(encoding="utf-8")
            self.assertIn("create_agent_website_app", backend_text)
            self.assertIn("_api_auth_error", backend_text)

            manifest_text = (agent_dir / "website" / "manifest.json").read_text(encoding="utf-8")
            self.assertIn('"frontend_stack": "fastapi_static"', manifest_text)

            html_text = (agent_dir / "website" / "frontend" / "index.html").read_text(encoding="utf-8")
            self.assertIn('<base href="./">', html_text)

            js_text = (agent_dir / "website" / "frontend" / "app.js").read_text(encoding="utf-8")
            self.assertIn("./api/status", js_text)

            readme_text = (agent_dir / "website" / "README.md").read_text(encoding="utf-8")
            self.assertIn("/agent/demo_agent/", readme_text)
            self.assertIn("8091", readme_text)
            self.assertIn("Simple website starter", readme_text)

            discovered = discover_frontend_manifests(
                agents_root=Path(tmp_dir),
                browser_base_url="http://127.0.0.1:8067",
                proxy_ports={"demo_agent": 8091},
            )
            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["agent_name"], "demo_agent")
            self.assertEqual(discovered[0]["launch_path"], "/agent/demo_agent/")
            self.assertEqual(discovered[0]["launch_url"], "http://127.0.0.1:8067/agent/demo_agent/")
            self.assertEqual(discovered[0]["local_url"], "http://127.0.0.1:8091/")

            registry = load_frontend_registry()
            self.assertTrue(any(item.get("agent_name") == "demo_agent" for item in registry.get("frontends", [])))
            self.assertTrue(Path(registry_path).is_file())

    def test_scaffold_website_split_creates_react_typescript_project(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            self._force_source_runtime_for_test()
            original_root = website_agent._AGENTS_ROOT
            website_agent._AGENTS_ROOT = Path(tmp_dir)
            self.addCleanup(setattr, website_agent, "_AGENTS_ROOT", original_root)
            original_registry_path = os.environ.get("AUTOYOU_FRONTEND_REGISTRY_PATH")
            registry_path = str(Path(tmp_dir) / "agent_frontends_registry.json")
            os.environ["AUTOYOU_FRONTEND_REGISTRY_PATH"] = registry_path
            if original_registry_path is None:
                self.addCleanup(os.environ.pop, "AUTOYOU_FRONTEND_REGISTRY_PATH", None)
            else:
                self.addCleanup(os.environ.__setitem__, "AUTOYOU_FRONTEND_REGISTRY_PATH", original_registry_path)

            agent_dir = Path(tmp_dir) / "demo_agent"
            agent_dir.mkdir(parents=True)
            (agent_dir / "agent.py").write_text("# test scaffold marker\n", encoding="utf-8")

            result = website_agent.scaffold_website_split(
                agent_name="demo",
                ui_purpose="Show a browser app for the agent.",
                local_port=8092,
                app_title="Demo React UI",
                frontend_stack="react_typescript",
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["frontend_stack"], "react_typescript")
            self.assertTrue((agent_dir / "website" / "frontend" / "package.json").exists())
            self.assertTrue((agent_dir / "website" / "frontend" / "src" / "App.tsx").exists())
            self.assertTrue((agent_dir / "website" / "frontend" / "dist" / "index.html").exists())
            backend_text = (agent_dir / "website" / "backend" / "app.py").read_text(encoding="utf-8")
            self.assertIn("create_agent_website_app", backend_text)
            self.assertIn('index_path=DIST_DIR / "index.html"', backend_text)
            self.assertIn(
                '"frontend_stack": "react_typescript"',
                (agent_dir / "website" / "manifest.json").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '"build": "tsc --noEmit && vite build"',
                (agent_dir / "website" / "frontend" / "package.json").read_text(encoding="utf-8"),
            )

    def test_agent_studio_draft_scaffold_preserves_frontend_stack(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            agents_root = Path(tmp_dir)
            scaffold_agent_draft(
                "demo",
                "Demo agent",
                "handle_demo",
                "Handle a demo request.",
                agents_root=agents_root,
            )

            scaffold_result = scaffold_frontend_draft(
                "demo",
                ui_purpose="Show a React app for the demo agent.",
                local_port=8093,
                app_title="Demo React",
                frontend_stack="react",
                agents_root=agents_root,
            )

            draft_dir = agents_root / ".drafts" / "demo_agent"
            self.assertEqual(scaffold_result["frontend_stack"], "react_typescript")
            self.assertTrue((draft_dir / "website" / "frontend" / "package.json").exists())

            saved = save_draft_frontend_manifest(
                "demo",
                title="Demo React",
                description="Updated manifest description.",
                recommended_port=8093,
                frontend_stack="react_typescript",
                agents_root=agents_root,
            )

            self.assertEqual(saved["manifest"]["frontend_stack"], "react_typescript")
            self.assertIn(
                '"frontend_stack": "react_typescript"',
                (draft_dir / "website" / "manifest.json").read_text(encoding="utf-8"),
            )

    def test_prepare_and_transfer_coding_handoff_from_website(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_root = website_agent._AGENTS_ROOT
            website_agent._AGENTS_ROOT = Path(tmp_dir)
            self.addCleanup(setattr, website_agent, "_AGENTS_ROOT", original_root)

            agent_dir = Path(tmp_dir) / "demo_agent"
            ui_dir = agent_dir / "website" / "frontend"
            ui_dir.mkdir(parents=True)
            (ui_dir / "index.html").write_text("<!doctype html>\n", encoding="utf-8")

            tool_context = _FakeToolContext()
            tool_context.state[ACTIVE_WEBSITE_CONTEXT_STATE_KEY] = {
                "agent_name": "demo_agent",
                "description": "Demo agent with browser UI",
                "tool_name": "demo_tool",
                "tool_description": "Handles demo workflow",
                "constraints": "Reuse the generated scaffold.",
                "testing_requirements": "yes - verify the browser shell",
            }

            prepared = website_agent.prepare_coding_handoff_from_website(
                agent_name="demo",
                implementation_brief="Implement the actual dashboard and API.",
                local_port=8088,
                tool_context=tool_context,
            )
            self.assertEqual(prepared["status"], "success")
            self.assertIn(CODING_HANDOFF_STATE_KEY, tool_context.state)

            transferred = website_agent.handoff_to_coding_agent(tool_context=tool_context)
            self.assertEqual(transferred["status"], "success")
            self.assertEqual(tool_context.actions.transfer_to_agent, "autoyou_coding_agent")

            consumed = get_pending_builder_handoff(tool_context)
            self.assertEqual(consumed["status"], "success")
            self.assertTrue(consumed["handoff_present"])
            self.assertEqual(consumed["handoff"]["agent_name"], "demo_agent")
            self.assertEqual(consumed["handoff"]["proxy_path"], "/agent/demo_agent/")


if __name__ == "__main__":
    unittest.main()
