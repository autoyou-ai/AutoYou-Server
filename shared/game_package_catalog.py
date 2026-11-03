"""One export library for remote WebRTC clients and Game Agent publication."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from shared import game_package as package


class GamePackages:
    def __init__(self, app_root, data_root):
        self.folders = (Path(app_root) / "assets/game", Path(data_root) / "mobile_games")
        self.cache = Path(data_root) / "game_package_cache"

    def candidates(self):
        result = {}
        for priority, folder in enumerate(self.folders):
            if not folder.is_dir(): continue
            for path in sorted(folder.iterdir())[:256]:
                identifier = path.stem if path.is_file() else path.name
                if not package.GAME_ID.fullmatch(identifier) or path.is_symlink(): continue
                rank = 3 if path.suffix == ".aygame" else 2 if path.is_dir() and (path / "game.json").is_file() else 1 if path.suffix == ".html" else 0
                if rank and (identifier not in result or (priority, rank) > result[identifier][0]):
                    result[identifier] = ((priority, rank), path)
        return {key: value[1] for key, value in result.items()}

    def resolve(self, identifier):
        if not package.GAME_ID.fullmatch(identifier): raise ValueError("Invalid game id")
        source = self.candidates().get(identifier)
        if source is None: raise FileNotFoundError("This game is unavailable")
        if source.suffix == ".aygame":
            manifest, _ = package.read_manifest(source)
            if manifest["id"] != identifier: raise ValueError("Game id does not match its package")
            return source
        descriptor = json.loads((source / "game.json").read_text()) if source.is_dir() else None
        names = ["game.json", *(descriptor.get("files") or [])] if descriptor else [source.name]
        def fingerprint():
            stamps = []
            for name in names:
                package.safe_path(name)
                file = source / name if descriptor else source
                if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(source if descriptor else source.parent):
                    raise ValueError("A game file is missing or outside its project")
                stat = file.stat(); stamps.append((name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
                wal = Path(str(file) + "-wal")
                if wal.is_file():
                    stat = wal.stat(); stamps.append((name + "-wal", stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
            return package.canonical([str(source), stamps])
        before = fingerprint()
        self.cache.mkdir(parents=True, exist_ok=True)
        target = self.cache / (identifier + "-" + hashlib.sha256(before).hexdigest() + ".aygame")
        if not target.exists():
            if descriptor: package.build(source, target, game_id=identifier)
            else: package.wrap_html(source, target, identifier)
            if fingerprint() != before:
                target.unlink(missing_ok=True)
                raise ValueError("This game is being edited. Download it again when saving finishes")
            for previous in self.cache.glob(identifier+"-*.aygame"):
                if previous != target: previous.unlink(missing_ok=True)
        return target

    def listing(self):
        result = []
        for identifier in sorted(self.candidates())[:64]:
            try:
                path = self.resolve(identifier)
                manifest, _ = package.read_manifest(path)
                result.append({key: manifest[key] for key in ("id", "title", "version", "revision")})
            except (OSError, ValueError, TypeError, KeyError): continue
        return {"format": 1, "games": result}

    def response(self, method, path, headers):
        """Bounded virtual HTTP response; bytes are base64-encoded by the caller."""
        response_headers = {"Cache-Control": "no-store", "Content-Type": "application/json"}
        if method != "GET": return 405, response_headers, b'{}'
        try:
            if path.rstrip("/") == "/api/v1/game-packages":
                return 200, response_headers, package.canonical(self.listing())
            match = re.fullmatch(r"/api/v1/game-packages/([a-z0-9-]{1,64})(/metadata)?", path)
            if not match: return 404, response_headers, b'{}'
            file = self.resolve(match[1]); manifest, _ = package.read_manifest(file)
            size = file.stat().st_size
            if match[2]:
                return 200, response_headers, package.canonical({**{key: manifest[key] for key in ("id", "title", "version", "revision")},
                    "size": size, "sha256": package.digest(file)})
            response_headers.update({"Content-Type": package.MIME, "Accept-Ranges": "bytes", "ETag": '"' + manifest["revision"] + '"'})
            range_value = next((value for key, value in headers.items() if key.lower() == "range"), "")
            span = re.fullmatch(r"bytes=(\d+)-(\d+)", range_value)
            if not span: return 416, response_headers, b''
            start, end = map(int, span.groups())
            if start >= size or end < start or end - start + 1 > 1024 * 1024: return 416, response_headers, b''
            end = min(end, size - 1)
            with file.open("rb") as stream:
                stream.seek(start); body = stream.read(end - start + 1)
            response_headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            response_headers["Content-Length"] = str(len(body))
            return 206, response_headers, body
        except (OSError, ValueError, TypeError, KeyError): return 404, response_headers, b'{}'
