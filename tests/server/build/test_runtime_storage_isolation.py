# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import importlib
from pathlib import Path

from autoyou_agents.notes_agent.notes_tool import NotesTool
import server
import service_manager
import session_utils
import shared.platform_runtime as platform_runtime


def _is_within(root: Path, target: str | Path) -> bool:
    root_path = Path(root).resolve()
    target_path = Path(target).resolve()
    return target_path == root_path or root_path in target_path.parents


def test_mutable_runtime_paths_are_redirected_into_pytest_root(autoyou_test_root):
    runtime_paths = [
        platform_runtime.get_user_data_dir("AutoYou"),
        platform_runtime.get_mutable_data_dir("AutoYou", anchor=__file__),
        platform_runtime.get_config_dir("AutoYou", anchor=__file__),
        platform_runtime.get_whisper_cache_dir("AutoYou"),
        platform_runtime.get_dynamic_agents_root("AutoYou", anchor=__file__),
        Path(server.CONFIG_FILE_PATH),
        Path(server.CONFIG_BAK_PATH),
        Path(server.LOGIN_UI_DB_PATH),
    ]

    for runtime_path in runtime_paths:
        assert _is_within(autoyou_test_root, runtime_path), runtime_path


def test_session_manager_defaults_use_isolated_database_paths(autoyou_test_root):
    session_manager = session_utils.MemoryIntegratedSessionManager(
        record_messages=False,
        adk_session_service=object(),
    )
    session_metrics = session_utils.SessionMetrics()
    service_config = service_manager.ServiceConfig()

    assert _is_within(autoyou_test_root, session_manager.db_path)
    assert _is_within(autoyou_test_root, session_metrics.db_path)
    assert _is_within(autoyou_test_root, service_config.db_path)
    assert _is_within(autoyou_test_root, service_config.adk_db_path)
    assert Path(session_manager.db_path).exists()
    assert service_manager._build_adk_db_url(service_config.adk_db_path).startswith("sqlite+aiosqlite:///")


def test_notes_tool_defaults_use_isolated_storage_root(autoyou_test_root):
    tool = NotesTool()

    assert _is_within(autoyou_test_root, tool._storage_dir)
    assert _is_within(autoyou_test_root, tool._db_path)
    assert Path(tool._db_path).exists()


def test_media_generation_agent_defaults_use_isolated_storage_root(autoyou_test_root):
    from autoyou_agents.media_generation_agent import media_generation_tool

    # This module resolves DB_PATH/OUTPUT_DIR at import time, unlike the tools
    # above which resolve theirs per instance. Any earlier test in the session
    # that imports it before this fixture activates bakes in the real user-data
    # path, so reload it here to re-resolve against the isolated root.
    media_generation_tool = importlib.reload(media_generation_tool)

    assert _is_within(autoyou_test_root, media_generation_tool.DB_PATH)
    assert _is_within(autoyou_test_root, media_generation_tool.OUTPUT_DIR)
    assert Path(media_generation_tool.DB_PATH).exists()


def test_voice_training_agent_defaults_use_isolated_storage_root(autoyou_test_root):
    from shared.platform_runtime import get_service_data_dir
    from shared.voice_training_storage import get_voice_training_dir, get_voice_training_storage_info
    vt_dir = get_service_data_dir("voice_training", anchor=__file__)

    assert _is_within(autoyou_test_root, vt_dir)
    assert vt_dir.exists()
    assert _is_within(autoyou_test_root, get_voice_training_dir())
    assert get_voice_training_storage_info()["active_dir"] == str(get_voice_training_dir())


def test_voice_training_custom_storage_path_is_persisted_under_test_root(autoyou_test_root, tmp_path):
    from shared.voice_training_storage import (
        get_voice_training_dir,
        reset_voice_training_dir,
        set_voice_training_dir,
        voice_training_storage_config_path,
    )

    custom_dir = tmp_path / "voice-store"
    storage = set_voice_training_dir(custom_dir)

    assert storage["active_dir"] == str(custom_dir.resolve())
    assert get_voice_training_dir() == custom_dir.resolve()
    assert _is_within(autoyou_test_root, voice_training_storage_config_path())

    reset = reset_voice_training_dir()

    assert _is_within(autoyou_test_root, reset["active_dir"])

