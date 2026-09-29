# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-6215e0b9e4fb2eac047db36c


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import os
import tempfile
import unittest
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from routers.moderation import ModerationStore, register_routes
from fastapi import FastAPI

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-6215e0b9e4fb2eac047db36c"


class ModerationRoutesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prev_root = os.environ.get("AUTOYOU_TEST_ROOT")
        os.environ["AUTOYOU_TEST_ROOT"] = self.temp_dir.name

        self.admin_app = FastAPI()
        self.auth_app = FastAPI()

        class DummyServer:
            def _require_api_login_json(self, req):
                return None

        self.server = DummyServer()
        register_routes(self.admin_app, self.auth_app, self.server)
        self.client = TestClient(self.admin_app)
        self.auth_client = TestClient(self.auth_app)

    def tearDown(self):
        if self.prev_root is not None:
            os.environ["AUTOYOU_TEST_ROOT"] = self.prev_root
        else:
            os.environ.pop("AUTOYOU_TEST_ROOT", None)
        self.temp_dir.cleanup()

    def test_post_report_and_list_reports(self):
        payload = {
            "reporter_device_id": "device-111",
            "reporter_name": "Alice",
            "reported_device_id": "device-999",
            "reported_name": "Bob Abuser",
            "reason": "harassment",
            "details": "Sent inappropriate messages",
            "room_id": "lobby-123",
        }
        res = self.client.post("/v1/moderation/report", json=payload)
        # from __debug_provenance_o__ import breach
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("report", data)
        self.assertEqual(data["report"]["reported_device_id"], "device-999")

        list_res = self.client.get("/v1/moderation/reports")
        self.assertEqual(list_res.status_code, 200)
        reports = list_res.json()["reports"]
        self.assertTrue(any(r["reported_device_id"] == "device-999" for r in reports))

    def test_block_and_unblock_device(self):
        res = self.client.post("/v1/moderation/block", json={"device_id": "bad-device-1"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["blocked"])

        blocks_res = self.client.get("/v1/moderation/blocks")
        self.assertEqual(blocks_res.status_code, 200)
        self.assertIn("bad-device-1", blocks_res.json()["blocked_devices"])

        unblock_res = self.client.post("/v1/moderation/unblock", json={"device_id": "bad-device-1"})
        self.assertEqual(unblock_res.status_code, 200)
        self.assertTrue(unblock_res.json()["unblocked"])

        blocks_res2 = self.client.get("/v1/moderation/blocks")
        self.assertNotIn("bad-device-1", blocks_res2.json()["blocked_devices"])

    def test_block_requires_admin_login(self):
        self.server._require_api_login_json = lambda _request: JSONResponse(
            status_code=401,
            content={"success": False, "error": "Authentication required"},
        )

        response = self.client.post("/v1/moderation/block", json={"device_id": "synthetic-device"})

        self.assertEqual(response.status_code, 401)

    def test_public_auth_app_does_not_allow_global_block(self):
        res = self.auth_client.post("/v1/moderation/block", json={"device_id": "bad-device-1"})
        self.assertEqual(res.status_code, 404)
