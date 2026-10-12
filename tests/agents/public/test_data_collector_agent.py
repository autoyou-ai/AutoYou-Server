# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-41736462525860c1ea6b78d8

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from io import BytesIO
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from unittest.mock import MagicMock
import zipfile

import pytest
from fastapi.testclient import TestClient
import shared.secure_storage as secure_storage
from shared.secure_storage import FILE_HEADER, disable_secure_storage, enable_secure_storage

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-41736462525860c1ea6b78d8"


def test_data_collector_manifest_uses_the_managed_proxy() -> None:
    manifest_path = Path(__file__).parents[3] / "autoyou_agents" / "data_collector_agent" / "website" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["requires_proxy_registration"] is True


def test_data_collector_owns_the_whatsapp_history_worker() -> None:
    from autoyou_agents.data_collector_agent import messaging
    from autoyou_agents.fine_tuning_agent import fine_tuning_tool

    worker = messaging._whatsapp_worker_path()
    assert worker is not None
    assert worker.parent.name == "data_collector_agent"
    assert fine_tuning_tool._whatsapp_history_worker_path() == worker


def test_data_collector_is_compiled_runtime_agent() -> None:
    import autoyou_agents.agent as root_agent
    from autoyou_agents.data_collector_agent.agent import create_data_collector_agent
    from autoyou_agents.shared_tools.agent_install_registry import (
        BUILTIN_AGENT_PACKAGE_NAMES,
        DEFAULT_AGENT_INSTALL_STATES,
    )

    assert root_agent._STATIC_AGENT_FACTORY_MAP["data_collector_agent"] is create_data_collector_agent
    assert DEFAULT_AGENT_INSTALL_STATES["data_collector_agent"] is False
    assert "data_collector_agent" in BUILTIN_AGENT_PACKAGE_NAMES


def _isolate_collector(monkeypatch, tmp_path: Path):
    from autoyou_agents.data_collector_agent import collector

    data_dir = tmp_path / "runtime" / "data_collector_agent"
    monkeypatch.setattr(collector, "DATA_DIR", data_dir)
    monkeypatch.setattr(collector, "JOB_PATH", data_dir / "collection-job.json")
    monkeypatch.setattr(collector, "OUTPUT_ROOT", data_dir / "collected_context")
    return collector


def _synthetic_job(collector, roots: dict[str, list[str]]) -> dict:
    return collector.normalize_job(
        {
            "machine_id": "synthetic-machine",
            "output_root": str(collector.OUTPUT_ROOT),
            "copy_raw": False,
            "apps": {"codex": True, "claude": True, "chatgpt": True},
            "source_roots": roots,
            "project_filters": [],
            "worktrees": [],
        }
    )


