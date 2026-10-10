#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Build a Windows PyAV wheel without the GPL x264 and x265 libraries.

PyAV's Windows wheels link FFmpeg against x264 and x265 (GPL-2.0), which a
Microsoft Store package cannot redistribute without commercial licenses, and
deleting those DLLs breaks ``import av`` because ``avcodec`` imports them. This
builds the same PyAV release against BtbN's LGPL FFmpeg shared build
(``--enable-version3``, ``--disable-libx264 --disable-libx265``), which still
has libvpx (aiortc's VP8), libopus, OpenH264 and Media Foundation, and vendors
its DLLs into the wheel with delvewheel the way PyAV's own wheels are made.
AutoYou then encodes H.264 with ``shared.h264_encoders``.

    python scripts/build_codec_clean_av.py --work-dir D:\\build\\codec-clean-av

Pass the printed wheel to ``servers/windows/build-backend.ps1 -AvWheel``. The
FFmpeg archive and PyAV source are checked against pinned SHA-256 values; keep
a copy of the archive (``--ffmpeg-zip``) because BtbN prunes old builds.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

PYAV_VERSION = "16.1.0"
PYAV_SDIST_SHA256 = "a094b4fd87a3721dacf02794d3d2c82b8d712c85b9534437e82a8a978c175ffd"
FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-08-13-05/"
    "ffmpeg-n8.1.3-14-g330caae0c1-win64-lgpl-shared-8.1.zip"
)
FFMPEG_SHA256 = "1af9562d002b8e2ab66817616e8a9211e77ab4b0fef58c5c345f467dc0213729"
BUILD_TOOLS = ("setuptools==84.0.0", "cython==3.3.0", "wheel==0.48.0", "delvewheel==1.13.1")
# AutoYou's calls need these in FFmpeg: aiortc's VP8 and Opus, and H.264
# encoders that are not x264.
REQUIRED_FFMPEG_OPTIONS = ("--enable-libvpx", "--enable-libopus", "--enable-libopenh264")
FORBIDDEN_FFMPEG_OPTIONS = ("--enable-gpl", "--enable-nonfree", "--enable-libx264", "--enable-libx265")
GPL_LIBRARY_NAMES = ("libx264", "libx265")
REPO_ROOT = Path(__file__).resolve().parents[1]


class BuildError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verified(path: Path, expected: str) -> Path:
    actual = sha256(path)
    if actual != expected:
        raise BuildError(f"{path} has sha256 {actual}, expected {expected}")
    return path


def download(url: str, destination: Path, expected: str) -> Path:
    if destination.is_file() and sha256(destination) == expected:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    os.replace(partial, destination)
    return verified(destination, expected)


def ffmpeg_configuration_problems(configuration: str) -> list[str]:
    options = configuration.split()
    problems = [f"FFmpeg was configured with {option}" for option in FORBIDDEN_FFMPEG_OPTIONS if option in options]
    problems += [f"FFmpeg lacks {option}" for option in REQUIRED_FFMPEG_OPTIONS if option not in options]
    return problems


def extract_ffmpeg(archive: Path, work_dir: Path) -> Path:
    target = work_dir / "ffmpeg"
    shutil.rmtree(target, ignore_errors=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    roots = [path for path in target.iterdir() if (path / "include" / "libavcodec").is_dir()]
    if len(roots) != 1:
        raise BuildError(f"{archive} does not contain one FFmpeg tree with include/ and lib/")
    root = roots[0]
    configuration = subprocess.run(
        [str(root / "bin" / "ffmpeg.exe"), "-hide_banner", "-buildconf"],
        capture_output=True, text=True, check=True,
    ).stdout + subprocess.run(
        [str(root / "bin" / "ffmpeg.exe"), "-hide_banner", "-version"],
        capture_output=True, text=True, check=True,
    ).stdout
    problems = ffmpeg_configuration_problems(configuration)
    if problems:
        raise BuildError("; ".join(problems))
    return root


def wheel_problems(names: list[str]) -> list[str]:
    problems = [f"{name} is GPL" for name in names if any(lib in name.lower() for lib in GPL_LIBRARY_NAMES)]
    if not any(name.startswith("av.libs/avcodec-") for name in names):
        problems.append("the wheel does not carry its FFmpeg DLLs in av.libs")
    return problems


def run(command: list[str], **kwargs) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, **kwargs)


