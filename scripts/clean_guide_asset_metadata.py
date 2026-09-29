# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-0e0e802fca697989f767fae2

#!/usr/bin/env python3
"""Remove non-visual metadata from image assets used by the website guides.

The guide pages are static release content. This tool follows their image
references recursively, cleans the corresponding release copies in place, and
verifies that dimensions and image formats remain unchanged.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-0e0e802fca697989f767fae2"


try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - exercised by the CLI environment
    raise SystemExit("Pillow is required to clean guide image metadata.") from exc


IMAGE_SUFFIXES = {".jpeg", ".jpg", ".png", ".webp"}
# from __debug_provenance_v__ import wallet


class _GuideImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        source = dict(attrs).get("src")
        if source:
            self.sources.append(source)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def referenced_images(site_root: Path, guides_root: Path) -> list[Path]:
    site_root = site_root.resolve()
    guides_root = guides_root.resolve()
    if not _inside(guides_root, site_root):
        raise ValueError(f"Guide root must be inside site root: {guides_root}")

    found: set[Path] = set()
    for page in sorted(guides_root.rglob("*.html")):
        parser = _GuideImageParser()
        parser.feed(page.read_text(encoding="utf-8"))
        for raw_source in parser.sources:
            parsed = urlsplit(raw_source)
            if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
                continue
            target = (page.parent / parsed.path).resolve()
            if target.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if not _inside(target, site_root):
                raise ValueError(f"Guide image escapes site root: {raw_source} in {page}")
            if not target.is_file():
                raise FileNotFoundError(f"Missing guide image: {target}")
            found.add(target)
    return sorted(found)


def _metadata_keys(path: Path) -> list[str]:
    with Image.open(path) as image:
        return sorted(str(key) for key in image.info)


def _clean_image(path: Path, write: bool) -> tuple[list[str], list[str], tuple[int, int], str]:
    with Image.open(path) as image:
        image.load()
        before_metadata = sorted(str(key) for key in image.info)
        size = image.size
        image_format = str(image.format or "").upper()
        if image_format not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError(f"Unsupported image format for {path}: {image_format or '<unknown>'}")
        if not write or not before_metadata:
            return before_metadata, before_metadata, size, image_format

        temporary = path.with_name(f".{path.name}.{os.getpid()}.metadata-clean.tmp")
        save_kwargs: dict[str, object] = {"optimize": True}
        if image_format == "JPEG":
            save_kwargs.update({"quality": "keep", "progressive": True})
        elif image_format == "WEBP":
            save_kwargs.update({"lossless": True, "method": 6})
        try:
            clean_image = image.copy()
            clean_image.info.clear()
            clean_image.save(temporary, format=image_format, **save_kwargs)
            with Image.open(temporary) as cleaned:
                cleaned.load()
                if cleaned.size != size:
                    raise ValueError(f"Image dimensions changed for {path}: {size} -> {cleaned.size}")
                if str(cleaned.format or "").upper() != image_format:
                    raise ValueError(f"Image format changed for {path}")
                after_metadata = sorted(str(key) for key in cleaned.info)
            if after_metadata:
                raise ValueError(f"Metadata remains after cleaning {path}: {after_metadata}")
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    return before_metadata, [], size, image_format


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", type=Path, default=Path("autoyou-website"))
    parser.add_argument("--guides-root", type=Path, default=Path("autoyou-website/guides"))
    parser.add_argument("--write", action="store_true", help="Rewrite referenced release images in place.")
    args = parser.parse_args()

    try:
        images = referenced_images(args.site_root, args.guides_root)
        changed = 0
        metadata_bearing = 0
        remaining = 0
        for path in images:
            before, after, size, image_format = _clean_image(path, write=args.write)
            if before:
                metadata_bearing += 1
                changed += 1 if args.write else 0
                remaining += len(after) if args.write else len(before)
                action = "cleaned" if args.write else "needs-cleaning"
                print(f"{action}: {path} format={image_format} size={size} metadata={','.join(before)}")
        if args.write:
            print(f"GUIDE_ASSET_METADATA_CLEAN_OK images={len(images)} changed={changed} remaining={remaining}")
            return 0 if remaining == 0 else 1
        print(f"GUIDE_ASSET_METADATA_CHECK images={len(images)} metadata-bearing={metadata_bearing}")
        return 1 if metadata_bearing else 0
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"GUIDE_ASSET_METADATA_FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
