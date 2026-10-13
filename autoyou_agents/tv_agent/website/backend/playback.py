"""Immutable, bounded HLS renditions for mobile and native players."""

from __future__ import annotations

import re
import json
from pathlib import Path

from fastapi import HTTPException

from .media import permitted_path

ID = re.compile(r"[a-f0-9]{24}\Z")
RESOURCE = re.compile(r"(?:master\.m3u8|\d{1,4}p/(?:index\.m3u8|segment-\d{5,}\.ts)|sub-\d+\.(?:m3u8|vtt))\Z")


def fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size:x}-{stat.st_mtime_ns:x}"


def rendition_root(store, item_id: str, version: str) -> Path:
    if not ID.fullmatch(item_id) or not ID.fullmatch(version):
        raise HTTPException(404, "Streaming version not available")
    return store.root / "renditions" / item_id / version


def ready(store, item: dict) -> bool:
    version = item["technical"].get("hls_version", "")
    if item["status"] == "deleted" or not item.get("file_path") or not ID.fullmatch(version):
        return False
    try:
        source = permitted_path(store, item["file_path"])
        return (fingerprint(source) == item["technical"].get("hls_source")
                and (rendition_root(store, item["id"], version) / "master.m3u8").is_file())
    except (HTTPException, OSError):
        return False


def resource_path(store, item: dict, version: str, resource: str) -> Path:
    # Removal of a source also revokes its prepared copies. No paths supplied by
    # the browser are interpreted outside the published, immutable version.
    if item["status"] == "deleted" or not item.get("file_path") or not RESOURCE.fullmatch(resource):
        raise HTTPException(404, "Streaming version not available")
    root = rendition_root(store, item["id"], version).resolve()
    if not root.is_relative_to(store.root.resolve()):
        raise HTTPException(404, "Streaming version not available")
    try:
        marker = root / "published.json"
        if marker.stat().st_size > 2048 or json.loads(marker.read_text())["source"] != fingerprint(permitted_path(store, item["file_path"])):
            raise ValueError("Stale version")
    except (OSError, ValueError, KeyError):
        raise HTTPException(404, "Streaming version not available") from None
    path = (root / resource).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Streaming segment not available")
    return path


def profiles(height: int | None) -> list[tuple[int, int]]:
    limit = max(2, int(height or 720)) // 2 * 2
    result = {}
    for size, bitrate in ((360, 550_000), (480, 1_100_000), (720, 2_300_000)):
        result.setdefault(min(size, limit), bitrate)
    return list(result.items())


def ffmpeg_args(ffmpeg: str, source: Path, destination: Path, height: int, bitrate: int) -> list[str]:
    return [ffmpeg, "-nostdin", "-hide_banner", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"scale=-2:min(ih\\,{height}),fps=30",
            "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main", "-pix_fmt", "yuv420p",
            "-threads", "2", "-b:v", str(bitrate), "-maxrate", str(bitrate), "-bufsize", str(bitrate * 2),
            "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-force_key_frames", "expr:gte(t,n_forced*2)",
            "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-f", "hls", "-hls_time", "2",
            "-hls_playlist_type", "vod", "-hls_flags", "independent_segments+temp_file",
            "-hls_segment_filename", str(destination / "segment-%05d.ts"),
            "-progress", "pipe:1", str(destination / "index.m3u8")]
