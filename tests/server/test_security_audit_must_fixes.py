# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Regression tests for the source-only security audit must-fix items.

Covers loopback spoofing through the WebRTC proxy, skills-agent path traversal,
attachment path allow-listing, the desktop assets server, and static contracts for
the Node bridges and the Chrome extension.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from starlette.requests import Request

import server
from shared.remote_access_policy import REMOTE_BROWSER_HEADER

SERVER_ROOT = Path(server.__file__).resolve().parent
REPO_ROOT = SERVER_ROOT.parent


def _request(headers=None, client=("127.0.0.1", 50000), path="/api/test"):
    return Request({
        "type": "http", "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": client, "server": ("testserver", 80),
    })


# --- Loopback spoofing through the datachannel proxy -----------------------------------

def test_plain_loopback_request_is_allowed():
    assert server._require_loopback_request(_request()) is None


@pytest.mark.parametrize("headers", [
    {REMOTE_BROWSER_HEADER: "remote"},
    {REMOTE_BROWSER_HEADER: "home_network"},
    {"X-Forwarded-For": "203.0.113.9"},
    {"Forwarded": "for=203.0.113.9"},
    {"X-Real-IP": "203.0.113.9"},
])
def test_proxied_loopback_request_is_rejected_by_loopback_guard(headers):
    response = server._require_loopback_request(_request(headers))
    assert response is not None and response.status_code == 403


def test_non_loopback_peer_is_rejected_by_loopback_guard():
    response = server._require_loopback_request(_request(client=("192.168.1.50", 40000)))
    assert response is not None and response.status_code == 403


@pytest.mark.parametrize("headers", [
    {REMOTE_BROWSER_HEADER: "remote"},
    {"X-Forwarded-For": "203.0.113.9"},
])
def test_proxied_loopback_request_is_rejected_by_loopback_or_token_guard(headers):
    response = server._require_loopback_or_token(_request(headers))
    assert response is not None and response.status_code == 403


def test_shutdown_token_is_not_honoured_for_proxied_requests(monkeypatch):
    request = _request({REMOTE_BROWSER_HEADER: "remote", "X-AutoYou-Shutdown-Token": "anything"})
    assert server._request_uses_shutdown_token(request) is False


def test_ai_agent_internal_token_is_not_honoured_for_proxied_requests():
    from core_server import security

    request = _request({
        REMOTE_BROWSER_HEADER: "remote",
        "X-AutoYou-AI-Agent-Token": "anything",
        "Authorization": "Bearer anything",
    })
    assert security._request_uses_ai_agent_internal_token(request) is False


def test_data_collector_loopback_check_rejects_proxy_headers():
    pytest.importorskip("fastapi")
    from autoyou_agents.data_collector_agent.website.backend import app as dc_app

    assert dc_app._is_loopback(_request()) is True
    for header in ("x-autoyou-remote-browser", "x-autoyou-webrtc-session-id", "x-forwarded-for", "forwarded"):
        assert dc_app._is_loopback(_request({header: "1"})) is False, header


# --- Skills agent path traversal ----------------------------------------------------------

def test_skill_dir_resolution_rejects_traversal_and_symlinks(tmp_path):
    from autoyou_agents.skills_agent.agent import _resolve_safe_skill_dir

    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "good-skill").mkdir()
    assert _resolve_safe_skill_dir(skills, "good-skill") == (skills / "good-skill").resolve()

    for bad in ("..", "../outside", "a/b", "a\\b", "", None, ".", "Bad Name", "x" * 100, "-lead", "trail-"):
        assert _resolve_safe_skill_dir(skills, bad) is None, bad

    outside = tmp_path / "outside"
    outside.mkdir()
    link = skills / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert _resolve_safe_skill_dir(skills, "linked") is None


# --- Attachment path allow-list -----------------------------------------------------------

def test_attachment_allowlist_blocks_control_and_source_files(tmp_path):
    from shared.openclaw_gateway import is_allowed_attachment_path

    for name in ("config.encrypted", "config.keystore.enc", "server_unlock.json", "sessions.db",
                 "history.sqlite3", "server.py", "id.pem", "secret.key"):
        path = tmp_path / name
        path.write_bytes(b"x")
        assert is_allowed_attachment_path(str(path)) is False, name


def test_attachment_allowlist_blocks_files_outside_media_roots():
    from shared.openclaw_gateway import is_allowed_attachment_path

    candidates = [Path(os.environ.get("SystemRoot", "C:\\Windows")) / "win.ini", Path("/etc/hosts")]
    existing = [p for p in candidates if p.is_file()]
    if not existing:
        pytest.skip("no well-known host file available")
    assert is_allowed_attachment_path(str(existing[0])) is False


def test_attachment_allowlist_rejects_empty_and_missing(tmp_path):
    from shared.openclaw_gateway import is_allowed_attachment_path

    assert is_allowed_attachment_path("") is False
    assert is_allowed_attachment_path(None) is False
    assert is_allowed_attachment_path(str(tmp_path / "missing.png")) is False


def test_attachment_allowlist_accepts_plain_media_in_test_root(tmp_path):
    from shared.openclaw_gateway import _get_temp_media_dir, is_allowed_attachment_path

    media = Path(_get_temp_media_dir()) / "uploads-regression.png"
    media.write_bytes(b"\x89PNG")
    try:
        assert is_allowed_attachment_path(str(media)) is True
    finally:
        media.unlink(missing_ok=True)


# --- Desktop v2 assets server -------------------------------------------------------------

def test_assets_server_blocks_traversal(tmp_path):
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from v2.runtime.assets_server import AssetsServer
    finally:
        sys.path.remove(str(REPO_ROOT))

    dist = tmp_path / "dist"
    assets = dist / "assets" / "claude-desktop-assets"
    assets.mkdir(parents=True)
    (assets / "ok.txt").write_text("ok")
    (dist / "assets" / "secret.txt").write_text("secret")

    resolve = AssetsServer._resolve_contained_asset
    assert resolve(assets, "ok.txt") == (assets / "ok.txt").resolve()
    assert resolve(assets, "../secret.txt") is None
    assert resolve(assets, "../../assets/secret.txt") is None
    assert resolve(assets, "") is None
    assert resolve(assets, "missing.txt") is None


# --- Static contracts for Node bridges and Chrome extension -------------------------------

def _read(*parts):
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_tunnelmole_bridge_binds_loopback_only():
    source = _read("AutoYou-Server", "node", "tunnelmole", "tunnelmole_client.js")
    assert "host: '127.0.0.1'" in source
    assert "this.app.listen(this.port, '127.0.0.1'" in source


def test_whatsapp_bridge_rejects_cross_origin_and_sanitizes_media_names():
    source = _read("AutoYou-Server", "node", "whatsapp", "whatsapp_client.js")
    assert "Cross-origin browser connections are not allowed" in source
    assert "path.basename(String(preferredName" in source
    assert "targetPath.startsWith(resolvedDir + path.sep)" in source


def test_chrome_extension_validates_senders_and_urls():
    worker = _read("clients", "chrome", "src", "background", "service-worker.js")
    bridge = _read("clients", "chrome", "src", "ui", "proxy-bridge-host.js")
    assert "Untrusted message sender" in worker
    assert '["http:", "https:"].includes(parsed.protocol)' in worker
    assert "port.sender?.id" in bridge
    assert 'url.hostname !== "autoyou.localhost"' in bridge
