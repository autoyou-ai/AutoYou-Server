"""Bounded media delivery and guarded HTTP/HLS fetching."""

from __future__ import annotations

import base64
import hashlib
import hmac
from ipaddress import ip_address
import json
import mimetypes
from pathlib import Path
import re
import socket
import time
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .store import Store

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".ts", ".mpg", ".mpeg", ".ogv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".amr", ".caf", ".weba"}
DOCUMENT_EXTENSIONS = {".pdf", ".epub"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | DOCUMENT_EXTENSIONS
CONTENT_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
                 ".mkv": "video/x-matroska", ".ts": "video/mp2t", ".m3u8": "application/vnd.apple.mpegurl",
                 ".vtt": "text/vtt", ".m4s": "video/iso.segment", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
                 ".pdf": "application/pdf", ".epub": "application/epub+zip", ".mp3": "audio/mpeg",
                 ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".ogg": "audio/ogg",
                 ".oga": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".amr": "audio/amr",
                 ".caf": "audio/x-caf", ".weba": "audio/webm"}
CHUNK_BYTES = 256 * 1024


def media_kind(suffix: str, requested: str = "video") -> str:
    if suffix in IMAGE_EXTENSIONS:
        return "photo"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in DOCUMENT_EXTENSIONS:
        return "document"
    return requested if requested in {"movie", "episode", "video"} else "video"


def parse_name(name: str) -> dict:
    stem = Path(name).stem
    stem = re.sub(r"[._]+", " ", stem)
    episode = re.search(r"\bS(\d{1,2})\s*E(\d{1,3})\b|\b(\d{1,2})x(\d{1,3})\b", stem, re.I)
    year = re.search(r"\b((?:19|20)\d{2})\b", stem)
    result = {"kind": "movie" if year else "video", "year": int(year[1]) if year else None,
              "season": None, "episode": None, "series": None}
    cutoff = min([m.start() for m in (episode, year) if m] or [len(stem)])
    title = stem[:cutoff]
    title = re.sub(r"\b(?:2160p|1080p|720p|4k|bluray|webrip|web dl|h264|x264|x265|hevc)\b.*", "", title, flags=re.I)
    title = title.strip(" ()[]-") or stem.strip()
    if episode:
        result.update(kind="episode", series=title, season=int(episode[1] or episode[3]),
                      episode=int(episode[2] or episode[4]))
        title = f"{title} - S{result['season']:02} E{result['episode']:02}"
    result["title"] = title
    return result


def public_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or len(url) > 4096 or any(ord(c) < 32 for c in url)):
        raise ValueError("Use a public HTTP or HTTPS media URL without embedded credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid URL port") from exc
    if port and port not in {80, 443, 8080, 8443}:
        raise ValueError("Use an HTTP media port (80, 443, 8080 or 8443)")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("Private network media URLs are not allowed; add a local source folder instead")
    try:
        addresses = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("The media host could not be resolved") from exc
    if not addresses or any(not _public_address(entry[4][0]) for entry in addresses):
        raise ValueError("Private network media URLs are not allowed; add a local source folder instead")
    return url.strip()


def _public_address(value: str) -> bool:
    address = ip_address(value.split("%")[0])
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


class PublicResolver(AbstractResolver):
    """Validate the actual addresses used by aiohttp, including after redirects."""
    def __init__(self):
        self.resolver = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        rows = await self.resolver.resolve(host, port, family)
        if not rows or any(not _public_address(row["host"]) for row in rows):
            raise OSError("Private network destination blocked")
        return rows

    async def close(self):
        await self.resolver.close()


def http_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False),
                                 timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=45),
                                 auto_decompress=False, trust_env=False)


async def fetch_response(session, url: str, *, method="GET", headers=None):
    for _ in range(6):
        # Numeric IPs bypass the connector resolver, so validate every URL too.
        import asyncio
        url = await asyncio.to_thread(public_url, url)
        response = await session.request(method, url, headers=headers, allow_redirects=False)
        if response.status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("The media server returned an empty redirect")
            url = urljoin(url, location)
            continue
        return response
    raise ValueError("Too many media redirects")


