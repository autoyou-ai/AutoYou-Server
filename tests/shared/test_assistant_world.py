import pytest

from shared.assistant_world import validate_world
from shared import assistant_skin, assistant_mode
from pathlib import Path


def test_world_roundtrip_and_appearance_preserve_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant"))
    world = validate_world({"enabled": True, "robot_glow": "#ffaa33", "objects": [{"id": "tree", "shape": "tree", "sound": "bell"}]})
    assert world["robot_glow"] == "#ffaa33"
    assistant_skin.save_active_skin({"id": "autoyou_classic", "world": world})
    assert assistant_skin.apply_preset("neon_cyber")["world"] == world
    assert assistant_skin.reset_active_skin()["world"] == world
    pack = assistant_skin.export_skin_pack()
    assert assistant_skin.import_skin_pack(pack)["world"] == world


def test_failed_scene_write_keeps_previous_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant"))
    old = assistant_skin.save_active_skin({"id": "autoyou_classic", "world": {"name": "Keep me"}})
    def failed_replace(*_args):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(Path, "replace", failed_replace)
    with pytest.raises(RuntimeError):
        assistant_skin.save_active_skin({"world": {"name": "New"}})
    assert assistant_skin.get_active_skin() == old


def test_assistant_world_tools_report_only_saved_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant"))
    from autoyou_agents.assistant_agent.agent import assistant_get_world, assistant_update_world
    result = assistant_update_world('{"enabled":true,"objects":[{"shape":"cloud"}]}')
    assert result["success"] and assistant_get_world() == result["world"]
    assert not assistant_update_world('{"objects":[{"shape":"script"}]}')["success"]
    assert assistant_get_world() == result["world"]


@pytest.mark.parametrize("world", [
    [], {"objects": [{}] * 97}, {"objects": [{"id": "same"}, {"id": "same"}]},
    {"objects": [{"shape": "script"}]}, {"objects": [{"path": '<image href="https://example.invalid"/>'}]},
    {"objects": [{"x": float("nan")}]}, {"objects": [{"x": 101}]}, {"enabled": "false"},
    {"version": 99}, {"version": True}, {"robot_shell": "url(external)"}, {"objects": [{"x": 10 ** 400}]}, {"objects": [{"path": "0" * 4097}]},
])
def test_invalid_world_is_rejected(world):
    with pytest.raises(ValueError):
        validate_world(world)


def test_wake_import_validates_before_writing_and_keeps_disabled_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOYOU_ASSISTANT_MODE_DIR", str(tmp_path / "assistant"))
    assistant_mode.save_settings({"enabled": False})
    model = {"format": "autoyou-wake-word", "version": 1, "phrase": "Nova", "base_threshold": .16,
             "templates": [{"mfcc": [[.1] * 12, [.2] * 12]}]}
    imported = assistant_mode.import_model(model)
    assert assistant_mode.load_settings()["enabled"] is False
    assert assistant_mode.load_model().to_dict() == imported
    for invalid in ({**model, "base_threshold": float("nan")}, {**model, "sensitivity": 10 ** 400},
                    {**model, "templates": [{"mfcc": [[True] * 12]}]}, {**model, "templates": [{"mfcc": [[0] * 11]}]}, {**model, "templates": []}):
        with pytest.raises(ValueError):
            assistant_mode.import_model(invalid)
        assert assistant_mode.load_model().to_dict() == imported
