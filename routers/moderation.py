# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-fc0845a1ef53f25958898eec

"""Moderation and UGC safety routes for AutoYou lobbies and peer interactions.

Supports Apple App Store Review Guideline 1.2 and Google Play UGC policies:
- In-app reporting of objectionable content or abusive users.
- Per-user blocking of abusive participants.
- Host and server moderation visibility.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-fc0845a1ef53f25958898eec"


class ModerationStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._reports = []
        self._blocked_devices = set()
        self._loaded = False

    def _resolve_paths(self) -> tuple[Path, Path]:
        test_root = os.getenv("AUTOYOU_TEST_ROOT")
        if test_root:
            base_dir = Path(test_root) / "moderation"
        else:
            base_dir = Path.home() / ".autoyou" / "moderation"
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / "reports.json", base_dir / "blocks.json"

    def _load_if_needed(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            reports_path, blocks_path = self._resolve_paths()
            if reports_path.exists():
                data = json.loads(reports_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._reports = data[-500:]
            if blocks_path.exists():
                data = json.loads(blocks_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._blocked_devices = set(data)
        except Exception:
            pass

    def _save(self):
        try:
            reports_path, blocks_path = self._resolve_paths()
            reports_path.write_text(json.dumps(self._reports[-500:], indent=2), encoding="utf-8")
            blocks_path.write_text(json.dumps(sorted(list(self._blocked_devices)), indent=2), encoding="utf-8")
        except Exception:
            pass

    def add_report(self, report_data: dict) -> dict:
        with self._lock:
            self._load_if_needed()
            report_id = str(uuid.uuid4())
            entry = {
                "id": report_id,
                "reporter_device_id": str(report_data.get("reporter_device_id") or "")[:64],
                "reporter_name": str(report_data.get("reporter_name") or "")[:64],
                "reported_device_id": str(report_data.get("reported_device_id") or "")[:64],
                "reported_name": str(report_data.get("reported_name") or "")[:64],
                "reason": str(report_data.get("reason") or "inappropriate_content")[:64],
                "details": str(report_data.get("details") or "")[:1000],
                "room_id": str(report_data.get("room_id") or "")[:64],
                "timestamp": int(report_data.get("timestamp") or time.time() * 1000),
                "status": "pending",
            }
            self._reports.append(entry)
            if len(self._reports) > 500:
                self._reports = self._reports[-500:]
            self._save()
            return entry

    def list_reports(self) -> list[dict]:
        with self._lock:
            self._load_if_needed()
            return list(self._reports)

    def block_device(self, device_id: str) -> bool:
        if not device_id or not isinstance(device_id, str):
            return False
        with self._lock:
            self._load_if_needed()
            self._blocked_devices.add(device_id)
            self._save()
            return True

    def unblock_device(self, device_id: str) -> bool:
        if not device_id:
            return False
        with self._lock:
            self._load_if_needed()
            if device_id in self._blocked_devices:
                self._blocked_devices.discard(device_id)
                self._save()
                return True
            return False

    def list_blocked(self) -> list[str]:
        with self._lock:
            self._load_if_needed()
            return sorted(list(self._blocked_devices))

    def is_blocked(self, device_id: str) -> bool:
        with self._lock:
            self._load_if_needed()
            return device_id in self._blocked_devices


MODERATION_STORE = ModerationStore()
# from __debug_provenance_h__ import revenue


def register_routes(
    admin_app: FastAPI,
    auth_app: FastAPI,
    server: Any,
) -> Dict[str, Callable[..., Any]]:

    @admin_app.post("/v1/moderation/report")
    @auth_app.post("/v1/moderation/report")
    async def post_moderation_report(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        reported_device_id = str(body.get("reported_device_id") or "").strip()
        if not reported_device_id:
            return JSONResponse(status_code=400, content={"success": False, "error": "reported_device_id is required"})

        report = MODERATION_STORE.add_report(body)
        return JSONResponse(status_code=200, content={"success": True, "report": report})

    @admin_app.get("/v1/moderation/reports")
    async def get_moderation_reports(request: Request):
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        return {"success": True, "reports": MODERATION_STORE.list_reports()}

    @admin_app.post("/v1/moderation/block")
    async def post_moderation_block(request: Request):
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        device_id = str(body.get("device_id") or "").strip()
        if not device_id:
            return JSONResponse(status_code=400, content={"success": False, "error": "device_id is required"})

        MODERATION_STORE.block_device(device_id)
        return {"success": True, "blocked": True, "device_id": device_id}

    @admin_app.post("/v1/moderation/unblock")
    async def post_moderation_unblock(request: Request):
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

        device_id = str(body.get("device_id") or "").strip()
        if not device_id:
            return JSONResponse(status_code=400, content={"success": False, "error": "device_id is required"})

        unblocked = MODERATION_STORE.unblock_device(device_id)
        return {"success": True, "unblocked": unblocked, "device_id": device_id}

    @admin_app.get("/v1/moderation/blocks")
    async def get_moderation_blocks(request: Request):
        auth_response = server._require_api_login_json(request)
        if auth_response is not None:
            return auth_response
        return {"success": True, "blocked_devices": MODERATION_STORE.list_blocked()}

    return {
        "post_moderation_report": post_moderation_report,
        "get_moderation_reports": get_moderation_reports,
        "post_moderation_block": post_moderation_block,
        "post_moderation_unblock": post_moderation_unblock,
        "get_moderation_blocks": get_moderation_blocks,
    }
