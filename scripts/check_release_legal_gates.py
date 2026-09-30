#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-U-usdt-b18b28fe096d5ca438862d61

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Release legal gates for Billing, store-copy, SBOM/NOTICE, and licenses."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import json
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

__debug_provenance_u__ = "AUTOYOU-PROVENANCE-U-usdt-b18b28fe096d5ca438862d61"


try:
    from scripts.generate_release_legal_artifacts import (
        REPO_ROOT,
        build_notice,
        build_sbom,
        collect_components_for_artifact,
        expand_artifact_profiles,
        generate_artifacts,
        load_config,
        normalize_name,
    )
except ModuleNotFoundError:
    from generate_release_legal_artifacts import (
        REPO_ROOT,
        build_notice,
        build_sbom,
        collect_components_for_artifact,
        expand_artifact_profiles,
        generate_artifacts,
        load_config,
        normalize_name,
    )

try:
    import certifi
except ModuleNotFoundError:
    certifi = None


DEFAULT_REPORT_PATH = REPO_ROOT / "build" / "legal-gate-report.json"
# from __debug_provenance_u__ import usdt
SOURCE_SUFFIXES = {".java", ".kt", ".kts", ".swift", ".m", ".mm", ".cs"}
WEBSITE_SUFFIXES = {".html", ".xml"}
LEGAL_WORDING_SUFFIXES = SOURCE_SUFFIXES | WEBSITE_SUFFIXES | {
    ".appxmanifest",
    ".bat",
    ".cjs",
    ".csproj",
    ".gradle",
    ".iss",
    ".js",
    ".jsx",
    ".json",
    ".mjs",
    ".md",
    ".pbxproj",
    ".php",
    ".plist",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xaml",
    ".xcconfig",
    ".yaml",
    ".yml",
}
LEGAL_WORDING_FILENAMES = {"LICENSE"}
LEGACY_LEGAL_WORDING = (
    "AutoYou MIT license",
    "waive your 14-day right",
    "AutoYou and its contributors accept",
    "AutoYou and its contributors are not liable",
    "complete waiver",
    "liability-free",
    "liability free",
    "liability waived",
    "legal-cleared",
    "legal cleared",
    "legal clearance",
    "legal-compliant",
    "legal compliant",
    "lawyer-reviewed",
    "lawyer reviewed",
    "statutory compliance",
    "guaranteed compliant",
    "fully compliant with laws",
    "compliant with all laws",
    "lawsuit-proof",
    "cease-and-desist-proof",
    "liability release",
    "Universal Waiver",
    "forever barred",
    "IP address constitutes binding",
    "we literally cannot see your messages",
    "complete privacy",
    "completely private",
    "fully private",
    "all free. all private",
    "zero cloud dependency",
    "cannot see your data",
    "Nobody else can see in",
    "can't be hacked",
    "zero attack surface",
    "zero tracking",
    "no data to steal",
    "never store usage information",
    "never passes through AutoYou servers",
    "never passes through our servers",
    "zero data collection",
    "zero data collected",
    "Local pairing collects nothing",
    "no data leaves your device",
    "AutoYou holds no personal data about you",
    "never collected or accessible by AutoYou",
    "stored in an encrypted database",
    "Licensed under the MIT License",
    "This software is released under the MIT License.",
    "AutoYou - MIT License",
    "AutoYou" + " LLC",
    "AutoYou" + ", LLC",
    "License :: Other/Proprietary License",
    "Proprietary and Confidential",
    "Unauthorized copying, modification, or distribution of this file is strictly prohibited",
    "open-source server components",
    "--accept-" + "legal",
)
LEGAL_WORDING_SKIP_PARTS = {
    ".git",
    ".venv",
    "artifacts",
    "build",
    "dist",
    "downloads",
    "Frameworks",
    "generated",
    "playwright-report",
    "test-results",
    "venv",
    "__pycache__",
    "node_modules",
    "runtime_site_packages",
    "runtime_stdlib",
}
RUNTIME_ACCEPTANCE_MARKERS = (('Root license',
  'LICENSE',
  ('AutoYou Source-Available Personal-Use License',
   'User Responsibility and Lawful Operation',
   'OpenStorey, the AutoYou Owner Board, contributors')),
 ('Trademark policy',
  'docs/legal/trademark-policy.md',
  ('Forked from AutoYou',
   'not sponsored, endorsed, certified, reviewed, or supported by OpenStorey LLC',
   'No Commercial Or Platform Rights',
   'OpenStorey may require correction, renaming, takedown')),
 ('Source publication manifest',
  'docs/legal/source-publication-manifest.md',
  ('Publishable Source Scope',
   'Do Not Publish In Source Releases',
   'production secrets, API keys, certificates, signing keys',
   'run the server release legal gate with `python scripts/check_release_legal_gates.py --artifact-scope server --strict-unknown-license`',
   'Publication Evidence',
   'publication URL or package source URL',
   'source archive checksum',
   'reviewer names or roles',
   'Publication of this source set does not publish or license OpenStorey-hosted services')),
 ('Server/Admin legal gate',
  ('server.py', 'routers/pairing.py', 'routers/admin_ui.py'),
  ('CURRENT_AGREEMENT_VERSION',
   '"agreement_version": server.CURRENT_AGREEMENT_VERSION',
   'is_license_acknowledged',
   'agreement_required',
   '_CURRENT_TERMS_ACCEPTANCE_LABEL',
   'terms-accepted',
   'I accept the current Terms of Use (EULA), License, responsibility terms',
   'https://www.autoyou.me/terms/',
   'https://www.autoyou.me/privacy/',
   "#ayu-login-root .ayu-legal-acceptance input[type='checkbox']",
   'appearance:auto',
   'width:18px',
   'record_license_acknowledgement',
   'RESPONSIBILITY NOTICE:',
   'OpenStorey LLC, the AutoYou owner board, and contributors disclaim warranties',
   'Bundled release notices',
   'get_third_party_notices_file',
   'get_sbom_file')),
 ('Server agreement version',
  'shared/first_run.py',
  ('CURRENT_AGREEMENT_VERSION = "2026-07-09"', 'LICENSE_ACKNOWLEDGEMENT')),
 ('Server PBKDF2 envelope work factor', 'server.py', ('iterations=600000',)),
 ('Shared encrypted JSON PBKDF2 work factor', 'shared/encrypted_json_store.py', ('iterations=600_000',)),
 ('Agent shell command opt-in gate',
  'autoyou_agents/shared_tools/workspace_tools.py',
  ('AUTOYOU_ENABLE_AGENT_RUN_COMMAND', 'disabled by default for release safety')),
 ('Auth global rate limit gate',
  'server.py',
  ('AUTH_GLOBAL_RATE_LIMITER', 'AUTOYOU_AUTH_GLOBAL_RATE_LIMIT_MAX_REQUESTS')),
 ('Page agent WebRTC remote write gate',
  'autoyou_agents/page_agent/website/backend/app.py',
  ('remote_browser_write_guard',
   'X-AutoYou-WebRTC-Session-Id',
   'Page feed write actions are only available from the local owner browser.')),
 ('Server cloud token local max age',
  'server.py',
  ('AUTOYOU_CLOUD_SERVER_TOKEN_MAX_AGE_SECONDS',
   '_cloud_server_token_expired',
   'Saved cloud session expired. Re-link Cloud Pair to continue.')),
 ('Windows server host OpenStorey metadata',
  'servers/windows/AutoYouWindowsHost/Package.appxmanifest',
  ('Publisher="CN=OpenStorey LLC"',
   '<PublisherDisplayName>OpenStorey LLC</PublisherDisplayName>',
   'Description="AutoYou local server host"')),
 ('Windows server installer legal metadata',
  'installer/AutoYouInstaller.iss',
  ('#define MyPublisher "OpenStorey LLC"',
   '#define MyURL "https://www.autoyou.me/"',
   'AppSupportURL=https://www.autoyou.me/support/',
   'LicenseFile={#MyDistDir}\\Legal\\LICENSE')),
 ('macOS server host OpenStorey metadata',
  'servers/macos/apple/AutoYou/Info.plist',
  ('Copyright (c) 2026 OpenStorey LLC. All rights reserved.',)),
 ('WSL server legal README notice',
  'servers/wsl/README.md',
  ('Review `Legal/LICENSE`', 'THIRD-PARTY-NOTICES.md', 'Use constitutes agreement', 'warranty disclaimer', 'liability limits')),
 ('Windows server legal README notice',
  'servers/windows/README.md',
  ('Review `servers/windows/dist/AutoYou-win-x64/Legal/LICENSE`',
   'THIRD-PARTY-NOTICES.md',
   'Use constitutes agreement',
   'warranty disclaimer',
   'liability limits')),
 ('macOS server legal README notice',
  'servers/macos/README.md',
  ('Review `servers/macos/build/AutoYou.app/Contents/Resources/Legal/LICENSE`',
   'THIRD-PARTY-NOTICES.md',
   'Use constitutes agreement',
   'warranty disclaimer',
   'liability limits')),
 ('macOS Intel server legal README notice',
  'servers/macos/intel/README.md',
  ('Review `servers/macos/intel/build/AutoYou.app/Contents/Resources/Legal/LICENSE`',
   'THIRD-PARTY-NOTICES.md',
   'Use constitutes agreement',
   'warranty disclaimer',
   'liability limits')))
