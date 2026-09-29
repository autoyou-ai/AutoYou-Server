# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-de444dfeeb6b2fade16e2ccc

#!/bin/bash
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.


################################################################################
# AutoYou macOS Signing and Packaging Script
#
# Handles code signing of the application and all bundled dependencies,
# then creates a distributable DMG installer with drag-and-drop installation
#
# Usage:
#   ./sign-and-compress.sh [OPTIONS]
#
# Options:
#   --sign              Enable code signing
#   --preflight-only    Validate signing credentials and exit (no build files touched)
#   --certificate NAME  Certificate name (default: "Developer ID Application")
#   --team-id ID        Apple Team ID (required for production signing)
#   --entitlements FILE Entitlements for Developer ID signing
#   --build-dir DIR     Build directory (default: ./build)
#   --help              Show this help message
#
################################################################################

set -euo pipefail

APP_BUNDLE_NAME="AutoYou.app"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
SIGN_ENABLED=false
PREFLIGHT_ONLY=false
SKIP_DMG=false
CERTIFICATE_NAME=""
TEAM_ID=""
ENTITLEMENTS_FILE="${SCRIPT_DIR}/apple/AutoYou.developer-id.entitlements"
TIMESTAMP_SIGNATURES=false
REQUIRED_LEGAL_FILES=(LICENSE THIRD-PARTY-NOTICES.md NOTICE.txt sbom.cdx.json)

# Logging
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

scrub_appledouble_sidecars() {
    local target_path="$1"

    [[ -e "$target_path" ]] || return 0
    find "$target_path" -name '._*' -delete
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --sign)
                SIGN_ENABLED=true
                shift
                ;;
            --preflight-only)
                PREFLIGHT_ONLY=true
                shift
                ;;
            --certificate)
                CERTIFICATE_NAME="$2"
                shift 2
                ;;
            --team-id)
                TEAM_ID="$2"
                shift 2
                ;;
            --entitlements)
                ENTITLEMENTS_FILE="$2"
                shift 2
                ;;
            --build-dir)
                BUILD_DIR="$2"
                shift 2
                ;;
            --help)
                head -n 20 "$0" | tail -n 18
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
    [[ "$SIGN_ENABLED" == true ]] || return 0
    if [[ "${AUTOYOU_SKIP_STRICT_RELEASE_LEGAL_GATE:-0}" == "1" ]]; then
        log_warning "Skipping strict release legal gate (AUTOYOU_SKIP_STRICT_RELEASE_LEGAL_GATE=1)."
        return 0
    fi

    log "Running strict release legal gate..."
    python3 "${PROJECT_ROOT}/scripts/check_release_legal_gates.py" --artifact-scope server --no-generate --strict-unknown-license || {
        log_error "Release legal gate failed. Resolve open blockers before macOS signed packaging."
        exit 1
    }
}

release_artifact_profile() {
    if [[ -n "${AUTOYOU_BUILD_ARTIFACT_PROFILE:-}" ]]; then
        printf '%s\n' "$AUTOYOU_BUILD_ARTIFACT_PROFILE"
        return 0
    fi

    local profile_path="${BUILD_DIR}/${APP_BUNDLE_NAME}/Contents/Resources/release-profile.json"
    if [[ -f "$profile_path" ]]; then
        python3 - "$profile_path" <<'PY'
import json
import sys
from pathlib import Path

profile = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(profile.get("artifactProfile") or "autoyou-server-macos-default")
PY
        return 0
    fi

    printf '%s\n' "autoyou-server-macos-default"
}

run_official_build_authorization_gate() {
    [[ "$SIGN_ENABLED" == true ]] || return 0

    log "Checking official build authorization..."
    python3 "${PROJECT_ROOT}/scripts/check_official_build_authorization.py" \
        --required \
        --artifact-profile "$(release_artifact_profile)" || {
        log_error "Official build authorization failed. Complete the SignToROSS/OpenSign build-access agreement before macOS signed packaging."
        exit 1
    }
}

assert_release_legal_bundle() {
    local app_path="$1"
    local legal_dir="${app_path}/Contents/Resources/Legal"

    if [[ ! -d "$legal_dir" ]]; then
        log_error "Missing release legal bundle: $legal_dir"
        return 1
    fi

    local required_legal_file
    for required_legal_file in "${REQUIRED_LEGAL_FILES[@]}"; do
        if [[ ! -f "${legal_dir}/${required_legal_file}" ]]; then
            log_error "Missing release legal file: ${legal_dir}/${required_legal_file}"
            return 1
        fi
    done

    log_success "Release legal bundle verified"
}

