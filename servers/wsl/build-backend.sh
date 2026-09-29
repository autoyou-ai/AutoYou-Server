# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-6010ff6c0cd3a0707baf1811

#!/usr/bin/env bash
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_ROOT="${SCRIPT_DIR}/build"
ARTIFACT_ROOT="${SCRIPT_DIR}/artifacts"
BACKEND_ARTIFACT_ROOT="${ARTIFACT_ROOT}/backend"
NUITKA_OUTPUT_ROOT="${BACKEND_ARTIFACT_ROOT}/nuitka"
FINAL_BACKEND_ROOT="${BACKEND_ARTIFACT_ROOT}/AutoYouServer"
RUNTIME_MODULE_BUILD_ROOT="${BUILD_ROOT}/runtime-modules"
RUNTIME_MODULE_BUILDER="${PROJECT_ROOT}/scripts/build_packaged_runtime_modules.py"
PACKAGED_GUIDES_SCRIPT="${PROJECT_ROOT}/scripts/prepare_packaged_guides.py"
RUNTIME_GUIDES_ROOT="${BUILD_ROOT}/runtime-guides"
HARDENING_VERIFIER="${PROJECT_ROOT}/scripts/verify_backend_hardening.py"
NONCOMMERCIAL_ASSET_PRUNER="${PROJECT_ROOT}/scripts/prune_noncommercial_release_assets.py"
RUNTIME_SITE_PACKAGES_PRUNER="${PROJECT_ROOT}/scripts/prune_runtime_site_packages_to_requirements.py"
REALTIMESTT_RUNTIME_INSTALLER="${PROJECT_ROOT}/scripts/install_realtimestt_runtime.py"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"
FULL_REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"
REALTIMESTT_RUNTIME_REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements/realtimestt-runtime.txt"
COGNEE_REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements/cognee.txt"
TUNING_REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements/tuning.txt"
LOCKED_CONSTRAINTS_SCRIPT="${PROJECT_ROOT}/scripts/gen_locked_constraints.py"

PYTHON_CMD="${PROJECT_ROOT}/.venv/bin/python"
DETECTED_CPUS="$(nproc 2>/dev/null || echo 2)"
DEFAULT_MAX_JOBS="${AUTOYOU_WSL_MAX_JOBS:-16}"
if [[ "$DETECTED_CPUS" =~ ^[0-9]+$ && "$DEFAULT_MAX_JOBS" =~ ^[0-9]+$ ]]; then
    if (( DETECTED_CPUS < DEFAULT_MAX_JOBS )); then
        NUITKA_JOBS="$DETECTED_CPUS"
    else
        NUITKA_JOBS="$DEFAULT_MAX_JOBS"
    fi
else
    NUITKA_JOBS=16
fi
CLEAN=false
INSTALL_BUILD_DEPS=false
INCLUDE_COGNEE=false
INCLUDE_TUNING=false
SKIP_VERIFY=false
UNOFFICIAL=false

include_cognee_enabled() {
    local env_value
    env_value="$(printf '%s' "${AUTOYOU_INCLUDE_COGNEE:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$INCLUDE_COGNEE" == true || "$env_value" =~ ^(1|true|yes|on)$ ]]
}

include_tuning_enabled() {
    local env_value
    env_value="$(printf '%s' "${AUTOYOU_INCLUDE_TUNING:-}" | tr '[:upper:]' '[:lower:]')"
    [[ "$INCLUDE_TUNING" == true || "$env_value" =~ ^(1|true|yes|on)$ ]]
}

resolve_locked_constraints() {
    local ignore_lock
    ignore_lock="$(printf '%s' "${AUTOYOU_IGNORE_LOCKFILE:-}" | tr '[:upper:]' '[:lower:]')"
    case "$ignore_lock" in
        1|true|yes|on) return 0 ;;
    esac
    if [[ -f "$LOCKED_CONSTRAINTS_SCRIPT" ]]; then
        "$PYTHON_CMD" "$LOCKED_CONSTRAINTS_SCRIPT" 2>/dev/null || true
    fi
}

# AutoYou does not enable RealtimeSTT wake-word mode. openWakeWord's pretrained
# model assets are CC-BY-NC-SA-4.0 and must never reach a release artifact.
prune_noncommercial_release_assets() {
    local root="$1"
    [[ -e "$root" ]] || return 0
    if [[ ! -f "$NONCOMMERCIAL_ASSET_PRUNER" ]]; then
        echo "Non-commercial asset pruner not found at $NONCOMMERCIAL_ASSET_PRUNER" >&2
        return 1
    fi
    "$PYTHON_CMD" "$NONCOMMERCIAL_ASSET_PRUNER" --prune --root "$root"
}

prune_noncommercial_site_packages() {
    local site_root
    while IFS= read -r site_root; do
        [[ -n "$site_root" ]] || continue
        prune_noncommercial_release_assets "$site_root"
    done < <("$PYTHON_CMD" - <<'PY'
import sysconfig

seen = set()
for key in ("purelib", "platlib"):
    value = sysconfig.get_path(key)
    if value and value not in seen:
        seen.add(value)
        print(value)
PY
)
}

