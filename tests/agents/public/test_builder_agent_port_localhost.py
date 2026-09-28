# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-14272129a85c6eef98e208fa


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-14272129a85c6eef98e208fa"

import asyncio
import json
import os
import sys
import unittest

from fastapi.responses import JSONResponse

from tests.support.paths import ensure_repo_on_path
ensure_repo_on_path()

import types


async def _async_value(value):
    return value


try:
    import autoyou_agents.internet_agent.internet_tool  # noqa: F401
except Exception:
    if "autoyou_agents.internet_agent.internet_tool" not in sys.modules:
        internet_tool = types.ModuleType("autoyou_agents.internet_agent.internet_tool")

        async def _empty_async_result(*args, **kwargs):
            return {}

        def _register_fastapi_cleanup(*args, **kwargs):
            return None

        class _DummyInternetTool:
            pass

        class _DummyPlaywrightDriverManager:
            pass

        def _resolve_headless_override(*args, **kwargs):
            return None

        internet_tool.InternetTool = _DummyInternetTool
        internet_tool.PlaywrightDriverManager = _DummyPlaywrightDriverManager
        internet_tool._resolve_headless_override = _resolve_headless_override
        internet_tool.internet_search = _empty_async_result
        internet_tool.scrape_website = _empty_async_result
        internet_tool.take_screenshot = _empty_async_result
        internet_tool.navigate_page = _empty_async_result
        internet_tool.register_fastapi_cleanup = _register_fastapi_cleanup
        sys.modules["autoyou_agents.internet_agent.internet_tool"] = internet_tool

if "telegram" not in sys.modules:
    telegram_module = types.ModuleType("telegram")

    class _DummyUpdate:
        pass

    telegram_module.Update = _DummyUpdate
    sys.modules["telegram"] = telegram_module

if "telegram.error" not in sys.modules:
    telegram_error_module = types.ModuleType("telegram.error")

    class _DummyTelegramError(Exception):
        pass

    telegram_error_module.BadRequest = _DummyTelegramError
    telegram_error_module.NetworkError = _DummyTelegramError
    telegram_error_module.RetryAfter = _DummyTelegramError
    telegram_error_module.TimedOut = _DummyTelegramError
    sys.modules["telegram.error"] = telegram_error_module

if "telegram.ext" not in sys.modules:
    telegram_ext_module = types.ModuleType("telegram.ext")

    class _DummyContextTypes:
        DEFAULT_TYPE = object

    telegram_ext_module.Application = object
    telegram_ext_module.CommandHandler = object
    telegram_ext_module.ContextTypes = _DummyContextTypes
    telegram_ext_module.ConversationHandler = object
    telegram_ext_module.MessageHandler = object
    telegram_ext_module.filters = object()
    sys.modules["telegram.ext"] = telegram_ext_module

import server


class _DummyClient:
    def __init__(self, host: str):
        self.host = host


class _DummyRequest:
    def __init__(self, host: str, payload: dict):
        self.client = _DummyClient(host)
        self._payload = payload

    async def json(self):
        return self._payload