PACKAGED_LEGAL_BUNDLES = (('Windows Server dist legal bundle',
  'servers/windows/dist/AutoYou-win-x64/Legal',
  ('binary-default', 'connector-full')),
 ('Windows Server backend dist legal bundle',
  'servers/windows/dist/AutoYou-win-x64/Backend/Legal',
  ('binary-default', 'connector-full')),
 ('Windows Server backend artifact legal bundle',
  'servers/windows/artifacts/backend/AutoYouServer/Legal',
  ('binary-default', 'connector-full')),
 ('macOS Server app legal bundle',
  'servers/macos/build/AutoYou.app/Contents/Resources/Legal',
  ('binary-default', 'connector-full')))
LEGAL_BUNDLE_FILES = ("LICENSE", "NOTICE.txt", "sbom.cdx.json", "THIRD-PARTY-NOTICES.md")
RELEASE_CHECKLIST_PATH = REPO_ROOT / "docs" / "legal" / "release-compliance-checklist.md"
HOSTED_LEGAL_PAGES = (
    ("privacy", "/privacy/", ("OpenStorey LLC", "Data Controller:", "CCPA", "not sell or share")),
    ("terms", "/terms/", ("OpenStorey LLC", "source-available", "Class action waiver")),
    ("license", "/license/", ("AutoYou Source-Available Personal-Use License", "OpenStorey LLC", "User responsibility")),
    ("subscription", "/subscription/", ("Subscription Information", "Cloud Pair", "auto-renew", "App Store", "Google Play")),
    ("support", "/support/", ("OpenStorey LLC", "support@autoyou.me")),
)
ARTIFACT_SCOPES = ("server",)
SERVER_ARTIFACT_ID_PREFIX = "autoyou-server-"


