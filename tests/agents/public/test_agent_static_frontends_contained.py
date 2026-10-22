"""Agent website catch-all routes serve files from their frontend folder only.

The auth boundary lets /assets/... through without a session, so the static
route must not follow "..", absolute names or Windows separators out of the
folder; anything else falls back to the page shell.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.responses import FileResponse

from tests.support.paths import REPO_ROOT

BACKENDS = [
    "autoyou_agents.data_collector_agent.website.backend.app",
    "autoyou_agents.fine_tuning_agent.website.backend.app",
    "autoyou_agents.media_generation_agent.website.backend.app",
    "autoyou_agents.notes_agent.website.backend.app",
    "autoyou_agents.persona_agent.website.backend.app",
    "autoyou_agents.voice_training_agent.website.backend.app",
]


def _catch_all(module):
    for route in module.app.routes:
        if getattr(route, "path", "") == "/{full_path:path}":
            return route.endpoint
    raise AssertionError(f"{module.__name__} has no catch-all frontend route")


def _served_file(response) -> Path | None:
    if isinstance(response, FileResponse):
        return Path(response.path).resolve()
    return None


@pytest.mark.parametrize("module_name", BACKENDS)
def test_catch_all_route_stays_inside_the_frontend_folder(module_name: str) -> None:
    module = importlib.import_module(module_name)
    endpoint = _catch_all(module)
    frontend = Path(module.FRONTEND_DIR).resolve()
    index = Path(module.INDEX_HTML_PATH).resolve()
    depth = len(frontend.parts)
    outside = [
        "../" * depth + "etc/hosts",
        "assets/" + "../" * (depth + 1) + "etc/hosts",
        "../agent.py",
        str((REPO_ROOT / "server.py").resolve()),
    ]
    for name in outside:
        served = _served_file(endpoint(name))
        assert served is None or served == index, f"{module_name} served {served} for {name!r}"
