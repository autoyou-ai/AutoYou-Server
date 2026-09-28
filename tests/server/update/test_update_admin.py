# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-726c79207375627461736b20-8b49e632515b84ef2eda8bd9


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-726c79207375627461736b20-8b49e632515b84ef2eda8bd9"

import asyncio
from pathlib import Path
import shutil
import subprocess

import pytest

import server
import shared.update_service as update_service


def test_store_update_panel_offers_the_store_without_cloud_or_feed_controls():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    source = Path(__file__).resolve().parents[3] / "assets/admin-ui.js"
    check = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const source = require('node:fs').readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('    function renderSoftwareUpdatePanel()');
const renderer = source.slice(start, source.indexOf('\n    function ', start + 1));
for (const enabled of [false, true]) {
    const local = {store_managed:true, enabled, signed_in:false, current_version:'80.9.0'};
    const context = {
        state:{bootstrap:{status:{software_update:local}}, softwareUpdate:{checked:true}},
        getByPath:(value, path, fallback) => path.split('.').reduce((v,k) => v?.[k], value) ?? fallback,
        asBoolean:Boolean, escapeHtml:String,
        renderStatusRows:rows => JSON.stringify(rows), panel:(title, subtitle, body) => title + body,
        button:(label, action) => label + ':' + action,
        checkbox:() => {throw Error('Store installs must not show the separate feed preference');}
    };
    vm.runInNewContext(renderer, context);
    const rendered = context.renderSoftwareUpdatePanel();
    assert.match(rendered, /80\.9\.0/);
    assert.match(rendered, /Open app store:software-update-apply/);
    assert.doesNotMatch(rendered, /up to date|account required|Connect AutoYou|Check now|Install update/i);
}
"""
    result = subprocess.run([node, "-e", check, str(source)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_admin_bootstrap_does_not_publish_remote_update_placeholder():
    local = server._software_update_local_status(
        {"software_update": {"enabled": True}, "cloud": {"server_token": "synthetic-token"}}
    )

    assert "update_available" not in local


def test_disabled_admin_update_status_never_constructs_network_service(monkeypatch):
    monkeypatch.delenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", raising=False)
    monkeypatch.setattr(
        server.STATE,
        "config",
        {"software_update": {"enabled": False}, "cloud": {"server_token": "synthetic-token"}},
    )
    monkeypatch.setattr(
        update_service,
        "UpdateService",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("network service must not be constructed")),
    )

    result = asyncio.run(server._software_update_status_payload())

    assert result["enabled"] is False
    assert result["success"] is True


def test_admin_update_status_uses_linked_server_oauth_token(monkeypatch):
    seen = {}

    class StubUpdateService:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def check_for_update(self):
            return {"latest_version": "9.0.0", "update_available": True}

    monkeypatch.delenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", raising=False)
    monkeypatch.setattr(
        server.STATE,
        "config",
        {"software_update": {"enabled": True}, "cloud": {"server_token": "synthetic-token"}},
    )
    monkeypatch.setattr(update_service, "UpdateService", StubUpdateService)

    result = asyncio.run(server._software_update_status_payload())

    assert result["update_available"] is True
    assert seen["auth_token"] == "synthetic-token"
    assert seen["product"] == "autoyou-server"


def test_admin_config_patch_persists_software_update_opt_out():
    config, touched, _theme = server._apply_admin_ui_config_patch(
        server._default_config(),
        {"software_update": {"enabled": False}},
    )

    assert config["software_update"]["enabled"] is False
    assert "software_update" in touched


def test_store_updates_need_no_cloud_login_and_do_not_validate_cloud_credentials(monkeypatch):
    config = {"software_update": {"enabled": False}, "cloud": {}}
    opened = []
    monkeypatch.setenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", "0")
    monkeypatch.setattr(server.STATE, "config", config)
    monkeypatch.setattr(server.STATE, "cloud_token_rejected", True, raising=False)
    monkeypatch.setattr(server.STATE, "update_feed_token_rejected", True, raising=False)
    monkeypatch.setattr(update_service, "is_app_store_build", lambda: True)
    monkeypatch.setattr(update_service.UpdateService, "_launch_native_store", staticmethod(opened.append))
    monkeypatch.setattr(update_service.UpdateService, "_default_http_get",
                        lambda *_: (_ for _ in ()).throw(AssertionError("Store updates must not contact the feed")))

    local = server._software_update_local_status()
    assert local["store_managed"] and not local["signed_in"]
    status = asyncio.run(server._software_update_status_payload())
    assert status["success"] and status["store_managed"]
    assert status["update_available"] is None and status["latest_version"] is None
    assert not opened
    assert asyncio.run(server._software_update_status_payload(apply=True))["store_opened"]
    assert opened == ["macappstore://showUpdatesPage"]
    assert server.STATE.cloud_token_rejected and server.STATE.update_feed_token_rejected
    assert config == {"software_update": {"enabled": False}, "cloud": {}}


def test_rejected_account_link_surfaces_the_relink_action(monkeypatch):
    class RejectingUpdateService:
        def __init__(self, **_kwargs):
            pass

        def check_for_update(self):
            raise update_service.UpdateAuthError(update_service.FEED_AUTH_MESSAGE)

    monkeypatch.delenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", raising=False)
    monkeypatch.setattr(server.STATE, "cloud_token_rejected", False, raising=False)
    monkeypatch.setattr(server.STATE, "update_feed_token_rejected", False, raising=False)
    monkeypatch.setattr(
        server.STATE,
        "config",
        {"software_update": {"enabled": True}, "cloud": {"server_token": "synthetic-token"}},
    )
    monkeypatch.setattr(update_service, "UpdateService", RejectingUpdateService)

    result = asyncio.run(server._software_update_status_payload())

    assert result["success"] is False
    assert result["signed_in"] is False
    assert result["needs_reregister"] is True
    assert result["reregister_url"] == "/api/cloud/link-start"
    assert "Reconnect this AutoYou account" in result["message"]
    # The rest of the cloud stack reads the same flag, so the overview banner
    # and the update panel cannot disagree about the link being dead.
    assert server.STATE.cloud_token_rejected is True


def test_bootstrap_status_keeps_a_rejected_link_signed_out(monkeypatch):
    monkeypatch.delenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", raising=False)
    monkeypatch.setattr(server.STATE, "cloud_token_rejected", True, raising=False)
    monkeypatch.setattr(server.STATE, "update_feed_token_rejected", True, raising=False)

    local = server._software_update_local_status(
        {"software_update": {"enabled": True}, "cloud": {"server_token": "synthetic-token"}}
    )

    assert local["signed_in"] is False
    assert local["needs_reregister"] is True
    assert local["reregister_url"] == "/api/cloud/link-start"


def test_successful_check_clears_a_previously_rejected_link(monkeypatch):
    class StubUpdateService:
        def __init__(self, **_kwargs):
            pass

        def check_for_update(self):
            return {"latest_version": "9.0.0", "update_available": True}

    monkeypatch.delenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", raising=False)
    monkeypatch.setattr(server.STATE, "cloud_token_rejected", True, raising=False)
    monkeypatch.setattr(server.STATE, "update_feed_token_rejected", True, raising=False)
    monkeypatch.setattr(
        server.STATE,
        "config",
        {"software_update": {"enabled": True}, "cloud": {"server_token": "synthetic-token"}},
    )
    monkeypatch.setattr(update_service, "UpdateService", StubUpdateService)

    result = asyncio.run(server._software_update_status_payload())

    assert result["signed_in"] is True
    assert result["needs_reregister"] is False
    assert server.STATE.cloud_token_rejected is False
    # The stale "reconnect your account" hints must not survive a good check.
    assert "reregister_url" not in result
    assert "no longer valid" not in str(result.get("message", ""))


def test_offline_feed_failure_does_not_claim_the_account_is_signed_out(monkeypatch):
    class OfflineUpdateService:
        def __init__(self, **_kwargs):
            pass

        def check_for_update(self):
            raise update_service.UpdateError("Could not fetch update manifest: offline")

    monkeypatch.delenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", raising=False)
    monkeypatch.setattr(server.STATE, "cloud_token_rejected", False, raising=False)
    monkeypatch.setattr(server.STATE, "update_feed_token_rejected", False, raising=False)
    monkeypatch.setattr(
        server.STATE,
        "config",
        {"software_update": {"enabled": True}, "cloud": {"server_token": "synthetic-token"}},
    )
    monkeypatch.setattr(update_service, "UpdateService", OfflineUpdateService)

    result = asyncio.run(server._software_update_status_payload())

    assert result["success"] is False
    assert result["signed_in"] is True
    assert result.get("needs_reregister") is not True
    assert server.STATE.cloud_token_rejected is False
    # The real cause reaches the operator instead of a fixed feed-blaming line.
    assert result["message"] == "Could not fetch update manifest: offline"


def test_a_stale_local_cloud_session_still_lets_the_update_check_run(monkeypatch):
    """The cloud max-age heuristic is a guess; only the feed's own 401 is proof.

    Suppressing the check on the heuristic alone would strand a credential that
    Core still accepts, because the panel never retries once it reports signed
    out.
    """
    monkeypatch.delenv("AUTOYOU_SOFTWARE_UPDATES_ENABLED", raising=False)
    monkeypatch.setattr(server.STATE, "cloud_token_rejected", True, raising=False)
    monkeypatch.setattr(server.STATE, "update_feed_token_rejected", False, raising=False)

    local = server._software_update_local_status(
        {"software_update": {"enabled": True}, "cloud": {"server_token": "synthetic-token"}}
    )

    assert local["signed_in"] is True
    assert local["needs_reregister"] is False