def parse_range(value: str, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or (not match[1] and not match[2]) or size == 0:
        raise ValueError("Unsatisfiable range")
    if not match[1]:
        suffix = int(match[2])
        if suffix <= 0:
            raise ValueError("Unsatisfiable range")
        return max(0, size - suffix), size - 1
    start = int(match[1])
    end = min(int(match[2]), size - 1) if match[2] else size - 1
    if start >= size or start > end:
        raise ValueError("Unsatisfiable range")
    return start, end


def permitted_path(store: Store, raw: str) -> Path:
    path = Path(raw).resolve()
    roots = [store.media_dir.resolve()]
    for folder in store.folders(all_folders=True):
        if folder["root_path"] and not folder["relative_path"]:
            roots.append(Path(folder["root_path"]).resolve())
    if not any(path.is_relative_to(root) for root in roots):
        raise HTTPException(404, "Media is outside a configured library folder")
    if not path.is_file():
        raise HTTPException(404, "Media is missing. Rescan its source folder.")
    return path


def file_stream(path: Path, request: Request, *, media_type: str | None = None):
    stat = path.stat()
    size = stat.st_size
    etag = f'"{stat.st_mtime_ns:x}-{size:x}"'
    headers = {"Accept-Ranges": "bytes", "ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate",
               "X-Content-Type-Options": "nosniff"}
    range_header = request.headers.get("range", "")
    if request.headers.get("if-range") and request.headers["if-range"] != etag:
        range_header = ""
    if request.headers.get("if-none-match") == etag and not range_header:
        return Response(status_code=304, headers=headers)
    try:
        byte_range = parse_range(range_header, size)
    except ValueError:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    start, end = byte_range or (0, max(0, size - 1))
    length = end - start + 1 if size else 0
    headers["Content-Length"] = str(length)
    if byte_range:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    content_type = media_type or CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if request.method == "HEAD":
        return Response(status_code=206 if byte_range else 200, headers=headers, media_type=content_type)

    def chunks():
        # The descriptor closes even if the client disconnects mid-film.
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    return StreamingResponse(chunks(), status_code=206 if byte_range else 200, headers=headers, media_type=content_type)


def resource_ticket(store: Store, item_id: str, url: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps([item_id, url, int(time.time()) + 86400], separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(store.setting("stream_secret").encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def read_ticket(store: Store, item_id: str, ticket: str) -> str:
    try:
        payload, supplied = ticket.rsplit(".", 1)
        actual = hmac.new(store.setting("stream_secret").encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(actual, supplied):
            raise ValueError()
        bound_id, url, expires = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if item_id != bound_id or expires < time.time():
            raise ValueError()
        return url
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(403, "Invalid or expired media resource") from None


def rewrite_hls(store: Store, item_id: str, body: str, base_url: str) -> str:
    def rewrite(uri: str):
        # Relative to /api/stream/<id>, so path proxies keep their prefix.
        return f"{item_id}?ticket=" + resource_ticket(store, item_id, urljoin(base_url, uri))
    lines = []
    for line in body.splitlines():
        if line and not line.startswith("#"):
            line = rewrite(line.strip())
        else:
            line = re.sub(r'URI="([^"]+)"', lambda m: 'URI="' + rewrite(m[1]) + '"', line)
        lines.append(line)
    return "\n".join(lines) + "\n"


async def remote_stream(store: Store, item_id: str, url: str, request: Request):
    session = http_session()
    try:
        headers = {"Accept-Encoding": "identity", "User-Agent": "AutoYou-TV/1.0"}
        for key in ("Range", "If-Range", "If-None-Match"):
            if request.headers.get(key):
                headers[key] = request.headers[key]
        upstream = await fetch_response(session, url, method=request.method, headers=headers)
        safe_headers = {key: value for key, value in upstream.headers.items() if key.lower() in
                        {"content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified"}}
        safe_headers["X-Content-Type-Options"] = "nosniff"
        is_hls = "mpegurl" in upstream.headers.get("Content-Type", "").lower() or urlsplit(str(upstream.url)).path.lower().endswith(".m3u8")
        if upstream.status not in {200, 206, 304, 416}:
            raise HTTPException(502, "The upstream media server could not provide this stream")
        content_type = upstream.headers.get("Content-Type", "application/octet-stream").split(";")[0].lower()
        if upstream.status in {200, 206} and not is_hls and not (
            content_type.startswith(("video/", "audio/")) or content_type in {
                "application/octet-stream", "binary/octet-stream", "application/mp4", "text/vtt"}
        ):
            raise HTTPException(502, "This link returned a web page instead of media. Use Download in Studio for supported video sites.")
        if upstream.headers.get("Content-Encoding") not in {None, "identity"}:
            raise HTTPException(502, "The media server did not provide an uncompressed byte stream")
        if request.method == "HEAD" or upstream.status in {304, 416}:
            status = upstream.status
            upstream.close()
            await session.close()
            return Response(status_code=status, headers=safe_headers)
        if is_hls:
            body = await upstream.content.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024 or upstream.headers.get("Content-Encoding") not in {None, "identity"}:
                raise HTTPException(502, "The upstream HLS manifest is too large or uses unsupported encoding")
            manifest = rewrite_hls(store, item_id, body.decode("utf-8-sig"), str(upstream.url))
            upstream.close()
            await session.close()
            return Response(manifest, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})

        async def chunks():
            try:
                async for chunk in upstream.content.iter_chunked(CHUNK_BYTES):
                    yield chunk
            finally:
                upstream.close()
                await session.close()
        return StreamingResponse(chunks(), status_code=upstream.status, headers=safe_headers)
    except Exception as exc:
        await session.close()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, "Could not reach a public media stream") from None


def subtitles(store: Store, item: dict) -> list[dict]:
    if not item.get("file_path"):
        return []
    path = permitted_path(store, item["file_path"])
    rows = []
    for candidate in sorted(path.parent.glob(path.stem + "*")):
        if candidate.suffix.lower() in {".srt", ".vtt"} and candidate.is_file():
            try:
                permitted_path(store, str(candidate))
            except HTTPException:
                continue
            label = candidate.stem[len(path.stem):].strip(" ._-") or "Subtitles"
            rows.append({"id": hashlib.sha256(candidate.name.encode()).hexdigest()[:16], "label": label,
                         "language": label[:2] if re.match(r"^[a-z]{2}\b", label) else "en"})
    return rows[:20]


def subtitle_content(store: Store, item: dict, track_id: str) -> str:
    path = permitted_path(store, item["file_path"])
    for candidate in path.parent.glob(path.stem + "*"):
        if hashlib.sha256(candidate.name.encode()).hexdigest()[:16] != track_id or candidate.suffix.lower() not in {".srt", ".vtt"}:
            continue
        candidate = permitted_path(store, str(candidate))
        if candidate.stat().st_size > 5 * 1024 * 1024:
            raise HTTPException(413, "Subtitle file is too large")
        text = candidate.read_text(encoding="utf-8-sig", errors="replace")
        if candidate.suffix.lower() == ".srt":
            text = "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
        return text
    raise HTTPException(404, "Subtitle not found")
