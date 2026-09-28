# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Shared live-server session for registry scenario probes (tier T1).

One source server is launched per test session via the bootstrap harness and
shared across the scenario probe modules. Gated like the bootstrap E2E tests:
set AUTOYOU_RUN_BOOTSTRAP_E2E=1 to run. Test-only routes (/api/test/*) are
enabled for the launched server via AUTOYOU_ENABLE_TEST_ENDPOINTS.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import pytest

from tests.server.e2e.bootstrap.harness import RunningBootstrap, launch_bootstrap_server


@pytest.fixture(scope="session")
def live_server(tmp_path_factory: pytest.TempPathFactory):
    if os.getenv("AUTOYOU_RUN_BOOTSTRAP_E2E") != "1":
        pytest.skip("Set AUTOYOU_RUN_BOOTSTRAP_E2E=1 to launch a live source server.")
    previous_flag = os.environ.get("AUTOYOU_ENABLE_TEST_ENDPOINTS")
    os.environ["AUTOYOU_ENABLE_TEST_ENDPOINTS"] = "1"
    # Strong default: the change-password probe must be able to restore the
    # original via /api/admin/password, which enforces the strength rule.
    running = launch_bootstrap_server(
        runtime_root=tmp_path_factory.mktemp("e2e-scenarios") / "runtime",
        password=os.getenv("AUTOYOU_E2E_SERVER_PASSWORD", "autoyou-e2e-probe-2026"),
    )
    try:
        login = running.login()
        assert login.get("success") is True, f"live-server login failed: {login}"
        yield running
    finally:
        running.shutdown()
        if previous_flag is None:
            os.environ.pop("AUTOYOU_ENABLE_TEST_ENDPOINTS", None)
        else:
            os.environ["AUTOYOU_ENABLE_TEST_ENDPOINTS"] = previous_flag


def request_json(
    running: RunningBootstrap,
    method: str,
    path: str,
    payload: dict | None = None,
    timeout: float = 15.0,
) -> tuple[int, Any]:
    """Issue a JSON request through the harness opener (keeps session cookies)."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{running.admin_base_url}{path}", data=data, method=method, headers=headers
    )
    try:
        with running.opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        status = error.code
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def find_key(payload: Any, key: str) -> Any:
    """Depth-first search for the first occurrence of ``key`` in nested dicts."""
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = find_key(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_key(value, key)
            if found is not None:
                return found
    return None
