# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Trainer progress protocol and the MLX executor's host-independent parts.

None of this needs Apple silicon: the dataset layout, command line, log parsing
and adapter conversion are plain data transformations. The first run on a Mac is
the integration test; these tests pin everything that can be pinned before it.
"""

from __future__ import annotations

import json
import math

import pytest

from autoyou_agents.fine_tuning_agent import mlx_executor, progress

np = pytest.importorskip("numpy")
safetensors_numpy = pytest.importorskip("safetensors.numpy")


# ---------------------------------------------------------------- progress
def test_progress_line_round_trips_and_carries_an_eta():
    line = progress.format_progress(step=25, total_steps=100, loss=1.5, learning_rate=1e-4, seconds_per_step=2.0,
                                    memory_gib=12.3, tokens_per_second=900.0)
    assert line.startswith(progress.PROGRESS_PREFIX)
    update = progress.parse_progress_line("2026-11-21 [train] " + line)
    assert update["step"] == 25 and update["total_steps"] == 100 and update["loss"] == 1.5
    assert update["eta_seconds"] == 150 and update["memory_gib"] == 12.3


def test_non_finite_numbers_are_reported_as_null_never_as_nan():
    line = progress.format_progress(step=3, total_steps=10, loss=float("nan"), grad_norm=float("inf"))
    payload = json.loads(line[len(progress.PROGRESS_PREFIX):])  # strict JSON: NaN would not parse
    assert payload["loss"] is None and payload["grad_norm"] is None and payload["non_finite_loss"] is True


@pytest.mark.parametrize("line", ["", "plain log line", "AUTOYOU_PROGRESS not-json", 'AUTOYOU_PROGRESS {"no_step": 1}'])
def test_garbage_lines_are_not_progress(line):
    assert progress.parse_progress_line(line) is None


def test_last_progress_picks_the_newest_and_percent_stays_inside_the_training_band():
    lines = [progress.format_progress(step=s, total_steps=50) for s in (1, 10, 40)] + ["Saving checkpoint"]
    assert progress.last_progress(lines)["step"] == 40
    assert progress.last_progress(["nothing"]) is None
    assert progress.progress_percent({"step": 0, "total_steps": 50}) == 2.0
    assert progress.progress_percent({"step": 50, "total_steps": 50}) == 95.0
    assert progress.progress_percent({"step": 999, "total_steps": 50}) == 95.0
    assert progress.progress_percent({"step": 5, "total_steps": 0}) is None and progress.progress_percent(None) is None


def test_mlx_log_lines_feed_the_same_protocol():
    train = progress.from_mlx_log_line(
        "Iter 10: Train loss 2.345, Learning Rate 1.000e-05, It/sec 0.500, Tokens/sec 123.4, Trained Tokens 1234, Peak mem 5.678 GB",
        total_steps=100,
    )
    assert train["step"] == 10 and train["loss"] == 2.345 and train["seconds_per_step"] == 2.0
    assert train["eta_seconds"] == 180 and train["tokens_per_second"] == 123.4 and train["memory_gib"] == 5.678
    val = progress.from_mlx_log_line("Iter 100: Val loss 2.100, Val took 3.200s", total_steps=100)
    assert val == {"phase": "eval", "step": 100, "total_steps": 100, "eval_loss": 2.1}
    assert progress.from_mlx_log_line("Loading pretrained model", total_steps=100) is None
    nan = progress.from_mlx_log_line("Iter 4: Train loss nan, Learning Rate 1e-05", total_steps=10)
    assert nan["non_finite_loss"] is True and nan["loss"] is None


# ----------------------------------------------------------------- dataset
def test_mlx_dataset_layout_skips_image_rows_and_always_writes_a_validation_file(tmp_path):
    rows = [{"messages": [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]} for i in range(40)]
    rows.append({"messages": [{"role": "user", "content": "see"}, {"role": "assistant", "content": "ok"}], "images": ["a.png"]})
    rows.append({"messages": [{"role": "user", "content": "unanswered"}]})
    counts = mlx_executor.export_mlx_dataset(rows, tmp_path / "data", valid_fraction=0.1)
    assert counts["train"] + counts["valid"] == 40 and counts["valid"] == 4
    train = [json.loads(line) for line in (tmp_path / "data" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(set(item) == {"messages"} for item in train)
    assert not any("see" in json.dumps(item) for item in train)

    tiny = mlx_executor.export_mlx_dataset(rows[:3], tmp_path / "tiny")
    assert tiny["valid"] == 1  # mlx_lm will not train without validation data

    explicit = mlx_executor.export_mlx_dataset(rows[:5], tmp_path / "explicit", valid_rows=rows[5:8])
    assert (explicit["train"], explicit["valid"]) == (5, 3)


# ----------------------------------------------------------------- command
def test_mlx_command_and_config(tmp_path):
    config = mlx_executor.write_lora_config(tmp_path / "lora.yaml", rank=16, alpha=32, dropout=0.05)
    params = json.loads(config.read_text(encoding="utf-8"))["lora_parameters"]
    assert params == {"rank": 16, "scale": 2.0, "dropout": 0.05}

    command = mlx_executor.build_mlx_lora_command(
        python="python3", model="mlx-community/Qwen3.5-4B-bf16", data_dir=tmp_path / "d", adapter_dir=tmp_path / "a",
        config_path=config, iters=200, batch_size=2, learning_rate=2e-4, max_seq_length=1024,
        resume_adapter_file=tmp_path / "a" / "0000100_adapters.safetensors",
    )
    assert command[:5] == ["python3", "-m", "mlx_lm", "lora", "--model"]
    joined = " ".join(command)
    for flag in ("--train", "--iters 200", "--batch-size 2", "--grad-checkpoint", "--mask-prompt",
                 "--resume-adapter-file", "--fine-tune-type lora", "--max-seq-length 1024"):
        assert flag in joined
    assert "--grad-checkpoint" not in mlx_executor.build_mlx_lora_command(
        python="p", model="m", data_dir=tmp_path, adapter_dir=tmp_path, config_path=config, iters=1, grad_checkpoint=False)


def test_running_mlx_training_echoes_lines_and_adds_progress():
    class FakeProcess:
        stdout = iter(["Loading model\n", "Iter 5: Train loss 1.25, Learning Rate 1e-04, It/sec 2.0\n", "done\n"])

        def wait(self):
            return 0

    seen = []
    code = mlx_executor.run_mlx_training(["x"], total_steps=10, emit=seen.append, popen=lambda *a, **k: FakeProcess())
    assert code == 0
    assert seen[0] == "Loading model"
    updates = [progress.parse_progress_line(line) for line in seen if line.startswith(progress.PROGRESS_PREFIX)]
    assert len(updates) == 1 and updates[0]["step"] == 5 and updates[0]["seconds_per_step"] == 0.5


# -------------------------------------------------------------- conversion
def _mlx_adapter(tmp_path, rank=4, in_features=8, out_features=6, prefix=""):
    rng = np.random.default_rng(7)
    tensors = {}
    for layer in (0, 1):
        for leaf in ("self_attn.q_proj", "mlp.down_proj"):
            module = f"{prefix}model.layers.{layer}.{leaf}"
            tensors[f"{module}.lora_a"] = rng.standard_normal((in_features, rank)).astype("float32")
            tensors[f"{module}.lora_b"] = rng.standard_normal((rank, out_features)).astype("float32")
    tensors["unrelated.bias"] = np.zeros(3, dtype="float32")
    adapter = tmp_path / "mlx"
    adapter.mkdir()
    safetensors_numpy.save_file(tensors, str(adapter / mlx_executor.MLX_ADAPTER_FILE))
    return adapter, tensors


def test_conversion_is_an_exact_transpose_with_matching_scale(tmp_path):
    rank, alpha = 4, 8
    adapter, tensors = _mlx_adapter(tmp_path, rank=rank)
    result = mlx_executor.mlx_adapter_to_peft(adapter, tmp_path / "peft", base_model="Qwen/Qwen3.5-4B", rank=rank, alpha=alpha)
    assert result["tensors"] == 8 and result["target_modules"] == ["down_proj", "q_proj"]

    config = json.loads((tmp_path / "peft" / "adapter_config.json").read_text(encoding="utf-8"))
    assert config["r"] == rank and config["lora_alpha"] == alpha and config["base_model_name_or_path"] == "Qwen/Qwen3.5-4B"
    assert config["peft_type"] == "LORA" and config["target_modules"] == ["down_proj", "q_proj"]

    peft = safetensors_numpy.load_file(str(tmp_path / "peft" / "adapter_model.safetensors"))
    module = "model.layers.1.self_attn.q_proj"
    A = peft[f"base_model.model.{module}.lora_A.weight"]
    B = peft[f"base_model.model.{module}.lora_B.weight"]
    a, b = tensors[f"{module}.lora_a"], tensors[f"{module}.lora_b"]
    assert A.shape == (rank, 8) and B.shape == (6, rank)

    # The two formulations must be the same function: scale * (x @ a) @ b  vs  (alpha / r) * B (A x).
    x = np.random.default_rng(1).standard_normal((5, 8)).astype("float32")
    mlx_out = (alpha / rank) * (x @ a) @ b
    peft_out = (alpha / rank) * (x @ A.T @ B.T)
    assert np.allclose(mlx_out, peft_out, atol=1e-5)
    assert not any("unrelated" in key for key in peft)


def test_conversion_strips_the_language_model_prefix_of_vlm_wrappers(tmp_path):
    adapter, _ = _mlx_adapter(tmp_path, prefix="language_model.")
    mlx_executor.mlx_adapter_to_peft(adapter, tmp_path / "peft", base_model="x", rank=4, alpha=8)
    keys = list(safetensors_numpy.load_file(str(tmp_path / "peft" / "adapter_model.safetensors")))
    assert keys and all(key.startswith("base_model.model.model.layers.") for key in keys)


def test_conversion_fails_loudly_on_a_missing_or_empty_adapter(tmp_path):
    with pytest.raises(FileNotFoundError):
        mlx_executor.mlx_adapter_to_peft(tmp_path, tmp_path / "out", base_model="x", rank=4, alpha=8)
    empty = tmp_path / "empty"
    empty.mkdir()
    safetensors_numpy.save_file({"other": np.zeros(2, dtype="float32")}, str(empty / mlx_executor.MLX_ADAPTER_FILE))
    with pytest.raises(ValueError):
        mlx_executor.mlx_adapter_to_peft(empty, tmp_path / "out2", base_model="x", rank=4, alpha=8)


def test_status_reports_without_mlx_installed():
    status = mlx_executor.mlx_status()
    assert set(status) >= {"native_apple_silicon", "installed", "available"}
    assert not (status["available"] and not status["installed"])
    assert math.isfinite(1.0)
