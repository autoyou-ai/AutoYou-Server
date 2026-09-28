# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-cae3934bd4e0416bf00850ef


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-6e6e75616c20726576656e75-cae3934bd4e0416bf00850ef"

from tests.support.paths import REPO_ROOT


def _admin_ui_script() -> str:
    return (REPO_ROOT / "assets" / "admin-ui.js").read_text(encoding="utf-8")


def test_admin_ui_renderer_preserves_active_form_controls():
    script = _admin_ui_script()

    assert "function captureActiveControl()" in script
    assert "function restoreActiveControl(snapshot)" in script
    assert "restoreActiveControl(activeSnapshot);" in script
    assert "if (options.passive && !options.force && hasActiveAdminControl())" in script
    assert "renderState.passiveQueued = true;" in script
    assert 'document.addEventListener("focusout"' in script
    assert 'document.addEventListener("compositionend"' in script


def test_admin_ui_does_not_restore_chat_draft_after_send_clears_it():
    script = _admin_ui_script()
    restore = script.split("function restoreActiveControl(snapshot)", 1)[1].split(
        "function flushPassiveRender()", 1
    )[0]

    assert 'snapshot.role === "chat-input" && state.chat.composer !== snapshot.value' in restore


def test_admin_ui_live_pollers_request_passive_renders():
    script = _admin_ui_script()

    assert "refreshModelDownloads(true)" in script
    assert "refreshSpeechDownloads(true)" in script
    assert "refreshTelegramSenders(false)" in script
    assert "renderApp({ passive: true });" in script
    assert "renderApp(silent ? { passive: true } : undefined);" in script
    assert "ensureOperationsData(false, { passive: true })" in script
    assert "ensureMediaDeviceData(false, { passive: true })" in script


def test_admin_ui_keeps_agent_restart_requirement_visible_until_restart():
    script = _admin_ui_script()

    assert "aiRestartRequired" in script
    assert "function renderAgentRestartNotice()" in script
    assert "The running chat process keeps its previous agent graph until it is restarted." in script
    assert 'state.agentWorkbench.aiRestartRequired = false;' in script
    assert 'action === "service:ai:restart"' in script