def _write_synthetic_sources(tmp_path: Path) -> dict[str, list[str]]:
    codex_root = tmp_path / "codex"
    claude_root = tmp_path / "claude"
    chatgpt_root = tmp_path / "chatgpt"
    codex_root.mkdir()
    claude_root.mkdir()
    chatgpt_root.mkdir()
    (codex_root / "synthetic.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "synthetic-codex", "cwd": "C:/Synthetic/Project"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "Synthetic Codex prompt"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "assistant_message", "message": "Synthetic Codex reply"}}),
            ]
        ),
        encoding="utf-8",
    )
    (claude_root / "synthetic.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "sessionId": "synthetic-claude", "message": {"content": "Synthetic Claude prompt"}}),
                json.dumps({"type": "assistant", "sessionId": "synthetic-claude", "message": {"content": "Synthetic Claude reply"}}),
            ]
        ),
        encoding="utf-8",
    )
    (chatgpt_root / "conversations.json").write_text(
        json.dumps(
            [
                {
                    "id": "synthetic-chatgpt",
                    "title": "Synthetic ChatGPT conversation",
                    "mapping": {
                        "one": {"message": {"author": {"role": "user"}, "create_time": 1, "content": {"parts": ["Synthetic ChatGPT prompt"]}}},
                        "two": {"message": {"author": {"role": "assistant"}, "create_time": 2, "content": {"parts": ["Synthetic ChatGPT reply"]}}},
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    return {"codex": [str(codex_root)], "claude": [str(claude_root)], "chatgpt": [str(chatgpt_root)]}


def test_collector_builds_training_export_from_supported_sources(monkeypatch, tmp_path: Path) -> None:
    collector = _isolate_collector(monkeypatch, tmp_path)
    # from __debug_provenance_v__ import wallet
    job = _synthetic_job(collector, _write_synthetic_sources(tmp_path))

    result = collector.collect(job)
    exported = collector.create_training_export(job, title="Synthetic export")

    assert result["status"] == "success"
    assert result["parsed"] == 3
    assert result["manifest"]["counts"] == {"sessions": 3, "turns": 6, "pairs": 3}
    assert exported["status"] == "success"
    assert exported["export"]["sample_count"] == 3
    assert "train_path" not in exported["export"]
    stored_manifest = collector.read_json(collector.OUTPUT_ROOT / "index_unified" / "exports_for_training" / exported["export"]["id"] / "manifest.json")
    assert Path(stored_manifest["train_path"]).is_file()
    assert not (collector.OUTPUT_ROOT / "raw").exists()
    archive = zipfile.ZipFile(BytesIO(collector.export_zip(exported["export"]["id"])))
    assert set(archive.namelist()) == {"manifest.json", "train.jsonl"}
    assert "train_path" not in json.loads(archive.read("manifest.json"))


def test_collector_stops_at_a_safe_boundary_when_cancelled(monkeypatch, tmp_path: Path) -> None:
    collector = _isolate_collector(monkeypatch, tmp_path)
    job = _synthetic_job(collector, _write_synthetic_sources(tmp_path))

    result = collector.collect(job, cancel_check=lambda: True)

    assert result["status"] == "cancelled"
    assert result["scanned"] == 0


def test_collector_cancellation_signal_is_shared_through_its_test_scoped_runtime(monkeypatch, tmp_path: Path) -> None:
    collector = _isolate_collector(monkeypatch, tmp_path)
    run_id = collector.begin_collection_run()

    assert collector.request_collection_cancellation()["status"] == "cancelling"
    assert collector.collection_cancellation_requested(run_id) is True

    collector.finish_collection_run(run_id)

    assert collector.request_collection_cancellation()["status"] == "error"


def test_collector_prevents_a_second_process_from_overwriting_the_active_run(monkeypatch, tmp_path: Path) -> None:
    collector = _isolate_collector(monkeypatch, tmp_path)
    run_id = collector.begin_collection_run()

    with pytest.raises(collector.CollectionRunBusy):
        collector.begin_collection_run()

    assert collector.collection_run_status()["state"] == "running"
    collector.finish_collection_run(run_id)


def test_collector_seals_private_export_without_rewriting_source_files(monkeypatch, tmp_path: Path) -> None:
    disable_secure_storage()
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    enable_secure_storage(app_name="AutoYou-test", root=tmp_path, password="synthetic-spm-password")
    collector = _isolate_collector(monkeypatch, tmp_path)
    roots = _write_synthetic_sources(tmp_path)
    source = Path(roots["codex"][0]) / "synthetic.jsonl"
    job = _synthetic_job(collector, roots)
    try:
        collector.collect(job)
        exported = collector.create_training_export(job, title="Synthetic protected export")
        export_dir = collector.OUTPUT_ROOT / "index_unified" / "exports_for_training" / exported["export"]["id"]

        assert (export_dir / "train.jsonl").read_bytes().startswith(FILE_HEADER)
        assert b"Synthetic Codex prompt" not in (export_dir / "train.jsonl").read_bytes()
        assert source.read_bytes().startswith(b'{"type"')
        archive = zipfile.ZipFile(BytesIO(collector.export_zip(exported["export"]["id"])))
        assert b"Synthetic Codex prompt" in archive.read("train.jsonl")
    finally:
        disable_secure_storage()


def test_collector_exports_only_selected_applications(monkeypatch, tmp_path: Path) -> None:
    collector = _isolate_collector(monkeypatch, tmp_path)
    job = _synthetic_job(collector, _write_synthetic_sources(tmp_path))
    collector.collect(job)
    codex_only = collector.normalize_job(
        {**job, "apps": {app: app == "codex" for app in collector.SUPPORTED_APPS}}
    )

    rebuilt = collector.rebuild(codex_only)
    exported = collector.create_training_export(codex_only, title="Synthetic Codex export")

    assert rebuilt["manifest"]["counts"] == {"sessions": 1, "turns": 2, "pairs": 1}
    assert exported["status"] == "success"
    assert exported["export"]["sample_count"] == 1
    assert exported["export"]["filters"]["apps"] == ["codex"]


def test_collecting_one_application_does_not_change_saved_collector_selection(monkeypatch, tmp_path: Path) -> None:
    collector = _isolate_collector(monkeypatch, tmp_path)
    saved = _synthetic_job(collector, _write_synthetic_sources(tmp_path))
    worktree = tmp_path / "private-worktree"
    worktree.mkdir()
    (worktree / ".git").mkdir()
    saved["worktrees"] = [{"name": "private-worktree", "path": str(worktree)}]
    collector.save_job(saved)

    result = collector.collect_application_for_training("codex", title="Synthetic Codex training export")
    after = collector.load_job()

    assert result["status"] == "success"
    assert result["application"] == "codex"
    assert result["collection"]["parsed"] == 1
    assert "manifest" not in result["collection"]
    assert str(worktree) not in json.dumps(result)
    assert result["export"]["filters"]["apps"] == ["codex"]
    assert after["apps"] == saved["apps"]
    assert after["worktrees"] == saved["worktrees"]


def test_data_collector_chat_tool_omits_private_collection_paths(monkeypatch) -> None:
    from autoyou_agents.data_collector_agent import agent

    private_path = r"C:\\synthetic-private-worktree"
    monkeypatch.setattr(
        agent,
        "collect",
        lambda **_kwargs: {
            "status": "success",
            "scanned": 1,
            "parsed": 1,
            "updated": 1,
            "worktrees": [{"path": private_path}],
            "manifest": {"collection": {"worktrees": [{"path": private_path}]}},
            "finished_at": "2026-08-24T00:00:00+00:00",
        },
    )

    result = agent.collect_local_conversations()

    assert result["parsed"] == 1
    assert private_path not in json.dumps(result)
    assert "worktrees" not in result
    assert "manifest" not in result


def test_collector_timeline_has_hierarchy_and_never_returns_message_bodies(monkeypatch, tmp_path: Path) -> None:
    collector = _isolate_collector(monkeypatch, tmp_path)
    job = _synthetic_job(collector, _write_synthetic_sources(tmp_path))
    collector.collect(job)

    timeline = collector.list_timeline(job, app_id="codex", direction="both", limit=10)
    capabilities = collector.application_capabilities(job)

    assert timeline["total"] == 2
    assert [event["direction"] for event in timeline["events"]] == ["sent", "received"]
    assert timeline["events"][0]["machine_id"] == "synthetic-machine"
    assert timeline["events"][0]["target"]["kind"] == "project"
    assert "text" not in timeline["events"][0]
    assert str(tmp_path) not in json.dumps(capabilities)


def test_telegram_saved_messages_collection_requires_consent_and_reads_all_available_history(monkeypatch) -> None:
    from autoyou_agents.data_collector_agent import messaging

    assert not messaging._within_dates("", "2026-01-01", None)
    assert messaging._within_dates("2026-01-01T00:00:00+00:00", "2026-01-01", "2026-01-01")

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        async def iter_messages(self, peer, *, limit, reverse):
            self.calls.append((peer, limit, reverse))
            yield type(
                "Message",
                (),
                {"message": "Synthetic owner reply", "date": datetime(2026, 1, 1, tzinfo=timezone.utc), "out": True, "fwd_from": None},
            )()
            yield type(
                "Message",
                (),
                {"message": "Synthetic incoming note", "date": datetime(2026, 1, 2, tzinfo=timezone.utc), "out": False, "fwd_from": None},
            )()

    class FakeService:
        def __init__(self, consent: bool) -> None:
            self.client = FakeClient()
            self.consent = consent

        def get_status(self):
            return {"owner_scoped": True, "training_export_consent": self.consent}

    denied_service = FakeService(consent=False)
    monkeypatch.setattr(messaging, "_runtime_telegram_service", lambda: denied_service)
    denied = messaging.collect_telegram_saved_messages(machine_id="synthetic-machine", options={"timeout_seconds": 60})
    assert denied["status"] == "error"
    assert denied_service.client.calls == []

    allowed_service = FakeService(consent=True)
    monkeypatch.setattr(messaging, "_runtime_telegram_service", lambda: allowed_service)
    collected = messaging.collect_telegram_saved_messages(machine_id="synthetic-machine", options={"timeout_seconds": 60})

    assert collected["status"] == "success"
    assert allowed_service.client.calls == [("me", None, True)]
    assert [turn["direction"] for turn in collected["sessions"][0]["turns"]] == ["sent", "received"]


def test_inactive_whatsapp_bridge_is_not_restarted_for_collection(monkeypatch) -> None:
    from autoyou_agents.data_collector_agent import messaging

    class InactiveBridge:
        node_process = None
        websocket = None

        async def get_status(self):
            return {"websocket_connected": False, "node_process_running": False, "client_ready": False}

        async def stop(self):
            raise AssertionError("An inactive bridge must not be stopped.")

    monkeypatch.setattr(messaging, "_runtime_whatsapp_service", lambda: InactiveBridge())

    assert messaging._pause_whatsapp_bridge() is False


def test_standalone_web_handoff_is_authenticated_and_one_time(monkeypatch, tmp_path: Path) -> None:
    from autoyou_agents.data_collector_agent.website.backend import app as collector_backend
    from autoyou_agents.shared_tools.localhost_auth import LoopbackTotpAuth, _totp_code

    # Earlier tests may import the root runtime; pin this case to the standalone
    # authentication boundary instead of inheriting that process-global state.
    monkeypatch.setattr(collector_backend, "_server_auth_context", lambda: ({}, None))
    collector = _isolate_collector(monkeypatch, tmp_path)
    job = _synthetic_job(collector, _write_synthetic_sources(tmp_path))
    collector.save_job(job)
    collector.collect(job)
    export = collector.create_training_export(job, title="Synthetic export")["export"]
    secret = "JBSWY3DPEHPK3PXP"
    monkeypatch.setenv("AUTOYOU_DATA_COLLECTOR_TOTP_SECRET", secret)
    monkeypatch.setattr(collector_backend, "_LOCAL_AUTH", LoopbackTotpAuth("data_collector_agent"))
    code = _totp_code(secret, int(time.time() // 30))
    assert code

    client = TestClient(collector_backend.app)
    login = client.post("/api/auth/login", json={"totp_code": code})
    assert login.status_code == 200
    token = login.json()["token"]
    assert client.get("/assets/styles.css").status_code == 200
    handoff = client.post("/api/handoffs", headers={"Authorization": f"Bearer {token}"}, json={"export_id": export["id"]})
    assert handoff.status_code == 200
    timeline = client.get("/api/timeline?app_id=codex", headers={"Authorization": f"Bearer {token}"})
    assert timeline.status_code == 200
    assert timeline.json()["events"]
    assert "text" not in timeline.json()["events"][0]
    exports = client.get("/api/exports", headers={"Authorization": f"Bearer {token}"})
    assert exports.status_code == 200
    assert "train_path" not in json.dumps(exports.json())
    consume = client.post("/api/handoffs/consume", json={"code": handoff.json()["code"]})
    assert consume.status_code == 200
    assert zipfile.is_zipfile(BytesIO(consume.content))
    assert client.post("/api/handoffs/consume", json={"code": handoff.json()["code"]}).status_code == 401


def test_data_collector_ui_requests_a_safe_run_cancellation(monkeypatch) -> None:
    from autoyou_agents.data_collector_agent.website.backend import app as collector_backend

    cancel_event = threading.Event()
    monkeypatch.setattr(collector_backend, "_is_authenticated", lambda request: True)
    monkeypatch.setattr(collector_backend, "request_collection_cancellation", lambda: {"status": "cancelling"})
    monkeypatch.setattr(collector_backend, "_RUN_CANCEL_EVENT", cancel_event)
    monkeypatch.setattr(collector_backend, "_RUN_STATE", {"state": "running", "action": "collect"})

    response = TestClient(collector_backend.app).post("/api/run/cancel")

    assert response.status_code == 202
    assert response.json()["success"] is True
    assert response.json()["run"]["state"] == "cancelling"
    assert cancel_event.is_set() is True


def test_data_collector_ui_can_cancel_an_agent_owned_run(monkeypatch) -> None:
    from autoyou_agents.data_collector_agent.website.backend import app as collector_backend

    monkeypatch.setattr(collector_backend, "_is_authenticated", lambda request: True)
    monkeypatch.setattr(collector_backend, "_RUN_CANCEL_EVENT", None)
    monkeypatch.setattr(collector_backend, "_RUN_STATE", {"state": "idle"})
    monkeypatch.setattr(collector_backend, "request_collection_cancellation", lambda: {"status": "cancelling", "message": "Synthetic cancellation requested."})
    monkeypatch.setattr(collector_backend, "collection_run_status", lambda: {"state": "cancelling", "origin": "agent"})

    response = TestClient(collector_backend.app).post("/api/run/cancel")

    assert response.status_code == 202
    assert response.json()["success"] is True
    assert response.json()["run"]["origin"] == "agent"


def test_data_collector_agent_exposes_a_cancellation_tool() -> None:
    from autoyou_agents.data_collector_agent.agent import create_data_collector_agent

    agent = create_data_collector_agent("synthetic-model")
    tool_names = {getattr(tool, "__name__", getattr(tool, "name", "")) for tool in agent.tools}

    assert "cancel_data_collection" in tool_names


def test_data_collector_uses_server_totp_when_installed(monkeypatch) -> None:
    from autoyou_agents.data_collector_agent.website.backend import app as collector_backend

    mock_server = MagicMock()
    mock_server.STATE.config = {}
    mock_server._default_config.return_value = {}
    mock_server._get_pairing_totp_secret.return_value = "TESTSECRET"
    mock_server._verify_totp_secret.return_value = True
    helpers = {
        "totp_capabilities": lambda: {"totp_configured": True},
        "create_session": lambda agent, days: "test-data-collector-token",
        "session_valid": lambda agent, token: token == "test-data-collector-token",
        "delete_session": MagicMock(),
        "cookie_name": lambda agent: "data_collector_session",
        "cookie_path": lambda agent: "/agent/data_collector_agent",
    }
    monkeypatch.setattr(collector_backend, "_server_auth_context", lambda: (helpers, mock_server))

    response = TestClient(collector_backend.app).post("/api/auth/login", json={"totp_code": "123456"})

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["token"] == "test-data-collector-token"


def test_preserved_legacy_cli_uses_test_scoped_runtime(autoyou_test_root: Path) -> None:
    from autoyou_agents.data_collector_agent.legacy_workflow import _agent_runtime_root

    assert _agent_runtime_root().resolve().is_relative_to(autoyou_test_root)
