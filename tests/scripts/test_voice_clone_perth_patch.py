# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License. See LICENSE.
"""The voice runtime can keep its watermark without obsolete pkg_resources."""
import base64
import csv
import hashlib
import json

import pytest

from scripts.install_voice_clone_runtime import patch_perth_resources


@pytest.fixture
def perth_wheel(tmp_path):
    package = tmp_path / "perth/perth_net"
    package.mkdir(parents=True)
    module = package / "__init__.py"
    module.write_text('from pkg_resources import resource_filename\n'
                      'PREPACKAGED_MODELS_DIR = resource_filename(__name__, "pretrained")\n\n'
                      'from .perth_net_implicit.perth_watermarker import PerthImplicitWatermarker\n')
    model = package / "pretrained/implicit/model.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"synthetic immutable model fixture")
    info = tmp_path / "resemble_perth-1.0.1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: resemble-perth\nVersion: 1.0.1\n")
    (info / "RECORD").write_text("perth/perth_net/__init__.py,,\nresemble_perth-1.0.1.dist-info/RECORD,,\n")
    return tmp_path, module, model, info


def test_patch_retains_watermarker_and_model_and_updates_wheel_record(perth_wheel):
    root, module, model, info = perth_wheel
    before = model.read_bytes()
    assert patch_perth_resources(root)
    source = module.read_text()
    assert "pkg_resources" not in source
    assert "from .perth_net_implicit.perth_watermarker import PerthImplicitWatermarker" in source
    assert model.read_bytes() == before
    evidence = json.loads((info / "AUTOYOU-PATCHES.json").read_text())
    assert evidence["version"] == "1.0.1"
    assert evidence["after_sha256"] == hashlib.sha256(module.read_bytes()).hexdigest()
    with (info / "RECORD").open(newline="") as handle:
        rows = {row[0]: row[1:] for row in csv.reader(handle)}
    for path in (module, info / "AUTOYOU-PATCHES.json"):
        data = path.read_bytes()
        expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        assert rows[path.relative_to(root).as_posix()] == ["sha256=" + expected, str(len(data))]
    assert not patch_perth_resources(root)
    (info / "AUTOYOU-PATCHES.json").unlink()
    assert patch_perth_resources(root)  # recover an interrupted metadata update


def test_unknown_source_is_not_modified(perth_wheel):
    root, module, _, info = perth_wheel
    module.write_text("# synthetic future upstream source\n")
    before = module.read_bytes(), (info / "RECORD").read_bytes()
    with pytest.raises(RuntimeError, match="differs from the reviewed"):
        patch_perth_resources(root)
    assert (module.read_bytes(), (info / "RECORD").read_bytes()) == before
    assert not (info / "AUTOYOU-PATCHES.json").exists()


def test_patch_does_not_fall_back_to_operator_environment(tmp_path):
    with pytest.raises(RuntimeError, match="Expected resemble-perth"):
        patch_perth_resources(tmp_path)


def test_failed_atomic_replace_preserves_source_and_cleans_temporary_files(perth_wheel, monkeypatch):
    from scripts import install_voice_clone_runtime as installer

    root, module, _, info = perth_wheel
    before = module.read_bytes(), (info / "RECORD").read_bytes()

    def fail_replace(*_args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(installer.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        patch_perth_resources(root)
    assert (module.read_bytes(), (info / "RECORD").read_bytes()) == before
    assert not list(root.rglob(".autoyou-patch-*"))


def test_installer_plans_a_private_perth_copy_when_reusing_torch(tmp_path, monkeypatch):
    from scripts import install_voice_clone_runtime as installer

    monkeypatch.setattr(installer, "_python_version", lambda _python: (3, 12))
    donor = tmp_path / "donor"
    donor_python = installer.venv_python(donor)
    donor_python.parent.mkdir(parents=True)
    donor_python.write_bytes(b"synthetic executable marker")
    monkeypatch.setattr(installer, "_site_packages", lambda _python: donor / "site-packages")
    commands = []
    monkeypatch.setattr(installer, "_run", lambda command, **_kwargs: commands.append(command))
    installer.install(target=tmp_path / "runtime", base_python="synthetic-python", reuse_torch_from=donor, dry_run=True)
    private_copy = next(i for i, command in enumerate(commands) if "resemble-perth==1.0.1" in command)
    assert "--ignore-installed" in commands[private_copy]
    assert "--no-deps" in commands[private_copy]
    assert "--force-reinstall" not in commands[private_copy]
    assert commands[private_copy][0] == str(installer.venv_python(tmp_path / "runtime/.venv"))
    assert next(i for i, command in enumerate(commands) if "-r" in command) < private_copy
    assert private_copy < next(i for i, command in enumerate(commands) if installer.CHATTERBOX_SPEC in command)
