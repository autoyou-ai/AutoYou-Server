#!/bin/bash
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.


################################################################################
# AutoYou macOS Backend Build Script
#
# Handles compilation of Python backend to macOS executable using Nuitka,
# downloads and bundles Node.js, and compiles dependencies (Playwright, etc.)
#
# Usage:
#   ./build-backend.sh [OPTIONS]
#
# Options:
#   --type TYPE         Build type: "dev" (default) or "release"
#   --requirements TYPE Requirements: "binary-default" (default), "full", "training-full", "base", or "server-macos"
#   --include-cognee    Install the optional Cognee memory backend for packaging
#   --accept-terms      Accept current notices for a local noninteractive build
#   --full              Use connector/full features (same as --requirements full)
#   --python PYTHON     Python executable (default: python3)
#   --jobs N            Parallel compilation jobs (default: auto)
#   --no-sign           Skip code signing (default: sign with ad-hoc identity)
#   --help              Show this help message
#
# Examples:
#   ./build-backend.sh --type dev                    # Fast dev build with binary-default features
#   ./build-backend.sh --type dev --requirements base  # Minimal feature build
#   ./build-backend.sh --type release                # Default production build
#
################################################################################

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${BUILD_DIR:-${AUTOYOU_MACOS_SERVER_BUILD_DIR:-${SCRIPT_DIR}/build}}"
BACKEND_BUILD="${BUILD_DIR}/backend"
ARTIFACTS_DIR="${BUILD_DIR}/artifacts"
RUNTIME_MODULE_BUILDER="${PROJECT_ROOT}/scripts/build_packaged_runtime_modules.py"
PACKAGED_GUIDES_SCRIPT="${PROJECT_ROOT}/scripts/prepare_packaged_guides.py"
BACKEND_RUNTIME_PIN_READER="${PROJECT_ROOT}/scripts/read_backend_runtime_pins.py"
NONCOMMERCIAL_ASSET_PRUNER="${PROJECT_ROOT}/scripts/prune_noncommercial_release_assets.py"
REALTIMESTT_RUNTIME_INSTALLER="${PROJECT_ROOT}/scripts/install_realtimestt_runtime.py"
REALTIMESTT_RUNTIME_REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements/realtimestt-runtime.txt"
VERSION_FILE="$PROJECT_ROOT/VERSION"
RUNTIME_GUIDES_ROOT="${BUILD_DIR}/runtime-guides"

if [[ ! -f "$VERSION_FILE" ]]; then
    echo "Missing canonical version file: $VERSION_FILE" >&2
    exit 1
fi
if [[ ! -f "$PACKAGED_GUIDES_SCRIPT" ]]; then
    echo "Missing packaged guide staging helper: $PACKAGED_GUIDES_SCRIPT" >&2
    exit 1
fi

stage_packaged_guides() {
    "$PYTHON_CMD" "$PACKAGED_GUIDES_SCRIPT" \
        --repo-root "$PROJECT_ROOT" \
        --output-root "$RUNTIME_GUIDES_ROOT"
}
APP_VERSION="$(head -n 1 "$VERSION_FILE" | tr -d '\r')"
if [[ ! "$APP_VERSION" =~ ^[0-9]+[.][0-9]+[.][0-9]+([.][0-9]+)?$ ]]; then
    echo "Invalid canonical version: $APP_VERSION" >&2
    exit 1
fi

# Build configuration
BUILD_TYPE="dev"
PYTHON_CMD="python3"
PYTHON_CMD_EXPLICIT=false
DEFAULT_LOGICAL_CPUS="$(sysctl -n hw.logicalcpu 2>/dev/null || echo 1)"
NUITKA_JOBS="$DEFAULT_LOGICAL_CPUS"
NUITKA_JOBS_EXPLICIT=false
NUITKA_WARN_FREE_SPACE_BYTES=$((8 * 1024 * 1024 * 1024))
NUITKA_MIN_FREE_SPACE_BYTES=$((6 * 1024 * 1024 * 1024))
VERBOSE=false
REQUIREMENTS_TYPE="binary-default"
INCLUDE_COGNEE=false
CLEAN_ONLY=false
SIGN_BINARIES=true
GOOGLE_NUITKA_INCLUDE_MODE="${AUTOYOU_MACOS_NUITKA_GOOGLE_INCLUDE_MODE:-broad}"
# When true, reuse an existing Nuitka launcher build under $BACKEND_BUILD and
# only re-run the cheap post-compile asset pipeline (runtime_modules, Node,
# tunnelmole, whisper, fonts, hardening, sign, rename). This is the macOS
# equivalent of the Windows `build-all.ps1 -SkipBackend` fast path: it avoids
# the ~3.5h launcher recompile when iterating on post-compile fixes.
SKIP_NUITKA=false
ACCEPT_TERMS=false
SKIP_LOCAL_BUILD_ACK=false

case "$GOOGLE_NUITKA_INCLUDE_MODE" in
    broad|targeted|runtime)
        ;;
    *)
        echo -e "${RED}Unsupported AUTOYOU_MACOS_NUITKA_GOOGLE_INCLUDE_MODE: $GOOGLE_NUITKA_INCLUDE_MODE${NC}"
        echo -e "${RED}Choose broad, targeted, or runtime.${NC}"
        exit 1
        ;;
esac

requirements_has_voice() {
    case "$REQUIREMENTS_TYPE" in
        full|source-full|connector-full|training-full|voice)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

requirements_has_tuning() {
    [[ "$REQUIREMENTS_TYPE" == "training-full" ]]
}

