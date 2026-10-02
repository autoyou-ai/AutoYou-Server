# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-e4cce6b4f14e2a8b1779117b

"""Shared manifest helpers for desktop-app bridge agents.

Each bridge agent owns a ``desktop_assets/manifest.json`` file that describes:
- the target application
- supported OS/platform variants
- launcher/process/window hints
- crowd-sourceable screenshot/coordinate packs

The helpers in this module mirror the existing frontend manifest workflow so
future agents can reuse the same disk-backed discovery pattern.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import importlib.resources as importlib_resources
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-e4cce6b4f14e2a8b1779117b"


LOGGER = logging.getLogger(__name__)
DESKTOP_MANIFEST_RELATIVE_PATH = Path("desktop_assets") / "manifest.json"
DESKTOP_MANIFEST_TEMPLATE_RELATIVE_PATH = Path("desktop_assets") / "manifest.template.json"
DESKTOP_LLM_RELATIVE_PATH = Path("desktop_assets") / "llm.txt"
SUPPORTED_PLATFORM_TAGS = {"windows", "macos", "linux", "any"}


def normalize_platform_tag(value: Optional[str]) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"", "default", "auto", "current"}:
        return "any"
    if raw in {"mac", "macos", "darwin", "osx"}:
        return "macos"
    if raw in {"win", "windows"}:
        return "windows"
    if raw in {"linux", "ubuntu", "raspbian", "pi", "wsl", "wsl2"}:
        return "linux"
    return raw if raw in SUPPORTED_PLATFORM_TAGS else "any"


def build_desktop_asset_pack(
    *,
    asset_pack_id: str,
    platform: str,
    valid_until: Optional[str],
    description: str,
    targets: Iterable[Dict[str, Any]],
    architectures: Optional[Iterable[str]] = None,
    os_versions: Optional[Iterable[str]] = None,
    coordinate_space: str = "window",
    window_title_hints: Optional[Iterable[str]] = None,
    process_names: Optional[Iterable[str]] = None,
    notes: Optional[str] = None,
    bootstrap_only: bool = False,
    submit_actions: Optional[Iterable[Dict[str, Any]]] = None,
    selection_controls: Optional[Dict[str, Any]] = None,
    app_version: Optional[str] = None,
    app_version_min: Optional[str] = None,
    app_version_max: Optional[str] = None,
    theme: str = "any",
    display_scale: Any = "any",
) -> Dict[str, Any]:
    return {
        "asset_pack_id": str(asset_pack_id).strip(),
        "platform": normalize_platform_tag(platform),
        "valid_until": str(valid_until).strip() if valid_until else None,
        "app_version": str(app_version).strip() if app_version else None,
        "app_version_min": str(app_version_min).strip() if app_version_min else None,
        "app_version_max": str(app_version_max).strip() if app_version_max else None,
        "theme": str(theme or "any").strip().lower() or "any",
        "display_scale": display_scale if display_scale is not None else "any",
        "description": str(description).strip(),
        "architectures": [str(item).strip() for item in (architectures or []) if str(item).strip()],
        "os_versions": [str(item).strip() for item in (os_versions or []) if str(item).strip()],
        "coordinate_space": str(coordinate_space or "window").strip() or "window",
        "window_title_hints": [str(item).strip() for item in (window_title_hints or []) if str(item).strip()],
        "process_names": [str(item).strip() for item in (process_names or []) if str(item).strip()],
        "notes": str(notes).strip() if notes else None,
        "bootstrap_only": bool(bootstrap_only),
        "selection_controls": dict(selection_controls or {}),
        "submit_actions": list(submit_actions or []),
        "targets": list(targets or []),
    }


def build_desktop_app_manifest(
    *,
    agent_name: str,
    title: str,
    description: str,
    app_id: str,
    transport: str = "desktop_gui",
    process_names: Optional[Dict[str, Iterable[str]]] = None,
    window_title_hints: Optional[Dict[str, Iterable[str]]] = None,
    launch_commands: Optional[Dict[str, Iterable[Dict[str, Any]]]] = None,
    working_directory_strategy: Optional[Dict[str, Any]] = None,
    preferred_prompt_target_id: str = "composer_box",
    preferred_submit_target_id: str = "send_button",
    preferred_copy_response_target_id: str = "copy_response_button",
    asset_packs: Optional[Iterable[Dict[str, Any]]] = None,
    tags: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "agent_name": str(agent_name).strip(),
        "title": str(title).strip() or str(agent_name).strip(),
        "description": str(description).strip(),
        "app_id": str(app_id).strip() or str(agent_name).strip(),
        "transport": str(transport or "desktop_gui").strip() or "desktop_gui",
        "process_names": {
            normalize_platform_tag(platform): [str(item).strip() for item in values if str(item).strip()]
            for platform, values in (process_names or {}).items()
        },
        "window_title_hints": {
            normalize_platform_tag(platform): [str(item).strip() for item in values if str(item).strip()]
            for platform, values in (window_title_hints or {}).items()
        },
        "launch_commands": {
            normalize_platform_tag(platform): list(values or [])
            for platform, values in (launch_commands or {}).items()
        },
        "working_directory_strategy": dict(working_directory_strategy or {}),
        "preferred_prompt_target_id": str(preferred_prompt_target_id or "composer_box").strip() or "composer_box",
        "preferred_submit_target_id": str(preferred_submit_target_id or "send_button").strip() or "send_button",
        "preferred_copy_response_target_id": str(preferred_copy_response_target_id or "").strip() or None,
        "asset_packs": list(asset_packs or []),
        "tags": [str(item).strip() for item in (tags or []) if str(item).strip()],
    }


def write_desktop_app_manifest(agent_dir: Path, manifest: Dict[str, Any]) -> Path:
    manifest_path = Path(agent_dir) / DESKTOP_MANIFEST_RELATIVE_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def _merge_pack_targets(
    base_targets: Optional[List[Dict[str, Any]]],
    derived_targets: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Merge target lists by ``target_id`` (derived overrides base, preserving order).

    The merge is per FIELD, not per target. A derived pack usually restates only what changed
    in the new app build - most often just a fresh sprite - and replacing the whole target dict
    would silently drop the inherited ``click_point``/``normalized_box``. A target with no
    resolvable coordinate makes ``_compute_click_point`` return None, so every action on it
    fails with "Could not compute or locate target", which reads like a broken app rather than
    a broken manifest. Inherit what the derived pack does not restate.

    A derived field that is explicitly null removes the inherited one, so a pack can still
    retire a coordinate that no longer exists in the new build.
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for target in list(base_targets or []) + list(derived_targets or []):
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("target_id") or "").strip()
        if not target_id:
            continue
        if target_id not in by_id:
            order.append(target_id)
            by_id[target_id] = dict(target)
            continue
        merged = dict(by_id[target_id])
        for key, value in target.items():  # later (derived) wins per field
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        by_id[target_id] = merged
    return [by_id[target_id] for target_id in order]


def _merge_selection_controls(
    base: Optional[Dict[str, Any]],
    derived: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Deep-merge selection controls: per-control keys merge, and per-option dicts merge."""
    merged: Dict[str, Any] = {key: dict(value) if isinstance(value, dict) else value for key, value in (base or {}).items()}
    for control_name, derived_control in (derived or {}).items():
        if isinstance(derived_control, dict) and isinstance(merged.get(control_name), dict):
            base_control = dict(merged[control_name])
            for key, derived_value in derived_control.items():
                if key in {"options", "effort_options"} and isinstance(derived_value, dict) and isinstance(base_control.get(key), dict):
                    options = dict(base_control[key])
                    for option_name, option_value in derived_value.items():
                        if isinstance(option_value, dict) and isinstance(options.get(option_name), dict):
                            merged_option = dict(options[option_name])
                            merged_option.update(option_value)
                            options[option_name] = merged_option
                        else:
                            options[option_name] = option_value
                    base_control[key] = options
                else:
                    base_control[key] = derived_value
            merged[control_name] = base_control
        else:
            merged[control_name] = derived_control
    return merged


