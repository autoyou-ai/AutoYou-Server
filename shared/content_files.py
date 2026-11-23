"""Bounded encrypted media chunks using the existing Maximus key boundary.

Only AutoYou-owned files use this format. External library files are read without
modification. Each chunk and the small manifest use secure_storage's envelopes,
so existing discovery, key rotation, and recovery apply to both.
"""
from __future__ import annotations
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from shared.secure_storage import FILE_HEADER, SecureStorageError, read_secure_file, write_secure_file, secure_storage_enabled

CHUNK_SIZE = 1024 * 1024
FORMAT = "autoyou-media-chunks-1"
REFERENCE = b"AUTOYOU-CONTENT-REFERENCE-1\n"


def reference_target(path):
    """Resolve only our small references to verified, owned content objects."""
    path = Path(path)
    if path.stat().st_size > 8192:
        return None
    raw = read_secure_file(path, migrate_plaintext=False)
    if not raw.startswith(REFERENCE):
        return None
    value = json.loads(raw[len(REFERENCE):])
    digest = value.get("digest", "")
    import re
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid content object reference")
    from shared.content_library import managed_roots
    target = Path(value["target"])
    allowed = [root / ".objects" / digest[:2] / digest for root in managed_roots()]
    if target.is_symlink() or not any(target.resolve() == item.resolve() and target.resolve().is_relative_to(root.resolve())
                                     for root, item in zip(managed_roots(), allowed)):
        raise ValueError("Content object is outside managed storage")
    if any(path.resolve().is_relative_to(root / ".objects") for root in managed_roots()):
        raise ValueError("Content objects cannot reference other objects")
    if not target.is_file():
        raise FileNotFoundError("Restore the library's .objects folder to read this item")
    return target


def make_reference(path, target, digest):
    """Replace a verified owned copy; external originals never call this helper."""
    path, target = Path(path), Path(target)
    if path.resolve() == target.resolve():
        return
    staged = path.with_name(path.name + ".reference")
    write_secure_file(staged, REFERENCE + json.dumps({"target": str(target), "digest": digest}).encode())
    reference_target(staged)  # Validate before replacing any bytes.
    # The object has already been read back and verified. Publish its reference
    # before cleaning the obsolete sidecar, so interruption never loses data.
    os.replace(staged, path)
    if parts(path).exists():
        shutil.rmtree(parts(path))


def fingerprint(path):
    digest, length = hashlib.sha256(), 0
    for chunk in read_range(path):
        digest.update(chunk)
        length += len(chunk)
    return digest.hexdigest(), length


def copy(source, destination):
    """Copy completed media in bounded chunks, honoring the active storage mode."""
    destination = Path(destination)
    create(destination)
    try:
        offset = 0
        for chunk in read_range(source):
            offset = append(destination, offset, chunk)
        return offset
    except BaseException:
        discard(destination)
        raise


def legacy_payload(path):
    with Path(path).open("rb") as handle:
        sealed = handle.read(len(FILE_HEADER)) == FILE_HEADER
    if not sealed:
        return None
    data = read_secure_file(path, migrate_plaintext=False)
    if FORMAT.encode() in data[:128]:
        raise SecureStorageError("Managed media chunks are missing; restore the media sidecar directory")
    return data


def parts(path):
    return Path(path).with_name(Path(path).name + ".parts")


def manifest(path):
    path = Path(path)
    if not parts(path).is_dir() or not path.is_file():
        return None
    if parts(path).is_symlink():
        raise ValueError("Media chunk directory cannot be a symbolic link")
    value = json.loads(read_secure_file(path, migrate_plaintext=False))
    if value.get("format") != FORMAT or not isinstance(value.get("sizes"), list):
        raise ValueError("Invalid managed media manifest")
    if any(type(size) is not int or not 0 < size <= CHUNK_SIZE for size in value["sizes"]):
        raise ValueError("Invalid managed media chunk size")
    return value


