#!/bin/bash
# Build the Apple Silicon PyAV wheel used by the official v2 macOS DMG.
# Usage: build_codec_clean_av_macos.sh <work-dir> [python3.13]
set -euo pipefail
if [[ "$(uname -sm)" != "Darwin arm64" || $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage on Apple Silicon: $0 <work-dir> [python3.13]" >&2
  exit 2
fi
script_dir="$(cd "$(dirname "$0")" && pwd)"
python_cmd="${2:-python3}"
mkdir -p "$1"
cd "$1"
build_root="$PWD"
prefix="$build_root/ffmpeg"
jobs="$(sysctl -n hw.logicalcpu)"
export MACOSX_DEPLOYMENT_TARGET=14.0
export CFLAGS="-O3 -fPIC -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"
download() {
  local name="$1" url="$2" digest="$3"
  if [[ ! -f "$name" ]]; then
    curl -fL --retry 3 --max-time 180 -o "$name.part" "$url"
    mv "$name.part" "$name"
  fi
  printf '%s  %s\n' "$digest" "$name" | shasum -a 256 -c -
}
mkdir -p sources
cd sources
download libvpx-1.17.0.tar.gz https://codeload.github.com/webmproject/libvpx/tar.gz/refs/tags/v1.17.0 1020f184046187baa2985dbde38e0691f49c44088bca7a1842b0236c6081dc0a
download opus-1.6.1.tar.gz https://ftp.osuosl.org/pub/xiph/releases/opus/opus-1.6.1.tar.gz 6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1
download ffmpeg-8.1.3.tar.xz https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz 7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3
cd "$build_root"
mkdir -p build
cd build
tar -xf ../sources/libvpx-1.17.0.tar.gz
tar -xf ../sources/opus-1.6.1.tar.gz
tar -xf ../sources/ffmpeg-8.1.3.tar.xz
(
  cd libvpx-1.17.0
  ./configure --prefix="$prefix" --target=arm64-darwin20-gcc --disable-examples --disable-tools --disable-docs --disable-unit-tests --enable-pic --disable-shared --enable-static
  make -j "$jobs"
  make install
)
(
  cd opus-1.6.1
  ./configure --prefix="$prefix" --enable-static --disable-shared --disable-extra-programs --disable-doc --with-pic
  make -j "$jobs"
  make install
)
(
  cd ffmpeg-8.1.3
  ./configure --prefix="$prefix" --enable-version3 --disable-gpl --disable-nonfree --disable-libx264 --disable-libx265 --disable-autodetect --enable-libvpx --enable-libopus --enable-videotoolbox --enable-audiotoolbox --enable-shared --disable-static --disable-programs --disable-doc --disable-debug --enable-pic
  make -j "$jobs"
  make install
)

cd "$build_root"
export ARCHFLAGS="-arch arm64" _PYTHON_HOST_PLATFORM=macosx-14.0-arm64
"$python_cmd" -m venv "$build_root/wheel-env"
wheel_python="$build_root/wheel-env/bin/python"
"$wheel_python" -m pip install -q 'setuptools==84.2.1' 'Cython==3.3.0' 'wheel==0.48.0' 'delocate==0.13.0'
if [[ ! -f sources/av-16.1.0.tar.gz ]]; then
  source_url="$(curl -fsSL https://pypi.org/pypi/av/16.1.0/json | "$wheel_python" -c 'import json,sys; print(next(x["url"] for x in json.load(sys.stdin)["urls"] if x["packagetype"] == "sdist"))')"
  curl -fL --retry 3 -o sources/av-16.1.0.tar.gz.part "$source_url"
  mv sources/av-16.1.0.tar.gz.part sources/av-16.1.0.tar.gz
fi
printf '%s\n' 'a094b4fd87a3721dacf02794d3d2c82b8d712c85b9534437e82a8a978c175ffd  sources/av-16.1.0.tar.gz' | shasum -a 256 -c -
tar -xf sources/av-16.1.0.tar.gz -C build
cd build/av-16.1.0
"$wheel_python" setup.py -q build_ext -j "$jobs" bdist_wheel --ffmpeg-dir="$build_root/ffmpeg" -d "$build_root/raw-wheel"
"$build_root/wheel-env/bin/delocate-wheel" --require-archs arm64 -w "$build_root/dist" "$build_root"/raw-wheel/av-*arm64.whl
"$wheel_python" -m pip install --no-deps "$build_root"/dist/av-*arm64.whl
"$wheel_python" "$script_dir/build_codec_clean_av.py" --check-installed

notices="$build_root/dist/codec-notices"
mkdir -p "$notices"
cp "$build_root/build/ffmpeg-8.1.3/COPYING.LGPLv3" "$notices/FFmpeg-LGPLv3.txt"
cp "$build_root/build/ffmpeg-8.1.3/COPYING.GPLv3" "$notices/GPLv3.txt"
cp "$build_root/build/libvpx-1.17.0/LICENSE" "$notices/libvpx-LICENSE.txt"
cp "$build_root/build/libvpx-1.17.0/PATENTS" "$notices/libvpx-PATENTS.txt"
cp "$build_root/build/opus-1.6.1/COPYING" "$notices/Opus-COPYING.txt"
cat >"$notices/README.txt" <<'EOF'
PyAV 16.1.0 dynamically loads FFmpeg 8.1.3 (LGPL v3 or later).
FFmpeg includes libvpx 1.17.0 and Opus 1.6.1. H.264 encoding uses Apple's
VideoToolbox. The build does not include libx264, libx265, or OpenH264.

The matching sources and build script are retained with the release. The
versioned source archive contains the unmodified upstream tarballs and the
build flags for the arm64 macOS 14 wheel. PyAV can be rebuilt against a
compatible modified FFmpeg using the same build steps. To test a modified
library in the packaged app, replace both copies (in AutoYouRuntime and in
runtime_site_packages/av/.dylibs) with the corresponding arm64 dylib and
re-sign the local app after replacing the libraries.
EOF
printf 'Wheel: %s\n' "$build_root"/dist/av-*arm64.whl
