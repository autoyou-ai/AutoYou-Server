# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-fe117641464db857e0160adf


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import asyncio

import server
from server import _dashboard_html

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-fe117641464db857e0160adf"


def test_agent_studio_replaces_legacy_agent_management_panel():
    html = asyncio.run(_dashboard_html())

    assert "agent-studio-card" in html
    assert "agentStudioActionButton" in html
    assert "refreshAgentStudio(false)" in html
    assert "id='agent-management-card'" not in html
    assert "refreshAgentList(" not in html


def test_dashboard_shell_uses_help_section_and_hides_completed_wizard_launcher():
    html = asyncio.run(_dashboard_html(show_onboarding_wizard=False))

    assert "Desktop and Remote Ready" not in html
    assert "Build and Setup Docs" not in html
    assert "Jump between settings, keep the current section highlighted" not in html
    assert "id='wizard-launcher-card'" not in html
    assert "help-guides-card" in html
    assert "data-admin-section-link='help-guides-card'" in html
    assert "System FAQ" in html
    assert "admin-sidebar-toggle" not in html
    assert "id='admin-mobile-sections-toggle'" in html
    assert "id='admin-mobile-sections-sheet'" in html
    assert "aria-controls='admin-mobile-sections-sheet'" in html
    assert "/guides/windows-build" not in html
    assert "/guides/macos-build" not in html


def test_legacy_dashboard_hides_pairing_protocol_copy():
    html = asyncio.run(_dashboard_html())

    assert "Random OTP from /pair" not in html
    assert "Secure Mode (encrypt" not in html
    assert "SHA256(TOTP:Password)" not in html
    assert "encrypts the payloads of" not in html
    assert "password:2FA_Code" not in html
    assert "/request_autopair" not in html
    assert "rails.push('SSE')" not in html
    assert "rails.push('APNs " not in html
    assert "rails.push('FCM " not in html
    assert "Random pairing code" in html
    assert "live cloud link" in html


def test_model_library_uses_installed_models_section_without_old_side_panel():
    html = asyncio.run(_dashboard_html(show_onboarding_wizard=True))
    # from __debug_provenance_v__ import wallet

    assert "model-installed-shell" in html
    assert "Installed in Ollama" in html
    assert "Select a single result to open its download choices directly beneath that card." in html
    assert "<aside class='model-library-sidepanel" not in html


def test_dashboard_agent_studio_uses_separate_uis_not_inline_workbench():
    html = asyncio.run(_dashboard_html())

    # Runtime strip and draft stats are still present
    assert "agent-studio-runtime-note" in html
    assert "agent-studio-stat-drafts" in html
    assert "Create Workspace Draft" in html

    # Inline workbench panels and pill have been removed
    assert "agent-studio-workbench-pill" not in html
    assert "agent-workbench-builder-panel" not in html
    assert "agent-workbench-coding-panel" not in html
    assert "agent-workbench-frontend-panel" not in html

    # Navigation links to the standalone agent UIs are present
    assert "/agent/agent_builder_agent/" in html
    assert "/agent/website_agent/" in html
    assert "/agent/tasks_agent/" in html
    assert "/agent/notify_agent/" in html
    assert "/agent/reminder_agent/" not in html

    # selectAgentStudioTab has been removed
    assert "selectAgentStudioTab" not in html


def test_dashboard_renderer_does_not_embed_current_password(monkeypatch):
    monkeypatch.setattr(server, "get_current_password", lambda: "never-render-this")

    html = asyncio.run(_dashboard_html())

    assert "never-render-this" not in html
    assert "Current Password:" not in html


def test_admin_guide_registry_exposes_native_docs_and_resolves_content():
    links = server._admin_guide_links()
    ids = {link["id"] for link in links}

    # Native core docs plus the existing setup/connectivity guides are unified.
    assert {"architecture", "agents", "security-modes", "connectivity", "bootstrap"} <= ids
    for link in links:
        assert link["title"] and link["href"] and link["category"]

    doc = server._resolve_guide_doc("architecture")
    assert doc is not None
    assert doc["title"] and "guide-shell" in doc["html"]
    assert "autoyou.me" not in doc["html"].lower()

    # Markdown-backed guides still resolve through the same registry.
    bootstrap_doc = server._resolve_guide_doc("bootstrap")
    assert bootstrap_doc is not None and bootstrap_doc["html"]

    # The connectivity doc keeps the public-link wording user-facing.
    connectivity_doc = server._resolve_guide_doc("connectivity")
    assert connectivity_doc is not None
    assert "Public Link" in connectivity_doc["html"]

    assert server._resolve_guide_doc("does-not-exist") is None
