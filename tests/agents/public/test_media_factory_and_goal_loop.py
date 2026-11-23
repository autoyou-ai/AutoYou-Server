# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autoyou_agents.media_generation_agent import agent as media_agent
from autoyou_agents.media_generation_agent import engines as media_engines
from autoyou_agents.media_generation_agent import factory as media_factory
from autoyou_agents.media_generation_agent import media_generation_tool as mgt
from autoyou_agents.media_generation_agent.website.backend import app as studio_app
from autoyou_agents.self_improvement import goal_loop
from shared import adk_state
from shared import media_generation_runtime as mgr

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7+8r8AAAAASUVORK5CYII="
)


def _isolate_media_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "media_history.db"
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mgt, "DB_PATH", db_path)
    monkeypatch.setattr(mgt, "OUTPUT_DIR", out_dir)
    monkeypatch.setattr(mgt, "MEDIA_DATA_DIR", tmp_path)
    monkeypatch.setattr(mgt, "_config_path", lambda: tmp_path / "video.json") if hasattr(mgt, "_config_path") else None
    monkeypatch.setattr(studio_app, "MEDIA_DIR", out_dir)
    monkeypatch.setattr(studio_app, "OUTPUT_DIR", out_dir)
    monkeypatch.setattr(goal_loop, "OUTPUT_DIR", tmp_path / "goal_reports")
    mgt.init_db()
    media_factory.init_factory_db()
    return out_dir


# ── 1. Hardware Memory Profile & Cross-Platform Accelerator Tests ───────────


def test_recommend_memory_profile_high_vram_low_ram_selects_profile_3_5():
    runtime = {
        "ready": True,
        "backend": "amd",
        "device": "Synthetic RDNA Unified GPU",
        "architecture": "gfx1151",
        "memory_bytes": 96 * (1024 ** 3),
        "unified_memory": True,
    }
    rec = mgr.recommend_wan_memory_profile(runtime, system_ram_bytes=32 * (1024 ** 3))
    assert rec["profile"] == "3.5"
    assert rec["unified_memory"] is True
    assert rec["vram_safety_coefficient"] == 0.85


def test_recommend_memory_profile_dgx_spark_gb10():
    runtime = {
        "ready": True,
        "backend": "nvidia",
        "device": "NVIDIA GB10",
        "machine": "aarch64",
        "compute_capability": [12, 1],
        "memory_bytes": 128 * (1024 ** 3),
        "unified_memory": True,
    }
    assert mgr.is_dgx_spark_runtime(runtime) is True
    assert mgr.is_unified_memory_gpu(runtime) is True
    rec = mgr.recommend_wan_memory_profile(runtime, system_ram_bytes=128 * (1024 ** 3))
    assert rec["profile"] == "3.5"
    assert rec["dgx_spark"] is True
    assert rec["prefer_nvfp4"] is True
    assert rec["prefer_bf16"] is True


def test_recommend_memory_profile_scales_for_small_and_medium_gpus():
    low = mgr.recommend_wan_memory_profile(
        {"backend": "nvidia", "memory_bytes": 8 * (1024 ** 3)},
        system_ram_bytes=32 * (1024 ** 3),
    )
    assert low["profile"] == "5"

    mid = mgr.recommend_wan_memory_profile(
        {"backend": "nvidia", "memory_bytes": 16 * (1024 ** 3)},
        system_ram_bytes=64 * (1024 ** 3),
    )
    assert mid["profile"] == "4"

    high_discrete = mgr.recommend_wan_memory_profile(
        {"backend": "nvidia", "memory_bytes": 24 * (1024 ** 3)},
        system_ram_bytes=64 * (1024 ** 3),
    )
    assert high_discrete["profile"] == "3"


def test_wan_generation_command_resolves_auto_profile_when_memory_known(tmp_path: Path):
    (tmp_path / "wgp.py").write_text(
        '"--profile" "--vram-safety-coefficient" "--perc-reserved-mem-max"\n',
        encoding="utf-8",
    )
    cmd = mgr.wan_generation_command(
        python=tmp_path / "python",
        settings=tmp_path / "settings.json",
        output=tmp_path / "out",
        runtime={"backend": "amd", "architecture": "gfx1151", "memory_bytes": 96 * (1024 ** 3), "unified_memory": True},
        config={"app_dir": str(tmp_path), "memory_profile": "auto"},
    )
    assert "--profile" in cmd
    assert cmd[cmd.index("--profile") + 1] == "3.5"
    assert "--vram-safety-coefficient" in cmd
    assert "--perc-reserved-mem-max" in cmd


