# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-972ecd0770a3a1f9789848e3

"""Server attribution for guests relayed through an authenticated root client."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-972ecd0770a3a1f9789848e3"


ensure_repo_on_path()

from shared.session_execution import SessionExecutionManager


def _message(path=None):
    metadata = {"relay_path": path} if path is not None else {}
    return SimpleNamespace(
        header=SimpleNamespace(session_id="relay-session", message_id="m1"),
        payload={"message": "hello", "metadata": metadata},
    )


def _path(*peer_ids):
    return [
        {"peer_id": peer_id, "name": f"Peer {hop}", "platform": "test", "hop": hop}
        for hop, peer_id in enumerate(peer_ids, start=1)
    ]


@pytest.fixture
def web_rtc_manager_class():
    from core_server.webrtc_engine import WebRTCManager

    return WebRTCManager


class TestRelayRouteValidation:
    def test_reads_true_origin_from_contiguous_route(self, web_rtc_manager_class):
        assert web_rtc_manager_class._relay_origin_sender_id(_message(_path("c2"))) == "c2"

    def test_legacy_claim_without_route_is_rejected(self, web_rtc_manager_class):
        message = _message()
        message.payload["metadata"]["relay_origin"] = {"peer_id": "spoofed"}
        assert web_rtc_manager_class._relay_origin_sender_id(message) == ""

    @pytest.mark.parametrize(
        "path",
        [
            "c2",
            [],
            [{"peer_id": "c2", "hop": 2}],
            [{"peer_id": "", "hop": 1}],
            _path(*[f"c{index}" for index in range(33)]),
            _path("c2", "c3"),
        ],
    )
    def test_malformed_routes_are_rejected(self, web_rtc_manager_class, path):
        assert web_rtc_manager_class._relay_origin_sender_id(_message(path)) == ""


class TestRelayIdentityScoping:
    def test_same_root_and_route_is_stable(self, web_rtc_manager_class):
        message = _message(_path("c2"))
        first = web_rtc_manager_class._relay_identity_key(message, "root-session")
        second = web_rtc_manager_class._relay_identity_key(message, "root-session")
        assert first == second
        assert len(first) == 64

    def test_route_and_authenticated_root_both_scope_identity(self, web_rtc_manager_class):
        base = web_rtc_manager_class._relay_identity_key(_message(_path("c2", "c3")), "root-a")
        assert base != web_rtc_manager_class._relay_identity_key(_message(_path("c3")), "root-a")
        assert base != web_rtc_manager_class._relay_identity_key(_message(_path("c2")), "root-b")

    def test_missing_route_or_root_has_no_relay_identity(self, web_rtc_manager_class):
        assert web_rtc_manager_class._relay_identity_key(_message(), "root") == ""
        assert web_rtc_manager_class._relay_identity_key(_message(_path("c2")), "") == ""


class TestRelayedGuestOwnsItsOwnConversation:
    @pytest.fixture
    def manager(self):
        return SessionExecutionManager()

    def test_guest_and_relay_get_different_owners(self, manager, web_rtc_manager_class):
        relay = manager.resolve_webrtc_identity("root-session")
        key = web_rtc_manager_class._relay_identity_key(_message(_path("c2")), "root-session")
        guest = manager.resolve_transport_identity("peer", key)
        assert guest.owner_key != relay.owner_key
        assert guest.canonical_session_id != relay.canonical_session_id

    def test_two_guests_on_one_relay_stay_separate(self, manager, web_rtc_manager_class):
        keys = [
            web_rtc_manager_class._relay_identity_key(_message(_path(peer)), "root-session")
            for peer in ("guest-1", "guest-2")
        ]
        identities = [manager.resolve_transport_identity("peer", key) for key in keys]
        assert identities[0].owner_key != identities[1].owner_key
        assert identities[0].canonical_session_id != identities[1].canonical_session_id

    def test_same_route_keeps_owner_without_hijacking_root(self, manager, web_rtc_manager_class):
        before = manager.resolve_webrtc_identity("root-session").owner_key
        key = web_rtc_manager_class._relay_identity_key(_message(_path("guest-42")), "root-session")
        # from __debug_provenance_c__ import subtask
        first = manager.resolve_transport_identity("peer", key)
        second = manager.resolve_transport_identity("peer", key)
        after = manager.resolve_webrtc_identity("root-session").owner_key
        assert first.owner_key == second.owner_key
        assert first.canonical_session_id == second.canonical_session_id
        assert before == after
