# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from autoyou_agents.fine_tuning_agent.training_data import (
    build_dataset_from_folder,
    parse_csv_messages,
    parse_json_dataset,
    parse_live_telegram_user_log,
    parse_whatsapp_history_dump,
)
from autoyou_agents.fine_tuning_agent.training_runner import (
    VisionDataset,
    _accelerator_backend,
    _build_masked_sample,
    _ollama_base_supports_vision,
    _write_modelfile,
)
import shared.secure_storage as secure_storage
from shared.secure_storage import FILE_HEADER, disable_secure_storage, enable_secure_storage, read_secure_file


class _SyntheticTokenizer:
    chat_template = "[SYSTEM_PROMPT]system[/SYSTEM_PROMPT][INST]user[/INST]"
    bos_token = "<s>"
    eos_token = "</s>"

    def __init__(self) -> None:
        self._ids: dict[str, int] = {}

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        del add_special_tokens
        tokens: list[int] = []
        for char in text:
            if char not in self._ids:
                self._ids[char] = len(self._ids) + 1
            tokens.append(self._ids[char])
        return tokens


class _SyntheticTelegramUserService:
    def __init__(self, status, log) -> None:
        self.status = status
        self.log = log
        self.log_reads = 0

    def get_status(self):
        return self.status

    def get_message_log(self):
        self.log_reads += 1
        return self.log

    def iter_dialogs(self):
        raise AssertionError("Fine tuning must not enumerate Telegram dialogs.")


class _SyntheticVisionProcessor:
    def apply_chat_template(self, conversation, **kwargs):
        del kwargs
        return json.dumps(conversation, sort_keys=True)


