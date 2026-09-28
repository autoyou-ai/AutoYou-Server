# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-7363686564756c6520796561-37d58eeedded97b2f1c4c375

"""HTTP contract for the Peer Link rendezvous, plus the full one-tap add.

These routes sit on the public pairing surface without a session, because the
person accepting an invite is a stranger until the pairing completes. Their
safety therefore has to be a property of the routes themselves, which is what
this file pins down.

The final test drives the entire product flow - inviter publishes, invitee taps
and answers, inviter collects - through the real codec, so a change that breaks
adding a contact fails here rather than on a phone.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-7363686564756c6520796561-37d58eeedded97b2f1c4c375"


import base64
import secrets
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server  # noqa: E402
from shared.peer_rendezvous import RENDEZVOUS  # noqa: E402

pytestmark = pytest.mark.server

OFFER = "/peerpair\nv3.abc.tag.body"
ANSWER = "/peerpair_answer\nv3.abc.tag.answer"


def _invitation_id() -> str:
    return secrets.token_urlsafe(32)


@pytest.fixture()
def client(monkeypatch):
    """A client for the public auth app, with the rendezvous emptied."""
    RENDEZVOUS._slots.clear()  # noqa: SLF001 - test isolation
    # The shared limiters are process-wide; reset so ordering cannot starve a
    # later test of budget.
    server.AUTH_RATE_LIMITER._requests.clear()  # noqa: SLF001
    server.AUTH_GLOBAL_RATE_LIMITER._requests.clear()  # noqa: SLF001
    with TestClient(server.auth_app) as test_client:
        yield test_client
    RENDEZVOUS._slots.clear()  # noqa: SLF001


def _open(client, invitation_id, offer=OFFER):
    return client.post(
        "/v1/peer/rendezvous/open",
        json={"invitation_id": invitation_id, "offer": offer},
    )


# --------------------------------------------------------------------------
# The exchange over HTTP
# --------------------------------------------------------------------------

def test_publish_fetch_answer_collect(client):
    iid = _invitation_id()
    assert _open(client, iid).json()["success"] is True

    offer = client.get(f"/v1/peer/rendezvous/{iid}/offer")
    assert offer.status_code == 200
    assert offer.json()["offer"] == OFFER

    assert client.get(f"/v1/peer/rendezvous/{iid}/answer").json()["state"] == "pending"

    posted = client.post(f"/v1/peer/rendezvous/{iid}/answer", json={"answer": ANSWER})
    assert posted.json()["success"] is True

    collected = client.get(f"/v1/peer/rendezvous/{iid}/answer").json()
    assert collected["state"] == "ready"
    assert collected["answer"] == ANSWER


def test_decline_reaches_the_inviter(client):
    iid = _invitation_id()
    _open(client, iid)
    client.post(f"/v1/peer/rendezvous/{iid}/answer", json={"declined": True})
    assert client.get(f"/v1/peer/rendezvous/{iid}/answer").json()["state"] == "declined"


def test_an_answer_is_collected_once(client):
    iid = _invitation_id()
    _open(client, iid)
    client.post(f"/v1/peer/rendezvous/{iid}/answer", json={"answer": ANSWER})
    assert client.get(f"/v1/peer/rendezvous/{iid}/answer").json()["state"] == "ready"
    assert client.get(f"/v1/peer/rendezvous/{iid}/answer").json()["state"] == "pending"


def test_withdrawing_an_invite_stops_it_resolving(client):
    iid = _invitation_id()
    _open(client, iid)
    assert client.post(f"/v1/peer/rendezvous/{iid}/close").json()["success"] is True
    assert client.get(f"/v1/peer/rendezvous/{iid}/offer").status_code == 404


# --------------------------------------------------------------------------
# The surface must not be an oracle
# --------------------------------------------------------------------------

def test_unknown_and_unanswered_invitations_look_identical(client):
    known = _invitation_id()
    _open(client, known)
    unknown = client.get(f"/v1/peer/rendezvous/{_invitation_id()}/answer").json()
    unanswered = client.get(f"/v1/peer/rendezvous/{known}/answer").json()
    assert unknown == unanswered == {"success": True, "state": "pending"}


def test_every_rejection_uses_one_message(client):
    """Distinguishing failure reasons to a stranger would leak slot state."""
    bodies = [
        client.get(f"/v1/peer/rendezvous/{_invitation_id()}/offer").json(),
        client.post(
            f"/v1/peer/rendezvous/{_invitation_id()}/answer", json={"answer": ANSWER}
        ).json(),
    ]
    for body in bodies:
        assert body["success"] is False
        assert body["error"] == "That invite is no longer available."


def test_a_second_answer_is_refused_over_http(client):
    iid = _invitation_id()
    _open(client, iid)
    client.post(f"/v1/peer/rendezvous/{iid}/answer", json={"answer": ANSWER})
    substituted = client.post(
        f"/v1/peer/rendezvous/{iid}/answer",
        json={"answer": "/peerpair_answer\nv3.abc.tag.attacker"},
    )
    assert substituted.json()["success"] is False
    assert client.get(f"/v1/peer/rendezvous/{iid}/answer").json()["answer"] == ANSWER


# --------------------------------------------------------------------------
# Input validation at the edge
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"invitation_id": "too-short", "offer": OFFER},
        {"invitation_id": secrets.token_urlsafe(32), "offer": ""},
        {"invitation_id": secrets.token_urlsafe(32), "offer": "no command line"},
    ],
)
def test_malformed_publishes_are_refused(client, payload):
    assert client.post("/v1/peer/rendezvous/open", json=payload).status_code == 400


def test_a_non_json_body_is_refused(client):
    response = client.post(
        "/v1/peer/rendezvous/open",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_an_oversized_body_is_refused(client):
    iid = _invitation_id()
    response = client.post(
        "/v1/peer/rendezvous/open",
        json={"invitation_id": iid, "offer": "/peerpair\n" + "a" * (512 * 1024)},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Operator diagnostics stay admin-gated and content-free
# --------------------------------------------------------------------------

def test_status_requires_admin():
    with TestClient(server.admin_app) as admin:
        assert admin.get("/api/peer/rendezvous/status").status_code == 401


def test_status_reports_counts_only():
    with TestClient(server.admin_app) as admin:
        server.ADMIN_SESSIONS["rv-test-session"] = True
        admin.cookies.set("admin_session", "rv-test-session")
        body = admin.get("/api/peer/rendezvous/status").json()
    assert body["success"] is True
    assert set(body) == {"success", "open_slots", "answered_slots", "max_slots"}


# --------------------------------------------------------------------------
# The product flow, end to end, through the real codec
# --------------------------------------------------------------------------

def test_one_tap_contact_addition_end_to_end(client):
    """Inviter publishes, invitee taps and accepts, inviter collects.

    Two people, one action each - the flow this replaced took four clipboard
    steps split across both of them.
    """
    from tests.support.paths import ensure_python_client_on_path

    ensure_python_client_on_path()
    from peer_link import rendezvous as rz  # noqa: E402
    from peer_link.codec import PeerAnswerAccepted, PeerPairCodec  # noqa: E402
    from peer_link.protocol import (  # noqa: E402
        PeerAnswerEnvelope,
        PeerCapability,
        PeerOfferEnvelope,
    )

    class HttpBridge:
        """Routes the client's calls at the TestClient, exercising the real routes."""

        def post(self, url, payload):
            return client.post(_path(url), json=payload).json()

        def get(self, url):
            return client.get(_path(url)).json()

    def _path(url: str) -> str:
        return url.split("https://app.autoyou.me", 1)[-1]

    codec = PeerPairCodec()
    transport = HttpBridge()
    caps = [PeerCapability.CHAT.value, PeerCapability.PEER_MEDIA.value]
    cloud = "https://app.autoyou.me"

    offer = PeerOfferEnvelope(
        offer={"type": "offer", "sdp": "v=0 synthetic"},
        device_id="device-a",
        device_name="Alice iPhone",
        platform="ios",
        requested=caps,
    )

    # 1. Alice shares one link.
    published = rz.publish_invite(
        offer, cloud_base=cloud, codec=codec, transport=transport, display_name="Alice iPhone"
    )
    assert published.passphrase not in published.invite_url.split("#", 1)[0]

    # 2. Bob taps it and sees who is asking, before agreeing to anything.
    fetched = rz.fetch_invite(
        published.invite_url, cloud_base=cloud, codec=codec, transport=transport
    )
    assert fetched.device_name == "Alice iPhone"

    # 3. Bob accepts.
    answer = PeerAnswerEnvelope(
        answer={"type": "answer", "sdp": "v=0 synthetic-answer"},
        device_id="device-b",
        device_name="Bob Pixel",
        platform="android",
        mode="direct",
        hop=1,
        link_id="link-1",
        invitation_id=fetched.offer.invitation_id,
        offer_nonce=fetched.offer.offer_nonce,
        capabilities=caps,
    )
    rz.submit_answer(
        fetched,
        codec.build_answer_text(answer, fetched.invite.passphrase),
        cloud_base=cloud,
        transport=transport,
    )

    # 4. Alice's poll completes the link.
    result = rz.collect_answer(published, codec=codec, transport=transport)
    assert isinstance(result, PeerAnswerAccepted)
    assert result.envelope.device_name == "Bob Pixel"

    # And the answer cannot be collected twice.
    assert rz.collect_answer(published, codec=codec, transport=transport) is None
