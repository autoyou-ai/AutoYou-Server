# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Chat & History attribution: the owner, their devices, relayed guests, Lobbies."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import pytest

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from shared.chat_history_identity import (
    ADMIN_CHAT_USER_ID,
    conversation_kind,
    conversation_origin,
    describe_conversation,
    normalize_admin_surface,
    sanitize_peer_relay,
)

PEER_USER_ID = "user::peer:" + "0" * 64


@pytest.mark.parametrize(
    ("user_id", "metadata", "kind"),
    [
        (ADMIN_CHAT_USER_ID, {"client": "admin-web", "source": "admin_web"}, "self"),
        ("user::admin-web:" + ADMIN_CHAT_USER_ID, {}, "self"),
        ("user::local:Phone-1", {"client": "ios"}, "device"),
        ("user::cloud:Desk-1", {"client": "autoyou-v2-native"}, "device"),
        ("user::telegram:100000001", {"client": "telegram"}, "messaging"),
        ("autoyou-mcp", {"client": "chatgpt", "source": "autoyou-mcp"}, "connector"),
        (PEER_USER_ID, {"client": "android"}, "peer"),
        ("user::room:room-principal-1", {}, "room"),
        ("user::guest:session-1", {}, "guest"),
        ("external-app-user", {"client": "custom"}, "external"),
    ],
)
def test_kind_comes_from_the_owner_id_the_server_assigned(user_id, metadata, kind):
    assert conversation_kind(user_id, metadata) == kind


def test_a_paired_device_cannot_claim_to_be_the_owner():
    forged = {"client": "admin-web", "source": "admin_web", "admin_surface": "this_computer"}

    identity = describe_conversation("user::local:Phone-1", forged, self_name="Test Server")

    assert identity["kind"] == "device"
    assert identity["is_self"] is False


def test_an_owner_turn_does_not_take_over_a_connector_or_app_conversation():
    owner_turn = {"admin_surface": "this_computer"}

    assert conversation_kind("autoyou-mcp", dict(owner_turn, client="chatgpt", source="autoyou-mcp")) == "connector"
    assert conversation_kind("external-app-user", dict(owner_turn, client="custom")) == "external"
    assert conversation_kind("custom-admin-key", owner_turn) == "self"


def test_the_owner_is_presented_with_the_server_profile_name_and_where_they_typed():
    here = describe_conversation(ADMIN_CHAT_USER_ID, {"admin_surface": "this_computer"}, self_name="Test Server")
    remote = describe_conversation(ADMIN_CHAT_USER_ID, {"admin_surface": "secure_remote_web"}, self_name="Test Server")
    legacy = describe_conversation(ADMIN_CHAT_USER_ID, {"source": "admin_web"}, self_name="Test Server")

    assert here == {"kind": "self", "name": "Test Server", "detail": "You · This computer", "is_self": True}
    assert remote["detail"] == "You · Secure Remote Web"
    assert legacy["detail"] == "You · Admin page"


def test_relayed_guest_shows_its_name_only_when_one_was_kept():
    relay = {"platform": "android", "hop": 1, "via_user_id": "user::local:Phone-1"}
    named = dict(relay, name="Sam's Pixel")

    anonymous = describe_conversation(PEER_USER_ID, {"peer_relay": relay}, known_names={"user::local:Phone-1": "Kitchen iPhone"})
    known = describe_conversation(PEER_USER_ID, {"peer_relay": named}, known_names={"user::local:Phone-1": "Kitchen iPhone"})
    unlinked = describe_conversation(PEER_USER_ID, {"peer_relay": relay})

    assert anonymous["name"] == "Peer Relay guest"
    assert anonymous["detail"] == "Android · via Kitchen iPhone"
    assert known["name"] == "Sam's Pixel"
    assert known["detail"] == "Peer Relay · via Kitchen iPhone"
    assert unlinked["detail"] == "Android · via a Local pair device"


def test_peer_relay_claim_on_a_direct_conversation_is_ignored():
    identity = describe_conversation(
        "user::local:Phone-1",
        {"client": "ios", "peer_relay": {"name": "Somebody Else", "hop": 1}},
    )

    assert identity["kind"] == "device"
    assert identity["name"] == "iPhone / iPad"


def test_device_names_follow_what_history_kept():
    named = describe_conversation("user::local:Phone-1", {"client": "ios", "client_display_name": "Kitchen iPhone"})
    unnamed = describe_conversation("user::local:Phone-1", {"client": "ios"})

    assert (named["name"], named["detail"]) == ("Kitchen iPhone", "Local pair")
    assert (unnamed["name"], unnamed["detail"]) == ("iPhone / iPad", "Local pair")
    assert conversation_origin("user::local:Phone-1", {"client": "ios", "client_display_name": "Kitchen iPhone"}) == "Kitchen iPhone · Local pair"


def test_sanitize_peer_relay_bounds_everything_it_keeps():
    cleaned = sanitize_peer_relay({
        "name": "Sam\x07\n's   Pixel" + "x" * 300,
        "platform": "Android<script>",
        "hop": True,
        "via_user_id": 7,
        "unexpected": "dropped",
    })

    assert cleaned["name"].startswith("Sam 's Pixel") and len(cleaned["name"]) == 120
    assert cleaned["platform"] == "androidscript"
    assert "hop" not in cleaned and "via_user_id" not in cleaned and "unexpected" not in cleaned
    assert sanitize_peer_relay("not a mapping") == {}
    assert sanitize_peer_relay({"hop": 1})["hop"] == 1


def test_admin_surface_is_a_closed_set():
    assert normalize_admin_surface("Secure_Remote_Web") == "secure_remote_web"
    assert normalize_admin_surface("anything else") == ""
