# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-21bad6f7c5e04557ad76d954


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import os
import sys
from pathlib import Path

from tests.support.paths import ensure_repo_on_path

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-21bad6f7c5e04557ad76d954"


ensure_repo_on_path()

import server
import ollama_service as ollama_service_module
from ollama_service import OllamaService


def test_apply_google_api_config_to_env_uses_config_as_source_of_truth(monkeypatch):
    original_config = server.STATE.config

    class DummyOllamaService:
        def __init__(self):
            self.reload_calls = 0
            # from __debug_provenance_i__ import or

        def reload_from_env(self):
            self.reload_calls += 1

    dummy_service = DummyOllamaService()
    monkeypatch.setattr(server, "ollama_service", dummy_service)

    monkeypatch.setenv("OLLAMA_API_BASE", "http://stale-host:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:8b")
    monkeypatch.setenv("USE_GOOGLE_API", "true")
    monkeypatch.setenv("GOOGLE_MODEL", "stale-model")
    monkeypatch.setenv("GOOGLE_API_KEY", "stale-key")

    server.STATE.config = {
        "ollama": {
            "api_base": "http://127.0.0.1:11434",
            "model": "ministral-3:8b",
            "use_google_api": False,
            "google_model": "gemini-2.5-flash",
            "google_api_key": "",
        }
    }

    try:
        server._apply_google_api_config_to_env()
    finally:
        server.STATE.config = original_config

    assert os.environ["OLLAMA_API_BASE"] == "http://127.0.0.1:11434"
    assert os.environ["OLLAMA_MODEL"] == "ministral-3:8b"
    assert os.environ["USE_GOOGLE_API"] == "false"
    assert os.environ["GOOGLE_MODEL"] == "gemini-2.5-flash"
    assert os.environ["GOOGLE_API_KEY"] == "NULL"
    assert dummy_service.reload_calls == 1


def test_apply_google_api_config_preserves_packaged_ollama_env_for_default_config(monkeypatch):
    original_config = server.STATE.config

    class DummyOllamaService:
        def __init__(self):
            self.reload_calls = 0

        def reload_from_env(self):
            self.reload_calls += 1

    dummy_service = DummyOllamaService()
    monkeypatch.setattr(server, "ollama_service", dummy_service)
    monkeypatch.setenv("AUTOYOU_PACKAGED_RUNTIME", "1")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:8b")

    server.STATE.config = {
        "ollama": {
            "api_base": "http://localhost:11434",
            "model": server.DEFAULT_WIZARD_MODEL,
            "use_google_api": False,
            "google_model": "gemini-2.5-flash",
            "google_api_key": "",
        }
    }

    try:
        server._apply_google_api_config_to_env()
    finally:
        server.STATE.config = original_config

    assert os.environ["OLLAMA_API_BASE"] == "http://host.docker.internal:11434"
    assert os.environ["OLLAMA_MODEL"] == "ministral-3:8b"
    assert os.environ["AI_PROVIDER"] == "ollama"
    assert dummy_service.reload_calls == 1


def test_apply_google_api_config_keeps_custom_config_in_packaged_runtime(monkeypatch):
    original_config = server.STATE.config

    class DummyOllamaService:
        def reload_from_env(self):
            return None

    monkeypatch.setattr(server, "ollama_service", DummyOllamaService())
    monkeypatch.setenv("AUTOYOU_PACKAGED_RUNTIME", "1")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://host.docker.internal:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:8b")

    server.STATE.config = {
        "ollama": {
            "api_base": "http://ollama:11434",
            "model": "custom-model:latest",
            "use_google_api": False,
            "google_model": "gemini-2.5-flash",
            "google_api_key": "",
        }
    }

    try:
        server._apply_google_api_config_to_env()
    finally:
        server.STATE.config = original_config

    assert os.environ["OLLAMA_API_BASE"] == "http://ollama:11434"
    assert os.environ["OLLAMA_MODEL"] == "custom-model:latest"