# Validate code signing certificate
validate_certificate() {
    log "Validating code signing certificate..."
    
    if [[ -z "$CERTIFICATE_NAME" ]]; then
        CERTIFICATE_NAME="Developer ID Application"
    fi
    
    # Find certificate in keychain
    local cert_id=$(security find-identity -v -p codesigning | \
        grep -m1 "$CERTIFICATE_NAME" | \
        awk '{print $2}')

    if [[ -z "$cert_id" ]]; then
        log_error "Certificate not found: $CERTIFICATE_NAME"
        echo "Available certificates:"
        security find-identity -v -p codesigning | head -10
        exit 1
    fi
    log "Found certificate: $cert_id ($CERTIFICATE_NAME)"
    if [[ "$CERTIFICATE_NAME" == *"Developer ID"* ]]; then
        TIMESTAMP_SIGNATURES=true
    fi

    # Set entitlements file
    export CODESIGN_IDENTITY="$cert_id"

    log_success "Certificate validated"
}

validate_entitlements_file() {
    [[ -n "$ENTITLEMENTS_FILE" ]] || return 0
    if [[ ! -f "$ENTITLEMENTS_FILE" ]]; then
        log_error "Entitlements file not found: $ENTITLEMENTS_FILE"
        exit 1
    fi
    plutil -lint "$ENTITLEMENTS_FILE" >/dev/null
    log "Using entitlements: $ENTITLEMENTS_FILE"
}

codesign_target() {
    local target_path="$1"
    local cert_identity="${2:--}"
    local use_entitlements="${3:-false}"
    local preserve_entitlements="${4:-false}"
    local args=(--sign "$cert_identity" --force)

    if [[ "$cert_identity" != "-" ]]; then
        args+=(--options runtime)
        [[ "$TIMESTAMP_SIGNATURES" == true ]] && args+=(--timestamp)
    fi

    if [[ "$use_entitlements" == true && -n "$ENTITLEMENTS_FILE" ]]; then
        args+=(--entitlements "$ENTITLEMENTS_FILE")
    elif [[ "$preserve_entitlements" == true ]]; then
        args+=(--preserve-metadata=entitlements)
    fi

    args+=("$target_path")
    codesign "${args[@]}"
}

sign_executables_with_entitlements() {
    local app_path="$1"
    local cert_identity="${2:--}"
    local candidate=""

    while IFS= read -r -d '' candidate; do
        if file -b "$candidate" 2>/dev/null | grep -qE 'Mach-O|bundle'; then
            codesign_target "$candidate" "$cert_identity" true false 2>&1 || return 1
        fi
    done < <(
        find "$app_path/Contents" -type f -perm -111 \
            \( -path "*/Contents/MacOS/*" -o -name "AutoYouServer" -o -path "*/runtime/node/bin/node" \) \
            -print0
    )
}

# Code sign nested binaries and frameworks without --deep.
codesign_recursive() {
    local app_path="$1"
    local cert_identity="${2:--}"  # Ad-hoc if not specified
    local strict="${3:-false}"
    local failures=0
    
    log "Recursively signing contents of $app_path (inside-out, no --deep)..."

    local sorted_files
    sorted_files="$(mktemp "${TMPDIR:-/tmp}/autoyou-sign-files.XXXXXX")"
    find "$app_path" -type f -print0 | sort -rz > "$sorted_files"
    while IFS= read -r -d '' binary; do
        if file -b "$binary" 2>/dev/null | grep -qE 'Mach-O|bundle'; then
            codesign_target "$binary" "$cert_identity" false true 2>&1 || {
                failures=$((failures + 1))
                [[ "$strict" == true ]] || true
            }
        fi
    done < "$sorted_files"
    rm -f "$sorted_files"

    local sorted_bundles
    sorted_bundles="$(mktemp "${TMPDIR:-/tmp}/autoyou-sign-bundles.XXXXXX")"
    find "$app_path" -type d \
        \( -name "*.framework" -o -name "*.app" -o -name "*.xpc" -o -name "*.appex" \) \
        -print0 | sort -rz > "$sorted_bundles"
    while IFS= read -r -d '' bundle; do
        [[ "$bundle" == "$app_path" ]] && continue
        local use_bundle_entitlements=false
        case "$bundle" in
            *.app|*.xpc|*.appex)
                use_bundle_entitlements=true
                ;;
        esac
        codesign_target "$bundle" "$cert_identity" "$use_bundle_entitlements" false 2>&1 || {
            failures=$((failures + 1))
            [[ "$strict" == true ]] || true
        }
    done < "$sorted_bundles"
    rm -f "$sorted_bundles"

    if [[ "$strict" == true && "$failures" -gt 0 ]]; then
        log_error "Failed to sign $failures nested code item(s)"
        return 1
    fi

    if ! sign_executables_with_entitlements "$app_path" "$cert_identity"; then
        if [[ "$strict" == true ]]; then
            log_error "Failed to sign executable payloads with entitlements"
            return 1
        fi
        log_warning "Could not add entitlements to every executable payload"
    fi

    codesign_target "$app_path" "$cert_identity" true false 2>&1 || {
        log_error "Failed to sign app bundle"
        return 1
    }
}

