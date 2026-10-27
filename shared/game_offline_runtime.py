"""An installed game's local assets and SQLite, independent of an AutoYou server."""
from __future__ import annotations

import base64
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import time
import uuid

from aiohttp import web
from shared import game_package as package

CSP = "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; form-action 'none'; base-uri 'self'"
SDK = Path(__file__).with_name("game_sdk.js")


def install(archive, destination, *, metadata=None, authorize=None):
    destination = Path(destination); destination.parent.mkdir(parents=True, exist_ok=True)
    previous = destination.with_name(destination.name + ".previous")
    if not destination.exists() and previous.exists(): previous.rename(destination)
    stage = destination.parent / (".install-" + destination.name + "-" + uuid.uuid4().hex)
    try:
        manifest = package.extract(archive, stage / "content")
        if (destination / "data").is_dir(): shutil.copytree(destination / "data", stage / "data")
        package.prepare_database(stage / "content", stage / "data", manifest)
        shutil.copyfile(archive, stage / "archive.aygame")
        (stage / "entry.json").write_bytes(package.canonical({**(metadata or {}), "game": manifest["id"], "title": manifest["title"],
            "version": manifest["version"], "revision": manifest["revision"]}))
        if authorize is not None and not authorize(): raise ValueError("Game access was revoked or the Peer Link disconnected")
        shutil.rmtree(previous, ignore_errors=True)
        if destination.exists(): destination.rename(previous)
        try: stage.rename(destination)
        except BaseException:
            if previous.exists(): previous.rename(destination)
            raise
        shutil.rmtree(previous, ignore_errors=True)
        return manifest
    finally: shutil.rmtree(stage, ignore_errors=True)


def execute(payload, content, data, manifest):
    statements = payload.get("statements")
    if not isinstance(statements, list) or not 0 < len(statements) <= 64: raise ValueError("Invalid game database request")
    asset = payload.get("asset")
    if asset is not None:
        package.safe_path(asset)
        if asset not in {item["path"] for item in manifest["files"]} or Path(asset).suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            raise ValueError("This game database asset is unavailable")
    file = Path(content) / asset if asset is not None else Path(data) / "state.sqlite"
    connection = sqlite3.connect(file.as_uri() + "?mode=ro", uri=True, timeout=3) if asset is not None else sqlite3.connect(file, timeout=3)
    with closing(connection) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 32768)
        db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 128)
        deadline = time.monotonic() + 3
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        def authorize(action, first, second, _database, _source):
            if action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA}: return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION and (second or first or "").lower() in {"load_extension", "readfile", "writefile"}: return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        results = []
        with db:
            db.execute("BEGIN IMMEDIATE" if asset is None else "BEGIN")
            for item in statements:
                if not isinstance(item, dict): raise ValueError("Invalid game statement")
                sql = package.validate_sql(item.get("sql")); params = item.get("params")
                if not isinstance(params, list) or len(params) > 999: raise ValueError("Invalid game query parameters")
                values = []
                for value in params:
                    if isinstance(value, dict) and set(value) == {"base64"}: value = base64.b64decode(value["base64"], validate=True)
                    if value is not None and not isinstance(value, (str, bytes, int, float, bool)): raise ValueError("Invalid game query parameter")
                    values.append(value)
                before = db.total_changes
                cursor = db.execute(sql, values)
                rows = cursor.fetchmany(4097)
                if len(rows) > 4096: raise ValueError("Query fewer game rows at once")
                result = {"columns": [column[0] for column in cursor.description or []],
                    "rows": [[{"base64": base64.b64encode(value).decode()} if isinstance(value, bytes) else value for value in row] for row in rows],
                    "changes": db.total_changes - before, "lastInsertId": cursor.lastrowid or 0}
                results.append(result)
                if len(package.canonical({"results": results})) > 1024 * 1024: raise ValueError("The game query result is too large")
        return {"results": results}


class OfflineGameServer:
    def __init__(self, root):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "content/__autoyou_manifest.json").read_text())
        self.token = secrets.token_urlsafe(32)
        self.cookie = "aygame_" + hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:16]
        self.runner = None
        self.origin = ""

    async def start(self):
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_route("*", "/{path:.*}", self.handle)
        self.runner = web.AppRunner(app, access_log=None); await self.runner.setup()
        port_file = self.root / "data/port"
        port = int(port_file.read_text()) if port_file.exists() else 0
        site = web.TCPSite(self.runner, "127.0.0.1", port)
        try: await site.start()
        except BaseException: await self.runner.cleanup(); self.runner = None; raise
        port = site._server.sockets[0].getsockname()[1]
        port_file.write_text(str(port))
        self.origin = f"http://127.0.0.1:{port}"
        return self.origin + "/__autoyou/launch?token=" + self.token

    async def close(self):
        if self.runner: await self.runner.cleanup(); self.runner = None

    async def handle(self, request):
        import asyncio
        headers = {"Cache-Control": "no-store", "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
        if request.host != self.origin.removeprefix("http://"): return web.Response(status=403)
        path = request.match_info["path"]
        if path == "__autoyou/launch" and request.method == "GET" and secrets.compare_digest(request.query.get("token", ""), self.token):
            response = web.HTTPFound("/" + self.manifest["entrypoint"], headers=headers)
            response.set_cookie(self.cookie, self.token, httponly=True, samesite="Strict")
            raise response
        if not secrets.compare_digest(request.cookies.get(self.cookie, ""), self.token): return web.Response(status=403)
        if request.headers.get("Service-Worker"): return web.Response(status=403)
        if path == "__autoyou/db" and request.method == "POST":
            if request.headers.get("Origin", self.origin) != self.origin: return web.Response(status=403)
            try:
                payload = await request.json()
                if not isinstance(payload, dict): raise ValueError("Invalid game database request")
                value = await asyncio.to_thread(execute, payload, self.root / "content", self.root / "data", self.manifest)
                return web.json_response(value, headers=headers)
            except (ValueError, sqlite3.Error, TypeError) as error: return web.json_response({"error": str(error)}, status=400, headers=headers)
        if request.method not in {"GET", "HEAD"}: return web.Response(status=405)
        if path == "__autoyou/sdk.js": return web.Response(body=SDK.read_bytes(), content_type="application/javascript", headers=headers)
        asset = next((item for item in self.manifest["files"] if item["path"] == path), None)
        if not asset: return web.Response(status=404)
        file = self.root / "content" / path
        if path == self.manifest["entrypoint"]:
            if file.stat().st_size > 8 * 1024 * 1024: return web.Response(status=413)
            html = file.read_text(encoding="utf-8")
            html = re.sub(r'''<meta\b[^>]*http-equiv\s*=\s*(['"]?)Content-Security-Policy\1[^>]*>''', "", html, flags=re.I | re.S)
            html = re.sub(r"(<head(?=[\s>])[^>]*>)", r'\1<script src="/__autoyou/sdk.js"></script>', html, count=1, flags=re.I)
            return web.Response(text=html, content_type="text/html", headers=headers)
        return web.FileResponse(file, headers={**headers, "Content-Type": asset["mime"]})