usage() {
    cat <<'EOF'
Usage: ./servers/wsl/build-backend.sh [OPTIONS]

Options:
  --clean                 Remove previous WSL backend artifacts first.
  --python PATH           Python interpreter to use. Defaults to .venv/bin/python.
  --jobs N                Nuitka job count. Defaults to min(nproc, 16).
  --jobs max              Use all logical CPUs reported by nproc.
  --install-build-deps    Install WSL build and native desktop requirements.
  --include-cognee        Install optional Cognee memory backend before packaging.
  --include-tuning        Install the optional local Fine Tuning Agent stack.
  --skip-verify           Skip packaged import and hardening verification.
  --unofficial            Build a local development artifact without official release gates.
  --help                  Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)
            CLEAN=true
            shift
            ;;
        --python)
            PYTHON_CMD="$2"
            shift 2
            ;;
        --jobs)
            if [[ "$2" == "max" ]]; then
                NUITKA_JOBS="$DETECTED_CPUS"
            else
                NUITKA_JOBS="$2"
            fi
            shift 2
            ;;
        --install-build-deps)
            INSTALL_BUILD_DEPS=true
            shift
            ;;
        --include-cognee)
            INCLUDE_COGNEE=true
            shift
            ;;
        --include-tuning)
            INCLUDE_TUNING=true
            shift
            ;;
        --skip-verify)
            SKIP_VERIFY=true
            shift
            ;;
        --unofficial)
            UNOFFICIAL=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "The WSL backend build must run on Linux/WSL." >&2
    exit 1
fi

if [[ ! -x "$PYTHON_CMD" ]]; then
    if command -v "$PYTHON_CMD" >/dev/null 2>&1; then
        PYTHON_CMD="$(command -v "$PYTHON_CMD")"
    else
        echo "Python interpreter not found or not executable: $PYTHON_CMD" >&2
        exit 1
    fi
fi

if [[ "$UNOFFICIAL" == false ]]; then
    "$PYTHON_CMD" "${PROJECT_ROOT}/scripts/check_official_build_authorization.py" \
        --required \
        --artifact-profile autoyou-server-source-full || {
        echo "Official build authorization failed. Complete the SignToROSS/OpenSign build-access agreement before WSL server release packaging." >&2
        exit 1
    }
fi

echo "Pruning non-commercial model assets from ${PYTHON_CMD} site-packages..."
prune_noncommercial_site_packages

if [[ "$UNOFFICIAL" == false ]]; then
    "$PYTHON_CMD" "${PROJECT_ROOT}/scripts/check_release_legal_gates.py" --artifact-scope server --no-generate --strict-unknown-license || {
        echo "Release legal gate failed. Resolve open blockers before WSL server release packaging." >&2
        exit 1
    }
else
    echo "Building an unofficial local WSL backend; release authorization is required for official packaging."
fi

if [[ "$CLEAN" == true ]]; then
    rm -rf "$BUILD_ROOT" "$BACKEND_ARTIFACT_ROOT"
fi

mkdir -p "$BUILD_ROOT" "$BACKEND_ARTIFACT_ROOT" "$NUITKA_OUTPUT_ROOT"

if [[ ! -f "$PACKAGED_GUIDES_SCRIPT" ]]; then
    echo "Missing packaged guide staging helper: $PACKAGED_GUIDES_SCRIPT" >&2
    exit 1
fi
"$PYTHON_CMD" "$PACKAGED_GUIDES_SCRIPT" \
    --repo-root "$PROJECT_ROOT" \
    --output-root "$RUNTIME_GUIDES_ROOT"

LOCKED_CONSTRAINTS="$(resolve_locked_constraints)"

if [[ ! -f "$REALTIMESTT_RUNTIME_INSTALLER" || ! -f "$REALTIMESTT_RUNTIME_REQUIREMENTS_FILE" ]]; then
    echo "RealtimeSTT runtime installer or requirements pin is missing." >&2
    exit 1
fi
if include_tuning_enabled && [[ ! -f "$TUNING_REQUIREMENTS_FILE" ]]; then
    echo "AUTOYOU_INCLUDE_TUNING requested, but requirements/tuning.txt is missing." >&2
    exit 1
fi
"$PYTHON_CMD" "$REALTIMESTT_RUNTIME_INSTALLER"

if [[ "$INSTALL_BUILD_DEPS" == true ]]; then
    "$PYTHON_CMD" -m pip install --upgrade 'pip>=26.1.2,<27'
    pip_install_args=(-r "$REQUIREMENTS_FILE" -r "$FULL_REQUIREMENTS_FILE")
    if [[ -n "$LOCKED_CONSTRAINTS" && -f "$LOCKED_CONSTRAINTS" ]]; then
        pip_install_args+=(-c "$LOCKED_CONSTRAINTS")
    fi
    "$PYTHON_CMD" -m pip install "${pip_install_args[@]}"
fi

