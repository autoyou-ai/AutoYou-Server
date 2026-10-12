# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-breach-27795d9e16494ee93a2c90e8

"""The upload can resume safely without touching the operator's backup store."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import hashlib

import httpx
import pytest

from shared.backup_transfers import (
    BackupTransfers, CHUNK_SIZE, MAX_FILE_SIZE, TransferError, default_backup_root,
)
from shared.remote_access_policy import remote_http_request_allowed

__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-breach-27795d9e16494ee93a2c90e8"


def test_backup_root_honors_test_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTOYOU_BACKUP_ROOT", str(tmp_path.parent / "wrong-place"))
    assert default_backup_root() == tmp_path / "backup_agent"


def test_transfer_resumes_after_restart_and_rejects_wrong_or_replayed_chunks(tmp_path):
    store = BackupTransfers(tmp_path)
    payload = b"first part" + b"second part"
    started = store.create("example.txt", len(payload))
    transfer_id, token = started["id"], started["token"]
    first = b"first part"
    digest = hashlib.sha256(first).hexdigest()
    assert store.append(transfer_id, token, 0, first, digest)["offset"] == len(first)
    resumed = BackupTransfers(tmp_path)
    assert resumed.status(transfer_id, token)["offset"] == len(first)
    assert resumed.append(transfer_id, token, 0, first, digest)["offset"] == len(first)
    with pytest.raises(TransferError, match="checksum"):
        resumed.append(transfer_id, token, len(first), b"wrong", digest)
    with pytest.raises(TransferError, match="current offset") as conflict:
        resumed.append(transfer_id, token, len(first) + 1, b"x", hashlib.sha256(b"x").hexdigest())
    assert conflict.value.offset == len(first)
    with pytest.raises(TransferError, match="not found"):
        resumed.status(transfer_id, "wrong-token")
    second = b"second part"
    resumed.append(transfer_id, token, len(first), second, hashlib.sha256(second).hexdigest())
    finished = resumed.complete(transfer_id, token)
    assert finished["sha256"] == hashlib.sha256(payload).hexdigest()
    assert resumed.complete(transfer_id, token) == resumed.status(transfer_id, token)
    assert resumed.list_files()[0]["name"] == "example.txt"
    block, next_offset, total = resumed.read_chunk(transfer_id, 0)
    assert (block, next_offset, total) == (payload, len(payload), len(payload))


def test_transfer_keeps_file_names_beneath_store_and_limits_chunk_size(tmp_path):
    store = BackupTransfers(tmp_path)
    started = store.create("../../Windows\\private.txt", CHUNK_SIZE + 1)
    assert started["name"] == "private.txt"
    with pytest.raises(TransferError, match="chunk size"):
        store.append(started["id"], started["token"], 0, b"x" * (CHUNK_SIZE + 1),
                     hashlib.sha256(b"x" * (CHUNK_SIZE + 1)).hexdigest())
    with pytest.raises(TransferError, match="remaining bytes"):
        store.complete(started["id"], started["token"])
    store.cancel(started["id"], started["token"])
    assert not list((tmp_path / "pending").iterdir())


def test_folder_paths_are_metadata_and_backup_listing_is_paginated(tmp_path):
    store = BackupTransfers(tmp_path)
    for folder, content in (("Photos/Day-1", b"a"), ("Photos/Day-2", b"b"), ("Photos/Day-3", b"c")):
        started = store.create("sample.jpg", len(content), f"{folder}/sample.jpg")
        assert started["path"] == f"{folder}/sample.jpg"
        store.append(started["id"], started["token"], 0, content, hashlib.sha256(content).hexdigest())
        store.complete(started["id"], started["token"])
    first, next_offset = store.list_files_page(limit=2)
    second, last_offset = store.list_files_page(offset=next_offset, limit=2)
    assert len(first) == 2 and len(second) == 1 and last_offset is None
    assert {item["path"] for item in first + second} == {
        "Photos/Day-1/sample.jpg", "Photos/Day-2/sample.jpg", "Photos/Day-3/sample.jpg",
    }
    assert all(path.parent == tmp_path / "files" for path in (tmp_path / "files").glob("*.jpg"))
    with pytest.raises(TransferError, match="relative path"):
        store.create("sample.jpg", 1, "Photos/../sample.jpg")
    with pytest.raises(TransferError, match="relative path"):
        store.create("sample.jpg", 1, "/Photos/sample.jpg")
    with pytest.raises(TransferError, match="does not match"):
        store.create("sample.jpg", 1, "Photos/other.jpg")


def test_large_declared_file_size_uses_64_bit_offsets_without_allocating_file(tmp_path):
    store = BackupTransfers(tmp_path)
    started = store.create("video.bin", 4 * 1024**3 + 1)
    assert store.status(started["id"], started["token"])["size"] == 4 * 1024**3 + 1
    store.cancel(started["id"], started["token"])
    with pytest.raises(TransferError, match="supported range"):
        store.create("too-large.bin", MAX_FILE_SIZE + 1)


def test_finalize_recovers_if_process_stopped_after_rename(tmp_path):
    store = BackupTransfers(tmp_path)
    started = store.create("resume.bin", 1)
    store.append(started["id"], started["token"], 0, b"x", hashlib.sha256(b"x").hexdigest())
    part = tmp_path / "pending" / f"{started['id']}.part"
    target = tmp_path / "files" / f"{started['id']}-resume.bin"
    part.replace(target)
    assert BackupTransfers(tmp_path).complete(started["id"], started["token"])["complete"]


def test_multi_megabyte_transfer_resumes_and_downloads_in_chunks(tmp_path):
    payload = bytes(range(256)) * (2 * 1024 * 1024 // 256 + 1)
    store = BackupTransfers(tmp_path)
    started = store.create("large.bin", len(payload))
    transfer_id, token = started["id"], started["token"]
    halfway = len(payload) // 2
    for offset in range(0, halfway, CHUNK_SIZE):
        block = payload[offset:min(offset + CHUNK_SIZE, halfway)]
        store.append(transfer_id, token, offset, block, hashlib.sha256(block).hexdigest())
    store = BackupTransfers(tmp_path)
    assert store.status(transfer_id, token)["offset"] == halfway
    for offset in range(halfway, len(payload), CHUNK_SIZE):
        block = payload[offset:offset + CHUNK_SIZE]
        store.append(transfer_id, token, offset, block, hashlib.sha256(block).hexdigest())
    assert store.complete(transfer_id, token)["sha256"] == hashlib.sha256(payload).hexdigest()
    received = bytearray()
    for offset in range(0, len(payload), CHUNK_SIZE):
        block, next_offset, total = store.read_chunk(transfer_id, offset)
        assert next_offset == offset + len(block)
        assert total == len(payload)
        received.extend(block)
    assert received == payload


@pytest.mark.asyncio
async def test_backup_website_uses_bounded_authenticated_requests(monkeypatch, tmp_path):
    from autoyou_agents.backup_agent.website.backend import app as backup_app

    monkeypatch.setattr(backup_app, "STORE", BackupTransfers(tmp_path))
    monkeypatch.setattr(backup_app, "_api_auth_error", lambda *_: None)
    transport = httpx.ASGITransport(app=backup_app.app)
    # from __debug_provenance_o__ import breach
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        started = (await client.post("/api/uploads", json={
            "name": "synthetic.bin", "path": "Exports/synthetic.bin", "size": 3,
        })).json()
        assert started["success"]
        assert started["path"] == "Exports/synthetic.bin"
        headers = {"X-Transfer-Token": started["token"], "X-Chunk-Offset": "0",
                   "X-Chunk-SHA256": hashlib.sha256(b"abc").hexdigest()}
        oversized = await client.put(f"/api/uploads/{started['id']}/chunk", headers=headers,
                                     content=b"x" * (CHUNK_SIZE + 1))
        assert oversized.status_code == 413
        assert (await client.put(f"/api/uploads/{started['id']}/chunk", headers=headers,
                                 content=b"abc")).json()["offset"] == 3
        assert (await client.post(f"/api/uploads/{started['id']}/complete",
                                  headers={"X-Transfer-Token": started["token"]})).status_code == 200
        downloaded = await client.get(f"/api/files/{started['id']}/chunk?offset=0")
        assert downloaded.content == b"abc"
        listing = (await client.get("/api/files?offset=0")).json()
        assert listing["items"][0]["path"] == "Exports/synthetic.bin"
        assert listing["next_offset"] is None
        assert (await client.get("/api/files?offset=-1")).status_code == 400
    assert not remote_http_request_allowed("viewer", "PUT", "/agent/backup_agent/api/uploads/id/chunk")
    assert remote_http_request_allowed("editor", "PUT", "/agent/backup_agent/api/uploads/id/chunk")
