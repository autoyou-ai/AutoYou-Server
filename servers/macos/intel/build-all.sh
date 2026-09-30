#!/bin/bash
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS_SCRIPT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}/build"
TARGET_ARCH="x86_64"

usage() {
    cat <<'EOF'
Usage: ./servers/macos/intel/build-all.sh [shared macOS build options]

Builds the Intel/x86_64 macOS AutoYou server app and DMG by calling the shared
servers/macos/build-all.sh pipeline.

Defaults:
  --jobs is set to the host's logical CPU count unless you pass --jobs yourself.
  AUTOYOU_MACOS_NUITKA_GOOGLE_INCLUDE_MODE defaults to runtime for Intel builds.
EOF
}

default_build_jobs() {
    sysctl -n hw.logicalcpu 2>/dev/null || printf '1\n'
}

extract_jobs_arg() {
    local previous=""
    local arg=""
    for arg in "$@"; do
        if [[ "$previous" == "--jobs" ]]; then
            printf '%s\n' "$arg"
            return 0
        fi
        case "$arg" in
            --jobs=*)
                printf '%s\n' "${arg#--jobs=}"
                return 0
                ;;
        esac
        previous="$arg"
    done
    return 1
}

args_include_jobs() {
    local arg=""
    for arg in "$@"; do
        case "$arg" in
            --jobs|--jobs=*)
                return 0
                ;;
        esac
    done
    return 1
}

args_require_release_gate() {
    local arg=""
    for arg in "$@"; do
        case "$arg" in
            --release|--notarize)
                return 0
                ;;
        esac
    done
    return 1
}

run_strict_release_legal_gate() {
    if ! args_require_release_gate "$@"; then
        return 0
    fi

    echo "Running strict release legal gate..."
    python3 "${MACOS_SCRIPT_DIR}/../../scripts/check_release_legal_gates.py" --artifact-scope server --no-generate --strict-unknown-license || {
        echo "Release legal gate failed. Resolve open blockers before macOS Intel release packaging." >&2
        exit 1
    }
}

export_parallel_build_env() {
    local jobs="$1"
    export HOMEBREW_MAKE_JOBS="${HOMEBREW_MAKE_JOBS:-$jobs}"
    export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-$jobs}"
    export NINJAJOBS="${NINJAJOBS:-$jobs}"
    export npm_config_jobs="${npm_config_jobs:-$jobs}"
    case " ${MAKEFLAGS:-} " in
        *" -j"*|*" --jobs"*) ;;
        *) export MAKEFLAGS="${MAKEFLAGS:+$MAKEFLAGS }-j$jobs" ;;
    esac
}

