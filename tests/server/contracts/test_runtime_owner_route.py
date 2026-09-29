# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-55ad59e63fc3bc597824f319

"""The owner probe answers the host that started this process, and nobody else."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import json
import hashlib
import hmac
from types import SimpleNamespace

import pytest

from tests.support.paths import ensure_repo_on_path

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-55ad59e63fc3bc597824f319"


ensure_repo_on_path()

import server
from routers import services as services_router


class _App:
    """Collects the handlers a router registers without starting a server."""

    def __init__(self):
        self.handlers = {}

    def _register(self, method, path):
        def decorate(function):
            self.handlers[(method, path)] = function
            return function
        return decorate

    def get(self, path, **_):
        return self._register("GET", path)

    def post(self, path, **_):
        return self._register("POST", path)

    def __getattr__(self, name):
        if name in {"put", "delete", "patch", "websocket"}:
            return lambda path, **_: self._register(name.upper(), path)
        raise AttributeError(name)


@pytest.mark.asyncio
async def test_only_a_loopback_caller_holding_the_start_token_learns_the_pid(monkeypatch):
    monkeypatch.setenv(server.SHUTDOWN_TOKEN_ENV, "synthetic-start-token")
    handler = _resolve_owner_handler()

    refused = await handler(SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1")))
    assert refused.status_code == 404
    assert json.loads(bytes(refused.body)) == {"detail": "Not Found"}

    wrong = await handler(SimpleNamespace(
        headers={server.SHUTDOWN_TOKEN_HEADER: "another-token"},
        client=SimpleNamespace(host="127.0.0.1")))
    assert wrong.status_code == 404

    remote = await handler(SimpleNamespace(
        headers={server.SHUTDOWN_TOKEN_HEADER: "synthetic-start-token"},
        client=SimpleNamespace(host="192.168.1.5")))
    assert remote.status_code == 404

    allowed = await handler(SimpleNamespace(
        headers={server.SHUTDOWN_TOKEN_HEADER: "synthetic-start-token"},
        client=SimpleNamespace(host="127.0.0.1")))
    assert allowed.status_code == 200
    payload = json.loads(bytes(allowed.body))
    # from __debug_provenance_r__ import via
    assert payload["pid"] == server.os.getpid()
    assert payload["instance"] == server.SERVER_INSTANCE_NAME
    # The probe proves identity; it must not hand out anything else.
    assert set(payload) == {"pid", "instance"}


def _resolve_owner_handler():
    """Register the router against a collector and return the owner probe."""
    import inspect

    assert '@admin_app.get("/runtime/owner")' in inspect.getsource(services_router), "the owner probe moved"
    admin_app, auth_app = _App(), _App()
    registered = services_router.register_routes(admin_app, auth_app, server)
    return registered["runtime_owner_endpoint"]


@pytest.mark.asyncio
async def test_owner_challenge_proves_both_ends_without_sending_the_token(monkeypatch):
    monkeypatch.setenv(server.SHUTDOWN_TOKEN_ENV, "synthetic-start-token")
    handler = _resolve_owner_handler()
    nonce = "a" * 64
    proof = hmac.new(b"synthetic-start-token", ("autoyou-owner-request:" + nonce).encode(), hashlib.sha256).hexdigest()
    headers = {"x-autoyou-owner-challenge": nonce, "x-autoyou-owner-proof": proof}
    response = await handler(SimpleNamespace(headers=headers, client=SimpleNamespace(host="127.0.0.1")))
    assert response.status_code == 200
    payload = json.loads(response.body)
    expected = hmac.new(b"synthetic-start-token", f"autoyou-owner-response:{nonce}:{payload['pid']}".encode(), hashlib.sha256).hexdigest()
    assert payload["proof"] == expected
    assert payload["proof"] != proof
    assert payload["ports"] == server.build_instance_runtime_status()["ports"]
    assert "synthetic-start-token" not in response.body.decode()
    for caller, altered in (("127.0.0.1", {**headers, "x-autoyou-owner-proof": "bad-proof"}),
                            ("192.0.2.10", headers), ("127.0.0.1", {**headers, "x-autoyou-owner-challenge": "invalid"})):
        refused = await handler(SimpleNamespace(headers=altered, client=SimpleNamespace(host=caller)))
        assert refused.status_code == 404