@dataclass
class GateReport:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def note(self, message: str) -> None:
        self.info.append(message)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failures": self.failures,
            "warnings": self.warnings,
            "info": self.info,
        }


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _artifact_profiles_for_scope(config: dict[str, Any], artifact_scope: str) -> list[dict[str, Any]]:
    profiles = expand_artifact_profiles(config)
    if artifact_scope == "server":
        return [profile for profile in profiles if str(profile.get("id", "")).startswith(SERVER_ARTIFACT_ID_PREFIX)]
    return profiles


def _config_for_scope(config: dict[str, Any], artifact_scope: str) -> dict[str, Any]:
    if artifact_scope != "server":
        return config
    return {
        **config,
        "artifactProfiles": [
            profile
            for profile in config.get("artifactProfiles", [])
            if str(profile.get("id", "")).startswith(SERVER_ARTIFACT_ID_PREFIX)
        ],
    }


def _display_report_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _should_scan_legal_wording(path: Path) -> bool:
    if path.name not in LEGAL_WORDING_FILENAMES and path.suffix not in LEGAL_WORDING_SUFFIXES:
        return False
    relative_parts = set(path.relative_to(REPO_ROOT).parts)
    return not (relative_parts & LEGAL_WORDING_SKIP_PARTS)


def check_legacy_legal_wording(report: GateReport) -> None:
    roots = [
        REPO_ROOT / "LICENSE",
        REPO_ROOT / "README.md",
        REPO_ROOT / "server.py",
        REPO_ROOT / "run_autoyou.bat",
        REPO_ROOT / "run_autoyou.sh",
        REPO_ROOT / "clients",
        REPO_ROOT / "installer",
        REPO_ROOT / "servers",
        REPO_ROOT / "shared",
        REPO_ROOT / "docs",
    ]
    lowered_needles = [(needle, needle.lower()) for needle in LEGACY_LEGAL_WORDING]
    for root in roots:
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file() or not _should_scan_legal_wording(path):
                continue
            lowered_text = _read_text(path).lower()
            for needle, lowered_needle in lowered_needles:
                if lowered_needle in lowered_text:
                    report.fail(f"Forbidden legal or privacy overclaim wording {needle!r} found in {_display_report_path(path)}.")


def check_runtime_acceptance_markers(report: GateReport) -> None:
    for label, relative_paths, needles in RUNTIME_ACCEPTANCE_MARKERS:
        paths = (relative_paths,) if isinstance(relative_paths, str) else tuple(relative_paths)
        missing_paths = [relative_path for relative_path in paths if not (REPO_ROOT / relative_path).is_file()]
        if missing_paths:
            report.fail(f"{label} file is missing: {', '.join(missing_paths)}")
            continue
        text = "\n".join(_read_text(REPO_ROOT / relative_path) for relative_path in paths)
        missing = [needle for needle in needles if needle not in text]
        if missing:
            report.fail(f"{label} is missing legal acceptance marker(s): {', '.join(missing)}")


