# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-7021b57984f45ecd0678d435

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import base64
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from shared.adk_state import (
    AUTOYOU_CONVERSATION_SESSION_STATE_KEY,
    AUTOYOU_OWNER_KEY_STATE_KEY,
    AUTOYOU_REPLY_TARGET_STATE_KEY,
)
from shared.session_execution import SESSION_CONTROL_STATE_KEY
from autoyou_agents.media_generation_agent import agent as media_agent
from autoyou_agents.media_generation_agent import media_generation_tool
from autoyou_agents.media_generation_agent.website.backend import app as media_ui_backend

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-7021b57984f45ecd0678d435"


FRONTEND_DIR = (
    Path(__file__).resolve().parents[3]
    / "autoyou_agents"
    / "media_generation_agent"
    / "website"
    / "frontend"
)


def _llm_request(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        contents=[
            SimpleNamespace(
                role="user",
                parts=[SimpleNamespace(text=text)],
            )
        ]
    )


def test_media_generation_ui_backend_auth_status_unauthenticated_by_default() -> None:
    client = TestClient(media_ui_backend.app)
    response = client.get("/api/auth/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["authenticated"] is False


def test_media_generation_ui_backend_protected_history_returns_401() -> None:
    client = TestClient(media_ui_backend.app)
    response = client.get("/api/history")
    assert response.status_code == 401
    assert response.json()["success"] is False
    assert response.json()["error"] == "Not authenticated"


def test_media_generation_ui_backend_protected_config_returns_401() -> None:
    client = TestClient(media_ui_backend.app)
    response = client.get("/api/config")
    assert response.status_code == 401


def test_media_generation_ui_backend_protected_generate_returns_401() -> None:
    client = TestClient(media_ui_backend.app)
    response = client.post("/api/generate", json={"prompt": "A scenic view"})
    assert response.status_code == 401


def test_media_generation_ui_backend_auth_login_fails_when_helpers_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(media_ui_backend, "_import_auth_helpers", lambda: {})
    client = TestClient(media_ui_backend.app)
    response = client.post("/api/auth/login", json={"totp_code": "123456"})
    assert response.status_code == 503


def test_media_generation_ui_backend_auth_login_fails_when_totp_not_configured(monkeypatch) -> None:
    mock_helpers = {
        "totp_capabilities": lambda: {"totp_configured": False},
        "create_session": MagicMock(),
        "cookie_name": lambda a: "media_session",
        "cookie_path": lambda a: "/",
        "runtime_server": MagicMock(),
    }
    monkeypatch.setattr(media_ui_backend, "_import_auth_helpers", lambda: mock_helpers)
    client = TestClient(media_ui_backend.app)
    response = client.post("/api/auth/login", json={"totp_code": "123456"})
    assert response.status_code == 400
    assert "not configured" in response.json()["error"].lower()


def test_media_generation_ui_backend_auth_login_issues_token_on_valid_totp(monkeypatch) -> None:
    mock_server = MagicMock()
    mock_server.STATE.config = {}
    mock_server._default_config.return_value = {}
    mock_server._get_pairing_totp_secret.return_value = "TESTSECRET"
    mock_server._verify_totp_secret.return_value = True

    mock_helpers = {
        "totp_capabilities": lambda: {"totp_configured": True},
        "create_session": lambda agent, days: "test-media-token-xyz",
        "session_valid": lambda agent, token: token == "test-media-token-xyz",
        "delete_session": MagicMock(),
        "cookie_name": lambda a: "media_agent_session",
        "cookie_path": lambda a: "/",
        "runtime_server": lambda: mock_server,
    }
    monkeypatch.setattr(media_ui_backend, "_import_auth_helpers", lambda: mock_helpers)

    client = TestClient(media_ui_backend.app)
    response = client.post("/api/auth/login", json={"totp_code": "123456"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["token"] == "test-media-token-xyz"


def test_media_generation_tool_switches_image_job_to_image_capable_model(tmp_path) -> None:
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "ltx2_distilled_gguf_q4_k_m.json").write_text(
        '{"model": {"architecture": "ltx2"}}',
        encoding="utf-8",
    )
    (defaults / "flux_schnell.json").write_text(
        '{"model": {"architecture": "flux_schnell", "image_outputs": true}}',
        encoding="utf-8",
    )

    selected, warning = media_generation_tool._resolve_wan_model_type(
        {"model_type": "ltx2_distilled_gguf_q4_k_m", "image_model_type": "flux_schnell"},
        tmp_path,
        "ltx2_distilled_gguf_q4_k_m",
        "image",
    )

    assert selected == "flux_schnell"
    assert "not configured as a Wan2GP image model" in warning


def test_media_generation_prompt_cleanup_is_single_line_and_bounded() -> None:
    raw = "**Scene:**\n- synthetic subject floating in space\n" + ("detail " * 500)

    cleaned = media_generation_tool._clean_prompt_text(raw, max_chars=180)

    assert "\n" not in cleaned
    assert "**" not in cleaned
    assert len(cleaned) <= 183


def test_media_generation_history_update_records_prompt_and_settings() -> None:
    item_id = media_generation_tool.save_history_item(
        media_type="image",
        original_prompt="synthetic prompt",
        optimized_prompt="Enhancing prompt...",
        file_path="",
        file_name="",
        settings={},
        status="generating",
    )

    media_generation_tool.update_history_status(
        item_id,
        "failed",
        "synthetic failure",
        optimized_prompt="synthetic optimized prompt",
        settings={
            "model_type": "flux_schnell",
            "resolution": "1280x720",
            "num_inference_steps": 10,
            "video_length": None,
            "seed": -1,
        },
    )

    item = media_generation_tool.get_history_item(item_id)
    assert item["status"] == "failed"
    assert item["optimized_prompt"] == "synthetic optimized prompt"
    assert item["model_type"] == "flux_schnell"
    assert item["resolution"] == "1280x720"
    assert item["steps"] == 10


def test_media_generation_api_redacts_local_paths(monkeypatch) -> None:
    monkeypatch.setattr(media_ui_backend, "_check_auth", lambda request: True)
    monkeypatch.setattr(
        media_ui_backend,
        "list_history",
        lambda limit=50, media_type=None: [
            {
                "id": 77,
                "timestamp": "2026-06-24T17:47:04+00:00",
                "media_type": "image",
                "original_prompt": "synthetic image prompt",
                "optimized_prompt": "synthetic optimized prompt",
                "file_path": r"C:\Users\example\AppData\Roaming\AutoYou\media_generation_agent\output\image.png",
                "file_name": "image.png",
                "model_type": "flux_schnell",
                "resolution": "1280x720",
                "steps": 10,
                "frames": None,
                "seed": -1,
                "status": "failed",
                "error_message": r"Logs were saved in C:\Users\example\AppData\Roaming\AutoYou\media_generation_agent\output\job. STDERR tail: File C:\private\wan\wgp.py failed.",
            }
        ],
    )

    client = TestClient(media_ui_backend.app)
    response = client.get("/api/history")

    assert response.status_code == 200
    payload_text = json.dumps(response.json())
    assert r"C:\Users\example" not in payload_text
    assert r"C:\private" not in payload_text
    item = response.json()["history"][0]
    assert item["file_path"] == "[local media file]/image.png"
    assert "[local path]" in item["error_message"]
    assert item["local_paths_redacted"] is True


def test_media_generation_external_python_env_strips_packaged_runtime(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHOME", r"C:\AutoYou\Backend\runtime_stdlib")
    monkeypatch.setenv("PYTHONPATH", r"C:\AutoYou\Backend\runtime_site_packages")
    monkeypatch.setenv("AUTOYOU_PACKAGED_RUNTIME", "1")
    monkeypatch.setenv("AUTOYOU_PACKAGED_RESOURCES_ROOT", r"C:\AutoYou\Backend")
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\AutoYou\venv")
    monkeypatch.setenv("PATH", r"C:\Windows\System32")

    env = media_generation_tool._external_python_subprocess_env()

    assert env["PATH"] == r"C:\Windows\System32"
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "AUTOYOU_PACKAGED_RUNTIME",
        "AUTOYOU_PACKAGED_RESOURCES_ROOT",
        "VIRTUAL_ENV",
    ):
        assert key not in env


def test_media_generation_chat_tool_detaches_and_notifies_with_image(monkeypatch, tmp_path) -> None:
    generated = tmp_path / "synthetic-space-baby.png"
    generated.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic image bytes")
    deliveries = []

    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon, name):
            self.target = target
            self.kwargs = kwargs
            self.daemon = daemon
            self.name = name

        def start(self):
            self.target(**self.kwargs)

    def fake_generate_media_sync(**kwargs):
        return {
            "status": "success",
            "item_id": kwargs["history_item_id"],
            "media_type": kwargs["media_type"],
            "file_path": str(generated),
            "file_name": generated.name,
            "message": "Synthetic success.",
        }

    monkeypatch.setattr(media_agent.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(media_agent, "generate_media_sync", fake_generate_media_sync)
    monkeypatch.setattr(
        media_agent,
        "_post_reply_target_message",
        lambda **kwargs: deliveries.append(kwargs) or {"status": "success", "message": "sent"},
    )

    tool_context = SimpleNamespace(
        state={
            AUTOYOU_OWNER_KEY_STATE_KEY: "cloud:synthetic-device",
            AUTOYOU_REPLY_TARGET_STATE_KEY: {
                "transport": "webrtc",
                "owner_key": "cloud:synthetic-device",
            },
            AUTOYOU_CONVERSATION_SESSION_STATE_KEY: (
                "session::dest::server::local%3Adesk-server::owner::cloud%3Asynthetic-device"
                "::target::synthetic-device"
            ),
            SESSION_CONTROL_STATE_KEY: {
                "owner_key": "cloud:synthetic-device",
                "canonical_session_id": "session::cloud:synthetic-device",
            },
        },
        user_id="user::cloud:synthetic-device",
    )

    result = media_agent.generate_media(
        "A cinematic image of a baby flying in space",
        media_type="image",
        tool_context=tool_context,
    )

    assert result["status"] == "started"
    assert result["media_type"] == "image"
    assert "started" in result["message"].lower()
    assert result["delivery_target_available"] is True
    assert deliveries
    delivery = deliveries[0]
    assert delivery["reply_target"] == {"transport": "webrtc", "owner_key": "cloud:synthetic-device"}
    assert delivery["message"] == "Your image is ready."
    assert delivery["metadata"]["source"] == "media_generation_agent"
    assert delivery["metadata"]["agent_display_name"] == "Media Generation"
    assert delivery["metadata"]["conversation_session_id"] == (
        "session::dest::server::local%3Adesk-server::owner::cloud%3Asynthetic-device"
        "::target::synthetic-device"
    )
    assert delivery["metadata"]["ai_agent_session_id"] == "session::cloud:synthetic-device"
    assert delivery["metadata"]["conversation_force_target"] is True
    attachment = delivery["context"][0]["attachments"][0]
    assert attachment["filename"] == generated.name
    assert attachment["mimetype"] == "image/png"
    assert base64.b64decode(attachment["data"]).startswith(b"\x89PNG")


def test_media_generation_chat_tool_notifies_failure(monkeypatch) -> None:
    deliveries = []

    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon, name):
            self.target = target
            self.kwargs = kwargs
            self.daemon = daemon
            self.name = name

        def start(self):
            self.target(**self.kwargs)

    def fake_generate_media_sync(**kwargs):
        return {
            "status": "error",
            "item_id": kwargs["history_item_id"],
            "media_type": kwargs["media_type"],
            "message": "Wan2GP application folder not found at a synthetic path.",
        }

    monkeypatch.setattr(media_agent.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(media_agent, "generate_media_sync", fake_generate_media_sync)
    monkeypatch.setattr(
        media_agent,
        "_post_reply_target_message",
        lambda **kwargs: deliveries.append(kwargs) or {"status": "success", "message": "sent"},
    )

    tool_context = SimpleNamespace(
        state={
            AUTOYOU_REPLY_TARGET_STATE_KEY: {
                "transport": "webrtc",
                "session_id": "synthetic-session",
            },
        }
    )

    result = media_agent.generate_media("Synthetic image prompt", media_type="image", tool_context=tool_context)

    assert result["status"] == "started"
    assert deliveries[0]["context"] == []
    assert deliveries[0]["message"].startswith("Image generation failed:")
    assert deliveries[0]["metadata"]["media_generation"]["status"] == "failed"


def test_media_generation_sync_retries_image_after_wan_memory_failure(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "media-output"
    subprocess_calls = []

    monkeypatch.setattr(media_generation_tool, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(
        media_generation_tool,
        "get_wan2gp_config",
        lambda: {
            "app_dir": str(tmp_path),
            "python": sys.executable,
            "image_model_type": "flux_schnell",
            "image_resolution": "1280x720",
            "image_num_inference_steps": 10,
            "seed": -1,
            "settings_version": 2.52,
            "enhance_prompt": False,
            "prompt_enhancer": "",
        },
    )

    def fake_run(command, **kwargs):
        settings_path = Path(command[command.index("--process") + 1])
        output_path = Path(command[command.index("--output-dir") + 1])
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        subprocess_calls.append(settings)
        if len(subprocess_calls) == 1:
            return media_generation_tool.subprocess.CompletedProcess(
                command,
                1,
                stdout="RuntimeError: bad allocation",
                stderr="fatal   : Memory allocation failure",
            )
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "synthetic-low-memory.png").write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
        return media_generation_tool.subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    monkeypatch.setattr(media_generation_tool.subprocess, "run", fake_run)

    result = media_generation_tool.generate_media_sync(
        "Generate an image of a baby driving a scooter in space",
        media_type="image",
        resolution="1280x720",
        steps=10,
        optimized_prompt="A detailed cinematic prompt with many visual descriptors. " * 12,
    )

    assert result["status"] == "success"
    assert result["retry_attempted"] is True
    assert len(subprocess_calls) == 2
    assert subprocess_calls[0]["resolution"] == "1280x720"
    assert subprocess_calls[0]["num_inference_steps"] == 10
    assert subprocess_calls[1]["resolution"] == "768x432"
    assert subprocess_calls[1]["num_inference_steps"] == 6
    assert subprocess_calls[1]["prompt"] == "Generate an image of a baby driving a scooter in space"
    assert Path(result["file_path"]).is_file()
    assert list(output_dir.glob("*/stderr.txt"))


def test_media_generation_before_model_starts_clear_image_requests(monkeypatch) -> None:
    calls = []

    def fake_generate_media(prompt, media_type="video", **kwargs):
        calls.append({"prompt": prompt, "media_type": media_type, **kwargs})
        return {
            "status": "started",
            "item_id": 123,
            "media_type": media_type,
            "message": "Image generation has started. I'll send the image here when it is ready.",
        }

    monkeypatch.setattr(media_agent, "generate_media", fake_generate_media)
    callback_context = SimpleNamespace(state={}, invocation_id="media-before-model")

    response = asyncio.run(
        media_agent._media_before_model_callback(
            callback_context,
            _llm_request("generate an image of baby flying in space"),
        )
    )

    assert calls == [
        {
            "prompt": "generate an image of baby flying in space",
            "media_type": "image",
            "tool_context": callback_context,
        }
    ]
    assert "Image generation has started" in response.content.parts[0].text
    assert response.custom_metadata["media_generation_deterministic_reply"] is True


def test_media_generation_ui_generate_detaches_thread_and_passes_optimized_prompt(monkeypatch) -> None:
    launched = []

    class ImmediateThread:
        def __init__(self, *, target, kwargs, daemon, name):
            self.target = target
            self.kwargs = kwargs
            self.daemon = daemon
            self.name = name

        def start(self):
            self.target(**self.kwargs)

    def fake_background_generation(**kwargs):
        launched.append(kwargs)
        media_generation_tool.update_history_status(kwargs["item_id"], "failed", "synthetic stop")

    monkeypatch.setattr(media_ui_backend, "_check_auth", lambda request: True)
    monkeypatch.setattr(media_ui_backend.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(media_ui_backend, "run_background_generation", fake_background_generation)

    client = TestClient(media_ui_backend.app)
    response = client.post(
        "/api/generate",
        json={
            "prompt": "synthetic image prompt",
            "media_type": "image",
            "model_type": "qwen_image_20b",
            "resolution": "1280x720",
            "steps": 10,
            "seed": -1,
            "optimized_prompt": "synthetic optimized prompt",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert launched[0]["media_type"] == "image"
    assert launched[0]["model_type"] == "qwen_image_20B"
    assert launched[0]["optimized_prompt"] == "synthetic optimized prompt"


def test_media_generation_ui_generate_rejects_invalid_media_type(monkeypatch) -> None:
    monkeypatch.setattr(media_ui_backend, "_check_auth", lambda request: True)
    client = TestClient(media_ui_backend.app)

    response = client.post(
        "/api/generate",
        json={"prompt": "synthetic prompt", "media_type": "audio"},
    )

    assert response.status_code == 400
    assert "media_type" in response.json()["error"]


def test_media_generation_frontend_exposes_image_defaults_and_mobile_rules() -> None:
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND_DIR / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND_DIR / "styles.css").read_text(encoding="utf-8")
    # from __debug_provenance_n__ import license

    assert "settings-image-model" in html
    assert "settings-image-resolution" in html
    assert "settings-image-steps" in html
    assert "image_num_inference_steps" in js
    assert "applyGenerationDefaultsForMediaType" in js
    assert "@media (max-width: 640px)" in css
    assert "min-height: 44px" in css


def test_wan2gp_default_paths_are_platform_appropriate() -> None:
    defaults = media_generation_tool._platform_default_wan2gp_paths()
    assert defaults["app_dir"].endswith("/app")
    if sys.platform.startswith("win"):
        assert defaults["python"].endswith("Scripts/python.exe")
    else:
        assert defaults["python"].endswith("env/bin/python")
        assert "D:/" not in defaults["root"]


def test_discover_wan2gp_installation_finds_pinokio_layout(tmp_path, monkeypatch) -> None:
    pinokio_home = tmp_path / "pinokio"
    app_dir = pinokio_home / "api" / "wan.git" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "wgp.py").write_text("# stub", encoding="utf-8")
    python_rel = "env/Scripts/python.exe" if sys.platform.startswith("win") else "env/bin/python"
    python_path = app_dir / python_rel
    python_path.parent.mkdir(parents=True)
    python_path.write_text("", encoding="utf-8")

    monkeypatch.setenv("PINOKIO_HOME", str(pinokio_home))
    found = media_generation_tool.discover_wan2gp_installation()
    assert found is not None
    assert found["app_dir"] == str(app_dir)
    assert found["python"] == str(python_path)
    assert found["root"] == str(pinokio_home / "api" / "wan.git")


def test_discover_wan2gp_installation_reports_incomplete_env(tmp_path, monkeypatch) -> None:
    pinokio_home = tmp_path / "pinokio"
    app_dir = pinokio_home / "api" / "wan2gp.git" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "wgp.py").write_text("# stub", encoding="utf-8")

    monkeypatch.setenv("PINOKIO_HOME", str(pinokio_home))
    monkeypatch.setattr(media_generation_tool.Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    # Keep discovery hermetic: the hardcoded drive-root probes (C:/pinokio,
    # D:/pinokio, ...) may hold a real, complete install on a dev machine,
    # which would outrank this intentionally incomplete stub.
    monkeypatch.setattr(media_generation_tool, "_candidate_pinokio_homes", lambda: [pinokio_home])
    monkeypatch.setattr(media_generation_tool, "MANAGED_WAN2GP_APP_DIR", tmp_path / "managed" / "app")
    found = media_generation_tool.discover_wan2gp_installation()
    assert found is not None
    assert found["app_dir"] == str(app_dir)
    assert found["python"] == ""


def test_get_wan2gp_config_overlays_discovered_paths_when_configured_missing(tmp_path, monkeypatch) -> None:
    pinokio_home = tmp_path / "pinokio"
    app_dir = pinokio_home / "api" / "wan.git" / "app"
    python_rel = "env/Scripts/python.exe" if sys.platform.startswith("win") else "env/bin/python"
    python_path = app_dir / python_rel
    python_path.parent.mkdir(parents=True)
    python_path.write_text("", encoding="utf-8")
    (app_dir / "wgp.py").write_text("# stub", encoding="utf-8")

    monkeypatch.setenv("PINOKIO_HOME", str(pinokio_home))
    config = media_generation_tool._overlay_discovered_wan2gp_paths(
        {
            "root": "D:/pinokio/api/wan.git",
            "app_dir": str(tmp_path / "definitely-missing" / "app"),
            "python": "D:/pinokio/api/wan.git/app/env/Scripts/python.exe",
            "model_type": "ltx2_distilled_gguf_q4_k_m",
        }
    )
    assert config["app_dir"] == str(app_dir)
    assert config["python"] == str(python_path)
    assert config["model_type"] == "ltx2_distilled_gguf_q4_k_m"


def test_wan2gp_config_uses_user_runtime_path_when_compiled(tmp_path, monkeypatch) -> None:
    bundle_root = tmp_path / "bundle" / "runtime_modules"
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(media_generation_tool, "PROJECT_ROOT", bundle_root)
    monkeypatch.setattr(media_generation_tool, "is_compiled", lambda: True)

    media_generation_tool.save_wan2gp_config({"app_dir": "synthetic-app"})

    runtime_path = tmp_path / "runtime" / "AutoYou" / "config" / "video.json"
    assert media_generation_tool._wan2gp_config_path() == runtime_path
    assert json.loads(runtime_path.read_text(encoding="utf-8"))["wan2gp"]["app_dir"] == "synthetic-app"
    assert not (bundle_root / "config" / "video.json").exists()


def test_wan2gp_config_migrates_source_legacy_file_on_save(tmp_path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    legacy_path = source_root / "config" / "video.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps({"script_defaults": {"synthetic": True}, "wan2gp": {"app_dir": "legacy-app"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(media_generation_tool, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(media_generation_tool, "is_compiled", lambda: False)

    media_generation_tool.save_wan2gp_config({"app_dir": "new-app"})

    runtime_path = tmp_path / "runtime" / "AutoYou" / "config" / "video.json"
    saved = json.loads(runtime_path.read_text(encoding="utf-8"))
    assert saved["script_defaults"] == {"synthetic": True}
    assert saved["wan2gp"]["app_dir"] == "new-app"
    assert json.loads(legacy_path.read_text(encoding="utf-8"))["wan2gp"]["app_dir"] == "legacy-app"


def test_overlay_keeps_existing_configured_paths(tmp_path) -> None:
    existing_app = tmp_path / "custom" / "app"
    existing_app.mkdir(parents=True)
    config_in = {"app_dir": str(existing_app), "python": "whatever"}
    assert media_generation_tool._overlay_discovered_wan2gp_paths(dict(config_in)) == config_in


def test_wan2gp_environment_endpoints_require_auth() -> None:
    client = TestClient(media_ui_backend.app)
    assert client.get("/api/wan2gp/environment").status_code == 401
    assert client.post("/api/wan2gp/detect").status_code == 401
    assert client.post("/api/wan2gp/install").status_code == 401
    assert client.get("/api/wan2gp/install/status").status_code == 401


def test_wan2gp_environment_status_shape(monkeypatch) -> None:
    status = media_generation_tool.wan2gp_environment_status()
    assert "platform" in status and "configured" in status and "install" in status
    assert isinstance(status["install_supported"], bool)
    assert status["install"]["status"] in {"idle", "running", "completed", "failed"}


def test_start_wan2gp_install_refuses_unsupported_machines(monkeypatch) -> None:
    monkeypatch.setattr(
        media_generation_tool,
        "_machine_profile",
        lambda: {"system": "Darwin", "machine": "x86_64", "has_nvidia": False},
    )
    result = media_generation_tool.start_wan2gp_install()
    assert result["status"] == "unsupported"
    assert "Intel" in result["message"]
