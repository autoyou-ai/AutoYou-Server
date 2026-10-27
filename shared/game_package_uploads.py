"""Authenticated, bounded and resumable game-package publication."""
from __future__ import annotations

import hashlib
from pathlib import Path
import secrets
import shutil
import threading
import time
from concurrent.futures import Future

from shared import game_package as package


class GamePackageUploads:
    def __init__(self, root):
        self.root = Path(root)
        self.sessions = {}
        self.lock = threading.Lock()
        self.jobs_lock = threading.Lock()
        self.jobs = {}

    def finish(self, key, owner):
        """Pollable verification keeps multi-gigabyte hashes outside HTTP timeouts."""
        with self.jobs_lock:
            for old, job in list(self.jobs.items()):
                if job["future"].done() and time.monotonic()-job["updated"] > 600: del self.jobs[old]
            job = self.jobs.get(key)
            if job is None:
                with self.lock: self._session(key, owner)
                if len(self.jobs) >= 8: raise ValueError("Other games are being verified. Try again shortly")
                future = Future(); job = {"owner": owner, "future": future, "updated": time.monotonic()}; self.jobs[key] = job
                def verify():
                    try: future.set_result(self.complete(key, owner))
                    except BaseException as error: future.set_exception(error)
                threading.Thread(target=verify, daemon=True, name="game-package-verification").start()
            if not secrets.compare_digest(job["owner"], owner): raise ValueError("This upload is unavailable")
            job["updated"] = time.monotonic()
            if not job["future"].done(): return {"pending": True, "upload_id": key}
            return job["future"].result()

    def _expire(self):
        for key, session in list(self.sessions.items()):
            if time.monotonic() - session["updated"] > 600:
                session["path"].unlink(missing_ok=True); del self.sessions[key]

    def begin(self, owner, payload):
        with self.lock:
            self._expire()
            if not owner or not isinstance(payload, dict): raise ValueError("Invalid package upload")
            size, checksum, identifier = payload.get("size"), payload.get("sha256"), payload.get("id")
            if type(size) is not int or not 12 < size <= package.MAX_BYTES or not package.HASH.fullmatch(str(checksum or "")) or not package.GAME_ID.fullmatch(str(identifier or "")):
                raise ValueError("Invalid package size, checksum or game id")
            for key, item in self.sessions.items():
                if item["owner"] == owner and item["size"] == size and item["sha256"] == checksum and item["id"] == identifier:
                    return {"upload_id": key, "offset": item["path"].stat().st_size}
            if len(self.sessions) >= 8 or sum(item["size"] for item in self.sessions.values()) + size > 2 * package.MAX_BYTES:
                raise ValueError("Other games are being uploaded. Try again when they finish")
            directory = self.root / "game_uploads"; directory.mkdir(parents=True, exist_ok=True)
            reserved = sum(item["size"]*2 - item["path"].stat().st_size for item in self.sessions.values())
            if shutil.disk_usage(directory).free < reserved + size*2 + 10*1024*1024:
                raise ValueError("This computer needs space for the complete game and its checked assets")
            key = secrets.token_hex(16); path = directory / (key + ".part"); path.touch(exist_ok=False)
            self.sessions[key] = {"owner": owner, "id": identifier, "size": size, "sha256": checksum, "path": path, "updated": time.monotonic()}
            return {"upload_id": key, "offset": 0}

    def _session(self, key, owner):
        self._expire()
        session = self.sessions.get(key)
        if not session or not secrets.compare_digest(session["owner"], owner): raise ValueError("This upload is unavailable")
        session["updated"] = time.monotonic()
        return session

    def write(self, key, owner, offset, data):
        with self.lock:
            session = self._session(key, owner); path = session["path"]
            if type(offset) is not int or offset < 0 or not data or len(data) > 1024 * 1024 or offset + len(data) > session["size"]:
                raise ValueError("Invalid game upload range")
            current = path.stat().st_size
            if offset < current:
                with path.open("rb") as stream:
                    stream.seek(offset)
                    if stream.read(len(data)) != data: raise ValueError("The uploaded game changed")
            elif offset == current:
                with path.open("ab") as stream: stream.write(data); stream.flush()
            else: raise ValueError("Resume from the computer's confirmed upload offset")
            return {"offset": path.stat().st_size}

    def complete(self, key, owner):
        with self.lock:
            session = self._session(key, owner); path = session["path"]
            if path.stat().st_size != session["size"] or package.digest(path) != session["sha256"]: raise ValueError("The game upload is incomplete or failed its checksum")
            stage = path.with_suffix(".verify")
            try:
                manifest = package.extract(path, stage)
                if manifest["id"] != session["id"]: raise ValueError("The package contains a different game")
                # Verify the seed and migration syntax before advertising the new game.
                package.prepare_database(stage, stage / "__autoyou_data", manifest)
                destination = self.root / "mobile_games" / (manifest["id"] + ".aygame")
                destination.parent.mkdir(parents=True, exist_ok=True)
                path.replace(destination)
                del self.sessions[key]
                return {"id": manifest["id"], "revision": manifest["revision"], "sha256": session["sha256"]}
            finally: shutil.rmtree(stage, ignore_errors=True)

    def cancel(self, key, owner):
        with self.lock:
            session = self._session(key, owner)
            session["path"].unlink(missing_ok=True)
            del self.sessions[key]
