# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Bootstrap sets up the Ollama it starts, and installs voice when it can.

Review 2026-11-05: bootstrap started a bare ``ollama serve`` with its output
thrown away, never mentioned a missing default model, and the default
launchers left voice out. Nothing here starts Ollama or pip.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import os

import pytest

import scripts.bootstrap_autoyou as bootstrap


def test_optional_components_are_added_unless_excluded_or_already_there():
    assert bootstrap.resolve_optional_components(["base", "internet"], ["voice"], []) == ["voice"]
    assert bootstrap.resolve_optional_components(["base"], ["voice"], ["voice"]) == []
    assert bootstrap.resolve_optional_components(["base", "voice"], ["voice"], []) == []
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.resolve_optional_components(["base"], ["synthetic-component"], [])


def test_an_optional_component_that_fails_is_skipped_and_a_required_one_still_stops(monkeypatch):
    monkeypatch.setattr(bootstrap, "build_locked_constraints", lambda torch_pins=None: None)
    installed = []

    def install(venv_python, component, constraints, component_env, before_component, upgrade):
        if component == "voice":
            raise bootstrap.BootstrapError("synthetic PortAudio build failure")
        installed.append(component)

    monkeypatch.setattr(bootstrap, "_install_component_requirements", install)

    skipped = bootstrap.install_requirements(
        bootstrap.Path("synthetic-python"), ["base", "voice", "internet"], optional=["voice"]
    )

    assert skipped == ["voice"] and installed == ["base", "internet"]
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.install_requirements(bootstrap.Path("synthetic-python"), ["base", "voice"])


@pytest.fixture
def ollama_env(monkeypatch):
    # Record the prior state so whatever bootstrap sets is undone after the test.
    for key in bootstrap.OLLAMA_SERVE_DEFAULTS:
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)
    return monkeypatch


def test_the_ollama_bootstrap_starts_gets_an_8_bit_cache_and_a_log(ollama_env, tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_KV_CACHE_TYPE", "f16")  # the user's choice is kept
    monkeypatch.setattr(bootstrap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap, "resolve_executable", lambda name: "synthetic-ollama")
    answers = iter([False, True])
    monkeypatch.setattr(bootstrap, "ollama_is_reachable", lambda: next(answers))
    monkeypatch.setattr(bootstrap.time, "sleep", lambda seconds: None)
    checked = []
    monkeypatch.setattr(bootstrap, "ensure_default_ollama_model", lambda ollama, pull: checked.append((ollama, pull)))
    launched = {}

    def fake_popen(command, **kwargs):
        launched.update(command=command, **kwargs)

    monkeypatch.setattr(bootstrap.subprocess, "Popen", fake_popen)

    bootstrap.start_ollama_if_possible(pull_default_model=True)

    assert launched["command"] == ["synthetic-ollama", "serve"]
    assert launched["env"]["OLLAMA_FLASH_ATTENTION"] == "1"
    assert launched["env"]["OLLAMA_KV_CACHE_TYPE"] == "f16"
    # The server sees the same settings, so it sizes the context for them.
    assert os.environ["OLLAMA_FLASH_ATTENTION"] == "1"
    assert launched["stdout"].name == str(tmp_path / "logs" / "ollama.log")
    assert checked == [("synthetic-ollama", True)]


def test_a_missing_default_model_is_named_with_the_command_to_get_it(monkeypatch, capsys):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(bootstrap, "ollama_installed_models", lambda: ["synthetic-other:3b"])
    ran = []
    monkeypatch.setattr(bootstrap, "run_command", lambda command, **kwargs: ran.append(command))

    bootstrap.ensure_default_ollama_model("synthetic-ollama", pull=False)
    assert "ollama pull ministral-3:8b" in capsys.readouterr().out
    assert ran == []

    bootstrap.ensure_default_ollama_model("synthetic-ollama", pull=True)
    assert ran == [["synthetic-ollama", "pull", "ministral-3:8b"]]

    monkeypatch.setattr(bootstrap, "ollama_installed_models", lambda: ["ministral-3:8b"])
    bootstrap.ensure_default_ollama_model("synthetic-ollama", pull=True)
    assert len(ran) == 1


def test_both_launchers_install_voice_when_they_can():
    for name in ("run_autoyou.bat", "run_autoyou.sh"):
        text = (bootstrap.REPO_ROOT / name).read_text(encoding="utf-8")
        assert "--with-optional voice" in text
        assert "--without voice" in text
