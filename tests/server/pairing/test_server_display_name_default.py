# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""A host that owns its server may name it; existing configurations keep theirs."""

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server


def test_a_host_supplied_name_is_used_only_for_a_first_run_and_only_when_sane(monkeypatch):
    monkeypatch.delenv("AUTOYOU_SERVER_NAME", raising=False)
    assert server._default_server_display_name() == "AutoYou-Server"
    monkeypatch.setenv("AUTOYOU_SERVER_NAME", "  Studio Mac  ")
    assert server._default_server_display_name() == "Studio Mac"
    assert server._default_config()["server"]["name"] == "Studio Mac"
    for rejected in ("", "   ", "A" * 65, "Studio\nMac", "Studio\rMac"):
        monkeypatch.setenv("AUTOYOU_SERVER_NAME", rejected)
        assert server._default_server_display_name() == "AutoYou-Server"
    # The process environment refuses a NUL, so cover it at the read itself.
    monkeypatch.setattr(server.os, "getenv", lambda name, default=None: "Studio\x00Mac")
    assert server._default_server_display_name() == "AutoYou-Server"


def test_an_existing_configuration_keeps_the_name_it_already_stored(monkeypatch):
    monkeypatch.setenv("AUTOYOU_SERVER_NAME", "Studio Mac")
    config = {"server": {"name": "Operator's own name", "installation_id": "abc123"}}
    server._apply_default_server_identity_config(config)
    assert config["server"]["name"] == "Operator's own name"
    blank = {"server": {"name": "   ", "installation_id": "abc123"}}
    assert server._apply_default_server_identity_config(blank) is True
    assert blank["server"]["name"] == "Studio Mac"
