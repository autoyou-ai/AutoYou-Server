#!/bin/bash
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.


################################################################################
# AutoYou macOS Notarization Script
#
# Submits DMG to Apple notarization service and waits for completion.
# Required for distribution on macOS 10.15+ to avoid Gatekeeper warnings.
#
# Prerequisites:
# - App signed with Developer ID Application certificate
# - Apple Developer account with valid credentials
# - App-specific password for notarization
#
# Usage:
#   ./notarize.sh <dmg-path> [OPTIONS]
#
# Options:
#   --apple-id ID       Apple ID (email)
#   --password PASS     App-specific password
#   --team-id ID        Apple Team ID
#   --keychain-profile PROFILE
#                       notarytool keychain profile (default: AutoYouNotary)
#   --poll-interval N   Check status every N seconds (default: 10)
#   --verbose           Show verbose output
#   --help              Show this help message
#
# Example:
#   ./notarize.sh AutoYou.dmg --team-id ABCD123456
#
################################################################################

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DMG_PATH="${1:-.}"
APPLE_ID=""
APP_PASSWORD=""
TEAM_ID=""
NOTARY_PROFILE="${NOTARY_PROFILE:-AutoYouNotary}"
USE_KEYCHAIN_PROFILE=false
POLL_INTERVAL=10
VERBOSE=false
NOTARY_TOOL="${NOTARY_TOOL:-xcrun notarytool}"
STAPLER="${STAPLER:-xcrun stapler}"

# Logging
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

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --apple-id)
                APPLE_ID="$2"
                shift 2
                ;;
            --password)
                APP_PASSWORD="$2"
                shift 2
                ;;
            --team-id)
                TEAM_ID="$2"
                shift 2
                ;;
            --keychain-profile)
                NOTARY_PROFILE="$2"
                shift 2
                ;;
            --poll-interval)
                POLL_INTERVAL="$2"
                shift 2
                ;;
            --verbose)
                VERBOSE=true
                shift
                ;;
            --help)
                head -n 40 "$0" | tail -n 38
                exit 0
                ;;
            *)
                if [[ "$1" != --* ]]; then
                    DMG_PATH="$1"
                    shift
                else
                    log_error "Unknown option: $1"
                    exit 1
                fi
                ;;
        esac
    done
}

run_strict_release_legal_gate() {
    log "Running strict release legal gate..."
    python3 "${PROJECT_ROOT}/scripts/check_release_legal_gates.py" --artifact-scope server --no-generate --strict-unknown-license || {
        log_error "Release legal gate failed. Resolve open blockers before macOS notarization."
        exit 1
    }
}

release_artifact_profile() {
    printf '%s\n' "${AUTOYOU_BUILD_ARTIFACT_PROFILE:-autoyou-server-macos-default}"
}

run_official_build_authorization_gate() {
    log "Checking official build authorization..."
    python3 "${PROJECT_ROOT}/scripts/check_official_build_authorization.py" \
        --required \
        --artifact-profile "$(release_artifact_profile)" || {
        log_error "Official build authorization failed. Complete the SignToROSS/OpenSign build-access agreement before macOS notarization."
        exit 1
    }
}

# Validate prerequisites
validate_prerequisites() {
    log "Validating notarization prerequisites..."
    
    # Check DMG exists
    if [[ ! -f "$DMG_PATH" ]]; then
        log_error "DMG file not found: $DMG_PATH"
        exit 1
    fi
    
    # Check for notarytool (Xcode 13+)
    if ! command -v xcrun &> /dev/null; then
        log_error "xcrun not found. Please install Xcode."
        exit 1
    fi
    
    # Check for stapler
    if ! xcrun stapler help &> /dev/null 2>&1; then
        log_warning "Stapler not available (Xcode 12.1+ required for full feature set)"
    fi
    
    log_success "Prerequisites validated"
}

