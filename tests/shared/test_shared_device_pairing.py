# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-d4dbdb9863b0a0ef8c78ef0f


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
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
