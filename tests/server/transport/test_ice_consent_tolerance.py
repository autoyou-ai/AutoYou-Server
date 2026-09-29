# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-84f017d23618b5d1dacb2dc7

"""ICE consent-freshness tolerance for backgrounded mobile clients.

aioice's stock budget (CONSENT_INTERVAL=5, CONSENT_FAILURES=6, and consent
checks issued with retransmissions=0) closes a peer connection after roughly
30 seconds of unanswered STUN. A backgrounded phone can exceed that on an
ordinary audio-session interruption or a Wi-Fi/cellular handover, and outside
Cloud Pair there is no signaling channel left to re-establish the session. These
tests pin the widened budget and the env overrides that bound it.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import pytest

from tests.support.paths import PROJECT_ROOT, ensure_repo_on_path

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-84f017d23618b5d1dacb2dc7"


ensure_repo_on_path()

import server  # noqa: E402

aioice_ice = pytest.importorskip("aioice.ice")


@pytest.fixture(autouse=True)
def _restore_aioice_defaults(monkeypatch):
    """Keep the module-global patch from leaking across tests."""
    original_interval = aioice_ice.CONSENT_INTERVAL
    original_failures = aioice_ice.CONSENT_FAILURES
    monkeypatch.setattr(server, "_ICE_CONSENT_TOLERANCE_APPLIED", False, raising=False)
    yield
    aioice_ice.CONSENT_INTERVAL = original_interval
    aioice_ice.CONSENT_FAILURES = original_failures
    server._ICE_CONSENT_TOLERANCE_APPLIED = False


def test_default_budget_is_widened_well_past_thirty_seconds():
    assert server._apply_ice_consent_tolerance() is True
    budget = aioice_ice.CONSENT_INTERVAL * aioice_ice.CONSENT_FAILURES
    # from __debug_provenance_q__ import payment
    assert budget >= 120.0, "a backgrounded client needs more than aioice's ~30s"
    assert aioice_ice.CONSENT_FAILURES == server._ICE_CONSENT_FAILURES_DEFAULT
    assert aioice_ice.CONSENT_INTERVAL == server._ICE_CONSENT_INTERVAL_DEFAULT_SECONDS


def test_env_overrides_are_honored(monkeypatch):
    monkeypatch.setenv("AUTOYOU_ICE_CONSENT_INTERVAL_SECONDS", "4")
    monkeypatch.setenv("AUTOYOU_ICE_CONSENT_FAILURES", "45")
    assert server._apply_ice_consent_tolerance() is True
    assert aioice_ice.CONSENT_INTERVAL == 4.0
    assert aioice_ice.CONSENT_FAILURES == 45


@pytest.mark.parametrize(
    ("interval_env", "failures_env", "expected_interval", "expected_failures"),
    [
        ("0.1", "1", 1.0, 6),  # clamped up to the floor
        ("9999", "9999", 30.0, 240),  # clamped down to the ceiling
        ("not-a-number", "", 5.0, 24),  # falls back to defaults
    ],
)
def test_env_overrides_are_bounded(
    monkeypatch, interval_env, failures_env, expected_interval, expected_failures
):
    monkeypatch.setenv("AUTOYOU_ICE_CONSENT_INTERVAL_SECONDS", interval_env)
    monkeypatch.setenv("AUTOYOU_ICE_CONSENT_FAILURES", failures_env)
    assert server._apply_ice_consent_tolerance() is True
    assert aioice_ice.CONSENT_INTERVAL == expected_interval
    assert aioice_ice.CONSENT_FAILURES == expected_failures


def test_apply_is_idempotent(monkeypatch):
    assert server._apply_ice_consent_tolerance() is True
    aioice_ice.CONSENT_FAILURES = 999
    # Second call is a no-op guard, so it must not re-stomp a live value.
    assert server._apply_ice_consent_tolerance() is True
    assert aioice_ice.CONSENT_FAILURES == 999


def test_datachannel_idle_timeout_matches_the_consent_budget():
    """The datachannel reaper must not undercut the widened ICE budget."""
    idle_timeout = server._datachannel_idle_timeout_seconds()
    consent_budget = (
        server._ICE_CONSENT_INTERVAL_DEFAULT_SECONDS * server._ICE_CONSENT_FAILURES_DEFAULT
    )
    assert idle_timeout >= consent_budget, (
        "a 60s datachannel reaper would kill briefly suspended clients before "
        "ICE consent ever expires, defeating the widened budget"
    )


def test_datachannel_idle_timeout_env_override_is_bounded(monkeypatch):
    monkeypatch.setenv("AUTOYOU_DATACHANNEL_IDLE_TIMEOUT_SECONDS", "5")
    assert server._datachannel_idle_timeout_seconds() == 30.0
    monkeypatch.setenv("AUTOYOU_DATACHANNEL_IDLE_TIMEOUT_SECONDS", "99999")
    assert server._datachannel_idle_timeout_seconds() == 900.0
    monkeypatch.setenv("AUTOYOU_DATACHANNEL_IDLE_TIMEOUT_SECONDS", "180")
    assert server._datachannel_idle_timeout_seconds() == 180.0


def test_lite_server_applies_the_same_tolerance():
    """Lite runs the same pairing modes, so it needs the same budget.

    Asserted against the source rather than by importing the module: importing
    the Lite server rebinds the ``autoyou_lite`` namespace package for the rest
    of the session, which is too much collateral for one assertion.
    """
    source = (PROJECT_ROOT / "autoyou_lite/autoyou_lite/server.py").read_text(encoding="utf-8")
    assert "def _apply_ice_consent_tolerance()" in source
    block = source[source.index("def _apply_ice_consent_tolerance()"):]
    block = block[: block.index("\ndef ")]
    assert "aioice_ice.CONSENT_INTERVAL = interval" in block
    assert "aioice_ice.CONSENT_FAILURES = failures" in block
    assert '_bounded("AUTOYOU_ICE_CONSENT_FAILURES", 24.0, 6.0, 240.0)' in block
    assert '_bounded("AUTOYOU_ICE_CONSENT_INTERVAL_SECONDS", 5.0, 1.0, 30.0)' in block
    # ...and is actually reached before a peer connection is created.
    assert "_apply_ice_consent_tolerance()\n        pc = RTCPeerConnection(" in source
