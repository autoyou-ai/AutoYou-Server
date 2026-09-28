# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable


_PATH_COMPONENT_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def _normalize_prefixes(prefixes: Iterable[str]) -> tuple[str, ...]:
    normalized = []
    for prefix in prefixes:
        cleaned = prefix.replace("\\", "/").strip("/")
        if cleaned:
            normalized.append(cleaned)
    return tuple(sorted(set(normalized)))


def _normalize_relative_paths(paths: Iterable[str]) -> set[str]:
    normalized = set()
    for relative_path in paths:
        cleaned = str(relative_path).replace("\\", "/").strip("/")
        if cleaned:
            normalized.add(cleaned)
    return normalized


def _matches_prefix(relative_path: str, prefixes: tuple[str, ...]) -> bool:
    return any(relative_path == prefix or relative_path.startswith(f"{prefix}/") for prefix in prefixes)


def _build_markers(repo_root: Path, extra_markers: Iterable[str]) -> tuple[tuple[bytes, ...], tuple[bytes, ...]]:
    variants = {
        str(repo_root),
        repo_root.as_posix(),
        str(repo_root).replace("/", "\\"),
        str(repo_root).replace("\\", "/"),
    }
    resolved_root = repo_root.resolve()
    variants.update(
        {
            str(resolved_root),
            resolved_root.as_posix(),
            str(resolved_root).replace("/", "\\"),
            str(resolved_root).replace("\\", "/"),
        }
    )
    variants.update(marker for marker in extra_markers if marker)

    exact_markers = tuple(sorted({variant.encode("utf-8") for variant in variants if variant}))
    lowered_markers = tuple(sorted({variant.lower().encode("utf-8") for variant in variants if variant}))
    return exact_markers, lowered_markers


def _iter_files(bundle_root: Path, skip_prefixes: tuple[str, ...]):
    for current_root, dir_names, file_names in os.walk(bundle_root):
        current_root_path = Path(current_root)
        relative_root = current_root_path.relative_to(bundle_root)
        relative_root_text = "" if str(relative_root) == "." else relative_root.as_posix()

        kept_dirs = []
        for dir_name in dir_names:
            candidate_rel = dir_name if not relative_root_text else f"{relative_root_text}/{dir_name}"
            if not _matches_prefix(candidate_rel, skip_prefixes):
                kept_dirs.append(dir_name)
        dir_names[:] = kept_dirs

        for file_name in file_names:
            relative_path = file_name if not relative_root_text else f"{relative_root_text}/{file_name}"
            if _matches_prefix(relative_path, skip_prefixes):
                continue
            yield current_root_path / file_name, relative_path


def _marker_has_path_boundaries(
    data: bytes,
    index: int,
    marker_length: int,
    *,
    prefix_byte: int | None,
    final: bool,
) -> bool:
    before = data[index - 1] if index else prefix_byte
    after_index = index + marker_length
    if after_index < len(data):
        after = data[after_index]
    elif final:
        after = None
    else:
        return False
    return (
        (before is None or before not in _PATH_COMPONENT_BYTES)
        and (after is None or after not in _PATH_COMPONENT_BYTES)
    )


def _buffer_contains_marker(
    data: bytes,
    prefix_byte: int | None,
    markers: tuple[bytes, ...],
    *,
    final: bool,
) -> bool:
    for marker in markers:
        offset = 0
        while True:
            index = data.find(marker, offset)
            if index < 0:
                break
            if _marker_has_path_boundaries(
                data,
                index,
                len(marker),
                prefix_byte=prefix_byte,
                final=final,
            ):
                return True
            offset = index + 1
    return False


def _file_contains_marker(path: Path, exact_markers: tuple[bytes, ...], lowered_markers: tuple[bytes, ...]) -> bool:
    max_marker_length = max(len(marker) for marker in exact_markers + lowered_markers)
    overlap = max_marker_length
    remainder = b""
    remainder_prefix: int | None = None
    previous_byte: int | None = None

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                if not remainder:
                    return False
                lowered_buffer = remainder.lower()
                return _buffer_contains_marker(
                    remainder,
                    remainder_prefix,
                    exact_markers,
                    final=True,
                ) or _buffer_contains_marker(
                    lowered_buffer,
                    remainder_prefix,
                    lowered_markers,
                    final=True,
                )

            buffer = remainder + chunk
            buffer_prefix = remainder_prefix if remainder else previous_byte
            lowered_buffer = buffer.lower()
            if _buffer_contains_marker(buffer, buffer_prefix, exact_markers, final=False):
                return True
            if _buffer_contains_marker(lowered_buffer, buffer_prefix, lowered_markers, final=False):
                return True

            previous_byte = buffer[-1]
            if overlap > 0 and len(buffer) > overlap:
                cut_index = len(buffer) - overlap
                remainder_prefix = buffer[cut_index - 1]
                remainder = buffer[cut_index:]
            else:
                remainder_prefix = buffer_prefix
                remainder = buffer


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify compiled backend artifacts do not leak source paths.")
    parser.add_argument("--bundle-root", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--allow-source-dir", action="append", default=[])
    parser.add_argument("--allow-source-file", action="append", default=[])
    parser.add_argument("--skip-dir", action="append", default=[])
    parser.add_argument("--extra-marker", action="append", default=[])
    args = parser.parse_args()

    bundle_root = Path(args.bundle_root).resolve()
    repo_root = Path(args.repo_root).resolve()

    if not bundle_root.is_dir():
        raise SystemExit(f"Bundle root does not exist: {bundle_root}")

    allow_source_dirs = _normalize_prefixes(args.allow_source_dir)
    allow_source_files = _normalize_relative_paths(args.allow_source_file)
    skip_dirs = _normalize_prefixes(args.skip_dir)
    exact_markers, lowered_markers = _build_markers(repo_root, args.extra_marker)

    raw_python_sources: list[str] = []
    leaked_files: list[str] = []

    for file_path, relative_path in _iter_files(bundle_root, skip_dirs):
        relative_posix = relative_path.replace("\\", "/")

        if (
            file_path.suffix == ".py"
            and relative_posix not in allow_source_files
            and not _matches_prefix(relative_posix, allow_source_dirs)
        ):
            raw_python_sources.append(relative_posix)

        if _matches_prefix(relative_posix, allow_source_dirs):
            continue

        if _file_contains_marker(file_path, exact_markers, lowered_markers):
            leaked_files.append(relative_posix)

    if raw_python_sources:
        raise SystemExit(
            "Found bundled raw Python source files outside allowed directories: "
            + ", ".join(sorted(raw_python_sources)[:20])
        )

    if leaked_files:
        raise SystemExit(
            "Found build-machine source path markers inside bundled artifacts: "
            + ", ".join(sorted(leaked_files)[:20])
        )

    print(f"Backend hardening verified for {bundle_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