# Verify code signature
verify_signature() {
    local app_path="$1"
    
    log "Verifying code signature..."
    
    if ! codesign -v "$app_path" 2>&1; then
        log_error "Signature verification failed"
        return 1
    fi
    
	codesign -d -v "$app_path" 2>&1 | sed -n '1,5p' || true
    
    log_success "Signature verified"
}

refresh_runtime_integrity_manifests() {
    local app_path="$1"
    local manifest_count=0

    log "Refreshing packaged runtime integrity manifests after signing..."

    while IFS= read -r -d '' manifest_path; do
        python3 - "$manifest_path" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
bundle_root = manifest_path.parent
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
tracked_files = manifest.get("files")
if not isinstance(tracked_files, dict):
    raise SystemExit(f"{manifest_path}: missing file hash table")

updated_files = {}
missing_files = []

for relative_path in sorted(tracked_files):
    cleaned = str(relative_path).replace("\\", "/").strip("/")
    candidate = bundle_root / cleaned
    if not candidate.is_file():
        missing_files.append(cleaned)
        continue

    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    updated_files[cleaned] = digest.hexdigest()

if missing_files:
    raise SystemExit(
        f"{manifest_path}: tracked runtime files are missing: "
        + ", ".join(missing_files[:10])
    )

manifest["files"] = updated_files
manifest_path.write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"Updated {manifest_path} ({len(updated_files)} files)")
PY
        manifest_count=$((manifest_count + 1))
    done < <(find "$app_path" -type f -name "runtime_integrity.json" -print0)

    if (( manifest_count == 0 )); then
        log_warning "No runtime_integrity.json manifests found under $app_path"
    else
        log_success "Runtime integrity manifests refreshed"
    fi
}

resign_outer_app_bundle() {
    local app_path="$1"
    local cert_identity="${2:--}"

    log "Re-signing outer app bundle after manifest refresh..."
    codesign_target "$app_path" "$cert_identity" true false 2>&1 || {
        log_error "Failed to re-sign app bundle after manifest refresh"
        return 1
    }
}

# ZIP fallback when there is not enough disk for the full DMG pipeline.
# ditto -c -k streams directly to the output file - no temp copy needed.
_create_zip_fallback() {
    local app_bundle="$1"
    local zip_path="${BUILD_DIR}/AutoYou.zip"
    rm -f "$zip_path"

    # Check if there's enough room for the zip (~75% of app size for binaries).
    local app_size_mb free_mb zip_est_mb
    app_size_mb=$(du -sm "$app_bundle" | awk '{print $1}')
    free_mb=$(df -m "$BUILD_DIR" | tail -1 | awk '{print $4}')
    zip_est_mb=$(( app_size_mb * 85 / 100 ))
    if (( free_mb < zip_est_mb )); then
        log_warning "Also not enough disk for a ZIP (${free_mb} MB free, ~${zip_est_mb} MB needed)."
        log_warning "Skipping packaging. The signed app is ready at:"
        log_warning "  ${app_bundle}"
        log_warning "Open it directly or free disk and re-run sign-and-compress.sh."
        return 0
    fi

    log "Creating ZIP archive of ${APP_BUNDLE_NAME}..."
    ditto -c -k --keepParent "$app_bundle" "$zip_path" || {
        log_warning "ZIP creation failed (likely disk space). Signed app is at:"
        log_warning "  ${app_bundle}"
        return 0
    }
    log "ZIP created: $zip_path ($(du -sh "$zip_path" | cut -f1))"
    log_success "ZIP archive created - open build/AutoYou.app directly to test, or share the ZIP"
}

