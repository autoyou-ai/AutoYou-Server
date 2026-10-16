"""Source launch staging uses synthetic public inputs and never operator state."""
import json
import sys
from pathlib import Path

import pytest

from scripts import stage_iroh_runtime as stage
from shared.iroh_release import IROH_VERSION, SDK_API, TARGETS, digest, source_fingerprints


@pytest.fixture
def source_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_RELEASE_GENERATION", "iroh")
    monkeypatch.setattr("shared.iroh_binding.target_tag", lambda: "windows-x86_64")
    source = tmp_path / "source"
    workspace = source / "transport-rust"
    workspace.mkdir(parents=True)
    (source / "VERSION").write_text("81.0.2\n")
    (workspace / "Cargo.lock").write_bytes(b"synthetic-lock\r\n")
    (workspace / "Cargo.toml").write_bytes(b"synthetic-source\n")
    sdk = tmp_path / "sdk"
    files = [TARGETS["windows-x86_64"][1], "autoyou_application.pyd", "generated/autoyou_client_bindings.py",
             "generated/AutoYouTransport.swift", "generated/AutoYouTransportFFI.h",
             "generated/AutoYouTransportFFI.modulemap",
             "generated/uniffi/autoyou_client_bindings/autoyou_client_bindings.kt",
             "legal/NOTICE.txt", "legal/sbom.cdx.json", "legal/covered-sources.zip"]
    for name in files:
        path = sdk / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic SDK artifact")
    (sdk / "sdk-manifest.json").write_text(json.dumps(dict(SDK_API,
        target_tag="windows-x86_64", target_triple=TARGETS["windows-x86_64"][0],
        lock_sha256=digest(workspace / "Cargo.lock"), source_fingerprints=source_fingerprints(workspace),
        files={name: digest(sdk / name) for name in files})))
    release = dict(schema_version=1, generation="iroh", version=IROH_VERSION,
        policy=dict(bind_addresses=["127.0.0.1:0"], brokered_relays=True, allow_lan_peers=True,
                    relays=[dict(url="https://relay.example.invalid/")]),
        core=dict(issuer="https://core.example.invalid", public_key="11" * 32))
    config = tmp_path / "release.json"
    config.write_text(json.dumps(release))
    monkeypatch.setenv("AUTOYOU_IROH_SDK", str(sdk))
    monkeypatch.setenv("AUTOYOU_IROH_CONFIG", str(config))
    return source, workspace, sdk, release


def test_source_launch_prepares_once_and_preserves_source_version(source_inputs, monkeypatch):
    source, workspace, sdk, release = source_inputs
    before = {str(p): p.read_bytes() for p in sdk.rglob("*") if p.is_file()}
    assert stage.prepare_source_runtime(source) == release
    staged = {str(p): p.read_bytes() for p in (source / "transport").rglob("*") if p.is_file()}
    monkeypatch.delenv("AUTOYOU_RELEASE_GENERATION")
    monkeypatch.delenv("AUTOYOU_IROH_SDK")
    monkeypatch.delenv("AUTOYOU_IROH_CONFIG")
    (workspace / "Cargo.lock").write_bytes(b"synthetic-lock\n")
    assert stage.prepare_source_runtime(source) == release
    assert staged == {str(p): p.read_bytes() for p in (source / "transport").rglob("*") if p.is_file()}
    assert before == {str(p): p.read_bytes() for p in sdk.rglob("*") if p.is_file()}
    assert (source / "VERSION").read_text() == "81.0.2\n"
    assert not list(source.glob(".iroh-source-*"))
    assert set(p.name for p in source.iterdir()) == {"VERSION", "transport", "transport-rust"}


@pytest.mark.parametrize("input_name", ["AUTOYOU_IROH_SDK", "AUTOYOU_IROH_CONFIG"])
def test_native_source_missing_inputs_fails_before_staging(source_inputs, monkeypatch, input_name):
    source, *_ = source_inputs
    monkeypatch.delenv(input_name)
    with pytest.raises(ValueError, match="requires AUTOYOU_IROH_SDK"):
        stage.prepare_source_runtime(source)
    assert not (source / "transport").exists()


@pytest.mark.parametrize("change", ["source", "lock", "missing_release"])
def test_source_launch_rejects_stale_or_incomplete_staged_sdk(source_inputs, change):
    source, workspace, *_ = source_inputs
    stage.prepare_source_runtime(source)
    if change == "missing_release":
        (source / "transport/release.json").unlink()
    else:
        (workspace / ("Cargo.toml" if change == "source" else "Cargo.lock")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="stale|missing"):
        stage.prepare_source_runtime(source)


def test_legacy_source_needs_no_native_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.delenv("AUTOYOU_RELEASE_GENERATION", raising=False)
    assert stage.prepare_source_runtime(tmp_path) is None
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setenv("AUTOYOU_RELEASE_GENERATION", "unsupported")
    with pytest.raises(ValueError, match="unknown"):
        stage.prepare_source_runtime(tmp_path)


def test_native_source_never_stages_under_live_root(source_inputs):
    with pytest.raises(ValueError, match="isolated"):
        stage.prepare_source_runtime(Path(stage.SERVER))


def test_bootstrap_checks_native_abi_and_passes_version_to_owned_child(source_inputs, monkeypatch):
    from scripts import bootstrap_autoyou as bootstrap
    source, *_ = source_inputs
    monkeypatch.setattr(bootstrap, "REPO_ROOT", source)
    monkeypatch.setattr(bootstrap, "create_venv", lambda *_: Path(sys.executable))
    monkeypatch.setattr(bootstrap, "warn_on_ports", lambda *_: [])
    monkeypatch.setattr(bootstrap, "report_autoyou_config_storage_status", lambda: None)
    monkeypatch.setattr(bootstrap, "playwright_package_installed", lambda *_: False)
    commands, launches = [], []
    monkeypatch.setattr(bootstrap, "run_command", lambda command, **_: commands.append(command))
    monkeypatch.setattr(bootstrap, "launch_server_process", lambda command, **kwargs: launches.append(kwargs) or 0)
    previous_directory = Path.cwd()
    try:
        assert bootstrap.main(["--profile", "base", "--skip-install", "--skip-tunnelmole"]) == 0
    finally:
        import os
        os.chdir(previous_directory)
    assert commands == [[sys.executable, "-c", "from shared.iroh_binding import load_binding; load_binding()"]]
    assert launches[0]["env"]["AUTOYOU_VERSION"] == IROH_VERSION
    assert launches[0]["env"]["AUTOYOU_TEST_ROOT"] == str(source.parent)