class BuilderAgentPortLocalhostTest(unittest.TestCase):
    def test_is_loopback_client_host(self):
        cases = [
            ("127.0.0.1", True),
            ("localhost", True),
            ("::1", True),
            ("::ffff:127.0.0.1", True),
            ("192.168.1.10", False),
            ("203.0.113.5", False),
            # Starlette's TestClient reports its peer host as the literal string
            # "testclient"; the server intentionally treats it as loopback so the
            # in-process test harness exercises local/trusted paths (rate-limit
            # trusted bridge, CSRF autopair, loopback-gated endpoints). No real
            # network peer ever has host "testclient", so this is safe in prod.
            ("testclient", True),
        ]
        for host, expected in cases:
            with self.subTest(host=host):
                self.assertEqual(server._is_loopback_client_host(host), expected)

    def test_admin_builder_register_agent_port_rejects_non_loopback(self):
        original_ports = dict(server.STATE.dynamic_agent_proxy_ports)
        original_sync = server._sync_frontend_registry_from_builder_payload
        server.STATE.dynamic_agent_proxy_ports = {}
        server._sync_frontend_registry_from_builder_payload = lambda payload: None

        try:
            request = _DummyRequest("203.0.113.5", {"agent_name": "demo_agent", "port": 8031})
            response = asyncio.run(server.admin_builder_register_agent_port(request))

            self.assertIsInstance(response, JSONResponse)
            self.assertEqual(response.status_code, 403)
            body = json.loads(response.body)
            self.assertFalse(body["success"])
            self.assertIn("localhost", body["error"].lower())
            self.assertEqual(server.STATE.dynamic_agent_proxy_ports, {})
        finally:
            server.STATE.dynamic_agent_proxy_ports = original_ports
            server._sync_frontend_registry_from_builder_payload = original_sync

    def test_admin_builder_register_agent_port_accepts_loopback(self):
        original_ports = dict(server.STATE.dynamic_agent_proxy_ports)
        original_sync = server._sync_frontend_registry_from_builder_payload
        original_builder_payload = server._build_agent_builder_listing_payload
        server.STATE.dynamic_agent_proxy_ports = {}
        server._sync_frontend_registry_from_builder_payload = lambda payload: None
        server._build_agent_builder_listing_payload = lambda: {
            "status": "success",
            "agents": ["demo_agent"],
            "proxy_ports": {"demo_agent": 8031},
        }

        try:
            request = _DummyRequest("127.0.0.1", {"agent_name": "demo_agent", "port": 8031})
            response = asyncio.run(server.admin_builder_register_agent_port(request))

            self.assertTrue(response["success"])
            self.assertEqual(response["agent_name"], "demo_agent")
            self.assertEqual(response["port"], 8031)
            self.assertEqual(response["proxy_path"], "/agent/demo_agent/")
            self.assertEqual(server.STATE.dynamic_agent_proxy_ports["demo_agent"], 8031)
        finally:
            server.STATE.dynamic_agent_proxy_ports = original_ports
            server._sync_frontend_registry_from_builder_payload = original_sync
            server._build_agent_builder_listing_payload = original_builder_payload

    def test_frontend_registry_sync_falls_back_to_running_managed_ports(self):
        original_config = server.STATE.config
        original_ports = dict(server.STATE.dynamic_agent_proxy_ports)
        server.STATE.config = {
            "agent_frontends": {"hosting_agent": True},
            "autoyou_page": {"port": 8067, "default_agent_website": "hosting_agent"},
        }
        server.STATE.dynamic_agent_proxy_ports = {"hosting_agent": 8089}

        try:
            server._sync_frontend_registry_from_builder_payload(
                {
                    "status": "success",
                    "frontends": [],
                    "proxy_ports": {"hosting_agent": 8089},
                    "autoyou_browser_base_url": "http://127.0.0.1:8067",
                }
            )
            registry = server.load_frontend_registry()
            hosting_entry = next(
                item for item in registry.get("frontends", []) if item.get("agent_name") == "hosting_agent"
            )
            self.assertEqual(hosting_entry["proxy_port"], 8089)
            self.assertEqual(registry["default_agent"], "hosting_agent")
        finally:
            server.STATE.config = original_config
            server.STATE.dynamic_agent_proxy_ports = original_ports

    def test_browser_port_routes_use_direct_forward_port(self):
        original_load = server.load_frontend_registry
        server.load_frontend_registry = lambda: {
            "frontends": [
                {
                    "agent_name": "admin_agent",
                    "title": "Admin UI",
                    "description": "Direct admin dashboard",
                    "proxy_port": 8001,
                    "direct_forward_port": 8001,
                    "entry_path": "/",
                    "local_url": "http://127.0.0.1:8001/",
                },
                {
                    "agent_name": "notes_agent",
                    "title": "Notes",
                    "description": "Path-routed UI",
                    "proxy_port": 8094,
                    "entry_path": "/",
                },
            ]
        }
        try:
            routes = server._build_browser_port_routes_from_frontend_registry()
            self.assertEqual(
                routes,
                [
                    {
                        "route_id": "agent:admin_agent",
                        "agent_name": "admin_agent",
                        "kind": "agent_frontend",
                        "title": "Admin UI",
                        "description": "Direct admin dashboard",
                        "port": 8001,
                        "local_url": "http://127.0.0.1:8001/",
                        "path": "/",
                        "proxy_path": "/agent/admin_agent/",
                        "launch_path": "/agent/admin_agent/",
                        "launch_url": None,
                        "open_url": "http://127.0.0.1:8001/",
                        "route_mode": "direct_forward",
                        "direct_forward_port": 8001,
                        "uses_direct_forward_port": True,
                        "websocket_enabled": False,
                    }
                ],
            )
            website_routes = server._build_agent_website_routes_from_frontend_registry()
            self.assertEqual([route["route_id"] for route in website_routes], ["agent:admin_agent", "agent:notes_agent"])
            self.assertEqual(website_routes[0]["route_mode"], "direct_forward")
            self.assertEqual(website_routes[0]["open_url"], "http://127.0.0.1:8001/")
            self.assertEqual(website_routes[1]["route_mode"], "path_proxy")
            self.assertEqual(website_routes[1]["open_url"], "/agent/notes_agent/")
            self.assertEqual(website_routes[1]["path_proxy_url"], "http://127.0.0.1:8067/agent/notes_agent/")
            self.assertEqual(website_routes[1]["page_service_url"], "http://127.0.0.1:8067")
            self.assertIsNone(website_routes[1]["local_url"])
            self.assertEqual(website_routes[1]["server_local_url"], "http://127.0.0.1:8094/")
        finally:
            server.load_frontend_registry = original_load

    def test_agent_frontend_route_mode_can_promote_agent_website_to_direct_port(self):
        original_load = server.load_frontend_registry
        server.load_frontend_registry = lambda: {
            "frontends": [
                {
                    "agent_name": "notes_agent",
                    "title": "Notes",
                    "description": "Notes website",
                    "proxy_port": 8094,
                    "entry_path": "/",
                }
            ]
        }
        cfg = {"agent_frontends": {"notes_agent": {"enabled": True, "route_mode": "direct_forward"}}}
        try:
            routes = server._build_browser_port_routes_from_frontend_registry(cfg=cfg)
            self.assertEqual(len(routes), 1)
            self.assertEqual(routes[0]["route_id"], "agent:notes_agent")
            self.assertEqual(routes[0]["port"], 8094)
            self.assertEqual(routes[0]["open_url"], "http://127.0.0.1:8094/")
            self.assertEqual(routes[0]["route_mode"], "direct_forward")
            self.assertTrue(routes[0]["uses_direct_forward_port"])

            website_routes = server._build_agent_website_routes_from_frontend_registry(cfg=cfg)
            self.assertEqual(website_routes[0]["route_mode"], "direct_forward")
            self.assertEqual(website_routes[0]["open_url"], "http://127.0.0.1:8094/")
            self.assertEqual(website_routes[0]["direct_forward_port"], 8094)
        finally:
            server.load_frontend_registry = original_load

    def test_structured_agent_frontend_config_preserves_enabled_false(self):
        cfg = {"agent_frontends": {"notes_agent": {"enabled": False, "route_mode": "direct_forward"}}}
        self.assertFalse(server._get_agent_frontend_enabled("notes_agent", cfg=cfg))

        updated = server._set_agent_frontend_enabled("notes_agent", True, cfg=cfg)
        self.assertEqual(
            updated["agent_frontends"]["notes_agent"],
            {"enabled": True, "route_mode": "direct_forward"},
        )

    def test_browser_port_routes_append_advertised_websites_without_overriding_frontends(self):
        original_load = server.load_frontend_registry
        server.load_frontend_registry = lambda: {
            "frontends": [
                {
                    "agent_name": "admin_agent",
                    "title": "Admin UI",
                    "description": "Direct admin dashboard",
                    "direct_forward_port": 8001,
                    "entry_path": "/",
                    "local_url": "http://127.0.0.1:8001/",
                }
            ]
        }
        cfg = {
            "autoyou_page": {
                "port": 8067,
                "custom_forward_enabled": False,
                "custom_forward_port": 8067,
                "advertised_websites": [
                    {
                        "port": 8094,
                        "label": "Notes",
                        "description": "AutoyouNote",
                        "target_url": "http://192.168.1.184:8094/",
                        "enabled": True,
                    },
                    {
                        "port": 8001,
                        "label": "Duplicate admin",
                        "description": "Should be ignored because frontend already owns the port",
                        "target_url": "http://127.0.0.1:8001/",
                        "enabled": True,
                    },
                ],
            },
            "agent_frontends": {"admin_agent": True},
            "admin": {"frontend_proxy_enabled": True},
        }
        try:
            routes = server._build_browser_port_routes(cfg)
            self.assertEqual([route["port"] for route in routes], [8001, 8094])
            self.assertEqual(routes[1]["kind"], "advertised")
            self.assertEqual(routes[1]["title"], "Notes")
            self.assertEqual(routes[1]["local_url"], "http://127.0.0.1:8094/")
        finally:
            server.load_frontend_registry = original_load

    def test_ads_watching_agent_keeps_its_direct_port_after_legacy_route_setting(self):
        original_load = server.load_frontend_registry
        server.load_frontend_registry = lambda: {
            "frontends": [
                {
                    "agent_name": "ads_watching_agent",
                    "title": "Ads Watching",
                    "description": "Direct ads website",
                    "proxy_port": 8088,
                    "entry_path": "/",
                }
            ]
        }
        cfg = {
            "autoyou_page": {
                "port": 8067,
                "custom_forward_enabled": False,
                "custom_forward_port": 8067,
            },
            # Older installations could persist the old path-proxy default.
            "agent_frontends": {
                "ads_watching_agent": {"enabled": True, "route_mode": "path_proxy"},
            },
        }
        try:
            routes = server._build_browser_port_routes(cfg)
            self.assertEqual(len(routes), 1)
            self.assertEqual(routes[0]["port"], 8088)
            self.assertEqual(routes[0]["local_url"], "http://127.0.0.1:8088/")
            self.assertEqual(routes[0]["route_mode"], "direct_forward")
            self.assertTrue(routes[0]["uses_direct_forward_port"])
            with self.assertRaises(ValueError):
                server._set_agent_frontend_route_mode(
                    "ads_watching_agent",
                    "path_proxy",
                    cfg=cfg,
                )
        finally:
            server.load_frontend_registry = original_load

    def test_browser_port_routes_do_not_advertise_disabled_remote_admin(self):
        original_load = server.load_frontend_registry
        server.load_frontend_registry = lambda: {
            "frontends": [
                {
                    "agent_name": "admin_agent",
                    "title": "Admin UI",
                    "description": "Direct admin dashboard",
                    "direct_forward_port": 8001,
                    "entry_path": "/",
                    "local_url": "http://127.0.0.1:8001/",
                }
            ]
        }
        cfg = {
            "autoyou_page": {
                "port": 8067,
                "custom_forward_enabled": False,
                "custom_forward_port": 8067,
                "advertised_websites": [
                    {
                        "port": 8001,
                        "label": "Admin duplicate",
                        "description": "Must not leak when remote admin is disabled",
                        "target_url": "http://127.0.0.1:8001/",
                        "enabled": True,
                    },
                    {
                        "port": 3100,
                        "label": "Dashboard",
                        "description": "Allowed app",
                        "target_url": "http://127.0.0.1:3100/",
                        "enabled": True,
                    },
                ],
            },
            "agent_frontends": {"admin_agent": False},
            "admin": {"frontend_proxy_enabled": False},
        }
        try:
            routes = server._build_browser_port_routes(cfg)
            self.assertEqual([route["port"] for route in routes], [3100])
            self.assertEqual(routes[0]["kind"], "advertised")
        finally:
            server.load_frontend_registry = original_load

    def test_enabled_admin_website_uses_active_admin_port(self):
        original_load = server.load_frontend_registry
        original_port = server.ADMIN_WEB_SERVICE_PORT
        server.load_frontend_registry = lambda: {
            "frontends": [{
                "agent_name": "admin_agent",
                "title": "Admin UI",
                "direct_forward_port": 8001,
                "local_url": "http://127.0.0.1:8001/",
                "entry_path": "/",
            }]
        }
        server.ADMIN_WEB_SERVICE_PORT = 50927
        cfg = {"admin": {"frontend_proxy_enabled": True}}
        try:
            port_route = server._build_browser_port_routes_from_frontend_registry(cfg)[0]
            website_route = server._build_agent_website_routes_from_frontend_registry(cfg)[0]
            self.assertEqual(port_route["port"], 50927)
            self.assertEqual(port_route["open_url"], "http://127.0.0.1:50927/")
            self.assertEqual(website_route["open_url"], "http://127.0.0.1:50927/")
        finally:
            server.ADMIN_WEB_SERVICE_PORT = original_port
            server.load_frontend_registry = original_load

    def test_browser_port_routes_canonicalize_main_ai_port_to_dev_ui(self):
        original_load = server.load_frontend_registry
        original_main_port = getattr(server.STATE, "main_server_port", 8081)
        server.load_frontend_registry = lambda: {"frontends": []}
        server.STATE.main_server_port = 8081
        cfg = {
            "autoyou_page": {
                "advertised_websites": [
                    {
                        "port": 8081,
                        "label": "Google ADK",
                        "description": "Embedded AI runtime",
                        "target_url": "http://127.0.0.1:8081/",
                        "enabled": True,
                    }
                ],
            }
        }
        try:
            routes = server._build_browser_port_routes(cfg)
            self.assertEqual(routes[0]["local_url"], "http://127.0.0.1:8081/dev-ui/")
            self.assertEqual(routes[0]["path"], "/dev-ui/")
            shortcuts = server._build_browser_shortcuts(cfg)
            self.assertEqual(shortcuts[0]["url"], "http://127.0.0.1:8081/dev-ui/")
        finally:
            server.load_frontend_registry = original_load
            server.STATE.main_server_port = original_main_port

    def test_browser_shortcuts_preserve_advertised_query_string(self):
        cfg = {
            "autoyou_page": {
                "advertised_websites": [
                    {
                        "port": 8081,
                        "label": "Google ADK",
                        "description": "Embedded AI runtime",
                        "target_url": "http://127.0.0.1:8081/dev-ui/?app=autoyou_agents",
                        "enabled": True,
                    }
                ],
            }
        }

        shortcuts = server._build_browser_shortcuts(cfg)

        self.assertEqual(
            shortcuts[0]["url"],
            "http://127.0.0.1:8081/dev-ui/?app=autoyou_agents",
        )

    def test_browser_shortcuts_include_server_managed_bookmarks(self):
        cfg = {
            "autoyou_page": {
                "advertised_websites": [
                    {
                        "port": 8094,
                        "label": "Notes",
                        "description": "Local notes UI",
                        "target_url": "http://127.0.0.1:8094/",
                        "enabled": True,
                    }
                ],
                "bookmarks": [
                    {
                        "id": "docs",
                        "title": "Docs",
                        "description": "External documentation",
                        "url": "https://example.com/docs?ref=autoyou",
                        "enabled": True,
                    },
                    {
                        "id": "disabled",
                        "title": "Disabled",
                        "url": "https://example.net/",
                        "enabled": False,
                    },
                ],
            }
        }

        shortcuts = server._build_browser_shortcuts(cfg)

        self.assertEqual([shortcut["id"] for shortcut in shortcuts], ["advertised:8094", "bookmark:docs"])
        self.assertEqual(shortcuts[1]["kind"], "bookmark")
        self.assertEqual(shortcuts[1]["title"], "Docs")
        self.assertEqual(shortcuts[1]["url"], "https://example.com/docs?ref=autoyou")

    def test_normalize_bookmark_entries_rejects_non_http_urls(self):
        with self.assertRaises(ValueError):
            server._normalize_bookmark_entries([{"title": "Bad", "url": "javascript:alert(1)"}])

    def test_normalize_client_loopback_target_rewrites_advertised_website_to_custom_upstream(self):
        rewritten = server._normalize_client_loopback_target(
            "http://127.0.0.1:8094/notes",
            proxy_target="http://127.0.0.1:8067",
            websocket=False,
            advertised_websites=[
                {
                    "port": 8094,
                    "label": "Notes",
                    "description": "",
                    "target_url": "http://192.168.1.184:8094/",
                    "enabled": True,
                }
            ],
        )

        self.assertEqual(rewritten, "http://192.168.1.184:8094/notes")

    def test_normalize_client_loopback_target_keeps_path_proxy_on_page_service(self):
        original_config = server.STATE.config
        try:
            server.STATE.config = {
                "autoyou_page": {"port": 8068, "custom_forward_enabled": True, "custom_forward_port": 9000},
                "agent_frontends": {"notes_agent": {"enabled": True, "route_mode": "path_proxy"}},
            }
            rewritten = server._normalize_client_loopback_target(
                "http://127.0.0.1:9000/agent/notes_agent/api/notes?limit=5",
                proxy_target="http://127.0.0.1:9000",
                websocket=False,
                advertised_websites=[],
            )

            self.assertEqual(rewritten, "http://127.0.0.1:8068/agent/notes_agent/api/notes?limit=5")
        finally:
            server.STATE.config = original_config

    def test_normalize_client_loopback_target_blocks_unauthorized_ports(self):
        # Default proxy_target port is 9000
        # Accessing 127.0.0.1:8001 (admin app) should be blocked and raise ValueError
        import os
        original_test_root = os.environ.pop("AUTOYOU_TEST_ROOT", None)
        try:
            with self.assertRaises(ValueError) as ctx:
                server._normalize_client_loopback_target(
                    "http://127.0.0.1:8001/admin",
                    proxy_target="http://127.0.0.1:9000",
                    websocket=False,
                    advertised_websites=[],
                )
            self.assertIn("blocked by safety policy", str(ctx.exception))
        finally:
            if original_test_root is not None:
                os.environ["AUTOYOU_TEST_ROOT"] = original_test_root

    def test_normalize_client_loopback_target_blocks_dns_rebind_loopback_ip(self):
        # We will mock socket.getaddrinfo to return loopback IP for a domain
        import socket
        import os
        original_getaddrinfo = socket.getaddrinfo
        original_test_root = os.environ.pop("AUTOYOU_TEST_ROOT", None)
        
        def mock_getaddrinfo(host, port, *args, **kwargs):
            if host == "localhost.attacker.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port or 0))]
            return original_getaddrinfo(host, port, *args, **kwargs)
            
        socket.getaddrinfo = mock_getaddrinfo
        try:
            # localhost.attacker.com:8001 points to loopback and 8001 is not allowed
            with self.assertRaises(ValueError) as ctx:
                server._normalize_client_loopback_target(
                    "http://localhost.attacker.com:8001/admin",
                    proxy_target="http://127.0.0.1:9000",
                    websocket=False,
                    advertised_websites=[],
                )
            self.assertIn("blocked by safety policy", str(ctx.exception))
        finally:
            socket.getaddrinfo = original_getaddrinfo
            if original_test_root is not None:
                os.environ["AUTOYOU_TEST_ROOT"] = original_test_root


    def test_admin_install_agent_rejects_workspace_draft_in_compiled_runtime(self):
        original_require_login = server._require_api_login
        original_is_compiled = server.is_compiled
        original_builder_payload = server._build_agent_builder_listing_payload
        server._require_api_login = lambda request: None
        server.is_compiled = lambda: True
        server._build_agent_builder_listing_payload = lambda: {
            "status": "success",
            "agent_details": {
                "custom_agent": {
                    "agent_name": "custom_agent",
                    "runtime_blocked": True,
                }
            },
        }

        try:
            request = _DummyRequest("127.0.0.1", {"agent_name": "custom_agent"})
            response = asyncio.run(server.admin_install_agent(request))

            self.assertIsInstance(response, JSONResponse)
            self.assertEqual(response.status_code, 409)
            body = json.loads(response.body)
            self.assertFalse(body["success"])
            self.assertIn("Workspace draft only", body["error"])
            self.assertEqual(body["detail"]["agent_name"], "custom_agent")
            self.assertEqual(body["payload"]["status"], "success")
        finally:
            server._require_api_login = original_require_login
            server.is_compiled = original_is_compiled
            server._build_agent_builder_listing_payload = original_builder_payload

    def test_admin_install_browser_agent_succeeds_in_compiled_build(self):
        """browser_agent is no longer a private package, so a compiled build can
        install it. Installability is decided by can_install_agent_in_runtime
        (browser_agent is now in BUILTIN_AGENT_PACKAGE_NAMES), not by the
        runtime_blocked hint in the listing payload."""
        original_require_login = server._require_api_login
        original_is_compiled = server.is_compiled
        original_builder_payload = server._build_agent_builder_listing_payload
        server._require_api_login = lambda request: None
        server.is_compiled = lambda: True
        server._build_agent_builder_listing_payload = lambda: {
            "status": "success",
            "agent_details": {
                "browser_agent": {
                    "agent_name": "browser_agent",
                    "runtime_blocked": True,
                }
            },
        }

        try:
            request = _DummyRequest("127.0.0.1", {"agent_name": "browser_agent"})
            response = asyncio.run(server.admin_install_agent(request))

            self.assertNotIsInstance(response, JSONResponse)
            self.assertTrue(response["success"])
            self.assertEqual(response["agent_name"], "browser_agent")
            self.assertNotIn("error", response)
        finally:
            server._require_api_login = original_require_login
            server.is_compiled = original_is_compiled
            server._build_agent_builder_listing_payload = original_builder_payload

    def test_admin_agent_workbench_detail_returns_selected_detail(self):
        original_require_login = server._require_api_login
        original_detail_payload = server._build_agent_workbench_detail_payload
        server._require_api_login = lambda request: None
        server._build_agent_workbench_detail_payload = lambda agent_name: {
            "status": "success",
            "agent_name": agent_name,
            "detail": {"agent_name": agent_name, "display_name": "Notes Agent"},
        }

        try:
            request = _DummyRequest("127.0.0.1", {})
            response = asyncio.run(server.admin_builder_status("notes_agent", request))

            self.assertTrue(response["success"])
            self.assertEqual(response["agent_name"], "notes_agent")
            self.assertEqual(response["detail"]["display_name"], "Notes Agent")
        finally:
            server._require_api_login = original_require_login
            server._build_agent_workbench_detail_payload = original_detail_payload

    def test_admin_agent_workbench_test_rejects_packaged_runtime(self):
        original_require_login = server._require_api_login
        original_is_compiled = server.is_compiled
        server._require_api_login = lambda request: None
        server.is_compiled = lambda: True

        try:
            request = _DummyRequest("127.0.0.1", {"agent_name": "custom_workspace_draft"})
            response = asyncio.run(server.admin_install_agent(request))

            self.assertIsInstance(response, JSONResponse)
            self.assertEqual(response.status_code, 409)
            body = json.loads(response.body)
            self.assertFalse(body["success"])
            self.assertIn("workspace draft", body["error"].lower())
        finally:
            server._require_api_login = original_require_login
            server.is_compiled = original_is_compiled

    def test_admin_install_uninstall_manages_bookmarks(self):
        import copy
        original_require_login = server._require_api_login
        original_can_install = server.can_install_agent_in_runtime
        original_refresh_registry = server.refresh_agent_install_registry
        original_builder_payload = server._build_agent_builder_listing_payload
        original_sync_backends = server.sync_managed_frontend_backends
        original_persist = server._persist_state_config
        original_config = server.STATE.config
        original_write_block = server._config_write_block_reason
        original_config_store = server.STATE.config_store

        server._require_api_login = lambda request: None
        server._config_write_block_reason = lambda: None
        # _loaded_config_for_update() lives in core_server.state and calls that module's own
        # _config_write_block_reason, so patching the re-export on `server` does not reach it.
        # Satisfy the real guard instead: a keystore-backed store counts as loaded and writable.
        server.STATE.config_store = server.CONFIG_STORE_KEYSTORE
        server.can_install_agent_in_runtime = lambda agent_name, compiled=False: True
        server.refresh_agent_install_registry = lambda agents_root=None: {"agents": {"my_custom_agent": {}}}
        server._build_agent_builder_listing_payload = lambda: {
            "status": "success",
            "frontends": [{"agent_name": "my_custom_agent", "has_frontend": True}],
            "agent_details": {},
        }
        
        async def fake_sync():
            pass
        server.sync_managed_frontend_backends = fake_sync

        # Mock config storage
        config_store = {"server": {"name": "AutoYou-Server"}, "autoyou_page": {"bookmarks": []}}
        server.STATE.config = config_store
        
        def fake_persist(config, *args, **kwargs):
            nonlocal config_store
            copied = copy.deepcopy(config)
            config_store.clear()
            config_store.update(copied)
            server.STATE.config = config_store
            return {}
        
        server._persist_state_config = fake_persist

        # Mock patch_root_agent
        import autoyou_agents.agent_builder_agent.agent as builder_agent
        original_patch = builder_agent.patch_root_agent
        builder_agent.patch_root_agent = lambda agent_name, desc: {"status": "success"}

        try:
            # Test Install
            request = _DummyRequest("127.0.0.1", {"agent_name": "my_custom_agent"})
            install_response = asyncio.run(server.admin_install_agent(request))
            self.assertTrue(install_response["requires_restart"])
            self.assertIn("Restart AutoYou AI", install_response["message"])
            
            # Check bookmark was added
            bookmarks = config_store.get("autoyou_page", {}).get("bookmarks", [])
            self.assertEqual(len(bookmarks), 1)
            self.assertEqual(bookmarks[0]["id"], "my_custom_agent")
            self.assertEqual(bookmarks[0]["title"], "My Custom")
            self.assertIn("/agent/my_custom_agent/", bookmarks[0]["url"])

            # Mock set_agent_installed for uninstall
            original_set_installed = server.set_agent_installed
            server.set_agent_installed = lambda agent_name, installed, source=None, agents_root=None: None

            try:
                # Test Uninstall
                uninstall_request = _DummyRequest("127.0.0.1", {"agent_name": "my_custom_agent"})
                uninstall_response = asyncio.run(server.admin_uninstall_agent(uninstall_request))
                self.assertTrue(uninstall_response["requires_restart"])
                self.assertIn("Restart AutoYou AI", uninstall_response["message"])
                
                # Check bookmark was removed
                bookmarks_after = config_store.get("autoyou_page", {}).get("bookmarks", [])
                self.assertEqual(len(bookmarks_after), 0)
            finally:
                server.set_agent_installed = original_set_installed

        finally:
            server._require_api_login = original_require_login
            server._config_write_block_reason = original_write_block
            server.STATE.config_store = original_config_store
            server.can_install_agent_in_runtime = original_can_install
            server.refresh_agent_install_registry = original_refresh_registry
            server._build_agent_builder_listing_payload = original_builder_payload
            server.sync_managed_frontend_backends = original_sync_backends
            server._persist_state_config = original_persist
            server.STATE.config = original_config
            builder_agent.patch_root_agent = original_patch

    def test_tunnelmole_website_hosting_payload_selects_page_service(self):
        original_config = server.STATE.config
        original_routes = server._build_agent_website_routes
        server.STATE.config = {
            "tunnelmole": {"website_hosting": {"enabled": True, "agent_name": "notes_agent"}},
            "autoyou_page": {"port": 8067},
            "cloud": {"email": "user@example.test"},
        }
        server._build_agent_website_routes = lambda cfg=None: [
            {
                "agent_name": "notes_agent",
                "kind": "agent_frontend",
                "title": "Notes",
                "description": "Private notes website",
                "proxy_path": "/agent/notes_agent/",
            },
            {
                "agent_name": "admin_agent",
                "kind": "agent_frontend",
                "title": "Admin",
                "proxy_path": "/agent/admin_agent/",
            },
        ]
        try:
            payload = server._build_tunnelmole_website_hosting_payload()
            self.assertTrue(payload["enabled"])
            self.assertTrue(payload["cloud_signed_in"])
            self.assertEqual(payload["selected"]["agent_name"], "notes_agent")
            self.assertEqual([item["agent_name"] for item in payload["options"]], ["notes_agent"])
            self.assertEqual(server._get_tunnelmole_hosted_website_port(server.STATE.config), 8067)
        finally:
            server.STATE.config = original_config
            server._build_agent_website_routes = original_routes

    def test_admin_set_tunnelmole_website_hosting_persists_selection(self):
        import copy
        original_require_login = server._require_api_login
        original_write_block = server._config_write_block_reason
        original_config_store = server.STATE.config_store
        original_config = server.STATE.config
        original_routes = server._build_agent_website_routes
        original_persist = server._persist_state_config
        original_sync = server.sync_managed_frontend_backends
        original_page_available = server.AUTOYOU_PAGE_SERVICE_AVAILABLE
        original_page_running = server.is_autoyou_page_service_running
        original_page_start = server.start_autoyou_page_service_background
        original_service = server.STATE.tunnelmole_service
        original_paid_entitlement = server._has_tunnelmole_paid_entitlement

        fake_service = server.TunnelmoleService(8123)
        saved = {}

        async def fake_sync():
            return {"notes_agent": 8094}

        async def fake_page_running():
            return True

        async def fake_page_start():
            return None

        def fake_persist(config, *args, **kwargs):
            saved.clear()
            saved.update(copy.deepcopy(config))
            server.STATE.config = saved
            return "test"

        server._require_api_login = lambda request: None
        server._config_write_block_reason = lambda: None
        # _loaded_config_for_update() lives in core_server.state and calls that module's own
        # _config_write_block_reason, so patching the re-export on `server` does not reach it.
        # Satisfy the real guard instead: a keystore-backed store counts as loaded and writable.
        server.STATE.config_store = server.CONFIG_STORE_KEYSTORE
        server.STATE.config = {
            "tunnelmole": {"website_hosting": {"enabled": False, "agent_name": ""}},
            "autoyou_page": {"port": 8067},
            "cloud": {},
        }
        server._build_agent_website_routes = lambda cfg=None: [
            {
                "agent_name": "notes_agent",
                "kind": "agent_frontend",
                "title": "Notes",
                "proxy_path": "/agent/notes_agent/",
            }
        ]
        server._persist_state_config = fake_persist
        server.sync_managed_frontend_backends = fake_sync
        server.AUTOYOU_PAGE_SERVICE_AVAILABLE = True
        server.is_autoyou_page_service_running = fake_page_running
        server.start_autoyou_page_service_background = fake_page_start
        server.STATE.tunnelmole_service = fake_service
        server._has_tunnelmole_paid_entitlement = lambda: _async_value(True)

        try:
            request = _DummyRequest(
                "127.0.0.1",
                {"agent_name": "notes_agent", "enabled": True, "auto_start_on_boot": True},
            )
            response = asyncio.run(server.admin_set_tunnelmole_website_hosting(request))
            body = json.loads(response.body)

            self.assertTrue(body["success"])
            self.assertTrue(saved["tunnelmole"]["website_hosting"]["enabled"])
            self.assertEqual(saved["tunnelmole"]["website_hosting"]["agent_name"], "notes_agent")
            self.assertTrue(saved["tunnelmole"]["auto_start_on_boot"])
            self.assertEqual(saved["autoyou_page"]["default_agent_website"], "notes_agent")
            self.assertEqual(fake_service.get_status()["website_port"], 8067)
        finally:
            server._require_api_login = original_require_login
            server._config_write_block_reason = original_write_block
            server.STATE.config_store = original_config_store
            server.STATE.config = original_config
            server._build_agent_website_routes = original_routes
            server._persist_state_config = original_persist
            server.sync_managed_frontend_backends = original_sync
            server.AUTOYOU_PAGE_SERVICE_AVAILABLE = original_page_available
            server.is_autoyou_page_service_running = original_page_running
            server.start_autoyou_page_service_background = original_page_start
            server.STATE.tunnelmole_service = original_service
            server._has_tunnelmole_paid_entitlement = original_paid_entitlement

    def test_admin_set_tunnelmole_website_hosting_requires_paid_plan_to_enable(self):
        import copy
        original_require_login = server._require_api_login
        original_write_block = server._config_write_block_reason
        original_config_store = server.STATE.config_store
        original_config = server.STATE.config
        original_routes = server._build_agent_website_routes
        original_persist = server._persist_state_config
        original_paid_entitlement = server._has_tunnelmole_paid_entitlement

        saved = {}

        def fake_persist(config, *args, **kwargs):
            saved.clear()
            saved.update(copy.deepcopy(config))
            server.STATE.config = saved
            return "test"

        server._require_api_login = lambda request: None
        server._config_write_block_reason = lambda: None
        # _loaded_config_for_update() lives in core_server.state and calls that module's own
        # _config_write_block_reason, so patching the re-export on `server` does not reach it.
        # Satisfy the real guard instead: a keystore-backed store counts as loaded and writable.
        server.STATE.config_store = server.CONFIG_STORE_KEYSTORE
        server.STATE.config = {
            "tunnelmole": {"website_hosting": {"enabled": False, "agent_name": ""}},
            "autoyou_page": {"port": 8067},
            "cloud": {},
        }
        server._build_agent_website_routes = lambda cfg=None: [
            {
                "agent_name": "notes_agent",
                "kind": "agent_frontend",
                "title": "Notes",
                "proxy_path": "/agent/notes_agent/",
            }
        ]
        server._persist_state_config = fake_persist
        server._has_tunnelmole_paid_entitlement = lambda: _async_value(False)

        try:
            request = _DummyRequest("127.0.0.1", {"agent_name": "notes_agent", "enabled": True})
            response = asyncio.run(server.admin_set_tunnelmole_website_hosting(request))
            body = json.loads(response.body)

            self.assertEqual(response.status_code, 402)
            self.assertFalse(body["success"])
            self.assertTrue(body["plan_required"])
            self.assertEqual(saved, {})
        finally:
            server._require_api_login = original_require_login
            server._config_write_block_reason = original_write_block
            server.STATE.config_store = original_config_store
            server.STATE.config = original_config
            server._build_agent_website_routes = original_routes
            server._persist_state_config = original_persist
            server._has_tunnelmole_paid_entitlement = original_paid_entitlement


if __name__ == "__main__":
    unittest.main()
