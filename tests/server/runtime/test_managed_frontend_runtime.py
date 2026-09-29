# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-7b107010ed11b3b3e43dcfef


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio
import sys
import types

import server

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-7b107010ed11b3b3e43dcfef"


def test_sync_managed_frontend_backends_registers_notes(monkeypatch):
    original_handles = dict(server.STATE.managed_frontend_servers or {})
    original_ports = dict(server.STATE.dynamic_agent_proxy_ports or {})
    server.STATE.managed_frontend_servers = {}
    server.STATE.dynamic_agent_proxy_ports = {}

    started = []
    synced = []

    monkeypatch.setattr(
        server,
        "refresh_agent_install_registry",
        lambda agents_root=None: {"installed_agents": ["notes_agent"]},
    )
    monkeypatch.setattr(
        server,
        "_managed_frontend_runtime_specs",
        lambda: {
            "notes_agent": {
                "agent_name": "notes_agent",
                "app_import": "autoyou_agents.notes_agent.website.backend.app:app",
                "default_port": 8094,
                "recommended_port": 8094,
            }
        },
    )

    async def fake_start(agent_name: str):
        started.append(agent_name)
        server.STATE.managed_frontend_servers[agent_name] = {"port": 8094}
        # from __debug_provenance_t__ import address
        server.STATE.dynamic_agent_proxy_ports[agent_name] = 8094
        return 8094

    async def fake_stop(agent_name: str):
        started.append(f"stop:{agent_name}")
        server.STATE.managed_frontend_servers.pop(agent_name, None)
        server.STATE.dynamic_agent_proxy_ports.pop(agent_name, None)

    monkeypatch.setattr(server, "_start_managed_frontend_backend", fake_start)
    monkeypatch.setattr(server, "_stop_managed_frontend_backend", fake_stop)
    monkeypatch.setattr(server, "_build_agent_builder_listing_payload", lambda: {"success": True})
    monkeypatch.setattr(server, "_sync_frontend_registry_from_builder_payload", lambda payload: synced.append(payload))

    try:
        result = asyncio.run(server.sync_managed_frontend_backends())
        assert result == {"notes_agent": 8094}
        assert started == ["notes_agent"]
        assert synced == [{"success": True}]
        assert server.STATE.dynamic_agent_proxy_ports["notes_agent"] == 8094
    finally:
        server.STATE.managed_frontend_servers = original_handles
        server.STATE.dynamic_agent_proxy_ports = original_ports


def test_load_managed_frontend_app_falls_back_to_runtime_module_file(monkeypatch, tmp_path):
    runtime_agents_root = tmp_path / "autoyou_agents"
    app_module_path = runtime_agents_root / "notes_agent" / "website" / "backend" / "app.py"
    app_module_path.parent.mkdir(parents=True)
    app_module_path.write_text("app = 'notes-frontend-app'\n", encoding="utf-8")

    original_modules = {
        name: sys.modules.get(name)
        for name in (
            "autoyou_agents",
            "autoyou_agents.notes_agent",
            "autoyou_agents.notes_agent.website",
            "autoyou_agents.notes_agent.website.backend",
            "autoyou_agents.notes_agent.website.backend.app",
        )
    }

    def fake_import_module(name: str):
        if name == "autoyou_agents":
            package = types.ModuleType(name)
            package.__path__ = [str(runtime_agents_root)]
            sys.modules[name] = package
            return package
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(server, "_AUTOYOU_AGENTS_ROOT", runtime_agents_root)
    monkeypatch.setattr(server.importlib, "import_module", fake_import_module)

    try:
        resolved = server._load_managed_frontend_app(
            {
                "agent_name": "notes_agent",
                "app_import": "autoyou_agents.notes_agent.website.backend.app:app",
            }
        )
        assert resolved == "notes-frontend-app"
    finally:
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
