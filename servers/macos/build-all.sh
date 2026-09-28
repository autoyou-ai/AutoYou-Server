# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-534454206164647265737320-a855a2aff5e6ff9c6a2d8b29

#!/bin/bash
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.


################################################################################
# AutoYou macOS Build Orchestrator
# 
# This master build script handles the entire macOS application build pipeline.
# It orchestrates backend compilation, frontend build, code signing, and DMG creation.
#
# Usage:
#   ./build-all.sh [OPTIONS]
#
# Options:
#   --no-sign       Build without code signing (default for development)
#   --sign          Enable code signing with certificate
#   --dev           Fast development build with minimal optimization
#   --release       Production build with optimizations
#   --requirements TYPE  Package requirements: "binary-default" (default), "full", "training-full", "base", or "server-macos"
#   --release-profile PROFILE  Release profile: "binary-default" (default) or "connector-full"
#   --full          Use all connector features including voice/audio (same as --requirements full --release-profile connector-full)
#   --include-cognee Include optional Cognee memory backend in the packaged runtime
#   --python PYTHON Python executable to pass to build-backend.sh
#   --jobs N        Nuitka parallel compilation jobs to pass to build-backend.sh
#   --certificate-name NAME   Specify certificate (default: "Developer ID Application")
#   --team-id ID    Apple Team ID for signing (required for --sign)
#   --notarize      Submit for Apple notarization after build
#   --skip-backend  Skip backend build entirely; reuse build/backend (frontend + sign only)
#   --skip-backend-compile  Reuse the compiled Nuitka launcher; re-run only the
#                   post-compile backend steps (Node/tunnelmole/whisper/rename/sign).
#                   Alias: --skip-nuitka. Avoids the ~3.5h launcher recompile.
#   --help          Show this help message
#
# Examples:
#   ./build-all.sh --no-sign --dev              # Fast unsigned build
#   ./build-all.sh --sign --release             # Production signed build
#   ./build-all.sh --release --notarize         # Default signed-binary profile
#   ./build-all.sh --full --release --notarize  # Full connector profile with notarization
#
################################################################################

set -euo pipefail

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${BUILD_DIR:-${AUTOYOU_MACOS_SERVER_BUILD_DIR:-${SCRIPT_DIR}/build}}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${AUTOYOU_MACOS_SERVER_ARTIFACTS_DIR:-${SCRIPT_DIR}/artifacts}}"
LOG_DIR="${BUILD_DIR}/logs"

