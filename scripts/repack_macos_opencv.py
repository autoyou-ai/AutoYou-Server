#!/usr/bin/env python3
"""Replace FFmpeg in the pinned arm64 OpenCV wheel without changing its Python extension."""

import argparse
import ctypes
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from build_codec_clean_av import GPL_LIBRARY_NAMES, ffmpeg_configuration_problems, verified


OPENCV_WHEEL_SHA256 = "caf60c071ec391ba51ed00a4a920f996d0b64e3e46068aac1f646b5de0326a19"
FFMPEG_LIBS = ("avcodec", "avdevice", "avfilter", "avformat", "avutil", "swresample", "swscale")


def lib_version(path: Path) -> tuple[int, ...]:
    return tuple(map(int, path.name.split(".")[1:-1]))


def prune_unused_libraries(extension: Path, libs: Path) -> None:
    bundled = {path.name: path for path in libs.glob("*.dylib")}
    reachable = set()
    pending = [extension]
    while pending:
        binary = pending.pop()
        loads = subprocess.check_output(["otool", "-L", str(binary)], text=True).splitlines()
        for line in loads[2 if binary.suffix == ".dylib" else 1:]:
            dependency = Path(line.strip().split(" (compatibility version", 1)[0]).name
            if dependency in bundled and dependency not in reachable:
                reachable.add(dependency)
                pending.append(bundled[dependency])
    for name, path in bundled.items():
        if name not in reachable:
            path.unlink()


def repack(wheel: Path, ffmpeg: Path, destination: Path) -> None:
    verified(wheel, OPENCV_WHEEL_SHA256)
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="autoyou-opencv-") as temporary:
        subprocess.run([sys.executable, "-m", "wheel", "unpack", str(wheel), "-d", temporary], check=True)
        root, = Path(temporary).iterdir()
        libs = root / "cv2/.dylibs"
        target = {}
        for component in FFMPEG_LIBS:
            old, = libs.glob(f"lib{component}.*.dylib")
            new = (ffmpeg / "lib" / f"lib{component}.dylib").resolve(strict=True)
            if lib_version(old)[0] != lib_version(new)[0] or lib_version(new) < lib_version(old):
                raise SystemExit(f"Incompatible OpenCV dependency: {old.name} vs {new.name}")
            target[component] = old
            shutil.copyfile(new, old)

        for path in target.values():
            loads = subprocess.check_output(["otool", "-L", str(path)], text=True)
            args = ["install_name_tool", "-id", f"@loader_path/{path.name}"]
            # The first load entry is the dylib's own install name.
            for line in loads.splitlines()[2:]:
                dependency = line.strip().split(" (compatibility version", 1)[0]
                match = re.match(r"lib([^.]+)\.", Path(dependency).name)
                if match and match[1] in target:
                    args.extend(["-change", dependency, f"@loader_path/{target[match[1]].name}"])
            subprocess.run([*args, str(path)], check=True)
            subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True)

        bootstrap = root / "cv2/__init__.py"
        extension = bootstrap.parent / "cv2.abi3.so"
        text = bootstrap.read_text()
        old = '    native_module = importlib.import_module("cv2")'
        if text.count(old) != 1 or not extension.is_file():
            raise SystemExit("Unexpected OpenCV extension bootstrap")
        prune_unused_libraries(extension, libs)
        if any(path.name.startswith(GPL_LIBRARY_NAMES) for path in libs.glob("*.dylib")):
            raise SystemExit("OpenCV still loads a GPL codec")
        # The compiled launcher's overlay finder otherwise resolves the bootstrap again.
        text = text.replace("import importlib\n", "import importlib\nimport importlib.util\n", 1)
        bootstrap.write_text(text.replace(old, '''    native_spec = importlib.util.spec_from_file_location(
        "cv2", os.path.join(os.path.dirname(__file__), "cv2.abi3.so")
    )
    native_module = importlib.util.module_from_spec(native_spec)
    sys.modules["cv2"] = native_module
    native_spec.loader.exec_module(native_module)'''))
        metadata, = root.glob("*.dist-info/WHEEL")
        metadata.write_text(metadata.read_text().replace("macosx_13_0_arm64", "macosx_14_0_arm64"))
        subprocess.run([sys.executable, "-m", "wheel", "pack", str(root), "-d", str(destination)], check=True)


def check_installed() -> None:
    import cv2

    package = Path(cv2.__file__).resolve().parent
    library, = (package / ".dylibs").glob("libavcodec.*.dylib")
    codec = ctypes.CDLL(str(library))
    codec.avcodec_configuration.restype = ctypes.c_char_p
    codec.avcodec_license.restype = ctypes.c_char_p
    problems = ffmpeg_configuration_problems(codec.avcodec_configuration().decode(), ("--enable-videotoolbox",))
    if not codec.avcodec_license().startswith(b"LGPL version "):
        problems.append("OpenCV FFmpeg is not LGPL")
    for name in GPL_LIBRARY_NAMES:
        if any(package.parent.rglob(f"{name}*.dylib")):
            problems.append(f"{name} is present in the installed runtime")
    if problems:
        raise SystemExit("; ".join(problems))
    print("OpenCV imports with LGPL FFmpeg 7.1 and no x264 or x265 payloads.")


if __name__ == "__main__":
    if sys.argv[1:] == ["--check-installed"]:
        check_installed()
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("wheel", type=Path)
        parser.add_argument("ffmpeg", type=Path)
        parser.add_argument("destination", type=Path)
        args = parser.parse_args()
        repack(args.wheel, args.ffmpeg, args.destination)
