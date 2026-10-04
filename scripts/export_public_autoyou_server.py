#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-8e65e7a8b7176ef7eb3c461f

"""Export the source-available AutoYou Server tree without private payloads."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-8e65e7a8b7176ef7eb3c461f"


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "build" / "public-autoyou-server-source"
SERVER_README = "docs/legal/member-server-readme.md"

PUBLIC_PREFIXES = (
    "assets/",
    "autoyou_agents/",
    "core_server/",
    "crypt/",
    "guides/",
    "installer/",
    "node/",
    "requirements/",
    "routers/",
    "scripts/",
    "servers/",
    "shared/",
    "tests/",
    "whatsapp/",
)

PRIVATE_PREFIXES = (
    "private/",
    ".agents/",
    ".claude/",
    ".codex/",
    ".gemini/",
    ".github/",
    ".llm/",
    "autoyou-core/",
    "skills/",
    "autoyou_agents/claude_desktop_agent/desktop_assets/",
    "autoyou_agents/codex_desktop_agent/desktop_assets/",
    "autoyou_agents/cloudflare_agent/",
    "autoyou_agents/ionos_agent/",
    "autoyou_agents/ionos_cloudflare_agent/",
    "autoyou_agents/mail_agent/",
    "autoyou_agents/private/",
    "autoyou_agents/robinhood_agent/",
    "autoyou_agents/streaming_agent/",
    "autoyou_agents/trading_agent/",
    "autoyou-outreach/",
    "autoyou-website/",
    "autoyou_lite/",
    "aws-checkout/",
    "clients/",
    "openclaw/",
    "patches/",
    "pinokio/",
    "reference/",
    "references/",
    "research/",
    "scratch/",
    "scripts/e2e/",
    "tools/",
    "tuning/",
    "v2/",
    "servers/macos/apple_lite/",
)

PRIVATE_TEST_PREFIXES = (
    "tests/legal/",
    "tests/agents/private/",
    "tests/clients/",
    "tests/e2e/",
    "tests/private_cloud/",
    "tests/support_training/",
    "tests/tools/",
    "tests/training/",
)

PUBLIC_GITHUB_PATHS = {
    ".github/FUNDING.yml",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/workflows/codeql.yml",
    ".github/workflows/public-checks.yml",
    ".github/workflows/dependency-advisories.yml",
    ".github/workflows/macos-audio-capture.yml",
}

PUBLIC_VENDOR_PATHS = {
    "vendor/INSTRUCTIONS.md",
}

# Reviewed EmotiVoice inference subset (Apache-2.0); see THIRD-PARTY-NOTICES.md.
PUBLIC_VENDOR_PREFIXES = (
    "vendor/emotivoice/",
)

PUBLIC_CONFIG_PATHS = {"config/donations.example.json"}

# Publish the generic schema, templates, and user-facing setup instructions.
# Generated manifests and app captures belong to each user's private data only.
PUBLIC_DESKTOP_ASSET_PATHS = {
    "autoyou_agents/shared_tools/desktop_asset_schema.json",
    "autoyou_agents/claude_desktop_agent/desktop_assets/manifest.template.json",
    "autoyou_agents/claude_desktop_agent/desktop_assets/setup_prompt.md",
    "autoyou_agents/codex_desktop_agent/desktop_assets/manifest.template.json",
    "autoyou_agents/codex_desktop_agent/desktop_assets/setup_prompt.md",
}

PUBLIC_SKILL_PATHS = {".agents/skills/autoyou-server-validate/SKILL.md"}

PUBLIC_GENERATED_LEGAL_PREFIXES = (
    "docs/legal/generated/autoyou-server-source-full/",
    "docs/legal/generated/autoyou-server-windows-default/",
    "docs/legal/generated/autoyou-server-windows-connector-full/",
    "docs/legal/generated/autoyou-server-macos-default/",
    "docs/legal/generated/autoyou-server-macos-connector-full/",
)

PUBLIC_GENERATED_LEGAL_PATHS = {
    "docs/legal/generated/README.md",
    "docs/legal/generated/manifest-summary.json",
}

PUBLIC_DOC_PREFIXES = (
    "docs/images/",
    "docs/security/",
    "docs/technical/",
)

PUBLIC_DOC_EXACT_PATHS = {
    # Standalone server documentation; new paths require explicit review.
    'docs/admin-ui/agent-workbench-ui.mdx',
    'docs/admin-ui/ai-and-speech.mdx',
    'docs/admin-ui/architecture.mdx',
    'docs/admin-ui/live-view-and-media.mdx',
    'docs/admin-ui/messaging-bridges.mdx',
    'docs/admin-ui/overview-and-status.mdx',
    'docs/admin-ui/security-console.mdx',
    'docs/admin-ui/setup-and-recipes.mdx',
    'docs/agent-framework/agent-web-frontends.mdx',
    'docs/agent-framework/agent-workbench.mdx',
    'docs/agent-framework/architecture.mdx',
    'docs/agent-framework/building-agents.mdx',
    'docs/agent-framework/core-agent-engine.mdx',
    'docs/agent-framework/security-and-permissions.mdx',
    'docs/agent-framework/self-improvement-and-harness-testing.mdx',
    'docs/agent-framework/shared-tools-and-subsystems.mdx',
    'docs/agent-framework/vendor-and-memory.mdx',
    'docs/agents/ai-and-training.mdx',
    'docs/agents/coding-and-building.mdx',
    'docs/agents/communication-and-tasks.mdx',
    'docs/agents/intelligence-and-models.mdx',
    'docs/agents/media-and-creative.mdx',
    'docs/agents/monetization.mdx',
    'docs/agents/system-and-admin.mdx',
    'docs/agents/utility.mdx',
    'docs/agents/web-and-automation.mdx',
    'docs/api-reference/admin-and-system.mdx',
    'docs/api-reference/admin-app.mdx',
    'docs/api-reference/agents-and-builder.mdx',
    'docs/api-reference/ai-agent-worker.mdx',
    'docs/api-reference/auth-app.mdx',
    'docs/api-reference/chat-and-sessions.mdx',
    'docs/api-reference/cloud-and-peer.mdx',
    'docs/api-reference/mcp-server.mdx',
    'docs/api-reference/messaging-gateways.mdx',
    'docs/api-reference/models-and-speech.mdx',
    'docs/api-reference/overview.mdx',
    'docs/api-reference/pairing-and-signaling.mdx',
    'docs/api-reference/security-and-totp.mdx',
    'docs/api-reference/webrtc-and-streaming.mdx',
    'docs/api-reference/website-gateway.mdx',
    'docs/api-reference/websites-and-bookmarks.mdx',
    'docs/architecture/cloud-and-remote.mdx',
    'docs/architecture/overview.mdx',
    'docs/architecture/pairing-and-discovery.mdx',
    'docs/architecture/security-and-auth.mdx',
    'docs/architecture/webrtc-and-datachannels.mdx',
    'docs/docs.json',
    'docs/guides/custom-agent-tutorial.mdx',
    'docs/guides/custom-api-router.mdx',
    'docs/guides/mcp-integration.mdx',
    'docs/guides/production-deployment.mdx',
    'docs/index.mdx',
    'docs/mint.json',
    'docs/quickstart.mdx',
    "docs/glossary.md",
    "docs/index.md",
    "docs/legal/release-artifacts.json",
    "docs/legal/release-model.md",
    "docs/legal/security-contact.md",
    "docs/legal/messaging-partner-policy.md",
    "docs/legal/open-source-commitment.md",
    "docs/legal/optional-integrations.md",
    "docs/legal/source-publication-manifest.md",
    "docs/legal/third-party-attributions.md",
    "docs/legal/trademark-policy.md",
    "docs/local-intent-routing.md",
}

PUBLIC_CONTRIBUTOR_GUIDE_PATHS = {
    "docs/contributors/AGENTS.md",
    "docs/contributors/CLA.md",
    "docs/contributors/CLAUDE.md",
    "docs/contributors/CODEX.md",
    "docs/contributors/LLM.txt",
    "docs/contributors/contributor-pool.md",
    "docs/contributors/README.md",
    "docs/contributors/TESTING.md",
    "docs/contributors/TESTING_SKILL.md",
    "docs/contributors/TEST_RUNNER_AGENT.md",
}

PUBLIC_RELEASE_ARTIFACTS_PATH = "docs/legal/release-artifacts.json"
SERVER_ARTIFACT_ID_PREFIX = "autoyou-server-"
PUBLIC_RELEASE_POLICY_KEYS = (
    "strongCopyleftLicenses",
    "reviewCopyleftLicenses",
    "commercialBlockingLicenses",
    "nonCommercialAssetScans",
    "proprietaryLicenses",
    "forbiddenPackages",
    "binaryDefaultForbiddenPackages",
)

PRIVATE_EXACT_PATHS = {
    "AGENTS.md",
    "Dockerfile.distributable",
    "Dockerfile.distributable.host",
    "autoyou_agents/codex_desktop_agent/desktop_assets/windows/26.611/full_window.png",
    "clients/android/app/google-services.json",
    "coding_guide.md",
    "config.encrypted",
    "config.keystore.enc",
    "docs/SAFE_DISTRIBUTION_MIGRATION.md",
    "docs/legal/release-matrix.md",
    "docs/technical/funding-os.md",
    "docs/technical/peer-relay-social-checkpoint-2026-08-24.md",
    "docs/technical/webrtc-media-mixer.md",
    "guides/PRIVATE_MESSAGING_PAIRING_GUIDE.md",
    "LICENSE_ACKNOWLEDGEMENT",
    "requirements/.locked.constraints.generated.txt",
    "requirements/autoyou-lite-release.txt",
    "requirements/research.txt",
    "scripts/audit_distribution_boundaries.py",
    "scripts/autoyou_system.py",
    "scripts/autoyou_agent_goal_hook.py",
    "scripts/autoyou_agent_goal_loop.py",
    "scripts/autoyou_lite_windows_entry.py",
    "scripts/build_autoyou_lite_windows_binary.py",
    "scripts/build_machine_failure_knowledge.json",
    "scripts/build_machine_intel.py",
    "scripts/build_protected_autoyou_lite_wheel.py",
    "scripts/check_windows_store_msix_upload_readiness.py",
    "scripts/commit_to_main.py",
    "scripts/e2e_validate.py",
    "scripts/generate_android_adaptive_icon.py",
    "scripts/install_commit_to_main_hooks.py",
    "scripts/mobile_ui_smoke.py",
    "scripts/package_docker_ecosystem.ps1",
    "scripts/package_windows_ecosystem.ps1",
    "scripts/release_readiness_eval.py",
    "scripts/release_autoyou_lite.py",
    "scripts/run_ad_reward_monthly_settlement.py",
    "scripts/run_notes_reader_mobile_test.py",
    "scripts/run_page_service_mobile_test.py",
    "scripts/sign_update_manifest.py",
    "scripts/smoke_windows_binaries.ps1",
    "scripts/sync_from_autoyou.py",
    "scripts/trigger_native_rewarded_ad.py",
    "scripts/validate_desktop_rewarded_ad_release_env.py",
    "scripts/verify_live_voice_partners.py",
    "scripts/verify_website_legal_upload_bundle.py",
    "scripts/verify_windows_store_msix_artifacts.py",
    "scripts/verify_windows_connect_release_artifacts.py",
    "installer/AutoYouConnectInstaller.iss",
    "installer/AutoYouEcosystemInstaller.iss",
    "installer/assets/installing.svg",
    "installer/assets/location.svg",
    "installer/assets/options.svg",
    "installer/assets/welcome.svg",
    "installer/bootstrap_installer.ps1",
    "installer/installer_web.py",
    "installer/run_installer.bat",
    "scripts/build_enterprise_agreement.py",
    "scripts/export_ollama_models.ps1",
    "tests/legal/test_official_build_authorization.py",
    "tests/legal/test_public_source_export.py",
    "tests/legal/test_release_legal_gates.py",
    "tests/legal/test_website_legal_upload_bundle.py",
    "tests/scripts/test_autoyou_system.py",
    "tests/scripts/test_build_machine_intel.py",
    "tests/test_autoyou_dev_coordinator.py",
    "tests/test_audit_prune.py",
    "tests/server/admin/test_ad_reward_settlement_job.py",
    "tests/server/api/test_rest_chat_attachments.py",
    "tests/server/admin/test_trigger_native_rewarded_ad_script.py",
    "tests/server/bootstrap/test_desktop_rewarded_ad_release_packaging.py",
    "tests/server/bootstrap/test_distribution_boundary_audit.py",
    "tests/server/bootstrap/test_e2e_validate_entrypoint.py",
    "tests/server/bootstrap/test_macos_signing_preflight.py",
    "tests/server/bootstrap/test_packaged_runtime_module_plan.py",
    "tests/server/bootstrap/test_windows_connect_release_artifacts.py",
    "tests/server/bootstrap/test_windows_msix_packaging.py",
    "tests/server/bootstrap/test_windows_signing_preflight.py",
    "tests/server/build/test_distributable_packaging_scripts.py",
    "tests/server/build/test_protected_autoyou_lite_wheel.py",
    "tests/server/build/test_release_autoyou_lite.py",
    "tests/server/test_autoyou_lite_windows_binary.py",
    "tests/server/build/test_wsl_client_build_script.py",
    "tests/server/messaging/test_openclaw_custom_voice_inventory.py",
    "tests/server/messaging/test_openclaw_voice_reply_gate.py",
    "tests/server/messaging/test_openclaw_webrtc_voice_note.py",
    "tests/server/messaging/test_openclaw_webrtc_voice_restart.py",
    "tests/server/pairing/test_otp_multiuse.py",
    "tests/server/pairing/test_autopair_admin.py",
    "tests/server/transport/test_tunnelmole_transport_direct.py",
    "tests/server/update/test_release_version_consistency.py",
    "tests/server/update/test_update_feed_staging.py",
    "guides/IONOS_WAITLIST_DATABASE.md",
    "installer/AutoYouEcosystemSetup.ps1",
    "installer/AutoYouLiteInstaller.iss",
    "installer/Start-AutoYouProducts.ps1",
    "installer/agent-catalog.json",
    "installer/model-catalog.json",
    "scripts/build_autoyou_lite_macos_binary.py",
    "scripts/build_windows_ecosystem_installer.ps1",
    "scripts/deploy_website_ionos.sh",
    "scripts/notarize_autoyou_lite_macos.sh",
    "scripts/sync_peer_link.py",
    "scripts/verify_autoyou_lite_windows_msix.py",
    "scripts/verify_autoyou_v2_windows_msix.py",
    "servers/windows/package-lite-msix.ps1",
    "servers/windows/package-lite-release.ps1",
    "servers/windows/publish-lite-desktop.ps1",
    "tests/agents/public/test_cloudflare_agent.py",
    "tests/agents/public/test_ionos_agents.py",
    "tests/agents/public/test_mail_agent.py",
    "tests/scripts/test_project_knowledge_governance.py",
    "tests/server/bootstrap/test_ecosystem_installer.py",
    "tests/server/bootstrap/test_pinokio_install.py",
    "tests/server/bootstrap/test_macos_build_backend_packaging.py",
    "tests/server/bootstrap/test_v2_voice_runtime_parity.py",
    "tests/server/admin/test_agent_security_routes.py",
    "tests/server/pairing/test_pairing_transport.py",
    "tests/server/runtime/test_local_authenticator_vault.py",
    "tests/server/messaging/test_signal_native.py",
    "tests/server/runtime/test_native_ai_settings.py",
    "tests/server/transport/test_server_profile_call_control.py",
    "tests/shared/test_ios_project_membership.py",
    "tests/shared/test_kotlin_peer_parity.py",
    "tests/shared/test_swift_source_integrity.py",
    "tests/shared/test_swift_symbol_resolution.py",
    "tests/shared/test_peer_rendezvous_cloud_parity.py",
    "shared/earnings_agent/pending_ad_credits.json",
}

PRIVATE_BASENAMES = {
    "llm.txt",
}

DESKTOP_CAPTURE_ASSET_PREFIXES = (
    "autoyou_agents/claude_desktop_agent/desktop_assets/",
    "autoyou_agents/codex_desktop_agent/desktop_assets/",
)
DESKTOP_CAPTURE_ASSET_SUFFIXES = (
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
)
DESKTOP_CAPTURE_ASSET_PARTS = {"captures", "images", "screenshots", "sprites", "tools"}
DESKTOP_CAPTURE_ASSET_BASENAMES = {"contributing.md", "readme.md"}

SENSITIVE_BASENAMES = {
    ".env",
    "apikeys.json",
    "googleservice-info.plist",
    "google-services.json",
    "keystore.properties",
    "release.jks",
}

SENSITIVE_SUFFIXES = (
    ".7z",
    ".aab",
    ".apk",
    ".appx",
    ".bz2",
    ".db",
    ".db-shm",
    ".db-wal",
    ".dmg",
    ".exe",
    ".gz",
    ".jks",
    ".key",
    ".msi",
    ".msix",
    ".mobileprovision",
    ".p12",
    ".p8",
    ".pem",
    ".pyo",
    ".pyc",
    ".rar",
    ".tar",
    ".tgz",
    ".tmp",
    ".whl",
    ".xz",
    ".zip",
)

RUNTIME_PATH_PARTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".wwebjs_auth",
    ".wwebjs_cache",
    "__pycache__",
    "artifacts",
    "cache",
    "caches",
    "downloads",
    "logs",
    "node_modules",
    "output",
    "uploads",
}

RUNTIME_BASENAMES = {
    "agent_frontends_registry.json",
    "agent_install_registry.json",
    "ai_agent_internal_api_token.txt",
    "config.encrypted",
    "config.keystore.enc",
    "pending_ad_credits.json",
    "server_unlock.json",
}

REVIEWED_ASSET_SUFFIXES = (
    ".avif",
    ".bmp",
    ".dll",
    ".dylib",
    ".gif",
    ".heic",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pyd",
    ".so",
    ".svg",
    ".tif",
    ".tiff",
    ".wav",
    ".webp",
)

REVIEWED_ASSET_PATHS = {
    "assets/logo-alt.ico",
    "assets/logo-alt.png",
    "assets/logo.ico",
    "assets/logo.png",
    "autoyou_agents/game_agent/website/frontend/assets/audio/stream-loop.wav",
    "autoyou_agents/page_agent/website/frontend/assets/autoyou-mark.svg",
    "docs/images/favicon.ico",
    "docs/images/admin/live-view.png",
    "docs/images/admin/overview.png",
    "docs/images/admin/security.png",
    "docs/images/logo.png",
    "installer/assets/installing.svg",
    "installer/assets/location.svg",
    "installer/assets/options.svg",
    "installer/assets/welcome.svg",
    "servers/windows/autoyouwindowshost/assets/lockscreenlogo.scale-200.png",
    "servers/windows/autoyouwindowshost/assets/logo.ico",
    "servers/windows/autoyouwindowshost/assets/splashscreen.scale-200.png",
    "servers/windows/autoyouwindowshost/assets/square150x150logo.scale-200.png",
    "servers/windows/autoyouwindowshost/assets/square44x44logo.scale-200.png",
    "servers/windows/autoyouwindowshost/assets/square44x44logo.targetsize-24_altform-unplated.png",
    "servers/windows/autoyouwindowshost/assets/storelogo.png",
    "servers/windows/autoyouwindowshost/assets/traylogo.png",
    "servers/windows/autoyouwindowshost/assets/wide310x150logo.scale-200.png",
    "shared/native/libsodium/darwin-arm64/libsodium.dylib",
    "shared/native/libsodium/windows-x86_64/libsodium.dll",
}

SENSITIVE_TEXT_PATTERNS = (
    ("private key PEM", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "Apple development team id",
        re.compile(
            r"\bDEVELOPMENT_TEAM\s*=\s*[A-Z0-9]{10}\b"
            r"|<key>com\.apple\.developer\.team-identifier</key>\s*<string>[A-Z0-9]{10}</string>",
            re.DOTALL,
        ),
    ),
    (
        "Apple provisioning profile",
        re.compile(
            r"\bPROVISIONING_PROFILE(?:_SPECIFIER)?[ \t]*=[ \t]*"
            r"(?:\"(?![\r\n\"\$])[^\r\n\"]+\"|'(?![\r\n'\$])[^\r\n']+'|[A-Za-z0-9][^\s#;\n]*)"
        ),
    ),
)

SAFE_ADMOB_PUBLISHER_IDS = {
    "0000000000000000",  # synthetic tests
    "3940256099942544",  # Google sample ads
}

ROOT_EXACT_PATHS = {
    ".dockerignore",
    ".gitattributes",
    ".gitleaks.toml",
    ".gitignore",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "THIRD-PARTY-NOTICES.md",
    "VERSION",
    "ai.txt",
    "autoyou_app.py",
    "autoyou_page_service.py",
    "coding_guide.md",
    "conftest.py",
    "docker-compose.yml",
    "ollama_service.py",
    "page_feed_db.py",
    "pairing_router.py",
    "pytest.ini",
    "requirements.txt",
    "rest_api.py",
    "robots.txt",
    "run_autoyou.bat",
    "run_autoyou.sh",
    "server.py",
    "service_manager.py",
    "session_utils.py",
    "signal_service.py",
    "telegram_user_service.py",
    "whatsapp_docker_service.py",
    "whatsapp_service.py",
}


@dataclass(frozen=True)
class GitEntry:
    path: str
    mode: str
    object_type: str
    object_id: str


def normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_sensitive_path(path: str) -> bool:
    normalized = normalize_path(path)
    lowered = normalized.lower()
    name = Path(lowered).name
    if (
        lowered in {item.lower() for item in PRIVATE_EXACT_PATHS}
        or (name in PRIVATE_BASENAMES and normalized not in PUBLIC_CONTRIBUTOR_GUIDE_PATHS)
        or name in SENSITIVE_BASENAMES
        or name in RUNTIME_BASENAMES
    ):
        return True
    parts = set(Path(lowered).parts)
    if parts & RUNTIME_PATH_PARTS or any(part.startswith(".test-runtime") for part in parts):
        return True
    if name.startswith(".env"):
        return True
    if lowered.endswith(REVIEWED_ASSET_SUFFIXES) and lowered not in REVIEWED_ASSET_PATHS:
        return True
    return lowered.endswith(SENSITIVE_SUFFIXES)


def is_private_desktop_capture_path(path: str) -> bool:
    normalized = normalize_path(path)
    prefix = next((item for item in DESKTOP_CAPTURE_ASSET_PREFIXES if normalized.startswith(item)), None)
    if prefix is None:
        return False
    relative = normalized[len(prefix):]
    name = Path(relative).name.lower()
    return (
        name in DESKTOP_CAPTURE_ASSET_BASENAMES
        or relative.lower().endswith(DESKTOP_CAPTURE_ASSET_SUFFIXES)
        or any(part.lower() in DESKTOP_CAPTURE_ASSET_PARTS for part in Path(relative).parts)
    )


def should_publish_path(path: str) -> bool:
    normalized = normalize_path(path)
    if not normalized or is_sensitive_path(normalized) or is_private_desktop_capture_path(normalized):
        return False
    if normalized in PUBLIC_DESKTOP_ASSET_PATHS:
        return True
    if normalized in PUBLIC_GITHUB_PATHS:
        return True
    if normalized in PUBLIC_VENDOR_PATHS:
        return True
    if normalized.startswith(PUBLIC_VENDOR_PREFIXES):
        return True
    if normalized in PUBLIC_CONFIG_PATHS:
        return True
    if normalized in PUBLIC_SKILL_PATHS:
        return True
    if normalized.startswith("docs/"):
        if normalized.startswith("docs/legal/generated/"):
            return (
                normalized in PUBLIC_GENERATED_LEGAL_PATHS
                or normalized.startswith(PUBLIC_GENERATED_LEGAL_PREFIXES)
            )
        return (
            normalized in PUBLIC_DOC_EXACT_PATHS
            or normalized in PUBLIC_CONTRIBUTOR_GUIDE_PATHS
            or normalized.startswith(PUBLIC_DOC_PREFIXES)
        )
    if normalized.startswith(PRIVATE_PREFIXES):
        return False
    if normalized.startswith(PRIVATE_TEST_PREFIXES):
        return False
    if normalized in ROOT_EXACT_PATHS:
        return True
    return normalized.startswith(PUBLIC_PREFIXES)


def audit_public_source_paths(paths: list[str]) -> list[str]:
    failures: list[str] = []
    for raw_path in paths:
        path = normalize_path(raw_path)
        if is_sensitive_path(path):
            failures.append(f"sensitive path included: {path}")
        if is_private_desktop_capture_path(path):
            failures.append(f"desktop capture asset included: {path}")
        if (
            path.startswith(PRIVATE_PREFIXES)
            and path not in PUBLIC_GITHUB_PATHS
            and path not in PUBLIC_SKILL_PATHS
            and path not in PUBLIC_DESKTOP_ASSET_PATHS
        ):
            failures.append(f"private path included: {path}")
        if path.startswith(PRIVATE_TEST_PREFIXES):
            failures.append(f"private test path included: {path}")
    return failures


def audit_public_repository_paths(paths: list[str]) -> list[str]:
    """Reject committed paths that do not belong in this public repository."""
    failures: list[str] = []
    for raw_path in paths:
        path = normalize_path(raw_path)
        if should_publish_path(path):
            continue
        if is_private_desktop_capture_path(path):
            reason = "desktop capture asset"
        elif is_sensitive_path(path):
            reason = "sensitive path"
        elif path.startswith(PRIVATE_PREFIXES) or path.startswith(PRIVATE_TEST_PREFIXES):
            reason = "private path"
        else:
            reason = "path outside the public allowlist"
        failures.append(f"{reason} committed to public repository: {path}")
    return failures


def public_release_artifacts_bytes(data: bytes) -> bytes:
    config = json.loads(data)
    config["artifactProfiles"] = [
        profile
        for profile in config.get("artifactProfiles", [])
        if str(profile.get("id", "")).startswith(SERVER_ARTIFACT_ID_PREFIX)
    ]
    policy = config.get("policy")
    if isinstance(policy, dict):
        config["policy"] = {key: policy[key] for key in PUBLIC_RELEASE_POLICY_KEYS if key in policy}
    return (json.dumps(config, indent=2) + "\n").encode("utf-8")


def public_blob_bytes(entry: GitEntry) -> bytes:
    data = subprocess.check_output(["git", "cat-file", "-p", entry.object_id], cwd=REPO_ROOT)
    if normalize_path(entry.path) == PUBLIC_RELEASE_ARTIFACTS_PATH and entry.mode != "120000":
        return public_release_artifacts_bytes(data)
    return data


def audit_public_content_blobs(blobs: list[tuple[str, bytes]]) -> list[str]:
    failures: list[str] = []
    for path, data in blobs:
        text = data.decode("utf-8", errors="ignore")
        for label, pattern in SENSITIVE_TEXT_PATTERNS:
            if pattern.search(text):
                failures.append(f"sensitive content included: {path} ({label})")
        for match in re.finditer(r"\bca-app-pub-(\d{16})(?:[~/]\d+)?\b", text):
            if match.group(1) not in SAFE_ADMOB_PUBLISHER_IDS:
                failures.append(f"sensitive content included: {path} (AdMob id)")
                break
    return failures


def audit_public_source_content(entries: list[GitEntry]) -> list[str]:
    return audit_public_content_blobs([(entry.path, public_blob_bytes(entry)) for entry in entries])


def git_tree_entries(ref: str) -> list[GitEntry]:
    output = subprocess.check_output(["git", "ls-tree", "-r", "-z", ref], cwd=REPO_ROOT)
    entries: list[GitEntry] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, path_bytes = record.split(b"\t", 1)
        mode, object_type, object_id = metadata.decode("ascii").split(" ", 2)
        entries.append(
            GitEntry(
                path=path_bytes.decode("utf-8", errors="surrogateescape"),
                mode=mode,
                object_type=object_type,
                object_id=object_id,
            )
        )
    return entries


def dirty_worktree_paths() -> list[str]:
    output = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=REPO_ROOT,
    )
    # from __debug_provenance_a__ import schedule
    paths: list[str] = []
    records = output.decode("utf-8", errors="surrogateescape").split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        if not record:
            index += 1
            continue
        status = record[:2]
        path = record[3:]
        if path:
            paths.append(normalize_path(path))
        if "R" in status or "C" in status:
            index += 1
            if index < len(records) and records[index]:
                paths.append(normalize_path(records[index]))
        index += 1
    return sorted(dict.fromkeys(paths))


def worktree_paths() -> list[str]:
    """Return present tracked/untracked files that a commit could include."""
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
    )
    paths: list[str] = []
    for raw_path in output.split(b"\0"):
        if not raw_path:
            continue
        path = normalize_path(raw_path.decode("utf-8", errors="surrogateescape"))
        source = REPO_ROOT / path
        if source.is_file() or source.is_symlink():
            paths.append(path)
    return sorted(dict.fromkeys(paths))


def worktree_file_bytes(path: str) -> bytes:
    source = REPO_ROOT / normalize_path(path)
    return os.fsencode(os.readlink(source)) if source.is_symlink() else source.read_bytes()


def build_worktree_check_plan() -> tuple[list[str], list[str]]:
    paths = worktree_paths()
    included = [path for path in paths if should_publish_path(path)]
    failures = audit_public_repository_paths(paths)
    blobs: list[tuple[str, bytes]] = []
    for path in included:
        try:
            blobs.append((path, worktree_file_bytes(path)))
        except OSError as exc:
            failures.append(f"unable to read worktree file: {path} ({exc})")
    failures.extend(audit_public_content_blobs(blobs))
    return included, failures


def build_export_plan(entries: list[GitEntry]) -> tuple[list[GitEntry], list[str]]:
    included = [entry for entry in entries if entry.object_type == "blob" and should_publish_path(entry.path)]
    template = next((entry for entry in entries if entry.path == SERVER_README and entry.mode == "100644"), None)
    if any(entry.path == "README.md" for entry in included):
        if template is None and any(entry.path.startswith(("clients/", ".llm/", "autoyou-core/")) for entry in entries):
            return included, ["Committed server-specific README is missing"]
        if template is not None:
            included = [replace(entry, object_id=template.object_id) if entry.path == "README.md" else entry
                        for entry in included]
    failures = audit_public_source_paths([entry.path for entry in included])
    failures.extend(audit_public_source_content(included))
    return included, failures


def export_entries(entries: list[GitEntry], output: Path, *, force: bool) -> None:
    if output.exists():
        if not force:
            raise RuntimeError(f"Output already exists: {output}. Pass --force to replace it.")
        if os.path.lexists(output / ".git"):
            raise RuntimeError(f"Refusing to replace a Git checkout: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)

    for entry in entries:
        destination = output / entry.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = public_blob_bytes(entry)
        if entry.mode == "120000":
            os.symlink(data.decode("utf-8"), destination)
            continue
        destination.write_bytes(data)
        if entry.mode == "100755":
            destination.chmod(0o755)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a sanitized public AutoYou Server source tree.")
    parser.add_argument("--ref", default="HEAD", help="Git ref to export. Defaults to HEAD, not dirty worktree files.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Validate the export plan without writing files.")
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="Validate the current nonignored worktree before committing; requires --check.",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing output directory.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    parser.add_argument(
        "--allow-dirty-source",
        action="store_true",
        help="Allow checking/exporting a committed ref while the current checkout has uncommitted changes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.worktree:
        if not args.check:
            raise SystemExit("--worktree requires --check")
        if args.ref != "HEAD":
            raise SystemExit("--worktree cannot be combined with --ref")
        included, failures = build_worktree_check_plan()
        summary = {
            "ref": "WORKTREE",
            "included_count": len(included),
            "output": str(args.output),
            "dirty_paths": [],
            "failures": failures,
        }
        if failures:
            print(json.dumps(summary, indent=2, sort_keys=True) if args.json else "\n".join(failures), file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True) if args.json else f"OK: {len(included)} files")
        return 0

    entries = git_tree_entries(args.ref)
    included, failures = build_export_plan(entries)
    dirty_paths = dirty_worktree_paths()
    if dirty_paths and not args.allow_dirty_source:
        failures.append(
            "dirty worktree: exporter reads committed Git content; commit/stash changes "
            "or pass --allow-dirty-source for a development-only check"
        )
    summary = {
        "ref": args.ref,
        "included_count": len(included),
        "output": str(args.output),
        "dirty_paths": dirty_paths,
        "failures": failures,
    }
    if failures:
        print(json.dumps(summary, indent=2, sort_keys=True) if args.json else "\n".join(failures), file=sys.stderr)
        return 1
    if not args.check:
        export_entries(included, args.output.resolve(), force=bool(args.force))
    print(json.dumps(summary, indent=2, sort_keys=True) if args.json else f"OK: {len(included)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