def test_multi_engine_status_and_verification(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _isolate_media_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(media_engines, "_media_data_dir", lambda: tmp_path)
    monkeypatch.setattr(media_engines, "_http_json", lambda *a, **kw: (0, "offline"))
    monkeypatch.setattr(
        media_engines,
        "find_ffmpeg_executables",
        lambda *a, **kw: {"ffmpeg": str(tmp_path / "ffmpeg"), "ffprobe": str(tmp_path / "ffprobe")},
    )
    wan_env = {
        "runtime": {
            "ready": True,
            "backend": "nvidia",
            "device": "Synthetic RTX 5090",
            "memory_bytes": 32 * (1024 ** 3),
        },
        "install_supported": True,
    }
    report = media_engines.verify_media_factory_installation(
        {"engine": "wan2gp", "root": str(tmp_path), "app_dir": str(tmp_path), "python": str(tmp_path / "py")},
        wan_env=wan_env,
        draw_things_status={"supported": False, "ready": False},
    )
    assert report["ok"] is True
    assert report["long_video_ready"] is True
    assert "wan2gp" in report["ready_engines"]
    assert set(report["engines"].keys()) == {"wan2gp", "comfyui", "mlx", "draw_things", "sdcpp"}


# ── 2. Media Factory Full Checkpointed Pipeline & Crash Recovery ────────────


def test_media_factory_batched_plan_edit_render_refine_assemble(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    out_dir = _isolate_media_storage(monkeypatch, tmp_path)
    ref_img = tmp_path / "anchor.png"
    ref_img.write_bytes(_TINY_PNG)

    # Test batched planning (> _PLAN_BATCH_MAX_SCENES = 16 -> 2 batches for 20 scenes)
    llm_calls = []

    def fake_llm(sys_p, user_p, **kwargs):
        llm_calls.append(user_p)
        return json.dumps({
            "style_bible": "Warm golden hour 35mm cinema look, coherent protagonist.",
            "script_text": "Act segment screenplay.",
            "scenes": [
                {
                    "title": f"Beat {i + 1}",
                    "duration_seconds": 5.0,
                    "prompt": f"Cinematic beat {i + 1} with gentle camera dolly.",
                    "camera_direction": "Slow dolly push-in",
                    "audio_cue": "Soft ambient score",
                    "transition": "crossfade",
                }
                for i in range(16)
            ],
        })

    monkeypatch.setattr(mgt, "llm_chat_complete", fake_llm)
    long_proj = media_factory.create_project(
        title="Synthetic Feature Film",
        original_request="A scenic orbital sunrise documentary.",
        target_duration_seconds=100.0,
        scene_duration_seconds=5.0,
        attachments=[{"path": str(ref_img), "mimetype": "image/png", "kind": "image"}],
        embedded_context={"conversation_summary": "User requested calm orbital pacing."},
        plan_now=True,
    )
    assert len(llm_calls) == 2
    assert long_proj["scene_count"] == 20
    assert long_proj["scenes"][0]["reference_image_path"]
    assert Path(long_proj["scenes"][0]["reference_image_path"]).is_file()

    # Now create a compact 2-scene project to exercise the full goal_loop lifecycle
    proj = media_factory.create_project(
        title="Two Scene Demo",
        original_request="Ocean waves at sunrise.",
        target_duration_seconds=10.0,
        scene_duration_seconds=5.0,
        plan_now=True,
    )
    pid = proj["id"]
    assert proj["scene_count"] == 2

    # Edit script and first scene
    updated = media_factory.update_scene(pid, proj["scenes"][0]["id"], {"title": "Dawn Horizon", "prompt": "Golden dawn over calm ocean."})
    assert updated and updated["title"] == "Dawn Horizon"

    # Simulate scene 1 failing on first render attempt, then succeeding during refine self-repair!
    render_attempts: dict[int, int] = {}

    def fake_generate_media_sync(**kwargs):
        idx_key = len(render_attempts)
        prompt = kwargs.get("prompt", "")
        if kwargs.get("media_type") == "image":
            img_out = out_dir / "images" / f"kf_{idx_key}.png"
            img_out.parent.mkdir(parents=True, exist_ok=True)
            img_out.write_bytes(_TINY_PNG)
            return {"status": "success", "file_path": str(img_out), "file_name": img_out.name, "optimized_prompt": prompt}

        call_no = render_attempts.get(hash(prompt), 0) + 1
        render_attempts[hash(prompt)] = call_no
        # Force second scene to fail once so goal_loop refine repairs it!
        if "Beat 2" in prompt or "beat 2" in prompt.lower():
            if sum(render_attempts.values()) == 2:
                return {"status": "error", "message": "Transient synthetic GPU hiccup"}
        clip_out = out_dir / "videos" / f"clip_{sum(render_attempts.values())}.mp4"
        clip_out.parent.mkdir(parents=True, exist_ok=True)
        clip_out.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128)
        return {"status": "success", "file_path": str(clip_out), "file_name": clip_out.name, "optimized_prompt": prompt}

    monkeypatch.setattr(mgt, "generate_media_sync", fake_generate_media_sync)
    monkeypatch.setattr(
        media_factory,
        "probe_media_file",
        lambda path, **kw: {"valid": Path(path).is_file(), "kind": "video", "width": 848, "height": 480, "duration_seconds": 5.0},
    )

    def fake_extract(video_path: Path, out_image: Path, **kw) -> Path:
        out_image.parent.mkdir(parents=True, exist_ok=True)
        out_image.write_bytes(_TINY_PNG)
        return out_image

    def fake_assemble(clip_paths, output_path: Path, **kw):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512)
        return {
            "output_path": str(output_path),
            "clip_count": len(clip_paths),
            "probe": {"valid": True, "width": 848, "height": 480, "duration_seconds": 10.0},
        }

    monkeypatch.setattr(media_factory, "extract_video_frame", fake_extract)
    monkeypatch.setattr(media_factory, "assemble_video_clips", fake_assemble)

    run = goal_loop.run_media_factory_goal(
        goal_loop.MediaGoalConfig(project_id=pid, stages=("render", "refine", "assemble"))
    )
    assert run.status == "completed"
    assert run.completed_stages == ["render", "refine", "assemble"]
    assert run.report_path and Path(run.report_path).is_file()

    final_proj = media_factory.get_project(pid, include_scenes=True, include_logs=True)
    assert final_proj is not None
    assert final_proj["status"] == "completed"
    assert final_proj["has_final_output"] is True
    assert final_proj["rendered_scene_count"] == 2
    assert final_proj["final_history_item_id"] is not None