def check_release_checklist_blockers(report: GateReport, *, allow_open_release_blockers: bool) -> None:
    if not RELEASE_CHECKLIST_PATH.is_file():
        report.fail(f"Release compliance checklist is missing: {_display_report_path(RELEASE_CHECKLIST_PATH)}")
        return
    open_items = [
        line.removeprefix("- [ ] ").strip()
        for line in _read_text(RELEASE_CHECKLIST_PATH).splitlines()
        if line.startswith("- [ ] ")
    ]
    if not open_items:
        report.note("Release compliance checklist has no open blockers.")
        return
    message = (
        f"Release compliance checklist has {len(open_items)} open blocker(s): "
        + " | ".join(open_items)
    )
    if allow_open_release_blockers:
        report.warn(message)
    else:
        report.fail(message)


def _decode_cloudflare_email(value: str) -> str | None:
    try:
        data = bytes.fromhex(value)
    except ValueError:
        return None
    if not data:
        return None
    key = data[0]
    try:
        return bytes(byte ^ key for byte in data[1:]).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _hosted_page_contains_marker(text: str, marker: str) -> bool:
    if marker in text:
        return True
    if "@" not in marker:
        return False
    for match in re.finditer(r'data-cfemail=["\']([0-9a-fA-F]+)["\']', text):
        if _decode_cloudflare_email(match.group(1)) == marker:
            return True
    return False


def _urlopen_with_context(request: Request, *, timeout_seconds: float, context: ssl.SSLContext):
    try:
        return urlopen(request, timeout=timeout_seconds, context=context)
    except TypeError:
        return urlopen(request, timeout=timeout_seconds)


def check_hosted_legal_pages(report: GateReport, base_url: str, *, timeout_seconds: float = 15.0) -> None:
    base = base_url.rstrip("/")
    ssl_context = ssl.create_default_context(cafile=certifi.where() if certifi else None)
    for label, path, markers in HOSTED_LEGAL_PAGES:
        url = f"{base}{path}"
        try:
            request = Request(url, headers={"User-Agent": "AutoYouReleaseLegalGate/1.0"})
            with _urlopen_with_context(request, timeout_seconds=timeout_seconds, context=ssl_context) as response:
                status = getattr(response, "status", response.getcode())
                text = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            report.fail(f"Hosted {label} page returned HTTP {exc.code}: {url}")
            continue
        except URLError as exc:
            report.fail(f"Hosted {label} page could not be reached: {url} ({exc.reason})")
            continue

        if status < 200 or status >= 300:
            report.fail(f"Hosted {label} page returned HTTP {status}: {url}")
            continue
        missing = [marker for marker in markers if not _hosted_page_contains_marker(text, marker)]
        if missing:
            report.fail(f"Hosted {label} page is missing legal marker(s): {', '.join(missing)} ({url})")
        else:
            report.note(f"Hosted {label} page is reachable and contains required legal markers: {url}")


def _expected_packaged_release_profiles(
    bundle_dir: Path,
    expected_profile: Any,
    report: GateReport,
    label: str,
) -> tuple[str, ...]:
    metadata_file = bundle_dir.parent / "release-profile.json"
    if metadata_file.is_file():
        try:
            metadata = json.loads(_read_text(metadata_file))
        except json.JSONDecodeError as exc:
            report.fail(f"{label} release-profile.json is not valid JSON: {exc}")
            return ()
        release_profile = str(metadata.get("releaseProfile", "")).strip()
        if release_profile:
            return (release_profile,)

    if isinstance(expected_profile, (list, tuple, set)):
        return tuple(str(profile) for profile in expected_profile)
    return (str(expected_profile),)


