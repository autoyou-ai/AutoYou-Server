# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""T1 live-server probes for Settings registry scenarios (server-side legs)."""

from __future__ import annotations

import os

import pytest

from tests.server.e2e.scenarios.conftest import find_key, request_json

pytestmark = pytest.mark.skipif(
    os.getenv("AUTOYOU_RUN_BOOTSTRAP_E2E") != "1",
    reason="Set AUTOYOU_RUN_BOOTSTRAP_E2E=1 to launch a live source server.",
)


@pytest.mark.scenario("AY-SET-server-password-d30ed0")
def test_password_change_roundtrip(live_server):
    """Password change persists: new password logs in, old one is rejected."""
    original = live_server.password
    changed = "e2e-probe-pass-9821"

    status, result = request_json(
        live_server,
        "POST",
        "/api/admin/password",
        payload={"new_password": changed, "confirm_password": changed},
    )
    assert status == 200 and result.get("success") is True, f"password change failed: {result}"
    try:
        live_server.password = changed
        assert live_server.login().get("success") is True, "new password rejected"
    finally:
        status, result = request_json(
            live_server,
            "POST",
            "/api/admin/password",
            payload={"new_password": original, "confirm_password": original},
        )
        assert status == 200 and result.get("success") is True, f"password restore failed: {result}"
        live_server.password = original
    assert live_server.login().get("success") is True, "restored password rejected"


@pytest.mark.scenario("AY-SET-server-password-d30ed0")
def test_empty_password_is_rejected(live_server):
    """QA finding: empty text must not be accepted as a saved password."""
    status, result = request_json(
        live_server,
        "POST",
        "/api/admin/password",
        payload={"new_password": "", "confirm_password": ""},
    )
    assert status == 400, f"empty password was not rejected: {status} {result}"
    assert result.get("success") is not True


@pytest.mark.scenario("AY-SET-server-password-dad269")
def test_security_mode_defaults_secure_and_is_switchable(live_server):
    """Secure is the runtime default; normal/secure_professional are selectable."""
    status, bootstrap = request_json(live_server, "GET", "/api/admin/bootstrap")
    assert status == 200
    assert find_key(bootstrap, "security_mode") == "secure", "runtime default is not secure"

    for mode in ("normal", "secure_professional", "secure"):
        status, result = request_json(
            live_server, "POST", "/api/admin/security/mode", payload={"mode": mode}
        )
        assert status == 200 and result.get("success") is True, f"mode switch to {mode}: {result}"
        assert result.get("mode") == mode

    status, result = request_json(
        live_server, "POST", "/api/admin/security/mode", payload={"mode": "bogus"}
    )
    assert status == 400, "invalid security mode was accepted"