if include_tuning_enabled; then
    tuning_pip_args=(-r "$TUNING_REQUIREMENTS_FILE")
    if [[ -n "$LOCKED_CONSTRAINTS" && -f "$LOCKED_CONSTRAINTS" ]]; then
        tuning_pip_args+=(-c "$LOCKED_CONSTRAINTS")
    fi
    "$PYTHON_CMD" -m pip install "${tuning_pip_args[@]}"
fi

if include_cognee_enabled; then
    if [[ ! -f "$COGNEE_REQUIREMENTS_FILE" ]]; then
        echo "AUTOYOU_INCLUDE_COGNEE requested, but requirements/cognee.txt is missing." >&2
        exit 1
    fi
    cognee_pip_args=(-r "$COGNEE_REQUIREMENTS_FILE")
    if [[ -n "$LOCKED_CONSTRAINTS" && -f "$LOCKED_CONSTRAINTS" ]]; then
        cognee_pip_args+=(-c "$LOCKED_CONSTRAINTS")
    fi
    "$PYTHON_CMD" -m pip install "${cognee_pip_args[@]}"
fi

reconcile_args=("${PROJECT_ROOT}/scripts/reconcile_python_runtime_env.py")
if [[ -n "$LOCKED_CONSTRAINTS" && -f "$LOCKED_CONSTRAINTS" ]]; then
    reconcile_args+=(--constraints "$LOCKED_CONSTRAINTS")
fi
if include_tuning_enabled; then
    reconcile_args+=(--include-tuning)
fi
"$PYTHON_CMD" "${reconcile_args[@]}"

"$PYTHON_CMD" -m pip check

if ! "$PYTHON_CMD" -c "import importlib.util; assert importlib.util.find_spec('mss') and importlib.util.find_spec('pyautogui')"; then
    echo "Native remote desktop packaging requires requirements/desktop-automation.txt (mss and pyautogui)." >&2
    exit 1
fi
if include_tuning_enabled && ! "$PYTHON_CMD" -c "import torch, torchvision, transformers, datasets, peft, accelerate, safetensors, sentencepiece"; then
    echo "Fine Tuning packaging requires the trainer imports from requirements/tuning.txt." >&2
    exit 1
fi

if ! "$PYTHON_CMD" -c "import nuitka" >/dev/null 2>&1; then
    echo "Nuitka is not installed in $PYTHON_CMD. Install servers/wsl/requirements.txt or pass --install-build-deps." >&2
    exit 1
fi

if ! "$PYTHON_CMD" -c "import zstandard, ordered_set" >/dev/null 2>&1; then
    echo "zstandard and ordered-set are required for Nuitka builds. Install servers/wsl/requirements.txt." >&2
    exit 1
fi

install_node_service_deps() {
    local node_cmd npm_cmd
    node_cmd="$(command -v node || true)"
    npm_cmd="$(command -v npm || true)"
    if [[ -z "$node_cmd" || -z "$npm_cmd" ]]; then
        echo "Node.js 22.12.0+ and npm are required to package the WhatsApp bridge." >&2
        exit 1
    fi
    if ! "$node_cmd" -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)'; then
        echo "Node.js 22.12.0+ is required to package the WhatsApp bridge." >&2
        exit 1
    fi
    for svc in whatsapp tunnelmole; do
        local svc_dir="${PROJECT_ROOT}/node/${svc}"
        [[ -f "${svc_dir}/package-lock.json" ]] || {
            echo "Missing package-lock.json for node/${svc}." >&2
            exit 1
        }
        echo "Installing locked npm dependencies for ${svc}..."
        (cd "$svc_dir" && "$npm_cmd" ci --omit=dev)
    done
}

bundle_node_runtime() {
    local node_version="${AUTOYOU_WSL_NODE_VERSION:-22.22.3}"
    local node_arch node_tarball node_url node_cache node_archive node_checksums node_sha node_extract node_source node_bundle
    case "$(uname -m)" in
        x86_64|amd64) node_arch="x64" ;;
        aarch64|arm64) node_arch="arm64" ;;
        *) echo "Unsupported WSL/Linux architecture for bundled Node.js: $(uname -m)" >&2; exit 1 ;;
    esac
    node_tarball="node-v${node_version}-linux-${node_arch}.tar.xz"
    node_url="https://nodejs.org/dist/v${node_version}/${node_tarball}"
    node_cache="${BUILD_ROOT}/node-cache"
    node_archive="${node_cache}/${node_tarball}"
    node_checksums="${node_cache}/SHASUMS256.txt"
    node_bundle="${BUILD_ROOT}/node-runtime"
    mkdir -p "$node_cache"
    if [[ ! -f "$node_archive" ]]; then
        curl -fsSL "$node_url" -o "$node_archive"
    fi
    curl -fsSL "https://nodejs.org/dist/v${node_version}/SHASUMS256.txt" -o "$node_checksums"
    node_sha="$(awk -v name="$node_tarball" '$2 == name { print $1; exit }' "$node_checksums")"
    [[ -n "$node_sha" ]] || { echo "Node.js checksum missing for $node_tarball." >&2; exit 1; }
    printf '%s  %s\n' "$node_sha" "$node_archive" | sha256sum -c -
    node_extract="${node_cache}/extract"
    rm -rf "$node_extract" "$node_bundle"
    mkdir -p "$node_extract" "$node_bundle"
    tar -xJf "$node_archive" -C "$node_extract"
    node_source="${node_extract}/node-v${node_version}-linux-${node_arch}"
    [[ -x "${node_source}/bin/node" ]] || { echo "Node.js archive is missing bin/node." >&2; exit 1; }
    cp -a "${node_source}/bin" "${node_bundle}/"
    cp -a "${node_source}/lib" "${node_bundle}/"
    chmod +x "${node_bundle}/bin/node"
}