def test_reconcile_interrupted_projects_moves_dead_owner_to_paused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _isolate_media_storage(monkeypatch, tmp_path)
    proj = media_factory.create_project(title="Interrupted Render", original_request="Clouds over mountains.")
    media_factory.update_project(proj["id"], {"status": "rendering", "owner_pid": 999999, "owner_started_at": 1.0})
    monkeypatch.setattr(mgt, "_job_owner_alive", lambda pid, ts: False)
    count = media_factory.reconcile_interrupted_projects()
    assert count == 1
    refreshed = media_factory.get_project(proj["id"])
    assert refreshed and refreshed["status"] == "paused"


def test_goal_loop_media_factory_suite_registered():
    assert "media_factory" in goal_loop.SUITES
    scenarios = goal_loop.SUITES["media_factory"]()
    assert len(scenarios) >= 2
    assert all(s.turns for s in scenarios)


# ── 3. Multimodal Chat State & Media Agent Tools ────────────────────────────


def test_multimodal_attachments_flow_into_media_agent_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _isolate_media_storage(monkeypatch, tmp_path)
    sample_img = tmp_path / "photo.png"
    sample_img.write_bytes(_TINY_PNG)
    sample_aud = tmp_path / "beat.wav"
    sample_aud.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")

    normalized = adk_state.normalize_turn_attachments([
        {"path": str(sample_img), "mimetype": "image/png", "filename": "photo.png"},
        {"path": str(sample_aud), "mimetype": "audio/wav", "filename": "beat.wav"},
    ])
    assert len(normalized) == 2
    assert {a["kind"] for a in normalized} == {"image", "audio"}

    fake_ctx = SimpleNamespace(
        state={
            adk_state.AUTOYOU_TURN_ATTACHMENTS_STATE_KEY: normalized,
            adk_state.AUTOYOU_RECENT_MEDIA_ATTACHMENTS_STATE_KEY: normalized,
        }
    )

    captured = {}

    def fake_launch(**kwargs):
        captured.update(kwargs)
        return {"status": "started", "item_id": 42, "message": "Started synthetic edit."}

    monkeypatch.setattr(media_agent, "generate_media", fake_launch)
    res = media_agent.modify_image(instruction="Make the sky twilight purple", tool_context=fake_ctx)
    assert res["status"] == "started"
    assert captured["edit_mode"] is True
    assert captured["reference_image_path"] == str(sample_img)

    # Chat-controlled settings update
    updated = media_agent.update_media_settings(video_duration_seconds=8.0, memory_profile="3.5")
    assert updated["status"] == "success"
    assert updated["settings"]["memory_profile"] == "3.5"
    assert updated["settings"]["video_length"] >= 180


