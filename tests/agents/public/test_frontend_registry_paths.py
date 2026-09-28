# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-E-652076696120284254432061-429951b4507589e1db5a4d07

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_e__ = "AUTOYOU-PROVENANCE-E-652076696120284254432061-429951b4507589e1db5a4d07"



def test_default_frontend_registry_path_respects_autoyou_test_root(tmp_path, monkeypatch):
    from autoyou_agents.shared_tools.frontend_registry import get_frontend_registry_path

    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(runtime_root))

    registry_path = get_frontend_registry_path()

    assert registry_path == (runtime_root / "AutoYou" / "agent_frontends_registry.json").resolve()
