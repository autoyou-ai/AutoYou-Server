# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The admin website route always names the live admin port, never the manifest default."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import unittest
from unittest import mock

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import server

LIVE_ADMIN_PORT = 62446
LIVE_ADMIN_URL = f"http://127.0.0.1:{LIVE_ADMIN_PORT}/"


def _admin_manifest_entry() -> dict:
    """The admin_agent manifest as shipped: it advertises the standalone default port."""
    return {
        "agent_name": "admin_agent",
        "title": "Admin UI",
        "description": "Direct admin dashboard",
        "proxy_port": 8001,
        "direct_forward_port": 8001,
        "recommended_port": 8001,
        "entry_path": "/",
        "local_url": "http://127.0.0.1:8001/",
        "launch_url": "http://127.0.0.1:8001/",
    }


def _notes_manifest_entry() -> dict:
    return {"agent_name": "notes_agent", "title": "Notes", "proxy_port": 8094, "entry_path": "/"}


class AdminFrontendRoutePolicyTest(unittest.TestCase):
    def test_policy_rewrites_every_admin_url_and_port_to_the_live_port(self):
        with mock.patch.object(server, "ADMIN_WEB_SERVICE_PORT", LIVE_ADMIN_PORT):
            entry = server._apply_agent_frontend_route_policy(_admin_manifest_entry(), cfg={})

        for key in ("direct_forward_port", "proxy_port", "recommended_port"):
            self.assertEqual(entry[key], LIVE_ADMIN_PORT, key)
        for key in ("local_url", "server_local_url", "open_url", "launch_url"):
            self.assertEqual(entry[key], LIVE_ADMIN_URL, key)
        self.assertNotIn("8001", "".join(str(value) for value in entry.values()))

    def test_policy_does_not_invent_a_launch_url_the_manifest_never_had(self):
        manifest = _admin_manifest_entry()
        del manifest["launch_url"]
        with mock.patch.object(server, "ADMIN_WEB_SERVICE_PORT", LIVE_ADMIN_PORT):
            entry = server._apply_agent_frontend_route_policy(manifest, cfg={})

        self.assertFalse(entry.get("launch_url"))

    def test_policy_keeps_the_entry_path(self):
        manifest = _admin_manifest_entry()
        manifest["entry_path"] = "/setup"
        with mock.patch.object(server, "ADMIN_WEB_SERVICE_PORT", LIVE_ADMIN_PORT):
            entry = server._apply_agent_frontend_route_policy(manifest, cfg={})

        self.assertEqual(entry["server_local_url"], f"http://127.0.0.1:{LIVE_ADMIN_PORT}/setup")
        self.assertEqual(entry["open_url"], f"http://127.0.0.1:{LIVE_ADMIN_PORT}/setup")

    def test_other_agents_keep_their_manifest_ports(self):
        with mock.patch.object(server, "ADMIN_WEB_SERVICE_PORT", LIVE_ADMIN_PORT):
            entry = server._apply_agent_frontend_route_policy(_notes_manifest_entry(), cfg={})

        self.assertEqual(entry["proxy_port"], 8094)
        self.assertNotIn(str(LIVE_ADMIN_PORT), "".join(str(value) for value in entry.values()))

    def test_browser_and_website_routes_agree_on_the_live_admin_port(self):
        registry = {"frontends": [_admin_manifest_entry(), _notes_manifest_entry()]}
        with mock.patch.object(server, "ADMIN_WEB_SERVICE_PORT", LIVE_ADMIN_PORT), \
                mock.patch.object(server, "load_frontend_registry", lambda: registry):
            browser_routes = server._build_browser_port_routes_from_frontend_registry()
            website_routes = server._build_agent_website_routes_from_frontend_registry()

        self.assertEqual([route["route_id"] for route in browser_routes], ["agent:admin_agent"])
        browser_admin = browser_routes[0]
        website_admin = next(route for route in website_routes if route["route_id"] == "agent:admin_agent")
        for route in (browser_admin, website_admin):
            self.assertEqual(route["port"], LIVE_ADMIN_PORT)
            self.assertEqual(route["local_url"], LIVE_ADMIN_URL)
            self.assertEqual(route["server_local_url"], LIVE_ADMIN_URL)
            self.assertEqual(route["open_url"], LIVE_ADMIN_URL)


if __name__ == "__main__":
    unittest.main()