# Default options
SIGN_APP=false
CERTIFICATE_NAME="Developer ID Application"
TEAM_ID=""
BUILD_TYPE="dev"
REQUIREMENTS_TYPE="binary-default"
RELEASE_PROFILE="binary-default"
INCLUDE_COGNEE=false
NOTARIZE_ENABLED=false
VERBOSE=false
NUITKA_JOBS=""
BACKEND_PYTHON=""
# --skip-backend: skip the backend build entirely (frontend host + sign only),
#   reusing the existing servers/macos/build/backend output. Windows parity:
#   build-all.ps1 -SkipBackend.
# --skip-backend-compile: still run the backend pipeline but reuse the existing
#   Nuitka launcher (passes --skip-nuitka to build-backend.sh), so post-compile
#   fixes (Node/tunnelmole/whisper/rename/sign) re-apply WITHOUT the ~3.5h
#   launcher recompile.
# Either flag preserves $BUILD_DIR (clean_build must not wipe the reusable output).
SKIP_BACKEND=false
SKIP_BACKEND_COMPILE=false

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --no-sign)
                SIGN_APP=false
                shift
                ;;
            --sign)
                SIGN_APP=true
                shift
                ;;
            --dev)
                BUILD_TYPE="dev"
                shift
                ;;
            --release)
                BUILD_TYPE="release"
                shift
                ;;
            --full)
                REQUIREMENTS_TYPE="full"
                RELEASE_PROFILE="connector-full"
                shift
                ;;
            --include-cognee)
                INCLUDE_COGNEE=true
                shift
                ;;
            --requirements)
                REQUIREMENTS_TYPE="$2"
                if [[ "$REQUIREMENTS_TYPE" == "full" || "$REQUIREMENTS_TYPE" == "source-full" || "$REQUIREMENTS_TYPE" == "connector-full" || "$REQUIREMENTS_TYPE" == "training-full" ]]; then
                    RELEASE_PROFILE="connector-full"
                fi
                shift 2
                ;;
            --release-profile)
                RELEASE_PROFILE="$2"
                shift 2
                ;;
            --certificate-name)
                CERTIFICATE_NAME="$2"
                shift 2
                ;;
            --team-id)
                TEAM_ID="$2"
                shift 2
                ;;
            --notarize)
                NOTARIZE_ENABLED=true
                shift
                ;;
            --skip-backend)
                SKIP_BACKEND=true
                shift
                ;;
            --skip-backend-compile|--skip-nuitka)
                SKIP_BACKEND_COMPILE=true
                shift
                ;;
            --jobs)
                NUITKA_JOBS="$2"
                shift 2
                ;;
            --python)
                BACKEND_PYTHON="$2"
                shift 2
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            --help)
                print_help
                exit 0
                ;;
            *)
                echo -e "${RED}Unknown option: $1${NC}"
                print_help
                exit 1
                ;;
        esac
    done

    case "$RELEASE_PROFILE" in
        binary-default|connector-full)
            ;;
        *)
            echo -e "${RED}Unsupported release profile: $RELEASE_PROFILE${NC}"
            echo -e "${RED}Choose binary-default or connector-full.${NC}"
            exit 1
            ;;
    esac

    if [[ -n "$NUITKA_JOBS" ]]; then
        if ! [[ "$NUITKA_JOBS" =~ ^[0-9]+$ ]] || [[ "$NUITKA_JOBS" -lt 1 ]]; then
            echo -e "${RED}Unsupported --jobs value: $NUITKA_JOBS${NC}"
            echo -e "${RED}Choose a positive integer.${NC}"
            exit 1
        fi
    fi
}

print_help() {
    head -n 34 "$0" | tail -n 32
}

# Logging functions
log() {
    echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERR]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

remove_tree_with_retry() {
    local target="$1"
    [[ -e "$target" ]] || return 0

    local attempt
    for attempt in 1 2 3; do
        xattr -dr com.apple.provenance "$target" 2>/dev/null || true
        xattr -dr com.apple.quarantine "$target" 2>/dev/null || true
        chmod -R u+w "$target" 2>/dev/null || true
        rm -rf "$target" 2>/dev/null || true
        [[ ! -e "$target" ]] && return 0
        sleep 1
    done

    log_error "Failed to remove build path after retries: $target"
    return 1
}