_PACK_DEEP_MERGE_KEYS = {"targets", "selection_controls"}


def _merge_raw_pack(base_raw: Dict[str, Any], derived_raw: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a derived (``extends``) pack over its base. Operates on RAW packs so that
    fields the derived pack does not declare are inherited rather than overwritten by
    normalization defaults."""
    merged = dict(base_raw)
    merged.update({key: value for key, value in derived_raw.items() if key not in _PACK_DEEP_MERGE_KEYS})
    merged["targets"] = _merge_pack_targets(base_raw.get("targets"), derived_raw.get("targets"))
    if base_raw.get("selection_controls") is not None or derived_raw.get("selection_controls") is not None:
        merged["selection_controls"] = _merge_selection_controls(
            base_raw.get("selection_controls") or {}, derived_raw.get("selection_controls") or {}
        )
    return merged


def _resolve_pack_inheritance(raw_packs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve ``extends`` references between raw asset packs (supports chains, cycle-safe)."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for pack in raw_packs:
        if isinstance(pack, dict):
            pack_id = str(pack.get("asset_pack_id") or "").strip()
            if pack_id:
                by_id[pack_id] = pack
    cache: Dict[str, Dict[str, Any]] = {}

    def resolve(pack: Dict[str, Any], seen: frozenset) -> Dict[str, Any]:
        pack_id = str(pack.get("asset_pack_id") or "").strip()
        if pack_id and pack_id in cache:
            return cache[pack_id]
        base_id = str(pack.get("extends") or "").strip()
        if not base_id or base_id not in by_id or base_id in seen or base_id == pack_id:
            resolved = dict(pack)
        else:
            base = resolve(by_id[base_id], seen | {pack_id})
            resolved = _merge_raw_pack(base, pack)
            resolved["extends_resolved_from"] = base_id
        if pack_id:
            cache[pack_id] = resolved
        return resolved

    return [resolve(pack, frozenset()) for pack in raw_packs if isinstance(pack, dict)]


def _normalize_desktop_manifest_payload(
    payload: Dict[str, Any],
    *,
    agent_name: str,
    manifest_path: str,
    assets_root: str,
) -> Dict[str, Any]:
    normalized_agent_name = str(payload.get("agent_name") or agent_name).strip() or agent_name
    normalized_asset_packs: List[Dict[str, Any]] = []
    for raw_pack in _resolve_pack_inheritance(list(payload.get("asset_packs") or [])):
        if not isinstance(raw_pack, dict):
            continue
        normalized_targets: List[Dict[str, Any]] = []
        for raw_target in raw_pack.get("targets") or []:
            if not isinstance(raw_target, dict):
                continue
            target = dict(raw_target)
            target["target_id"] = str(target.get("target_id") or "").strip()
            if not target["target_id"]:
                continue
            if isinstance(target.get("expected_image_path"), str) and target["expected_image_path"].strip():
                target["expected_image_path"] = target["expected_image_path"].strip()
            normalized_targets.append(target)

        normalized_asset_packs.append(
            {
                **raw_pack,
                "asset_pack_id": str(raw_pack.get("asset_pack_id") or "").strip(),
                "platform": normalize_platform_tag(raw_pack.get("platform")),
                "valid_until": str(raw_pack.get("valid_until") or "").strip() or None,
                "app_version": str(raw_pack.get("app_version") or "").strip() or None,
                "app_version_min": str(raw_pack.get("app_version_min") or "").strip() or None,
                "app_version_max": str(raw_pack.get("app_version_max") or "").strip() or None,
                "theme": str(raw_pack.get("theme") or "any").strip().lower() or "any",
                "display_scale": raw_pack.get("display_scale", "any"),
                "coordinate_space": str(raw_pack.get("coordinate_space") or "window").strip() or "window",
                "architectures": [
                    str(item).strip()
                    for item in (raw_pack.get("architectures") or [])
                    if str(item).strip()
                ],
                "os_versions": [
                    str(item).strip()
                    for item in (raw_pack.get("os_versions") or [])
                    if str(item).strip()
                ],
                "window_title_hints": [
                    str(item).strip()
                    for item in (raw_pack.get("window_title_hints") or [])
                    if str(item).strip()
                ],
                "process_names": [
                    str(item).strip()
                    for item in (raw_pack.get("process_names") or [])
                    if str(item).strip()
                ],
                "bootstrap_only": bool(raw_pack.get("bootstrap_only", False)),
                "selection_controls": dict(raw_pack.get("selection_controls") or {}),
                "submit_actions": list(raw_pack.get("submit_actions") or []),
                "targets": normalized_targets,
            }
        )

    return {
        "schema_version": int(payload.get("schema_version", 1)),
        "agent_name": normalized_agent_name,
        "title": str(payload.get("title") or normalized_agent_name).strip() or normalized_agent_name,
        "description": str(payload.get("description") or "").strip(),
        "app_id": str(payload.get("app_id") or normalized_agent_name).strip() or normalized_agent_name,
        "transport": str(payload.get("transport") or "desktop_gui").strip() or "desktop_gui",
        "process_names": {
            normalize_platform_tag(platform): [str(item).strip() for item in values if str(item).strip()]
            for platform, values in (payload.get("process_names") or {}).items()
            if isinstance(values, list)
        },
        "window_title_hints": {
            normalize_platform_tag(platform): [str(item).strip() for item in values if str(item).strip()]
            for platform, values in (payload.get("window_title_hints") or {}).items()
            if isinstance(values, list)
        },
        "launch_commands": {
            normalize_platform_tag(platform): list(values or [])
            for platform, values in (payload.get("launch_commands") or {}).items()
            if isinstance(values, list)
        },
        "working_directory_strategy": dict(payload.get("working_directory_strategy") or {}),
        "preferred_prompt_target_id": str(payload.get("preferred_prompt_target_id") or "composer_box").strip() or "composer_box",
        "preferred_submit_target_id": str(payload.get("preferred_submit_target_id") or "send_button").strip() or "send_button",
        "preferred_copy_response_target_id": str(payload.get("preferred_copy_response_target_id") or "").strip() or None,
        "asset_packs": normalized_asset_packs,
        "tags": [str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()],
        "manifest_path": str(manifest_path),
        "assets_root": str(assets_root),
    }


def _normalize_desktop_manifest_with_user_packs(
    payload: Dict[str, Any],
    *,
    agent_name: str,
    manifest_path: str,
    assets_root: str,
) -> Optional[Dict[str, Any]]:
    try:
        from .desktop_asset_store import load_user_desktop_asset_packs

        user_packs = load_user_desktop_asset_packs(agent_name)
    except Exception as exc:
        LOGGER.warning("Ignoring invalid user-local desktop packs for %s: %s", agent_name, exc)
        user_packs = []
    combined = dict(payload)
    combined_packs = list(payload.get("asset_packs") or []) if isinstance(payload.get("asset_packs"), list) else []
    if not combined_packs and not user_packs:
        local_manifest = Path(assets_root) / "manifest.json"
        if local_manifest.is_file() and str(local_manifest.resolve()) != str(Path(manifest_path).resolve()):
            try:
                local_data = json.loads(local_manifest.read_text(encoding="utf-8"))
                if isinstance(local_data, dict):
                    if isinstance(local_data.get("asset_packs"), list):
                        combined_packs = list(local_data.get("asset_packs"))
                    if not combined.get("launch_commands") and isinstance(local_data.get("launch_commands"), dict):
                        combined["launch_commands"] = dict(local_data.get("launch_commands"))
                    if not combined.get("working_directory_strategy") and isinstance(local_data.get("working_directory_strategy"), dict):
                        combined["working_directory_strategy"] = dict(local_data.get("working_directory_strategy"))
            except Exception as exc:
                LOGGER.debug("Could not read local manifest fallback %s: %s", local_manifest, exc)
    combined["asset_packs"] = combined_packs + user_packs
    return _normalize_desktop_manifest_payload(
        combined,
        agent_name=agent_name,
        manifest_path=manifest_path,
        assets_root=assets_root,
    )


def _read_desktop_manifest_path(manifest_path: Path, *, agent_name: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        LOGGER.warning("Ignoring invalid desktop manifest at %s: %s", manifest_path, exc)
        return None
    if not isinstance(payload, dict):
        LOGGER.warning("Ignoring invalid desktop manifest at %s: expected a JSON object", manifest_path)
        return None
    declared_agent_name = str(payload.get("agent_name") or agent_name).strip()
    if declared_agent_name != agent_name:
        LOGGER.warning(
            "Ignoring desktop manifest at %s: it declares agent %s, expected %s",
            manifest_path,
            declared_agent_name,
            agent_name,
        )
        return None
    return _normalize_desktop_manifest_with_user_packs(
        payload,
        agent_name=agent_name,
        manifest_path=str(manifest_path),
        assets_root=str(manifest_path.parent),
    )


def _load_desktop_manifest_from_package(agent_name: str) -> Optional[Dict[str, Any]]:
    try:
        assets_root = importlib_resources.files(f"autoyou_agents.{agent_name}").joinpath("desktop_assets")
        manifest_resource = assets_root.joinpath("manifest.template.json")
        if not manifest_resource.is_file():
            manifest_resource = assets_root.joinpath("manifest.json")
        if not manifest_resource.is_file():
            return None
        with manifest_resource.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        return _normalize_desktop_manifest_with_user_packs(
            payload,
            agent_name=agent_name,
            manifest_path=str(manifest_resource),
            assets_root=str(assets_root),
        )
    except ModuleNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.warning("Ignoring invalid packaged desktop manifest for %s: %s", agent_name, exc)
        return None


def load_desktop_app_manifest(agent_dir: Path) -> Optional[Dict[str, Any]]:
    agent_dir_path = Path(agent_dir)
    agent_name = agent_dir_path.name
    roots = [agent_dir_path]
    try:
        from shared.platform_runtime import iter_agent_roots

        roots.extend(root / agent_name for root in iter_agent_roots(__file__))
    except Exception:
        pass

    unique_roots: List[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved not in unique_roots:
            unique_roots.append(resolved)

    # A code-owned template defines the portable metadata contract and takes
    # precedence over any legacy checkout-local manifest. User imagery is
    # merged only from the private per-user store.
    for root in unique_roots:
        template_path = root / DESKTOP_MANIFEST_TEMPLATE_RELATIVE_PATH
        if template_path.is_file():
            manifest = _read_desktop_manifest_path(template_path, agent_name=agent_name)
            if manifest is not None:
                return manifest
    for root in unique_roots:
        manifest_path = root / DESKTOP_MANIFEST_RELATIVE_PATH
        if manifest_path.is_file():
            manifest = _read_desktop_manifest_path(manifest_path, agent_name=agent_name)
            if manifest is not None:
                return manifest

    return _load_desktop_manifest_from_package(agent_name)


def discover_desktop_app_manifests(
    *,
    agents_root: Path,
    agent_names: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    roots: List[Path] = []
    try:
        from shared.platform_runtime import is_compiled, iter_agent_roots

        if is_compiled():
            for candidate in iter_agent_roots(__file__):
                candidate_path = Path(candidate).resolve()
                if candidate_path not in roots:
                    roots.append(candidate_path)
    except Exception:
        pass

    resolved_agents_root = Path(agents_root).resolve()
    if resolved_agents_root not in roots:
        roots.append(resolved_agents_root)

    if agent_names is not None:
        names = sorted({str(item).strip() for item in agent_names if str(item).strip()})
    else:
        names = []
        discovered: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.iterdir():
                if path.is_dir() and (
                    (path / DESKTOP_MANIFEST_RELATIVE_PATH).is_file()
                    or (path / DESKTOP_MANIFEST_TEMPLATE_RELATIVE_PATH).is_file()
                ):
                    discovered.add(path.name)
        names = sorted(discovered)

    manifests: List[Dict[str, Any]] = []
    for agent_name in names:
        manifest = None
        for root in roots:
            candidate = root / agent_name
            if candidate.is_dir():
                manifest = load_desktop_app_manifest(candidate)
                if manifest:
                    break
        if manifest is None:
            manifest = _load_desktop_manifest_from_package(agent_name)
        if manifest:
            manifests.append(manifest)
    return manifests


def render_desktop_agent_llm_reference(manifest: Dict[str, Any]) -> str:
    lines: List[str] = []
    title = str(manifest.get("title") or manifest.get("agent_name") or "Desktop Agent").strip()
    lines.append(f"{title} Desktop Automation Reference")
    lines.append("")
    lines.append("Purpose")
    lines.append(f"- Agent: `{manifest.get('agent_name')}`")
    lines.append(f"- App ID: `{manifest.get('app_id')}`")
    lines.append(f"- Transport: `{manifest.get('transport')}`")
    if manifest.get("description"):
        lines.append(f"- Scope: {manifest['description']}")
    lines.append("")
    lines.append("Working Directory Strategy")
    strategy = manifest.get("working_directory_strategy") or {}
    mode = str(strategy.get("mode") or "none").strip() or "none"
    lines.append(f"- Mode: `{mode}`")
    template = str(strategy.get("template") or "").strip()
    if template:
        lines.append(f"- Prompt template: `{template}`")
    lines.append("")
    lines.append("Preferred Targets")
    lines.append(f"- Prompt target: `{manifest.get('preferred_prompt_target_id') or 'composer_box'}`")
    lines.append(f"- Submit target: `{manifest.get('preferred_submit_target_id') or 'send_button'}`")
    if manifest.get("preferred_copy_response_target_id"):
        lines.append(f"- Copy response target: `{manifest.get('preferred_copy_response_target_id')}`")
    lines.append("")
    lines.append("Asset Packs")
    for pack in manifest.get("asset_packs") or []:
        pack_id = str(pack.get("asset_pack_id") or "").strip() or "(unnamed)"
        platform = str(pack.get("platform") or "any").strip() or "any"
        lines.append(f"- `{pack_id}`")
        lines.append(f"  - Platform: `{platform}`")
        if pack.get("valid_until"):
            lines.append(f"  - Valid until: `{pack['valid_until']}`")
        if pack.get("architectures"):
            lines.append(f"  - Architectures: {', '.join(pack['architectures'])}")
        if pack.get("os_versions"):
            lines.append(f"  - OS versions: {', '.join(pack['os_versions'])}")
        if pack.get("description"):
            lines.append(f"  - Description: {pack['description']}")
        if pack.get("notes"):
            lines.append(f"  - Notes: {pack['notes']}")
        if pack.get("bootstrap_only"):
            lines.append("  - Bootstrap only: requires better screenshots before production use.")
        selection_controls = pack.get("selection_controls") or {}
        if selection_controls:
            lines.append("  - Selection controls:")
            for control_name, control in selection_controls.items():
                if not isinstance(control, dict):
                    continue
                open_target = str(control.get("open_target_id") or "").strip()
                option_names: List[str] = []
                for group_name in ("options", "effort_options"):
                    group = control.get(group_name)
                    if isinstance(group, dict):
                        option_names.extend(str(name) for name in group.keys())
                details = f"open target `{open_target}`" if open_target else "no open target"
                if option_names:
                    details += f"; options: {', '.join(sorted(option_names))}"
                lines.append(f"    - `{control_name}`: {details}")
        targets = pack.get("targets") or []
        if targets:
            lines.append("  - Targets:")
            for target in targets:
                target_id = str(target.get("target_id") or "").strip()
                if not target_id:
                    continue
                description = str(target.get("description") or "").strip()
                lines.append(f"    - `{target_id}`: {description or 'No description provided.'}")
                if target.get("visual_anchor_hint"):
                    lines.append(f"      - Visual hint: {target['visual_anchor_hint']}")
                if target.get("expected_image_path"):
                    lines.append(f"      - Expected image path: `{target['expected_image_path']}`")
                if target.get("normalized_box"):
                    lines.append(f"      - Normalized box: {target['normalized_box']}")
                if target.get("click_point"):
                    lines.append(f"      - Click point: {target['click_point']}")
        submit_actions = pack.get("submit_actions") or []
        if submit_actions:
            lines.append(f"  - Submit actions: {submit_actions}")
    lines.append("")
    lines.append("Crowd-Sourcing Rules")
    lines.append("- Keep each new screenshot pack versioned with a stable `asset_pack_id` and a `valid_until` date.")
    lines.append("- Prefer window-relative coordinates when possible; use screen-relative coordinates only as a fallback.")
    lines.append("- When replacing screenshots, update both `manifest.json` and this `llm.txt` so future LLMs inherit the new visual map.")
    lines.append("- Never delete an older pack until a newer pack has been validated on the target OS build.")
    lines.append("")
    lines.append("Future Maintenance")
    lines.append("- Add new screenshots under `desktop_assets/images/...` and reference them from the matching target entry.")
    lines.append("- If a UI version stops matching before its `valid_until` date, shorten the date and add a replacement pack.")
    lines.append("- Record whether the app was maximized, windowed, or on a scaled display when the pack was captured.")
    return "\n".join(lines).rstrip() + "\n"


def write_desktop_agent_llm_reference(agent_dir: Path, manifest: Dict[str, Any]) -> Path:
    llm_path = Path(agent_dir) / DESKTOP_LLM_RELATIVE_PATH
    # from __debug_provenance_q__ import payment
    llm_path.parent.mkdir(parents=True, exist_ok=True)
    llm_path.write_text(render_desktop_agent_llm_reference(manifest), encoding="utf-8")
    return llm_path