# Create DMG installer
create_dmg() {
    log "Creating DMG installer..."

    local app_bundle="${BUILD_DIR}/${APP_BUNDLE_NAME}"
    local dmg_path="${BUILD_DIR}/AutoYou.dmg"
    # Use PID-unique name so a stuck old temp file never blocks a new run.
    local temp_dmg="${BUILD_DIR}/AutoYou-temp-$$.dmg"
    local attached_device=""

    if [[ ! -d "$app_bundle" ]]; then
        log_error "App bundle not found: $app_bundle"
        return 1
    fi

    # Clean up any leftover temp DMGs from previous failed runs.
    for old_tmp in "${BUILD_DIR}"/AutoYou-temp*.dmg; do
        [[ -f "$old_tmp" ]] || continue
        hdiutil detach "$old_tmp" 2>/dev/null || true
        rm -f "$old_tmp" 2>/dev/null || true
    done
    rm -f "$dmg_path"

    # Cleanup helper - called on both success and failure paths.
    _dmg_cleanup() {
        [[ -n "$attached_device" ]] && hdiutil detach "$attached_device" 2>/dev/null || true
        rm -f "$temp_dmg" 2>/dev/null || true
    }

    # Compute DMG size with adequate HFS+ overhead.
    # The app bundle contains thousands of small signed Python/Node files; HFS+
    # can need substantially more room than the APFS source tree reports.
    local app_size_mb
    app_size_mb=$(du -sm "$app_bundle" | awk '{print $1}')
    local dmg_overhead=$(( app_size_mb * 100 / 100 ))
    [[ $dmg_overhead -lt 2048 ]] && dmg_overhead=2048
    local dmg_size=$(( app_size_mb + dmg_overhead ))

    # Sanity-check host free space: need temp DMG (sparse, grows to ~dmg_size)
    # + compressed final DMG (~50% of app size). Fall back to zip if tight.
    local free_mb
    free_mb=$(df -m "$BUILD_DIR" | tail -1 | awk '{print $4}')
    local need_mb=$(( dmg_size + app_size_mb / 2 ))
    if (( free_mb < need_mb )); then
        log_warning "Not enough disk for DMG (${free_mb} MB free, ${need_mb} MB needed)."
        log_warning "Falling back to ZIP - free ${need_mb-free_mb}+ MB and rebuild for a proper DMG."
        _create_zip_fallback "$app_bundle"
        return $?
    fi

    log "Creating ${dmg_size} MB sparse image (${app_size_mb} MB content + ${dmg_overhead} MB HFS+ overhead)..."
    hdiutil create -size "${dmg_size}M" \
        -fs HFS+ \
        -volname "AutoYou" \
        "$temp_dmg" > /dev/null 2>&1 || {
        log_error "Failed to create temporary disk image (size: ${dmg_size} MB)"
        return 1
    }

    # Auto-mount - macOS picks /Volumes/AutoYou (avoids fixed-path conflicts).
    log "Mounting disk image..."
    local hdi_out
    hdi_out=$(hdiutil attach "$temp_dmg" 2>&1)
    attached_device=$(echo "$hdi_out" | awk '/Apple_HFS/{print $1; exit}')
    local vol_mount
    vol_mount=$(echo "$hdi_out" | awk '/Apple_HFS/{for(i=3;i<=NF;i++) printf "%s%s",$i,(i<NF?" ":""); print ""; exit}' | xargs)

    if [[ -z "$attached_device" || -z "$vol_mount" ]]; then
        log_error "Failed to mount disk image"
        _dmg_cleanup; return 1
    fi

    log "Mounted at $vol_mount ($attached_device)"

    # Copy files to image. Use ditto for app bundles so framework symlinks,
    # code-signing resources, and extended attributes are preserved.
    log "Copying application to disk image..."
    ditto --rsrc --extattr "$app_bundle" "$vol_mount/${APP_BUNDLE_NAME}" || {
        log_error "Copy failed - disk image too small or host disk full"
        _dmg_cleanup; return 1
    }
    ln -s /Applications "$vol_mount/Applications" || true

    # Unmount cleanly
    log "Unmounting disk image..."
    hdiutil detach "$attached_device" 2>/dev/null || {
        sleep 2
        hdiutil detach "$attached_device" 2>/dev/null || \
            diskutil unmountDisk force "$attached_device" 2>/dev/null || true
    }
    attached_device=""

    # Convert to compressed DMG
    log "Compressing disk image..."
    hdiutil convert "$temp_dmg" \
        -format UDZO \
        -imagekey zlib-level=9 \
        -o "$dmg_path" > /dev/null 2>&1 || {
        log_error "Failed to compress disk image"
        _dmg_cleanup; return 1
    }

    _dmg_cleanup

    if [[ ! -f "$dmg_path" ]]; then
        log_error "DMG creation failed"
        return 1
    fi

    log "DMG created: $dmg_path ($(du -sh "$dmg_path" | cut -f1))"
    log_success "DMG installer created"
}