def create(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if secure_storage_enabled():
        if path.exists() or parts(path).exists():
            raise FileExistsError(path.name)
        parts(path).mkdir()
        write_secure_file(path, json.dumps({"format": FORMAT, "sizes": []}).encode())
    else:
        path.touch(exist_ok=False)


def size(path):
    target = reference_target(path)
    if target is not None:
        return size(target)
    data = manifest(path)
    if data:
        return sum(data["sizes"])
    payload = legacy_payload(path)
    return len(payload) if payload is not None else Path(path).stat().st_size


def protected(path):
    if reference_target(path) is not None:
        return True
    if parts(path).exists():
        return True
    with Path(path).open("rb") as handle:
        return handle.read(len(FILE_HEADER)) == FILE_HEADER


def truncate(path, length):
    """Recover an append whose database transaction did not commit."""
    path = Path(path)
    data = manifest(path)
    if not data:
        with path.open("r+b") as handle:
            handle.truncate(length)
        return
    if length < 0 or length > sum(data["sizes"]):
        raise ValueError("Invalid committed upload size")
    retained, remaining = [], length
    for index, count in enumerate(data["sizes"]):
        if not remaining:
            break
        keep = min(count, remaining)
        if keep < count:
            chunk = parts(path) / f"{index:08d}.chunk"
            write_secure_file(chunk, read_secure_file(chunk, migrate_plaintext=False)[:keep])
        retained.append(keep)
        remaining -= keep
    write_secure_file(path, json.dumps({"format": FORMAT, "sizes": retained}).encode())
    for index in range(len(retained), len(data["sizes"])):
        (parts(path) / f"{index:08d}.chunk").unlink(missing_ok=True)


def read_range(path, start=0, end=None):
    path = Path(path)
    target = reference_target(path)
    if target is not None:
        yield from read_range(target, start, end)
        return
    data = manifest(path)
    legacy = None if data else legacy_payload(path)
    total = sum(data["sizes"]) if data else len(legacy) if legacy is not None else path.stat().st_size
    end = min(total - 1, end) if end is not None else total - 1
    if start < 0 or end < start - 1:
        raise ValueError("Invalid media range")
    if data is None:
        if legacy is not None:
            for position in range(start, end + 1, CHUNK_SIZE):
                yield legacy[position:min(end + 1, position + CHUNK_SIZE)]
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = handle.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise OSError("Media file was truncated")
                remaining -= len(chunk)
                yield chunk
        return
    offset = 0
    for index, length in enumerate(data["sizes"]):
        if offset > end:
            break
        if offset + length > start:
            chunk_path = parts(path) / f"{index:08d}.chunk"
            if chunk_path.is_symlink():
                raise ValueError("Media chunks cannot be symbolic links")
            payload = read_secure_file(chunk_path, migrate_plaintext=False)
            if len(payload) != length:
                raise OSError("Media chunk size mismatch")
            yield payload[max(0, start - offset):min(length, end - offset + 1)]
        offset += length


def append(path, offset, payload):
    """Append or acknowledge an identical retry. Call under the upload DB lock."""
    path = Path(path)
    data = manifest(path)
    current = sum(data["sizes"]) if data else path.stat().st_size
    if offset < 0 or offset > current:
        raise ValueError("Resume from the received offset")
    if offset < current:
        if offset + len(payload) > current or b"".join(read_range(path, offset, offset + len(payload) - 1)) != bytes(payload):
            raise ValueError("Upload chunk conflicts with received data")
        return current
    if data is None:
        if secure_storage_enabled():
            # A security mode change requires restarting the upload, rather than
            # silently writing new plaintext to a pre-existing upload session.
            raise ValueError("Security mode changed. Restart this upload for encrypted storage.")
        with path.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    else:
        for start in range(0, len(payload), CHUNK_SIZE):
            chunk = bytes(payload[start:start + CHUNK_SIZE])
            write_secure_file(parts(path) / f'{len(data["sizes"]):08d}.chunk', chunk)
            data["sizes"].append(len(chunk))
        write_secure_file(path, json.dumps(data).encode())
    return current + len(payload)


def promote(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if parts(source).exists():
        manifest(source)
        if parts(destination).exists():
            raise FileExistsError("Destination media already has chunks")
        # Publish a copy of the sealed manifest first. At every interruption
        # either source or destination has both its manifest and its chunks.
        shutil.copyfile(source, destination)
        shutil.move(str(parts(source)), str(parts(destination)))
        source.unlink()
    else:
        shutil.move(str(source), str(destination))


def finish_upload(source, destination, expected):
    """Recover publication before the upload database transaction committed."""
    source, destination = Path(source), Path(destination)
    if destination.is_file() and (parts(destination).is_dir() or not source.exists()):
        if size(destination) != expected:
            raise ValueError("Published upload size does not match")
        if source.exists() and not parts(source).exists():
            source.unlink()
        return
    if not source.is_file() or size(source) != expected:
        raise ValueError("Upload data is unavailable. Restart this upload.")
    promote(source, destination)


def seal(path):
    """Seal a codec's completed managed output, streaming in bounded chunks."""
    path = Path(path)
    if not secure_storage_enabled() or protected(path):
        return
    staged = path.with_name(path.name + ".sealed")
    try:
        create(staged)
        offset = 0
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                offset = append(staged, offset, chunk)
        promote(staged, path)
    finally:
        if staged.exists():
            discard(staged)


def discard(path):
    path = Path(path)
    if parts(path).exists():
        # A crash can leave obsolete chunks after the verified reference was published.
        raw = read_secure_file(path, migrate_plaintext=False)
        if not raw.startswith(REFERENCE):
            manifest(path)
        shutil.rmtree(parts(path))
    path.unlink(missing_ok=True)


@contextmanager
def materialized(path):
    """Short-lived input for codecs. Never used for HTTP streaming."""
    path = Path(path)
    if not protected(path):
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="autoyou-media-") as directory:
        target = Path(directory) / path.name
        with target.open("xb") as handle:
            for chunk in read_range(path):
                handle.write(chunk)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        yield target


def response(path, request, media_type=None):
    """Authenticated callers keep their policy; this only serves bounded bytes."""
    import mimetypes
    import re
    from fastapi.responses import Response, StreamingResponse
    path = Path(path)
    length = size(path)
    etag = f'"{path.stat().st_mtime_ns:x}-{length:x}"'
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store", "ETag": etag, "X-Content-Type-Options": "nosniff"}
    # A caller without an HTTP request (a test, an internal render) is served the whole file.
    request_headers = getattr(request, "headers", None) or {}
    value = request_headers.get("range", "")
    if request_headers.get("if-range") and request_headers["if-range"] != etag:
        value = ""
    start, end = 0, length - 1
    if value:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
        try:
            if not match or not length or not any(match.groups()):
                raise ValueError()
            if not match[1]:
                if int(match[2]) <= 0:
                    raise ValueError()
                start = max(0, length - int(match[2]))
            else:
                start = int(match[1])
                end = min(length - 1, int(match[2])) if match[2] else length - 1
            if start > end or start >= length:
                raise ValueError()
        except ValueError:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{length}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{length}"
    headers["Content-Length"] = str(max(0, end - start + 1))
    content_type = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if getattr(request, "method", "GET") == "HEAD":
        return Response(status_code=206 if value else 200, headers=headers, media_type=content_type)
    return StreamingResponse(read_range(path, start, end), status_code=206 if value else 200, headers=headers, media_type=content_type)