# Use the verified runtime for npm, including in the Bookworm Docker builder.
bundle_node_runtime
export PATH="${BUILD_ROOT}/node-runtime/bin:${PATH}"
install_node_service_deps

# Bundles Playwright's Chromium (internet_agent, and any auto-browser-adjacent
# tooling that shells out to a local browser) into runtime/playwright/, the
# same location shared/platform_runtime.py's find_bundled_playwright_root()
# already looks for on Linux (get_runtime_root(anchor) / "playwright") -- that
# runtime lookup has been in place for a while, but nothing populated it for
# WSL builds, unlike the macOS and Windows build scripts, which already bundle
# runtime/playwright the same way. Graceful no-op if playwright isn't
# installed in this build's venv (it is an optional requirements/internet.txt
# extra, not part of every profile).
bundle_playwright_chromium() {
    if ! "$PYTHON_CMD" -c "import playwright" >/dev/null 2>&1; then
        echo "Playwright not installed in this environment; skipping Chromium bundle."
        return 0
    fi

    local browsers_path="${AUTOYOU_WSL_PLAYWRIGHT_CACHE:-${HOME}/.cache/ms-playwright}"
    if ! ls -d "${browsers_path}"/chromium-* >/dev/null 2>&1; then
        echo "Downloading Playwright Chromium for bundling..."
        PLAYWRIGHT_BROWSERS_PATH="$browsers_path" "$PYTHON_CMD" -m playwright install chromium
    fi

    if ! ls -d "${browsers_path}"/chromium-* >/dev/null 2>&1; then
        echo "Playwright Chromium artifacts not found at ${browsers_path} after install attempt; skipping bundle." >&2
        return 0
    fi

    local target_playwright_dir="${FINAL_BACKEND_ROOT}/runtime/playwright"
    mkdir -p "$target_playwright_dir"
    local artifact_path
    for artifact_path in "${browsers_path}"/chromium-* "${browsers_path}"/chromium_headless_shell-* "${browsers_path}"/ffmpeg-*; do
        [[ -e "$artifact_path" ]] || continue
        cp -a "$artifact_path" "$target_playwright_dir/"
    done
}

PATCHELF_BIN="${AUTOYOU_PATCHELF:-}"
if [[ -z "$PATCHELF_BIN" && -x "${PROJECT_ROOT}/.venv/native/patchelf/bin/patchelf" ]]; then
    PATCHELF_BIN="${PROJECT_ROOT}/.venv/native/patchelf/bin/patchelf"
fi
if [[ -z "$PATCHELF_BIN" ]]; then
    PATCHELF_BIN="$(command -v patchelf || true)"
fi
if [[ -z "$PATCHELF_BIN" || ! -x "$PATCHELF_BIN" ]]; then
    echo "patchelf is required for Nuitka standalone builds. Install patchelf or set AUTOYOU_PATCHELF." >&2
    exit 1
fi

export PATH="$(dirname "$PATCHELF_BIN"):/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONDONTWRITEBYTECODE=1

echo "Using Python: $PYTHON_CMD"
echo "Using patchelf: $PATCHELF_BIN"
echo "Using Nuitka jobs: $NUITKA_JOBS"

rm -rf "$NUITKA_OUTPUT_ROOT" "$FINAL_BACKEND_ROOT"
mkdir -p "$NUITKA_OUTPUT_ROOT"

echo "Compiling AutoYou server launcher..."
"$PYTHON_CMD" "$PROJECT_ROOT/scripts/prepare_intent_router.py"
"$PYTHON_CMD" -m nuitka \
    --standalone \
    "--jobs=${NUITKA_JOBS}" \
    --lto=no \
    --low-memory \
    --assume-yes-for-downloads \
    --file-reference-choice=runtime \
    --enable-plugin=dill-compat \
    --enable-plugin=multiprocessing \
    --disable-plugin=anti-bloat \
    --nofollow-imports \
    --noinclude-pytest-mode=nofollow \
    --include-package=PIL \
    "--include-data-file=${PROJECT_ROOT}/config/donations.example.json=config/donations.json" \
    "--include-data-file=${PROJECT_ROOT}/VERSION=VERSION" \
    --output-filename=AutoYou \
    "--output-dir=${NUITKA_OUTPUT_ROOT}" \
    "${PROJECT_ROOT}/autoyou_app.py"

LAUNCHER_DIST=""
if [[ -d "${NUITKA_OUTPUT_ROOT}/autoyou_app.dist" ]]; then
    LAUNCHER_DIST="${NUITKA_OUTPUT_ROOT}/autoyou_app.dist"