include_cognee_enabled() {
    local env_value
    env_value="$(printf '%s' "${AUTOYOU_INCLUDE_COGNEE:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$INCLUDE_COGNEE" == true || "$env_value" =~ ^(1|true|yes|on)$ ]]
}

# These desktop-automation / PyObjC-backed / heavy runtime packages must stay
# out of the launcher graph on macOS. They are copied into runtime_site_packages
# and imported only after the packaged runtime paths are configured.
MACOS_RUNTIME_SITE_PACKAGE_OVERLAY=(
    "anyio"
    "bless"
    "bleak"
    "certifi"
    "charset_normalizer"
    "docker"
    "greenlet"
    "h11"
    "httpcore"
    "httpx"
    "idna"
    "mss"
    "onnxruntime"
    "playwright"
    "pyaudio"
    "pyaes"
    "pyasn1"
    "pyee"
    "pyautogui"
    "pygetwindow"
    "pymsgbox"
    "pyotp"
    "pyperclip"
    "pysignalclirestapi"
    "pysodium"
    "pyscreeze"
    "pytweening"
    "pyrect"
    "requests"
    "rsa"
    "six"
    "telegram"
    "telethon"
    "typing_extensions"
    "urllib3"
    "mouseinfo"
    "AppKit"
    "Cocoa"
    "CoreBluetooth"
    "CoreFoundation"
    "Foundation"
    "libdispatch"
    "Quartz"
    "objc"
    "PyObjCTools"
    "yt_dlp"
)

# Intel runtime-only launcher builds keep these third-party distributions out
# of the Nuitka launcher graph and copy their installed distribution closure
# into runtime_site_packages instead. This preserves runtime capability while
# avoiding multi-hour google/litellm/aiortc graph analysis on Intel machines.
MACOS_RUNTIME_SITE_PACKAGE_DISTRIBUTION_OVERLAY=(
    "bless"
    "bleak"
    "aiortc"
    "aiohttp"
    "fastapi"
    "starlette"
    "pydantic"
    "uvicorn"
    "python-dotenv"
    "cryptography"
    "requests"
    "beautifulsoup4"
    "qrcode"
    "python-multipart"
    "ollama"
    "httpx"
    "h2"
    "hpack"
    "hyperframe"
    "python-telegram-bot"
    "Telethon"
    "websockets"
    "zeroconf"
    "docker"
    "keyring"
    "pyotp"
    "pysodium"
    "psutil"
    "pysignalclirestapi"
    "playwright"
    "yt-dlp"
    "SQLAlchemy"
    "aiosqlite"
    "greenlet"
    "google-adk"
    "google-genai"
    "google-cloud-aiplatform"
    "onnxruntime"
    "litellm"
    "huggingface-hub"
)

# Logging functions
log() {
    echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

build_runtime_modules_bundle() {
    local resources_root="$1"
    local runtime_modules_root="${resources_root}/runtime_modules"
    local runtime_modules_build_root="${BUILD_DIR}/runtime-modules-build"
    local runtime_integrity_manifest="${resources_root}/runtime_integrity.json"

    if [[ ! -f "$RUNTIME_MODULE_BUILDER" ]]; then
        log_error "Packaged runtime module builder not found at $RUNTIME_MODULE_BUILDER"
        return 1
    fi

    rm -rf "$runtime_modules_build_root" "${resources_root}/runtime_source" "$runtime_modules_root"
    mkdir -p "$runtime_modules_build_root"

    log "Compiling packaged runtime modules into $runtime_modules_root..."
    local desktop_args=()
    if [[ "${AUTOYOU_BUILD_DESKTOP_V2:-0}" == "1" ]]; then
        desktop_args+=(--desktop)
    else
        desktop_args+=(--include-sibling-agents)
    fi
    if requirements_has_voice; then
        desktop_args+=(--include-emotivoice)
    fi
    "$PYTHON_CMD" "$RUNTIME_MODULE_BUILDER" \
        --repo-root "$PROJECT_ROOT" \
        --bundle-root "$resources_root" \
        --build-root "$runtime_modules_build_root" \
        --jobs "$NUITKA_JOBS" \
        --nuitka-arg=--disable-plugin=transformers \
        ${desktop_args[@]+"${desktop_args[@]}"}

    if [[ ! -d "$runtime_modules_root" ]]; then
        log_error "Expected compiled runtime modules directory at $runtime_modules_root"
        return 1
    fi
    if [[ ! -f "$runtime_integrity_manifest" ]]; then
        log_error "Expected runtime integrity manifest at $runtime_integrity_manifest"
        return 1
    fi

    local libsodium_source="${runtime_modules_root}/shared/native/libsodium"
    local libsodium_compat="${resources_root}/shared/native/libsodium"
    if [[ -d "$libsodium_source" ]]; then
        log "Installing packaged libsodium compatibility sidecar..."
        rm -rf "$libsodium_compat"
        mkdir -p "$(dirname "$libsodium_compat")"
        cp -R "$libsodium_source" "$libsodium_compat"

        # The repository currently carries the Apple Silicon sidecar. On an
        # Intel host, use the native Homebrew library when the x86_64 sidecar
        # is not present so pairing verification and runtime loading remain
        # architecture-correct without changing the ARM64 source asset.
        if [[ "$(uname -m)" == "x86_64" && ! -f "${libsodium_source}/darwin-x86_64/libsodium.dylib" ]]; then
            local host_libsodium=""
            if command -v brew >/dev/null 2>&1; then
                host_libsodium="$(brew --prefix libsodium 2>/dev/null)/lib/libsodium.dylib"
            fi
            if [[ -f "$host_libsodium" ]]; then
                mkdir -p "${libsodium_source}/darwin-x86_64" "${libsodium_compat}/darwin-x86_64"
                cp -p "$host_libsodium" "${libsodium_source}/darwin-x86_64/libsodium.dylib"
                cp -p "$host_libsodium" "${libsodium_compat}/darwin-x86_64/libsodium.dylib"
                log "Added Intel libsodium sidecar from Homebrew"
            else
                log_warning "Intel libsodium sidecar is missing and Homebrew libsodium was not found"
            fi
        fi
    fi

    log_success "Compiled runtime modules installed: $runtime_modules_root"
}

get_python_site_package_roots() {
    "$PYTHON_CMD" - <<'PY'
import sysconfig

seen = set()
for key in ("purelib", "platlib"):
    value = sysconfig.get_path(key)
    if value and value not in seen:
        seen.add(value)
        print(value)
PY
}

copy_runtime_overlay_path() {
    local kind="$1"
    local source_path="$2"
    local destination_path="$3"

    mkdir -p "$(dirname "$destination_path")"
    if [[ "$kind" == "DIR" ]]; then
        rm -rf "$destination_path"
        if command -v ditto >/dev/null 2>&1; then
            ditto "$source_path" "$destination_path"
        else
            cp -pR "$source_path" "$destination_path"
        fi
    else
        cp -p "$source_path" "$destination_path"
    fi
}

install_runtime_site_packages_overlay() {
    local resources_root="$1"
    local runtime_site_packages_root="${resources_root}/runtime_site_packages"
    local distribution_overlay=("${MACOS_RUNTIME_SITE_PACKAGE_DISTRIBUTION_OVERLAY[@]}")
    local overlay_output=""
    local dist_overlay_output=""
    local line=""
    local kind=""
    local source_path=""
    local relative_path=""
    local copied_any=false
    local missing_modules=()
    local missing_distributions=()

    rm -rf "$runtime_site_packages_root"
    mkdir -p "$runtime_site_packages_root"

    if requirements_has_voice; then
        # Voice/full profiles install these heavy native packages.
        MACOS_RUNTIME_SITE_PACKAGE_OVERLAY+=("torch" "torchgen" "torchaudio")
        distribution_overlay+=(
            "torch"
            "transformers"
            "huggingface-hub"
            "safetensors"
            "tokenizers"
            "realtimestt"
            "faster-whisper"
            "ctranslate2"
            "scipy"
            "yacs"
            "g2p-en"
            "jieba"
            "pypinyin"
            "pypinyin-dict"
            "cn2an"
            "numba"
            "soundfile"
            "nltk"
            "modelscope"
            "tqdm"
            "sentence-stream"
            "onnxruntime"
        )
    fi
    if requirements_has_tuning; then
        MACOS_RUNTIME_SITE_PACKAGE_OVERLAY+=("torch" "torchgen" "torchvision" "transformers" "datasets" "peft" "accelerate" "safetensors" "sentencepiece" "PIL")
        distribution_overlay+=(
            "torch"
            "torchvision"
            "transformers"
            "datasets"
            "peft"
            "accelerate"
            "safetensors"
            "sentencepiece"
            "Pillow"
            "psutil"
        )
    fi

    # cryptg only ships wheels for CPython 3.11+; requirements install it with a
    # matching marker, so include it only when this interpreter actually has it.
    if "$PYTHON_CMD" -c "import cryptg" >/dev/null 2>&1; then
        distribution_overlay+=("cryptg")
    fi

    log "Installing runtime site-packages overlay for macOS desktop automation..."

    if ! overlay_output=$(
        "$PYTHON_CMD" - "${MACOS_RUNTIME_SITE_PACKAGE_OVERLAY[@]}" <<'PY'
import importlib.util
import sys
import sysconfig
from pathlib import Path

roots = []
for key in ("purelib", "platlib"):
    value = sysconfig.get_path(key)
    if value and value not in roots:
        roots.append(value)

for module_name in sys.argv[1:]:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        print(f"MISSING\t{module_name}")
        continue

    origin = None
    kind = None
    if spec.submodule_search_locations:
        origin = Path(next(iter(spec.submodule_search_locations))).resolve()
        kind = "DIR"
    elif spec.origin and spec.origin not in {"built-in", "frozen"}:
        origin = Path(spec.origin).resolve()
        kind = "FILE"

    if origin is None:
        print(f"MISSING\t{module_name}")
        continue

    relative = None
    for root in roots:
        try:
            relative = origin.relative_to(Path(root).resolve())
            break
        except Exception:
            continue

    if relative is None:
        print(f"MISSING\t{module_name}")
        continue

    print(f"{kind}\t{origin}\t{relative.as_posix()}")
PY
    ); then
        log_error "Failed to resolve runtime site-packages overlay paths"
        return 1
    fi

    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        IFS=$'\t' read -r kind source_path relative_path <<<"$line"
        if [[ "$kind" == "MISSING" ]]; then
            log_warning "Runtime overlay package not found for interpreter ${PYTHON_CMD}: $source_path"
            missing_modules+=("$source_path")
            continue
        fi
        if [[ -z "$source_path" || -z "$relative_path" ]]; then
            continue
        fi

        local destination_path="${runtime_site_packages_root}/${relative_path}"
        copy_runtime_overlay_path "$kind" "$source_path" "$destination_path"
        copied_any=true
    done <<<"$overlay_output"

    if [[ "$GOOGLE_NUITKA_INCLUDE_MODE" == "runtime" ]]; then
        log "Installing runtime distribution overlay closure for Intel runtime-only launcher..."
    elif requirements_has_tuning; then
        log "Installing runtime distribution overlay closure for training dependencies..."
    elif requirements_has_voice; then
        log "Installing runtime distribution overlay closure for voice dependencies..."
    fi
    if [[ "$GOOGLE_NUITKA_INCLUDE_MODE" == "runtime" ]] || requirements_has_tuning || requirements_has_voice; then

        if ! dist_overlay_output=$(
            "$PYTHON_CMD" - "$runtime_site_packages_root" "${distribution_overlay[@]}" <<'PY'
from __future__ import annotations

import importlib.metadata as metadata
import os
import re
import shutil
import sys
import sysconfig
from pathlib import Path

try:
    from packaging.requirements import Requirement
except Exception as exc:
    raise SystemExit(f"packaging is required to resolve runtime distribution dependencies: {exc}")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "").strip()).lower()


target_root = Path(sys.argv[1]).resolve()
root_names = [name for name in sys.argv[2:] if str(name).strip()]

site_roots: list[Path] = []
for key in ("purelib", "platlib"):
    value = sysconfig.get_path(key)
    if value:
        candidate = Path(value).resolve()
        if candidate not in site_roots:
            site_roots.append(candidate)

distributions = {}
for dist in metadata.distributions():
    dist_name = dist.metadata.get("Name") or dist.metadata.get("Summary") or ""
    normalized = normalize_name(dist_name)
    if normalized and normalized not in distributions:
        distributions[normalized] = dist

missing: list[str] = []
queued: list[str] = []
seen: set[str] = set()

for root_name in root_names:
    normalized = normalize_name(root_name)
    if normalized in distributions:
        queued.append(normalized)
    else:
        missing.append(root_name)

copied_files = 0
copied_dists: list[str] = []

while queued:
    current = queued.pop(0)
    if current in seen:
        continue
    seen.add(current)

    dist = distributions.get(current)
    if dist is None:
        continue

    copied_dists.append(dist.metadata.get("Name") or current)

    for requirement_text in dist.requires or ():
        try:
            requirement = Requirement(requirement_text)
        except Exception:
            continue

        if requirement.marker is not None:
            try:
                if not requirement.marker.evaluate({"extra": ""}):
                    continue
            except Exception:
                continue

        dependency_name = normalize_name(requirement.name)
        if dependency_name and dependency_name in distributions and dependency_name not in seen:
            queued.append(dependency_name)

    for package_file in dist.files or ():
        source = Path(dist.locate_file(package_file)).resolve()
        if not source.is_file() and not source.is_symlink():
            continue
        if "__pycache__" in source.parts or source.suffix in {".pyc", ".pyo"}:
            continue

        relative_path = None
        for site_root in site_roots:
            try:
                relative_path = source.relative_to(site_root)
                break
            except ValueError:
                continue

        if relative_path is None:
            continue

        destination = target_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)

        if source.is_symlink():
            if destination.exists() or destination.is_symlink():
                destination.unlink()
            os.symlink(os.readlink(source), destination)
        else:
            shutil.copy2(source, destination)
        copied_files += 1

for missing_name in missing:
    print(f"MISSING\t{missing_name}")

print(f"COPIED\t{len(copied_dists)}\t{copied_files}")
for dist_name in sorted(copied_dists, key=str.lower):
    print(f"DIST\t{dist_name}")
PY
        ); then
            log_error "Failed to copy runtime distribution overlay closure"
            return 1
        fi

        while IFS= read -r line; do
            [[ -n "$line" ]] || continue
            IFS=$'\t' read -r kind source_path relative_path <<<"$line"
            case "$kind" in
                MISSING)
                    log_warning "Runtime overlay distribution not found for interpreter ${PYTHON_CMD}: $source_path"
                    missing_distributions+=("$source_path")
                    ;;
                COPIED)
                    log "Runtime distribution overlay copied ${source_path:-0} distributions / ${relative_path:-0} files"
                    copied_any=true
                    ;;
                DIST)
                    log "  distribution: $source_path"
                    ;;
            esac
        done <<<"$dist_overlay_output"
    fi

    find "$runtime_site_packages_root" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
    find "$runtime_site_packages_root" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true

    if [[ "$REQUIREMENTS_TYPE" != "base" && ${#missing_modules[@]} -gt 0 ]]; then
        log_error "Required runtime overlay packages are missing: ${missing_modules[*]}"
        return 1
    fi
    if [[ "$REQUIREMENTS_TYPE" != "base" && ${#missing_distributions[@]} -gt 0 ]]; then
        log_error "Required runtime overlay distributions are missing: ${missing_distributions[*]}"
        return 1
    fi

    if [[ "$copied_any" == true ]]; then
        log_success "Runtime site-packages overlay installed: $runtime_site_packages_root"
    else
        log_warning "Runtime site-packages overlay is empty; desktop automation backends will be unavailable."
    fi
}

sync_runtime_adk_browser_bundle() {
    local resources_root="$1"

    if [[ "$GOOGLE_NUITKA_INCLUDE_MODE" != "runtime" ]]; then
        return 0
    fi

    local source_browser="${resources_root}/runtime_site_packages/google/adk/cli/browser"
    local target_browser="${resources_root}/google/adk/cli/browser"

    if [[ ! -d "$source_browser" ]]; then
        log_warning "ADK browser assets not found in runtime overlay: $source_browser"
        return 0
    fi

    log "Syncing ADK browser assets from runtime overlay..."
    mkdir -p "$target_browser"
    rsync -a \
        --exclude='*.py' \
        --exclude='__pycache__' \
        "$source_browser/" "$target_browser/"
    log_success "ADK browser assets synced: $target_browser"
}

install_runtime_stdlib_overlay() {
    local resources_root="$1"

    if [[ "$GOOGLE_NUITKA_INCLUDE_MODE" != "runtime" ]]; then
        return 0
    fi

    local runtime_stdlib_root="${resources_root}/runtime_stdlib"
    local stdlib_root=""

    if ! stdlib_root=$("$PYTHON_CMD" - <<'PY'
import sysconfig

stdlib = sysconfig.get_path("stdlib")
if stdlib:
    print(stdlib)
PY
    ); then
        log_error "Failed to resolve Python stdlib path for runtime overlay"
        return 1
    fi

    if [[ -z "$stdlib_root" || ! -d "$stdlib_root" ]]; then
        log_error "Python stdlib path not found for runtime overlay: ${stdlib_root:-<empty>}"
        return 1
    fi

    log "Installing runtime stdlib overlay for Intel runtime-only launcher..."
    rm -rf "$runtime_stdlib_root"
    mkdir -p "$runtime_stdlib_root"
    rsync -a --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.pyo' \
        --exclude='site-packages' \
        --exclude='dist-packages' \
        --exclude='test' \
        --exclude='tests' \
        --exclude='idlelib' \
        --exclude='turtledemo' \
        --exclude='ensurepip' \
        "$stdlib_root/" "$runtime_stdlib_root/"

    local config_dir=""
    local libpython_link=""
    config_dir="$(find "$runtime_stdlib_root" -maxdepth 1 -type d -name 'config-*-darwin' -print -quit 2>/dev/null || true)"
    if [[ -n "$config_dir" && -f "${resources_root}/Python" ]]; then
        for libpython_link in "$config_dir"/libpython*.a "$config_dir"/libpython*.dylib; do
            [[ -L "$libpython_link" ]] || continue
            if [[ "$(readlink "$libpython_link")" == "../../../Python" ]]; then
                ln -sf "../../Python" "$libpython_link"
            fi
        done
    fi

    relocate_runtime_stdlib_extensions "$resources_root"
    log_success "Runtime stdlib overlay installed: $runtime_stdlib_root"
}

relocate_runtime_stdlib_extensions() {
    local resources_root="$1"
    local stdlib_root="${resources_root}/runtime_stdlib"
    local module dep
    if [[ "${AUTOYOU_BUILD_DESKTOP_V2:-0}" == "1" ]]; then
        # The native desktop UI never loads Tcl/Tk; do not retain its external
        # Python.framework dependencies through the optional stdlib overlay.
        rm -rf "${stdlib_root}/tkinter"
        rm -f "${stdlib_root}"/lib-dynload/_tkinter*.so
    fi
    for module in "${stdlib_root}"/lib-dynload/*.so; do
        [[ -f "$module" ]] || continue
        while IFS= read -r dep; do
            case "$dep" in
                /Library/Frameworks/Python.framework/Versions/*/lib/*.dylib)
                    bundle_macos_dylib_closure "$dep" "${stdlib_root}/lib-dynload"
                    install_name_tool -change "$dep" "@loader_path/$(basename "$dep")" "$module"
                    ;;
            esac
        done < <(otool -l "$module" | awk '
            $1 == "cmd" { load = ($2 == "LC_LOAD_DYLIB" || $2 == "LC_LOAD_WEAK_DYLIB") }
            load && $1 == "name" { print $2; load = 0 }
        ' | sort -u)
    done
}

# Strip import-generated bytecode caches from runtime_modules/.
#
# runtime_integrity.json tracks intentional package bridges, including the
# optional desktop client packages. Preserve tracked files when cleaning caches.
#
# Currently the macOS pipeline does not execute anything from runtime_modules/
# between build_runtime_modules_bundle() and verify_backend_hardening(), so
# there is no production source of fresh bytecode here. This function is a
# belt-and-braces defense so that if anyone later adds an import smoke test
# (as the Windows pipeline has via Assert-PackagedBackendServerImports) the
# bytecode those imports create is scrubbed before the hardening verifier
# runs, matching the Windows build flow.
scrub_runtime_modules_bytecode() {
    local bundle_root="$1"
    local runtime_modules_root="${bundle_root}/runtime_modules"

    if [[ ! -d "$runtime_modules_root" ]]; then
        return 0
    fi

    log "Scrubbing Python bytecode caches from $runtime_modules_root..."
    "$PYTHON_CMD" - "$bundle_root" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
tracked = json.loads((root / "runtime_integrity.json").read_text())["files"]
modules = root / "runtime_modules"
for path in modules.rglob("*"):
    if path.is_file() and path.suffix in {".pyc", ".pyo"} and path.relative_to(root).as_posix() not in tracked:
        path.unlink()
for path in sorted(modules.rglob("__pycache__"), reverse=True):
    if path.is_dir() and not any(path.iterdir()):
        path.rmdir()
PY
}

prune_noncommercial_release_assets() {
    local root="$1"
    [[ -e "$root" ]] || return 0
    if [[ ! -f "$NONCOMMERCIAL_ASSET_PRUNER" ]]; then
        log_error "Non-commercial asset pruner not found at $NONCOMMERCIAL_ASSET_PRUNER"
        return 1
    fi
    "$PYTHON_CMD" "$NONCOMMERCIAL_ASSET_PRUNER" --prune --root "$root"
}

prune_noncommercial_site_packages() {
    local site_root
    while IFS= read -r site_root; do
        [[ -n "$site_root" ]] || continue
        prune_noncommercial_release_assets "$site_root"
    done < <(get_python_site_package_roots)
}

auto_detect_nuitka_jobs() {
    local logical_cpus="$1"
    local mem_bytes="$2"
    # The launcher pulls in google.genai.types - a single pydantic module whose
    # generated C unit needs well over 6 GiB under clang -O3. Reserving 8 GiB
    # per parallel job makes a 16 GiB machine compile serially (the only safe
    # choice for that workload) while 24+ GiB machines still parallelize.
    local mem_per_job_bytes=$((8 * 1024 * 1024 * 1024))
    local jobs_by_mem=1

    if ! [[ "$logical_cpus" =~ ^[0-9]+$ ]] || [[ "$logical_cpus" -lt 1 ]]; then
        logical_cpus=1
    fi

    if [[ "$mem_bytes" =~ ^[0-9]+$ ]] && [[ "$mem_bytes" -gt 0 ]]; then
        jobs_by_mem=$(( mem_bytes / mem_per_job_bytes ))
        if [[ "$jobs_by_mem" -lt 1 ]]; then
            jobs_by_mem=1
        fi
    fi

    if [[ "$jobs_by_mem" -lt "$logical_cpus" ]]; then
        printf '%s\n' "$jobs_by_mem"
    else
        printf '%s\n' "$logical_cpus"
    fi
}

bytes_to_gib_string() {
    local bytes="${1:-0}"
    awk -v bytes="$bytes" 'BEGIN { printf "%.1f", bytes / 1073741824 }'
}

get_available_space_bytes() {
    local target_path="$1"
    local available_kib
    available_kib="$(df -Pk "$target_path" 2>/dev/null | awk 'NR==2 {print $4}')"

    if ! [[ "$available_kib" =~ ^[0-9]+$ ]]; then
        printf '0\n'
        return 0
    fi

    printf '%s\n' $((available_kib * 1024))
}

remove_path_for_space() {
    local path="$1"
    local description="$2"

    if [[ ! -e "$path" ]]; then
        return 0
    fi

    local size="unknown"
    size="$(du -sh "$path" 2>/dev/null | awk '{print $1}')"
    if [[ -z "$size" ]]; then
        size="unknown"
    fi

    log_warning "Removing $description at $path ($size) to free disk space"
    rm -rf "$path"
}

reclaim_disk_space_for_nuitka() {
    local before_bytes
    before_bytes="$(get_available_space_bytes "$BUILD_DIR")"

    log_warning "Available disk space before cleanup: $(bytes_to_gib_string "$before_bytes") GiB"
    log_warning "Disabling ccache for this build retry to avoid further cache growth"
    export CCACHE_DISABLE=1

    remove_path_for_space "$HOME/Library/Caches/Nuitka" "Nuitka cache"
    remove_path_for_space "$HOME/Library/Caches/pip" "pip cache"
    remove_path_for_space "$ARTIFACTS_DIR/chromium" "bundled Playwright Chromium artifacts"
    remove_path_for_space "$BUILD_DIR/AutoYou.app" "stale frontend app bundle"
    remove_path_for_space "$BUILD_DIR/executables" "stale extracted executables"

    rm -rf "$BACKEND_BUILD"/*.app "$BACKEND_BUILD"/*.dist "$BACKEND_BUILD"/AutoYou 2>/dev/null || true

    local after_bytes
    after_bytes="$(get_available_space_bytes "$BUILD_DIR")"
    log "Available disk space after cleanup: $(bytes_to_gib_string "$after_bytes") GiB"

    if [[ "$after_bytes" -le "$before_bytes" ]]; then
        log_warning "Automatic cleanup reclaimed little or no space. Additional manual cleanup may still be necessary."
    fi
}

ensure_nuitka_disk_space() {
    local available_bytes
    available_bytes="$(get_available_space_bytes "$BUILD_DIR")"
    log "Available disk space before Nuitka compile: $(bytes_to_gib_string "$available_bytes") GiB"

    if [[ "$available_bytes" -ge "$NUITKA_WARN_FREE_SPACE_BYTES" ]]; then
        return 0
    fi

    log_warning "Low disk space detected before Nuitka compile. Cleaning rebuildable caches and stale artifacts."
    reclaim_disk_space_for_nuitka
    available_bytes="$(get_available_space_bytes "$BUILD_DIR")"

    if [[ "$available_bytes" -lt "$NUITKA_MIN_FREE_SPACE_BYTES" ]]; then
        log_error "Only $(bytes_to_gib_string "$available_bytes") GiB free after cleanup."
        log_error "A full Nuitka standalone build of this app needs ~25-40 GiB of free headroom."
        log_error "A near-full disk is also a common cause of the Nuitka 'codesign ... internal error in Code Signing subsystem' FATAL, because codesign cannot write the signature seal."
        log_error "Free additional disk space (your own large files - the build only auto-reclaims rebuildable caches) before retrying the backend build."
        return 1
    fi

    return 0
}

copy_playwright_cache_to_artifacts() {
    local source_root="$1"
    local target_root="${ARTIFACTS_DIR}/chromium"
    local copied_any=false
    local artifact_path=""

    if [[ ! -d "$source_root" ]]; then
        return 1
    fi

    mkdir -p "$target_root"

    for artifact_path in \
        "$source_root"/chromium-* \
        "$source_root"/chromium_headless_shell-* \
        "$source_root"/ffmpeg-*; do
        [[ -e "$artifact_path" ]] || continue
        rsync -a "$artifact_path" "$target_root/"
        copied_any=true
    done

    [[ "$copied_any" == true ]]
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --type)
                BUILD_TYPE="$2"
                shift 2
                ;;
            --python)
                PYTHON_CMD="$2"
                PYTHON_CMD_EXPLICIT=true
                shift 2
                ;;
            --jobs)
                NUITKA_JOBS="$2"
                NUITKA_JOBS_EXPLICIT=true
                shift 2
                ;;
            --requirements)
                REQUIREMENTS_TYPE="$2"
                shift 2
                ;;
            --full)
                REQUIREMENTS_TYPE="full"
                shift
                ;;
            --include-cognee)
                INCLUDE_COGNEE=true
                shift
                ;;
            --accept-terms)
                ACCEPT_TERMS=true
                shift
                ;;
            --skip-local-build-ack)
                SKIP_LOCAL_BUILD_ACK=true
                shift
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            --clean)
                CLEAN_ONLY=true
                shift
                ;;
            --no-sign)
                SIGN_BINARIES=false
                shift
                ;;
            --skip-nuitka)
                # Reuse the existing Nuitka launcher output; only re-run the
                # post-compile asset pipeline. Fails if no prior build exists.
                SKIP_NUITKA=true
                shift
                ;;
            --help)
                head -n 35 "$0" | tail -n 33
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 1
                ;;
        esac
    done
}

run_strict_release_legal_gate() {
    if [[ "$BUILD_TYPE" != "release" ]]; then
        return 0
    fi
    if [[ "${AUTOYOU_SKIP_STRICT_RELEASE_LEGAL_GATE:-0}" == "1" ]]; then
        log_warning "Skipping strict release legal gate (AUTOYOU_SKIP_STRICT_RELEASE_LEGAL_GATE=1)."
        return 0
    fi

    log "Running strict release legal gate..."
    python3 "${PROJECT_ROOT}/scripts/check_release_legal_gates.py" --artifact-scope server --no-generate --strict-unknown-license || {
        log_error "Release legal gate failed. Resolve open blockers before macOS backend release packaging."
        exit 1
    }
}

release_artifact_profile() {
    case "$REQUIREMENTS_TYPE" in
        full|source-full|connector-full|training-full)
            printf '%s\n' "autoyou-server-macos-connector-full"
            ;;
        *)
            printf '%s\n' "autoyou-server-macos-default"
            ;;
    esac
}

run_official_build_authorization_gate() {
    if [[ "$BUILD_TYPE" != "release" ]]; then
        return 0
    fi

    log "Checking official build authorization..."
    python3 "${PROJECT_ROOT}/scripts/check_official_build_authorization.py" \
        --required \
        --artifact-profile "$(release_artifact_profile)" || {
        log_error "Official build authorization failed. Complete the SignToROSS/OpenSign build-access agreement before release packaging."
        exit 1
    }
}

acknowledge_private_build() {
    if [[ "$BUILD_TYPE" == "release" || "$SKIP_LOCAL_BUILD_ACK" == true ]]; then
        return 0
    fi
    local ack_args=()
    [[ "$ACCEPT_TERMS" == true ]] && ack_args+=(--accept-terms)
    python3 "${PROJECT_ROOT}/scripts/acknowledge_local_build.py" "${ack_args[@]}"
}

# Ensure required macOS system dependencies are installed via Homebrew.
# This is called automatically at build start - no manual brew install needed.
ensure_system_deps() {
    log "Checking system dependencies..."

    if ! command -v brew &>/dev/null; then
        log_error "Homebrew is not installed. Install it from https://brew.sh and retry."
        exit 1
    fi

    # Map: brew formula -> what it provides / why it's needed
    local -a brew_deps=()
    if requirements_has_voice; then
        brew_deps+=(
            "cmake"  # fallback whisper.cpp CLI build
            "sox"
            "portaudio"
        )
    fi

    if [[ ${#brew_deps[@]} -eq 0 ]]; then
        log_success "No Homebrew runtime dependencies required for requirements profile: $REQUIREMENTS_TYPE"
        return 0
    fi

    local missing=()
    for formula in "${brew_deps[@]}"; do
        if ! brew list "$formula" &>/dev/null; then
            missing+=("$formula")
        fi
    done

    if [[ ${#missing[@]} -eq 0 ]]; then
        log_success "All system dependencies present: ${brew_deps[*]}"
        return 0
    fi

    log "Installing missing Homebrew packages: ${missing[*]}"
    brew install "${missing[@]}" || {
        log_error "brew install failed for: ${missing[*]}"
        exit 1
    }
    log_success "System dependencies installed: ${missing[*]}"
}

# Validate Python environment
validate_python() {
    log "Validating Python environment..."

    local python_version=""
    if [[ "$PYTHON_CMD_EXPLICIT" == true ]]; then
        if [[ "$PYTHON_CMD" == */* ]]; then
            if [[ ! -x "$PYTHON_CMD" ]]; then
                log_error "Python not found or not executable: $PYTHON_CMD"
                exit 1
            fi
        elif ! command -v "$PYTHON_CMD" &> /dev/null; then
            log_error "Python not found: $PYTHON_CMD"
            exit 1
        fi
        python_version=$("$PYTHON_CMD" --version 2>&1 | awk '{print $2}')
        log "Using requested Python: $python_version (command: $PYTHON_CMD)"
    else
        # Try to find compatible Python version (3.11+)
        local python_candidates=("python3.13" "python3.12" "python3.11" "python3.10" "python3")
        local found_python=""

        for py_cmd in "${python_candidates[@]}"; do
            if command -v "$py_cmd" &> /dev/null; then
                local version=$("$py_cmd" --version 2>&1 | awk '{print $2}')
                local major=$(echo "$version" | cut -d. -f1)
                local minor=$(echo "$version" | cut -d. -f2)

                # Prefer Python 3.10+ for compatibility
                if [[ $major -gt 3 ]] || [[ ($major -eq 3 && $minor -ge 10) ]]; then
                    found_python="$py_cmd"
                    PYTHON_CMD="$py_cmd"
                    python_version="$version"
                    log "Found compatible Python: $py_cmd ($version)"
                    break
                fi
            fi
        done

        if [[ -z "$found_python" ]]; then
            # Fall back to whatever Python is available
            log_warning "No Python 3.10+ found, using system Python"
            if ! command -v "$PYTHON_CMD" &> /dev/null; then
                log_error "Python not found: $PYTHON_CMD"
                exit 1
            fi
            python_version=$("$PYTHON_CMD" --version 2>&1 | awk '{print $2}')
        fi

        log "Using Python: $python_version (command: $PYTHON_CMD)"
    fi
    
    # Check if this SDK needs path fixing (common for portable Python builds)
    fix_python_sdk_if_needed

    if [[ "$NUITKA_JOBS_EXPLICIT" != true ]]; then
        local mem_bytes
        mem_bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
        NUITKA_JOBS="$(auto_detect_nuitka_jobs "$DEFAULT_LOGICAL_CPUS" "$mem_bytes")"
        if [[ "$mem_bytes" =~ ^[0-9]+$ ]] && [[ "$mem_bytes" -gt 0 ]]; then
            local mem_gib=$(( (mem_bytes + 1073741823) / 1073741824 ))
            log "Auto-tuned Nuitka parallelism to $NUITKA_JOBS job(s) for ${mem_gib} GiB RAM across ${DEFAULT_LOGICAL_CPUS} logical CPUs"
        else
            log "Auto-tuned Nuitka parallelism to $NUITKA_JOBS job(s)"
        fi
    fi
    
    log_success "Python validation passed"
}

# Fix Python SDK paths if they contain hardcoded strings like '/install'
fix_python_sdk_if_needed() {
    local py_exe="$PYTHON_CMD"
    if ! [[ "$py_exe" == /* ]]; then
        py_exe=$(command -v "$py_exe")
    fi
    
    local sdk_root=$(cd "$(dirname "$py_exe")/.." && pwd)
    local lib_dir="${sdk_root}/lib"
    local major_minor=$(echo "$python_version" | cut -d. -f1-2)
    local dylib="${lib_dir}/libpython${major_minor}.dylib"
    
    if [[ -f "$dylib" ]]; then
        local current_id=$(otool -D "$dylib" | tail -n 1)
        if [[ "$current_id" == "/install/"* ]]; then
            log_warning "Detected bad SDK prefix in $dylib. Fixing..."
            
            # 1. Fix dylib ID
            chmod +w "$dylib" 2>/dev/null || true
            install_name_tool -id "$dylib" "$dylib"
            
            # 2. Fix sysconfigdata
            local sysconfig_file=$(find "${lib_dir}/python${major_minor}" -name "_sysconfigdata*darwin_darwin.py" | head -1)
            if [[ -n "$sysconfig_file" ]]; then
                log "Patching $sysconfig_file..."
                chmod +w "$sysconfig_file" 2>/dev/null || true
                sed -i '' "s|/install|${sdk_root}|g" "$sysconfig_file"
            fi
            
            log_success "Python SDK paths repaired"
        fi
    fi
}

# Setup Python virtual environment
setup_venv() {
    log "Setting up Python virtual environment..."
    
    local venv_dir="${BUILD_DIR}/venv"
    
    if [[ ! -d "$venv_dir" ]]; then
        "$PYTHON_CMD" -m venv "$venv_dir"
    fi
    
    # Activate venv
    source "${venv_dir}/bin/activate"
    
    # Upgrade pip
    pip install --upgrade 'pip>=26.1.2,<27' 'setuptools>=83,<84' wheel > /dev/null 2>&1 || true

    log_success "Virtual environment ready"
}

scrub_appledouble_sidecars() {
    local target_dir="$1"

    [[ -d "$target_dir" ]] || return 0
    find "$target_dir" -name '._*' -delete
}

# Verify backend runtime pin versions (Windows parity)
resolve_requirements_file() {
    local req_file=""
    case "$REQUIREMENTS_TYPE" in
        base)
            req_file="${PROJECT_ROOT}/requirements/base.txt"
            ;;
        server-macos)
            req_file="${PROJECT_ROOT}/requirements/server-macos.txt"
            if [[ ! -f "$req_file" ]]; then
                log_warning "server-macos.txt not found, falling back to base.txt" >&2
                req_file="${PROJECT_ROOT}/requirements/base.txt"
            fi
            ;;
        full|source-full|connector-full)
            req_file="${PROJECT_ROOT}/requirements.txt"
            ;;
        *)
            req_file="${PROJECT_ROOT}/requirements/$REQUIREMENTS_TYPE.txt"
            ;;
    esac

    if [[ ! -f "$req_file" ]]; then
        log_error "Requirements file not found: $req_file" >&2
        return 1
    fi

    printf '%s\n' "$req_file"
}

verify_backend_runtime_pins() {
    log "Verifying backend runtime package versions..."

    if [[ ! -f "$BACKEND_RUNTIME_PIN_READER" ]]; then
        log_error "Backend runtime pin reader not found at $BACKEND_RUNTIME_PIN_READER"
        return 1
    fi

    local req_file=""
    if ! req_file="$(resolve_requirements_file)"; then
        return 1
    fi

    local expected_packages=()
    while IFS= read -r pkg_spec; do
        [[ -n "$pkg_spec" ]] && expected_packages+=("$pkg_spec")
    done < <(
        "$PYTHON_CMD" "$BACKEND_RUNTIME_PIN_READER" \
            --requirements-file "$req_file" \
            --packages google-adk google-genai google-cloud-aiplatform fastapi sqlalchemy \
            --format lines
    )

    if [[ ${#expected_packages[@]} -eq 0 ]]; then
        log_error "No backend runtime pins resolved from $req_file"
        return 1
    fi
    
    local mismatches=()
    
    for pkg_spec in "${expected_packages[@]}"; do
        local pkg_name="${pkg_spec%==*}"
        local required_version="${pkg_spec##*==}"
        
        local installed_version
        installed_version=$("$PYTHON_CMD" -c "import importlib.metadata; print(importlib.metadata.version('$pkg_name'))" 2>/dev/null || echo "not-installed")
        
        if [[ "$installed_version" != "$required_version" ]]; then
            mismatches+=("$pkg_name: expected $required_version, got $installed_version")
        fi
    done
    
    if [[ ${#mismatches[@]} -gt 0 ]]; then
        log_error "Backend runtime version mismatches:"
        for mismatch in "${mismatches[@]}"; do
            log_error "  - $mismatch"
        done
        return 1
    fi
    
    log_success "Backend runtime pins verified from $(basename "$req_file"): ${expected_packages[*]}"
}

prepare_backend_python_dependencies() {
    step_header "Install Python dependencies"
    install_dependencies

    step_header "Prune non-commercial model assets"
    prune_noncommercial_site_packages

    if requirements_has_voice; then
        step_header "Patch native library paths (torchaudio/sox)"
        patch_torchaudio_sox

        step_header "Patch native library paths (pyaudio/portaudio)"
        patch_pyaudio_portaudio

        step_header "Patch native library paths (torch/libomp)"
        patch_torch_libomp

        step_header "Patch native library paths (onnxruntime)"
        patch_onnxruntime_dylib
    else
        log "Skipping voice-native dylib patches for requirements profile: $REQUIREMENTS_TYPE"
        if [[ "${AUTOYOU_BUILD_DESKTOP_V2:-0}" == "1" ]]; then
            patch_pyaudio_portaudio
        fi
    fi
}

get_runtime_integrity_allowed_python_files() {
    local bundle_root="$1"
    local manifest_path="${bundle_root}/runtime_integrity.json"

    if [[ ! -f "$manifest_path" ]]; then
        log_error "Runtime integrity manifest not found at $manifest_path"
        return 1
    fi

    "$PYTHON_CMD" - "$manifest_path" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
for relative_path in manifest.get("allowed_python_files") or []:
    cleaned = str(relative_path).replace("\\", "/").strip("/")
    if cleaned:
        print(cleaned)
PY
}

verify_backend_hardening() {
    local bundle_root="$1"
    local verifier_script="$PROJECT_ROOT/scripts/verify_backend_hardening.py"
    local relative_path=""
    local verify_command=(
        "$PYTHON_CMD"
        "$verifier_script"
        --bundle-root "$bundle_root"
        --repo-root "$PROJECT_ROOT"
    )

    if [[ ! -f "$verifier_script" ]]; then
        log_error "Backend hardening verifier not found at $verifier_script"
        return 1
    fi

    while IFS= read -r relative_path; do
        if [[ -n "$relative_path" ]]; then
            verify_command+=(--allow-source-file "$relative_path")
        fi
    done < <(get_runtime_integrity_allowed_python_files "$bundle_root")

    verify_command+=(
        --allow-source-dir "runtime_stdlib" \
        --allow-source-dir "runtime_site_packages" \
        --skip-dir "node" \
        --skip-dir "runtime/node" \
        --skip-dir "runtime/playwright" \
        --skip-dir "runtime/whisper" \
        --skip-dir "runtime/tunnelmole" \
        --skip-dir "runtime_modules"
    )

    "${verify_command[@]}"
}

resolve_packaged_libsodium_dir() {
    local resources_root="$1"
    local arch_subdir=""
    case "$(uname -m)" in
        arm64|aarch64)
            arch_subdir="darwin-arm64"
            ;;
        x86_64|amd64)
            arch_subdir="darwin-x86_64"
            ;;
        *)
            return 1
            ;;
    esac

    local candidate="${resources_root}/runtime_modules/shared/native/libsodium/${arch_subdir}"
    if [[ -f "${candidate}/libsodium.dylib" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi

    return 1
}

verify_packaged_backend_imports() {
    local backend_exe="$1"
    local resources_root=""

    if [[ ! -x "$backend_exe" ]]; then
        log_error "Packaged backend executable not found or not executable at $backend_exe"
        return 1
    fi

    resources_root="$(cd "$(dirname "$backend_exe")" && pwd)"

    local -a runtime_verify_args=(
        --verify-runtime-import "keyring"
        --verify-runtime-import "keyring.backends.macOS"
        --verify-runtime-import "pyotp"
        --verify-runtime-import "psutil"
        --verify-runtime-import "httpx"
        --verify-runtime-import "h2"
        --verify-runtime-import "hpack"
        --verify-runtime-import "hyperframe"
        --verify-runtime-import "websockets"
        --verify-runtime-import "telegram"
        --verify-runtime-import "telegram.ext"
        --verify-runtime-import "telegram.constants"
        --verify-runtime-import "telegram.error"
        --verify-runtime-import "telethon"
        --verify-runtime-import "docker"
        --verify-runtime-import "pysignalclirestapi"
        --verify-runtime-import "playwright.async_api"
        --verify-runtime-import "yt_dlp"
        --verify-runtime-import "pysodium"
        --verify-runtime-import "shared.native_libsodium"
        --verify-runtime-import "shared.pairing_cpace"
        --verify-runtime-import "shared.keystore"
        --verify-runtime-import "shared.ollama_gateway"
        --verify-runtime-import "shared.odysseus_gateway"
        --verify-runtime-import "shared.process_lifecycle"
        --verify-runtime-import "shared.remote_desktop_input"
        --verify-runtime-import "shared.remote_desktop_settings"
    )
    local -a server_verify_args=(
        --verify-server-imports
    )

    if requirements_has_voice; then
        log "Verifying packaged voice/STT imports..."
        runtime_verify_args+=(
            --verify-runtime-import "shared.emotivoice_tts"
            --verify-runtime-import "torch"
            --verify-runtime-import "torchaudio"
            --verify-runtime-import "transformers"
            --verify-runtime-import "yacs"
            --verify-runtime-import "g2p_en"
            --verify-runtime-import "jieba"
            --verify-runtime-import "pypinyin"
            --verify-runtime-import "pypinyin_dict"
            --verify-runtime-import "cn2an"
            --verify-runtime-import "numba"
            --verify-runtime-import "soundfile"
            --verify-runtime-import "nltk"
            --verify-runtime-import "scipy"
            --verify-runtime-import "RealtimeSTT"
            --verify-runtime-import "faster_whisper"
            --verify-runtime-import "ctranslate2"
        )
    fi

    if [[ "$REQUIREMENTS_TYPE" != "base" ]]; then
        if [[ ! -d "${resources_root}/runtime_site_packages/mss" || ! -d "${resources_root}/runtime_site_packages/pyautogui" ]]; then
            log_error "Packaged native remote desktop requires mss and pyautogui in runtime_site_packages."
            return 1
        fi
        runtime_verify_args+=(
            --verify-runtime-import "mss"
            --verify-runtime-import "pyautogui"
        )
    fi

    if [[ "$GOOGLE_NUITKA_INCLUDE_MODE" == "runtime" ]]; then
        log "Skipping packaged AI-agent frontend import verification for runtime-only launcher include mode."
    else
        runtime_verify_args+=(
            --verify-runtime-import "autoyou_agents.agent_builder_agent.website.backend.app"
            --verify-runtime-import "autoyou_agents.education_agent.website.backend.app"
            --verify-runtime-import "autoyou_agents.notify_agent.website.backend.app"
            --verify-runtime-import "autoyou_agents.skills_agent.website.backend.app"
            --verify-runtime-import "autoyou_agents.tasks_agent.website.backend.app"
            --verify-runtime-import "autoyou_agents.voice_training_agent.website.backend.app"
            --verify-runtime-import "autoyou_agents.website_agent.website.backend.app"
            --verify-runtime-import "autoyou_agents.page_agent.website.backend.app"
            --verify-runtime-import "autoyou_agents.ads_watching_agent.website.backend.app"
            --verify-runtime-import "autoyou_agents.donation_agent.website.backend.app"
        )

        if [[ -d "${resources_root}/runtime_site_packages/pyautogui" && -d "${resources_root}/runtime_site_packages/mss" ]]; then
            runtime_verify_args+=(--verify-runtime-import "autoyou_agents.remote_desktop_agent.website.backend.app")
        else
            log "Skipping packaged remote-desktop import verification; desktop automation overlay not bundled."
        fi
    fi

    runtime_verify_args+=(
        --verify-runtime-import "autoyou_agents.data_collector_agent.website.backend.app"
        --verify-runtime-import "autoyou_agents.fine_tuning_agent.website.backend.app"
        --verify-runtime-import "autoyou_agents.data_collector_agent.agent"
        --verify-runtime-import "autoyou_agents.fine_tuning_agent.agent"
    )
    if requirements_has_tuning; then
        runtime_verify_args+=(
            --verify-runtime-import "torch"
            --verify-runtime-import "torchvision"
            --verify-runtime-import "transformers"
            --verify-runtime-import "datasets"
            --verify-runtime-import "peft"
            --verify-runtime-import "accelerate"
        )
    fi

    local verify_timeout_seconds="${AUTOYOU_MACOS_BACKEND_VERIFY_TIMEOUT_SECONDS:-240}"
    run_packaged_backend_verifier() {
        local mode_name="$1"
        shift
        local verifier_pid=""
        local elapsed=0
        local libsodium_dir=""
        local dyld_library_path=""

        libsodium_dir="$(resolve_packaged_libsodium_dir "$resources_root" || true)"
        if [[ -n "$libsodium_dir" ]]; then
            dyld_library_path="$libsodium_dir"
            if [[ -n "${DYLD_LIBRARY_PATH:-}" ]]; then
                dyld_library_path="${dyld_library_path}:${DYLD_LIBRARY_PATH}"
            fi
        fi

        if [[ -n "$dyld_library_path" ]]; then
            env PYTHONDONTWRITEBYTECODE=1 DYLD_LIBRARY_PATH="$dyld_library_path" "$backend_exe" "$@" &
        else
            PYTHONDONTWRITEBYTECODE=1 "$backend_exe" "$@" &
        fi
        verifier_pid=$!

        while kill -0 "$verifier_pid" 2>/dev/null; do
            if [[ "$elapsed" -ge "$verify_timeout_seconds" ]]; then
                log_warning "Packaged backend ${mode_name} verification timed out after ${verify_timeout_seconds}s"
                kill -TERM "$verifier_pid" 2>/dev/null || true
                sleep 2
                kill -KILL "$verifier_pid" 2>/dev/null || true
                wait "$verifier_pid" 2>/dev/null || true
                return 124
            fi
            sleep 1
            elapsed=$((elapsed + 1))
        done

        wait "$verifier_pid"
    }

    local verify_status=0
    log "Verifying packaged backend runtime imports..."
    set +e
    run_packaged_backend_verifier "runtime import" "${runtime_verify_args[@]}"
    verify_status=$?
    set -e
    if [[ "$verify_status" -ne 0 ]]; then
        log_error "Packaged backend runtime import verification failed (exit $verify_status)"
        return "$verify_status"
    fi
    log_success "Packaged backend runtime imports verified"

    log "Verifying packaged backend server imports..."
    set +e
    run_packaged_backend_verifier "server import" "${server_verify_args[@]}"
    verify_status=$?
    set -e
    if [[ "$verify_status" -ne 0 ]]; then
        if [[ "$verify_status" -eq 124 && "$GOOGLE_NUITKA_INCLUDE_MODE" == "runtime" && "${AUTOYOU_MACOS_BACKEND_VERIFY_TIMEOUT_IS_FATAL:-0}" != "1" ]]; then
            log_warning "Runtime-only packaged server import verification timed out; continuing because live app smoke tests validate this build."
        else
            log_error "Packaged backend server import verification failed (exit $verify_status)"
            return "$verify_status"
        fi
    else
        log_success "Packaged backend server imports verified"
    fi
}

# Vendor Google Fonts for ADK browser (Windows parity)
vendor_adk_browser_fonts() {
    local resources_root="$1"
    local browser_root="${resources_root}/google/adk/cli/browser"
    local index_html="${browser_root}/index.html"
    local python_cmd="${PYTHON_CMD:-python3}"
    
    if [[ ! -f "$index_html" ]]; then
        log_warning "ADK index.html not found at $index_html, skipping font vendoring"
        return 0
    fi
    
    log "Vendoring Google Fonts for ADK browser..."
    
    local font_output="${browser_root}/vendor-fonts"
    rm -rf "$font_output"
    mkdir -p "$font_output"
    
    # Extract exact font URLs from inline CSS url(...) references.
    local font_urls
    font_urls=$(
        "$python_cmd" - "$index_html" <<'PY'
import re
import sys
from pathlib import Path

index_html = Path(sys.argv[1])
text = index_html.read_text(encoding="utf-8", errors="ignore")
pattern = re.compile(r"https://fonts\.gstatic\.com/[^)\"'\s]+")
for url in sorted(set(pattern.findall(text))):
    print(url)
PY
    )

    if [[ -z "$font_urls" ]]; then
        log_warning "No external font URLs found in ADK index.html"
        return 0
    fi
    
    # Download each font with retry logic
    echo "$font_urls" | sort -u | while read -r fonturl; do
        [[ -n "$fonturl" ]] || continue
        local relative_font_path="${fonturl#https://fonts.gstatic.com/}"
        local fontname=$(basename "$relative_font_path")
        local fontpath="${font_output}/${relative_font_path}"
        mkdir -p "$(dirname "$fontpath")"
        
        if [[ -f "$fontpath" ]]; then
            log "  Font cached: $fontname"
        else
            log "  Downloading font: $fontname"
            if curl -fsSL --max-time 30 "$fonturl" -o "$fontpath" 2>/dev/null; then
                log "  Downloaded: $fontname"
            else
                log_warning "  Failed to download: $fonturl (will use CDN fallback)"
            fi
        fi
    done
    
    # Update HTML to use vendored fonts (safe sed for macOS)
    log "Updating ADK index.html to reference vendored fonts..."
    sed -i '' 's|https://fonts\.gstatic\.com/|vendor-fonts/|g' "$index_html" 2>/dev/null || true
    
    # Remove Google Fonts preconnect links (no longer needed)
    sed -i '' '/fonts\.googleapis\.com/d' "$index_html" 2>/dev/null || true
    sed -i '' '/fonts\.gstatic\.com.*preconnect/d' "$index_html" 2>/dev/null || true
    
    log_success "Google Fonts vendored for offline use"
}

# Verify ADK browser bundle has compiled assets (Windows parity)
verify_adk_browser_bundle() {
    local resources_root="$1"
    local browser_root="${resources_root}/google/adk/cli/browser"
    
    log "Verifying ADK browser bundle..."
    
    # Check for required core files
    local required_files=(
        "index.html"
        "assets/audio-processor.js"
        "assets/config/runtime-config.json"
    )
    
    for file in "${required_files[@]}"; do
        local filepath="${browser_root}/${file}"
        if [[ ! -f "$filepath" ]]; then
            log_error "Missing ADK browser asset: $filepath"
            return 1
        fi
    done
    
    # Check for compiled JS/CSS bundles (Webpack output naming pattern)
    if ! ls "${browser_root}"/main-*.js 2>/dev/null | head -1 >/dev/null; then
        log_warning "ADK main-*.js bundle not found (may use different bundler)"
    fi
    
    if ! ls "${browser_root}"/styles-*.css 2>/dev/null | head -1 >/dev/null; then
        log_warning "ADK styles-*.css bundle not found (may use different bundler)"
    fi
    
    log_success "ADK browser bundle verified"
}

# Verify node_modules for bundled services (Windows parity)
verify_node_service_modules() {
    local node_root="$1"
    
    log "Verifying Node.js service modules..."
    
    for svc in whatsapp tunnelmole; do
        local svc_dir="${node_root}/${svc}"
        local nm_dir="${svc_dir}/node_modules"
        
        if [[ ! -d "$nm_dir" ]]; then
            log_warning "$svc node_modules not found at $nm_dir"
            continue
        fi
        
        # Check for native addons (.node files) which must be compiled.
        # tunnelmole itself is pure JS, so missing native addons there is fine.
        if find "$nm_dir" -name "*.node" -type f 2>/dev/null | head -1 >/dev/null; then
            log "  $svc: native addons detected ✓"
        elif [[ "$svc" == "tunnelmole" ]]; then
            log "  $svc: pure JavaScript package bundle detected ✓"
        else
            log_warning "  $svc: no native addons found (may still work)"
        fi
    done
    
    log_success "Node service modules verified"
}

# Verify voice processing libraries (macOS dylib compatibility)
verify_voice_processing_libs() {
    log "Verifying voice/STT processing libraries..."
    
    local resources_root="$1"

    # CTranslate2 wheels already contain the native macOS library. Confirm it
    # and the runtime STT packages are in the bundle before shipping a full
    # voice build. Binary-default builds keep using whisper.cpp and skip here.
    
    local voice_packages=(
        "torch"
        "torchgen"
        "torchaudio"
        "ctranslate2"
        "faster_whisper"
        "RealtimeSTT"
    )
    
    local missing_fast_stt=()
    for pkg in "${voice_packages[@]}"; do
        if find "$resources_root" \( -name "${pkg}*" -o -name "${pkg}*.so" \) -print -quit 2>/dev/null | grep -q .; then
            log "  ✓ $pkg detected"
        else
            case "$pkg" in
                ctranslate2|faster_whisper|RealtimeSTT)
                    log_error "Required voice/STT package is missing: $pkg"
                    missing_fast_stt+=("$pkg")
                    ;;
                *)
                    log_warning "  $pkg not found (optional voice enhancement unavailable)"
                    ;;
            esac
        fi
    done

    local ctranslate2_dylib
    ctranslate2_dylib=$(find "$resources_root" -type f -name 'libctranslate2*.dylib' -print -quit 2>/dev/null || true)
    if [[ -n "$ctranslate2_dylib" ]]; then
        log "  ✓ CTranslate2 native library detected"
    else
        log_error "Required CTranslate2 native library is missing from the full voice bundle"
        missing_fast_stt+=("libctranslate2*.dylib")
    fi

    if [[ ${#missing_fast_stt[@]} -gt 0 ]]; then
        log_error "Voice/STT packaging is incomplete: ${missing_fast_stt[*]}"
        return 1
    fi

    local whisper_runtime_root="${resources_root}/runtime/whisper"
    local whisper_binary=""
    for candidate in \
        "$whisper_runtime_root/whisper-cli" \
        "$whisper_runtime_root/whisper" \
        "$whisper_runtime_root/main"; do
        if [[ -x "$candidate" ]]; then
            whisper_binary="$candidate"
            break
        fi
    done

    if [[ -n "$whisper_binary" ]]; then
        log "  ✓ whisper.cpp runtime detected ($whisper_binary)"
    else
        log_warning "  whisper.cpp runtime not found (compiled STT fallback will fail)"
    fi
    
    log_success "Voice library verification complete"
}

# Install Python dependencies
install_dependencies() {
    log "Installing Python dependencies..."
    log "Requirements type: $REQUIREMENTS_TYPE"
    if include_cognee_enabled; then
        log "Optional Cognee memory backend: enabled"
    fi
    
    # First make sure we have common build tools
    "$PYTHON_CMD" -m pip install --upgrade 'pip>=26.1.2,<27' 'setuptools>=83,<84' wheel 2>&1 | grep -E "(Collecting|Successfully|already)" | head -5
    
    # Install Playwright separately before attempting browser installation
    log "Installing Playwright..."
    "$PYTHON_CMD" -m pip install playwright 2>&1 | grep -E "(Collecting|Successfully|already)" || true
    
    # Determine requirements file to use
    local req_file=""
    if ! req_file="$(resolve_requirements_file)"; then
        exit 1
    fi
    
    log "Installing packages from $(basename "$req_file")..."
    log "This may take several minutes - packages are being downloaded and compiled..."

    # Constrain packages present in the shared lock. This does not enforce its
    # hashes or cover every optional/build dependency; audit the resolved artifact. Set
    # AUTOYOU_IGNORE_LOCKFILE=1 to opt out.
    local pip_install_args=(-r "$req_file" --progress-bar on --no-color)
    local locked_constraints=""
    local ignore_lock
    ignore_lock="$(printf '%s' "${AUTOYOU_IGNORE_LOCKFILE:-}" | tr '[:upper:]' '[:lower:]')"
    case "$ignore_lock" in
        1|true|yes|on) ;;
        *)
            locked_constraints="$("$PYTHON_CMD" "${PROJECT_ROOT}/scripts/gen_locked_constraints.py" 2>/dev/null || true)"
            if [ -n "$locked_constraints" ] && [ -f "$locked_constraints" ]; then
                log "Pinning backend install to audited lockfile versions via $locked_constraints"
                pip_install_args+=(-c "$locked_constraints")
            fi
            ;;
    esac

    if requirements_has_voice; then
        if [[ ! -f "$REALTIMESTT_RUNTIME_INSTALLER" || ! -f "$REALTIMESTT_RUNTIME_REQUIREMENTS_FILE" ]]; then
            log_error "RealtimeSTT runtime installer or requirements pin is missing"
            exit 1
        fi
        log "Installing AutoYou RealtimeSTT runtime without wake-word extras..."
        if ! "$PYTHON_CMD" "$REALTIMESTT_RUNTIME_INSTALLER" 2>&1 | tee -a "${BUILD_DIR}/pip_install.log"; then
            log_error "Failed to install AutoYou RealtimeSTT runtime"
            exit 1
        fi
    fi

    # Use pip with progress for better real-time feedback
    if ! "$PYTHON_CMD" -m pip install "${pip_install_args[@]}" 2>&1 | tee -a "${BUILD_DIR}/pip_install.log" | grep -E "(Collecting|Downloading|Installing|Successfully|already|Processing)" | sed 's/^/  /'; then
        log_error "Failed to install requirements"
        log "Last 50 lines of pip output:"
        tail -50 "${BUILD_DIR}/pip_install.log"
        exit 1
    fi

    if include_cognee_enabled; then
        local cognee_req="${PROJECT_ROOT}/requirements/cognee.txt"
        if [[ ! -f "$cognee_req" ]]; then
            log_error "AUTOYOU_INCLUDE_COGNEE requested, but requirements/cognee.txt is missing"
            exit 1
        fi

        local cognee_pip_install_args=(-r "$cognee_req" --progress-bar on --no-color)
        if [[ ${#pip_install_args[@]} -gt 0 ]]; then
            local idx=0
            while [[ $idx -lt ${#pip_install_args[@]} ]]; do
                if [[ "${pip_install_args[$idx]}" == "-c" && $((idx + 1)) -lt ${#pip_install_args[@]} ]]; then
                    cognee_pip_install_args+=("-c" "${pip_install_args[$((idx + 1))]}")
                    idx=$((idx + 2))
                    continue
                fi
                idx=$((idx + 1))
            done
        fi

        log "Installing optional Cognee memory backend from requirements/cognee.txt..."
        if ! "$PYTHON_CMD" -m pip install "${cognee_pip_install_args[@]}" 2>&1 | tee -a "${BUILD_DIR}/pip_install.log" | grep -E "(Collecting|Downloading|Installing|Successfully|already|Processing)" | sed 's/^/  /'; then
            log_error "Failed to install optional Cognee requirements"
            log "Last 50 lines of pip output:"
            tail -50 "${BUILD_DIR}/pip_install.log"
            exit 1
        fi
    fi
    
    # Install build tools used by the macOS packaging pipeline.
    log "Installing Nuitka and build tools..."
    "$PYTHON_CMD" -m pip install -q nuitka ordered-set imageio zstandard 2>&1 | tee -a "${BUILD_DIR}/pip_install.log" || true
    log "Nuitka and build tools installed"

    log "Reconciling dependency drift in reused build environment..."
    local reconcile_args=("${PROJECT_ROOT}/scripts/reconcile_python_runtime_env.py")
    if [ -n "$locked_constraints" ] && [ -f "$locked_constraints" ]; then
        reconcile_args+=(--constraints "$locked_constraints")
    fi
    if requirements_has_tuning; then
        reconcile_args+=(--include-tuning)
    fi
    if ! "$PYTHON_CMD" "${reconcile_args[@]}" 2>&1 | tee -a "${BUILD_DIR}/pip_install.log"; then
        log_error "Failed to reconcile Python runtime dependency drift"
        exit 1
    fi

    log "Checking installed Python dependency consistency..."
    if ! "$PYTHON_CMD" -m pip check 2>&1 | tee -a "${BUILD_DIR}/pip_install.log"; then
        log_error "Installed Python dependencies are inconsistent"
        exit 1
    fi
    
    log_success "Dependencies installed"
}

# Copy libsox.dylib from Homebrew into torchaudio's lib directory.
#
# Recursively vendor a Homebrew dylib closure into a target directory and
# rewrite all non-system load commands to @loader_path/<basename>.
#
# This is needed when a Python extension depends on a copied Homebrew dylib
# whose own load commands still point back to /opt/homebrew/... or /usr/local/.
# Nuitka's dependency scanner treats those absolute references as external
# dependencies and aborts during the final link/dependency scan stage.
bundle_macos_dylib_closure() {
    local root_dylib="$1"
    local target_dir="$2"
    local root_output_name="${3:-$(basename "$1")}"

    mkdir -p "$target_dir"

    local -a queue=("$root_dylib")
    local seen_paths=$'\n'
    local current=""

    while [[ ${#queue[@]} -gt 0 ]]; do
        current="${queue[0]}"
        queue=("${queue[@]:1}")

        case "$seen_paths" in
            *$'\n'"$current"$'\n'*)
                continue
                ;;
        esac
        seen_paths+="$current"$'\n'

        if [[ ! -f "$current" ]]; then
            log_warning "Skipping missing dylib dependency: $current"
            continue
        fi

        local base_name
        base_name="$(basename "$current")"
        if [[ "$current" == "$root_dylib" ]]; then
            base_name="$root_output_name"
        fi

        local bundled_path="$target_dir/$base_name"
        if [[ "$current" != "$bundled_path" ]]; then
            if [[ ! -f "$bundled_path" ]]; then
                cp -L "$current" "$bundled_path"
                log "  Copied $(basename "$current") → $bundled_path"
            fi
        fi

        chmod u+w "$bundled_path" 2>/dev/null || true

        local desired_id="@loader_path/$base_name"
        local current_id
        current_id=$(otool -D "$bundled_path" 2>/dev/null | tail -n 1 | xargs) || true
        if [[ -n "$current_id" && "$current_id" != "$desired_id" ]]; then
            log "  Rewriting install ID: $current_id → $desired_id"
            install_name_tool -id "$desired_id" "$bundled_path"
        fi

        local dep_path=""
        local resolved_dep_path=""
        while IFS= read -r dep_path; do
            [[ -z "$dep_path" ]] && continue
            resolved_dep_path="$dep_path"
            if [[ "$resolved_dep_path" != /opt/homebrew/* && "$resolved_dep_path" != /usr/local/* ]]; then
                resolved_dep_path="$(resolve_macos_dependency_path "$dep_path" "$bundled_path" || true)"
            fi
            if [[ -z "$resolved_dep_path" ]]; then
                continue
            fi
            if [[ "$resolved_dep_path" != /opt/homebrew/* && "$resolved_dep_path" != /usr/local/* && "$resolved_dep_path" != /Library/Frameworks/Python.framework/Versions/*/lib/*.dylib ]]; then
                continue
            fi

            local dep_base
            dep_base="$(basename "$resolved_dep_path")"
            local dep_bundled="$target_dir/$dep_base"
            if [[ ! -f "$dep_bundled" ]]; then
                cp -L "$resolved_dep_path" "$dep_bundled"
                log "  Copied $dep_base → $dep_bundled"
            fi
            chmod u+w "$dep_bundled" 2>/dev/null || true

            log "  Rewriting load command in $base_name: $dep_path → @loader_path/$dep_base"
            install_name_tool -change "$dep_path" "@loader_path/$dep_base" "$bundled_path"

            case "$seen_paths" in
                *$'\n'"$dep_bundled"$'\n'*)
                    ;;
                *)
                    queue+=("$dep_bundled")
                    ;;
            esac
        done < <(otool -L "$bundled_path" 2>/dev/null | awk 'NR>1 {print $1}')
    done
}

iter_macos_rpaths() {
    local binary_path="$1"
    otool -l "$binary_path" 2>/dev/null | awk '
        $1 == "cmd" && $2 == "LC_RPATH" { in_rpath = 1; next }
        in_rpath && $1 == "path" { print $2; in_rpath = 0 }
    '
}

resolve_macos_loader_token() {
    local token="$1"
    local owner_path="$2"
    local owner_dir=""
    owner_dir="$(cd "$(dirname "$owner_path")" && pwd)"

    case "$token" in
        @loader_path)
            printf '%s\n' "$owner_dir"
            return 0
            ;;
        @loader_path/*)
            printf '%s/%s\n' "$owner_dir" "${token#@loader_path/}"
            return 0
            ;;
        @executable_path)
            printf '%s\n' "$owner_dir"
            return 0
            ;;
        @executable_path/*)
            printf '%s/%s\n' "$owner_dir" "${token#@executable_path/}"
            return 0
            ;;
        /*)
            printf '%s\n' "$token"
            return 0
            ;;
    esac

    return 1
}

resolve_macos_dependency_path() {
    local dep_spec="$1"
    local owner_path="$2"
    local candidate=""
    local dep_tail=""
    local rpath_entry=""
    local resolved_rpath=""

    if [[ -f "$dep_spec" ]]; then
        printf '%s\n' "$dep_spec"
        return 0
    fi

    case "$dep_spec" in
        @loader_path|@loader_path/*|@executable_path|@executable_path/*)
            candidate="$(resolve_macos_loader_token "$dep_spec" "$owner_path" || true)"
            if [[ -n "$candidate" && -f "$candidate" ]]; then
                printf '%s\n' "$candidate"
                return 0
            fi
            ;;
        @rpath/*)
            dep_tail="${dep_spec#@rpath/}"
            while IFS= read -r rpath_entry; do
                [[ -z "$rpath_entry" ]] && continue
                resolved_rpath="$(resolve_macos_loader_token "$rpath_entry" "$owner_path" || true)"
                [[ -z "$resolved_rpath" ]] && continue
                candidate="${resolved_rpath%/}/$dep_tail"
                if [[ -f "$candidate" ]]; then
                    printf '%s\n' "$candidate"
                    return 0
                fi
            done < <(iter_macos_rpaths "$owner_path")

            for resolved_rpath in /opt/homebrew/lib /usr/local/lib; do
                candidate="${resolved_rpath%/}/$dep_tail"
                if [[ -f "$candidate" ]]; then
                    printf '%s\n' "$candidate"
                    return 0
                fi
            done
            ;;
    esac

    return 1
}

adhoc_sign_macos_path() {
    local target_path="$1"
    if [[ ! -f "$target_path" ]]; then
        return 0
    fi
    if ! file -b "$target_path" 2>/dev/null | grep -qE 'Mach-O|bundle'; then
        return 0
    fi
    if ! codesign -s - --force --preserve-metadata=entitlements "$target_path" >/dev/null 2>&1; then
        log_warning "Could not ad-hoc sign bundled runtime file: $target_path"
        return 1
    fi
}

adhoc_sign_macos_tree() {
    local root_path="$1"
    local sorted_paths
    sorted_paths="$(mktemp "${TMPDIR:-/tmp}/autoyou-adhoc-sign.XXXXXX")"
    find "$root_path" -type f -print0 | sort -rz > "$sorted_paths"
    while IFS= read -r -d '' binary; do
        adhoc_sign_macos_path "$binary" || true
    done < "$sorted_paths"
    rm -f "$sorted_paths"
}

adhoc_sign_packaged_runtime_dependencies() {
    local resources_root="$1"
    local runtime_modules_root="${resources_root}/runtime_modules"

    if [[ ! -d "$resources_root" ]]; then
        return 0
    fi

    log "Ad-hoc signing packaged Mach-O runtime dependencies before import verification..."
    local sorted_paths
    sorted_paths="$(mktemp "${TMPDIR:-/tmp}/autoyou-runtime-sign.XXXXXX")"
    find "$resources_root" \
        \( -path "$runtime_modules_root" -o -path "$runtime_modules_root/*" \) -prune \
        -o -type f -print0 | sort -rz > "$sorted_paths"
    while IFS= read -r -d '' binary; do
        adhoc_sign_macos_path "$binary" || true
    done < "$sorted_paths"
    rm -f "$sorted_paths"
}

verify_macos_binary_self_check() {
    local binary_path="$1"
    local timeout_seconds="${2:-20}"
    local verify_output=""

    if ! verify_output=$("$PYTHON_CMD" - "$binary_path" "$timeout_seconds" <<'PY'
import subprocess
import sys

binary = sys.argv[1]
timeout = float(sys.argv[2])

for flag in ("-h", "--help"):
    try:
        result = subprocess.run(
            [binary, flag],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="ignore")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="ignore")
        detail = "\n".join(part for part in (stdout, stderr) if part).strip()
        print(f"Timed out while probing {binary} {flag}")
        if detail:
            print(detail[-4000:])
        continue
    except Exception as exc:
        print(f"Failed to execute {binary} {flag}: {exc}")
        continue

    detail = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode == 0:
        sys.exit(0)

    print(f"{binary} {flag} exited with code {result.returncode}")
    if detail:
        print(detail[-4000:])

sys.exit(1)
PY
); then
        [[ -n "$verify_output" ]] && printf '%s\n' "$verify_output"
        return 1
    fi

    return 0
}

# torchaudio's _torchaudio_sox.so contains a load command:
#   @rpath/libsox.dylib
# Nuitka resolves @rpath relative to the .so's directory (torchaudio/lib)
# and expects to find the file there.  sox ships as a separate Homebrew
# formula and is never bundled inside the torchaudio wheel, so without this
# step Nuitka aborts with:
#   FATAL: Error, failed to find path '@rpath/libsox.dylib'
#
# In addition, the copied libsox.dylib itself depends on a closure of Homebrew
# codec libraries (libpng, flac, vorbis, sndfile, opus, mpg123, etc.).  Those
# must also be vendored and rewritten to @loader_path, otherwise Nuitka aborts
# later with dependency-scan failures on the next absolute path in the chain.
patch_torchaudio_sox() {
    log "Patching torchaudio: installing libsox.dylib and its dylib closure into torchaudio/lib..."

    # Locate libsox.dylib from Homebrew (works on both Apple Silicon and Intel).
    # Search each candidate directory individually so that a missing directory
    # (e.g. /usr/local/Cellar/sox absent on Apple Silicon) does not cause `find`
    # to exit 1 and abort the script under set -eo pipefail.
    local libsox=""
    for _sox_dir in /opt/homebrew/Cellar/sox /usr/local/Cellar/sox; do
        [[ -d "$_sox_dir" ]] || continue
        libsox=$(find "$_sox_dir" -name "libsox.dylib" 2>/dev/null | head -1) || true
        [[ -n "$libsox" ]] && break
    done
    if [[ -z "$libsox" ]]; then
        for _lib_dir in /opt/homebrew/lib /usr/local/lib; do
            [[ -f "${_lib_dir}/libsox.dylib" ]] && libsox="${_lib_dir}/libsox.dylib" && break
        done
    fi

    if [[ -z "$libsox" ]]; then
        log_error "libsox.dylib not found - ensure sox is installed ('brew install sox' runs automatically in step 1)"
        exit 1
    fi
    log "  Found $libsox"

    # Locate torchaudio/lib in the active (build) venv
    local ta_lib
    ta_lib=$(python -c "import torchaudio, os; print(os.path.join(os.path.dirname(torchaudio.__file__), 'lib'))" 2>/dev/null || true)

    if [[ -z "$ta_lib" || ! -d "$ta_lib" ]]; then
        log_warning "torchaudio/lib not found in build venv - skipping sox patch"
        return 0
    fi

    bundle_macos_dylib_closure "$libsox" "$ta_lib" "libsox.dylib"
    log_success "torchaudio sox dylib closure bundled"
}

# Copy libportaudio.2.dylib from Homebrew into pyaudio's package directory and
# retarget _portaudio.*.so's load command to @loader_path.
#
# pyaudio's _portaudio extension embeds an absolute Homebrew path:
#   /opt/homebrew/opt/portaudio/lib/libportaudio.2.dylib
# Nuitka's dependency scanner encounters the absolute path, tries to handle it
# as a bundled dependency, and aborts with:
#   FATAL: Error, problem with dependency scan of '_portaudio.*.so'
#          with '/opt/homebrew/opt/portaudio/lib/libportaudio.2.dylib'
# The fix mirrors patch_torchaudio_sox(): copy the dylib alongside the .so and
# rewrite the load command to @loader_path so Nuitka can resolve it locally.
patch_pyaudio_portaudio() {
    log "Patching pyaudio: installing libportaudio.2.dylib into pyaudio package dir..."

    # Locate libportaudio.2.dylib from Homebrew (Apple Silicon + Intel paths).
    local libportaudio=""
    for _lib_dir in \
        /opt/homebrew/opt/portaudio/lib \
        /opt/homebrew/lib \
        /usr/local/opt/portaudio/lib \
        /usr/local/lib; do
        if [[ -f "${_lib_dir}/libportaudio.2.dylib" ]]; then
            libportaudio="${_lib_dir}/libportaudio.2.dylib"
            break
        fi
    done
    if [[ -z "$libportaudio" ]]; then
        for _pa_dir in /opt/homebrew/Cellar/portaudio /usr/local/Cellar/portaudio; do
            [[ -d "$_pa_dir" ]] || continue
            libportaudio=$(find "$_pa_dir" -name "libportaudio.2.dylib" 2>/dev/null | head -1) || true
            [[ -n "$libportaudio" ]] && break
        done
    fi

    if [[ -z "$libportaudio" ]]; then
        log_error "libportaudio.2.dylib not found - ensure portaudio is installed ('brew install portaudio' runs automatically in step 1)"
        exit 1
    fi
    log "  Found $libportaudio"

    # Locate the pyaudio package directory in the active (build) venv.
    local pyaudio_dir
    pyaudio_dir=$(python -c "import pyaudio, os; print(os.path.dirname(pyaudio.__file__))" 2>/dev/null || true)

    if [[ -z "$pyaudio_dir" || ! -d "$pyaudio_dir" ]]; then
        log_warning "pyaudio package not found in build venv - skipping portaudio patch"
        return 0
    fi

    # Copy the dylib next to the .so and rewrite its install ID to @loader_path
    # so Nuitka sees it as a bundled local dependency rather than the original
    # Homebrew path.
    bundle_macos_dylib_closure "$libportaudio" "$pyaudio_dir" "libportaudio.2.dylib"

    # Rewrite the absolute Homebrew load command in _portaudio.*.so to
    # @loader_path/libportaudio.2.dylib so Nuitka resolves it locally.
    local portaudio_so
    portaudio_so=$(find "$pyaudio_dir" -name "_portaudio*.so" 2>/dev/null | head -1) || true
    if [[ -z "$portaudio_so" ]]; then
        log_warning "_portaudio*.so not found in $pyaudio_dir - skipping load-command rewrite"
        return 0
    fi

    # Determine the current absolute path embedded in the .so.
    local embedded_path
    embedded_path=$(otool -L "$portaudio_so" 2>/dev/null \
        | awk '/libportaudio/{print $1}' \
        | grep -v '@loader_path' \
        | head -1) || true

    if [[ -z "$embedded_path" ]]; then
        log_success "_portaudio.so already uses a relative/loader path - no rewrite needed"
        return 0
    fi

    log "  Rewriting load command: $embedded_path → @loader_path/libportaudio.2.dylib"
    install_name_tool -change \
        "$embedded_path" \
        "@loader_path/libportaudio.2.dylib" \
        "$portaudio_so"

    log_success "pyaudio portaudio patch applied"
}

# torch's bundled libomp.dylib can retain an absolute Homebrew install ID even
# when it already lives inside torch/lib.  Torch is currently excluded from the
# Nuitka follow graph, but rewrite it proactively so future changes do not trip
# the same dependency-scan failure class.
patch_torch_libomp() {
    log "Patching torch: rewriting libomp.dylib install ID if present..."

    local torch_lib_dir
    torch_lib_dir=$(python -c "import torch, os; print(os.path.join(os.path.dirname(torch.__file__), 'lib'))" 2>/dev/null || true)
    if [[ -z "$torch_lib_dir" || ! -d "$torch_lib_dir" ]]; then
        log_warning "torch/lib not found in build venv - skipping libomp patch"
        return 0
    fi

    local libomp_target="${torch_lib_dir}/libomp.dylib"
    if [[ ! -f "$libomp_target" ]]; then
        log_warning "torch libomp.dylib not found in $torch_lib_dir - skipping"
        return 0
    fi

    bundle_macos_dylib_closure "$libomp_target" "$torch_lib_dir" "libomp.dylib"
    log_success "torch libomp patch applied"
}

retarget_packaged_torchaudio_torch_libs() {
    local resources_root="$1"
    local torch_lib_dir="${resources_root}/runtime_site_packages/torch/lib"

    if [[ ! -d "$torch_lib_dir" ]]; then
        log_warning "Packaged torch/lib overlay not found at $torch_lib_dir - skipping torchaudio Torch dylib retarget"
        return 0
    fi

    log "Retargeting packaged torchaudio Torch dylib load commands to runtime_site_packages/torch/lib..."

    local -a torch_deps=(
        "libtorch_python.dylib"
        "libtorch.dylib"
        "libtorch_cpu.dylib"
        "libc10.dylib"
    )
    local -a torchaudio_targets=(
        "${resources_root}/torchaudio/lib/_torchaudio.so"
        "${resources_root}/torchaudio/lib/_torchaudio_sox.so"
        "${resources_root}/torchaudio/lib/libtorchaudio.so"
        "${resources_root}/libtorchaudio_sox.so"
    )

    local patched=0
    local target dep old_load new_load existing_loads
    for target in "${torchaudio_targets[@]}"; do
        [[ -f "$target" ]] || continue
        existing_loads="$(otool -L "$target" 2>/dev/null || true)"
        [[ -n "$existing_loads" ]] || continue
        for dep in "${torch_deps[@]}"; do
            old_load="@executable_path/${dep}"
            new_load="@executable_path/runtime_site_packages/torch/lib/${dep}"
            if grep -Fq "$old_load" <<<"$existing_loads"; then
                install_name_tool -change "$old_load" "$new_load" "$target"
                patched=$((patched + 1))
            fi
        done
    done

    if [[ "$patched" -gt 0 ]]; then
        log_success "Retargeted $patched torchaudio Torch dylib load command(s)"
    else
        log "Packaged torchaudio Torch dylib load commands already use runtime_site_packages/torch/lib"
    fi
}

# onnxruntime wheels ship only libonnxruntime.<major>.<minor>.<patch>.dylib but
# the dylib's own load command references itself via the shorter unversioned name:
#   @rpath/libonnxruntime.1.dylib   (with @rpath = @loader_path)
# Because the wheel never creates the unversioned alias, Nuitka's dependency
# scanner resolves @rpath → @loader_path → <capi_dir> and then cannot find
# libonnxruntime.1.dylib, aborting with:
#   FATAL: Error, failed to find path '@rpath/libonnxruntime.1.dylib'
#
# Fix: create libonnxruntime.1.dylib as a real copy of the versioned file,
# set its install ID to @loader_path/libonnxruntime.1.dylib, and rewrite the
# @rpath load command in the versioned dylib to @loader_path so Nuitka can
# resolve the dependency locally without following a missing file.
patch_onnxruntime_dylib() {
    log "Patching onnxruntime: creating libonnxruntime.1.dylib alias in capi directory..."

    # Locate the onnxruntime capi directory in the active (build) venv.
    local onnxruntime_capi_dir
    onnxruntime_capi_dir=$(python -c "import onnxruntime, os; print(os.path.join(os.path.dirname(onnxruntime.__file__), 'capi'))" 2>/dev/null || true)

    if [[ -z "$onnxruntime_capi_dir" || ! -d "$onnxruntime_capi_dir" ]]; then
        log_warning "onnxruntime capi directory not found in build venv - skipping onnxruntime patch"
        return 0
    fi

    # Find the versioned dylib (e.g. libonnxruntime.1.26.0.dylib).
    # Exclude the unversioned name itself to avoid matching an existing alias.
    local versioned_dylib
    versioned_dylib=$(find "$onnxruntime_capi_dir" -maxdepth 1 \
        -name "libonnxruntime.*.dylib" \
        ! -name "libonnxruntime.1.dylib" \
        2>/dev/null | sort | head -1) || true

    if [[ -z "$versioned_dylib" ]]; then
        log_warning "libonnxruntime versioned dylib not found in $onnxruntime_capi_dir - skipping"
        return 0
    fi
    log "  Found versioned dylib: $versioned_dylib"

    # Check whether the @rpath/libonnxruntime.1.dylib load command is present.
    local rpath_entry
    rpath_entry=$(otool -L "$versioned_dylib" 2>/dev/null \
        | awk '/@rpath\/libonnxruntime\.1\.dylib/{print $1}' \
        | head -1) || true

    if [[ -z "$rpath_entry" ]]; then
        log_success "libonnxruntime versioned dylib has no @rpath/libonnxruntime.1.dylib load command - no patch needed"
        return 0
    fi
    log "  Found @rpath load command: $rpath_entry"

    local unversioned_dylib="$onnxruntime_capi_dir/libonnxruntime.1.dylib"

    # Materialise the unversioned alias as a real file if it does not exist or
    # is a symlink (pip wheels never include symlinks, so a symlink here means
    # something external created it; replace it with a concrete copy so Nuitka
    # can stat it reliably).
    if [[ -L "$unversioned_dylib" || ! -f "$unversioned_dylib" ]]; then
        [[ -L "$unversioned_dylib" ]] && rm -f "$unversioned_dylib"
        log "  Creating $unversioned_dylib as a real copy of $(basename "$versioned_dylib")"
        cp -L "$versioned_dylib" "$unversioned_dylib"
        chmod u+w "$unversioned_dylib"
    fi

    # Fix the install ID of the unversioned alias so Nuitka records the correct
    # @loader_path-relative name when it bundles the file.
    local desired_id="@loader_path/libonnxruntime.1.dylib"
    local current_id
    current_id=$(otool -D "$unversioned_dylib" 2>/dev/null | tail -n 1 | xargs) || true
    if [[ -n "$current_id" && "$current_id" != "$desired_id" ]]; then
        log "  Rewriting install ID: $current_id → $desired_id"
        install_name_tool -id "$desired_id" "$unversioned_dylib"
    fi

    # Rewrite the @rpath load command in the versioned dylib to @loader_path so
    # Nuitka resolves the dependency against the same directory as the binary.
    log "  Rewriting load command in $(basename "$versioned_dylib"): $rpath_entry → @loader_path/libonnxruntime.1.dylib"
    chmod u+w "$versioned_dylib"
    install_name_tool -change \
        "$rpath_entry" \
        "@loader_path/libonnxruntime.1.dylib" \
        "$versioned_dylib"

    log_success "onnxruntime dylib @rpath patch applied"
}

download_and_bundle_nodejs() {
    log "Downloading Node.js..."

    # Use LTS v22 (Active LTS) to match common system Node.js versions.
    # Auto-detect arch so the same script works on both Apple Silicon and Intel.
    local node_version="22.16.0"
    local node_arch
    node_arch="$(uname -m)"
    case "$node_arch" in
        arm64)  node_arch="arm64" ;;
        x86_64) node_arch="x64"   ;;
        *)
            log_warning "Unknown architecture '$node_arch'; defaulting to arm64"
            node_arch="arm64"
            ;;
    esac

    local node_tarball="node-v${node_version}-darwin-${node_arch}.tar.xz"
    local node_url="https://nodejs.org/dist/v${node_version}/${node_tarball}"
    local node_dist="${BUILD_DIR}/node-dist"
    local node_bundle="${ARTIFACTS_DIR}/node"

    mkdir -p "$node_dist" "$node_bundle"

    # Download with progress (skip if already cached)
    if [[ ! -f "${node_dist}/${node_tarball}" ]]; then
        log "Downloading Node.js ${node_version} (${node_arch})..."
        curl -L -o "${node_dist}/${node_tarball}" "$node_url" \
            --progress-bar 2>&1 || {
            log_error "Failed to download Node.js"
            exit 1
        }
    else
        log "Using cached Node.js tarball: ${node_dist}/${node_tarball}"
    fi

    # Extract
    log "Extracting Node.js..."
    tar -xf "${node_dist}/${node_tarball}" -C "$node_dist"

    local node_extracted_dir="${node_dist}/node-v${node_version}-darwin-${node_arch}"

    # Copy to artifacts (only bin/ and lib/ - skip docs/man)
    cp -R -P "${node_extracted_dir}/bin" "$node_bundle/"
    cp -R -P "${node_extracted_dir}/lib" "$node_bundle/" 2>/dev/null || true

    # Cleanup download to save space
    rm -rf "$node_dist"

    log_success "Node.js ${node_version} (${node_arch}) bundled: $node_bundle"
}

# Download Playwright Chromium
download_playwright_browsers() {
    log "Installing Playwright browsers (Chromium)..."

    export PLAYWRIGHT_BROWSERS_PATH="${ARTIFACTS_DIR}/chromium"
    mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"

    log "Using browser cache path: $PLAYWRIGHT_BROWSERS_PATH"

    # Use Python to download via Playwright with proper environment
    local playwright_log="${BUILD_DIR}/playwright.log"

    source "${BUILD_DIR}/venv/bin/activate" 2>/dev/null || true

    if ! "$PYTHON_CMD" -c "import playwright" >/dev/null 2>&1; then
        log_warning "Playwright package not installed for this requirements set; skipping browser bundle"
        return 0
    fi

    if ! PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
        "$PYTHON_CMD" -m playwright install chromium 2>&1 | tee "$playwright_log"; then
        log_warning "Playwright install reported completion (may still have succeeded)"
    fi

    # Show download progress
    if [[ -f "$playwright_log" ]]; then
        grep -E "(Downloading|Extracting|✓)" "$playwright_log" 2>/dev/null || echo "(no progress info)"
    fi

    # Verify download with multiple possible locations
    log "Verifying Chromium installation..."

    local chromium_found=false
    local cache_root=""

    # Check in the specified PLAYWRIGHT_BROWSERS_PATH
    if ls -d "${PLAYWRIGHT_BROWSERS_PATH}"/chromium-* 2>/dev/null | head -1 > /dev/null 2>&1; then
        chromium_found=true
        log "Found Chromium in: $PLAYWRIGHT_BROWSERS_PATH"
    fi

    # Also check in user's Playwright cache roots.
    if [[ "$chromium_found" != true ]]; then
        for cache_root in "$HOME/Library/Caches/ms-playwright" "$HOME/.cache/ms-playwright"; do
            if copy_playwright_cache_to_artifacts "$cache_root"; then
                if ls -d "${PLAYWRIGHT_BROWSERS_PATH}"/chromium-* 2>/dev/null | head -1 > /dev/null 2>&1; then
                    log "Restored Chromium artifacts from cache: $cache_root"
                    chromium_found=true
                    break
                fi
            fi
        done
    fi

    if [[ "$chromium_found" != true ]]; then
        log_error "Chromium browser not found"
        log "Checked locations:"
        log "  1. $PLAYWRIGHT_BROWSERS_PATH"
        log "  2. $HOME/Library/Caches/ms-playwright"
        log "  3. $HOME/.cache/ms-playwright"
        log "Directory contents of $PLAYWRIGHT_BROWSERS_PATH:"
        ls -la "$PLAYWRIGHT_BROWSERS_PATH" 2>/dev/null || echo "  (empty or doesn't exist)"
        return 1
    fi

    log_success "Playwright installation complete"
}

ensure_playwright_browser_artifacts() {
    if [[ -d "$ARTIFACTS_DIR/chromium" ]] && ls "$ARTIFACTS_DIR/chromium"/chromium-* >/dev/null 2>&1; then
        return 0
    fi

    log_warning "Bundled Playwright artifacts are missing; restoring them before packaging."
    download_playwright_browsers
}

# Install npm dependencies for bundled Node.js services
install_node_service_deps() {
    log "Installing Node.js dependencies for bundled services..."

    local node_exe=""
    # Prefer the bundled node we just downloaded
    if [[ -f "${ARTIFACTS_DIR}/node/bin/node" ]]; then
        node_exe="${ARTIFACTS_DIR}/node/bin/node"
    elif command -v node &>/dev/null; then
        node_exe="$(command -v node)"
    fi

    if [[ -z "$node_exe" ]]; then
        log_warning "node not found - skipping npm install for node services."
        log_warning "Run 'npm install' inside node/whatsapp and node/tunnelmole before building."
        return 0
    fi

    local node_bin_dir
    node_bin_dir="$(dirname "$node_exe")"

    for svc in whatsapp tunnelmole; do
        local svc_dir="${PROJECT_ROOT}/node/${svc}"
        if [[ ! -f "${svc_dir}/package.json" ]]; then
            log_warning "No package.json for node service '${svc}' at ${svc_dir} - skipping."
            continue
        fi
        log "Running locked 'npm ci --omit=dev' for '${svc}'..."
        if PATH="${node_bin_dir}:${PATH}" \
             "${node_exe}" "$(dirname "$node_bin_dir")/lib/node_modules/npm/bin/npm-cli.js" \
             ci --omit=dev --prefix "${svc_dir}" 2>&1 | tee -a "${BUILD_DIR}/npm_${svc}.log"; then
            log_success "Locked npm dependencies installed for '${svc}'."
        else
            # Fallback: try system npm
            if command -v npm &>/dev/null; then
                log_warning "Bundled npm failed for '${svc}', retrying locked npm ci..."
                (cd "${svc_dir}" && npm ci --omit=dev 2>&1 | tee -a "${BUILD_DIR}/npm_${svc}.log")
            else
                log_warning "npm ci failed for '${svc}' - services may not start."
            fi
        fi
    done
}

install_tunnelmole_runtime() {
    local runtime_root="$1"

    log "Preparing tunnelmole runtime..."
    mkdir -p "$runtime_root"

    local prep_output
    if ! prep_output=$("$PYTHON_CMD" - <<'PY'
from pathlib import Path
import sys

repo_root = Path.cwd().resolve()
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from shared.tunnelmole_downloader import download_tunnelmole

binary = download_tunnelmole(force=True)
if binary is None:
    raise SystemExit("tunnelmole runtime binary unavailable")

print(f"TUNNELMOLE_BIN={Path(binary).resolve()}")
PY
); then
        log_error "Failed to prepare tunnelmole runtime"
        return 1
    fi

    local source_binary
    source_binary="$(printf '%s\n' "$prep_output" | awk -F= '/^TUNNELMOLE_BIN=/{print $2}' | tail -1)"
    if [[ -z "$source_binary" || ! -f "$source_binary" ]]; then
        log_error "Could not locate the prepared tunnelmole runtime binary"
        printf '%s\n' "$prep_output"
        return 1
    fi

    cp "$source_binary" "$runtime_root/$(basename "$source_binary")"
    chmod +x "$runtime_root/$(basename "$source_binary")" || true
    log_success "Tunnelmole runtime bundled from $source_binary"
}

find_whisper_cpp_source_archive() {
    local version="$1"
    local override="${AUTOYOU_MACOS_WHISPER_CPP_SOURCE_ARCHIVE:-}"
    local candidate=""

    if [[ -n "$override" ]]; then
        if [[ -f "$override" ]]; then
            printf '%s\n' "$override"
            return 0
        fi
        log_error "AUTOYOU_MACOS_WHISPER_CPP_SOURCE_ARCHIVE does not exist: $override" >&2
        return 1
    fi

    for candidate in \
        "${HOME}/Library/Caches/Homebrew/whisper-cpp--${version}.tar.gz" \
        "${HOME}/Library/Caches/Homebrew/downloads/"*"--whisper.cpp-${version}.tar.gz"; do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    local cache_dir="${BUILD_DIR}/cache"
    local archive_path="${cache_dir}/whisper.cpp-${version}.tar.gz"
    local archive_tmp="${archive_path}.tmp"
    mkdir -p "$cache_dir"

    if [[ -f "$archive_path" ]]; then
        printf '%s\n' "$archive_path"
        return 0
    fi

    local archive_url="https://github.com/ggerganov/whisper.cpp/archive/refs/tags/v${version}.tar.gz"
    log "Downloading whisper.cpp source fallback: $archive_url" >&2
    if curl -fL --retry 3 --connect-timeout 20 "$archive_url" -o "$archive_tmp"; then
        mv "$archive_tmp" "$archive_path"
        printf '%s\n' "$archive_path"
        return 0
    fi

    rm -f "$archive_tmp"
    log_error "Failed to download whisper.cpp source fallback" >&2
    return 1
}

build_minimal_whisper_cpp_cli() {
    local runtime_root="$1"
    local version="${AUTOYOU_MACOS_WHISPER_CPP_VERSION:-1.8.6}"
    local build_root="${BUILD_DIR}/whisper-cpp-build"
    local source_extract_root="${build_root}/src"
    local source_dir=""
    local archive_path=""

    if ! command -v cmake >/dev/null 2>&1; then
        log_error "cmake is required to build the fallback whisper.cpp CLI"
        return 1
    fi

    if ! archive_path="$(find_whisper_cpp_source_archive "$version")"; then
        return 1
    fi

    log "Building fallback whisper.cpp CLI ${version} with ${NUITKA_JOBS} job(s)..."
    rm -rf "$build_root"
    mkdir -p "$source_extract_root"

    if ! tar -xzf "$archive_path" -C "$source_extract_root"; then
        log_error "Failed to extract whisper.cpp source archive: $archive_path"
        return 1
    fi

    source_dir="$(find "$source_extract_root" -maxdepth 1 -type d -name 'whisper.cpp-*' -print -quit)"
    if [[ -z "$source_dir" || ! -d "$source_dir" ]]; then
        log_error "Could not locate extracted whisper.cpp source under $source_extract_root"
        return 1
    fi

    cmake \
        -S "$source_dir" \
        -B "${build_root}/build" \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DWHISPER_BUILD_EXAMPLES=ON \
        -DWHISPER_BUILD_SERVER=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_SDL2=OFF \
        -DWHISPER_USE_SYSTEM_GGML=OFF \
        -DGGML_METAL=OFF \
        -DGGML_OPENMP=OFF

    cmake --build "${build_root}/build" --target whisper-cli --parallel "$NUITKA_JOBS"

    local source_binary="${build_root}/build/bin/whisper-cli"
    if [[ ! -x "$source_binary" ]]; then
        log_error "Fallback whisper.cpp CLI was not produced at $source_binary"
        return 1
    fi

    bundle_macos_executable_with_dylib_closure "$source_binary" "$runtime_root" "whisper-cli"
    log_success "Fallback whisper.cpp runtime bundled from source build"
}

bundle_macos_executable_with_dylib_closure() {
    local source_executable="$1"
    local target_dir="$2"
    local output_name="${3:-$(basename "$1")}"

    mkdir -p "$target_dir"

    local bundled_executable="$target_dir/$output_name"
    cp -L "$source_executable" "$bundled_executable"
    chmod u+w "$bundled_executable" 2>/dev/null || true
    chmod +x "$bundled_executable" 2>/dev/null || true

    local dep_path=""
    local resolved_dep_path=""
    while IFS= read -r dep_path; do
        [[ -z "$dep_path" ]] && continue
        resolved_dep_path="$dep_path"
        if [[ "$resolved_dep_path" != /opt/homebrew/* && "$resolved_dep_path" != /usr/local/* ]]; then
            resolved_dep_path="$(resolve_macos_dependency_path "$dep_path" "$bundled_executable" || true)"
        fi
        if [[ -z "$resolved_dep_path" ]]; then
            continue
        fi
        if [[ "$resolved_dep_path" != /opt/homebrew/* && "$resolved_dep_path" != /usr/local/* ]]; then
            continue
        fi

        local dep_base
        dep_base="$(basename "$resolved_dep_path")"
        bundle_macos_dylib_closure "$resolved_dep_path" "$target_dir" "$dep_base"

        log "  Rewriting executable load command: $dep_path → @executable_path/$dep_base"
        install_name_tool -change "$dep_path" "@executable_path/$dep_base" "$bundled_executable"
    done < <(otool -L "$bundled_executable" 2>/dev/null | awk 'NR>1 {print $1}')
}

install_whisper_cpp_runtime() {
    local runtime_root="$1"

    log "Preparing whisper.cpp runtime..."
    mkdir -p "$runtime_root"

    local source_binary=""
    local whisper_prefix=""
    # Recent Homebrew ggml discovers additional backends through dlopen at its
    # Cellar path. Static CLI builds keep the v2 speech runtime self-contained.
    if [[ "${AUTOYOU_BUILD_DESKTOP_V2:-0}" != "1" ]]; then
        whisper_prefix="$(brew --prefix whisper-cpp 2>/dev/null || true)"
    fi
    if [[ -n "$whisper_prefix" && -d "$whisper_prefix" ]]; then
        for candidate in \
            "$whisper_prefix/bin/whisper-cli" \
            "$whisper_prefix/bin/whisper" \
            "$whisper_prefix/bin/main"; do
            if [[ -x "$candidate" ]]; then
                source_binary="$candidate"
                break
            fi
        done

        if [[ -z "$source_binary" ]]; then
            source_binary="$(find "$whisper_prefix" -type f \( -name 'whisper-cli' -o -name 'whisper' -o -name 'main' \) -perm -111 2>/dev/null | head -1)"
        fi
    fi

    if [[ -n "$source_binary" && -x "$source_binary" ]]; then
        bundle_macos_executable_with_dylib_closure "$source_binary" "$runtime_root" "$(basename "$source_binary")"
    else
        log "Building a self-contained whisper-cli runtime."
        if ! build_minimal_whisper_cpp_cli "$runtime_root"; then
            return 1
        fi
    fi

    local bundled_binary=""
    for candidate in \
        "$runtime_root/whisper-cli" \
        "$runtime_root/whisper" \
        "$runtime_root/main"; do
        if [[ -x "$candidate" ]]; then
            bundled_binary="$candidate"
            break
        fi
    done

    if [[ ! -x "$bundled_binary" ]]; then
        log_error "Bundled whisper.cpp binary missing after copy"
        return 1
    fi

    # install_name_tool invalidates the upstream ad-hoc signatures. Re-sign the
    # rewritten closure before launching it or arm64 macOS can SIGKILL it.
    adhoc_sign_macos_tree "$runtime_root"

    if verify_macos_binary_self_check "$bundled_binary" 20; then
        log_success "whisper.cpp runtime bundled at $bundled_binary"
        return 0
    fi

    log_error "Bundled whisper.cpp binary failed self-check: $bundled_binary"
    return 1
}

run_nuitka_with_retry() {
    local requested_jobs="$1"
    local log_file="$2"
    shift 2
    local base_args=("$@")

    local attempt_jobs="$requested_jobs"
    local cache_reset_done=false
    local no_space_cleanup_done=false
    local data_composer_retry_done=false
    local data_composer_all_cache_retry=false
    local has_disable_cache_flag=false
    local has_clean_cache_flag=false
    local arg

    for arg in "${base_args[@]}"; do
        case "$arg" in
            --disable-cache=*)
                has_disable_cache_flag=true
                ;;
            --clean-cache=*)
                has_clean_cache_flag=true
                ;;
        esac
    done

    if ! [[ "$attempt_jobs" =~ ^[0-9]+$ ]] || [[ "$attempt_jobs" -lt 1 ]]; then
        attempt_jobs=1
    fi

    while true; do
        local attempt_args=()
        local arg
        for arg in "${base_args[@]}"; do
            if [[ "$arg" == --jobs=* ]]; then
                attempt_args+=("--jobs=$attempt_jobs")
            else
                attempt_args+=("$arg")
            fi
        done

        if [[ "$data_composer_retry_done" == true && "$has_disable_cache_flag" == false ]]; then
                attempt_args+=("--disable-cache=bytecode" "--disable-cache=compression" "--clean-cache=bytecode" "--clean-cache=compression")
        fi

        log "Invoking Nuitka with --jobs=$attempt_jobs via $PYTHON_CMD..."
        "$PYTHON_CMD" -m nuitka "${attempt_args[@]}" autoyou_app.py 2>&1 | tee "$log_file"
        local pipeline_status=("${PIPESTATUS[@]}")
        local nuitka_status="${pipeline_status[0]:-1}"
        if [[ "$nuitka_status" -eq 0 ]]; then
            NUITKA_EFFECTIVE_JOBS="$attempt_jobs"
            return 0
        fi

        if grep -qiE "Nuitka-DataComposer:WARNING: Problem with constant file|invalid load key|Error executing data composer" "$log_file"; then
            if [[ "$data_composer_retry_done" == false ]]; then
                log_warning "Nuitka data composer failed while reading constants; retrying once with bytecode/compression cache disabled and cleaned."
                data_composer_retry_done=true
                rm -rf "$BACKEND_BUILD"/*.app "$BACKEND_BUILD"/*.dist "$BACKEND_BUILD"/*.bin "$BACKEND_BUILD"/*.so "$BACKEND_BUILD"/*.dylib "$BACKEND_BUILD"/*.build "$BACKEND_BUILD"/*.c "$BACKEND_BUILD"/*.h "$BACKEND_BUILD"/*.o 2>/dev/null || true
                mkdir -p "$BACKEND_BUILD"
                scrub_appledouble_sidecars "$BACKEND_BUILD"
                # Nuitka 4.1.x does not accept comma-separated cache names; pass values as separate options.
                attempt_args+=("--disable-cache=bytecode" "--disable-cache=compression" "--clean-cache=bytecode" "--clean-cache=compression")
                continue
            fi

            if [[ "$data_composer_all_cache_retry" == false ]]; then
                log_warning "Nuitka data composer failed after cache-reset retry; retrying once with all caches disabled and single-job compile."
                data_composer_all_cache_retry=true
                attempt_jobs=1
                rm -rf "$BACKEND_BUILD"/*.app "$BACKEND_BUILD"/*.dist "$BACKEND_BUILD"/*.bin "$BACKEND_BUILD"/*.so "$BACKEND_BUILD"/*.dylib "$BACKEND_BUILD"/*.build "$BACKEND_BUILD"/*.c "$BACKEND_BUILD"/*.h "$BACKEND_BUILD"/*.o 2>/dev/null || true
                mkdir -p "$BACKEND_BUILD"
                scrub_appledouble_sidecars "$BACKEND_BUILD"
                attempt_args+=("--disable-cache=all" "--clean-cache=all")
                continue
            fi

            log_warning "Nuitka data-composer failure persisted after cache-reset retry; treating as a fatal compile failure."
            return 1
        fi

        # Nuitka's macOS post-build step force-signs the standalone output with
        # a single `codesign --force --deep --preserve-metadata=entitlements
        # <every Mach-O>` call. On a large bundle and/or under disk pressure
        # this dies with "internal error in Code Signing subsystem" and Nuitka
        # treats it as FATAL - even though C compilation, linking, the
        # .dist/.app, bundled data and the AutoYouServer executable were all
        # already produced. The script does its OWN correct inside-out signing
        # later in codesign_binaries() (no --deep), so a failure limited to
        # Nuitka's cosmetic ad-hoc sign must NOT abort the ~3.5h build. Only
        # swallow it when the executable actually exists AND the failure is not
        # a disk-full event (those fall through to the no-space retry below,
        # which reclaims space and retries - the better recovery).
        if grep -qiE "call to '/usr/bin/codesign' failed|internal error in Code Signing subsystem" "$log_file" \
            && ! grep -qi 'No space left on device' "$log_file"; then
            local produced_app produced_dist produced_exe=""
            produced_app=$(resolve_nuitka_app_bundle_path || true)
            produced_dist=$(resolve_nuitka_dist_path || true)
            if [[ -n "$produced_app" && -f "$produced_app/Contents/MacOS/AutoYouServer" ]]; then
                produced_exe="$produced_app/Contents/MacOS/AutoYouServer"
            elif [[ -n "$produced_dist" && -f "$produced_dist/AutoYouServer" ]]; then
                produced_exe="$produced_dist/AutoYouServer"
            fi
            if [[ -n "$produced_exe" ]]; then
                log_warning "Nuitka finished compiling but its internal 'codesign --deep' post-step failed (known macOS Code Signing subsystem error on large standalone bundles)."
                log_warning "Compiled output is present: ${produced_app:-$produced_dist}"
                log_warning "Continuing - the build's inside-out codesign_binaries() will sign correctly without --deep."
                NUITKA_EFFECTIVE_JOBS="$attempt_jobs"
                return 0
            fi
            log_warning "Nuitka codesign post-step failed and no usable executable was produced; treating as a real compile failure."
        fi

        local was_sigkill=false
        local was_no_space=false
        if [[ "$nuitka_status" -eq 137 || "$nuitka_status" -eq 9 ]]; then
            was_sigkill=true
        fi

        if grep -qi 'No space left on device' "$log_file"; then
            was_no_space=true
        fi

        if [[ "$was_no_space" == true ]]; then
            if [[ "$no_space_cleanup_done" == true ]]; then
                return 1
            fi

            log_warning "Nuitka exhausted local disk space during packaging. Cleaning caches and retrying while preserving the incremental .build cache."
            reclaim_disk_space_for_nuitka
            if [[ "$(get_available_space_bytes "$BUILD_DIR")" -lt "$NUITKA_MIN_FREE_SPACE_BYTES" ]]; then
                return 1
            fi
            no_space_cleanup_done=true
            continue
        fi

        if [[ "$cache_reset_done" == false ]] && grep -qiE '__helpers\.h|__constants\.h' "$log_file"; then
            log_warning "Detected an incomplete Nuitka incremental cache. Clearing preserved .build output and retrying once."
            rm -rf "$BACKEND_BUILD"/*
            mkdir -p "$BACKEND_BUILD"
            cache_reset_done=true
            continue
        fi

        # macOS Jetsam kills the clang child during scons, not the Nuitka
        # parent: Nuitka exits non-137/9 and the log shows
        # "scons: *** [...] Error 255" + "Failed unexpectedly in Scons C
        # backend compilation" rather than "Killed: 9"/"out of memory". The
        # original detector missed that, so the halve-jobs retry below never
        # engaged and the build burned hours before giving up. Treat the scons
        # C-backend failure / clang exec failure as a memory-pressure signal so
        # the bounded jobs-halving retry (only while attempt_jobs > 1) can
        # recover automatically.
        local was_scons_backend_failure=false
        if grep -qiE 'Failed unexpectedly in Scons C backend compilation|scons: \*\*\* \[[^]]*\] Error (255|254|139|137)|clang: error: (unable to execute command|clang frontend command failed)|posix_spawn failed' "$log_file"; then
            was_scons_backend_failure=true
        fi

        if [[ "$was_sigkill" == false && "$was_scons_backend_failure" == false ]] && ! grep -qiE 'MemoryError|Killed: 9| killed$|out of memory|cannot allocate memory|LLVM ERROR' "$log_file"; then
            return 1
        fi

        if [[ "$was_scons_backend_failure" == true && "$attempt_jobs" -le 1 ]]; then
            log_error "Scons C backend compilation failed even at --jobs=1. This is not parallelism-related; inspect $log_file (look for the clang command and the module it died on)."
            return 1
        fi

        if [[ "$attempt_jobs" -le 1 ]]; then
            return 1
        fi

        local next_jobs=$(( attempt_jobs / 2 ))
        if [[ "$next_jobs" -lt 1 ]]; then
            next_jobs=1
        fi

        if [[ "$was_sigkill" == true ]]; then
            log_warning "Nuitka/clang was killed by macOS while compiling with --jobs=$attempt_jobs. Retrying with --jobs=$next_jobs."
        else
            log_warning "Nuitka ran out of memory with --jobs=$attempt_jobs. Retrying with --jobs=$next_jobs."
        fi
        rm -rf "$BACKEND_BUILD"/*
        mkdir -p "$BACKEND_BUILD"
        attempt_jobs="$next_jobs"
    done
}

resolve_nuitka_app_bundle_path() {
    local candidate=""
    for candidate in \
        "$BACKEND_BUILD/AutoYou.app" \
        "$BACKEND_BUILD/AutoYouServer.app" \
        "$BACKEND_BUILD/autoyou_app.app"; do
        if [[ -d "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    candidate=$(find "$BACKEND_BUILD" -maxdepth 1 -type d -name '*.app' 2>/dev/null | sed -n '1p')
    if [[ -n "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi
    return 1
}

resolve_nuitka_dist_path() {
    local candidate=""
    for candidate in \
        "$BACKEND_BUILD/AutoYou.dist" \
        "$BACKEND_BUILD/AutoYouServer.dist" \
        "$BACKEND_BUILD/autoyou_app.dist"; do
        if [[ -d "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    candidate=$(find "$BACKEND_BUILD" -maxdepth 1 -type d -name '*.dist' 2>/dev/null | sed -n '1p')
    if [[ -n "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi
    return 1
}

cleanup_incomplete_nuitka_build_cache() {
    local build_cache=""

    for build_cache in "$BACKEND_BUILD"/*.build; do
        [[ -d "$build_cache" ]] || continue

        if [[ -f "$build_cache/__helpers.h" && -f "$build_cache/__constants.h" ]]; then
            continue
        fi

        log_warning "Removing incomplete Nuitka build cache: $build_cache"
        rm -rf "$build_cache"
    done
}

# Compile server.py to binary
compile_python_backend() {
    log "Compiling Python backend to native binary..."
    stage_packaged_guides
    "$PYTHON_CMD" "$PROJECT_ROOT/scripts/prepare_intent_router.py" || return 1

    # --skip-nuitka: reuse an existing launcher build and skip the destructive
    # prep + the ~3.5h recompile. Everything after this (runtime_modules, Node,
    # tunnelmole, whisper, fonts, hardening) still re-runs so post-compile
    # fixes are applied without paying the Nuitka cost again.
    SKIP_THIS_NUITKA=false
    if [[ "$SKIP_NUITKA" == true ]]; then
        if resolve_nuitka_app_bundle_path >/dev/null 2>&1 || resolve_nuitka_dist_path >/dev/null 2>&1; then
            SKIP_THIS_NUITKA=true
            log_warning "--skip-nuitka: reusing existing Nuitka output under $BACKEND_BUILD; skipping the launcher recompile."
        else
            log_error "--skip-nuitka requested but no existing AutoYou.dist/.app (or autoyou_app.*) found under $BACKEND_BUILD."
            log_error "Run one full build first, then --skip-nuitka can reuse it."
            exit 1
        fi
    fi

    if [[ "$SKIP_THIS_NUITKA" != true ]]; then
        ensure_nuitka_disk_space || exit 1

        # Ensure output directory exists. Remove previous output bundles (.app/.dist) to
        # avoid stale-state issues, but only preserve Nuitka .build/ caches that still
        # contain the generated shared headers required for incremental compilation.
        mkdir -p "$BACKEND_BUILD"
        rm -rf "$BACKEND_BUILD"/*.app "$BACKEND_BUILD"/*.dist "$BACKEND_BUILD"/AutoYou
        cleanup_incomplete_nuitka_build_cache
    fi

    local nuitka_args=(
        "--standalone"
        "--output-dir=$BACKEND_BUILD"
        "--output-filename=AutoYouServer"
        # Keep compiled file references runtime-relative instead of embedding checkout paths.
        "--file-reference-choice=runtime"
        "--assume-yes-for-downloads"
        # Preserve docstrings: upstream runtime libraries like SQLAlchemy and NumPy
        # still inspect their own docs during packaged bootstrap on macOS.
        "--noinclude-pytest-mode=nofollow"
        "--nofollow-import-to=tests"
        # Avoid shipping large scientific-library test suites. Do not exclude
        # every '*.tests' package: modules such as jinja2.tests are runtime code.
        "--nofollow-import-to=scipy.tests"
        "--nofollow-import-to=scipy.*.tests"
        "--nofollow-import-to=scipy.*.tests.*"
        "--nofollow-import-to=sklearn.tests"
        "--nofollow-import-to=sklearn.*.tests"
        "--nofollow-import-to=sklearn.*.tests.*"
        "--nofollow-import-to=__pycache__"
        "--nofollow-import-to=pytest"
        "--nofollow-import-to=vertexai"
        "--nofollow-import-to=agentplatform._genai"
        "--nofollow-import-to=agentplatform.agent_engines"
        "--nofollow-import-to=agentplatform.batch_prediction"
        "--nofollow-import-to=agentplatform.model_garden"
        "--nofollow-import-to=agentplatform.preview"
        "--nofollow-import-to=agentplatform.rag"
        "--nofollow-import-to=agentplatform.resources"
        "--nofollow-import-to=autoyou_agents.admin_agent.test_agent"
        "--nofollow-import-to=autoyou_agents.agent_builder_agent.test_handoff_tools"
        "--nofollow-import-to=autoyou_agents.coding_agent.test_workspace_tools"
        "--nofollow-import-to=autoyou_agents.frontend_proxy_agent.test_frontend_workflow"
        "--nofollow-import-to=autoyou_agents.internet_agent.test_internet_tool"
        "--nofollow-import-to=autoyou_agents.notes_agent.test_agent"
        "--nofollow-import-to=autoyou_agents.page_agent.test_agent"
        "--nofollow-import-to=autoyou_agents"
        "--nofollow-import-to=transformers"
        "--nofollow-import-to=torch"
        "--nofollow-import-to=sentence_transformers"
        "--nofollow-import-to=diffusers"
        "--nofollow-import-to=timm"
        "--nofollow-import-to=sympy"
        "--nofollow-import-to=cv2"
        "--nofollow-import-to=matplotlib"
        "--nofollow-import-to=jax"
        # Remote-desktop / desktop-automation support is loaded from the
        # packaged runtime overlay, not from the launcher graph. Keep the
        # PyObjC-backed packages out of this compile or Nuitka will reach
        # Quartz/Foundation and demand --mode=app.
        "--nofollow-import-to=mss"
        "--nofollow-import-to=pyautogui"
        "--nofollow-import-to=pygetwindow"
        "--nofollow-import-to=pymsgbox"
        "--nofollow-import-to=pyperclip"
        "--nofollow-import-to=pyscreeze"
        "--nofollow-import-to=pytweening"
        "--nofollow-import-to=pyrect"
        "--nofollow-import-to=mouseinfo"
        "--nofollow-import-to=bless"
        "--nofollow-import-to=bleak"
        "--nofollow-import-to=AppKit"
        "--nofollow-import-to=Cocoa"
        "--nofollow-import-to=CoreBluetooth"
        "--nofollow-import-to=Foundation"
        "--nofollow-import-to=libdispatch"
        "--nofollow-import-to=Quartz"
        "--nofollow-import-to=objc"
        "--nofollow-import-to=PyObjCTools"
        # The native Swift host owns the macOS tray lifecycle. Keep GUI-only
        # pystray/PyObjC modules out of the backend launcher graph or Nuitka
        # forces app-bundle mode as soon as Foundation/AppKit appear.
        "--nofollow-import-to=pystray"
        # macOS server builds use the native `say` path instead of pyttsx3's
        # NSSpeechSynthesizer driver, which also pulls Foundation/AppKit.
        "--nofollow-import-to=pyttsx3"
        # yt-dlp ships ~1,800 site-specific extractor submodules. Following it
        # makes Nuitka emit one giant C unit per extractor; combined with the
        # google.genai.types pydantic monster at -O3 this exhausts RAM on a
        # 16 GiB machine and clang gets OOM-killed (scons "Error 255"). Windows
        # already excludes it (global --nofollow-imports) and ships the wheel in
        # runtime_site_packages. macOS use is the single optional, guarded import
        # in autoyou_page_service._resolve_media_url (suppress(Exception) +
        # `if yt_dlp is None: return None`), so excluding it degrades gracefully
        # exactly like the torch/cv2 nofollows above rather than crashing.
        "--nofollow-import-to=yt_dlp"
        # Match the Windows standalone build: avoid anti-bloat analysis
        # interfering with optional cv2 dependency scans during compilation.
        "--disable-plugin=anti-bloat"
        # Transformers is explicitly nofollowed out of the launcher graph.
        # Nuitka 4.x still auto-detects its support plugin when the wheel is
        # installed and can spend large memory probing transformers/models.
        "--disable-plugin=transformers"
        # shared, autoyou_agents, and autoyou_lite are resolved from
        # runtime_modules on packaged builds. Keep the launcher stub minimal so
        # those app modules are not duplicated into the main executable.
    )

    if [[ "$GOOGLE_NUITKA_INCLUDE_MODE" == "runtime" ]]; then
        log "Using runtime-only AI provider overlay for this build"
        nuitka_args+=(
            "--nofollow-import-to=aiortc"
            "--nofollow-import-to=autoyou_page_service"
            "--nofollow-import-to=fastapi"
            "--nofollow-import-to=starlette"
            "--nofollow-import-to=pydantic"
            "--nofollow-import-to=uvicorn"
            "--nofollow-import-to=dotenv"
            "--nofollow-import-to=cryptography"
            "--nofollow-import-to=requests"
            "--nofollow-import-to=aiohttp"
            "--nofollow-import-to=bs4"
            "--nofollow-import-to=qrcode"
            "--nofollow-import-to=python_multipart"
            "--nofollow-import-to=multipart"
            "--nofollow-import-to=ollama"
            "--nofollow-import-to=httpx"
            "--nofollow-import-to=docker"
            "--nofollow-import-to=keyring"
            "--nofollow-import-to=sqlalchemy"
            "--nofollow-import-to=aiosqlite"
            "--nofollow-import-to=greenlet"
            "--nofollow-import-to=google"
            "--nofollow-import-to=litellm"
            "--nofollow-import-to=huggingface_hub"
        )
    else
        nuitka_args+=(
        "--include-package=aiortc"
        "--include-package=autoyou_page_service"
        # litellm uses internal provider discovery / dynamic imports for model routing.
        "--include-package=litellm"

        )

        if [[ "$GOOGLE_NUITKA_INCLUDE_MODE" == "broad" ]]; then
            # Default/Apple-compatible mode: include the google namespace to avoid
            # Nuitka namespace-package assertion failures seen in the shared build.
            nuitka_args+=("--include-package=google")
        else
            log "Using targeted Google Nuitka includes for this build"
        fi

        nuitka_args+=(
        "--include-package=google.adk"
        "--include-package=google.genai"
        # google-cloud-aiplatform 1.156.0 declares agentplatform as a related
        # top-level package. ADK 2.2 runtime checks need the distribution
        # metadata, so include only the top-level module to satisfy Nuitka's
        # metadata validation. The lazy agentplatform subpackages above are
        # nofollowed because they are unused by the packaged local/Ollama
        # server path and include very large generated pydantic modules.
        "--include-module=agentplatform"
        "--include-package=huggingface_hub"
        "--include-distribution-metadata=google-adk"
        "--include-distribution-metadata=google-genai"
        "--include-distribution-metadata=google-cloud-aiplatform"
        "--include-distribution-metadata=litellm"
        "--include-package-data=google.adk.cli"
        "--include-package-data=litellm"
        )
    fi

    if [[ "$GOOGLE_NUITKA_INCLUDE_MODE" != "runtime" ]]; then
        nuitka_args+=(
        # ollama: local-LLM provider included in server-macos requirements.
        "--include-package=ollama"
        # httpx: used by openclaw_agent (query_openclaw) and TTS providers.
        "--include-package=httpx"
        # docker: Python Docker SDK used by whatsapp_docker_service (optional, guarded by try/except).
        "--include-package=docker"
        # keyring: required to unlock keystore-backed live config through
        # macOS Keychain when the packaged app starts from the native host.
        "--include-package=keyring"
        "--include-package=keyring.backends.macOS"
        # ADK DatabaseSessionService support; required so compiled main/worker
        # processes share saved reply-target/session state.
        "--include-package=sqlalchemy"
        "--include-package=aiosqlite"
        "--include-package=greenlet"
        "--include-distribution-metadata=fastapi"
        "--include-distribution-metadata=SQLAlchemy"
        "--include-distribution-metadata=ollama"
        "--include-distribution-metadata=httpx"
        "--include-distribution-metadata=docker"
        "--include-distribution-metadata=keyring"
        )
    fi

    nuitka_args+=(
        # Do NOT use --include-data-dir for Python packages - it exposes source
        # code. Agent/UI assets needed at runtime are bundled via runtime_modules.
        # ctranslate2: loads libctranslate2.*.dylib from its .dylibs/ subdir via ctypes.CDLL
        # at import time - the dylib is data, not a .so Nuitka auto-follows.
        # faster_whisper: VAD filter loads assets/silero_encoder_v5.onnx and
        # assets/silero_decoder_v5.onnx via os.path.join(get_assets_path(), ...).
        # RealtimeSTT: audio_recorder.py loads warmup_audio.wav via os.path.join(__file__, ...)
        # for STT engine warmup on first start.
        "--include-data-dir=$PROJECT_ROOT/assets=assets"
        "--include-data-dir=${RUNTIME_GUIDES_ROOT}=guides"
        "--include-data-dir=$PROJECT_ROOT/requirements=requirements"
        "--include-data-file=$PROJECT_ROOT/config/donations.example.json=config/donations.json"
        "--include-data-file=$PROJECT_ROOT/VERSION=VERSION"
        "--include-data-file=$PROJECT_ROOT/servers/windows/AutoYouWindowsHost/Assets/TrayLogo.png=assets/TrayLogo.png"
        "--include-data-file=$PROJECT_ROOT/assets/logo.png=assets/AppIcon.png"
        "--include-data-file=$PROJECT_ROOT/assets/logo.png=assets/AppLogo@1x.png"
        "--noinclude-data-files=config.encrypted"
        "--noinclude-data-files=config.encrypted.bak"
        "--noinclude-data-files=login_ui_state.db"
        "--noinclude-data-files=page_feed.db"
        "--noinclude-data-files=sessions.db"
        "--noinclude-data-files=sessions.db.bak"
        "--noinclude-data-files=autoyou_agents/.adk/*"
        "--noinclude-data-files=autoyou_agents/.adk/**/*"
        "--noinclude-data-files=autoyou_agents/notes_agent/*.db"
        "--noinclude-data-files=autoyou_agents/notes_agent/*.sqlite"
        "--noinclude-data-files=autoyou_agents/notes_agent/*.sqlite3"
        "--noinclude-data-files=autoyou_agents/notes_agent/media/*"
        "--noinclude-data-files=autoyou_agents/notes_agent/media/**/*"
        "--noinclude-data-files=autoyou_agents/notes_agent/autoyou_notes_agent/*"
        "--noinclude-data-files=autoyou_agents/notes_agent/autoyou_notes_agent/**/*"
        "--enable-plugin=dill-compat"
        "--enable-plugin=multiprocessing"
        # The compiled AI agent worker re-enters this binary via
        # `sys.executable --run-ai-agent-server ...`. Allow that packaged
        # self-execution path explicitly or Nuitka aborts the child process at
        # runtime before localhost:8081 becomes healthy.
        "--no-deployment-flag=self-execution"
        # Heavy ML packages such as torch are externalized into
        # runtime_site_packages. Allow those intentionally excluded modules to
        # import from the packaged runtime instead of aborting at import time.
        "--no-deployment-flag=excluded-module-usage"
        "--jobs=$NUITKA_JOBS"
    )

    if [[ "${AUTOYOU_BUILD_DESKTOP_V2:-0}" == "1" ]]; then
        nuitka_args+=("--include-package=pyaudio" "--include-package=numpy")
    fi

    if requirements_has_voice; then
    local skip_voice_flag="${AUTOYOU_MACOS_SKIP_VOICE_NUITKA_PACKAGES:-0}"
    local skip_voice_upper="$(printf '%s' "$skip_voice_flag" | tr '[:lower:]' '[:upper:]')"
    if [[ "${skip_voice_flag}" == "1" \
            || "${skip_voice_upper}" == "YES" \
            || "${skip_voice_upper}" == "TRUE" \
            || "${skip_voice_upper}" == "ON" \
            || "${skip_voice_upper}" == "Y" ]]; then
            log_warning "Skipping Nuitka voice/STT include-packages due AUTOYOU_MACOS_SKIP_VOICE_NUITKA_PACKAGES."
            nuitka_args+=(
                "--nofollow-import-to=RealtimeSTT"
                "--nofollow-import-to=faster_whisper"
                "--nofollow-import-to=ctranslate2"
                "--nofollow-import-to=sklearn"
                "--nofollow-import-to=scipy"
            )
        else
            nuitka_args+=(
                "--include-package=RealtimeSTT"
                "--include-package=faster_whisper"
                "--include-package=ctranslate2"
                "--include-package=scipy"
                "--include-distribution-metadata=realtimestt"
                "--include-distribution-metadata=faster-whisper"
                "--include-distribution-metadata=ctranslate2"
                "--include-distribution-metadata=scipy"
                "--include-package-data=ctranslate2"
                "--include-package-data=faster_whisper"
                "--include-package-data=RealtimeSTT"
            )
        fi
    else
        log "Skipping voice/STT Nuitka includes for requirements profile: $REQUIREMENTS_TYPE"
    fi
     
    # Add optimization level based on build type
    # NOTE: --static-libpython is intentionally NOT used on macOS.
    # Python.org / homebrew / pyenv installers place libpython at non-standard
    # paths that Nuitka cannot locate, causing:
    #   FATAL: Error, failed to find path '/install/lib/libpythonX.Y.dylib'
    # Using the shared dylib (--static-libpython=no) is the correct macOS approach
    # and avoids the path resolution failure entirely.
    nuitka_args+=("--static-libpython=no")
    # Mirror the Windows launcher (servers/windows/build-backend.ps1): --low-memory
    # tells Nuitka to cap its own C-compiler parallelism and use a less
    # RAM-hungry codegen path. Without it, compiling google.genai.types at -O3
    # alongside another job exceeds 16 GiB and macOS Jetsam kills clang
    # ("scons: *** Error 255"). This is the documented remedy for the
    # "Slow C compilation detected ... scalability problem" warning.
    nuitka_args+=("--low-memory")
    if [[ "$BUILD_TYPE" == "release" ]]; then
        # ADK 2.2 and the connector-full voice stack expand the launcher graph
        # enough that arm64 clang LTO can sit in a single >10k-object link for
        # close to an hour without producing a usable dist. Keep release builds
        # reliable by default and make LTO an explicit opt-in for release jobs
        # with enough headroom.
        case "${AUTOYOU_MACOS_RELEASE_LTO:-no}" in
            0|false|False|FALSE|no|No|NO|off|Off|OFF)
                nuitka_args+=("--lto=no")
                log "Release build with --lto=no (set AUTOYOU_MACOS_RELEASE_LTO=yes to opt into LTO)"
                ;;
            *)
                nuitka_args+=("--lto=yes")
                log "Release build with LTO optimizations"
                ;;
        esac
    else
        # LTO multiplies peak C-compiler memory. Windows always builds with
        # --lto=no; pin it explicitly for dev so Nuitka's default LTO mode
        # cannot silently re-enable thin-LTO and reintroduce the OOM.
        nuitka_args+=("--lto=no")
        log "Development build (fast compilation, --lto=no --low-memory)"
    fi
    
    log "Nuitka command: $PYTHON_CMD -m nuitka ${nuitka_args[*]} autoyou_app.py"
    log "Compiling Python backend to native binary (this may take 5-30 minutes)..."
    
    cd "$PROJECT_ROOT"

    scrub_appledouble_sidecars "${BUILD_DIR}/venv"
    scrub_appledouble_sidecars "$BACKEND_BUILD"

    if [[ "$SKIP_THIS_NUITKA" == true ]]; then
        log_warning "Skipping Nuitka launcher compilation (--skip-nuitka); reusing existing output and re-running post-compile asset steps only."
    # Show real-time compilation progress
    elif run_nuitka_with_retry "$NUITKA_JOBS" "${BUILD_DIR}/nuitka.log" "${nuitka_args[@]}"; then
        log_success "Nuitka compilation succeeded"
        log "Nuitka build completed with --jobs=${NUITKA_EFFECTIVE_JOBS:-$NUITKA_JOBS}"
    else
        log_error "Nuitka compilation failed"
        log "Last 30 lines of compilation log:"
        tail -30 "${BUILD_DIR}/nuitka.log"
        exit 1
    fi

    # Verify executable was created
    local app_path=""
    local dist_path=""
    local exe_path=""
    app_path=$(resolve_nuitka_app_bundle_path || true)
    dist_path=$(resolve_nuitka_dist_path || true)
    if [[ -n "$app_path" ]]; then
        exe_path="$app_path/Contents/MacOS/AutoYouServer"
    elif [[ -n "$dist_path" && -f "$dist_path/AutoYouServer" ]]; then
        exe_path="$dist_path/AutoYouServer"
    else
        log_error "Executable not created under $BACKEND_BUILD"
        exit 1
    fi

    # =========================================================================
    # Determine the Nuitka resources root inside the built bundle/dist folder.
    # For macOS app bundles, Nuitka places the compiled executable and bundled
    # data under Contents/MacOS, which is also the effective
    # __compiled__.containing_dir at runtime.
    # =========================================================================
    local resources_root=""
    if [[ -n "$app_path" ]]; then
        resources_root="$app_path/Contents/MacOS"
    elif [[ -n "$dist_path" ]]; then
        resources_root="$dist_path"
    else
        log_error "Cannot locate Nuitka resources root under $BACKEND_BUILD"
        exit 1
    fi

    build_runtime_modules_bundle "$resources_root"
    log "Building native macOS computer-sound capture..."
    mkdir -p "$resources_root/runtime/macos"
    MACOSX_DEPLOYMENT_TARGET=13.0 swiftc -parse-as-library -O \
        "$SCRIPT_DIR/native/AutoYouAudioCapture.swift" \
        -framework AVFoundation -framework CoreMedia -framework ScreenCaptureKit \
        -o "$resources_root/runtime/macos/AutoYouAudioCapture"
    log "Syncing current admin assets into the backend bundle..."
    mkdir -p "$resources_root/assets"
    rsync -a "$PROJECT_ROOT/assets/" "$resources_root/assets/"
    for admin_asset in admin-ui.js admin-ui.css; do
        if ! cmp -s "$PROJECT_ROOT/assets/$admin_asset" "$resources_root/assets/$admin_asset"; then
            log_error "Packaged admin asset is missing or stale: $resources_root/assets/$admin_asset"
            exit 1
        fi
    done
    install_runtime_stdlib_overlay "$resources_root"
    install_runtime_site_packages_overlay "$resources_root"
    if requirements_has_voice; then
        retarget_packaged_torchaudio_torch_libs "$resources_root"
    fi
    sync_runtime_adk_browser_bundle "$resources_root"

    # =========================================================================
    # Copy bundled Node.js binary into the runtime directory.
    # find_bundled_node_executable() (shared/macos_runtime_support.py) looks for:
    #   <resources_root>/runtime/node/bin/node
    # The ARTIFACTS_DIR/node/ directory was populated by download_and_bundle_nodejs().
    # =========================================================================
    log "Installing bundled Node.js runtime into app bundle..."
    local target_node_dir="$resources_root/runtime/node"
    mkdir -p "$target_node_dir"
    if [[ -d "$ARTIFACTS_DIR/node" ]]; then
        rsync -a --delete "$ARTIFACTS_DIR/node/" "$target_node_dir/"
        log_success "Node.js runtime installed: $target_node_dir"
    else
        log_warning "Node.js artifacts not found at $ARTIFACTS_DIR/node - skipping"
    fi

    # ── Verify bundled node binary is present ─────────────────────────────────
    local bundled_node="$target_node_dir/bin/node"
    if [[ -f "$bundled_node" ]]; then
        local node_ver
        node_ver=$("$bundled_node" --version 2>/dev/null || echo "unknown")
        log_success "Bundled Node.js verified: $bundled_node ($node_ver)"
    else
        log_warning "Bundled node binary NOT found at $bundled_node"
        log_warning "Node-dependent services (WhatsApp) will fall back to system PATH."
        log "Contents of $target_node_dir/bin:"
        ls -la "$target_node_dir/bin/" 2>/dev/null || echo "  (empty or does not exist)"
    fi

    # ── Verify node_modules for node services ─────────────────────────────────
    # Manual installation of Node services (whatsapp, tunnelmole) from source.
    # Nuitka's --include-data-dir is unreliable for 9000+ files and crashes on
    # broken symlinks during macOS signing.
    log "Bundling Node services manually (preventing Nuitka signing errors)..."
    local node_data_dir="$resources_root/node"
    mkdir -p "$node_data_dir"

    # Use rsync to copy the node directory.
    # --exclude='node_modules/.bin' prevents signing failures on broken symlinks.
    # --exclude='.wwebjs_auth' prevents bundling large local session caches.
    rsync -a \
        --exclude='node_modules/.bin' \
        --exclude='.wwebjs_auth' \
        --exclude='.wwebjs_cache' \
        "$PROJECT_ROOT/node/" "$node_data_dir/"

    for svc in whatsapp tunnelmole; do
        local nm_dir="$node_data_dir/$svc/node_modules"
        if [[ -d "$nm_dir" ]]; then
            log_success "node_modules bundled for service '$svc'"
        else
            log_warning "node_modules missing for service '$svc' at $nm_dir"
            log_warning "Run 'npm install' inside $PROJECT_ROOT/node/$svc and rebuild."
        fi
    done

    install_tunnelmole_runtime "$resources_root/runtime/tunnelmole"
    if requirements_has_voice || [[ "${AUTOYOU_BUILD_DESKTOP_V2:-0}" == "1" ]]; then
        install_whisper_cpp_runtime "$resources_root/runtime/whisper"
    else
        log "Skipping whisper.cpp runtime bundle for requirements profile: $REQUIREMENTS_TYPE"
    fi

    # ── Verify and vendor ADK browser assets ──────────────────────────────────
    verify_adk_browser_bundle "$resources_root"
    vendor_adk_browser_fonts "$resources_root"
    
    # ── Verify node services have modules ────────────────────────────────────
    verify_node_service_modules "$node_data_dir"

    # ── HARD GATE: a Node-less / incomplete bundle must fail the build ────────
    # When the build previously aborted at Nuitka's codesign step, this whole
    # post-compile block never ran and the dist shipped with no Node runtime -
    # which is exactly why the packaged WhatsApp service stayed "stopped" and
    # tunnelmole fell back to the public *.tunnelmole.net URL instead of the
    # self-hosted tm.autoyou.me path (both need bundled Node + node/<svc>).
    # Fail loudly here so a broken backend can never be silently shipped.
    local _gate_node="$resources_root/runtime/node/bin/node"
    local _gate_wa="$node_data_dir/whatsapp"
    local _gate_tm="$node_data_dir/tunnelmole"
    local _gate_failures=()
    [[ -x "$_gate_node" ]] || _gate_failures+=("bundled Node binary missing/not executable: $_gate_node")
    [[ -d "$_gate_wa/node_modules" ]] || _gate_failures+=("WhatsApp service node_modules missing: $_gate_wa/node_modules")
    [[ -d "$_gate_tm/node_modules" ]] || _gate_failures+=("tunnelmole service node_modules missing: $_gate_tm/node_modules")
    [[ -f "$_gate_wa/whatsapp_client.js" ]] || _gate_failures+=("WhatsApp client missing: $_gate_wa/whatsapp_client.js")
    [[ -f "$_gate_wa/wwebjs_recovery.js" ]] || _gate_failures+=("WhatsApp recovery guard missing: $_gate_wa/wwebjs_recovery.js")
    if [[ ${#_gate_failures[@]} -gt 0 ]]; then
        log_error "Backend bundle is incomplete - Node runtime/services did not get bundled:"
        local _gf
        for _gf in "${_gate_failures[@]}"; do
            log_error "  - $_gf"
        done
        log_error "Shipping this would break the packaged WhatsApp service and force tunnelmole onto the public *.tunnelmole.net URL. Failing the build."
        exit 1
    fi
    log_success "Node runtime + whatsapp/tunnelmole services verified present in bundle"

    # ── Verify voice/STT processing libraries ────────────────────────────────
    if requirements_has_voice; then
        verify_voice_processing_libs "$resources_root"
    else
        log "Skipping voice/STT verification for requirements profile: $REQUIREMENTS_TYPE"
    fi

    # =========================================================================
    # Copy bundled Playwright/Chromium into the runtime directory.
    # find_bundled_playwright_root() looks for:
    #   <resources_root>/runtime/playwright/
    # =========================================================================
    ensure_playwright_browser_artifacts

    log "Installing bundled Playwright/Chromium into app bundle..."
    local target_playwright_dir="$resources_root/runtime/playwright"
    mkdir -p "$target_playwright_dir"
    if [[ -d "$ARTIFACTS_DIR/chromium" ]] && ls "$ARTIFACTS_DIR/chromium"/chromium-* &>/dev/null; then
        rsync -a --delete "$ARTIFACTS_DIR/chromium/" "$target_playwright_dir/"
        log_success "Playwright/Chromium installed: $target_playwright_dir"
    else
        log_warning "Playwright/Chromium artifacts not found at $ARTIFACTS_DIR/chromium - skipping"
    fi
    # =========================================================================

    # NOTE: backend entrypoint modules now ship via runtime_modules plus the
    # runtime_integrity.json manifest, mirroring the hardened packaged layout
    # used on Windows. Mutable runtime state still lives in the user data
    # directory at runtime, never in the bundle.

    # Mirror the Windows pipeline's __pycache__/*.pyc sweep so any bytecode
    # accidentally produced between manifest generation and this point is
    # removed before the hardening verifier and the integrity manifest are
    # finalized. See build-backend.ps1 lines 1256-1257 for the parallel call.
    scrub_runtime_modules_bytecode "$resources_root"
    prune_noncommercial_release_assets "$resources_root"

    # Runtime overlays and install_name_tool retargeting can invalidate upstream
    # ad-hoc signatures even when the user asked for an unsigned development
    # build. Sign non-manifested Mach-O payloads before import verification so
    # macOS does not kill the packaged launcher with CODESIGNING/Invalid Page.
    adhoc_sign_packaged_runtime_dependencies "$resources_root"

    verify_packaged_backend_imports "$exe_path"
    verify_backend_hardening "$resources_root"

    chmod +x "$exe_path"
    log_success "Backend compiled: $exe_path"
}

# Codesign compiled binary
#
# macOS codesign --deep is DEPRECATED and generates a single command line
# containing every file path in the .app bundle.  With large Nuitka .app
# bundles this exceeds ARG_MAX (~260 KB) and fails with:
#   "command line was too long"
#
# The correct approach is to sign inside-out:
#   1. Sign every Mach-O binary (dylibs, frameworks, helpers) individually
#   2. Sign the outer .app bundle last (without --deep)
#
# Nuitka performs its own internal ad-hoc signing during bundle creation.
# We re-sign the resulting bundle inside-out here so the final artifact is
# consistently signed without relying on a single huge --deep invocation.
codesign_binaries() {
    log "Codesigning compiled binaries..."

    local app_path=""
    local dist_path=""
    app_path=$(resolve_nuitka_app_bundle_path || true)
    dist_path=$(resolve_nuitka_dist_path || true)
    local exe_path="$dist_path/AutoYouServer"

    if [[ -d "$app_path" ]]; then
        # ------------------------------------------------------------------ #
        # Step 1: Sign every Mach-O binary inside the bundle, bottom-up.
        # We use `file` to identify Mach-O objects so we don't rely on
        # extension names, and process them from deepest path first so that
        # nested frameworks are signed before their parent containers.
        # ------------------------------------------------------------------ #
        log "Signing Mach-O binaries inside $app_path (inside-out, no --deep)..."
        local sorted_paths
        sorted_paths="$(mktemp "${TMPDIR:-/tmp}/autoyou-codesign-app.XXXXXX")"
        # Compiled modules are already signed and covered by runtime_integrity.json.
        # Signing them again changes their bytes after the manifest was verified.
        local runtime_modules_root="$app_path/Contents/MacOS/runtime_modules"
        find "$app_path" \
            \( -path "$runtime_modules_root" -o -path "$runtime_modules_root/*" \) -prune \
            -o -type f -print0 | sort -rz > "$sorted_paths"
        while IFS= read -r -d '' binary; do
            # file(1) output for Mach-O starts with the path then ": Mach-O"
            if file -b "$binary" 2>/dev/null | grep -qE 'Mach-O|bundle'; then
                codesign -s - --force --preserve-metadata=entitlements "$binary" 2>&1 || \
                    log "  Warning: could not sign $binary (non-fatal)"
            fi
        done < "$sorted_paths"
        rm -f "$sorted_paths"

        # ------------------------------------------------------------------ #
        # Step 2: Sign the .app bundle itself (no --deep; contents already signed)
        # ------------------------------------------------------------------ #
        log "Signing .app bundle: $app_path"
        codesign -s - --force "$app_path" 2>&1 || {
            log_error "Failed to sign .app bundle: $app_path"
            return 1
        }
    elif [[ -d "$dist_path" ]]; then
        # Standalone .dist folder
        log "Signing binaries in standalone .dist folder: $dist_path"
        local sorted_paths
        sorted_paths="$(mktemp "${TMPDIR:-/tmp}/autoyou-codesign-dist.XXXXXX")"
        local runtime_modules_root="$dist_path/runtime_modules"
        find "$dist_path" \
            \( -path "$runtime_modules_root" -o -path "$runtime_modules_root/*" \) -prune \
            -o -type f -print0 | sort -rz > "$sorted_paths"
        while IFS= read -r -d '' binary; do
            if file -b "$binary" 2>/dev/null | grep -qE 'Mach-O|bundle'; then
                codesign -s - --force --preserve-metadata=entitlements "$binary" 2>&1 || \
                    log "  Warning: could not sign $binary (non-fatal)"
            fi
        done < "$sorted_paths"
        rm -f "$sorted_paths"
        
        # Sign the main executable specifically
        if [[ -f "$exe_path" ]]; then
            log "Signing main executable: $exe_path"
            codesign -s - --force "$exe_path" 2>&1 || {
                log_error "Failed to sign binary: $exe_path"
                return 1
            }
        fi
    elif [[ -f "$BACKEND_BUILD/AutoYouServer" ]]; then
        # Fallback for single binary
        codesign -s - --force "$BACKEND_BUILD/AutoYouServer" 2>&1 || {
            log_error "Failed to sign binary: $BACKEND_BUILD/AutoYouServer"
            return 1
        }
    else
        log_error "No executable found to sign at $app_path, $dist_path, or $BACKEND_BUILD/AutoYouServer"
        return 1
    fi

    # Sign Node.js if present (it lives outside the .app bundle)
    if [[ -f "${ARTIFACTS_DIR}/node/bin/node" ]]; then
        codesign -s - --force "${ARTIFACTS_DIR}/node/bin/node" 2>&1 || true
    elif [[ -d "${ARTIFACTS_DIR}/node" ]]; then
        codesign -s - --force "${ARTIFACTS_DIR}/node" 2>&1 || true
    fi

    log_success "Binaries codesigned"
}

# Normalise Nuitka output names so the user-facing backend bundle is named
# AutoYou.app / AutoYou.dist instead of Nuitka's autoyou_app.* default.
normalize_nuitka_output_name() {
    if [[ -d "$BACKEND_BUILD/autoyou_app.app" ]]; then
        rm -rf "$BACKEND_BUILD/AutoYou.app"
        log "Renaming autoyou_app.app → AutoYou.app"
        mv "$BACKEND_BUILD/autoyou_app.app" "$BACKEND_BUILD/AutoYou.app"
        log_success "Renamed to AutoYou.app"
    elif [[ -d "$BACKEND_BUILD/AutoYouServer.app" ]]; then
        rm -rf "$BACKEND_BUILD/AutoYou.app"
        log "Renaming AutoYouServer.app → AutoYou.app"
        mv "$BACKEND_BUILD/AutoYouServer.app" "$BACKEND_BUILD/AutoYou.app"
        log_success "Renamed to AutoYou.app"
    fi
    if [[ -d "$BACKEND_BUILD/autoyou_app.dist" ]]; then
        rm -rf "$BACKEND_BUILD/AutoYou.dist"
        log "Renaming autoyou_app.dist → AutoYou.dist"
        mv "$BACKEND_BUILD/autoyou_app.dist" "$BACKEND_BUILD/AutoYou.dist"
        log_success "Renamed to AutoYou.dist"
    elif [[ -d "$BACKEND_BUILD/AutoYouServer.dist" ]]; then
        rm -rf "$BACKEND_BUILD/AutoYou.dist"
        log "Renaming AutoYouServer.dist → AutoYou.dist"
        mv "$BACKEND_BUILD/AutoYouServer.dist" "$BACKEND_BUILD/AutoYou.dist"
        log_success "Renamed to AutoYou.dist"
    fi
}

normalize_backend_bundle_metadata() {
    local app_bundle=""
    app_bundle=$(resolve_nuitka_app_bundle_path || true)
    if [[ -z "$app_bundle" ]]; then
        return 0
    fi

    local info_plist="${app_bundle}/Contents/Info.plist"
    if [[ ! -f "$info_plist" ]]; then
        return 0
    fi

    local plistbuddy="/usr/libexec/PlistBuddy"
    if [[ -x "$plistbuddy" ]]; then
        "$plistbuddy" -c "Set :CFBundleName AutoYou" "$info_plist" >/dev/null 2>&1 || \
            "$plistbuddy" -c "Add :CFBundleName string AutoYou" "$info_plist" >/dev/null 2>&1 || true
        "$plistbuddy" -c "Set :CFBundleDisplayName AutoYou" "$info_plist" >/dev/null 2>&1 || \
            "$plistbuddy" -c "Add :CFBundleDisplayName string AutoYou" "$info_plist" >/dev/null 2>&1 || true
        "$plistbuddy" -c "Set :CFBundleIdentifier com.autoyou.backend" "$info_plist" >/dev/null 2>&1 || true
        "$plistbuddy" -c "Set :CFBundleShortVersionString $APP_VERSION" "$info_plist" >/dev/null 2>&1 || \
            "$plistbuddy" -c "Add :CFBundleShortVersionString string $APP_VERSION" "$info_plist" >/dev/null 2>&1 || true
        "$plistbuddy" -c "Set :CFBundleVersion $APP_VERSION" "$info_plist" >/dev/null 2>&1 || \
            "$plistbuddy" -c "Add :CFBundleVersion string $APP_VERSION" "$info_plist" >/dev/null 2>&1 || true
        log_success "Normalized backend bundle metadata: $(basename "$app_bundle") → AutoYou"
        return 0
    fi

    /usr/bin/plutil -replace CFBundleName -string "AutoYou" "$info_plist" 2>/dev/null || true
    /usr/bin/plutil -replace CFBundleDisplayName -string "AutoYou" "$info_plist" 2>/dev/null || true
    /usr/bin/plutil -replace CFBundleIdentifier -string "com.autoyou.backend" "$info_plist" 2>/dev/null || true
    /usr/bin/plutil -replace CFBundleShortVersionString -string "$APP_VERSION" "$info_plist" 2>/dev/null || true
    /usr/bin/plutil -replace CFBundleVersion -string "$APP_VERSION" "$info_plist" 2>/dev/null || true
    log_success "Normalized backend bundle metadata via plutil: $(basename "$app_bundle") → AutoYou"
}

# Bundle Python dependencies
bundle_python_deps() {
    log "Bundling Python dependencies..."
    
    local deps_dir="${ARTIFACTS_DIR}/python_deps"
    mkdir -p "$deps_dir"
    
    # Copy site-packages
    local site_packages=$(python3 -c "import site; print(site.getsitepackages()[0])")
    
    # Copy only essential packages (exclude test files, etc)
    if [[ -d "$site_packages" ]]; then
        rsync -av \
            --exclude="*.pyc" \
            --exclude="__pycache__" \
            --exclude="*.dist-info/tests" \
            --exclude="tests" \
            "$site_packages/*" "$deps_dir/" 2>&1 | grep -E "(copying|^sent)" || true
    fi
    
    log_success "Python dependencies bundled"
}

# Verify build artifacts
verify_artifacts() {
    log "Verifying build artifacts..."

    local missing=()

    # Accept the actual Nuitka output naming used on disk.
    if ! resolve_nuitka_app_bundle_path >/dev/null 2>&1 && ! resolve_nuitka_dist_path >/dev/null 2>&1; then
        missing+=("Backend binary (*.app or *.dist)")
    fi

    local backend_root=""
    if [[ -d "$BACKEND_BUILD/AutoYou.app" ]]; then
        backend_root="$BACKEND_BUILD/AutoYou.app/Contents/MacOS"
    elif [[ -d "$BACKEND_BUILD/AutoYouServer.app" ]]; then
        backend_root="$BACKEND_BUILD/AutoYouServer.app/Contents/MacOS"
    elif [[ -d "$BACKEND_BUILD/AutoYou.dist" ]]; then
        backend_root="$BACKEND_BUILD/AutoYou.dist"
    elif [[ -d "$BACKEND_BUILD/AutoYouServer.dist" ]]; then
        backend_root="$BACKEND_BUILD/AutoYouServer.dist"
    elif [[ -d "$BACKEND_BUILD/autoyou_app.app" ]]; then
        backend_root="$BACKEND_BUILD/autoyou_app.app/Contents/MacOS"
    elif [[ -d "$BACKEND_BUILD/autoyou_app.dist" ]]; then
        backend_root="$BACKEND_BUILD/autoyou_app.dist"
    fi

    if [[ -n "$backend_root" ]]; then
        [[ -f "$backend_root/runtime_integrity.json" ]] || missing+=("runtime_integrity.json")
        compgen -G "$backend_root/runtime_modules/server*.so" >/dev/null || missing+=("runtime_modules/server*.so")
        compgen -G "$backend_root/runtime_modules/shared/platform_runtime*.so" >/dev/null || missing+=("runtime_modules/shared/platform_runtime*.so")
        compgen -G "$backend_root/runtime_modules/shared/client_conversation_contract*.so" >/dev/null || missing+=("runtime_modules/shared/client_conversation_contract*.so")
        compgen -G "$backend_root/runtime_modules/shared/remote_desktop_input*.so" >/dev/null || missing+=("runtime_modules/shared/remote_desktop_input*.so")
        compgen -G "$backend_root/runtime_modules/shared/remote_desktop_settings*.so" >/dev/null || missing+=("runtime_modules/shared/remote_desktop_settings*.so")
        cmp -s "$PROJECT_ROOT/assets/admin-ui.js" "$backend_root/assets/admin-ui.js" || missing+=("current assets/admin-ui.js")
        cmp -s "$PROJECT_ROOT/assets/admin-ui.css" "$backend_root/assets/admin-ui.css" || missing+=("current assets/admin-ui.css")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/__init__.pyc" ]] || missing+=("runtime_modules/autoyou_agents/__init__.pyc")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/donation_agent/website/frontend/index.html" ]] || missing+=("donation_agent frontend/index.html")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/donation_agent/website/frontend/assets/app.js" ]] || missing+=("donation_agent frontend/assets/app.js")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/donation_agent/website/frontend/assets/styles.css" ]] || missing+=("donation_agent frontend/assets/styles.css")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/donation_agent/website/manifest.json" ]] || missing+=("donation_agent website/manifest.json")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/data_collector_agent/website/frontend/index.html" ]] || missing+=("data_collector_agent frontend/index.html")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/data_collector_agent/website/frontend/assets/app.js" ]] || missing+=("data_collector_agent frontend/assets/app.js")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/data_collector_agent/website/frontend/assets/styles.css" ]] || missing+=("data_collector_agent frontend/assets/styles.css")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/data_collector_agent/website/manifest.json" ]] || missing+=("data_collector_agent website/manifest.json")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/data_collector_agent/whatsapp_history_dump.mjs" ]] || missing+=("data_collector_agent whatsapp_history_dump.mjs")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/fine_tuning_agent/website/frontend/index.html" ]] || missing+=("fine_tuning_agent frontend/index.html")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/fine_tuning_agent/website/frontend/app.js" ]] || missing+=("fine_tuning_agent frontend/app.js")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/fine_tuning_agent/website/frontend/styles.css" ]] || missing+=("fine_tuning_agent frontend/styles.css")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/fine_tuning_agent/website/manifest.json" ]] || missing+=("fine_tuning_agent website/manifest.json")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/location_agent/website/frontend/index.html" ]] || missing+=("location_agent frontend/index.html")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/location_agent/website/frontend/assets/app.js" ]] || missing+=("location_agent frontend/assets/app.js")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/location_agent/website/frontend/assets/styles.css" ]] || missing+=("location_agent frontend/styles.css")
        [[ -f "$backend_root/runtime_modules/autoyou_agents/location_agent/website/manifest.json" ]] || missing+=("location_agent website/manifest.json")
        [[ -f "$backend_root/config/donations.json" ]] || missing+=("config/donations.json")
        if [[ "$REQUIREMENTS_TYPE" != "base" ]]; then
            [[ -d "$backend_root/runtime_site_packages" ]] || missing+=("runtime_site_packages")
        fi
        if requirements_has_voice; then
            compgen -G "$backend_root/runtime_modules/vendor/emotivoice/models/prompt_tts_modified/jets*.so" >/dev/null || missing+=("compiled EmotiVoice JETS module")
            compgen -G "$backend_root/runtime_modules/vendor/emotivoice/config/joint/config*.so" >/dev/null || missing+=("compiled EmotiVoice configuration module")
            [[ -f "$backend_root/runtime_modules/vendor/emotivoice/config/joint/config.yaml" ]] || missing+=("EmotiVoice config.yaml")
            [[ -f "$backend_root/runtime_modules/vendor/emotivoice/data/youdao/text/tokenlist" ]] || missing+=("EmotiVoice tokenlist")
            [[ -f "$backend_root/runtime_modules/vendor/emotivoice/data/youdao/text/speaker2" ]] || missing+=("EmotiVoice speaker data")
            [[ -f "$backend_root/runtime_modules/vendor/emotivoice/lexicon/librispeech-lexicon.txt" ]] || missing+=("EmotiVoice pronunciation lexicon")
            [[ -f "$backend_root/runtime_modules/vendor/emotivoice/LICENSE" ]] || missing+=("EmotiVoice license")
            if [[ ! -x "$backend_root/runtime/whisper/whisper-cli" && ! -x "$backend_root/runtime/whisper/whisper" && ! -x "$backend_root/runtime/whisper/main" ]]; then
                missing+=("runtime/whisper/{whisper-cli,whisper,main}")
            fi
        fi
    fi

    [[ -d "$ARTIFACTS_DIR/node/bin" ]] || missing+=("Node.js binaries")
    # Chromium is optional - internet-agent only.
    if [[ ! -d "$ARTIFACTS_DIR/chromium" ]]; then
        log_warning "Chromium not found at $ARTIFACTS_DIR/chromium - internet agent will use system Chrome"
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required artifacts: ${missing[*]}"
        return 1
    fi

    local backend_size
    backend_size=$(du -sh "$BACKEND_BUILD" | cut -f1)
    local node_size
    node_size=$(du -sh "$ARTIFACTS_DIR/node" 2>/dev/null | cut -f1 || echo "n/a")
    local chromium_size
    chromium_size=$(du -sh "$ARTIFACTS_DIR/chromium" 2>/dev/null | cut -f1 || echo "not bundled")

    log "Backend size:  $backend_size"
    log "Node.js size:  $node_size"
    log "Chromium size: $chromium_size"

    log_success "Build artifacts verified"
}

