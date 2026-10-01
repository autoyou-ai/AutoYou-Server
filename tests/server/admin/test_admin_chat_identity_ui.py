# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Chat & History presents the owner with their profile, not a history key."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

from tests.support.paths import REPO_ROOT


def _script() -> str:
    return (REPO_ROOT / "assets" / "admin-ui.js").read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_self_card_shows_the_owner_profile_instead_of_the_history_key():
    script = _script()
    renderer = _between(script, "function renderChatHistoryScreen()", "async function sendChatTurn()")
    card = _between(script, "function chatSelfCardMarkup()", "function chatKindForMime(")

    assert "Active UserID" not in script
    assert ".slice(-2).toUpperCase()" not in renderer
    assert "chatSelfCardMarkup()" in renderer
    assert "self.userId" in card and "title=" in card  # the key survives only as a tooltip
    assert "self.surfaceLabel" in card and "self.deviceName" in card


def test_owner_photo_resolves_through_the_secure_remote_web_prefix():
    script = _script()
    face = _between(script, "function chatFaceMarkup(identity, size)", "function chatSelfCardMarkup()")
    sidebar = _between(script, "function renderSidebar()", "function renderMobileBar()")

    assert "adminAssetUrl(self.avatarUrl)" in face
    assert "escapeHtml(adminAssetUrl(avatarUrl))" in sidebar


def test_turns_are_attributed_to_the_owner_or_the_conversations_counterpart():
    script = _script()
    message = _between(script, "function chatMessageMarkup(message)", "function chatThreadIdentity()")
    send = _between(script, "async function sendChatTurn()", "async function refreshTelegramSenders")

    assert 'counterpart.is_self || item.author === "self"' in message
    assert '(mine ? "You" : counterpart.name)' in message
    assert 'author: "self"' in send


def test_every_identity_kind_has_a_face():
    styles = (REPO_ROOT / "assets" / "admin-ui.css").read_text(encoding="utf-8")

    assert ".ayu-chat-face.is-self" in styles
    for kind in ("messaging", "connector", "peer", "room", "guest", "external"):
        assert f".ayu-chat-face.kind-{kind}" in styles
