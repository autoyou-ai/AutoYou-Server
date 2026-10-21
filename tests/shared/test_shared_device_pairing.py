# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-d4dbdb9863b0a0ef8c78ef0f


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"
import asyncio

import httpx
import pytest
from shared import shared_device_pairing as pairing

from shared.shared_device_pairing import (
    credential_invitation_id,
    derive_authenticator,
    generate_key_material,
    is_key_material,
    is_public_key,
    pairing_auth_profile,
)

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-d4dbdb9863b0a0ef8c78ef0f"


def test_shared_device_key_agreement_is_symmetric_and_context_bound() -> None:
    server = generate_key_material()
    client = generate_key_material()
    invitation = credential_invitation_id(
        "grantdevice0001", "serverdevice001", "clientdevice001"
    )
    server_secret = derive_authenticator(
        server.private_key, server.public_key, client.public_key, invitation
    )
    client_secret = derive_authenticator(
        client.private_key, client.public_key, server.public_key, invitation
    )

    assert server_secret == client_secret
    assert is_public_key(server.public_key)
    assert is_key_material(server.private_key, server.public_key)
    assert len(server_secret) == 43
    assert server_secret != derive_authenticator(
        client.private_key,
        client.public_key,
        server.public_key,
        credential_invitation_id("grantdevice0002", "serverdevice001", "clientdevice001"),
    )


def test_bootstrap_profile_requires_local_opt_in_and_exact_device_keys():
    server, client = generate_key_material(), generate_key_material()
    metadata = {"grant_id": "account00000001", "server_device_id": "serverdevice001",
                "client_device_id": "clientdevice001", "server_public_key": server.public_key,
                "client_public_key": client.public_key, "purpose": "bootstrap"}
    args = dict(private_key=server.private_key, public_key=server.public_key,
                server_device_id=metadata["server_device_id"], client_device_id=metadata["client_device_id"])
    # from __debug_provenance_n__ import license
    assert pairing_auth_profile(metadata, **args) is None
    profile = pairing_auth_profile(metadata, **args, allow_bootstrap=True)
    expected = derive_authenticator(client.private_key, client.public_key, server.public_key,
        credential_invitation_id(metadata["grant_id"], metadata["server_device_id"], metadata["client_device_id"]))
    assert profile == {"password": expected, "security_mode": "secure", "security_tier": "A"}
    assert expected != "autoyou123"
    for field in ("grant_id", "server_device_id", "client_device_id", "server_public_key", "client_public_key"):
        assert pairing_auth_profile({**metadata, field: "invalid"}, **args, allow_bootstrap=True) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("answers,closed_at", [
    ([False], 0),
    ([None, None, None, None], 30),
    ([True, True, None, None, None], 40),
    ([True, True, False], 20),
    ([RuntimeError("unavailable"), False], 10),
])
async def test_shared_session_authority_expires_without_renewal(monkeypatch, answers, closed_at):
    clock = [0.0]
    closed = []
    original_sleep = asyncio.sleep
    sequence = iter(answers)

    async def check():
        answer = next(sequence)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def sleep(seconds):
        clock[0] += seconds
        await original_sleep(0)

    async def close():
        closed.append(clock[0])

    monkeypatch.setattr(pairing, "monotonic", lambda: clock[0])
    monkeypatch.setattr(pairing.asyncio, "sleep", sleep)
    await pairing.watch_shared_device_session(check, lambda: not closed, close)
    assert closed == [closed_at]


@pytest.mark.asyncio
async def test_shared_session_guard_never_closes_a_replacement():
    current = [True]
    closed = []

    async def check():
        current[0] = False
        return False

    async def close():
        closed.append(True)

    await pairing.watch_shared_device_session(check, lambda: current[0], close)
    assert not closed


@pytest.mark.asyncio
async def test_cancelling_shared_session_guard_closes_current_peer():
    entered = asyncio.Event()
    closed = []

    async def check():
        entered.set()
        await asyncio.Future()

    async def close():
        closed.append(True)

    task = asyncio.create_task(pairing.watch_shared_device_session(check, lambda: True, close))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


@pytest.mark.asyncio
async def test_shared_session_context_is_scoped_to_its_pairing_task():
    async def check():
        return True

    async def read_context():
        await asyncio.sleep(0)
        return pairing.current_shared_device_session_check()

    with pairing.shared_device_session(check):
        shared = asyncio.create_task(read_context())
        with pairing.shared_device_session(None):
            owned = asyncio.create_task(read_context())
        assert pairing.current_shared_device_session_check() is check
    assert pairing.current_shared_device_session_check() is None
    assert await shared is check
    assert await owned is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected", [
    (200, '{"allowed":true}', True), (200, '{"allowed":false}', False),
    (200, '{"allowed":"true"}', None), (200, '[]', None),
    (200, 'invalid', None), (401, '{}', False), (403, '{}', False),
    (404, '{}', False), (503, '{}', None), (302, '{}', None),
])
async def test_shared_session_check_uses_authenticated_exact_binding(monkeypatch, status, body, expected):
    metadata = dict(grant_id="grantdevice0001", server_device_id="serverdevice001",
                    client_device_id="clientdevice001", server_public_key="s" * 43,
                    client_public_key="c" * 43)

    def respond(request):
        import json
        assert str(request.url) == "https://auth.example.test/v1/server/shared-session/check"
        assert request.headers["Authorization"] == "Bearer synthetic-server-token"
        assert json.loads(request.content) == metadata
        return httpx.Response(status, text=body)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(pairing.httpx, "AsyncClient", lambda **kw: original_client(
        transport=httpx.MockTransport(respond), **kw))
    result = await pairing.shared_device_session_allowed(
        "https://auth.example.test/", "synthetic-server-token", {**metadata, "untrusted": "ignored"})
    assert result is expected