def test_ollama_service_reload_from_env_resets_cached_state(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:22434")
    monkeypatch.setenv("OLLAMA_MODEL", "ollama_chat/ministral-3:8b")

    service = OllamaService()
    service.client = object()
    service.ollama_available = True

    service.reload_from_env()

    assert service.api_base == "http://127.0.0.1:22434"
    assert service.default_model == "ministral-3:8b"
    assert service.client is None
    assert service.ollama_available is False


def test_ollama_service_get_client_refreshes_cached_config(monkeypatch):
    created_hosts = []

    class DummyClient:
        def __init__(self, host):
            created_hosts.append(host)
            self.host = host

        def list(self):
            return {"models": [{"model": "ministral-3:8b"}]}

    monkeypatch.setattr(ollama_service_module.ollama, "Client", DummyClient)
    monkeypatch.setenv("OLLAMA_API_BASE", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:8b")

    service = OllamaService()
    service._get_client()

    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:22434")
    monkeypatch.setenv("OLLAMA_MODEL", "ollama_chat/ministral-3:8b")

    service._get_client()

    assert created_hosts == ["http://localhost:11434", "http://127.0.0.1:22434"]
    assert service.api_base == "http://127.0.0.1:22434"
    assert service.default_model == "ministral-3:8b"
    assert service.ollama_available is True


def test_ollama_service_keeps_huggingface_references_intact(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M")

    service = OllamaService()

    assert service.default_model == "hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M"


def test_ensure_local_ollama_runtime_ready_starts_local_runtime_when_unreachable(monkeypatch):
    launches = []
    probes = iter(
        [
            (False, set()),
            (True, {"ministral-3:8b"}),
        ]
    )

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "ollama_chat/ministral-3:8b")
    monkeypatch.setattr(server, "_probe_ollama_runtime", lambda api_base, timeout_seconds=1.5: next(probes))
    monkeypatch.setattr(server.shutil, "which", lambda name: r"C:\Ollama\ollama.exe")
    monkeypatch.setattr(server, "_launch_ollama_background", lambda path: launches.append(path))
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(server, "_OLLAMA_AUTOSTART_LAST_ATTEMPT_AT", 0.0)
    monkeypatch.setattr(server.time, "monotonic", lambda: 100.0)

    server._ensure_local_ollama_runtime_ready()

    assert launches == [r"C:\Ollama\ollama.exe"]


def test_ensure_local_ollama_runtime_ready_starts_bundled_runtime_when_path_missing(monkeypatch):
    launches = []
    bundled_ollama = Path(r"C:\AutoYou\Backend\runtime\ollama\ollama.exe")
    probes = iter(
        [
            (False, set()),
            (True, {"ministral-3:8b"}),
        ]
    )

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:8b")
    monkeypatch.setattr(server, "_probe_ollama_runtime", lambda api_base, timeout_seconds=1.5: next(probes))
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    monkeypatch.setattr(server, "find_bundled_ollama_executable", lambda anchor: bundled_ollama)
    monkeypatch.setattr(server, "_launch_ollama_background", lambda path: launches.append(path))
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(server, "_OLLAMA_AUTOSTART_LAST_ATTEMPT_AT", 0.0)
    monkeypatch.setattr(server.time, "monotonic", lambda: 100.0)

    server._ensure_local_ollama_runtime_ready()

    assert launches == [str(bundled_ollama)]


def test_ensure_local_ollama_runtime_ready_skips_remote_api_base(monkeypatch):
    launches = []

    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_API_BASE", "https://shared-ollama.example.com")
    monkeypatch.setenv("OLLAMA_MODEL", "ministral-3:8b")
    monkeypatch.setattr(server, "_probe_ollama_runtime", lambda api_base, timeout_seconds=1.5: (False, set()))
    monkeypatch.setattr(server.shutil, "which", lambda name: launches.append(name))
    monkeypatch.setattr(server, "_OLLAMA_AUTOSTART_LAST_ATTEMPT_AT", 0.0)

    server._ensure_local_ollama_runtime_ready()

    assert launches == []
