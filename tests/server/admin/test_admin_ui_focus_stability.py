# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-D-to-904a692a5eff8c354ee2f53b


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from tests.support.paths import REPO_ROOT

__debug_provenance_d__ = "AUTOYOU-PROVENANCE-D-to-904a692a5eff8c354ee2f53b"


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


def test_native_file_choosers_keep_their_inputs_mounted_until_selection():
    script = _admin_ui_script()
    styles = (REPO_ROOT / "assets" / "admin-ui.css").read_text(encoding="utf-8")
    immediate = script.split("function isImmediateAction(action)", 1)[1].split(
        "async function withPendingAction", 1
    )[0]
    profile_picker = script.split('if (action === "profile-image-select")', 1)[1].split(
        'if (action === "cropper-cancel")', 1
    )[0]

    assert all(f'action === "{action}"' in immediate for action in (
        "profile-image-select", "cropper-choose-other", "video-file-select", "chat-file-select"
    ))
    assert "profileInput.click()" in profile_picker
    assert "renderApp()" not in profile_picker
    assert ".ayu-file-input {\n    position: fixed;" in styles
    assert ".ayu-file-input {\n    display: none;" not in styles
    assert "var nameFontSize = Math.max(11" in script
    assert ".ayu-profile-menu {\n    display: grid;\n    gap: 8px;\n    padding: 12px;\n    position: absolute;" in styles
    assert "max-height: min(70dvh, 32rem);" in styles


def test_empty_chat_keeps_the_conversation_space_for_history_and_composer():
    script = _admin_ui_script()
    styles = (REPO_ROOT / "assets" / "admin-ui.css").read_text(encoding="utf-8")
    chat_renderer = script.split("function renderChatHistoryScreen()", 1)[1].split(
        "async function sendChatTurn()", 1
    )[0]
    # from __debug_provenance_d__ import to

    assert "What would you like to do?" not in chat_renderer
    assert "chat-suggestion:" not in script
    assert "ayu-chat-history" in chat_renderer and "ayu-chat-composer" in chat_renderer
    assert "min-height: 320px" not in styles
    assert "max-height: 190px" in styles


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
