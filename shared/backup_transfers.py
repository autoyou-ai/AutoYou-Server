# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-W-stripe-9955126ee34aee9dce81c5b4

"""Durable, sequential file transfers for the opt-in Backup Agent website."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import uuid
from pathlib import Path

from shared.platform_runtime import get_mutable_data_dir

__debug_provenance_w__ = "AUTOYOU-PROVENANCE-W-stripe-9955126ee34aee9dce81c5b4"


CHUNK_SIZE = 128 * 1024
MAX_FILE_SIZE = 2 * 1024**4
MAX_PENDING = 100
_ID = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class TransferError(Exception):
    def __init__(self, message: str, status: int = 400, offset: int | None = None):
        super().__init__(message)
        self.status = status
        # from __debug_provenance_w__ import stripe
        self.offset = offset


def default_backup_root() -> Path:
    test_root = os.environ.get("AUTOYOU_TEST_ROOT", "").strip()
    if test_root:
        return Path(test_root).expanduser() / "backup_agent"
    configured = os.environ.get("AUTOYOU_BACKUP_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    return get_mutable_data_dir("AutoYou", anchor=__file__) / "backup_agent"


class BackupTransfers:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else default_backup_root()
        self.pending = self.root / "pending"
        self.files = self.root / "files"
        self.lock = threading.RLock()

    def _ensure_dirs(self) -> None:
        self.pending.mkdir(parents=True, exist_ok=True)
        self.files.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise TransferError("Invalid transfer ID.", 404)
        return value

    @staticmethod
    def _name(value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 240:
            raise TransferError("Choose a file with a shorter name.")
        name = re.sub(r"[^A-Za-z0-9._ -]+", "_", value.replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")
        if not name or name in {".", ".."}:
            raise TransferError("Choose a file with a valid name.")
        return name[:180]

    @classmethod
    def _relative_path(cls, value: str | None, name: str) -> str:
        """Keep a selected folder path as display metadata, never a disk path."""
        if value is None:
            return name
        if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value:
            raise TransferError("Choose a file with a valid relative path.")
        segments = value.split("/")
        if len(segments) > 32 or any(segment in {"", ".", ".."} for segment in segments):
            raise TransferError("Choose a file with a valid relative path.")
        clean = [cls._name(segment) for segment in segments]
        if clean[-1] != name:
            raise TransferError("The file name does not match its relative path.")
        return "/".join(clean)

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _metadata(self, transfer_id: str, token: str, *, complete: bool = False) -> dict:
        transfer_id = self._id(transfer_id)
        location = self.files if complete else self.pending
        path = location / f"{transfer_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TransferError("Transfer not found.", 404) from exc
        if not isinstance(data, dict) or not hmac.compare_digest(
            str(data.get("token_hash", "")), hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        ):
            raise TransferError("Transfer not found.", 404)
        return data

    @staticmethod
    def _public(data: dict) -> dict:
        result = {key: data[key] for key in ("id", "name", "size", "offset", "complete") if key in data}
        result["path"] = data.get("path", data.get("name", ""))
        return result

    def create(self, name: str, size: int, path: str | None = None) -> dict:
        clean_name = self._name(name)
        clean_path = self._relative_path(path, clean_name)
        if type(size) is not int or not 0 <= size <= MAX_FILE_SIZE:
            raise TransferError("File size is outside the supported range.")
        with self.lock:
            self._ensure_dirs()
            if sum(1 for _ in self.pending.glob("*.json")) >= MAX_PENDING:
                raise TransferError("Too many unfinished transfers. Finish or cancel one first.", 429)
            transfer_id = uuid.uuid4().hex
            token = secrets.token_urlsafe(32)
            data = {
                "id": transfer_id, "name": clean_name, "path": clean_path, "size": size, "offset": 0,
                "complete": False, "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            }
            (self.pending / f"{transfer_id}.part").touch(exist_ok=False)
            try:
                self._write_json(self.pending / f"{transfer_id}.json", data)
            except Exception:
                (self.pending / f"{transfer_id}.part").unlink(missing_ok=True)
                raise
            return {**self._public(data), "token": token, "chunk_size": CHUNK_SIZE}

    def status(self, transfer_id: str, token: str) -> dict:
        with self.lock:
            try:
                data = self._metadata(transfer_id, token)
            except TransferError:
                data = self._metadata(transfer_id, token, complete=True)
            return {**self._public(data), "chunk_size": CHUNK_SIZE,
                    **({"sha256": data["sha256"]} if data.get("complete") else {})}

    def append(self, transfer_id: str, token: str, offset: int, body: bytes, digest: str) -> dict:
        if type(offset) is not int or offset < 0:
            raise TransferError("Invalid chunk offset.")
        if not isinstance(body, bytes) or not body or len(body) > CHUNK_SIZE:
            raise TransferError("Send one non-empty chunk within the advertised chunk size.")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or hashlib.sha256(body).hexdigest() != digest:
            raise TransferError("Chunk checksum mismatch.")
        with self.lock:
            data = self._metadata(transfer_id, token)
            part = self.pending / f"{transfer_id}.part"
            current = data["offset"]
            if offset + len(body) > data["size"]:
                raise TransferError("Chunk exceeds the declared file size.", 409, current)
            try:
                with part.open("rb+") as handle:
                    actual = handle.seek(0, os.SEEK_END)
                    if actual < current:
                        raise TransferError("Stored transfer is incomplete. Start again.", 409, current)
                    if actual > current:
                        handle.truncate(current)
                    if offset < current and offset + len(body) <= current:
                        handle.seek(offset)
                        if hmac.compare_digest(handle.read(len(body)), body):
                            return self._public(data)
                    if offset != current:
                        raise TransferError("Resume at the server's current offset.", 409, current)
                    handle.seek(current)
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise TransferError("Could not store this chunk.", 507, current) from exc
            data["offset"] = current + len(body)
            self._write_json(self.pending / f"{transfer_id}.json", data)
            return self._public(data)

    def complete(self, transfer_id: str, token: str) -> dict:
        with self.lock:
            try:
                data = self._metadata(transfer_id, token)
            except TransferError:
                return self.status(transfer_id, token)
            if data["offset"] != data["size"]:
                raise TransferError("Upload the remaining bytes before completing.", 409, data["offset"])
            part = self.pending / f"{transfer_id}.part"
            target = self.files / f"{transfer_id}-{data['name']}"
            try:
                # A process can stop after the rename but before the completed
                # manifest is written. A retry finishes that last step.
                source = part if part.exists() else target
                if source.stat().st_size != data["size"]:
                    raise TransferError("Stored transfer size changed. Start again.", 409, data["offset"])
                digest = hashlib.sha256()
                with source.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                data["sha256"] = digest.hexdigest()
                data["complete"] = True
                if source == part:
                    os.replace(part, target)
                self._write_json(self.files / f"{transfer_id}.json", data)
                (self.pending / f"{transfer_id}.json").unlink(missing_ok=True)
            except OSError as exc:
                raise TransferError("Could not finish this transfer.", 507) from exc
            return {**self._public(data), "sha256": data["sha256"]}

    def cancel(self, transfer_id: str, token: str) -> None:
        with self.lock:
            self._metadata(transfer_id, token)
            (self.pending / f"{transfer_id}.json").unlink(missing_ok=True)
            (self.pending / f"{transfer_id}.part").unlink(missing_ok=True)

    def list_files_page(self, offset: int = 0, limit: int = 200) -> tuple[list[dict], int | None]:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise TransferError("Invalid backup listing page.")
        with self.lock:
            self._ensure_dirs()
            files = []
            paths = sorted(self.files.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
            for path in paths[offset:offset + limit]:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if (data.get("complete") and data.get("id") == path.stem and
                            self._name(data["name"]) == data["name"] and
                            self._relative_path(data.get("path"), data["name"]) == data.get("path", data["name"]) and
                            (self.files / f"{data['id']}-{data['name']}").is_file()):
                        files.append({**self._public(data), "sha256": data["sha256"]})
                except (OSError, ValueError, KeyError, TypeError, TransferError):
                    continue
            next_offset = offset + limit if offset + limit < len(paths) else None
            return files, next_offset

    def list_files(self) -> list[dict]:
        return self.list_files_page()[0]

    def read_chunk(self, transfer_id: str, offset: int) -> tuple[bytes, int, int]:
        if type(offset) is not int or offset < 0:
            raise TransferError("Invalid download offset.")
        transfer_id = self._id(transfer_id)
        with self.lock:
            try:
                data = json.loads((self.files / f"{transfer_id}.json").read_text(encoding="utf-8"))
                if (not data.get("complete") or data.get("id") != transfer_id or
                        self._name(data["name"]) != data["name"] or offset > data["size"]):
                    raise TransferError("Invalid download offset.", 416)
                path = self.files / f"{transfer_id}-{data['name']}"
                with path.open("rb") as handle:
                    handle.seek(offset)
                    block = handle.read(CHUNK_SIZE)
                return block, offset + len(block), data["size"]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise TransferError("Backup not found.", 404) from exc