def build(python: Path, work_dir: Path, ffmpeg_zip: Path | None, output_dir: Path) -> Path:
    archive = verified(ffmpeg_zip, FFMPEG_SHA256) if ffmpeg_zip else download(
        FFMPEG_URL, work_dir / "downloads" / FFMPEG_URL.rsplit("/", 1)[1], FFMPEG_SHA256
    )
    ffmpeg = extract_ffmpeg(archive, work_dir)

    venv = work_dir / "build-venv"
    if not (venv / "Scripts" / "python.exe").is_file():
        run([str(python), "-m", "venv", str(venv)])
    venv_python = venv / "Scripts" / "python.exe"
    run([str(venv_python), "-m", "pip", "install", "--quiet", *BUILD_TOOLS])

    sources = work_dir / "src"
    sdist = sources / f"av-{PYAV_VERSION}.tar.gz"
    if not (sdist.is_file() and sha256(sdist) == PYAV_SDIST_SHA256):
        run([str(venv_python), "-m", "pip", "download", f"av=={PYAV_VERSION}",
             "--no-binary", "av", "--no-deps", "--dest", str(sources)])
    verified(sdist, PYAV_SDIST_SHA256)
    tree = sources / f"av-{PYAV_VERSION}"
    shutil.rmtree(tree, ignore_errors=True)
    with tarfile.open(sdist) as archive_file:
        archive_file.extractall(sources, filter="data")

    raw = work_dir / "raw-wheel"
    shutil.rmtree(raw, ignore_errors=True)
    run([str(venv_python), "setup.py", "-q", "bdist_wheel", f"--ffmpeg-dir={ffmpeg}", "-d", str(raw)], cwd=tree)
    built = sorted(raw.glob("av-*.whl"))
    if len(built) != 1:
        raise BuildError(f"expected one wheel in {raw}, found {built}")

    output_dir.mkdir(parents=True, exist_ok=True)
    run([str(venv_python), "-m", "delvewheel", "repair", "--add-path", str(ffmpeg / "bin"),
         "-w", str(output_dir), str(built[0])])
    wheel = output_dir / built[0].name
    with zipfile.ZipFile(wheel) as bundle:
        problems = wheel_problems(bundle.namelist())
    if problems:
        raise BuildError(f"{wheel}: " + "; ".join(problems))
    return wheel


def installed_av_problems() -> list[str]:
    """GPL encoders or DLLs in the PyAV installed for this interpreter."""
    import av

    problems = [f"PyAV has the {name} encoder" for name in GPL_LIBRARY_NAMES if name in av.codecs_available]
    libs = Path(av.__file__).resolve().parents[1] / "av.libs"
    if libs.is_dir():
        problems += [f"{path} is GPL" for path in libs.iterdir()
                     if any(lib in path.name.lower() for lib in GPL_LIBRARY_NAMES)]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / "build" / "codec-clean-av")
    parser.add_argument("--ffmpeg-zip", type=Path, help="local copy of the pinned BtbN archive")
    parser.add_argument("--python", type=Path, default=Path(sys.executable),
                        help="Python 3.11 or newer to build with (the wheel is abi3)")
    parser.add_argument("--output-dir", type=Path, help="default: <work-dir>/dist")
    parser.add_argument("--check-installed", action="store_true",
                        help="only check the PyAV installed for this interpreter, then exit")
    args = parser.parse_args(argv)
    if args.check_installed:
        problems = installed_av_problems()
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        if not problems:
            print("PyAV has no x264 or x265.")
        return 1 if problems else 0
    if os.name != "nt":
        parser.error("this builds the Windows wheel; run it on Windows")
    work_dir = args.work_dir.resolve()
    try:
        wheel = build(args.python, work_dir, args.ffmpeg_zip, (args.output_dir or work_dir / "dist").resolve())
    except (BuildError, subprocess.CalledProcessError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Codec-clean PyAV wheel: {wheel}")
    print(f"sha256: {sha256(wheel)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
