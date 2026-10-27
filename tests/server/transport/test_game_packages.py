"""Portable package integrity, consistent SQLite export, updates and upload ownership."""
import json
from pathlib import Path
import shutil
import sqlite3
import hashlib
import struct
import subprocess
import time

import pytest

from shared import game_package as package
from shared.game_package_catalog import GamePackages
from shared.game_package_uploads import GamePackageUploads
pytestmark = pytest.mark.server

FIXTURES = Path(__file__).resolve().parents[4] / "tests/fixtures/offline_games"


def test_full_assets_and_sqlite_snapshot_migrate_without_losing_progress(tmp_path):
    first, second = FIXTURES / "package-v1.aygame", FIXTURES / "package-v2.aygame"
    manifest = package.extract(first, tmp_path / "one")
    database = package.prepare_database(tmp_path / "one", tmp_path / "data", manifest)
    with sqlite3.connect(database) as db: db.execute("UPDATE progress SET steps=14 WHERE id=1")
    manifest = package.extract(second, tmp_path / "two")
    package.prepare_database(tmp_path / "two", tmp_path / "data", manifest)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT steps,power FROM progress").fetchone() == (14, "new")
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
    assert (tmp_path / "one/assets/music.wav").read_bytes() != (tmp_path / "two/assets/music.wav").read_bytes()
    assert json.loads((tmp_path / "two/levels.json").read_text())["name"].endswith("2")


def test_export_captures_uncheckpointed_sqlite_wal(tmp_path):
    source = tmp_path / "project"; shutil.copytree(FIXTURES / "project-v1", source)
    with sqlite3.connect(source / "seed.sqlite") as db:
        db.execute("PRAGMA journal_mode=WAL"); db.execute("UPDATE progress SET steps=23"); db.commit()
        package.build(source, tmp_path / "game.aygame")
        manifest = package.extract(tmp_path / "game.aygame", tmp_path / "content")
        result = package.prepare_database(tmp_path / "content", tmp_path / "data", manifest)
        with sqlite3.connect(result) as snapshot: assert snapshot.execute("SELECT steps FROM progress").fetchone() == (23,)


@pytest.mark.parametrize("path", ["../escape", "/root", "a/../b", "a//b", "C:escape", "a\\b", "a%2fb", "a?b", "CON.txt", "x.", "__autoyou/db"])
def test_rejects_nonportable_and_escaping_paths(path):
    with pytest.raises(ValueError): package.safe_path(path)


@pytest.mark.parametrize("sql", ["ATTACH DATABASE '/tmp/other' AS x", "VACUUM INTO '/tmp/other'", "PRAGMA temp_store_directory='/tmp'", "SELECT load_extension('x')", "SELECT 1; DELETE FROM progress"])
def test_sql_cannot_escape_game_database(sql):
    with pytest.raises(ValueError): package.validate_sql(sql)


def test_corrupt_payload_never_leaves_a_partial_install(tmp_path):
    file = tmp_path / "bad.aygame"; content = bytearray((FIXTURES / "package-v1.aygame").read_bytes()); content[-1] ^= 1; file.write_bytes(content)
    with pytest.raises(ValueError, match="checksum"): package.extract(file, tmp_path / "install")
    assert not (tmp_path / "install").exists()


def test_remote_range_download_and_newer_publication(tmp_path):
    root = tmp_path / "state"; folder = root / "mobile_games"; folder.mkdir(parents=True)
    target = folder / "synthetic-package.aygame"; shutil.copyfile(FIXTURES / "package-v1.aygame", target)
    library = GamePackages(tmp_path / "app", root)
    before = library.listing()["games"][0]
    data = bytearray(); offset = 0
    while offset < target.stat().st_size:
        status, headers, chunk = library.response("GET", "/api/v1/game-packages/synthetic-package", {"Range": f"bytes={offset}-{offset+4095}"})
        assert status == 206 and headers["Content-Range"].startswith(f"bytes {offset}-")
        data.extend(chunk); offset += len(chunk)
    assert data == target.read_bytes()
    assert library.response("GET", "/api/v1/game-packages/synthetic-package", {})[0] == 416
    shutil.copyfile(FIXTURES / "package-v2.aygame", target)
    after = library.listing()["games"][0]
    assert after["version"] == "2" and after["revision"] != before["revision"]


def test_upload_resume_integrity_and_session_ownership(tmp_path):
    source = FIXTURES / "package-v2.aygame"; store = GamePackageUploads(tmp_path)
    data = source.read_bytes(); metadata = {"id": "synthetic-package", "size": len(data), "sha256": package.digest(source)}
    first = store.begin("synthetic-owner-a", metadata); key = first["upload_id"]
    assert store.write(key, "synthetic-owner-a", 0, data[:4096])["offset"] == 4096
    assert store.begin("synthetic-owner-a", metadata) == {"upload_id": key, "offset": 4096}
    with pytest.raises(ValueError): store.write(key, "synthetic-owner-b", 4096, data[4096:])
    with pytest.raises(ValueError): store.complete(key, "synthetic-owner-a")
    assert store.write(key, "synthetic-owner-a", 0, data[:4096])["offset"] == 4096
    offset = 4096
    while offset < len(data):
        offset = store.write(key, "synthetic-owner-a", offset, data[offset:offset+1024*1024])["offset"]
    assert store.complete(key, "synthetic-owner-a")["sha256"] == metadata["sha256"]
    assert (tmp_path / "mobile_games/synthetic-package.aygame").read_bytes() == data


