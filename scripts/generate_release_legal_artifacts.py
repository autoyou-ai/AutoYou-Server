#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-89ba6dfe106124ec13651047

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Generate per-artifact SBOM and NOTICE bundles for AutoYou releases."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import copy
import datetime as dt
import json
import re
import uuid
from pathlib import Path
from typing import Any

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-89ba6dfe106124ec13651047"


try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback.
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "docs" / "legal" / "release-artifacts.json"

REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*(.*)$")
REQ_EXACT_RE = re.compile(r"==\s*([^,;\s]+)")
GRADLE_VAR_RE = re.compile(r"\bval\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([^\"]+)\"")
GRADLE_COORD_RE = re.compile(
    r"\"([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)(?::([^\"()]+))?\""
)
GRADLE_TEST_DEPENDENCY_RE = re.compile(r"^\s*(?:test|androidTest)\w*\s*\(")


def repo_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def normalize_lookup_key(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    return json.loads(config_path.read_text(encoding="utf-8"))


def _license_entry(license_name: str) -> dict[str, Any]:
    spdx_like = bool(re.fullmatch(r"[A-Za-z0-9.+-]+(?:\s+OR\s+[A-Za-z0-9.+-]+)?", license_name))
    if spdx_like and "Terms" not in license_name and "License" not in license_name:
        return {"license": {"id": license_name}}
    return {"license": {"name": license_name}}


def _purl(ecosystem: str, name: str, version: str | None, *, group: str | None = None) -> str | None:
    if ecosystem == "pypi":
        base = f"pkg:pypi/{name}"
    elif ecosystem == "npm":
        base = f"pkg:npm/{name}"
    elif ecosystem == "maven" and group:
        base = f"pkg:maven/{group}/{name}"
    else:
        return None
    return f"{base}@{version}" if version else base


def make_component(
    *,
    component_type: str,
    name: str,
    ecosystem: str,
    version: str | None = None,
    license_name: str | None = None,
    source: str | None = None,
    group: str | None = None,
    properties: dict[str, str] | None = None,
) -> dict[str, Any]:
    component: dict[str, Any] = {
        "type": component_type,
        "name": name,
        "ecosystem": ecosystem,
    }
    if version:
        component["version"] = version
    if group:
        component["group"] = group
    if source:
        component["source"] = source
    if license_name:
        component["license"] = license_name
        component["licenses"] = [_license_entry(license_name)]
    purl = _purl(ecosystem, name, version, group=group)
    if purl:
        component["purl"] = purl
    if properties:
        component["properties"] = [{"name": key, "value": value} for key, value in sorted(properties.items())]
    return component


def _strip_requirement_comment(line: str) -> str:
    for marker in (" #", "\t#"):
        if marker in line:
            return line.split(marker, 1)[0].strip()
    return line.strip()


def _parse_requirement_spec(spec: str, *, source: str) -> dict[str, Any] | None:
    spec = _strip_requirement_comment(spec)
    if not spec or spec.startswith("#") or spec.startswith("--"):
        return None
    if "://" in spec and "#egg=" in spec:
        name = spec.rsplit("#egg=", 1)[1].split("&", 1)[0]
        return make_component(component_type="library", name=name, ecosystem="pypi", source=source)
    spec_without_marker = spec.split(";", 1)[0].strip()
    match = REQ_NAME_RE.match(spec_without_marker)
    if not match:
        return None
    name, remainder = match.groups()
    exact_match = REQ_EXACT_RE.search(remainder)
    version = exact_match.group(1) if exact_match else None
    properties = {"requirement": spec}
    if remainder.strip() and not version:
        properties["specifier"] = remainder.strip()
    return make_component(
        component_type="library",
        name=name,
        ecosystem="pypi",
        version=version,
        source=source,
        properties=properties,
    )


def parse_requirements(path: Path, *, seen: set[Path] | None = None) -> list[dict[str, Any]]:
    seen = seen or set()
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    if not path.is_file():
        raise FileNotFoundError(f"requirements file not found: {path}")
    components: list[dict[str, Any]] = []
    # from __debug_provenance_d__ import to
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = _strip_requirement_comment(raw_line)
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            nested = line.split(maxsplit=1)[1]
            components.extend(parse_requirements(path.parent / nested, seen=seen))
            continue
        component = _parse_requirement_spec(raw_line, source=repo_relative(path))
        if component:
            components.append(component)
    return components


def parse_pyproject(path: Path, *, include_optional: bool) -> list[dict[str, Any]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    project_name = str(project.get("name", "")).strip()
    specs: list[str] = list(project.get("dependencies", []) or [])
    if include_optional:
        optional = project.get("optional-dependencies", {}) or {}
        for values in optional.values():
            specs.extend(values or [])
    components: list[dict[str, Any]] = []
    for spec in specs:
        component = _parse_requirement_spec(str(spec), source=repo_relative(path))
        if not component:
            continue
        if project_name and normalize_name(component["name"]) == normalize_name(project_name):
            continue
        components.append(component)
    return components


def parse_npm_package(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    dependencies = data.get("dependencies", {}) or {}
    lock_path = path.with_name("package-lock.json")
    lock_data = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.is_file() else {}
    locked_packages = lock_data.get("packages", {}) or {}
    locked_dependencies = lock_data.get("dependencies", {}) or {}
    components = []
    for name, raw_version in sorted(dependencies.items()):
        specifier = str(raw_version).strip()
        source_specifier = (
            "://" in specifier
            or specifier.startswith(("file:", "git+", "git@", "github:", "gitlab:", "bitbucket:"))
            or specifier.partition("#")[0].endswith((".tgz", ".tar.gz"))
        )
        if source_specifier:
            locked = locked_packages.get(f"node_modules/{name}") or locked_dependencies.get(name) or {}
            version = str(locked.get("version") or "").strip() or None
        else:
            version = specifier.lstrip("^~") or None
        components.append(
            make_component(
                component_type="library",
                name=name,
                ecosystem="npm",
                version=version,
                source=repo_relative(path),
                properties={"specifier": str(raw_version)},
            )
        )
    return components


def _resolve_gradle_version(raw: str | None, variables: dict[str, str]) -> str | None:
    if raw is None:
        return None
    version = raw.strip()
    if version.startswith("${") and version.endswith("}"):
        return variables.get(version[2:-1], version)
    if version.startswith("$"):
        return variables.get(version[1:], version)
    return version


def parse_gradle(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    variables = {name: value for name, value in GRADLE_VAR_RE.findall(text)}
    components: list[dict[str, Any]] = []
    for line in text.splitlines():
        if GRADLE_TEST_DEPENDENCY_RE.match(line):
            continue
        for group, name, raw_version in GRADLE_COORD_RE.findall(line):
            version = _resolve_gradle_version(raw_version or None, variables)
            component_name = f"{group}:{name}"
            components.append(
                make_component(
                    component_type="library",
                    name=component_name,
                    ecosystem="maven",
                    version=version,
                    source=repo_relative(path),
                    group=group,
                    properties={"artifact": name},
                )
            )
    return components


def parse_swift_package_resolved(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    components = []
    for pin in data.get("pins", []):
        state = pin.get("state", {}) or {}
        version = state.get("version") or state.get("revision")
        components.append(
            make_component(
                component_type="library",
                name=pin.get("identity", pin.get("location", "unknown")),
                ecosystem="swiftpm",
                version=version,
                source=pin.get("location") or repo_relative(path),
                properties={"revision": state.get("revision", "")},
            )
        )
    return components


def _manual_component(raw: dict[str, Any]) -> dict[str, Any]:
    component = {
        "type": raw.get("type", "library"),
        "name": raw["name"],
        "ecosystem": raw.get("ecosystem", "manual"),
    }
    for key in ("version", "source", "distributionBoundary"):
        if raw.get(key):
            component[key] = raw[key]
    license_name = raw.get("license")
    if license_name:
        component["license"] = license_name
        component["licenses"] = [_license_entry(license_name)]
    return component


def expand_artifact_profiles(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_profiles = {profile["id"]: profile for profile in config.get("artifactProfiles", [])}
    resolved: dict[str, dict[str, Any]] = {}

    def resolve(profile_id: str) -> dict[str, Any]:
        if profile_id in resolved:
            return copy.deepcopy(resolved[profile_id])
        profile = copy.deepcopy(raw_profiles[profile_id])
        parent_id = profile.pop("inherits", None)
        exclude_manual_components = set(profile.pop("excludeManualComponents", []))
        if parent_id:
            merged = resolve(parent_id)
            merged["id"] = profile_id
            if exclude_manual_components:
                merged["manualComponents"] = [
                    component
                    for component in merged.get("manualComponents", [])
                    if component.get("name") not in exclude_manual_components
                ]
            for key, value in profile.items():
                if isinstance(value, list) and isinstance(merged.get(key), list):
                    merged[key] = merged[key] + value
                else:
                    merged[key] = value
            profile = merged
        resolved[profile_id] = copy.deepcopy(profile)
        return profile

    return [resolve(profile["id"]) for profile in config.get("artifactProfiles", [])]


def _component_identity(component: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        component.get("ecosystem", ""),
        normalize_lookup_key(component.get("group", "")),
        normalize_lookup_key(component.get("name", "")),
        component.get("version", ""),
    )


def _apply_known_license(component: dict[str, Any], known: dict[str, str]) -> dict[str, Any]:
    if component.get("license"):
        return component
    keys = [
        normalize_lookup_key(component.get("name", "")),
        normalize_name(component.get("name", "")),
    ]
    if component.get("group"):
        artifact = component.get("properties", [{}])[0].get("value", "") if component.get("properties") else ""
        keys.append(normalize_lookup_key(f"{component['group']}:{artifact}"))
        keys.append(normalize_lookup_key(component["name"]))
    for key in keys:
        if key in known:
            component["license"] = known[key]
            component["licenses"] = [_license_entry(known[key])]
            break
    return component


def collect_components_for_artifact(profile: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for source in profile.get("sources", []):
        path = REPO_ROOT / source["path"]
        source_type = source["type"]
        if source_type == "requirements":
            components.extend(parse_requirements(path))
        elif source_type == "pyproject":
            components.extend(parse_pyproject(path, include_optional=bool(source.get("includeOptional"))))
        elif source_type == "npm":
            components.extend(parse_npm_package(path))
        elif source_type == "gradle":
            components.extend(parse_gradle(path))
        elif source_type == "swiftpm":
            components.extend(parse_swift_package_resolved(path))
        else:
            raise ValueError(f"Unsupported source type {source_type!r} in {profile['id']}")
    components.extend(_manual_component(item) for item in profile.get("manualComponents", []))

    known = {normalize_lookup_key(key): value for key, value in config.get("knownLicenses", {}).items()}
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for component in components:
        component = _apply_known_license(component, known)
        deduped.setdefault(_component_identity(component), component)
    return sorted(deduped.values(), key=lambda item: (item.get("ecosystem", ""), item.get("name", ""), item.get("version", "")))


def build_sbom(profile: dict[str, Any], components: list[dict[str, Any]], *, timestamp: str) -> dict[str, Any]:
    cyclone_components = []
    for component in components:
        item = {key: value for key, value in component.items() if key not in {"ecosystem", "license", "source", "distributionBoundary"}}
        properties = item.setdefault("properties", [])
        properties.append({"name": "autoyou:ecosystem", "value": component.get("ecosystem", "")})
        if component.get("source"):
            properties.append({"name": "autoyou:source", "value": component["source"]})
        if component.get("distributionBoundary"):
            properties.append({"name": "autoyou:distributionBoundary", "value": component["distributionBoundary"]})
        cyclone_components.append(item)
    application_component: dict[str, Any] = {
        "type": "application",
        "name": profile["displayName"],
        "bom-ref": profile["id"],
    }
    app_properties = []
    for key, property_name in (
        ("releaseProfile", "autoyou:releaseProfile"),
        ("dependencyProfile", "autoyou:dependencyProfile"),
        ("legalBundle", "autoyou:legalBundle"),
    ):
        if profile.get(key):
            app_properties.append({"name": property_name, "value": str(profile[key])})
    if app_properties:
        application_component["properties"] = app_properties

    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": [
                {
                    "vendor": "AutoYou",
                    "name": "generate_release_legal_artifacts.py",
                    "version": "1",
                }
            ],
            "component": application_component,
        },
        "components": cyclone_components,
    }


def build_notice(profile: dict[str, Any], components: list[dict[str, Any]], *, timestamp: str) -> str:
    lines = [
        "AutoYou Artifact Notice Bundle",
        "================================",
        "",
        f"Artifact: {profile['displayName']} ({profile['id']})",
        f"Release profile: {profile.get('releaseProfile', 'unspecified')}",
        f"Dependency profile: {profile.get('dependencyProfile', 'unspecified')}",
        f"Generated: {timestamp}",
        "",
        profile.get("description", ""),
        "",
        "AutoYou License",
        "---------------",
        "AutoYou project code is licensed as stated in the repository LICENSE files and any artifact-specific license files.",
        "This NOTICE file does not grant rights beyond those licenses.",
        "",
        "Third-Party Components",
        "----------------------",
    ]
    if not components:
        lines.append("- No third-party components were discovered for this artifact profile.")
    for component in components:
        version = f" {component['version']}" if component.get("version") else ""
        license_name = component.get("license", "UNKNOWN - review before release")
        source = component.get("source") or component.get("purl") or component.get("ecosystem", "unknown source")
        boundary = f"; boundary: {component['distributionBoundary']}" if component.get("distributionBoundary") else ""
        lines.append(f"- {component['name']}{version} [{component.get('ecosystem', 'unknown')}] - {license_name} - {source}{boundary}")

    if profile.get("allowedCopyleft"):
        lines.extend(["", "Copyleft Review Notes", "---------------------"])
        for item in profile["allowedCopyleft"]:
            lines.append(f"- {item['name']} ({item['license']}): {item['condition']}")

    if profile.get("proprietaryReview"):
        lines.extend(["", "Vendor / Proprietary Terms Review", "----------------------------------"])
        for item in profile["proprietaryReview"]:
            lines.append(f"- {item}: review and retain current vendor terms before release.")

    if profile.get("specialObligations"):
        lines.extend(["", "Special Release Obligations", "---------------------------"])
        for item in profile["specialObligations"]:
            lines.append(f"- {item}")

    lines.extend([
        "",
        "Repository Notice Index",
        "-----------------------",
        "See THIRD-PARTY-NOTICES.md and https://autoyou.me/attributions/ for the broader notice index.",

        "",
    ])
    return "\n".join(lines)


def generate_artifacts(config: dict[str, Any], *, only_artifact: str | None = None) -> dict[str, Any]:
    generated_root = REPO_ROOT / config.get("generatedRoot", "docs/legal/generated")
    generated_root.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    summary: dict[str, Any] = {"generatedAt": timestamp, "artifacts": []}
    for profile in expand_artifact_profiles(config):
        if only_artifact and profile["id"] != only_artifact:
            continue
        components = collect_components_for_artifact(profile, config)
        artifact_dir = generated_root / profile["id"]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        sbom = build_sbom(profile, components, timestamp=timestamp)
        (artifact_dir / "sbom.cdx.json").write_text(
            json.dumps(sbom, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (artifact_dir / "NOTICE.txt").write_text(
            build_notice(profile, components, timestamp=timestamp),
            encoding="utf-8",
        )
        summary["artifacts"].append(
            {
                "id": profile["id"],
                "displayName": profile["displayName"],
                "componentCount": len(components),
                "notice": repo_relative(artifact_dir / "NOTICE.txt"),
                "sbom": repo_relative(artifact_dir / "sbom.cdx.json"),
            }
        )
    # A scoped release refresh owns only its artifact directory. Rewriting the
    # aggregate files here would silently erase every profile not in scope.
    if only_artifact is None:
        (generated_root / "manifest-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        readme_lines = [
            "# Generated Release Legal Artifacts",
            "",
            f"Generated at: {timestamp}",
            "",
            "Run `python scripts/generate_release_legal_artifacts.py` to refresh these files before release packaging.",
            "",
        ]
        for item in summary["artifacts"]:
            readme_lines.append(f"- `{item['id']}`: `{item['notice']}`, `{item['sbom']}`")
        (generated_root / "README.md").write_text(
            "\n".join(readme_lines) + "\n",
            encoding="utf-8",
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate AutoYou per-artifact SBOM and NOTICE bundles.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--artifact", help="Generate a single artifact profile by id.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    summary = generate_artifacts(config, only_artifact=args.artifact)
    print(f"Generated {len(summary['artifacts'])} artifact legal bundle(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