python_supports_server_build() {
    local candidate="$1"
    "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

resolve_python() {
    local candidate=""
    local candidates=(
        "${PYTHON:-}"
        "/usr/local/bin/python3.12"
        "/usr/local/bin/python3.11"
        "/usr/local/bin/python3.10"
        "/opt/homebrew/bin/python3.12"
        "/opt/homebrew/bin/python3.11"
        "/opt/homebrew/bin/python3.10"
        "python3.12"
        "python3.11"
        "python3.10"
    )

    for candidate in "${candidates[@]}"; do
        [[ -n "$candidate" ]] || continue
        if [[ "$candidate" == */* ]]; then
            if [[ -x "$candidate" ]] && python_supports_server_build "$candidate"; then
                printf '%s\n' "$candidate"
                return 0
            fi
            continue
        fi
        if command -v "$candidate" >/dev/null 2>&1; then
            local resolved
            resolved="$(command -v "$candidate")"
            if python_supports_server_build "$resolved"; then
                printf '%s\n' "$resolved"
                return 0
            fi
        fi
    done

    return 1
}

verify_macho_arch() {
    local path="$1"
    local expected="$2"
    if ! command -v lipo >/dev/null 2>&1; then
        echo "lipo not found; skipping architecture check for $path"
        return 0
    fi
    local archs
    archs="$(lipo -archs "$path" 2>/dev/null || true)"
    if [[ " $archs " != *" $expected "* ]]; then
        echo "Expected $path to contain $expected; found: ${archs:-unknown}"
        return 1
    fi
    echo "Verified $path architecture: $archs"
}

frontend_failed_after_backend_success() {
    local frontend_log="${MACOS_SCRIPT_DIR}/build/logs/frontend.log"
    local backend_exe="${MACOS_SCRIPT_DIR}/build/backend/AutoYou.dist/AutoYouServer"

    [[ -x "$backend_exe" ]] || return 1
    [[ -f "$frontend_log" ]] || return 1
    grep -Eq "Build failed with both SPM and xcodebuild|xcrun: error: unable to lookup item 'PlatformPath'|requires Xcode" "$frontend_log"
}

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            usage
            exit 0
            ;;
    esac
done

host_arch="$(uname -m)"
if [[ "$host_arch" != "$TARGET_ARCH" && "${AUTOYOU_ALLOW_CROSS_ARCH_INTEL_BUILD:-0}" != "1" ]]; then
    echo "This Intel packaging wrapper must run on an x86_64 macOS host."
    echo "Detected: $host_arch"
    echo "Set AUTOYOU_ALLOW_CROSS_ARCH_INTEL_BUILD=1 only if your Python, Nuitka, Swift, Node, and native wheels all support x86_64."
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

PYTHON_BIN="$(resolve_python)" || {
    echo "Unable to find Python 3.10+ for the Intel server build."
    exit 1
}

echo "Building Intel AutoYou server with $("$PYTHON_BIN" --version 2>&1) at $PYTHON_BIN"

export AUTOYOU_MACOS_NUITKA_GOOGLE_INCLUDE_MODE="${AUTOYOU_MACOS_NUITKA_GOOGLE_INCLUDE_MODE:-runtime}"
run_strict_release_legal_gate "$@"

shared_build_args=(--python "$PYTHON_BIN")
BUILD_JOBS="$(extract_jobs_arg "$@" || true)"
if ! args_include_jobs "$@"; then
    BUILD_JOBS="${AUTOYOU_INTEL_BUILD_JOBS:-$(default_build_jobs)}"
    shared_build_args+=(--jobs "$BUILD_JOBS")
    echo "Using maximum logical CPU build jobs: $BUILD_JOBS"
fi
if [[ -n "$BUILD_JOBS" ]]; then
    export_parallel_build_env "$BUILD_JOBS"
    echo "Exported parallel native build jobs: $BUILD_JOBS"
fi

rm -f "${MACOS_SCRIPT_DIR}/build/logs/frontend.log"
set +e
"${MACOS_SCRIPT_DIR}/build-all.sh" "${shared_build_args[@]}" "$@"
shared_status=$?
set -e
if [[ "$shared_status" -ne 0 ]]; then
    if [[ "${AUTOYOU_INTEL_ENABLE_FALLBACK_HOST:-1}" == "1" ]] && frontend_failed_after_backend_success; then
        echo "Swift frontend could not be built on this Intel host; creating Intel fallback host app."
        "${SCRIPT_DIR}/create-fallback-app.sh" --build-dir "${MACOS_SCRIPT_DIR}/build"
        "${MACOS_SCRIPT_DIR}/sign-and-compress.sh" --build-dir "${MACOS_SCRIPT_DIR}/build"
    else
        exit "$shared_status"
    fi
fi

MACOS_APP="${MACOS_SCRIPT_DIR}/build/AutoYou.app"
INTEL_APP="${OUTPUT_DIR}/AutoYou.app"
MACOS_DMG="${MACOS_SCRIPT_DIR}/build/AutoYou.dmg"
INTEL_DMG="${OUTPUT_DIR}/AutoYou-macOS-${TARGET_ARCH}.dmg"

if [[ -d "$MACOS_APP" ]]; then
    verify_macho_arch "${MACOS_APP}/Contents/MacOS/AutoYou" "$TARGET_ARCH"

    for required_legal_file in LICENSE THIRD-PARTY-NOTICES.md NOTICE.txt sbom.cdx.json; do
        if [[ ! -f "${MACOS_APP}/Contents/Resources/Legal/${required_legal_file}" ]]; then
            echo "Missing server app legal file: ${MACOS_APP}/Contents/Resources/Legal/${required_legal_file}"
            exit 1
        fi
    done

    backend_executable=""
    for candidate in \
        "${MACOS_APP}/Contents/Resources/backend/AutoYou.dist/AutoYouServer" \
        "${MACOS_APP}/Contents/Resources/backend/AutoYouServer.dist/AutoYouServer" \
        "${MACOS_APP}/Contents/Resources/backend/autoyou_app.dist/AutoYouServer" \
        "${MACOS_APP}/Contents/Resources/backend/AutoYou.app/Contents/MacOS/AutoYouServer" \
        "${MACOS_APP}/Contents/Resources/backend/AutoYouServer.app/Contents/MacOS/AutoYouServer" \
        "${MACOS_APP}/Contents/Resources/backend/autoyou_app.app/Contents/MacOS/AutoYouServer"; do
        if [[ -f "$candidate" ]]; then
            backend_executable="$candidate"
            break
        fi
    done
    if [[ -n "$backend_executable" ]]; then
        verify_macho_arch "$backend_executable" "$TARGET_ARCH"
    else
        echo "Bundled backend executable not found in $MACOS_APP"
        exit 1
    fi

    rm -rf "$INTEL_APP"
    if command -v ditto >/dev/null 2>&1; then
        ditto --rsrc --extattr "$MACOS_APP" "$INTEL_APP"
    else
        cp -R "$MACOS_APP" "$INTEL_APP"
    fi
    echo "Copied Intel server app bundle to: $INTEL_APP"
fi

if [[ ! -f "$MACOS_DMG" ]]; then
    echo "Expected server DMG was not produced: $MACOS_DMG"
    echo "The shared macOS packager may have fallen back to ZIP because of low disk space."
    exit 1
fi

cp "$MACOS_DMG" "$INTEL_DMG"
if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$INTEL_DMG" | awk -v filename="$(basename "$INTEL_DMG")" '{print $1 "  " filename}' > "${INTEL_DMG}.sha256"
fi

echo "Copied Intel server DMG to: $INTEL_DMG"
