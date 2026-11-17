# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License. See LICENSE.
import json
import os
from pathlib import Path

import pytest

from shared.accelerate_checkpoint_guard import validated_checkpoint


def _index(folder, shards):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "model.safetensors.index.json"
    path.write_text(json.dumps({"weight_map": shards}), encoding="utf-8")
    return path


def _symlink(link, target):
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation requires platform privileges")


@pytest.mark.parametrize("name", ["../outside.safetensors", "/outside.safetensors", "C:/outside.bin",
                                  "C:outside.bin", "//server/share/model.bin", "..\\outside.bin",
                                  "weights.bin:stream", "", None, 12, "bad\0name"])
def test_rejects_untrusted_shard_names_before_loader(tmp_path, name):
    index = _index(tmp_path / "model", {"weight": name})
    with pytest.raises(ValueError):
        with validated_checkpoint(index):
            pytest.fail("Malformed shard reached the loader")


def test_valid_index_is_snapshotted_and_cleaned_after_load_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path / "test-state"))
    model = tmp_path / "model"
    index = _index(model, {"weight": "nested/weights.safetensors"})
    shard = model / "nested/weights.safetensors"
    shard.parent.mkdir()
    shard.write_bytes(b"synthetic weights")
    with pytest.raises(RuntimeError, match="synthetic load failure"):
        with validated_checkpoint(index) as snapshot:
            snapshot = Path(snapshot)
            assert snapshot.is_relative_to(tmp_path / "test-state")
            index.write_text('{"weight_map":{"weight":"../outside"}}')
            assert json.loads(snapshot.read_text())["weight_map"] == {"weight": str(shard)}
            raise RuntimeError("synthetic load failure")
    assert not snapshot.exists()
    assert not list((tmp_path / "test-state").iterdir())


def test_rejects_oversized_index_before_json_parse(tmp_path, monkeypatch):
    import shared.accelerate_checkpoint_guard as guard

    index = _index(tmp_path / "model", {"weight": "weights.safetensors"})
    monkeypatch.setattr(guard, "MAX_INDEX_BYTES", 4)
    with pytest.raises(ValueError, match="no larger"):
        with validated_checkpoint(index):
            pytest.fail("Oversized index accepted")


def test_training_installs_guard_before_loading_model_or_data(monkeypatch, capsys):
    import argparse
    import shared.accelerate_checkpoint_guard as guard
    from autoyou_agents.fine_tuning_agent.training_runner import train_adapter

    def unavailable():
        raise RuntimeError("synthetic unsupported checkpoint API")

    monkeypatch.setattr(guard, "install_checkpoint_guard", unavailable)
    assert train_adapter(argparse.Namespace()) == 2
    assert "synthetic unsupported checkpoint API" in capsys.readouterr().out


def test_rejects_external_symlink_and_directory_shards(tmp_path):
    model = tmp_path / "model"
    index = _index(model, {"weight": "weights.safetensors"})
    outside = tmp_path / "private.bin"
    outside.write_bytes(b"synthetic outside file")
    shard = model / "weights.safetensors"
    _symlink(shard, outside)
    with pytest.raises(ValueError, match="regular files inside"):
        with validated_checkpoint(index):
            pytest.fail("External symlink accepted")
    shard.unlink()
    shard.mkdir()
    with pytest.raises(ValueError, match="regular files"):
        with validated_checkpoint(index):
            pytest.fail("Directory shard accepted")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipe")
def test_rejects_fifo_shard_and_index_without_waiting(tmp_path):
    index = _index(tmp_path / "model", {"weight": "fifo.bin"})
    os.mkfifo(index.parent / "fifo.bin")
    with pytest.raises(ValueError, match="regular files"):
        with validated_checkpoint(index):
            pytest.fail("FIFO accepted")
    index.unlink()
    os.mkfifo(index)
    with pytest.raises(ValueError, match="regular files"):
        with validated_checkpoint(index):
            pytest.fail("FIFO index accepted")


@pytest.mark.parametrize("subfolder", ["", "transformer"])
def test_huggingface_cache_links_preserve_safetensors_names(tmp_path, subfolder):
    repo = tmp_path / "models--synthetic--fixture"
    snapshot = repo / "snapshots" / ("a" * 40) / subfolder
    snapshot.mkdir(parents=True)
    blobs = repo / "blobs"
    blobs.mkdir()
    data = blobs / ("b" * 64)
    data.write_bytes(b"synthetic weights")
    shard = snapshot / "weights.safetensors"
    _symlink(shard, Path(os.path.relpath(data, shard.parent)))
    index_blob = blobs / ("c" * 40)
    index_blob.write_text(json.dumps({"weight_map": {"weight": shard.name}}))
    index = snapshot / "model.safetensors.index.json"
    _symlink(index, Path(os.path.relpath(index_blob, index.parent)))
    with validated_checkpoint(snapshot) as checked:
        assert json.loads(Path(checked).read_text())["weight_map"] == {"weight": str(shard)}
    unsharded = snapshot / "model.safetensors"
    _symlink(unsharded, Path(os.path.relpath(data, unsharded.parent)))
    with validated_checkpoint(snapshot) as checked:
        assert checked == str(unsharded)


def test_actual_accelerate_loaders_reject_traversal_and_load_synthetic_weights(tmp_path, monkeypatch):
    accelerate = pytest.importorskip("accelerate")
    torch = pytest.importorskip("torch")
    from safetensors.torch import save_file
    from shared.accelerate_checkpoint_guard import install_checkpoint_guard

    # Restore the real modules' aliases after this test; never replace an
    # installed dependency with a sys.modules stub.
    import accelerate.big_modeling as big_modeling
    import accelerate.utils as utils
    import accelerate.utils.bnb as bnb
    import accelerate.utils.modeling as modeling
    for module in (accelerate, big_modeling, utils, modeling, bnb):
        if hasattr(module, "load_checkpoint_in_model"):
            monkeypatch.setattr(module, "load_checkpoint_in_model", module.load_checkpoint_in_model)
    install_checkpoint_guard()
    guarded = modeling.load_checkpoint_in_model
    install_checkpoint_guard()
    assert modeling.load_checkpoint_in_model is guarded
    index = _index(tmp_path / "model", {"weight": "weights.safetensors"})
    save_file({"weight": torch.full((2, 2), 3.0)}, str(index.parent / "weights.safetensors"), metadata={"format": "pt"})
    for loader in (accelerate.load_checkpoint_in_model, modeling.load_checkpoint_in_model,
                   utils.load_checkpoint_in_model, big_modeling.load_checkpoint_in_model, bnb.load_checkpoint_in_model,
                   accelerate.load_checkpoint_and_dispatch):
        model = torch.nn.Linear(2, 2, bias=False)
        loader(model, checkpoint=str(index), device_map={"": "cpu"})
        assert torch.equal(model.weight, torch.full((2, 2), 3.0))
        index.write_text(json.dumps({"weight_map": {"weight": "../outside.safetensors"}}))
        with pytest.raises(ValueError, match="escapes"):
            loader(model, checkpoint=str(index), device_map={"": "cpu"})
        _index(index.parent, {"weight": "weights.safetensors"})