elif [[ -d "${NUITKA_OUTPUT_ROOT}/AutoYouServer.dist" ]]; then
    LAUNCHER_DIST="${NUITKA_OUTPUT_ROOT}/AutoYouServer.dist"
else
    LAUNCHER_DIST="$(find "$NUITKA_OUTPUT_ROOT" -maxdepth 1 -type d -name '*.dist' | head -n 1 || true)"
fi

if [[ -z "$LAUNCHER_DIST" || ! -d "$LAUNCHER_DIST" ]]; then
    echo "Could not find Nuitka standalone dist under $NUITKA_OUTPUT_ROOT" >&2
    exit 1
fi

cp -a "$LAUNCHER_DIST" "$FINAL_BACKEND_ROOT"
if [[ "$UNOFFICIAL" == true ]]; then
    printf 'Unofficial local build; release authorization was not checked.\n' > "${FINAL_BACKEND_ROOT}/UNOFFICIAL_BUILD"
else
    rm -f "${FINAL_BACKEND_ROOT}/UNOFFICIAL_BUILD"
fi
if [[ -x "${FINAL_BACKEND_ROOT}/autoyou_app.bin" ]]; then
    mv "${FINAL_BACKEND_ROOT}/autoyou_app.bin" "${FINAL_BACKEND_ROOT}/AutoYou"
elif [[ -x "${FINAL_BACKEND_ROOT}/AutoYou.bin" ]]; then
    mv "${FINAL_BACKEND_ROOT}/AutoYou.bin" "${FINAL_BACKEND_ROOT}/AutoYou"
elif [[ ! -x "${FINAL_BACKEND_ROOT}/AutoYou" ]]; then
    echo "Could not find compiled launcher binary in $FINAL_BACKEND_ROOT" >&2
    exit 1
fi
chmod +x "${FINAL_BACKEND_ROOT}/AutoYou"

echo "Bundling verified Node.js runtime..."
mkdir -p "${FINAL_BACKEND_ROOT}/runtime/node"
cp -a "${BUILD_ROOT}/node-runtime/." "${FINAL_BACKEND_ROOT}/runtime/node/"

echo "Bundling Playwright Chromium (if available)..."
bundle_playwright_chromium

echo "Building compiled runtime modules..."
"$PYTHON_CMD" "$RUNTIME_MODULE_BUILDER" \
    --repo-root "$PROJECT_ROOT" \
    --bundle-root "$FINAL_BACKEND_ROOT" \
    --build-root "$RUNTIME_MODULE_BUILD_ROOT" \
    --jobs "$NUITKA_JOBS" \
    --nuitka-arg=--disable-plugin=transformers \
    --include-sibling-agents \
    --include-emotivoice

for native_module in remote_desktop_input remote_desktop_settings; do
    if ! compgen -G "${FINAL_BACKEND_ROOT}/runtime_modules/shared/${native_module}*.so" >/dev/null; then
        echo "Compiled native remote desktop module is missing: runtime_modules/shared/${native_module}*.so" >&2
        exit 1
    fi
done

for required_emotivoice_module in \
    "${FINAL_BACKEND_ROOT}/runtime_modules/vendor/emotivoice/frontend*.so" \
    "${FINAL_BACKEND_ROOT}/runtime_modules/vendor/emotivoice/models/prompt_tts_modified/jets*.so" \
    "${FINAL_BACKEND_ROOT}/runtime_modules/vendor/emotivoice/config/joint/config*.so"; do
    if ! compgen -G "$required_emotivoice_module" >/dev/null; then
        echo "Compiled EmotiVoice module is missing: $required_emotivoice_module" >&2
        exit 1
    fi
done
for required_emotivoice_asset in \
    "${FINAL_BACKEND_ROOT}/runtime_modules/vendor/emotivoice/data/youdao/text/tokenlist" \
    "${FINAL_BACKEND_ROOT}/runtime_modules/vendor/emotivoice/data/youdao/text/speaker2" \
    "${FINAL_BACKEND_ROOT}/runtime_modules/vendor/emotivoice/lexicon/librispeech-lexicon.txt" \
    "${FINAL_BACKEND_ROOT}/runtime_modules/vendor/emotivoice/LICENSE"; do
    if [[ ! -f "$required_emotivoice_asset" ]]; then
        echo "Packaged EmotiVoice runtime asset is missing: $required_emotivoice_asset" >&2
        exit 1
    fi
done

echo "Copying runtime support assets..."
for dir_name in assets requirements; do
    if [[ -d "${PROJECT_ROOT}/${dir_name}" ]]; then
        rm -rf "${FINAL_BACKEND_ROOT}/${dir_name}"
        cp -a "${PROJECT_ROOT}/${dir_name}" "${FINAL_BACKEND_ROOT}/${dir_name}"
    fi