def test_five_gib_manifest_and_ranges_use_64_bit_offsets_without_allocating_game(tmp_path):
    manifest, _ = package.read_manifest(FIXTURES / "package-v1.aygame")
    large = 5 * 1024**3 - 2 * 1024**2
    manifest["files"].append({"path": "large.bin", "size": large, "sha256": "0"*64, "mime": "application/octet-stream"})
    manifest["revision"] = hashlib.sha256(package.canonical({key: value for key, value in manifest.items() if key != "revision"})).hexdigest()
    raw = package.canonical(manifest); size = 12+len(raw)+sum(item["size"] for item in manifest["files"])
    root = tmp_path / "state/mobile_games"; root.mkdir(parents=True); file = root / "synthetic-package.aygame"
    with file.open("wb") as stream:
        stream.write(package.MAGIC+struct.pack(">I",len(raw))+raw); stream.truncate(size)
        stream.seek(size-4); stream.write(b"TAIL")
    assert package.MAX_BYTES == 5*1024**3
    assert package.read_manifest(file)[0]["files"][-1]["size"] == large
    status, headers, data = GamePackages(tmp_path/"app", root.parent).response("GET", "/api/v1/game-packages/synthetic-package", {"Range": f"bytes={size-4}-{size-1}"})
    assert status == 206 and data == b"TAIL" and headers["Content-Range"] == f"bytes {size-4}-{size-1}/{size}"
    manifest["files"][-1]["size"] = package.MAX_BYTES
    with pytest.raises(ValueError, match="large|5 GiB"): package.validate(manifest)


def test_portable_case_collisions_and_json_types(tmp_path):
    manifest, _ = package.read_manifest(FIXTURES/"package-v1.aygame")
    manifest["format"] = True
    with pytest.raises(ValueError): package.validate(manifest)
    for path in ['a<b', 'a"b', 'a|b', 'a*b', 'a+b', 'a\x7fb']:
        with pytest.raises(ValueError): package.safe_path(path)
    manifest["format"] = 1
    manifest["files"].extend({"path": name, "size": 0, "sha256": "0"*64, "mime": "text/plain"} for name in ["Straße.txt", "STRASSE.txt"])
    with pytest.raises(ValueError, match="Duplicate"): package.validate(manifest)


def test_shared_sdk_generic_input_contract():
    node = shutil.which("node")
    if not node: pytest.skip("Node is needed for the shared browser input contract")
    subprocess.run([node, str(Path(__file__).with_name("game_input_contract.cjs")),
        str(Path(package.__file__).with_name("game_sdk.js"))], check=True, timeout=15)


def test_large_package_verification_is_pollable_idempotent_and_owner_bound(tmp_path):
    file=FIXTURES/"package-v1.aygame";store=GamePackageUploads(tmp_path)
    session=store.begin("synthetic-owner", {"id":"synthetic-package", "size":file.stat().st_size, "sha256":package.digest(file)})
    key=session["upload_id"];offset=0
    with file.open("rb") as stream:
        while chunk:=stream.read(1024*1024): offset=store.write(key,"synthetic-owner",offset,chunk)["offset"]
    result=store.finish(key,"synthetic-owner")
    with pytest.raises(ValueError):store.finish(key,"synthetic-other")
    end=time.monotonic()+10
    while result.get("pending") and time.monotonic()<end:
        time.sleep(.01);result=store.finish(key,"synthetic-owner")
    assert result["sha256"]==package.digest(file)
    assert store.finish(key,"synthetic-owner")==result


@pytest.mark.asyncio
async def test_real_webrtc_game_route_and_desktop_binary_decoder(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import server
    client_root = Path(__file__).resolve().parents[4]
    if not (client_root / "v2/runtime/client.py").is_file():
        pytest.skip("Desktop interoperability needs the parent client checkout")
    monkeypatch.syspath_prepend(str(client_root))
    from v2.runtime.client import DesktopClient
    monkeypatch.setenv("AUTOYOU_TEST_ROOT",str(tmp_path/"state"))
    monkeypatch.setattr(server,"APP_ROOT",tmp_path/"app")
    config=server._default_config();config["autoyou_page"]["remote_access_role"]="editor"
    monkeypatch.setattr(server.STATE,"config",config,raising=False)
    folder=server.get_mutable_data_dir("AutoYou",anchor=server.__file__)/"mobile_games";folder.mkdir(parents=True)
    shutil.copyfile(FIXTURES/"package-v1.aygame",folder/"synthetic-package.aygame")
    engine=server.WebRTCManager();pending={}
    async def deliver(message):
        pending[message.payload["request_id"]].set_result(message.payload);return True
    engine.datachannel_managers["synthetic-game-session"]=SimpleNamespace(send_message=AsyncMock(side_effect=deliver))
    async def send(message):await engine._handle_http_request(message);return True
    client=SimpleNamespace(connected=True,data_channel=SimpleNamespace(readyState="open"),session_id="synthetic-game-session",connection_session="synthetic-game-connection",
        pending_requests=pending,datachannel_manager=SimpleNamespace(send_message=send),_clear_pending_response_stream=lambda _key:None)
    async def request(path,headers=None):return await DesktopClient.game_package_request(client,"GET",path,headers)
    code,_,body=await request("/api/v1/game-packages/synthetic-package/metadata");metadata=json.loads(body)
    assert code==200 and metadata["size"]>1024*1024
    checksum=hashlib.sha256();offset=0
    while offset<metadata["size"]:
        end=min(metadata["size"]-1,offset+1024*1024-1)
        code,headers,body=await request("/api/v1/game-packages/synthetic-package",{"Range":f"bytes={offset}-{end}"})
        assert code==206 and headers["Content-Range"]==f"bytes {offset}-{end}/{metadata['size']}" and len(body)==end-offset+1
        checksum.update(body);offset+=len(body)
    assert checksum.hexdigest()==metadata["sha256"] and not pending