# Validate prerequisites
validate_prerequisites() {
    log "Validating build prerequisites..."
    
    local missing_tools=()
    
    # Check for required tools
    command -v xcodebuild &> /dev/null || missing_tools+=("xcodebuild")
    command -v swift &> /dev/null || missing_tools+=("swift")
    command -v python3 &> /dev/null || missing_tools+=("python3")
    command -v node &> /dev/null || missing_tools+=("node")
    command -v npm &> /dev/null || missing_tools+=("npm")
    
    if [[ ${#missing_tools[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing_tools[*]}"
        echo "Please install the missing tools and try again."
        exit 1
    fi
    
    # Check for signing certificate if signing is enabled
    if [[ "$SIGN_APP" == true ]]; then
        if [[ -z "$TEAM_ID" ]]; then
            log_error "Team ID required when signing. Use --team-id <ID>"
            exit 1
        fi
        
        security find-identity -v -p codesigning | grep -q "$CERTIFICATE_NAME" || {
            log_error "Certificate not found: $CERTIFICATE_NAME"
            echo "Available certificates:"
            security find-identity -v -p codesigning | head -10
            exit 1
        }
    fi
    
    log_success "All prerequisites validated"
}

run_strict_release_legal_gate() {
    if [[ "$BUILD_TYPE" != "release" && "$NOTARIZE_ENABLED" != true ]]; then
        return 0
    fi

    log "Running strict release legal gate..."
    python3 "${PROJECT_ROOT}/scripts/check_release_legal_gates.py" --artifact-scope server --no-generate --strict-unknown-license || {
        log_error "Release legal gate failed. Resolve open blockers before macOS release packaging."
        exit 1
    }
}

release_artifact_profile() {
    if [[ "$RELEASE_PROFILE" == "connector-full" ]]; then
        printf '%s\n' "autoyou-server-macos-connector-full"
    else
        printf '%s\n' "autoyou-server-macos-default"
    fi
}

run_official_build_authorization_gate() {
    if [[ "$BUILD_TYPE" != "release" && "$NOTARIZE_ENABLED" != true ]]; then
        return 0
    fi

    log "Checking official build authorization..."
    python3 "${PROJECT_ROOT}/scripts/check_official_build_authorization.py" \
        --required \
        --artifact-profile "$(release_artifact_profile)" || {
        log_error "Official build authorization failed. Complete the SignToROSS/OpenSign build-access agreement before macOS release packaging."
        exit 1
    }
}

# Setup build directories
setup_directories() {
    log "Setting up build directories..."
    
    mkdir -p "$BUILD_DIR"
    mkdir -p "$ARTIFACTS_DIR"
    mkdir -p "$LOG_DIR"
    
    log_success "Build directories ready"
}

# Clean previous build artifacts
clean_build() {
    # When reusing the backend (--skip-backend / --skip-backend-compile) we must
    # NOT wipe $BUILD_DIR - that is where the compiled backend (and the cached
    # node/playwright artifacts the post-compile steps rsync from) lives. Only
    # the Swift/SPM frontend cache is cleared so the host still rebuilds fresh.
    if [[ "$SKIP_BACKEND" == true || "$SKIP_BACKEND_COMPILE" == true ]]; then
        log "Reusing existing backend build (skip flag set) - preserving ${BUILD_DIR}"
        mkdir -p "$BUILD_DIR" "$ARTIFACTS_DIR" "$LOG_DIR"
        remove_tree_with_retry "${BUILD_DIR}/AutoYou.app"
        rm -f "${BUILD_DIR}/AutoYou.dmg" "${BUILD_DIR}/AutoYou.dmg.sha256"
        remove_tree_with_retry "${SCRIPT_DIR}/apple/.build"
        remove_tree_with_retry "${SCRIPT_DIR}/apple/build"
        log_success "Frontend/package artifacts cleaned; backend preserved"
        return 0
    fi

    log "Cleaning previous build artifacts..."

    # npm installs some files read-only; chmod first so rm -rf can delete them.
    remove_tree_with_retry "$BUILD_DIR"
    remove_tree_with_retry "$ARTIFACTS_DIR"
    mkdir -p "$BUILD_DIR" "$ARTIFACTS_DIR" "$LOG_DIR"

    # Clean Swift/SPM build cache
    remove_tree_with_retry "${SCRIPT_DIR}/apple/.build"
    remove_tree_with_retry "${SCRIPT_DIR}/apple/build"

    log_success "Build artifacts cleaned"
}

# Reject stale compiled backends before either Apple or Intel packaging reuses them.
verify_reused_backend_freshness() {
    local backend_dir="${BUILD_DIR}/backend"
    local backend_root=""
    local candidate
    for candidate in \
        "${backend_dir}/AutoYou.dist" \
        "${backend_dir}/AutoYouServer.dist" \
        "${backend_dir}/autoyou_app.dist"; do
        if [[ -d "$candidate" ]]; then
            backend_root="$candidate"
            break
        fi
    done

    if [[ -z "$backend_root" ]]; then
        for candidate in \
            "${backend_dir}/AutoYou.app" \
            "${backend_dir}/AutoYouServer.app" \
            "${backend_dir}/autoyou_app.app"; do
            if [[ -d "$candidate" ]]; then
                backend_root="${candidate}/Contents/MacOS"
                break
            fi
        done
    fi

    if [[ -z "$backend_root" ]]; then
        log_error "--skip-backend found no reusable AutoYou app or dist under ${backend_dir}."
        return 1
    fi

    local missing=()
    [[ -x "${backend_root}/AutoYouServer" ]] || missing+=("executable AutoYouServer")
    compgen -G "${backend_root}/runtime_modules/shared/remote_desktop_input*.so" >/dev/null || missing+=("runtime_modules/shared/remote_desktop_input*.so")
    compgen -G "${backend_root}/runtime_modules/shared/remote_desktop_settings*.so" >/dev/null || missing+=("runtime_modules/shared/remote_desktop_settings*.so")
    cmp -s "${PROJECT_ROOT}/assets/admin-ui.js" "${backend_root}/assets/admin-ui.js" || missing+=("current assets/admin-ui.js")
    cmp -s "${PROJECT_ROOT}/assets/admin-ui.css" "${backend_root}/assets/admin-ui.css" || missing+=("current assets/admin-ui.css")
    if [[ "$REQUIREMENTS_TYPE" != "base" ]]; then
        [[ -d "${backend_root}/runtime_site_packages/mss" ]] || missing+=("runtime_site_packages/mss")
        [[ -d "${backend_root}/runtime_site_packages/pyautogui" ]] || missing+=("runtime_site_packages/pyautogui")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "--skip-backend rejected a stale backend: ${missing[*]}"
        return 1
    fi
    log_success "Reused backend passed Remote Desktop freshness checks"
}

# Build backend (Python + Node.js + Chromium bundling)
build_backend() {
    if [[ "$SKIP_BACKEND" == true ]]; then
        log_warning "Skipping backend build entirely (--skip-backend); reusing ${BUILD_DIR}/backend"
        if [[ ! -d "${BUILD_DIR}/backend" ]]; then
            log_error "--skip-backend set but ${BUILD_DIR}/backend does not exist. Run a full build once first."
            exit 1
        fi
        verify_reused_backend_freshness
        return 0
    fi

    log "Building backend..."

    local backend_log="${LOG_DIR}/backend.log"
    local backend_args=("--type" "$BUILD_TYPE" "--requirements" "$REQUIREMENTS_TYPE")

    [[ -n "$BACKEND_PYTHON" ]] && backend_args+=("--python" "$BACKEND_PYTHON")
    [[ -n "$NUITKA_JOBS" ]] && backend_args+=("--jobs" "$NUITKA_JOBS")
    [[ "$INCLUDE_COGNEE" == true ]] && backend_args+=("--include-cognee")

    # Skip signing in backend if not needed (defer to sign-and-compress for release builds)
    [[ "$SIGN_APP" == false ]] && backend_args+=("--no-sign")

    # Reuse the existing Nuitka launcher; only re-run the post-compile asset
    # pipeline (Node/tunnelmole/whisper/rename/sign). Saves the ~3.5h recompile.
    [[ "$SKIP_BACKEND_COMPILE" == true ]] && backend_args+=("--skip-nuitka")
    
    if [[ "$VERBOSE" == true ]]; then
        "${SCRIPT_DIR}/build-backend.sh" "${backend_args[@]}" 2>&1 | tee "$backend_log"
    else
        "${SCRIPT_DIR}/build-backend.sh" "${backend_args[@]}" > "$backend_log" 2>&1 || {
            log_error "Backend build failed. Check ${backend_log} for details"
            cat "$backend_log" | tail -20
            exit 1
        }
    fi
    
    log_success "Backend build completed"
}

# Build frontend (Swift + Xcode)
build_frontend() {
    log "Building frontend..."
    
    local frontend_log="${LOG_DIR}/frontend.log"
    
    if [[ "$VERBOSE" == true ]]; then
        "${SCRIPT_DIR}/build-frontend.sh" --build-dir "$BUILD_DIR" 2>&1 | tee "$frontend_log"
    else
        "${SCRIPT_DIR}/build-frontend.sh" --build-dir "$BUILD_DIR" > "$frontend_log" 2>&1 || {
            log_error "Frontend build failed. Check ${frontend_log} for details"
            cat "$frontend_log" | tail -20
            exit 1
        }
    fi
    
    log_success "Frontend build completed"
}

copy_release_legal_bundle() {
    log "Copying release legal bundle..."

    local app_bundle="${BUILD_DIR}/AutoYou.app"
    if [[ ! -d "$app_bundle" ]]; then
        log_error "Expected app bundle at $app_bundle before copying legal files"
        exit 1
    fi

    local artifact_id="autoyou-server-macos-default"
    if [[ "$RELEASE_PROFILE" == "connector-full" ]]; then
        artifact_id="autoyou-server-macos-connector-full"
    fi

    python3 "${PROJECT_ROOT}/scripts/copy_release_legal_artifacts.py" \
        --artifact "$artifact_id" \
        --target "${app_bundle}/Contents/Resources/Legal" \
        --generate

    local legal_dir="${app_bundle}/Contents/Resources/Legal"
    local required_legal_file
    for required_legal_file in LICENSE THIRD-PARTY-NOTICES.md NOTICE.txt sbom.cdx.json; do
        if [[ ! -f "${legal_dir}/${required_legal_file}" ]]; then
            log_error "Missing release legal file: ${legal_dir}/${required_legal_file}"
            exit 1
        fi
    done

    local commit="unknown"
    commit="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    python3 - "$app_bundle/Contents/Resources/release-profile.json" "$RELEASE_PROFILE" "$REQUIREMENTS_TYPE" "$artifact_id" "$commit" <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path

target, release_profile, dependency_profile, artifact_profile, commit = sys.argv[1:6]
metadata = {
    "releaseProfile": release_profile,
    "dependencyProfile": dependency_profile,
    "artifactProfile": artifact_profile,
    "builtAtUtc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    "commit": commit.strip() or "unknown",
    "legalBundle": "Contents/Resources/Legal",
}
Path(target).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
PY

    log_success "Release legal bundle copied"
}

# Sign and create DMG
sign_and_package() {
    log "Signing and packaging application..."
    
    local sign_log="${LOG_DIR}/sign.log"
    local sign_command=("${SCRIPT_DIR}/sign-and-compress.sh" --build-dir "$BUILD_DIR")

    if [[ "$SIGN_APP" == true ]]; then
        sign_command=(
            "${SCRIPT_DIR}/sign-and-compress.sh"
            --sign
            --certificate "$CERTIFICATE_NAME"
            --team-id "$TEAM_ID"
            --build-dir "$BUILD_DIR"
        )
    fi

    local artifact_profile
    artifact_profile="$(release_artifact_profile)"
    
    if [[ "$VERBOSE" == true ]]; then
        AUTOYOU_BUILD_ARTIFACT_PROFILE="$artifact_profile" "${sign_command[@]}" 2>&1 | tee "$sign_log"
    else
        AUTOYOU_BUILD_ARTIFACT_PROFILE="$artifact_profile" "${sign_command[@]}" > "$sign_log" 2>&1 || {
            log_error "Signing/packaging failed. Check ${sign_log} for details"
            cat "$sign_log" | tail -20
            exit 1
        }
    fi
    
    log_success "Application signed and packaged"
}

# Notarize application
notarize_app() {
    log "Submitting application for notarization..."
    
    local notarize_log="${LOG_DIR}/notarize.log"
    
    local dmg_path="${BUILD_DIR}/AutoYou.dmg"
    
    if [[ ! -f "$dmg_path" ]]; then
        log_error "DMG not found at $dmg_path"
        return 1
    fi
    
    local notarize_command=("${SCRIPT_DIR}/notarize.sh" "$dmg_path")
    [[ -n "$TEAM_ID" ]] && notarize_command+=(--team-id "$TEAM_ID")

    local artifact_profile
    artifact_profile="$(release_artifact_profile)"

    if [[ "$VERBOSE" == true ]]; then
        AUTOYOU_BUILD_ARTIFACT_PROFILE="$artifact_profile" "${notarize_command[@]}" 2>&1 | tee "$notarize_log"
    else
        AUTOYOU_BUILD_ARTIFACT_PROFILE="$artifact_profile" "${notarize_command[@]}" > "$notarize_log" 2>&1 || {
            log_error "Notarization failed. Check ${notarize_log} for details"
            return 1
        }
    fi
    
    log_success "Application notarized successfully"
}

# Print build summary
print_summary() {
    log "========================================="
    log "BUILD SUMMARY"
    log "========================================="
    
    local app_bundle="${BUILD_DIR}/AutoYou.app"
    if [[ -d "$app_bundle" ]]; then
        local size=$(du -sh "$app_bundle" | cut -f1)
        log "Application bundle: $app_bundle (${size})"
    fi
    
    local dmg_path="${BUILD_DIR}/AutoYou.dmg"
    if [[ -f "$dmg_path" ]]; then
        local size=$(du -sh "$dmg_path" | cut -f1)
        log "DMG installer: $dmg_path (${size})"
    fi
    
    log "Build type: $BUILD_TYPE"
    log "Code signing: $([ "$SIGN_APP" == true ] && echo "enabled" || echo "disabled")"
    
    if [[ "$NOTARIZE_ENABLED" == true && -f "$dmg_path" ]]; then
        log "Notarization: enabled"
    fi
    
    log "Logs: $LOG_DIR"
    log "========================================="
    
    log_success "Build completed successfully!"
}

# Progress tracking
_BUILD_STEP=0
_TOTAL_BUILD_STEPS=3

build_step_header() {
    _BUILD_STEP=$((_BUILD_STEP + 1))
    log ""
    log "${BLUE}[STEP $_BUILD_STEP/$_TOTAL_BUILD_STEPS]${NC} $1"
    log "---------------------------------------"
}

# Main build flow
main() {
    parse_args "$@"
    _TOTAL_BUILD_STEPS=$([ "$NOTARIZE_ENABLED" == true ] && echo 4 || echo 3)
    
    log ""
    log "========================================"
    log "AutoYou macOS Full Build"
    log "========================================"
    log ""
    log "Build type:        $BUILD_TYPE"
    log "Release profile:   $RELEASE_PROFILE"
    log "Requirements:      $REQUIREMENTS_TYPE"
    [[ -n "$BACKEND_PYTHON" ]] && log "Backend Python:    $BACKEND_PYTHON"
    [[ -n "$NUITKA_JOBS" ]] && log "Nuitka jobs:       $NUITKA_JOBS"
    log "Code signing:      $([ "$SIGN_APP" == true ] && echo "enabled" || echo "disabled")"
    log "Notarization:      $([ "$NOTARIZE_ENABLED" == true ] && echo "enabled" || echo "disabled")"
    log ""
    
    validate_prerequisites
    run_strict_release_legal_gate
    run_official_build_authorization_gate
    setup_directories
    clean_build
    
    local start_time=$(date +%s)
    
    build_step_header "Build backend (Python, dependencies, Node.js, Playwright)"
    build_backend
    
    build_step_header "Build frontend host (Swift menu bar app)"
    build_frontend
    copy_release_legal_bundle

    build_step_header "Sign and package application"
    sign_and_package
    
    if [[ "$NOTARIZE_ENABLED" == true ]]; then
        [[ "$SIGN_APP" == true ]] || {
            log_error "Notarization requires --sign enabled"
            exit 1
        }
        build_step_header "Submit for Apple notarization"
        notarize_app
    fi
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    local minutes=$((duration / 60))
    local seconds=$((duration % 60))
    
    log ""
    log "========================================"
    log "Build Completed Successfully"
    log "========================================"
    log ""
    log_success "Total build time: ${BLUE}${minutes}m ${seconds}s${NC}"
    print_summary
}

# Run main function
main "$@"
