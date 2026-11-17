# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License. See LICENSE.
"""Constrain Accelerate checkpoint indexes in AutoYou's training subprocess.

Upstream 1.15.0 still trusts shard filenames (GHSA-4j2p-28q2-5m79).
Install before importing training libraries. This is an input boundary, not
a sandbox for Python model code or a concurrently malicious local account.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from functools import wraps
from pathlib import Path, PureWindowsPath

MAX_INDEX_BYTES = 32 * 1024 * 1024


def _regular_file(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=True)
    contained = resolved.is_relative_to(root)
    if not contained:
        # Hugging Face snapshots use symlinks into this repository's blobs.
        # Preserve that layout, without permitting arbitrary external links.
        for ancestor in (root, *root.parents):
            if ancestor.parent.name != "snapshots" or not re.fullmatch(r"[0-9a-f]{40,64}", ancestor.name):
                continue
            blobs = ancestor.parent.parent / "blobs"
            contained = (blobs.resolve() == blobs and resolved.parent == blobs
                         and re.fullmatch(r"[0-9a-f]{40,64}", resolved.name) is not None)
            break
    if not contained or not stat.S_ISREG(resolved.stat().st_mode):
        raise ValueError("Checkpoint files must be regular files inside the model directory or its HF blobs")
    # Accelerate selects safetensors by filename suffix. Keep the snapshot
    # name, not the extensionless blob name, after validating its destination.
    return path


def _shard_path(name: str, root: Path) -> Path:
    # Check both syntaxes on every OS, including Windows drive-relative paths
    # and alternate data streams. HF indexes use portable forward slashes.
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\0" in name:
        raise ValueError("Invalid checkpoint shard path")
    relative = Path(name)
    if relative.is_absolute() or PureWindowsPath(name).anchor or ".." in relative.parts:
        raise ValueError("Checkpoint shard path escapes the model directory")
    return _regular_file(root / relative, root)


def _read_index(path: Path) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INDEX_BYTES:
            raise ValueError("Checkpoint index must be a regular file no larger than 32 MiB")
        content = handle.read(MAX_INDEX_BYTES + 1)
    if len(content) > MAX_INDEX_BYTES:
        raise ValueError("Checkpoint index exceeds 32 MiB")
    index = json.loads(content)
    if not isinstance(index, dict):
        raise ValueError("Checkpoint index must be an object")
    weights = index.get("weight_map", index)
    if not isinstance(weights, dict) or not weights:
        raise ValueError("Checkpoint index needs a nonempty weight map")
    return weights


@contextmanager
def validated_checkpoint(checkpoint, *, weights_name="pytorch_model.bin", safe_weights_name="model.safetensors"):
    """Yield a validated file or a private index containing validated paths."""
    supplied = Path(os.path.abspath(os.fspath(checkpoint)))
    if supplied.is_dir():
        root = supplied.resolve(strict=True)
        for filename in (weights_name, safe_weights_name):
            candidate = root / filename
            # Upstream gives an unsharded checkpoint precedence over indexes.
            if candidate.exists() or candidate.is_symlink():
                yield str(_regular_file(candidate, root))
                return
        indexes = sorted(root.glob("*.index.json"))
        if len(indexes) != 1:
            raise ValueError("Checkpoint directory must contain exactly one shard index")
        supplied = indexes[0]
    else:
        root = supplied.parent.resolve(strict=True)
    source = _regular_file(supplied, root)
    if not supplied.name.endswith(".json"):
        yield str(source)
        return
    weights = _read_index(source.resolve(strict=True))
    validated, paths = {}, {}
    for key, name in weights.items():
        if not isinstance(name, str):
            raise ValueError("Invalid checkpoint shard path")
        if name not in paths:
            paths[name] = str(_shard_path(name, root))
        validated[key] = paths[name]
    test_root = os.environ.get("AUTOYOU_TEST_ROOT")
    if test_root:
        Path(test_root).mkdir(parents=True, exist_ok=True)
    # Do not let upstream reopen the original index after validation. It may
    # be replaced by a downloader while a model load is in progress.
    with tempfile.TemporaryDirectory(prefix="autoyou-checkpoint-", dir=test_root) as scratch:
        snapshot = Path(scratch) / "model.index.json"
        snapshot.write_text(json.dumps({"weight_map": validated}), encoding="utf-8")
        yield str(snapshot)


def install_checkpoint_guard() -> None:
    """Guard the public aliases and the dispatch loader in this process only."""
    modules = [importlib.import_module(name) for name in
               ("accelerate.utils.modeling", "accelerate.utils", "accelerate.big_modeling",
                "accelerate.utils.bnb", "accelerate")]
    original = modules[0].load_checkpoint_in_model
    if getattr(original, "_autoyou_checkpoint_guard", False):
        return
    signature = inspect.signature(original)
    if "checkpoint" not in signature.parameters:
        raise RuntimeError("Unsupported Accelerate checkpoint API")
    aliases = [module for module in modules if hasattr(module, "load_checkpoint_in_model")]
    if any(module.load_checkpoint_in_model is not original for module in aliases):
        raise RuntimeError("Accelerate checkpoint aliases differ; refusing an incomplete guard")

    @wraps(original)
    def guarded(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        with validated_checkpoint(
            bound.arguments["checkpoint"], weights_name=modules[0].WEIGHTS_NAME,
            safe_weights_name=modules[0].SAFE_WEIGHTS_NAME,
        ) as checkpoint:
            bound.arguments["checkpoint"] = checkpoint
            return original(*bound.args, **bound.kwargs)

    guarded._autoyou_checkpoint_guard = True
    for module in aliases:
        module.load_checkpoint_in_model = guarded
