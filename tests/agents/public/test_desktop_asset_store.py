# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-desktop-assets-20260929

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from autoyou_agents.shared_tools import desktop_app_control
from autoyou_agents.shared_tools.desktop_app_manifest import load_desktop_app_manifest
from autoyou_agents.shared_tools.desktop_app_control import select_desktop_asset_pack
from autoyou_agents.shared_tools.desktop_asset_store import (
    get_desktop_asset_preferences,
    get_desktop_asset_agent_catalog,
    get_user_desktop_agents_root,
    import_user_desktop_asset_bundle,
    load_user_desktop_asset_packs,
    render_desktop_asset_setup_prompt,
    save_desktop_asset_preferences,
)

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-desktop-assets-20260929"

AGENT_NAME = "codex_desktop_agent"
APP_ID = "codex_desktop"


@pytest.fixture
def user_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(root))
    monkeypatch.delenv("AUTOYOU_RUNTIME_ROOT", raising=False)
    return root / "AutoYou"


def _sprite_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), color=(25, 60, 100)).save(output, format="PNG")
    return output.getvalue()


def _bundle_bytes(
    *,
    agent_name: str = AGENT_NAME,
    image_path: str = "sprites/composer.png",
    include_image: bool = True,
    pack_id: str = "codex-windows-1.0-dark-150",
) -> bytes:
    manifest = {
        "schema_version": 2,
        "agent_name": agent_name,
        "app_id": APP_ID,
        "asset_packs": [
            {
                "asset_pack_id": pack_id,
                "platform": "windows",
                "app_version": "1.0.0",
                "theme": "dark",
                "display_scale": 1.5,
                "coordinate_space": "window",
                "targets": [
                    {
                        "target_id": "composer_box",
                        "click_point": [0.5, 0.8],
                        "normalized_box": [0.2, 0.7, 0.8, 0.9],
                        "expected_image_path": image_path,
                    }
                ],
            }
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        if include_image:
            archive.writestr(image_path, _sprite_bytes())
    return output.getvalue()


def test_imported_sprites_and_preferences_are_scoped_to_test_runtime(user_data_root: Path) -> None:
    result = import_user_desktop_asset_bundle(AGENT_NAME, _bundle_bytes(), expected_app_id=APP_ID)

    assert result["installed_pack_ids"] == ["codex-windows-1.0-dark-150"]
    assert result["storage"] == "private per-user application data"
    assets_root = get_user_desktop_agents_root() / AGENT_NAME / "desktop_assets"
    assert assets_root.is_relative_to(user_data_root)

    packs = load_user_desktop_asset_packs(AGENT_NAME)
    assert len(packs) == 1
    assert packs[0]["_user_local_pack"] is True
    target = packs[0]["targets"][0]
    assert target["_desktop_assets_root"] == str(assets_root)
    image_path = assets_root / target["expected_image_path"]
    assert image_path.is_file()
    with Image.open(image_path) as image:
        assert image.format == "PNG"
        assert image.size == (12, 8)

    saved = save_desktop_asset_preferences(
        AGENT_NAME,
        {"theme": "custom:studio dark", "display_scale": 1.5},
    )
    assert saved == {"theme": "custom:studio dark", "display_scale": 1.5}
    assert get_desktop_asset_preferences(AGENT_NAME) == saved


def test_user_local_pack_merges_with_template_and_selects_by_version_theme_and_scale(
    user_data_root: Path,
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "agents" / AGENT_NAME
    assets_dir = agent_dir / "desktop_assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "manifest.template.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "agent_name": AGENT_NAME,
                "app_id": APP_ID,
                "asset_packs": [],
            }
        ),
        encoding="utf-8",
    )
    (assets_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "agent_name": AGENT_NAME,
                "app_id": APP_ID,
                "title": "legacy checkout data",
                "asset_packs": [
                    {
                        "asset_pack_id": "legacy-checkout-pack",
                        "platform": "windows",
                        "targets": [{"target_id": "send_button", "click_point": [0.5, 0.5]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    import_user_desktop_asset_bundle(AGENT_NAME, _bundle_bytes(), expected_app_id=APP_ID)

    manifest = load_desktop_app_manifest(agent_dir)
    assert manifest is not None
    assert len(manifest["asset_packs"]) == 1
    assert manifest["title"] == AGENT_NAME
    assert manifest["asset_packs"][0]["_user_local_pack"] is True
    selected = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        app_version="1.0.0",
        theme="dark",
        display_scale=1.5,
    )
    assert selected is not None
    assert selected["asset_pack_id"] == "codex-windows-1.0-dark-150"

    target = selected["targets"][0]
    resolved_sprite = desktop_app_control._target_image_path(manifest, target)
    assert resolved_sprite is not None
    assert resolved_sprite.is_file()
    assert resolved_sprite.is_relative_to(get_user_desktop_agents_root())

    assert select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        app_version="2.0.0",
        theme="dark",
        display_scale=1.5,
    ) is None
    assert select_desktop_asset_pack(
        manifest,
        platform_tag="macos",
        architecture="arm64",
        app_version="1.0.0",
        theme="dark",
        display_scale=1.5,
    ) is None
    assert desktop_app_control._get_artifacts_root().is_relative_to(user_data_root)


def test_exact_app_version_pack_beats_a_broader_compatible_range(user_data_root: Path) -> None:
    manifest = {
        "agent_name": AGENT_NAME,
        "asset_packs": [
            {
                "asset_pack_id": "range-1-to-3",
                "platform": "windows",
                "app_version_min": "1.0.0",
                "app_version_max": "3.0.0",
                "theme": "dark",
                "display_scale": 1.5,
                "targets": [{"target_id": "composer_box"}],
            },
            {
                "asset_pack_id": "exact-2",
                "platform": "windows",
                "app_version": "2.0.0",
                "theme": "dark",
                "display_scale": 1.5,
                "targets": [{"target_id": "composer_box"}],
            },
        ],
    }

    selected = select_desktop_asset_pack(
        manifest,
        platform_tag="windows",
        architecture="x86_64",
        app_version="2.0.0",
        theme="dark",
        display_scale=1.5,
    )

    assert selected is not None
    assert selected["asset_pack_id"] == "exact-2"


@pytest.mark.parametrize(
    ("image_path", "include_image", "message"),
    [
        ("../outside.png", True, "Bundle paths must stay inside"),
        ("sprites/missing.png", False, "references a missing sprite"),
    ],
)
def test_import_rejects_traversal_and_missing_images(
    user_data_root: Path,
    image_path: str,
    include_image: bool,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        import_user_desktop_asset_bundle(
            AGENT_NAME,
            _bundle_bytes(image_path=image_path, include_image=include_image),
            expected_app_id=APP_ID,
        )


def test_import_rejects_wrong_app_and_duplicate_pack_ids(user_data_root: Path) -> None:
    with pytest.raises(ValueError, match="app_id does not match"):
        import_user_desktop_asset_bundle(
            AGENT_NAME,
            _bundle_bytes(),
            expected_app_id="different_app",
        )

    import_user_desktop_asset_bundle(AGENT_NAME, _bundle_bytes(), expected_app_id=APP_ID)
    with pytest.raises(ValueError, match="already installed"):
        import_user_desktop_asset_bundle(AGENT_NAME, _bundle_bytes(), expected_app_id=APP_ID)


def test_preferences_reject_out_of_range_scale(user_data_root: Path) -> None:
    with pytest.raises(ValueError, match="Display scale"):
        save_desktop_asset_preferences(AGENT_NAME, {"theme": "dark", "display_scale": 7})

    assert get_desktop_asset_preferences(AGENT_NAME) == {"theme": "auto", "display_scale": "auto"}


def test_setup_prompt_is_self_contained_and_uses_selected_compatibility_values(user_data_root: Path) -> None:
    server_anchor = Path(__file__).resolve().parents[3] / "server.py"
    catalog = get_desktop_asset_agent_catalog(server_anchor)
    assert {item["agent_name"] for item in catalog["agents"]} == {
        "claude_desktop_agent",
        "codex_desktop_agent",
    }
    assert catalog["storage"] == "private per-user application data"

    prompt = render_desktop_asset_setup_prompt(
        AGENT_NAME,
        server_anchor,
        platform="windows",
        app_version="26.803.41515",
        theme="custom:studio dark",
        display_scale="1.5",
    )

    assert "{{" not in prompt
    assert "26.803.41515" in prompt
    assert "custom:studio dark" in prompt
    assert '"schema_version": 2' in prompt
    assert '"$schema": "https://json-schema.org/draft/2020-12/schema"' in prompt

    repo_root = Path(__file__).resolve().parents[3]
    schema = json.loads((repo_root / "autoyou_agents/shared_tools/desktop_asset_schema.json").read_text(encoding="utf-8"))
    template = json.loads((repo_root / "autoyou_agents/codex_desktop_agent/desktop_assets/manifest.template.json").read_text(encoding="utf-8"))
    assert set(template).issubset(schema["properties"])