# Get credentials if not provided
get_credentials() {
    log "Configuring notarization credentials..."

    if [[ -z "$APPLE_ID" && -z "$APP_PASSWORD" && -n "$NOTARY_PROFILE" ]]; then
        log "Checking notarytool keychain profile: $NOTARY_PROFILE"

        local history_command=(xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" --output-format json)
        [[ -n "$TEAM_ID" ]] && history_command+=(--team-id "$TEAM_ID")

        if "${history_command[@]}" >/dev/null 2>&1; then
            USE_KEYCHAIN_PROFILE=true
            log_success "Using notarytool keychain profile"
            return 0
        fi

        log_warning "Keychain profile '$NOTARY_PROFILE' was not usable; falling back to direct credentials"
    fi
    
    # Try to get Apple ID from environment or keychain
    if [[ -z "$APPLE_ID" ]]; then
        # Check environment variable
        if [[ -n "${NOTARIZE_APPLE_ID:-}" ]]; then
            APPLE_ID="$NOTARIZE_APPLE_ID"
        else
            log_warning "Apple ID not provided"
            read -p "Enter Apple ID (email): " APPLE_ID < /dev/tty
        fi
    fi
    
    if [[ -z "$APP_PASSWORD" ]]; then
        if [[ -n "${NOTARIZE_PASSWORD:-}" ]]; then
            APP_PASSWORD="$NOTARIZE_PASSWORD"
        else
            log "Getting app-specific password from Keychain..."
            
            # Try to get password from Keychain
            if security find-generic-password -s "Apple Notarization" -a "$APPLE_ID" -w &> /dev/null 2>&1; then
                APP_PASSWORD=$(security find-generic-password -s "Apple Notarization" -a "$APPLE_ID" -w)
            else
                log_warning "Password not in Keychain, please provide"
                read -sp "Enter app-specific password: " APP_PASSWORD < /dev/tty
                echo
                
                # Optionally save to keychain
                read -p "Save to Keychain? (y/n): " -n 1 save_to_keychain < /dev/tty
                echo
                if [[ "$save_to_keychain" == "y" ]]; then
                    security add-generic-password -s "Apple Notarization" -a "$APPLE_ID" -p "$APP_PASSWORD" -T /usr/bin/security
                fi
            fi
        fi
    fi
    
    log_success "Credentials configured"
}

# Submit to notarization
submit_for_notarization() {
    log "Submitting DMG for notarization..."
    log "DMG: $DMG_PATH"
    if [[ "$USE_KEYCHAIN_PROFILE" == true ]]; then
        log "Credentials: keychain profile $NOTARY_PROFILE"
    else
        log "Apple ID: $APPLE_ID"
    fi
    
    # Create submission request
    local submission_output=$(mktemp)
    local submit_command=(xcrun notarytool submit "$DMG_PATH" --wait --output-format json)

    if [[ "$USE_KEYCHAIN_PROFILE" == true ]]; then
        submit_command+=(--keychain-profile "$NOTARY_PROFILE")
        [[ -n "$TEAM_ID" ]] && submit_command+=(--team-id "$TEAM_ID")
    else
        submit_command+=(--apple-id "$APPLE_ID" --password "$APP_PASSWORD" --team-id "$TEAM_ID")
    fi
    
    "${submit_command[@]}" > "$submission_output" 2>&1 || {
        log_error "Notarization submission failed"
        cat "$submission_output" | tail -20
        rm -f "$submission_output"
        return 1
    }
    
    # Extract request ID (for newer xcrun versions)
    if grep -q '"id"' "$submission_output"; then
        REQUEST_ID=$(jq -r '.id' "$submission_output" 2>/dev/null || \
                     grep '"id"' "$submission_output" | head -1 | sed 's/.*"id": "//;s/".*//')
    fi
    
    log "Submission complete"
    
    if [[ $VERBOSE == true ]]; then
        cat "$submission_output"
    fi
    
    rm -f "$submission_output"
}

# Check notarization status (legacy method)
check_notarization_status() {
    log "Checking notarization status..."
    
    if [[ -z "${REQUEST_ID:-}" ]]; then
        log_warning "No request ID available"
        return 1
    fi
    
    local status_output=$(mktemp)
    local info_command=(xcrun notarytool info "$REQUEST_ID" --output-format json)

    if [[ "$USE_KEYCHAIN_PROFILE" == true ]]; then
        info_command+=(--keychain-profile "$NOTARY_PROFILE")
        [[ -n "$TEAM_ID" ]] && info_command+=(--team-id "$TEAM_ID")
    else
        info_command+=(--apple-id "$APPLE_ID" --password "$APP_PASSWORD" --team-id "$TEAM_ID")
    fi
    
    "${info_command[@]}" > "$status_output" 2>&1 || {
        log_error "Failed to check status"
        rm -f "$status_output"
        return 1
    }
    
    # Parse status
    local status=$(jq -r '.status' "$status_output" 2>/dev/null || \
                   grep '"status"' "$status_output" | sed 's/.*"status": "//;s/".*//')
    
    log "Status: $status"
    
    if [[ "$status" != "Accepted" ]]; then
        log_warning "Notarization not yet complete"
        cat "$status_output" | jq '.' 2>/dev/null || cat "$status_output"
    fi
    
    rm -f "$status_output"
    
    [[ "$status" == "Accepted" ]]
}

# Staple ticket to DMG
staple_notarization() {
    log "Stapling notarization ticket to DMG..."
    
    if ! xcrun stapler staple "$DMG_PATH" 2>&1; then
        log_warning "Stapling failed - notarization may have failed or is still pending"
        return 1
    fi
    
    log_success "Stapling complete"
}

# Verify notarization
verify_notarization() {
    log "Verifying notarization..."
    
    if ! spctl -a -v --type open --context context:primary-signature "$DMG_PATH" 2>&1; then
        log_warning "Package signature not recognized yet"
        return 1
    fi
    
    log_success "Package signature verified"
}

refresh_checksum() {
    local dmg_name
    dmg_name="$(basename "$DMG_PATH")"
    shasum -a 256 "$DMG_PATH" |
        awk -v name="$dmg_name" '{print $1 "  " name}' > "${DMG_PATH}.sha256"
    log_success "Updated checksum: ${DMG_PATH}.sha256"
}

# Poll notarization with timeout
wait_for_notarization() {
    log "Waiting for notarization to complete (may take 5-30 minutes)..."
    
    local max_attempts=$((1800 / POLL_INTERVAL))  # 30 minute timeout
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        attempt=$((attempt + 1))
        
        log "Check $attempt/$max_attempts: Polling status..."
        
        if check_notarization_status; then
            log_success "Notarization accepted!"
            return 0
        fi
        
        if [[ $attempt -lt $max_attempts ]]; then
            log "Waiting ${POLL_INTERVAL}s before next check..."
            sleep "$POLL_INTERVAL"
        fi
    done
    
    log_error "Notarization timeout - not completed within 30 minutes"
    return 1
}

# Print summary
print_summary() {
    log "==========================================="
    log "NOTARIZATION SUMMARY"
    log "==========================================="
    log "DMG: $(basename "$DMG_PATH")"
    log "File size: $(du -h "$DMG_PATH" | cut -f1)"
    log "Notarization: COMPLETE"
    log "==========================================="
    log ""
    log "The DMG can now be distributed without Gatekeeper warnings."
    log "Users on macOS 10.15+ will see the verified publisher."
}

# Main flow
main() {
    parse_args "$@"
    
    log "Apple Notarization Script"
    log "========================="
    
    validate_prerequisites
    run_strict_release_legal_gate
    run_official_build_authorization_gate
    get_credentials
    
    local start_time=$(date +%s)
    
    # Submit for notarization (this now waits automatically in newer xcrun)
    submit_for_notarization
    
    # For older xcrun versions, poll status
    if command -v xcrun &> /dev/null && ! xcrun notarytool submit --help 2>&1 | grep -q "\-\-wait"; then
        wait_for_notarization
    fi
    
    staple_notarization
    verify_notarization
    refresh_checksum
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    print_summary
    log_success "Notarization completed in $((duration / 60))m $((duration % 60))s"
}

# Error handler
trap 'log_error "Script interrupted"; exit 1' INT TERM

# Run main
main "$@"