def _isolate_fine_tuning_tool(monkeypatch, tmp_path: Path):
    from autoyou_agents.fine_tuning_agent import fine_tuning_tool

    data_dir = tmp_path / "runtime" / "AutoYou" / "fine_tuning_agent"
    workspace_dir = data_dir / "workspace"
    monkeypatch.setattr(fine_tuning_tool, "DATA_DIR", data_dir)
    monkeypatch.setattr(fine_tuning_tool, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(fine_tuning_tool, "DATASETS_DIR", workspace_dir / "datasets")
    monkeypatch.setattr(fine_tuning_tool, "RUNS_DIR", workspace_dir / "runs")
    monkeypatch.setattr(fine_tuning_tool, "DUMPS_DIR", workspace_dir / "dumps")
    monkeypatch.setattr(fine_tuning_tool, "HF_CACHE_DIR", workspace_dir / "hf_cache")
    monkeypatch.setattr(fine_tuning_tool, "TOOLS_DIR", workspace_dir / "tools")
    monkeypatch.setattr(fine_tuning_tool, "LLAMA_CPP_DIR", workspace_dir / "tools" / "llama.cpp")
    monkeypatch.setattr(fine_tuning_tool, "DB_PATH", data_dir / "fine_tuning_history.db")
    fine_tuning_tool.init_db()
    return fine_tuning_tool


def _insert_synthetic_training_job(fine_tuning_tool, job_id: str, created_at: str) -> Path:
    job_dir = fine_tuning_tool.RUNS_DIR / job_id
    output_dir = job_dir / "adapter"
    ollama_dir = job_dir / "ollama"
    output_dir.mkdir(parents=True)
    ollama_dir.mkdir(parents=True)
    (output_dir / "adapter_model.safetensors").write_bytes((job_id * 64).encode("utf-8"))
    log_path = job_dir / "training.log"
    log_path.write_text("synthetic completed run\n", encoding="utf-8")
    with fine_tuning_tool._connect() as conn:
        conn.execute(
            """
            INSERT INTO training_jobs (
                id, dataset_id, created_at, updated_at, completed_at, status, progress_percent,
                eta_seconds, title, model_name, training_model_id, ollama_base_model,
                output_dir, ollama_dir, modelfile_path, log_path, return_code,
                install_status, config_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "ds_synthetic",
                created_at,
                created_at,
                created_at,
                "completed",
                100.0,
                0,
                f"Synthetic {job_id}",
                f"autoyou-{job_id}",
                "synthetic/model",
                "ministral-3:8b",
                str(output_dir),
                str(ollama_dir),
                str(ollama_dir / "Modelfile"),
                str(log_path),
                0,
                None,
                "{}",
            ),
        )
        conn.commit()
    return job_dir


def test_training_runner_command_reenters_the_packaged_launcher(monkeypatch, tmp_path: Path) -> None:
    from autoyou_agents.fine_tuning_agent import fine_tuning_tool

    source_python = tmp_path / "source" / "python"
    source_python.parent.mkdir()
    monkeypatch.setattr(fine_tuning_tool.sys, "executable", str(source_python))
    assert fine_tuning_tool._training_runner_command() == [
        str(source_python),
        "-m",
        "autoyou_agents.fine_tuning_agent.training_runner",
    ]

    packaged_launcher = tmp_path / "AutoYou" / "AutoYou.exe"
    (packaged_launcher.parent / "runtime_modules").mkdir(parents=True)
    monkeypatch.setattr(fine_tuning_tool.sys, "executable", str(packaged_launcher))
    assert fine_tuning_tool._training_runner_command() == [
        str(packaged_launcher.resolve()),
        "--run-fine-tuning-runner",
    ]


def test_csv_sender_message_builds_owner_reply_samples() -> None:
    csv_text = "\n".join(
        [
            "timestamp,sender,message",
            "2026-01-01 10:00,Synthetic Contact,Are you available later?",
            '2026-01-01 10:01,Synthetic Owner,"Yes, after lunch works."',
        ]
    )

    result = parse_csv_messages(csv_text, me_name="Synthetic Owner")

    assert result.message_count == 2
    assert result.assistant_message_count == 1
    assert result.warnings == []
    assert len(result.samples) == 1
    sample_messages = result.samples[0]["messages"]
    assert sample_messages[-2] == {
        "role": "user",
        "content": "Are you available later?",
    }
    assert sample_messages[-1] == {
        "role": "assistant",
        "content": "Yes, after lunch works.",
    }


def test_whatsapp_history_dump_marks_group_context_and_owner_replies() -> None:
    payload = {
        "messages": [
            {
                "body": "Can you review the draft?",
                "fromMe": False,
                "senderName": "Synthetic Teammate",
                "timestamp": 1767225600,
                "chat": {"id": "synthetic-group@g.us", "name": "Synthetic Group", "isGroup": True},
            },
            {
                "body": "I will send notes this afternoon.",
                "fromMe": True,
                "timestamp": 1767225660,
                "chat": {"id": "synthetic-group@g.us", "name": "Synthetic Group", "isGroup": True},
            },
        ]
    }

    messages = parse_whatsapp_history_dump(payload)

    assert len(messages) == 2
    assert messages[0].sender == "Synthetic Teammate"
    assert messages[0].text == "Synthetic Teammate: Can you review the draft?"
    assert messages[0].chat_name == "Synthetic Group"
    assert messages[1].sender == "me"
    assert messages[1].from_me is True


def test_telegram_user_live_log_keeps_only_owner_saved_messages() -> None:
    messages = parse_live_telegram_user_log(
        [
            {
                "message": "Synthetic saved note",
                "timestamp": 1767225600,
                "saved_messages": True,
                "owner_scoped": True,
                "direction": "assistant",
            },
            {
                "message": "/pair synthetic-code",
                "saved_messages": True,
                "owner_scoped": True,
                "direction": "user",
            },
            {"message": "Synthetic unscoped note"},
            {"message": "Synthetic forwarded note", "is_forwarded": True},
            {"message": "Synthetic external note", "owner_scoped": False},
        ]
    )

    assert len(messages) == 1
    assert messages[0].from_me is True
    assert messages[0].chat_name == "Telegram Saved Messages"
    assert messages[0].source == "telegram_user_live"


def test_ministral_prompt_masking_trains_only_assistant_tokens() -> None:
    tokenizer = _SyntheticTokenizer()
    assistant_text = "Synthetic owner reply"
    sample = _build_masked_sample(
        [
            {"role": "system", "content": "Synthetic system prompt"},
            {"role": "user", "content": "Synthetic user message"},
            {"role": "assistant", "content": assistant_text},
        ],
        tokenizer,
        max_length=512,
    )

    assert sample is not None
    assistant_tokens = tokenizer.encode(f"{assistant_text}{tokenizer.eos_token}", add_special_tokens=False)
    assert sample["labels"][-len(assistant_tokens) :] == assistant_tokens
    assert all(label == -100 for label in sample["labels"][: -len(assistant_tokens)])
    assert sample["attention_mask"] == [1] * len(sample["input_ids"])


def test_write_modelfile_prefers_gguf_adapter(tmp_path: Path) -> None:
    output_dir = tmp_path / "run"
    adapter_dir = output_dir / "adapter"
    ollama_dir = output_dir / "ollama"
    adapter_dir.mkdir(parents=True)
    ollama_dir.mkdir(parents=True)
    (ollama_dir / "adapter.gguf").write_bytes(b"synthetic-gguf")

    modelfile = _write_modelfile(
        output_dir=output_dir,
        ollama_dir=ollama_dir,
        ollama_model_name="autoyou-synthetic-model",
        ollama_base_model="ministral-3:8b",
        adapter_dir=adapter_dir,
    )

    text = modelfile.read_text(encoding="utf-8")
    manifest = json.loads((ollama_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "FROM ministral-3:8b" in text
    assert "ADAPTER adapter.gguf" in text
    assert Path(manifest["adapter_path"]).name == "adapter.gguf"


def test_fine_tuning_status_uses_autoyou_test_root(monkeypatch, autoyou_test_root: Path) -> None:
    from autoyou_agents.fine_tuning_agent import fine_tuning_tool

    monkeypatch.setattr(
        fine_tuning_tool,
        "list_ollama_models",
        lambda: {"status": "success", "models": []},
    )
    monkeypatch.setattr(
        fine_tuning_tool,
        "get_live_whatsapp_snapshot",
        lambda: {"available": False, "messages": []},
    )
    monkeypatch.setattr(
        fine_tuning_tool,
        "get_whatsapp_history_dump_support",
        lambda: {"available": False, "reason": "synthetic test"},
    )
    monkeypatch.setattr(
        fine_tuning_tool,
        "get_live_telegram_user_snapshot",
        lambda: {"available": False, "message": "synthetic test"},
    )

    status = fine_tuning_tool.get_fine_tuning_status()

    assert Path(status["workspace"]).resolve().is_relative_to(autoyou_test_root)
    assert Path(status["db_path"]).resolve().is_relative_to(autoyou_test_root)
    assert fine_tuning_tool._data_collector_root().resolve().is_relative_to(autoyou_test_root)
    assert status["counts"] == {"datasets": 0, "jobs": 0, "dump_jobs": 0}
    assert status["telegram_user"] == {"available": False, "message": "synthetic test"}
    images = status["training_modalities"]["images"]
    assert images["dataset_ready"] is True
    assert "training_ready" in images
    assert "available" in status["training_capability"]["cpu"]
    assert "mlx" in status["training_capability"]


def test_folder_and_json_imports_build_trainable_samples(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-export"
    source.mkdir()
    (source / "conversations.json").write_text(
        json.dumps(
            [
                {
                    "messages": [
                        {"role": "user", "content": "Synthetic question"},
                        {"role": "assistant", "content": "Synthetic answer"},
                    ]
                }
            ]
        ),
        encoding="utf-8",
    )
    (source / "notes.md").write_text("Synthetic reference note.", encoding="utf-8")

    parsed_json = parse_json_dataset((source / "conversations.json").read_text(encoding="utf-8"))
    imported = build_dataset_from_folder(source, max_total_bytes=1024 * 1024)

    assert len(parsed_json.samples) == 1
    assert len(imported.samples) == 2
    assert imported.assistant_message_count == 2


def test_hardware_profile_defaults_to_prepare_only_without_cuda(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent import fine_tuning_tool

    capability = {"real_training_ready": False, "nvidia_smi": {"gpus": []}}
    monkeypatch.setattr(fine_tuning_tool, "_training_capability_status", lambda: capability)

    default_config = fine_tuning_tool._normalize_job_config({})
    explicit_training = fine_tuning_tool._normalize_job_config({"prepare_only": False, "max_seq_length": 256})

    assert default_config["prepare_only"] is True
    assert default_config["max_seq_length"] == 512
    assert explicit_training["prepare_only"] is False
    assert explicit_training["max_seq_length"] == 256


def test_explicit_cpu_opt_in_requests_training_not_only_preparation(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent import fine_tuning_tool

    capability = {"real_training_ready": False, "nvidia_smi": {"gpus": []}}
    monkeypatch.setattr(fine_tuning_tool, "_training_capability_status", lambda: capability)

    cpu_training = fine_tuning_tool._normalize_job_config({"allow_cpu_training": True})
    explicit_prepare = fine_tuning_tool._normalize_job_config(
        {"allow_cpu_training": True, "prepare_only": True}
    )

    assert cpu_training["allow_cpu_training"] is True
    assert cpu_training["prepare_only"] is False
    assert explicit_prepare["prepare_only"] is True


def test_vision_pixel_budget_requires_a_valid_range() -> None:
    from autoyou_agents.fine_tuning_agent import fine_tuning_tool

    try:
        fine_tuning_tool._normalize_job_config({"vision_min_pixels": 100, "vision_max_pixels": 99})
    except ValueError as exc:
        assert "max must be at least min" in str(exc)
    else:
        raise AssertionError("Invalid vision pixel settings must be rejected.")


def test_fine_tuning_agent_exposes_training_not_message_collection_tools() -> None:
    from autoyou_agents.fine_tuning_agent.agent import create_fine_tuning_agent

    agent = create_fine_tuning_agent("synthetic-model")
    tool_names = {getattr(tool, "__name__", getattr(tool, "name", "")) for tool in agent.tools}

    assert "import_data_collector_handoff" in tool_names
    assert "cancel_fine_tuning_job" in tool_names
    assert "create_whatsapp_live_dataset" not in tool_names


def test_queued_training_job_can_be_cancelled_without_starting_a_trainer(monkeypatch, tmp_path: Path) -> None:
    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    dataset = fine_tuning_tool.create_dataset_from_upload(
        filename="synthetic.jsonl",
        data=b'{"messages":[{"role":"user","content":"Synthetic prompt"},{"role":"assistant","content":"Synthetic reply"}]}\n',
    )["dataset"]

    class NoopThread:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def start(self) -> None:
            return None

    monkeypatch.setattr(fine_tuning_tool.threading, "Thread", NoopThread)
    started = fine_tuning_tool.start_training_job(dataset_id=dataset["id"])
    job_id = started["job"]["id"]

    cancelled = fine_tuning_tool.cancel_fine_tuning_job(job_id)

    assert cancelled["status"] == "cancelled"
    assert fine_tuning_tool.get_training_job(job_id)["job"]["status"] == "cancelled"


def test_running_training_job_escalates_a_halt_to_kill_after_the_grace_period(monkeypatch, tmp_path: Path) -> None:
    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    dataset = fine_tuning_tool.create_dataset_from_upload(
        filename="synthetic.jsonl",
        data=b'{"messages":[{"role":"user","content":"Synthetic prompt"},{"role":"assistant","content":"Synthetic reply"}]}\n',
    )["dataset"]

    class StubbornProcess:
        returncode = None
        terminated = False
        killed = False

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    class NoopThread:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def start(self) -> None:
            return None

    process = StubbornProcess()
    monkeypatch.setattr(fine_tuning_tool.threading, "Thread", NoopThread)
    job_id = fine_tuning_tool.start_training_job(dataset_id=dataset["id"])["job"]["id"]
    monotonic_values = iter((0.0, float(fine_tuning_tool._CANCEL_GRACE_SECONDS + 1)))
    cancel_states = iter((False, True, True, True))
    monkeypatch.setattr(fine_tuning_tool, "_training_cancel_requested", lambda _: next(cancel_states))
    monkeypatch.setattr(fine_tuning_tool, "_start_logged_process", lambda *args, **kwargs: (process, None, []))
    monkeypatch.setattr(fine_tuning_tool, "_finish_logged_process", lambda *args, **kwargs: None)
    # The module's own seams, not `fine_tuning_tool.time` - that attribute *is*
    # the shared `time` module, so patching it replaces `time.monotonic` for the
    # whole process. Any asyncio loop left running by another test then calls
    # this lambda on every tick, drains the two-value iterator, and dies with
    # StopIteration - which is exactly how this test used to fail, and only when
    # run alongside the tests that leave one running.
    monkeypatch.setattr(fine_tuning_tool, "_monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(fine_tuning_tool, "_sleep", lambda _: None)

    fine_tuning_tool._run_training_job(job_id)

    assert process.terminated is True
    assert process.killed is True
    assert fine_tuning_tool.get_training_job(job_id)["job"]["status"] == "cancelled"


def test_dataset_can_be_removed_when_no_job_references_it(monkeypatch, tmp_path: Path) -> None:
    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    result = fine_tuning_tool.create_dataset_from_upload(
        filename="synthetic.jsonl",
        data=b'{"messages":[{"role":"user","content":"Synthetic prompt"},{"role":"assistant","content":"Synthetic reply"}]}\n',
    )
    dataset_id = result["dataset"]["id"]

    deleted = fine_tuning_tool.delete_dataset(dataset_id)

    assert deleted == {"status": "success", "deleted_dataset_id": dataset_id}
    assert fine_tuning_tool.get_dataset(dataset_id) is None
    assert not (fine_tuning_tool.DATASETS_DIR / dataset_id).exists()


def test_batch_upload_prepares_one_dataset_without_retaining_raw_files(monkeypatch, tmp_path: Path) -> None:
    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    result = fine_tuning_tool.create_dataset_from_uploads(
        files=[
            ("first.jsonl", b'{"messages":[{"role":"user","content":"Synthetic prompt one"},{"role":"assistant","content":"Synthetic reply one"}]}\n'),
            ("second.jsonl", b'{"messages":[{"role":"user","content":"Synthetic prompt two"},{"role":"assistant","content":"Synthetic reply two"}]}\n'),
        ],
        title="Synthetic dropped folder",
    )

    assert result["status"] == "success"
    dataset = result["dataset"]
    assert dataset["source_type"] == "uploads"
    assert dataset["sample_count"] == 2
    assert dataset["metadata"]["uploaded_file_count"] == 2
    assert not (fine_tuning_tool.DATASETS_DIR / dataset["id"] / "first.jsonl").exists()
    assert not (fine_tuning_tool.DATASETS_DIR / dataset["id"] / "second.jsonl").exists()


def test_batch_upload_bundles_verified_images_for_vision_training(monkeypatch, tmp_path: Path) -> None:
    from PIL import Image

    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2), color=(18, 52, 86)).save(image_bytes, format="PNG")
    manifest = json.dumps(
        {
            "messages": [
                {"role": "user", "content": "What is visible in this synthetic image?"},
                {"role": "assistant", "content": "A synthetic color sample."},
            ],
            "images": ["synthetic.png"],
        }
    ).encode("utf-8") + b"\n"

    result = fine_tuning_tool.create_dataset_from_uploads(
        files=[("samples.jsonl", manifest), ("synthetic.png", image_bytes.getvalue())],
        title="Synthetic vision dataset",
    )

    assert result["status"] == "success"
    dataset = result["dataset"]
    assert dataset["metadata"]["image_count"] == 1
    row = json.loads(read_secure_file(Path(dataset["train_path"])).decode("utf-8"))
    assert row["images"] == ["images/synthetic.png"]
    assert (fine_tuning_tool.DATASETS_DIR / dataset["id"] / "images" / "synthetic.png").is_file()


def test_vision_dataset_loads_only_its_private_bundled_image(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "images" / "synthetic.png"
    image_path.parent.mkdir()
    Image.new("RGB", (2, 2), color=(18, 52, 86)).save(image_path)
    dataset = VisionDataset(
        [
            {
                "messages": [
                    {"role": "user", "content": "Describe this synthetic image."},
                    {"role": "assistant", "content": "A synthetic color sample."},
                ],
                "images": ["images/synthetic.png"],
            }
        ],
        _SyntheticVisionProcessor(),
        tmp_path,
    )

    row = dataset[0]

    assert len(row["images"]) == 1
    assert row["images"][0].mode == "RGB"
    assert '"type": "image"' in row["text"]


def test_accelerator_backend_supports_cuda_rocm_mps_and_cpu() -> None:
    class _Cuda:
        def __init__(self, available: bool) -> None:
            self.available = available

        def is_available(self) -> bool:
            return self.available

    class _Mps:
        def __init__(self, available: bool) -> None:
            self.available = available

        def is_available(self) -> bool:
            return self.available

    def fake_torch(*, cuda: bool = False, hip: str | None = None, mps: bool = False):
        return SimpleNamespace(
            cuda=_Cuda(cuda),
            version=SimpleNamespace(hip=hip),
            backends=SimpleNamespace(mps=_Mps(mps)),
        )

    assert _accelerator_backend(fake_torch(cuda=True)) == "cuda"
    assert _accelerator_backend(fake_torch(cuda=True, hip="6.4")) == "rocm"
    assert _accelerator_backend(fake_torch(mps=True)) == "mps"
    assert _accelerator_backend(fake_torch()) == "cpu"


def test_ollama_vision_capability_probe_is_truthful_without_requiring_ollama(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent import training_runner

    monkeypatch.setattr(training_runner.shutil, "which", lambda _name: "synthetic-ollama")
    monkeypatch.setattr(
        training_runner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="Capabilities\n  completion\n  vision\n"),
    )
    assert _ollama_base_supports_vision("synthetic-vision") is True

    monkeypatch.setattr(
        training_runner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="Capabilities\n  completion\n  tools\n"),
    )
    assert _ollama_base_supports_vision("synthetic-text") is False

    monkeypatch.setattr(training_runner.shutil, "which", lambda _name: None)
    assert _ollama_base_supports_vision("synthetic-unavailable") is None


def test_data_collector_status_never_follows_loopback_redirects(monkeypatch, tmp_path: Path) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    target_hits: list[str] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            target_hits.append(self.path)
            body = b'{"loopback_handoff": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/redirected-health")
            self.end_headers()

        def log_message(self, _format: str, *_args) -> None:
            pass

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    try:
        for server in (target, redirect):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        result = fine_tuning_tool.get_data_collector_status(f"http://127.0.0.1:{redirect.server_port}")
    finally:
        redirect.shutdown()
        target.shutdown()
        redirect.server_close()
        target.server_close()

    assert result["available"] is False
    assert target_hits == []


def test_local_data_collector_export_imports_from_autoyou_runtime(monkeypatch, tmp_path: Path) -> None:
    disable_secure_storage()
    monkeypatch.setattr(secure_storage, "keyring_available", lambda: False)
    enable_secure_storage(app_name="AutoYou-test", root=tmp_path, password="synthetic-spm-password")
    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    collector_root = tmp_path / "runtime" / "data_collector_agent"
    export_dir = collector_root / "collected_context" / "index_unified" / "exports_for_training" / "export_synthetic"
    export_dir.mkdir(parents=True)
    train_path = export_dir / "train.jsonl"
    train_path.write_text(
        '{"messages":[{"role":"user","content":"Synthetic prompt"},{"role":"assistant","content":"Synthetic reply"}]}\n',
        encoding="utf-8",
    )
    (export_dir / "manifest.json").write_text(
        json.dumps(
            {
                "id": "export_synthetic",
                "title": "Synthetic Data Collector export",
                "train_path": str(train_path),
                "eval_path": None,
                "sample_count": 1,
                "train_sample_count": 1,
                "message_count": 2,
                "assistant_message_count": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fine_tuning_tool, "_data_collector_root", lambda: collector_root)

    try:
        imported = fine_tuning_tool.create_dataset_from_datacollector_export(export_id="export_synthetic")

        assert imported["status"] == "success"
        assert imported["dataset"]["source_type"] == "datacollector"
        imported_train = Path(imported["dataset"]["train_path"])
        assert imported_train.resolve().is_relative_to(tmp_path)
        assert imported_train.read_bytes().startswith(FILE_HEADER)
        assert b"Synthetic prompt" not in imported_train.read_bytes()
        assert read_secure_file(imported_train).startswith(b'{"messages"')
        assert train_path.read_bytes().startswith(b'{"messages"')
    finally:
        disable_secure_storage()


def test_telegram_user_dataset_requires_scoped_consent_and_never_echoes_log_content(monkeypatch, tmp_path: Path) -> None:
    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    private_text = "Synthetic private Saved Messages content"
    service = _SyntheticTelegramUserService(
        {
            "connected": True,
            "owner_scoped": True,
            "training_export_consent": False,
            "status": "connected",
            "last_error": private_text,
        },
        [
            {
                "message": "Synthetic prompt",
                "saved_messages": True,
                "owner_scoped": True,
                "direction": "user",
            },
            {
                "message": private_text,
                "saved_messages": True,
                "owner_scoped": True,
                "direction": "assistant",
            },
        ],
    )
    monkeypatch.setattr(
        fine_tuning_tool,
        "_runtime_server_state",
        lambda: SimpleNamespace(telegram_user_service=service),
    )

    denied = fine_tuning_tool.create_dataset_from_live_telegram_user()
    snapshot = fine_tuning_tool.get_live_telegram_user_snapshot()

    assert denied["status"] == "error"
    assert service.log_reads == 0
    assert private_text not in json.dumps(snapshot)
    assert "last_error" not in json.dumps(snapshot)

    service.status["training_export_consent"] = True
    allowed_snapshot = fine_tuning_tool.get_live_telegram_user_snapshot()
    result = fine_tuning_tool.create_dataset_from_live_telegram_user()

    assert allowed_snapshot["available"] is True
    assert allowed_snapshot["message_count"] == 2
    assert private_text not in json.dumps(allowed_snapshot)
    assert result["status"] == "success"
    assert service.log_reads == 2
    assert result["dataset"]["source_type"] == "telegram_user_live"
    assert result["dataset"]["metadata"]["source_scope"] == "telegram_saved_messages"
    assert private_text not in json.dumps(result)
    assert Path(result["dataset"]["train_path"]).resolve().is_relative_to(tmp_path)


def test_prune_old_runs_keeps_recent_runs_and_deletes_oldest(monkeypatch, tmp_path: Path) -> None:
    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    old_dir = _insert_synthetic_training_job(fine_tuning_tool, "job_old", "2026-01-01T00:00:00+00:00")
    mid_dir = _insert_synthetic_training_job(fine_tuning_tool, "job_mid", "2026-01-02T00:00:00+00:00")
    new_dir = _insert_synthetic_training_job(fine_tuning_tool, "job_new", "2026-01-03T00:00:00+00:00")

    result = fine_tuning_tool.prune_old_runs(max_runs=2, min_free_gb=0)

    assert result["status"] == "success"
    assert result["deleted_job_ids"] == ["job_old"]
    assert result["deleted_count"] == 1
    assert result["freed_bytes"] > 0
    assert not old_dir.exists()
    assert mid_dir.exists()
    assert new_dir.exists()


def test_prune_old_runs_under_low_disk_keeps_at_least_latest_run(monkeypatch, tmp_path: Path) -> None:
    fine_tuning_tool = _isolate_fine_tuning_tool(monkeypatch, tmp_path)
    old_dir = _insert_synthetic_training_job(fine_tuning_tool, "job_low_old", "2026-01-01T00:00:00+00:00")
    mid_dir = _insert_synthetic_training_job(fine_tuning_tool, "job_low_mid", "2026-01-02T00:00:00+00:00")
    new_dir = _insert_synthetic_training_job(fine_tuning_tool, "job_low_new", "2026-01-03T00:00:00+00:00")

    result = fine_tuning_tool.prune_old_runs(max_runs=2, min_free_gb=10**9)

    assert result["status"] == "success"
    assert set(result["deleted_job_ids"]) == {"job_low_old", "job_low_mid"}
    assert result["policy"]["low_disk"] is True
    assert result["policy"]["kept_recent_runs"] == 1
    assert not old_dir.exists()
    assert not mid_dir.exists()
    assert new_dir.exists()


def test_fine_tuning_ui_backend_protected_status_returns_401(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent.website.backend import app as fine_ui_backend

    mock_server = MagicMock()
    mock_server._is_logged_in.return_value = False
    mock_helpers = {
        "session_valid": lambda agent, token: False,
        "cookie_name": lambda agent: "fine_tuning_session",
        "runtime_server": lambda: mock_server,
    }
    monkeypatch.setattr(fine_ui_backend, "_import_auth_helpers", lambda: mock_helpers)

    client = TestClient(fine_ui_backend.app)
    response = client.get("/api/status")

    assert response.status_code == 401
    assert response.json()["success"] is False
    assert response.json()["error"] == "Not authenticated"


def test_fine_tuning_ui_backend_requests_a_safe_job_cancellation(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent.website.backend import app as fine_ui_backend

    monkeypatch.setattr(fine_ui_backend, "_check_auth", lambda request: True)
    monkeypatch.setattr(
        fine_ui_backend,
        "cancel_fine_tuning_job",
        lambda job_id: {"status": "cancelling", "message": "Synthetic cancellation requested.", "job": {"id": job_id}},
    )

    response = TestClient(fine_ui_backend.app).post("/api/jobs/job_synthetic/cancel")

    assert response.status_code == 202
    assert response.json()["success"] is True
    assert response.json()["status"] == "cancelling"


def test_fine_tuning_ui_backend_checks_requested_collector_url(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent.website.backend import app as fine_ui_backend

    mock_helpers = {
        "session_valid": lambda agent, token: token == "valid-token",
        "cookie_name": lambda agent: "fine_tuning_session",
        "runtime_server": MagicMock(),
    }
    monkeypatch.setattr(fine_ui_backend, "_import_auth_helpers", lambda: mock_helpers)
    monkeypatch.setattr(
        fine_ui_backend,
        "get_data_collector_status",
        lambda url: {"available": True, "url": url, "message": "Synthetic collector ready."},
    )

    client = TestClient(fine_ui_backend.app)
    response = client.get(
        "/api/datacollector/status",
        headers={"Authorization": "Bearer valid-token"},
        params={"url": "http://127.0.0.1:18067"},
    )

    assert response.status_code == 200
    assert response.json()["url"] == "http://127.0.0.1:18067"


def test_fine_tuning_ui_backend_auth_login_issues_token_on_valid_totp(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent.website.backend import app as fine_ui_backend

    mock_server = MagicMock()
    mock_server.STATE.config = {}
    mock_server._default_config.return_value = {}
    mock_server._get_pairing_totp_secret.return_value = "TESTSECRET"
    mock_server._verify_totp_secret.return_value = True
    mock_helpers = {
        "totp_capabilities": lambda: {"totp_configured": True},
        "create_session": lambda agent, days: "test-fine-tuning-token",
        "session_valid": lambda agent, token: token == "test-fine-tuning-token",
        "delete_session": MagicMock(),
        "cookie_name": lambda agent: "fine_tuning_session",
        "cookie_path": lambda agent: "/agent/fine_tuning_agent",
        "runtime_server": lambda: mock_server,
    }
    monkeypatch.setattr(fine_ui_backend, "_import_auth_helpers", lambda: mock_helpers)

    client = TestClient(fine_ui_backend.app)
    response = client.post("/api/auth/login", json={"totp_code": "123456"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["authenticated"] is True
    assert payload["token"] == "test-fine-tuning-token"


def test_fine_tuning_ui_backend_upload_csv_creates_dataset_without_content_echo(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent.website.backend import app as fine_ui_backend

    mock_helpers = {
        "session_valid": lambda agent, token: token == "valid-token",
        "cookie_name": lambda agent: "fine_tuning_session",
        "runtime_server": MagicMock(),
    }
    monkeypatch.setattr(fine_ui_backend, "_import_auth_helpers", lambda: mock_helpers)
    csv_text = "\n".join(
        [
            "timestamp,sender,message",
            "2026-01-01 10:00,Synthetic Contact,Are you available later?",
            '2026-01-01 10:01,Synthetic Owner,"Yes, after lunch works."',
        ]
    )

    client = TestClient(fine_ui_backend.app)
    response = client.post(
        "/api/datasets/upload",
        headers={"Authorization": "Bearer valid-token"},
        data={"me_name": "Synthetic Owner", "title": "Synthetic upload"},
        files={"file": ("synthetic.csv", csv_text.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    dataset = payload["dataset"]
    assert dataset["title"] == "Synthetic upload"
    assert dataset["source_type"] == "upload"
    assert dataset["sample_count"] == 1
    assert dataset["message_count"] == 2
    assert dataset["assistant_message_count"] == 1
    response_text = json.dumps(payload, sort_keys=True)
    assert "Are you available later?" not in response_text
    assert "Yes, after lunch works." not in response_text


def test_fine_tuning_ui_backend_batch_upload_creates_one_dataset(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent.website.backend import app as fine_ui_backend

    mock_helpers = {
        "session_valid": lambda agent, token: token == "valid-token",
        "cookie_name": lambda agent: "fine_tuning_session",
        "runtime_server": MagicMock(),
    }
    monkeypatch.setattr(fine_ui_backend, "_import_auth_helpers", lambda: mock_helpers)

    client = TestClient(fine_ui_backend.app)
    response = client.post(
        "/api/datasets/uploads",
        headers={"Authorization": "Bearer valid-token"},
        data={"title": "Synthetic dropped files"},
        files=[
            ("files", ("first.jsonl", b'{"messages":[{"role":"user","content":"Prompt one"},{"role":"assistant","content":"Reply one"}]}\n', "application/jsonl")),
            ("files", ("second.jsonl", b'{"messages":[{"role":"user","content":"Prompt two"},{"role":"assistant","content":"Reply two"}]}\n', "application/jsonl")),
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["dataset"]["source_type"] == "uploads"
    assert payload["dataset"]["sample_count"] == 2
    assert "Prompt one" not in json.dumps(payload)


def test_fine_tuning_ui_backend_creates_telegram_user_dataset(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent.website.backend import app as fine_ui_backend

    mock_helpers = {
        "session_valid": lambda agent, token: token == "valid-token",
        "cookie_name": lambda agent: "fine_tuning_session",
        "runtime_server": MagicMock(),
    }
    monkeypatch.setattr(fine_ui_backend, "_import_auth_helpers", lambda: mock_helpers)
    monkeypatch.setattr(
        fine_ui_backend,
        "create_dataset_from_live_telegram_user",
        lambda title=None: {
            "status": "success",
            "dataset": {
                "id": "ds_synthetic",
                "title": title,
                "source_type": "telegram_user_live",
                "sample_count": 1,
            },
        },
    )

    client = TestClient(fine_ui_backend.app)
    response = client.post(
        "/api/datasets/telegram-user",
        headers={"Authorization": "Bearer valid-token"},
        json={"title": "Synthetic Saved Messages"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["dataset"]["source_type"] == "telegram_user_live"


def test_fine_tuning_ui_backend_upload_rejects_unsupported_file(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent.website.backend import app as fine_ui_backend

    mock_helpers = {
        "session_valid": lambda agent, token: token == "valid-token",
        "cookie_name": lambda agent: "fine_tuning_session",
        "runtime_server": MagicMock(),
    }
    monkeypatch.setattr(fine_ui_backend, "_import_auth_helpers", lambda: mock_helpers)

    client = TestClient(fine_ui_backend.app)
    response = client.post(
        "/api/datasets/upload",
        headers={"Authorization": "Bearer valid-token"},
        files={"file": ("synthetic.pdf", b"synthetic private line", "application/pdf")},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["success"] is False
    assert "Unsupported file type" in " ".join(payload["warnings"])
    assert "synthetic private line" not in json.dumps(payload)


def test_fine_tuning_ui_backend_cleanup_endpoint_runs_prune(monkeypatch) -> None:
    from autoyou_agents.fine_tuning_agent.website.backend import app as fine_ui_backend

    mock_helpers = {
        "session_valid": lambda agent, token: token == "valid-token",
        "cookie_name": lambda agent: "fine_tuning_session",
        "runtime_server": MagicMock(),
    }
    monkeypatch.setattr(fine_ui_backend, "_import_auth_helpers", lambda: mock_helpers)
    monkeypatch.setattr(
        fine_ui_backend,
        "prune_old_runs",
        lambda max_runs=None, min_free_gb=None: {
            "status": "success",
            "deleted_count": 1,
            "deleted_job_ids": ["job_synthetic_old"],
            "freed_bytes": 128,
            "policy": {"max_runs": max_runs, "min_free_gb": min_free_gb},
        },
    )

    client = TestClient(fine_ui_backend.app)
    response = client.post(
        "/api/jobs/cleanup",
        headers={"Authorization": "Bearer valid-token"},
        json={"max_runs": 3, "min_free_gb": 4},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["deleted_count"] == 1
    assert payload["deleted_job_ids"] == ["job_synthetic_old"]
    assert payload["policy"] == {"max_runs": 3, "min_free_gb": 4.0}
