# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-62d284338c8a652bb1a529c0

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from shared import media_generation_runtime as runtime
from shared import secure_storage
from shared.secure_storage import read_secure_file
from autoyou_agents.media_generation_agent import media_generation_tool as media
from autoyou_agents.media_generation_agent.website.backend import app as website


@pytest.fixture
def media_db(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(media, "OUTPUT_DIR", tmp_path / "output")
    media.init_db()
    return tmp_path


def _pending():
    return media.save_history_item("image", "synthetic prompt", "", "", "", {}, "queued")


def _fake_config(tmp_path):
    (tmp_path / "wgp.py").write_text("settings_version = 2.79\n", encoding="utf-8")
    return {
        "app_dir": str(tmp_path), "python": sys.executable,
        "engine": "wan2gp", "enhance_prompt": False,
        "generation_timeout_seconds": 10,
    }


def test_owned_worker_writes_live_logs_and_times_out(tmp_path):
    observations = []

    def observe(stream, text):
        observations.append(read_secure_file(tmp_path / f"{stream}.txt"))

    started = time.monotonic()
    result = runtime.run_owned_media_process(
        [sys.executable, "-u", "-c",
         "import sys,time; print('synthetic progress',flush=True); print('synthetic diagnostic',file=sys.stderr,flush=True); time.sleep(60)"],
        cwd=tmp_path, env=dict(os.environ), timeout=0.8,
        work_dir=tmp_path, on_output=observe,
    )
    assert time.monotonic() - started < 10
    assert result.returncode == 124
    assert "timed out" in result.stderr
    assert b"synthetic progress" in read_secure_file(tmp_path / "stdout.txt")
    assert b"synthetic diagnostic" in read_secure_file(tmp_path / "stderr.txt")
    assert observations and any(b"synthetic progress" in value for value in observations)


def test_live_process_logs_remain_encrypted_in_maximus(tmp_path, monkeypatch):
    secure_storage.disable_secure_storage()
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    try:
        secure_storage.enable_secure_storage(
            app_name="AutoYou-test", root=tmp_path, password="synthetic-media-storage-password",
        )
        result = runtime.run_owned_media_process(
            [sys.executable, "-u", "-c", "print('synthetic private generation detail',flush=True)"],
            cwd=tmp_path, env=dict(os.environ), timeout=10, work_dir=tmp_path,
        )
        assert result.returncode == 0
        assert b"synthetic private" not in (tmp_path / "stdout.txt").read_bytes()
        assert b"synthetic private" in read_secure_file(tmp_path / "stdout.txt")
    finally:
        secure_storage.disable_secure_storage()


def test_owned_worker_cleans_children_that_keep_output_pipes_open(tmp_path):
    child = "import time; time.sleep(60)"
    parent = (
        "import subprocess,sys; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "print(p.pid,flush=True)"
    )
    started = time.monotonic()
    result = runtime.run_owned_media_process(
        [sys.executable, "-u", "-c", parent],
        cwd=tmp_path, env=dict(os.environ), timeout=10,
    )
    assert result.returncode == 0
    assert time.monotonic() - started < 10
    import psutil
    pid = int(result.stdout.strip())
    try:
        process = psutil.Process(pid)
        assert not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        pass


def test_posix_cleanup_stops_owned_group_before_reaping_exited_root(monkeypatch):
    from types import SimpleNamespace

    events = []

    def killpg(pid, sig):
        events.append(("kill_group", pid, sig))

    def wait(*, timeout):
        events.append(("reap", timeout))
        return 0

    process = SimpleNamespace(
        pid=111,
        wait=wait,
        poll=lambda: pytest.fail("Do not reap the root before stopping its owned group"),
    )
    # Replace module references, without changing os.name for the test runner.
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="posix", killpg=killpg))
    monkeypatch.setattr(runtime, "signal", SimpleNamespace(SIGKILL=9))
    assert runtime._stop_owned_process(process, None)
    assert events == [("kill_group", 111, 9), ("reap", 5)]


@pytest.mark.parametrize("gpu_backend", ["amd", "nvidia"])
def test_gpu_probe_reports_actual_device_and_caches(tmp_path, monkeypatch, gpu_backend):
    config = _fake_config(tmp_path)
    calls = []
    response = {"ready": True, "backend": gpu_backend, "device": "Synthetic GPU", "rocm_version": "10.0.0"}

    def probe(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, runtime.RUNTIME_MARKER + json.dumps(response), "")

    runtime.clear_wan_runtime_cache()
    monkeypatch.setattr(runtime, "run_owned_media_process", probe)
    assert runtime.probe_wan_runtime(config, {}).get("ready")
    assert runtime.probe_wan_runtime(config, {})["device"] == "Synthetic GPU"
    assert len(calls) == 1
    runtime.probe_wan_runtime(config, {}, refresh=True)
    assert len(calls) == 2