def check_packaged_legal_bundles(report: GateReport) -> None:
    for label, relative_dir, expected_profile in PACKAGED_LEGAL_BUNDLES:
        bundle_dir = REPO_ROOT / relative_dir
        if not bundle_dir.exists():
            if bundle_dir.parent.exists():
                report.fail(f"{label} is missing legal bundle directory: {relative_dir}")
            continue
        if not bundle_dir.is_dir():
            report.fail(f"{label} exists but is not a directory: {relative_dir}")
            continue
        for filename in LEGAL_BUNDLE_FILES:
            path = bundle_dir / filename
            if not path.is_file():
                report.fail(f"{label} is missing {filename}.")
        license_file = bundle_dir / "LICENSE"
        if license_file.is_file():
            license_text = _read_text(license_file)
            if "AutoYou Source-Available Personal-Use License" not in license_text:
                report.fail(f"{label} LICENSE is not the AutoYou source-available license.")
            if "User Responsibility and Lawful Operation" not in license_text:
                report.fail(f"{label} LICENSE is missing the user responsibility covenant.")
        notice_file = bundle_dir / "NOTICE.txt"
        if notice_file.is_file() and "Third-Party Components" not in _read_text(notice_file):
            report.fail(f"{label} NOTICE.txt is missing third-party component notices.")
        sbom_file = bundle_dir / "sbom.cdx.json"
        if sbom_file.is_file():
            try:
                sbom_data = json.loads(_read_text(sbom_file))
            except json.JSONDecodeError as exc:
                report.fail(f"{label} SBOM is not valid JSON: {exc}")
                continue
            if sbom_data.get("bomFormat") != "CycloneDX":
                report.fail(f"{label} SBOM is not a CycloneDX BOM.")
            metadata_component = (sbom_data.get("metadata") or {}).get("component") or {}
            properties = {
                item.get("name"): item.get("value")
                for item in metadata_component.get("properties", [])
                if isinstance(item, dict)
            }
            expected_profiles = _expected_packaged_release_profiles(bundle_dir, expected_profile, report, label)
            actual_profile = properties.get("autoyou:releaseProfile")
            if expected_profiles and actual_profile not in expected_profiles:
                expected_label = (
                    repr(expected_profiles[0])
                    if len(expected_profiles) == 1
                    else "one of " + ", ".join(repr(profile) for profile in expected_profiles)
                )
                report.fail(
                    f"{label} SBOM release profile is {actual_profile!r}; "
                    f"expected {expected_label}."
                )
        report.note(f"{label} is present and contains current legal bundle files.")


def _allowed_copyleft(profile: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (normalize_name(str(item["name"])), str(item["license"]))
        for item in profile.get("allowedCopyleft", [])
    }


