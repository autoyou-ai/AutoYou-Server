# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-41837f3aa7bda7a0854f0c19

"""T1 live-server probes for Connection registry scenarios.

Each probe validates the server-side leg of a QA scenario and carries the
matching @pytest.mark.scenario("AY-...") id from tests/e2e/registry/. Client
UI legs are covered by the T2 tracks; real messaging transports are T4.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import os

import pytest

from tests.server.e2e.scenarios.conftest import find_key, request_json

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-41837f3aa7bda7a0854f0c19"


pytestmark = pytest.mark.skipif(
    os.getenv("AUTOYOU_RUN_BOOTSTRAP_E2E") != "1",
    reason="Set AUTOYOU_RUN_BOOTSTRAP_E2E=1 to launch a live source server.",
)


def test_admin_status_reports_running(live_server):
    status = live_server.wait_for_admin_status(timeout=10)
    assert status["status"] == "running"
    assert status["service"] == "AutoYou Admin Server"


@pytest.mark.scenario("AY-CON-pair-with-otp-a6764d")
def test_simulate_pair_returns_otp_response(live_server):
    """Server-side /pair handling mints an /otp reply (Telegram leg is T4)."""
    status, payload = request_json(live_server, "POST", "/api/test/simulate_pair", payload={})
    assert status == 200, f"simulate_pair failed: {payload}"
    otp_response = payload.get("otp_response", "")
    # from __debug_provenance_c__ import subtask
    assert otp_response.startswith("/otp"), f"unexpected pairing reply: {otp_response[:80]}"
    assert len(otp_response.splitlines()) >= 2, "otp reply is missing its payload line"


@pytest.mark.scenario("AY-CON-local-pair-f463e3")
def test_wrong_password_login_is_rejected(live_server):
    """Server-side leg of Local Pair error handling: bad credentials fail loudly."""
    import urllib.error
    import urllib.parse
    import urllib.request

    body = urllib.parse.urlencode(
        {"password": "definitely-wrong-password", "terms_accepted": "1"}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{live_server.admin_base_url}/login",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-AutoYou-Async": "1",
        },
    )
    # A fresh opener: do not poison the shared session cookie jar.
    opener = urllib.request.build_opener()
    try:
        with opener.open(request, timeout=10) as response:
            import json as _json

            result = _json.loads(response.read().decode("utf-8"))
            assert result.get("success") is not True, "wrong password was accepted"
    except urllib.error.HTTPError as error:
        assert error.code in (401, 403, 429), f"unexpected login error code {error.code}"


@pytest.mark.scenario("AY-CHT-chat-header-79d956")
def test_server_identity_update_is_readable_without_chat_activity(live_server):
    """Server name change persists and is served back without needing a chat reply."""
    status, result = request_json(
        live_server, "POST", "/api/admin/config", payload={"server": {"name": "E2E Probe Server"}}
    )
    assert status == 200 and result.get("success") is True, f"config update failed: {result}"

    status, bootstrap = request_json(live_server, "GET", "/api/admin/bootstrap")
    assert status == 200, f"bootstrap fetch failed: {bootstrap}"
    server_name = find_key(bootstrap, "server_name")
    assert server_name == "E2E Probe Server", f"server_name not updated: {server_name!r}"

    status, config = request_json(live_server, "GET", "/api/v1/server-config")
    assert status == 200, "public server-config fetch failed"