@pytest.mark.parametrize("output", ["", "AUTOYOU_MEDIA_RUNTIME=[]", "AUTOYOU_MEDIA_RUNTIME=invalid", 'AUTOYOU_MEDIA_RUNTIME={"ready": false, "error": "No GPU"}'])
def test_gpu_probe_never_claims_ready_for_invalid_or_failed_diagnostics(tmp_path, monkeypatch, output):
    config = _fake_config(tmp_path)
    runtime.clear_wan_runtime_cache()
    monkeypatch.setattr(runtime, "run_owned_media_process", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, output, ""))
    assert runtime.probe_wan_runtime(config, {})["ready"] is False


@pytest.mark.parametrize("version", ["7.0.0rc20250919", "7.12.0", "7.13.0a20260301"])
def test_old_windows_rocm_is_rejected(monkeypatch, version):
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    assert runtime._known_old_windows_rocm({"backend": "amd", "rocm_version": version})
    assert not runtime._known_old_windows_rocm({"backend": "nvidia", "rocm_version": version})


def test_modern_windows_rocm_is_accepted(monkeypatch):
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    assert not runtime._known_old_windows_rocm({"backend": "amd", "rocm_version": "10.0.0"})
    assert not runtime._known_old_windows_rocm({"backend": "amd", "rocm_version": "7.13.0a20260312"})


def test_amd_command_selects_portable_attention_and_explicit_gpu(tmp_path):
    command = runtime.wan_generation_command(Path("python"), tmp_path / "settings.json", tmp_path, {"backend": "amd"}, {})
    assert command[command.index("--gpu") + 1] == "cuda:0"
    assert command[command.index("--attention") + 1] == "sdpa"
    assert "torch.cuda.synchronize()" in command[command.index("-c") + 1]
    assert "enable_flash_sdp(False)" in runtime.WAN_WORKER_BOOTSTRAP
    nvidia = runtime.wan_generation_command(Path("python"), tmp_path / "settings.json", tmp_path, {"backend": "nvidia"}, {"gpu": "cuda:1"})
    assert "--attention" not in nvidia
    assert nvidia[nvidia.index("--gpu") + 1] == "cuda:1"


def _install_memory_options(tmp_path):
    source = tmp_path / "shared" / "cli_args.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text("\n".join(
        f'add("{option}")' for option in (
            "--profile", "--preload", "--ram-allocator", "--perc-reserved-mem-max",
        )
    ), encoding="utf-8")


def test_windows_amd_avoids_native_ram_allocator_and_retry_streams_weights(tmp_path, monkeypatch):
    _install_memory_options(tmp_path)
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    config = {"app_dir": str(tmp_path), "memory_profile": "4"}
    command = runtime.wan_generation_command(Path("python"), tmp_path / "settings.json", tmp_path, {"backend": "amd"}, config)
    assert command[command.index("--ram-allocator") + 1] == "default"
    assert command[command.index("--profile") + 1] == "4"
    retry = runtime.wan_generation_command(Path("python"), tmp_path / "settings.json", tmp_path, {"backend": "amd"}, dict(config, _low_memory_retry=True))
    assert retry[retry.index("--profile") + 1] == "5"
    assert retry[retry.index("--preload") + 1] == "0"
    assert retry[retry.index("--perc-reserved-mem-max") + 1] == "0.1"
    assert retry.count("--ram-allocator") == 1
    nvidia = runtime.wan_generation_command(Path("python"), tmp_path / "settings.json", tmp_path, {"backend": "nvidia"}, {"app_dir": str(tmp_path)})
    assert "--ram-allocator" not in nvidia and "--profile" not in nvidia