# Sign DMG (optional enhanced security)
sign_dmg() {
    local dmg_path="$1"
    local cert_identity="${2:--}"
    
    log "Signing DMG package..."

    if [[ "$SIGN_ENABLED" != true || "$cert_identity" == "-" ]]; then
        log_warning "DMG signing skipped (signing disabled)"
        return 0
    fi

    if [[ ! -f "$dmg_path" ]]; then
        log_error "DMG not found for signing: $dmg_path"
        return 1
    fi

    codesign --force --sign "$cert_identity" --timestamp "$dmg_path" 2>&1 || {
        log_error "Failed to sign DMG"
        return 1
    }

    codesign --verify --verbose=2 "$dmg_path" 2>&1 || {
        log_error "Signed DMG verification failed"
        return 1
    }

    log_success "DMG package signed"
}

# Generate checksum for verification (skipped if file doesn't exist)
generate_checksum() {
    local target_path="$1"
    [[ -f "$target_path" ]] || return 0

    log "Generating SHA256 checksum..."
    local checksum
    checksum=$(shasum -a 256 "$target_path" | awk '{print $1}')
    local checksum_file="${target_path}.sha256"
    echo "$checksum  $(basename "$target_path")" > "$checksum_file"
    log "Checksum: $checksum"
    log_success "Saved to $checksum_file"
}

# Create release notes
create_release_notes() {
    local release_notes="${BUILD_DIR}/RELEASE_NOTES.txt"
    
    log "Creating release notes..."
    
    cat > "$release_notes" << 'EOF'
AutoYou macOS Release Notes
===========================

Installation:
1. Download the DMG file
2. Open the DMG
3. Drag AutoYou.app to Applications folder
4. Launch AutoYou from Applications

System Requirements:
- macOS 11.0 or later
- Apple Silicon (M1/M2/M3) or Intel processor
- 4GB RAM minimum
- 2GB free disk space

First Run:
- AutoYou will create a menu bar icon
- Left-click the menu bar icon to open Admin UI
- Right-click the menu bar icon for Admin UI, AI Agent UI, and Exit
- AI Agent UI opens http://localhost:8081/dev-ui/?app=autoyou_agents by default
- Default URL: http://127.0.0.1:8001

Legal:
- Before use, review AutoYou.app/Contents/Resources/Legal/LICENSE.
- Review THIRD-PARTY-NOTICES.md, NOTICE.txt, and sbom.cdx.json in the same Legal folder.
- By using AutoYou, you agree to the Terms of Use (EULA), License, Privacy Policy, responsibility terms, warranty disclaimer, and liability limits.

Troubleshooting:
- If the app doesn't start, check System Preferences > Security & Privacy
- For notarization issues on older macOS, see the README
- Logs are available in ~/Library/Application Support/AutoYou/

For more information, visit: https://github.com/AutoYou/AutoYou
EOF
    
    log_success "Release notes created"
}