done
rm -rf "${FINAL_BACKEND_ROOT}/guides"
cp -a "$RUNTIME_GUIDES_ROOT" "${FINAL_BACKEND_ROOT}/guides"
for admin_asset in admin-ui.js admin-ui.css; do
    if ! cmp -s "${PROJECT_ROOT}/assets/${admin_asset}" "${FINAL_BACKEND_ROOT}/assets/${admin_asset}"; then
        echo "Packaged admin asset is missing or stale: assets/${admin_asset}" >&2
        exit 1
    fi
done

if [[ -d "${PROJECT_ROOT}/node" ]]; then
    rm -rf "${FINAL_BACKEND_ROOT}/node"
    mkdir -p "${FINAL_BACKEND_ROOT}/node"
    tar \
        --exclude='node_modules/.bin' \
        --exclude='node/lib/node_modules/npm' \
        --exclude='.wwebjs_auth' \
        --exclude='.wwebjs_cache' \
        -C "$PROJECT_ROOT" \
        -cf - node \
        | tar -C "$FINAL_BACKEND_ROOT" -xf -
fi

for required_node_path in \
    "${FINAL_BACKEND_ROOT}/node/whatsapp/whatsapp_client.js" \
    "${FINAL_BACKEND_ROOT}/node/whatsapp/wwebjs_recovery.js" \
    "${FINAL_BACKEND_ROOT}/node/whatsapp/node_modules/whatsapp-web.js" \
    "${FINAL_BACKEND_ROOT}/runtime/node/bin/node"; do
    if [[ ! -e "$required_node_path" ]]; then
        echo "Packaged WhatsApp bridge is incomplete: missing $required_node_path" >&2
        exit 1
    fi
done

STDLIB_ROOT="$("$PYTHON_CMD" -c "import sysconfig; print(sysconfig.get_path('stdlib'))")"
if [[ -d "$STDLIB_ROOT" ]]; then
    rm -rf "${FINAL_BACKEND_ROOT}/runtime_stdlib"
    mkdir -p "${FINAL_BACKEND_ROOT}/runtime_stdlib"
    cp -a "${STDLIB_ROOT}/." "${FINAL_BACKEND_ROOT}/runtime_stdlib/"
    find "${FINAL_BACKEND_ROOT}/runtime_stdlib" \
        \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' -o -name 'test' -o -name 'tests' \) \
        -prune -exec rm -rf {} +
fi

SITE_PACKAGES_ROOT="$("$PYTHON_CMD" -c "import sysconfig; print(sysconfig.get_path('purelib'))")"
if [[ -d "$SITE_PACKAGES_ROOT" ]]; then
    rm -rf "${FINAL_BACKEND_ROOT}/runtime_site_packages"
    mkdir -p "${FINAL_BACKEND_ROOT}/runtime_site_packages"
    cp -a "${SITE_PACKAGES_ROOT}/." "${FINAL_BACKEND_ROOT}/runtime_site_packages/"
    find "${FINAL_BACKEND_ROOT}/runtime_site_packages" \
        \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' \) \
        -prune -exec rm -rf {} +
    runtime_prune_args=(
        "$RUNTIME_SITE_PACKAGES_PRUNER"
        --site-packages-root "${FINAL_BACKEND_ROOT}/runtime_site_packages"
        --requirements-file "$FULL_REQUIREMENTS_FILE"
        --requirements-file "$REALTIMESTT_RUNTIME_REQUIREMENTS_FILE"
    )
    if include_cognee_enabled; then
        runtime_prune_args+=(--requirements-file "$COGNEE_REQUIREMENTS_FILE")
    fi
    if include_tuning_enabled; then
        runtime_prune_args+=(--requirements-file "$TUNING_REQUIREMENTS_FILE")
    fi
    "$PYTHON_CMD" "${runtime_prune_args[@]}"
    prune_noncommercial_release_assets "${FINAL_BACKEND_ROOT}/runtime_site_packages"
fi
for desktop_package in mss pyautogui; do
    if [[ ! -d "${FINAL_BACKEND_ROOT}/runtime_site_packages/${desktop_package}" ]]; then
        echo "Packaged native remote desktop dependency is missing: runtime_site_packages/${desktop_package}" >&2
        exit 1
    fi
done

mkdir -p \
    "${FINAL_BACKEND_ROOT}/uploads" \
    "${FINAL_BACKEND_ROOT}/output" \
    "${FINAL_BACKEND_ROOT}/memory" \
    "${FINAL_BACKEND_ROOT}/signal_data" \
    "${FINAL_BACKEND_ROOT}/logs"

echo "Copying release legal bundle..."
"$PYTHON_CMD" "${PROJECT_ROOT}/scripts/copy_release_legal_artifacts.py" \
    --artifact autoyou-server-source-full \
    --target "${FINAL_BACKEND_ROOT}/Legal" \
    --generate

scrub_runtime_modules_bytecode() {
    local runtime_modules_root="${FINAL_BACKEND_ROOT}/runtime_modules"
    if [[ -d "$runtime_modules_root" ]]; then
        find "$runtime_modules_root" -type d -name '__pycache__' -prune -exec rm -rf {} +
        find "$runtime_modules_root" -type f \( -name '*.pyc' -o -name '*.pyo' \) \
            ! -path "$runtime_modules_root/autoyou_agents/__init__.pyc" -delete
    fi
}