def test_root_agent_media_factory_specialist_classifier():
    from autoyou_agents import agent as root_mod

    assert root_mod._is_media_factory_specialist_request("create a 5 minute multi-scene video documentary about Mars") is True
    assert root_mod._is_media_factory_specialist_request("edit this image to change the background to snow") is True
    assert root_mod._is_media_factory_specialist_request("update media generation settings to memory profile 3.5") is True
    assert root_mod._is_media_factory_specialist_request("generate an image of baby flying in space") is False


# ── 4. Redesigned Media Factory Studio HTTP API & Path Security ─────────────


def test_studio_api_auth_uploads_security_and_project_crud(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _isolate_media_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(mgt, "llm_chat_complete", lambda *a, **kw: None)  # deterministic fallback

    client = TestClient(studio_app.app)

    # 1. Auth gating enforced on all new endpoints
    monkeypatch.setattr(studio_app, "_check_auth", lambda req: False)
    for method, url in [
        ("GET", "/api/projects"),
        ("POST", "/api/uploads"),
        ("POST", "/api/edit-image"),
        ("GET", "/api/engines"),
        ("GET", "/api/hardware"),
        ("GET", "/api/llm-options"),
    ]:
        r = client.request(method, url, json={})
        assert r.status_code == 401, f"{method} {url} should require auth"

    # 2. Authenticated multimodal upload + traversal/extension rejection
    monkeypatch.setattr(studio_app, "_check_auth", lambda req: True)
    bad_ext = client.post(
        "/api/uploads",
        json={"filename": "malware.exe", "data_base64": base64.b64encode(b"MZ").decode()},
    )
    assert bad_ext.status_code == 400

    good_up = client.post(
        "/api/uploads",
        json={"filename": "scene_ref.png", "mimetype": "image/png", "data_base64": base64.b64encode(_TINY_PNG).decode()},
    )
    assert good_up.status_code == 200
    upload_info = good_up.json()["upload"]
    assert upload_info["kind"] == "image"
    assert upload_info["ref"].startswith("upload:")

    # Arbitrary host paths must be rejected by _resolve_client_media_ref!
    assert studio_app._resolve_client_media_ref("/etc/passwd") is None
    assert studio_app._resolve_client_media_ref(str(tmp_path / "media_history.db")) is None
    assert studio_app._resolve_client_media_ref("../secret.png") is None
    assert studio_app._resolve_client_media_ref(upload_info["ref"]) is not None

    # Verify /api/models returns HTTP 200 with catalog & flat installed list
    models_resp = client.get("/api/models")
    assert models_resp.status_code == 200
    assert models_resp.json()["success"] is True
    assert isinstance(models_resp.json()["installed_models"], list)
    assert isinstance(models_resp.json()["models"], dict)

    # 3. Project CRUD + Scene CRUD + Goal Status endpoints
    created = client.post(
        "/api/projects",
        json={
            "title": "API Factory Test",
            "original_request": "A quiet pine forest in winter.",
            "target_duration_seconds": 15,
            "scene_duration_seconds": 5,
            "attachment_refs": [upload_info["ref"]],
            "plan_now": True,
        },
    )
    assert created.status_code == 200
    proj = created.json()["project"]
    pid = proj["id"]
    assert proj["scene_count"] == 3
    # Verify host paths are redacted from API response
    assert str(tmp_path) not in json.dumps(proj)

    patched = client.patch(f"/api/projects/{pid}", json={"style_bible": "Crisp winter 4K cinema."})
    assert patched.status_code == 200
    assert patched.json()["project"]["style_bible"] == "Crisp winter 4K cinema."

    added_scene = client.post(f"/api/projects/{pid}/scenes", json={"title": "Epilogue", "duration_seconds": 4, "prompt": "Snow fades to dusk."})
    assert added_scene.status_code == 200
    assert added_scene.json()["project"]["scene_count"] == 4

    goal_status = client.get(f"/api/projects/{pid}/goal/status")
    assert goal_status.status_code == 200
    assert goal_status.json()["goal"]["project_id"] == pid

    deleted = client.delete(f"/api/projects/{pid}")
    assert deleted.status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404


# ── 5. Compiled Runtime Plan & admin-ui.js Optimization Contracts ───────────


def test_compiled_runtime_plan_includes_media_factory_and_goal_loop():
    from scripts import build_packaged_runtime_modules as bprm

    server_root = Path(__file__).resolve().parents[3]
    plan = bprm.build_runtime_module_plan(server_root)
    compiled = {spec.source_relative_path.as_posix() for spec in plan.compile_specs}
    stubs = {p.as_posix() for p in plan.bridge_stubs}
    assets = {p.as_posix() for p in plan.asset_files}

    assert "autoyou_agents/media_generation_agent/engines.py" in compiled
    assert "autoyou_agents/media_generation_agent/factory.py" in compiled
    assert "autoyou_agents/self_improvement/goal_loop.py" in compiled
    assert "shared/media_generation_runtime.py" in compiled
    assert "autoyou_agents/self_improvement/__init__.py" in stubs
    assert "autoyou_agents/media_generation_agent/website/frontend/index.html" in assets
    assert "autoyou_agents/media_generation_agent/website/frontend/app.js" in assets


def test_admin_ui_js_optimizations_and_safety_markers():
    server_root = Path(__file__).resolve().parents[3]
    text = (server_root / "assets" / "admin-ui.js").read_text(encoding="utf-8")

    # 1. GET request deduplication (null-prototype map, skips custom headers/cache options)
    assert "var inFlightGetRequests = Object.create(null);" in text
    assert "!options.cache" in text
    # 2. Object URL attachment preview & cleanup (keeps multi-MB base64 out of DOM markup)
    assert "URL.createObjectURL(file)" in text
    assert "chatRevokeAttachmentUrls" in text
    # 3. Safe Uint8Array WebRTC chunk reassembly (no apply stack overflow)
    assert "String.fromCharCode.apply(null, chatBase64Bytes(part))" not in text
    assert "var merged = new Uint8Array(totalBytes);" in text
    # 4. Background tab polling guards
    assert "if (document.hidden)" in text
    # 5. Media Factory quick launch link in Chat header
    assert "/agent/media_generation_agent/" in text
    # 6. Transient network/proxy retry in requestJson and boot() (recovers from iOS WebKit "Load failed")
    assert "bootRetryDelays" in text
    assert 'fetchError.name !== "AbortError"' in text
    # 7. Preserved boot marker expected by Playwright stability harness
    assert text.rstrip().endswith("    boot();\n}());")


def test_admin_ui_js_recovers_from_transient_load_failed_on_bootstrap():
    """Simulate iOS WebKit throwing TypeError('Load failed') on first /api/admin/bootstrap."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    server_root = Path(__file__).resolve().parents[3]
    admin_js = server_root / "assets" / "admin-ui.js"
    runner = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
let bootstrapAttempts = 0;
global.fetch = async (url) => {
  if (url === '/api/admin/bootstrap') {
    bootstrapAttempts += 1;
    if (bootstrapAttempts === 1) {
      throw new TypeError('Load failed');
    }
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      text: async () => JSON.stringify({ success: true, admin: { title: 'Test' } }),
    };
  }
  return { ok: true, status: 200, statusText: 'OK', text: async () => '{}' };
};
const start = src.indexOf('var inFlightGetRequests = Object.create(null);');
const end = src.indexOf('async function postJson(');
const fn = new Function(src.slice(start, end) + '\nreturn { requestJson };');
const { requestJson } = fn();
(async () => {
  const payload = await requestJson('/api/admin/bootstrap');
  if (!payload || !payload.success) process.exit(2);
  if (bootstrapAttempts !== 2) process.exit(3);
  console.log('RECOVERED_ATTEMPTS=' + bootstrapAttempts);
})().catch((err) => { console.error(err); process.exit(1); });
"""
    proc = subprocess.run([node, "-e", runner, str(admin_js)], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"stdout={proc.stdout} stderr={proc.stderr}"
    assert "RECOVERED_ATTEMPTS=2" in proc.stdout
