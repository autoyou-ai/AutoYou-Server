"""The generated reference pages must match the source they are generated from.

The route catalog, the agent pages, and the shared-tools page are produced by
``scripts/generate_reference_docs.py`` with the ``ast`` module only: nothing is
imported or started, so this test reads source files and writes nothing.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "generate_reference_docs.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_reference_docs", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_generated_reference_pages_are_current():
    generator = _load_generator()
    stale = []
    for path, expected in generator.outputs().items():
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else ""
        if current != expected:
            stale.append(path.relative_to(REPO_ROOT).as_posix())
    assert not stale, (
        "Generated reference pages are out of date: "
        + ", ".join(stale)
        + ". Run `python scripts/generate_reference_docs.py` and commit the result."
    )


def test_every_agent_is_assigned_to_a_domain_page():
    generator = _load_generator()
    assigned = [name for *_, names in generator.AGENT_DOMAINS for name in names]
    on_disk = {
        path.name
        for path in generator.AGENTS_DIR.iterdir()
        if path.is_dir() and not path.name.startswith("_") and (path / "agent.py").exists()
    }
    assert sorted(set(assigned) ^ on_disk) == []
    assert len(assigned) == len(set(assigned))
