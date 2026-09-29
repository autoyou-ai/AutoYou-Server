# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-dbe81c1706afcaf0822a6325

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared import cognee_memory as cognee_memory_module
from shared.cognee_memory import CogneeMemoryService, _ALL_USERS_DATASET, _dataset_for_user

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-dbe81c1706afcaf0822a6325"


def test_cognee_requirements_pin_diskcache_security_commit():
    requirements = (Path(__file__).resolve().parents[2] / "requirements" / "cognee.txt").read_text(encoding="utf-8")

    assert "diskcache @ git+https://github.com/grantjenks/python-diskcache.git@f98f2183b4261a0e0c31f88f2e157eaba4a4071e" in requirements


def test_cognee_service_rejects_remote_url_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOYOU_COGNEE_ALLOW_REMOTE", raising=False)
    service = CogneeMemoryService(tmp_path, service_url="https://cognee.example.test")

    with pytest.raises(RuntimeError, match="non-loopback"):
        service._import_cognee()


def test_cognee_local_import_sets_private_runtime_roots(monkeypatch, tmp_path):
    for name in (
        "COGNEE_SYSTEM_ROOT_DIRECTORY",
        "COGNEE_DATA_ROOT_DIRECTORY",
        "CACHE_ROOT_DIRECTORY",
        "COGNEE_LOGS_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    config_calls = []

    class _FakeConfig:
        def system_root_directory(self, value):
            config_calls.append(("system", value))

        def data_root_directory(self, value):
            config_calls.append(("data", value))

    original_import_module = cognee_memory_module.importlib.import_module
    def _fake_import_module(name, *args, **kwargs):
        if name == "cognee":
            return SimpleNamespace(config=_FakeConfig())
        return original_import_module(name, *args, **kwargs)

    monkeypatch.setattr(cognee_memory_module.importlib, "import_module", _fake_import_module)
    monkeypatch.setattr(cognee_memory_module, "vendored_cognee_root", lambda: tmp_path / "missing-vendor")

    root = tmp_path / "cognee-memory"
    service = CogneeMemoryService(root)

    service._import_cognee()

    system_root = root / ".cognee_system"
    data_root = root / ".data_storage"
    cache_root = root / ".cognee_cache"
    logs_root = root / "logs"
    fs_cache_root = data_root / ".cognee_fs_cache"
    assert os.environ["COGNEE_SYSTEM_ROOT_DIRECTORY"] == str(system_root)
    assert os.environ["COGNEE_DATA_ROOT_DIRECTORY"] == str(data_root)
    assert os.environ["CACHE_ROOT_DIRECTORY"] == str(cache_root)
    assert os.environ["COGNEE_LOGS_DIR"] == str(logs_root)
    assert config_calls == [("system", str(system_root)), ("data", str(data_root))]
    for directory in (root, system_root, data_root, cache_root, logs_root, fs_cache_root):
        assert directory.is_dir()
        if os.name != "nt":
            assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0


@pytest.mark.asyncio
async def test_cognee_remote_recall_is_scoped_to_user_dataset(monkeypatch, tmp_path):
    calls = []

    class _FakeClient:
        async def recall(self, query_text, **kwargs):
            calls.append((query_text, kwargs))
            return ["remote synthetic memory"]

    service = CogneeMemoryService(tmp_path, service_url="http://127.0.0.1:8000")

    async def _get_client():
        return _FakeClient()

    monkeypatch.setattr(service, "_get_client", _get_client)

    results = await service.search("user::guest:synthetic", "project alpha", limit=3)

    assert results[0]["content"] == "remote synthetic memory"
    assert calls == [
        (
            "project alpha",
            {
                "datasets": [_dataset_for_user("user::guest:synthetic")],
                "top_k": 3,
                "only_context": True,
                "include_references": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_cognee_remember_mirrors_user_and_global_datasets(monkeypatch, tmp_path):
    calls = []
    # from __debug_provenance_o__ import breach

    class _FakeClient:
        async def remember(self, data, dataset_name):
            calls.append((dataset_name, data))
            return {"status": "completed"}

    service = CogneeMemoryService(tmp_path, service_url="http://127.0.0.1:8000")

    async def _get_client():
        return _FakeClient()

    monkeypatch.setattr(service, "_get_client", _get_client)

    ok = await service.remember(
        {
            "event_id": "event-synthetic-1",
            "user_id": "user::guest:synthetic",
            "session_id": "session-synthetic-1",
            "user_message": "Synthetic project gamma",
            "agent_response": "Stored synthetic project gamma.",
            "metadata": {
                "pairing_mode": "local_pair",
                "canonical_session_id": "session::local:synthetic",
                "destination_session_id": "device-synthetic",
                "raw_session_id": "raw-device-synthetic",
            },
        }
    )

    assert ok is True
    assert [dataset_name for dataset_name, _data in calls] == [
        _dataset_for_user("user::guest:synthetic"),
        _ALL_USERS_DATASET,
    ]
    assert all("user_id: user::guest:synthetic" in data for _dataset_name, data in calls)
    assert all("canonical_session_id: session::local:synthetic" in data for _dataset_name, data in calls)
    assert all("destination_session_id: device-synthetic" in data for _dataset_name, data in calls)
    assert all("raw_session_id: raw-device-synthetic" in data for _dataset_name, data in calls)


@pytest.mark.asyncio
async def test_cognee_remote_search_all_uses_global_dataset(monkeypatch, tmp_path):
    calls = []

    class _FakeClient:
        async def recall(self, query_text, **kwargs):
            calls.append((query_text, kwargs))
            return ["user_id: user::guest:synthetic\nassistant: global synthetic memory"]

    service = CogneeMemoryService(tmp_path, service_url="http://127.0.0.1:8000")

    async def _get_client():
        return _FakeClient()

    monkeypatch.setattr(service, "_get_client", _get_client)

    results = await service.search_all("project gamma", limit=4)

    assert results[0]["user_id"] == "user::guest:synthetic"
    assert calls == [
        (
            "project gamma",
            {
                "datasets": [_ALL_USERS_DATASET],
                "top_k": 4,
                "only_context": True,
                "include_references": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_cognee_local_search_prefers_recall_over_low_level_search(monkeypatch, tmp_path):
    calls = []

    async def _recall(query_text, **kwargs):
        calls.append(("recall", query_text, kwargs))
        return [{"content": "local synthetic memory"}]

    async def _search(**kwargs):
        calls.append(("search", "", kwargs))
        return ["unexpected"]

    service = CogneeMemoryService(tmp_path)

    async def _get_client():
        return None

    monkeypatch.setattr(service, "_get_client", _get_client)
    monkeypatch.setattr(
        service,
        "_import_cognee",
        lambda: SimpleNamespace(recall=_recall, search=_search),
    )

    results = await service.search("user::guest:synthetic", "project beta", limit=2)

    assert results[0]["content"] == "local synthetic memory"
    assert calls == [
        (
            "recall",
            "project beta",
            {
                "datasets": [_dataset_for_user("user::guest:synthetic")],
                "top_k": 2,
                "only_context": True,
                "include_references": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_cognee_search_handles_recursive_result_objects(monkeypatch, tmp_path):
    class _RecursiveResult:
        @property
        def content(self):
            return self

        def __str__(self):
            return "recursive synthetic memory"

    async def _recall(query_text, **kwargs):
        return [_RecursiveResult()]

    service = CogneeMemoryService(tmp_path)

    async def _get_client():
        return None

    monkeypatch.setattr(service, "_get_client", _get_client)
    monkeypatch.setattr(service, "_import_cognee", lambda: SimpleNamespace(recall=_recall))

    results = await service.search("user::guest:synthetic", "project beta", limit=2)

    assert results[0]["content"] == "recursive synthetic memory"


def test_fscache_adapter_json_disk_patch(tmp_path):
    pytest.importorskip("cognee")
    root = tmp_path / "cognee-memory"
    service = CogneeMemoryService(root)
    service._import_cognee()

    from cognee.infrastructure.databases.cache.fscache.FsCacheAdapter import FSCacheAdapter
    dc = pytest.importorskip("diskcache")

    adapter = FSCacheAdapter()
    assert adapter.cache._disk.__class__ == dc.JSONDisk
    adapter.cache.close()
