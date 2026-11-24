"""Small previews of images, so a remote screen never downloads originals to draw a grid.

Thumbnails are made in memory from the stored bytes (decrypted on the way when
Maximus is on) and kept in a bounded in-memory cache only. Nothing is written to
disk, so a sealed library never leaves a readable preview behind.
"""
from __future__ import annotations

import io
import threading
from collections import OrderedDict

from shared import content_files

SIZES = (96, 192, 320, 640)
MAX_SOURCE_BYTES = 40 * 1024 * 1024
MAX_PIXELS = 80_000_000
CACHE_BYTES = 24 * 1024 * 1024

_CACHE: "OrderedDict[tuple, tuple[bytes, str]]" = OrderedDict()
_BYTES = 0
_LOCK = threading.Lock()


def nearest_size(value) -> int:
    try:
        wanted = int(value)
    except (TypeError, ValueError):
        wanted = 320
    return next((size for size in SIZES if size >= wanted), SIZES[-1])


def _read(path) -> bytes | None:
    if content_files.size(path) > MAX_SOURCE_BYTES:
        return None
    return b"".join(content_files.read_range(path))


def make(path, size: int) -> tuple[bytes, str] | None:
    """Shrink one stored image. None when it is not an image Pillow can read safely."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        data = _read(path)
        if not data:
            return None
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > MAX_PIXELS:
            return None
        image.draft("RGB", (size * 2, size * 2))
        image = ImageOps.exif_transpose(image)
        image.thumbnail((size, size))
        alpha = "A" in image.getbands() or image.info.get("transparency") is not None
        out = io.BytesIO()
        if alpha:
            image.convert("RGBA").save(out, "PNG", optimize=True)
            return out.getvalue(), "image/png"
        image.convert("RGB").save(out, "JPEG", quality=74)
        return out.getvalue(), "image/jpeg"
    except Exception:
        # A corrupt or unsupported file simply has no preview; the screen draws an icon.
        return None


def thumbnail(path, key: tuple, size: int) -> tuple[bytes, str] | None:
    global _BYTES
    cache_key = (*key, size)
    with _LOCK:
        if cache_key in _CACHE:
            _CACHE.move_to_end(cache_key)
            return _CACHE[cache_key]
    made = make(path, size)
    if made is None:
        return None
    with _LOCK:
        _CACHE[cache_key] = made
        _BYTES += len(made[0])
        while _BYTES > CACHE_BYTES and _CACHE:
            _, (old, _) = _CACHE.popitem(last=False)
            _BYTES -= len(old)
    return made


def clear() -> None:
    global _BYTES
    with _LOCK:
        _CACHE.clear()
        _BYTES = 0