if [[ ! -f "${FINAL_BACKEND_ROOT}/runtime_modules/autoyou_agents/__init__.pyc" ]]; then
    echo "Packaged agent bytecode bridge is missing from the WSL bundle." >&2
    exit 1
fi

scrub_build_path_markers() {
    local runtime_path_marker="${AUTOYOU_BUILD_RUNTIME_PATH_MARKER:-}"
    [[ -n "$runtime_path_marker" ]] || return 0

    echo "Scrubbing builder source path markers from the release bundle..."
    "$PYTHON_CMD" - "${FINAL_BACKEND_ROOT}" "${PROJECT_ROOT}" "${runtime_path_marker}" <<'PY'
import hashlib
import json
import mmap
import sys
from pathlib import Path

bundle_root = Path(sys.argv[1])
source_root = Path(sys.argv[2]).resolve()
replacement = sys.argv[3].encode("utf-8")

marker_texts = {
    str(source_root),
    source_root.as_posix(),
    str(source_root).replace("/", "\\"),
    str(source_root).replace("\\", "/"),
}
marker_bytes = []
for marker_text in sorted(marker_texts, key=len, reverse=True):
    marker = marker_text.encode("utf-8")
    if not marker or marker in marker_bytes:
        continue
    if len(marker) != len(replacement):
        raise SystemExit(
            f"Runtime path marker length mismatch: {marker_text!r} and {sys.argv[3]!r}."
        )
    marker_bytes.append(marker)

path_component_bytes = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"

def marker_has_path_boundaries(data, index, marker_length):
    before = data[index - 1] if index else None
    after_index = index + marker_length
    after = data[after_index] if after_index < len(data) else None
    return (
        (before is None or before not in path_component_bytes)
        and (after is None or after not in path_component_bytes)
    )

def contains_marker_at_path_boundary(data):
    for marker in marker_bytes:
        offset = 0
        while True:
            index = data.find(marker, offset)
            if index < 0:
                break
            if marker_has_path_boundaries(data, index, len(marker)):
                return True
            offset = index + 1
    return False

replaced_occurrences = 0
replaced_files = 0
for path in sorted(candidate for candidate in bundle_root.rglob("*") if candidate.is_file()):
    file_replaced = False
    try:
        with path.open("r+b") as handle:
            if handle.seek(0, 2) == 0:
                continue
            handle.seek(0)
            with mmap.mmap(handle.fileno(), 0) as mapped:
                for marker in marker_bytes:
                    offset = 0
                    while True:
                        index = mapped.find(marker, offset)
                        if index < 0:
                            break
                        if not marker_has_path_boundaries(mapped, index, len(marker)):
                            offset = index + 1
                            continue
                        mapped[index : index + len(marker)] = replacement
                        offset = index + len(marker)
                        replaced_occurrences += 1
                        file_replaced = True
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Could not scrub builder path marker from {path}: {exc}") from exc
    if file_replaced:
        replaced_files += 1

manifest_path = bundle_root / "runtime_integrity.json"
if manifest_path.is_file():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Rebuild the tracked file map from the files that actually survived
    # packaging and scrubbing. A stale entry can remain when an asset was
    # renamed or omitted between the runtime plan and the final bundle.
    runtime_modules_root = bundle_root / "runtime_modules"
    tracked_files = {}
    if runtime_modules_root.is_dir():
        for tracked_path in sorted(path for path in runtime_modules_root.rglob("*") if path.is_file()):
            relative_path = tracked_path.relative_to(bundle_root).as_posix()
            digest = hashlib.sha256()
            with tracked_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            tracked_files[relative_path] = digest.hexdigest()
    manifest["files"] = tracked_files
    # Serializing the manifest after the binary scrub can reintroduce a
    # builder path if a generated manifest field carried one. Apply the same
    # byte-level replacement to the final serialized bytes and assert that no
    # source marker remains in this release metadata file.
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    for marker in marker_bytes:
        offset = 0
        while True:
            index = manifest_bytes.find(marker, offset)
            if index < 0:
                break
            if not marker_has_path_boundaries(manifest_bytes, index, len(marker)):
                offset = index + 1
                continue
            manifest_bytes = manifest_bytes[:index] + replacement + manifest_bytes[index + len(marker):]
            offset = index + len(replacement)
    if contains_marker_at_path_boundary(manifest_bytes):
        raise SystemExit(f"Could not scrub builder path marker from {manifest_path}")
    manifest_path.write_bytes(manifest_bytes)

print(f"Builder path scrubbed: files={replaced_files}, occurrences={replaced_occurrences}")
PY
}
if [[ "$SKIP_VERIFY" != true ]]; then
    echo "Verifying packaged server imports..."
    (
        cd "${FINAL_BACKEND_ROOT}"
        ./AutoYou \
            --verify-server-imports \
            --verify-runtime-import keyring \
            --verify-runtime-import shared.keystore \
            --verify-runtime-import shared.ollama_gateway \
            --verify-runtime-import shared.odysseus_gateway \
            --verify-runtime-import shared.process_lifecycle \
            --verify-runtime-import shared.remote_desktop_input \
            --verify-runtime-import shared.remote_desktop_settings \
            --verify-runtime-import httpx \
            --verify-runtime-import mss \
            --verify-runtime-import telethon \
            --verify-runtime-import cryptg \
            --verify-runtime-import playwright.async_api \
            --verify-runtime-import auto_browser_client \
            --verify-runtime-import autoyou_agents.agent_builder_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.education_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.notify_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.skills_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.tasks_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.website_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.page_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.ads_watching_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.donation_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.data_collector_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.fine_tuning_agent.website.backend.app \
            --verify-runtime-import autoyou_agents.data_collector_agent.agent \
            --verify-runtime-import autoyou_agents.fine_tuning_agent.agent
    )

    if include_tuning_enabled; then
        (
            cd "${FINAL_BACKEND_ROOT}"
            ./AutoYou \
                --verify-runtime-import torch \
                --verify-runtime-import torchvision \
                --verify-runtime-import transformers \
                --verify-runtime-import datasets \
                --verify-runtime-import peft \
                --verify-runtime-import accelerate
        )
    fi

    DONATION_FRONTEND_ROOT="${FINAL_BACKEND_ROOT}/runtime_modules/autoyou_agents/donation_agent/website/frontend"
    for required_donation_asset in index.html assets/app.js assets/styles.css ../manifest.json; do
        if [[ ! -f "${DONATION_FRONTEND_ROOT}/${required_donation_asset}" ]]; then
            echo "Donation Agent frontend asset is missing from the WSL bundle: ${DONATION_FRONTEND_ROOT}/${required_donation_asset}" >&2
            exit 1
        fi
    done
    if [[ ! -f "${FINAL_BACKEND_ROOT}/config/donations.json" ]]; then
        echo "Donation configuration is missing from the WSL bundle: ${FINAL_BACKEND_ROOT}/config/donations.json" >&2
        exit 1
    fi

    DATA_COLLECTOR_FRONTEND_ROOT="${FINAL_BACKEND_ROOT}/runtime_modules/autoyou_agents/data_collector_agent/website/frontend"
    for required_collector_asset in index.html assets/app.js assets/styles.css ../manifest.json; do
        if [[ ! -f "${DATA_COLLECTOR_FRONTEND_ROOT}/${required_collector_asset}" ]]; then
            echo "Data Collector Agent frontend asset is missing from the WSL bundle: ${DATA_COLLECTOR_FRONTEND_ROOT}/${required_collector_asset}" >&2
            exit 1
        fi
    done
    if [[ ! -f "${FINAL_BACKEND_ROOT}/runtime_modules/autoyou_agents/data_collector_agent/whatsapp_history_dump.mjs" ]]; then
        echo "Data Collector Agent WhatsApp worker is missing from the WSL bundle." >&2
        exit 1
    fi

    FINE_TUNING_FRONTEND_ROOT="${FINAL_BACKEND_ROOT}/runtime_modules/autoyou_agents/fine_tuning_agent/website/frontend"
    for required_training_asset in index.html app.js styles.css ../manifest.json; do
        if [[ ! -f "${FINE_TUNING_FRONTEND_ROOT}/${required_training_asset}" ]]; then
            echo "Fine Tuning Agent frontend asset is missing from the WSL bundle: ${FINE_TUNING_FRONTEND_ROOT}/${required_training_asset}" >&2
            exit 1
        fi
    done

    LOCATION_FRONTEND_ROOT="${FINAL_BACKEND_ROOT}/runtime_modules/autoyou_agents/location_agent/website/frontend"
    for required_location_asset in index.html assets/app.js assets/styles.css ../manifest.json; do
        if [[ ! -f "${LOCATION_FRONTEND_ROOT}/${required_location_asset}" ]]; then
            echo "Location Agent frontend asset is missing from the WSL bundle: ${LOCATION_FRONTEND_ROOT}/${required_location_asset}" >&2
            exit 1
        fi
    done

    scrub_runtime_modules_bytecode
    scrub_build_path_markers

    echo "Verifying backend hardening..."
    mapfile -t ALLOWED_PYTHON_FILES < <(
        "$PYTHON_CMD" - "${FINAL_BACKEND_ROOT}/runtime_integrity.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for relative_path in manifest.get("allowed_python_files", []):
    print(relative_path)
PY
    )

    HARDENING_ARGS=(
        --bundle-root "$FINAL_BACKEND_ROOT"
        --repo-root "$PROJECT_ROOT"
        --allow-source-dir runtime_stdlib
        --allow-source-dir runtime_site_packages
        --skip-dir node
        --skip-dir runtime/node
        --skip-dir runtime/playwright
    )
    for allowed_file in "${ALLOWED_PYTHON_FILES[@]}"; do
        HARDENING_ARGS+=(--allow-source-file "$allowed_file")
    done
    "$PYTHON_CMD" "$HARDENING_VERIFIER" "${HARDENING_ARGS[@]}"
fi

echo "WSL server build complete."
echo "Output: ${FINAL_BACKEND_ROOT}/AutoYou"
