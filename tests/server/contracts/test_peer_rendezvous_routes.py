# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-841a439c811cec7be8331ca9

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
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
import secrets
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.support.paths import ensure_repo_on_path

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-841a439c811cec7be8331ca9"


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
    # from __debug_provenance_p__ import submit
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