def _allowed_copyleft_conditions(profile: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {
        (normalize_name(str(item["name"])), str(item["license"])): str(item.get("condition", ""))
        for item in profile.get("allowedCopyleft", [])
    }


def _proprietary_reviewed(profile: dict[str, Any]) -> set[str]:
    return {normalize_name(str(item)) for item in profile.get("proprietaryReview", [])}


def _component_label(component: dict[str, Any]) -> str:
    version = f" {component['version']}" if component.get("version") else ""
    return f"{component.get('name', 'unknown')}{version}"


def _is_binary_default_profile(profile: dict[str, Any]) -> bool:
    return str(profile.get("releaseProfile", "")).strip().lower() == "binary-default"


def _is_compiled_release_profile(profile: dict[str, Any]) -> bool:
    return str(profile.get("releaseProfile", "")).strip().lower() in {
        "binary-default",
        "connector-full",
    }


def _profile_source_paths(profile: dict[str, Any]) -> set[str]:
    return {str(source.get("path", "")).replace("\\", "/") for source in profile.get("sources", [])}


def _strong_copyleft_is_nonlinked(component: dict[str, Any], allowed_condition: str) -> bool:
    boundary = str(component.get("distributionBoundary", "")).strip().lower()
    condition = allowed_condition.strip().lower()
    if boundary in {"build-tool", "downloaded-at-runtime", "external-service", "separate-service", "user-opened web page"}:
        return True
    return "not linked" in condition or "separate" in condition


def check_dependency_policies(
    config: dict[str, Any],
    report: GateReport,
    *,
    strict_unknown_license: bool,
    artifact_scope: str = "server",
) -> None:
    policy = config["policy"]
    strong_copyleft = set(policy.get("strongCopyleftLicenses", []))
    review_copyleft = set(policy.get("reviewCopyleftLicenses", []))
    commercial_blocking = set(policy.get("commercialBlockingLicenses", []))
    proprietary = set(policy.get("proprietaryLicenses", []))
    forbidden = {normalize_name(item["name"]): item for item in policy.get("forbiddenPackages", [])}
    binary_default_forbidden = {
        normalize_name(item["name"]): item
        for item in policy.get("binaryDefaultForbiddenPackages", [])
    }

    for profile in _artifact_profiles_for_scope(config, artifact_scope):
        components = collect_components_for_artifact(profile, config)
        allowed = _allowed_copyleft(profile)
        allowed_conditions = _allowed_copyleft_conditions(profile)
        reviewed = _proprietary_reviewed(profile)
        binary_default = _is_binary_default_profile(profile)
        compiled_release = _is_compiled_release_profile(profile)

        if binary_default and "requirements/research.txt" in _profile_source_paths(profile):
            report.fail(f"{profile['id']} is a binary-default artifact but includes requirements/research.txt.")

        for component in components:
            label = _component_label(component)
            normalized = normalize_name(component.get("name", ""))
            license_name = component.get("license")

            if normalized in forbidden:
                reason = forbidden[normalized].get("reason", "Forbidden package.")
                report.fail(f"{profile['id']} includes forbidden dependency {label}: {reason}")

            if binary_default and normalized in binary_default_forbidden:
                reason = binary_default_forbidden[normalized].get("reason", "Not approved for binary-default artifacts.")
                report.fail(f"{profile['id']} includes binary-default excluded dependency {label}: {reason}")

            if not license_name:
                message = f"{profile['id']} dependency {label} has unknown license metadata."
                if strict_unknown_license:
                    report.fail(message)
                else:
                    report.warn(message)
                continue

            if license_name in strong_copyleft and (normalized, license_name) not in allowed:
                report.fail(f"{profile['id']} includes unapproved strong copyleft dependency {label} ({license_name}).")
            elif license_name in strong_copyleft:
                condition = allowed_conditions.get((normalized, license_name), "")
                if compiled_release and not _strong_copyleft_is_nonlinked(component, condition):
                    report.fail(
                        f"{profile['id']} allows strong copyleft dependency {label} ({license_name}) "
                        "without a separate/non-linked distribution boundary for compiled binaries."
                    )
                report.note(f"{profile['id']} includes approved separate-boundary copyleft dependency {label} ({license_name}).")

            if license_name in commercial_blocking:
                report.fail(
                    f"{profile['id']} includes commercial-blocking dependency/data {label} ({license_name}); "
                    "exclude it from commercial bundles, replace it, or obtain compatible commercial rights."
                )

            if license_name in review_copyleft and (normalized, license_name) not in allowed:
                report.fail(f"{profile['id']} includes manual-review copyleft dependency {label} ({license_name}) without an explicit release condition.")
            elif license_name in review_copyleft:
                report.note(f"{profile['id']} includes approved manual-review copyleft dependency {label} ({license_name}).")

            if license_name in proprietary and normalized not in reviewed:
                report.fail(f"{profile['id']} includes proprietary/vendor dependency {label} ({license_name}) without proprietaryReview acknowledgement.")
            elif license_name in proprietary:
                report.note(f"{profile['id']} includes acknowledged vendor-terms dependency {label} ({license_name}); counsel/current terms review remains a release sign-off item.")


def check_noncommercial_assets(config: dict[str, Any], report: GateReport) -> None:
    for rule in config["policy"].get("nonCommercialAssetScans", []):
        suffixes = {str(suffix).lower() for suffix in rule.get("suffixes", [])}
        expected_path = "/".join(str(part).lower() for part in rule.get("pathParts", []))
        for root_value in rule.get("roots", []):
            root = REPO_ROOT / root_value
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if suffixes and path.suffix.lower() not in suffixes:
                    continue
                lowered = "/".join(part.lower() for part in path.parts)
                if expected_path and expected_path not in lowered:
                    continue
                report.fail(
                    f"Commercial release blocker: {rule['name']} ({rule['license']}) found at "
                    f"{_display_report_path(path)}. Prune it, replace it, or obtain compatible commercial rights."
                )


def check_generated_bundles(config: dict[str, Any], report: GateReport, *, artifact_scope: str = "server") -> None:
    generated_root = REPO_ROOT / config.get("generatedRoot", "docs/legal/generated")
    for profile in _artifact_profiles_for_scope(config, artifact_scope):
        artifact_dir = generated_root / profile["id"]
        sbom = artifact_dir / "sbom.cdx.json"
        notice = artifact_dir / "NOTICE.txt"
        if not sbom.is_file():
            report.fail(f"Missing generated SBOM for {profile['id']}: {_display_report_path(sbom)}")
        if not notice.is_file():
            report.fail(f"Missing generated NOTICE for {profile['id']}: {_display_report_path(notice)}")
        notice_text = _read_text(notice) if notice.is_file() else None
        if notice_text is not None:
            if "Third-Party Components" not in notice_text:
                report.fail(f"Generated NOTICE for {profile['id']} is missing the third-party component section.")
            for item in profile.get("allowedCopyleft", []):
                if item["name"] not in notice_text or item["license"] not in notice_text:
                    report.fail(f"Generated NOTICE for {profile['id']} is missing copyleft note for {item['name']}.")
        if sbom.is_file():
            try:
                sbom_data = json.loads(_read_text(sbom))
            except json.JSONDecodeError as exc:
                report.fail(f"Generated SBOM for {profile['id']} is not valid JSON: {exc}")
                continue
            if not isinstance(sbom_data, dict):
                report.fail(f"Generated SBOM for {profile['id']} is not a JSON object.")
                continue
            if sbom_data.get("bomFormat") != "CycloneDX":
                report.fail(f"Generated SBOM for {profile['id']} is not a CycloneDX BOM.")
            metadata = sbom_data.get("metadata")
            if not isinstance(metadata, dict):
                report.fail(f"Generated SBOM for {profile['id']} is missing metadata.")
                continue
            metadata_component = metadata.get("component") or {}
            if not isinstance(metadata_component, dict):
                report.fail(f"Generated SBOM for {profile['id']} has invalid component metadata.")
                continue
            profile_properties = {
                item.get("name"): item.get("value")
                for item in metadata_component.get("properties", [])
                if isinstance(item, dict)
            }
            if profile.get("releaseProfile") and profile_properties.get("autoyou:releaseProfile") != profile.get("releaseProfile"):
                report.fail(f"Generated SBOM for {profile['id']} is missing matching release profile metadata.")
            if profile.get("dependencyProfile") and profile_properties.get("autoyou:dependencyProfile") != profile.get("dependencyProfile"):
                report.fail(f"Generated SBOM for {profile['id']} is missing matching dependency profile metadata.")
            timestamp = metadata.get("timestamp")
            if not isinstance(timestamp, str) or not timestamp:
                report.fail(f"Generated SBOM for {profile['id']} is missing its generation timestamp.")
                continue
            components = collect_components_for_artifact(profile, config)
            expected_sbom = build_sbom(profile, components, timestamp=timestamp)
            expected_sbom["serialNumber"] = sbom_data.get("serialNumber")
            if sbom_data != expected_sbom:
                report.fail(
                    f"Generated SBOM for {profile['id']} is stale; "
                    "run python scripts/generate_release_legal_artifacts.py."
                )
            if notice_text is not None and notice_text != build_notice(profile, components, timestamp=timestamp):
                report.fail(
                    f"Generated NOTICE for {profile['id']} is stale; "
                    "run python scripts/generate_release_legal_artifacts.py."
                )


def check_packaging_hooks(report: GateReport) -> None:
    hooks = [
        (
            "AutoYou Server Windows legal bundle packaging",
            REPO_ROOT / "servers" / "windows" / "publish-desktop.ps1",
            ("copy_release_legal_artifacts.py", "check_release_legal_gates.py", "artifact-scope", "server", "check_official_build_authorization.py", "--required", "strict-unknown-license", "autoyou-server-windows-default"),
        ),
        (
            "AutoYou Server Windows backend artifact legal packaging",
            REPO_ROOT / "servers" / "windows" / "build-backend.ps1",
            ("copy_release_legal_artifacts.py", "Resolve-BackendLegalArtifactId", "autoyou-server-windows-default", "autoyou-server-windows-connector-full"),
        ),
        (
            "AutoYou Server Windows release archive legal validator",
            REPO_ROOT / "servers" / "windows" / "package-release.ps1",
            ("Assert-LegalBundle", "check_release_legal_gates.py", "artifact-scope", "server", "check_official_build_authorization.py", "--required", "strict-unknown-license", "THIRD-PARTY-NOTICES.md"),
        ),
        (
            "AutoYou Server Windows MSIX legal validator",
            REPO_ROOT / "servers" / "windows" / "package-msix.ps1",
            ("Assert-LegalBundle", "check_release_legal_gates.py", "artifact-scope", "server", "check_official_build_authorization.py", "--required", "strict-unknown-license", "THIRD-PARTY-NOTICES.md"),
        ),
        (
            "AutoYou Server Windows MSIX post-package verifier",
            REPO_ROOT / "scripts" / "verify_windows_server_msix.py",
            ("LEGAL_FILES", "runFullTrust", "privateNetworkClientServer", "mutable runtime state", "debug symbol file"),
        ),
        (
            "AutoYou Server Windows Authenticode legal gate",
            REPO_ROOT / "servers" / "windows" / "sign-backend.ps1",
            ("Invoke-StrictReleaseLegalGate", "Assert-ReleaseLegalBundle", "check_release_legal_gates.py", "artifact-scope", "server", "strict-unknown-license", "THIRD-PARTY-NOTICES.md"),
        ),
        (
            "AutoYou Server macOS legal bundle packaging",
            REPO_ROOT / "servers" / "macos" / "build-all.sh",
            ("copy_release_legal_artifacts.py", "check_release_legal_gates.py", "artifact-scope", "server", "check_official_build_authorization.py", "--required", "strict-unknown-license", "autoyou-server-macos-default"),
        ),
        (
            "AutoYou Server macOS backend release legal gate",
            REPO_ROOT / "servers" / "macos" / "build-backend.sh",
            ("BUILD_TYPE", "check_release_legal_gates.py", "artifact-scope", "server", "check_official_build_authorization.py", "--required", "strict-unknown-license"),
        ),
        (
            "AutoYou Server macOS signing package legal validator",
            REPO_ROOT / "servers" / "macos" / "sign-and-compress.sh",
            ("run_strict_release_legal_gate", "assert_release_legal_bundle", "check_release_legal_gates.py", "artifact-scope", "server", "check_official_build_authorization.py", "--required", "AutoYou.app/Contents/Resources/Legal/LICENSE", "Terms of Use (EULA)", "warranty disclaimer", "liability limits", "THIRD-PARTY-NOTICES.md", "sbom.cdx.json"),
        ),
        (
            "AutoYou Server macOS notarization legal gate",
            REPO_ROOT / "servers" / "macos" / "notarize.sh",
            ("run_strict_release_legal_gate", "check_release_legal_gates.py", "artifact-scope", "server", "check_official_build_authorization.py", "--required", "strict-unknown-license", "notarytool"),
        ),
        (
            "AutoYou Server macOS Intel wrapper legal validator",
            REPO_ROOT / "servers" / "macos" / "intel" / "build-all.sh",
            ("check_release_legal_gates.py", "artifact-scope", "server", "strict-unknown-license", "Contents/Resources/Legal", "THIRD-PARTY-NOTICES.md", "sbom.cdx.json"),
        ),
        (
            "AutoYou Server macOS Intel fallback legal packaging",
            REPO_ROOT / "servers" / "macos" / "intel" / "create-fallback-app.sh",
            ("copy_release_legal_artifacts.py", "check_release_legal_gates.py", "artifact-scope", "server", "strict-unknown-license", "autoyou-server-macos-default"),
        ),
        (
            "AutoYou Server WSL legal bundle packaging",
            REPO_ROOT / "servers" / "wsl" / "build-backend.sh",
            ("copy_release_legal_artifacts.py", "check_release_legal_gates.py", "artifact-scope", "server", "check_official_build_authorization.py", "--required", "strict-unknown-license", "autoyou-server-source-full"),
        ),
    ]

    for label, path, needles in hooks:
        if not path.is_file():
            report.fail(f"{label} script is missing: {_display_report_path(path)}")
            continue
        text = _read_text(path)
        missing = [needle for needle in needles if needle not in text]
        if missing:
            report.fail(f"{label} is missing expected legal hook marker(s): {', '.join(missing)}")
        else:
            report.note(f"{label} is wired.")


def run_gates(
    config: dict[str, Any],
    *,
    generate: bool,
    strict_unknown_license: bool,
    allow_open_release_blockers: bool,
    artifact_scope: str = "server",
    hosted_legal_base_url: str | None = None,
) -> GateReport:
    report = GateReport()
    scoped_config = _config_for_scope(config, artifact_scope)
    if generate:
        summary = generate_artifacts(scoped_config)
        report.note(f"Generated {len(summary['artifacts'])} SBOM/NOTICE bundle(s).")
    check_legacy_legal_wording(report)
    check_runtime_acceptance_markers(report)
    check_release_checklist_blockers(report, allow_open_release_blockers=allow_open_release_blockers)
    if hosted_legal_base_url:
        check_hosted_legal_pages(report, hosted_legal_base_url)
    check_dependency_policies(scoped_config, report, strict_unknown_license=strict_unknown_license, artifact_scope=artifact_scope)
    check_noncommercial_assets(scoped_config, report)
    check_generated_bundles(scoped_config, report, artifact_scope=artifact_scope)
    check_packaging_hooks(report)
    check_packaged_legal_bundles(report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AutoYou Server release legal gates.")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "docs" / "legal" / "release-artifacts.json")
    parser.add_argument("--artifact-scope", choices=ARTIFACT_SCOPES, default="server", help="Limit checks to one publishable artifact family.")
    parser.add_argument("--generate", dest="generate", action="store_true", help="Refresh generated SBOM/NOTICE bundles before checking.")
    parser.add_argument("--no-generate", dest="generate", action="store_false", help="Check existing SBOM/NOTICE bundles without writing them.")
    parser.set_defaults(generate=False)
    parser.add_argument("--strict-unknown-license", action="store_true", help="Fail on unknown license metadata instead of warning.")
    parser.add_argument(
        "--allow-open-release-blockers",
        action="store_true",
        help="Warn instead of failing when the release compliance checklist still has open blocker items.",
    )
    parser.add_argument("--hosted-legal-base-url", help="Fetch hosted Privacy/Terms/License/Subscription/Support pages and require 2xx plus legal markers.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    report = run_gates(
        config,
        generate=args.generate,
        strict_unknown_license=args.strict_unknown_license,
        allow_open_release_blockers=args.allow_open_release_blockers,
        artifact_scope=args.artifact_scope,
        hosted_legal_base_url=args.hosted_legal_base_url,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for message in report.info:
        print(f"INFO: {message}")
    for message in report.warnings:
        print(f"WARN: {message}")
    for message in report.failures:
        print(f"FAIL: {message}")
    print(f"Release legal gates {'passed' if report.ok else 'failed'}; report: {_display_report_path(args.report)}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