def test_legacy_wan_installation_does_not_receive_unknown_recovery_options(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    command = runtime.wan_generation_command(Path("python"), tmp_path / "settings.json", tmp_path, {"backend": "amd"}, {"app_dir": str(tmp_path), "_low_memory_retry": True})
    assert "--ram-allocator" not in command and "--profile" not in command
    with pytest.raises(ValueError, match="does not support"):
        runtime.wan_generation_command(Path("python"), tmp_path / "settings.json", tmp_path, {"backend": "amd"}, {"app_dir": str(tmp_path), "memory_profile": "5"})


@pytest.mark.parametrize("code", [3221225477, -1073741819])
def test_native_allocator_crash_is_distinct_from_gpu_memory_pressure(code):
    result = subprocess.CompletedProcess([], code, "", "Exception Code: 0xC0000005\nram_alloc_win_amd64.dll\nc10.dll alloc_cpu")
    assert media._process_failure_kind(result) == "native_allocator_crash"
    assert "CPU memory allocator crashed" in media._process_failure_summary(result)
    assert not media._looks_like_wan_memory_failure(result.stderr)


@pytest.mark.parametrize("code,kind", [(124, "timeout"), (125, "cleanup_failed")])
def test_timeout_and_incomplete_cleanup_never_trigger_memory_retry(media_db, monkeypatch, code, kind):
    monkeypatch.setattr(media, "get_wan2gp_config", lambda: _fake_config(media_db))
    monkeypatch.setattr(media, "probe_wan_runtime", lambda *args: {"ready": True, "backend": "amd"})
    calls = []

    def failed(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, code, "CUDA out of memory", "Synthetic worker stopped.")

    monkeypatch.setattr(media, "run_owned_media_process", failed)
    item = _pending()
    result = media.generate_media_sync("synthetic prompt", media_type="image", history_item_id=item)
    assert len(calls) == 1
    assert result["failure_kind"] == kind
    assert media.get_history_item(item)["status"] == "failed"


def test_native_allocator_retry_recovers_same_history_item_with_runtime_changes(media_db, monkeypatch):
    _install_memory_options(media_db)
    config = dict(_fake_config(media_db), image_resolution="512x512", image_num_inference_steps=6)
    monkeypatch.setattr(media, "get_wan2gp_config", lambda: config)
    monkeypatch.setattr(media, "probe_wan_runtime", lambda *args: {"ready": True, "backend": "nvidia"})
    calls = []

    def generate(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 3221225477, "", "Exception Code: 0xC0000005\nram_alloc_win_amd64.dll")
        assert command[command.index("--profile") + 1] == "5"
        assert command[command.index("--ram-allocator") + 1] == "default"
        output = Path(command[command.index("--output-dir") + 1]) / "synthetic-recovered.png"
        Image.new("RGB", (32, 32), "navy").save(output)
        return subprocess.CompletedProcess(command, 0, "done", "")

    monkeypatch.setattr(media, "run_owned_media_process", generate)
    item = _pending()
    result = media.generate_media_sync("synthetic prompt", media_type="image", optimized_prompt="synthetic prompt", history_item_id=item)
    assert result["status"] == "success" and result["item_id"] == item
    assert result["retry_attempted"] is True and len(calls) == 2
    assert media.get_history_item(item)["status"] == "completed"
    assert len(media.list_history()) == 1
    assert len(list(media.OUTPUT_DIR.glob("*/command.json"))) == 2


def test_unrelated_native_crash_is_not_retried_or_dumped_into_chat(media_db, monkeypatch):
    monkeypatch.setattr(media, "get_wan2gp_config", lambda: _fake_config(media_db))
    monkeypatch.setattr(media, "probe_wan_runtime", lambda *args: {"ready": True, "backend": "amd"})
    calls = []
    stack = "Exception Code: 0xC0000005\n" + "synthetic_driver.dll 0x12345678\n" * 300

    def failed(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 3221225477, "", stack)

    monkeypatch.setattr(media, "run_owned_media_process", failed)
    item = _pending()
    result = media.generate_media_sync("synthetic prompt", media_type="image", history_item_id=item)
    assert len(calls) == 1 and result["failure_kind"] == "native_crash"
    assert result["runtime_repair_required"] is True
    assert "access violation" in result["message"]
    assert "synthetic_driver.dll" not in result["message"]
    assert len(result["message"]) < 1000
    assert stack.encode() == read_secure_file(next(media.OUTPUT_DIR.glob("*/stderr.txt")))


def test_settings_reject_invalid_memory_profile_before_writing(media_db, monkeypatch):
    monkeypatch.setattr(website, "_check_auth", lambda request: True)
    monkeypatch.setattr(media, "_wan2gp_config_path", lambda **kwargs: pytest.fail("Invalid settings must not reach storage"))
    response = TestClient(website.app).post("/api/config", json={"memory_profile": "unsafe"})
    assert response.status_code == 400
    assert "valid Wan2GP memory profile" in response.json()["error"]


def test_generation_checks_gpu_before_launch_and_finishes_failure(media_db, monkeypatch):
    monkeypatch.setattr(media, "get_wan2gp_config", lambda: _fake_config(media_db))
    monkeypatch.setattr(media, "probe_wan_runtime", lambda *args: {"ready": False, "error": "Synthetic GPU runtime failure."})
    monkeypatch.setattr(media, "run_owned_media_process", lambda *args, **kwargs: pytest.fail("Must not launch an unusable runtime"))
    item = _pending()
    result = media.generate_media_sync("synthetic prompt", media_type="image", history_item_id=item)
    assert result["status"] == "error"
    assert media.get_history_item(item)["status"] == "failed"
    assert "Synthetic GPU runtime failure" in result["message"]


@pytest.mark.parametrize("outcome", ["valid", "corrupt", "timeout"])
def test_generation_finishes_history_and_validates_image(media_db, monkeypatch, outcome):
    monkeypatch.setattr(media, "get_wan2gp_config", lambda: _fake_config(media_db))
    monkeypatch.setattr(media, "probe_wan_runtime", lambda *args: {"ready": True, "backend": "amd"})
    calls = []

    def generate(command, **kwargs):
        settings = json.loads(Path(command[command.index("--process") + 1]).read_text(encoding="utf-8"))
        calls.append(settings)
        assert kwargs["timeout"] == 10
        if outcome == "timeout":
            return subprocess.CompletedProcess(command, 124, "", "Generation timed out.")
        output = Path(command[command.index("--output-dir") + 1]) / "synthetic.png"
        if outcome == "valid":
            Image.new("RGB", (32, 32), "navy").save(output)
        else:
            output.write_bytes(b"synthetic invalid image")
        return subprocess.CompletedProcess(command, 0, "synthetic progress", "")

    monkeypatch.setattr(media, "run_owned_media_process", generate)
    item = _pending()
    result = media.generate_media_sync("synthetic prompt", media_type="image", history_item_id=item)
    row = media.get_history_item(item)
    assert row["status"] == ("completed" if outcome == "valid" else "failed")
    assert result["status"] == ("success" if outcome == "valid" else "error")
    assert row["progress_message"] == ""
    assert calls[0]["settings_version"] == 2.79
    assert "sample_solver" not in calls[0] and "flow_shift" not in calls[0]
    assert "enhance_prompt" not in calls[0]


def test_history_recovers_interrupted_jobs_and_keeps_live_owner(media_db, monkeypatch):
    import sqlite3
    dead, live, legacy = _pending(), _pending(), _pending()
    with sqlite3.connect(media.DB_PATH) as conn:
        conn.execute("UPDATE media_history SET owner_pid=111 WHERE id=?", (dead,))
        conn.execute("UPDATE media_history SET owner_pid=222 WHERE id=?", (live,))
        conn.execute("UPDATE media_history SET owner_pid=NULL WHERE id=?", (legacy,))
    monkeypatch.setattr(media, "_job_owner_alive", lambda pid, started: pid == 222)
    rows = {row["id"]: row for row in media.list_history()}
    assert rows[dead]["status"] == rows[legacy]["status"] == "failed"
    assert rows[live]["status"] == "queued"


def test_generation_slot_is_shared_and_queue_wait_is_bounded(media_db):
    acquired, release = threading.Event(), threading.Event()

    def hold():
        with media._generation_slot(None, 3):
            acquired.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert acquired.wait(3)
        with pytest.raises(TimeoutError, match="queue wait timed out"):
            with media._generation_slot(None, 0.2):
                pytest.fail("A second job acquired the busy GPU slot")
    finally:
        release.set()
        thread.join(5)
    with media._generation_slot(None, 1):
        pass


def test_config_readiness_uses_gpu_probe_not_path_existence(media_db, monkeypatch):
    monkeypatch.setattr(website, "_check_auth", lambda request: True)
    monkeypatch.setattr(website, "get_wan2gp_config", lambda: _fake_config(media_db))
    monkeypatch.setattr(website, "probe_wan_runtime", lambda *args: {"ready": False, "error": "Synthetic unavailable GPU"})
    response = TestClient(website.app).get("/api/config")
    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.json()["diagnostic"]["runtime"]["error"] == "Synthetic unavailable GPU"


def test_thread_start_failure_finishes_job(media_db, monkeypatch):
    from types import SimpleNamespace
    class BrokenThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("synthetic thread failure")

    monkeypatch.setattr(website, "_check_auth", lambda request: True)
    monkeypatch.setattr(website, "threading", SimpleNamespace(Thread=BrokenThread))
    response = TestClient(website.app).post("/api/generate", json={"prompt": "synthetic prompt", "media_type": "image"})
    assert response.status_code == 500
    assert media.list_history()[0]["status"] == "failed"


def test_amd_managed_install_is_supported_when_target_is_detected():
    supported, guidance = media._install_support_for_machine({"system": "Windows", "amd_targets": ["gfx1151"], "has_nvidia": False})
    assert supported
    assert "ROCm" in guidance and "3.12" in guidance


def test_media_runtime_data_is_excluded_from_compiled_packages():
    root = Path(media.__file__).resolve().parents[2]
    for relative in ("servers/windows/build-backend.ps1", "servers/macos/build-backend.sh"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "autoyou_agents/media_generation_agent/media_generation_agent/**/*" in source
        assert "media_generation_agent/**/*" in source
    assert "*.gpu.lock" in (root / ".gitignore").read_text(encoding="utf-8")