# Progress tracking
_STEP_NUM=0
_TOTAL_STEPS=14

step_header() {
    _STEP_NUM=$((_STEP_NUM + 1))
    log ""
    log "${BLUE}[STEP $_STEP_NUM/$_TOTAL_STEPS]${NC} $1"
    log "───────────────────────────────────┼"
}

# Main build flow
main() {
    parse_args "$@"
    
    if [[ "$CLEAN_ONLY" == true ]]; then
        log "Cleaning build directory..."
        rm -rf "$BUILD_DIR"
        log_success "Build directory cleaned"
        exit 0
    fi
    
    log ""
    log "${BLUE}╔════════════════════════════════════╗${NC}"
    log "${BLUE}║   AutoYou macOS Backend Build      ║${NC}"
    log "${BLUE}╚════════════════════════════════════╝${NC}"
    log ""
    log "Build type:        $BUILD_TYPE"
    log "Requirements:      $REQUIREMENTS_TYPE"
    log "Python:            $PYTHON_CMD"
    log "Parallel jobs:     $NUITKA_JOBS"
    log "Architecture:      $(uname -m)"
    log ""
    log_warning "Nuitka compilation is CPU-intensive and may take 30-120 minutes."
    log_warning "Run this in a long-lived terminal (tmux/screen) or with nohup to avoid disconnection."
    log ""
    
    local start_time=$(date +%s)

    run_official_build_authorization_gate
    run_strict_release_legal_gate
    acknowledge_private_build

    step_header "Ensure system dependencies (Homebrew)"
    ensure_system_deps

    step_header "Validate Python environment"
    validate_python
    
    step_header "Setup Python virtual environment"
    setup_venv

    if [[ "$SKIP_NUITKA" != true ]]; then
        prepare_backend_python_dependencies
    elif verify_backend_runtime_pins; then
        log_warning "--skip-nuitka: existing build venv already satisfies runtime pins; skipping pip install + native-dylib patches."
    else
        log_warning "--skip-nuitka: existing build venv is missing runtime pins; installing dependencies without recompiling the launcher."
        prepare_backend_python_dependencies
    fi

    step_header "Verify backend runtime pins"
    verify_backend_runtime_pins
    
    # Save current directory
    pushd "$PROJECT_ROOT" > /dev/null
    
    step_header "Download and bundle Node.js"
    download_and_bundle_nodejs
    
    step_header "Download Playwright browsers"
    download_playwright_browsers
    
    popd > /dev/null
    
    step_header "Install Node.js service dependencies"
    install_node_service_deps

    step_header "Compile Python backend with Nuitka"
    compile_python_backend
    normalize_nuitka_output_name
    normalize_backend_bundle_metadata

    if [[ "$SIGN_BINARIES" == true ]]; then
        step_header "Codesign binaries"
        codesign_binaries
    else
        log "Skipping codesigning (--no-sign flag used)"
    fi
    
    step_header "Bundle Python dependencies"
    bundle_python_deps
    
    step_header "Verify build artifacts"
    verify_artifacts
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    log ""
    log "${GREEN}╔════════════════════════════════════╗${NC}"
    log "${GREEN}║     Build Completed Successfully   ║${NC}"
    log "${GREEN}╚════════════════════════════════════╝${NC}"
    log ""
    log_success "Backend build completed in ${BLUE}$((duration / 60))m $((duration % 60))s${NC}"
}

main "$@"