# Print summary
print_summary() {
    log "========================================="
    log "SIGNING AND PACKAGING SUMMARY"
    log "========================================="
    
    local app_bundle="${BUILD_DIR}/${APP_BUNDLE_NAME}"
    local dmg_path="${BUILD_DIR}/AutoYou.dmg"
    
    if [[ -d "$app_bundle" ]]; then
        local app_size=$(du -sh "$app_bundle" | cut -f1)
        log "Application: ${APP_BUNDLE_NAME} (${app_size})"
    fi
    
    if [[ "$SKIP_DMG" == true ]]; then
        log "DMG Installer: skipped (AUTOYOU_SKIP_DMG=1)"
    elif [[ -f "$dmg_path" ]]; then
        log "DMG Installer: AutoYou.dmg ($(du -sh "$dmg_path" | cut -f1))"
    elif [[ -f "${BUILD_DIR}/AutoYou.zip" ]]; then
        log "ZIP Archive:   AutoYou.zip ($(du -sh "${BUILD_DIR}/AutoYou.zip" | cut -f1)) [DMG skipped: low disk]"
    fi
    
    if [[ "$SIGN_ENABLED" == true ]]; then
        log "Code signing: ENABLED"
        log "Certificate: $CERTIFICATE_NAME"
        [[ -n "$TEAM_ID" ]] && log "Team ID: $TEAM_ID"
    else
        log "Code signing: Ad-hoc (development only)"
    fi
    
    log "========================================="
}

# Main flow
# Credential-only validation: certificate resolvable in the keychain and the
# entitlements file parseable. Exits before the legal gate and before any file
# is touched, so operators can confirm signing readiness without a build
# (mirrors the Windows sign-backend.ps1 -PreflightOnly contract).
assert_signing_credential() {
    if [[ "$SIGN_ENABLED" == true ]]; then
        validate_certificate
        validate_entitlements_file
        log_success "Signing preflight OK: certificate + entitlements ready"
    else
        log_warning "Signing preflight: signing disabled - ad-hoc signatures would be used"
    fi
}

main() {
    parse_args "$@"
    mkdir -p "$BUILD_DIR"
    BUILD_DIR="$(cd "$BUILD_DIR" && pwd)"

    case "$(printf '%s' "${AUTOYOU_SKIP_DMG:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on)
            SKIP_DMG=true
            ;;
    esac
    
    log "Signing and Packaging Script"
    log "============================"
    log "Build directory: $BUILD_DIR"

    if [[ "$PREFLIGHT_ONLY" == true ]]; then
        assert_signing_credential
        exit 0
    fi
    
    local start_time=$(date +%s)
    
    local app_bundle="${BUILD_DIR}/${APP_BUNDLE_NAME}"
    if [[ ! -d "$app_bundle" ]]; then
        log_error "App bundle not found: $app_bundle"
        exit 1
    fi
    run_strict_release_legal_gate
    run_official_build_authorization_gate
    assert_release_legal_bundle "$app_bundle"
    local active_codesign_identity="-"
    scrub_appledouble_sidecars "$app_bundle"
    
    # Code signing
    if [[ "$SIGN_ENABLED" == true ]]; then
        validate_certificate
        validate_entitlements_file
        active_codesign_identity="$CODESIGN_IDENTITY"
        codesign_recursive "$app_bundle" "$active_codesign_identity" true
    else
        log_warning "Signing disabled - using ad-hoc signatures"
        codesign_recursive "$app_bundle" "$active_codesign_identity" false
    fi

    refresh_runtime_integrity_manifests "$app_bundle"
    scrub_appledouble_sidecars "$app_bundle"
    resign_outer_app_bundle "$app_bundle" "$active_codesign_identity"
    verify_signature "$app_bundle"

    if [[ "$SKIP_DMG" == true ]]; then
        log_warning "AUTOYOU_SKIP_DMG=1 - leaving the signed app bundle in place without DMG or ZIP output"
        local end_time=$(date +%s)
        local duration=$((end_time - start_time))

        print_summary
        log_success "Signing completed in $((duration / 60))m $((duration % 60))s"
        return 0
    fi
    
    # Create DMG
    create_dmg

    if [[ -f "${BUILD_DIR}/AutoYou.dmg" ]]; then
        sign_dmg "${BUILD_DIR}/AutoYou.dmg" "$active_codesign_identity"
    fi
    
    # Checksum whichever artifact was produced (DMG or ZIP fallback)
    if [[ -f "${BUILD_DIR}/AutoYou.dmg" ]]; then
        generate_checksum "${BUILD_DIR}/AutoYou.dmg"
    elif [[ -f "${BUILD_DIR}/AutoYou.zip" ]]; then
        generate_checksum "${BUILD_DIR}/AutoYou.zip"
    fi
    
    # Create documentation
    create_release_notes
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    print_summary
    log_success "Signing and packaging completed in $((duration / 60))m $((duration % 60))s"
}

main "$@"
