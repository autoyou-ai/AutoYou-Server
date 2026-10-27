"""Portable offline web games: a bounded manifest followed by checked file bytes.

AYGAME1 archives deliberately use no executable installer or archive extractor.
Each client reads the same manifest, streams exactly the declared bytes, verifies
SHA-256, and commits an installation only after all files have been checked.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import tempfile
import unicodedata

MAGIC = b"AYGAME1\n"
MIME = "application/vnd.autoyou.game"
MAX_BYTES = 5 * 1024 * 1024 * 1024
MAX_HEADER = 1024 * 1024
MAX_FILE = MAX_BYTES
MAX_HTML = 8 * 1024 * 1024
MAX_FILES = 4096
CHUNK = 64 * 1024
GAME_ID = re.compile(r"[a-z0-9-]{1,64}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")


def safe_path(value):
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512
            or value != unicodedata.normalize("NFC", value)
            or any(ord(c) < 32 or ord(c) == 127 or c in '\\:%?#<>"|*+' for c in value)):
        raise ValueError("Invalid game file name")
    for part in value.split("/"):
        if (not part or part in {".", ".."} or part[-1] in " ."
                or part.casefold().split(".")[0] in {"con", "prn", "aux", "nul", *("com" + str(i) for i in range(1, 10)), *("lpt" + str(i) for i in range(1, 10))}
                or part.casefold().startswith("__autoyou")):
            raise ValueError("Invalid game file name")
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def path_key(value):
    return unicodedata.normalize("NFC", value.upper().lower())


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(CHUNK): result.update(block)
    return result.hexdigest()


def validate(manifest):
    if not isinstance(manifest, dict) or type(manifest.get("format")) is not int or manifest["format"] != 1 or manifest.get("runtime") != "web":
        raise ValueError("Unsupported offline game format")
    if not isinstance(manifest.get("id"), str) or not GAME_ID.fullmatch(manifest["id"]): raise ValueError("Invalid game id")
    if not isinstance(manifest.get("title"), str) or not 0 < len(manifest["title"]) <= 80: raise ValueError("Invalid game title")
    if not isinstance(manifest.get("version"), str) or not 0 < len(manifest["version"]) <= 64: raise ValueError("Invalid game version")
    files = manifest.get("files")
    if not isinstance(files, list) or not 0 < len(files) <= MAX_FILES: raise ValueError("Invalid game file list")
    names, total = set(), 0
    for item in files:
        if not isinstance(item, dict): raise ValueError("Invalid game file")
        path = safe_path(item.get("path"))
        if path_key(path) in names: raise ValueError("Duplicate game file")
        names.add(path_key(path))
        if type(item.get("size")) is not int or not 0 <= item["size"] <= MAX_FILE: raise ValueError("Game file is too large")
        if not HASH.fullmatch(str(item.get("sha256", ""))): raise ValueError("Invalid game checksum")
        if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", str(item.get("mime", ""))): raise ValueError("Invalid game content type")
        total += item["size"]
    if total + len(canonical(manifest)) + 12 > MAX_BYTES: raise ValueError("Game package exceeds 5 GiB")
    entry = safe_path(manifest.get("entrypoint"))
    if entry not in {item["path"] for item in files} or not entry.lower().endswith((".html", ".htm")): raise ValueError("The game entry page is missing")
    if next(item["size"] for item in files if item["path"] == entry) > MAX_HTML: raise ValueError("Move large game assets out of the HTML entry page")
    database = manifest.get("database")
    if database is not None:
        if not isinstance(database, dict) or type(database.get("schema_version")) is not int or not 0 <= database["schema_version"] <= 2**31 - 1:
            raise ValueError("Invalid game database schema")
        seed = database.get("seed")
        if seed is not None and safe_path(seed) not in {item["path"] for item in files}: raise ValueError("The game database seed is missing")
        migrations = database.get("migrations", [])
        if not isinstance(migrations, list) or len(migrations) > 256: raise ValueError("Invalid game migrations")
        versions = set()
        for migration in migrations:
            if (not isinstance(migration, dict) or type(migration.get("version")) is not int
                    or not 1 <= migration["version"] <= database["schema_version"] or migration["version"] in versions):
                raise ValueError("Invalid game migration version")
            versions.add(migration["version"])
            statements = migration.get("statements")
            if not isinstance(statements, list) or not 0 < len(statements) <= 256: raise ValueError("Invalid migration statements")
            for sql in statements:
                if not isinstance(sql, str) or not 0 < len(sql.encode()) <= 32768: raise ValueError("Migration statement is too large")
                validate_sql(sql)
    revision = manifest.get("revision", "")
    semantic = {key: value for key, value in manifest.items() if key != "revision"}
    if not HASH.fullmatch(str(revision)) or hashlib.sha256(canonical(semantic)).hexdigest() != revision:
        raise ValueError("The game manifest checksum does not match")
    if len(canonical(manifest)) > MAX_HEADER: raise ValueError("Game manifest is too large")
    return manifest


def read_manifest(package):
    with Path(package).open("rb") as stream:
        if stream.read(8) != MAGIC: raise ValueError("Choose an AutoYou game package")
        size = stream.read(4)
        if len(size) != 4: raise ValueError("Truncated game package")
        length = struct.unpack(">I", size)[0]
        if not 0 < length <= MAX_HEADER: raise ValueError("Invalid game manifest length")
        raw = stream.read(length)
        if len(raw) != length: raise ValueError("Truncated game manifest")
    manifest = validate(json.loads(raw))
    if Path(package).stat().st_size != 12 + length + sum(item["size"] for item in manifest["files"]):
        raise ValueError("Incomplete game package")
    return manifest, 12 + length


def extract(package, destination):
    """Destination must be a fresh staging directory owned by the caller."""
    manifest, offset = read_manifest(package)
    destination = Path(destination)
    if destination.exists(): raise ValueError("Game staging directory already exists")
    destination.mkdir(parents=True)
    try:
        with Path(package).open("rb") as stream:
            stream.seek(offset)
            for item in manifest["files"]:
                target = destination / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                checksum, left = hashlib.sha256(), item["size"]
                with target.open("xb") as output:
                    while left:
                        block = stream.read(min(CHUNK, left))
                        if not block: raise ValueError("Incomplete game file")
                        output.write(block); checksum.update(block); left -= len(block)
                if checksum.hexdigest() != item["sha256"]: raise ValueError("A game file failed its checksum")
        (destination / "__autoyou_manifest.json").write_bytes(canonical(manifest))
        return manifest
    except BaseException:
        shutil.rmtree(destination)
        raise


def build(source, output, *, game_id=None):
    """Export declared assets and consistent SQLite snapshots from a game project."""
    source, output = Path(source).resolve(), Path(output)
    descriptor = json.loads((source / "game.json").read_text(encoding="utf-8"))
    paths = descriptor.get("files")
    if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES: raise ValueError("Declare every offline asset in game.json files")
    with tempfile.TemporaryDirectory(prefix="autoyou-game-export-") as staging:
        staged = Path(staging)
        files = []
        for name in sorted(paths):
            safe_path(name)
            original = source / name
            if not original.is_file() or original.is_symlink() or not original.resolve().is_relative_to(source): raise ValueError("Game asset is missing or outside the project")
            if original.stat().st_size > MAX_FILE: raise ValueError("Game asset exceeds 5 GiB")
            target = staged / name; target.parent.mkdir(parents=True, exist_ok=True)
            if name.lower().endswith((".sqlite", ".sqlite3", ".db")):
                with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as db, closing(sqlite3.connect(target)) as snapshot:
                    db.backup(snapshot)
            else: shutil.copyfile(original, target)
            files.append({"path": name, "size": target.stat().st_size, "sha256": digest(target),
                          "mime": mimetypes.guess_type(name)[0] or "application/octet-stream"})
        manifest = {"format": 1, "runtime": "web", "id": game_id or descriptor.get("id"), "title": descriptor.get("title"),
                    "version": descriptor.get("version"), "entrypoint": descriptor.get("entrypoint", "index.html"), "files": files}
        if descriptor.get("database") is not None: manifest["database"] = descriptor["database"]
        manifest["revision"] = hashlib.sha256(canonical(manifest)).hexdigest()
        validate(manifest)
        entry = (staged / manifest["entrypoint"]).read_text(encoding="utf-8")
        if not re.search(r"<head(?=[\s>])[^>]*>", entry, re.I): raise ValueError("The game entry page needs a head element")
        raw = canonical(manifest)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".part", delete=False) as temp:
            temporary = Path(temp.name)
            try:
                temp.write(MAGIC + struct.pack(">I", len(raw)) + raw)
                for item in files:
                    with (staged / item["path"]).open("rb") as stream: shutil.copyfileobj(stream, temp, CHUNK)
                temp.flush(); os.fsync(temp.fileno()); temp.close()
                os.replace(temporary, output)
            finally: temporary.unlink(missing_ok=True)
        return manifest


def wrap_html(source, output, game_id, title=None):
    with tempfile.TemporaryDirectory(prefix="autoyou-game-html-") as staging:
        root = Path(staging)
        (root / "index.html").write_bytes(Path(source).read_bytes())
        (root / "game.json").write_bytes(canonical({"id": game_id, "title": title or game_id.replace("-", " ").title(),
            "version": "1", "entrypoint": "index.html", "files": ["index.html"]}))
        return build(root, output)


def validate_sql(sql):
    """One local-database statement; the same lexical boundary is used on phones."""
    if not isinstance(sql, str) or not 0 < len(sql.encode()) <= 32768 or "\x00" in sql: raise ValueError("Invalid game SQL")
    clean = re.sub(r"--[^\n]*(?:\n|$)|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`[^`]*`|\[[^\]]*\]", " ", sql, flags=re.S)
    words = re.findall(r"[A-Za-z_]+", clean.upper())
    if not words or words[0] not in {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "REPLACE", "EXPLAIN"}:
        raise ValueError("This SQL operation is unavailable in a game")
    if set(words) & {"ATTACH", "DETACH", "VACUUM", "PRAGMA", "LOAD_EXTENSION", "READFILE", "WRITEFILE"}:
        raise ValueError("Games can access only their own database")
    # No second statement. A trailing terminator is harmless.
    if ";" in clean.rstrip().rstrip(";").rstrip(): raise ValueError("Send SQL statements separately")
    return sql


def prepare_database(content, data, manifest):
    """Migrate a private staged copy, so a failed update retains the old game/data."""
    content, data = Path(content), Path(data)
    data.mkdir(parents=True, exist_ok=True)
    target = data / "state.sqlite"
    specification = manifest.get("database") or {"schema_version": 0}
    desired = specification["schema_version"]
    if not target.exists() and specification.get("seed"):
        shutil.copyfile(content / specification["seed"], target)
    with closing(sqlite3.connect(target)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",): raise ValueError("The game database is damaged")
        current = db.execute("PRAGMA user_version").fetchone()[0]
        if current > desired and manifest.get("database") is not None: raise ValueError("This game update would downgrade its database")
        migrations = {row["version"]: row["statements"] for row in specification.get("migrations", [])}
        with db:
            db.execute("BEGIN IMMEDIATE")
            for version in range(current + 1, desired + 1):
                if version not in migrations: raise ValueError("The game is missing a database migration")
                for sql in migrations[version]: db.execute(validate_sql(sql))
            if desired: db.execute("PRAGMA user_version=" + str(desired))
    return target
