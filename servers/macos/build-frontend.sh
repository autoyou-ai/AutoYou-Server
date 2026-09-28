#!/bin/bash
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.


################################################################################
# AutoYou macOS Frontend Build Script
#
# Compiles the Swift macOS frontend application using xcodebuild
# and integrates it with the backend runtime
#
# Usage:
#   ./build-frontend.sh [OPTIONS]
#
# Options:
#   --build-dir DIR     Build directory (default: ./build)
#   --scheme SCHEME     Xcode scheme (default: AutoYou)
#   --configuration     Release or Debug (default: Release)
#   --help              Show this help message
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
BUILD_DIR="${SCRIPT_DIR}/build"
SCHEME="AutoYou"
CONFIGURATION="Release"
XCODE_PROJECT="${SCRIPT_DIR}/apple/AutoYou.xcodeproj"
VERSION_FILE="$PROJECT_ROOT/VERSION"

if [[ ! -f "$VERSION_FILE" ]]; then
    echo "Missing canonical version file: $VERSION_FILE" >&2
    exit 1
fi
APP_VERSION="$(head -n 1 "$VERSION_FILE" | tr -d '\r')"
if [[ ! "$APP_VERSION" =~ ^[0-9]+[.][0-9]+[.][0-9]+([.][0-9]+)?$ ]]; then
    echo "Invalid canonical version: $APP_VERSION" >&2
    exit 1
fi

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

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --build-dir)
                BUILD_DIR="$2"
                shift 2
                ;;
            --scheme)
                SCHEME="$2"
                shift 2
                ;;
            --configuration)
                CONFIGURATION="$2"
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

# Validate Xcode installation
validate_xcode() {
    log "Validating Xcode installation..."
    
    if ! command -v xcodebuild &> /dev/null; then
        log_error "xcodebuild not found. Please install Xcode."
        exit 1
    fi
    
    if ! command -v swift &> /dev/null; then
        log_error "Swift not found. Please install Xcode."
        exit 1
    fi
    
    local xcode_version=$(xcodebuild -version | head -1)
    log "Using: $xcode_version"
    
    log_success "Xcode validation passed"
}

# Validate project structure
validate_project() {
    log "Validating project structure..."
    
    if [[ ! -d "$XCODE_PROJECT" ]]; then
        log_warning "Xcode project not found at $XCODE_PROJECT"
        log "Attempting to create project structure..."
        
        # Create basic project structure if needed
        mkdir -p "$(dirname "$XCODE_PROJECT")"
    fi
    
    # Check for source files
    local source_dir="${SCRIPT_DIR}/apple/AutoYou"
    if [[ ! -d "$source_dir" ]]; then
        log_error "Source directory not found: $source_dir"
        exit 1
    fi
    
    local required_files=(
        "main.swift"
        "AppDelegate.swift"
        "BackendManager.swift"
        "HostDiagnostics.swift"
        "HostRuntimeConfiguration.swift"
        "StatusMonitor.swift"
        "TrayMenuController.swift"
    )
    
    for file in "${required_files[@]}"; do
        if [[ ! -f "${source_dir}/${file}" ]]; then
            log_error "Required source file not found: $file"
            exit 1
        fi
    done
    
    log_success "Project structure validated"
}

# Build using Swift Package Manager (if project.swift exists)
build_with_spm() {
    log "Building with Swift Package Manager..."
    
    local spm_package="${SCRIPT_DIR}/apple/Package.swift"
    
    if [[ ! -f "$spm_package" ]]; then
        log_warning "Package.swift not found, skipping SPM build"
        return 1
    fi
    
    cd "${SCRIPT_DIR}/apple"
    
    local build_args=(
        "build"
        "--product" "AutoYou"
        "--configuration" "release"
    )
    
    # Build with Swift
    if swift "${build_args[@]}" 2>&1 | tee "${BUILD_DIR}/swift_build.log"; then
        log_success "SPM build completed"
        
        # Copy build output
        local build_output="${SCRIPT_DIR}/apple/.build/release/AutoYou"
        if [[ -f "$build_output" ]]; then
            mkdir -p "${BUILD_DIR}/executables"
            cp "$build_output" "${BUILD_DIR}/executables/AutoYou"
            return 0
        fi
    else
        log_warning "SPM build failed, attempting xcodebuild"
        return 1
    fi
}

# Build using xcodebuild
build_with_xcode() {
    log "Building with xcodebuild..."
    
    if [[ ! -d "$XCODE_PROJECT" ]]; then
        log_warning "Xcode project not found, using SPM instead"
        return 1
    fi
    
    local build_settings=(
        "CONFIGURATION_BUILD_DIR=${BUILD_DIR}"
        "SYMROOT=${BUILD_DIR}"
        "OBJROOT=${BUILD_DIR}/intermediate"
        "CODE_SIGN_IDENTITY=-"
        "PROVISIONING_PROFILE_SPECIFIER="
    )
    
    xcodebuild \
        -project "$XCODE_PROJECT" \
        -scheme "$SCHEME" \
        -configuration "$CONFIGURATION" \
        "${build_settings[@]}" \
        build 2>&1 | tee "${BUILD_DIR}/xcode_build.log" || {
        log_error "xcodebuild failed"
        return 1
    }
    
    log_success "xcodebuild completed successfully"
}

# Create app bundle structure
create_app_bundle() {
    log "Creating application bundle..."
    
    local app_bundle="${BUILD_DIR}/AutoYou.app"
    local contents="${app_bundle}/Contents"
    local macos_dir="${contents}/MacOS"
    local resources_dir="${contents}/Resources"

    if [[ -e "$app_bundle" ]]; then
        xattr -dr com.apple.provenance "$app_bundle" 2>/dev/null || true
        xattr -dr com.apple.quarantine "$app_bundle" 2>/dev/null || true
        chmod -R u+w "$app_bundle" 2>/dev/null || true
        rm -rf "$app_bundle"
    fi
    
    # Create directory structure
    mkdir -p "$macos_dir"
    mkdir -p "$resources_dir"
    mkdir -p "${contents}/Frameworks"
    
    # Copy Info.plist
    local info_plist="${SCRIPT_DIR}/apple/AutoYou/Info.plist"
    if [[ -f "$info_plist" ]]; then
        cp "$info_plist" "${contents}/Info.plist"
    else
        log_warning "Info.plist not found, creating minimal version"
        cat > "${contents}/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleExecutable</key>
    <string>AutoYou</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon.icns</string>
    <key>CFBundleIdentifier</key>
    <string>com.autoyou.macos.host</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleDisplayName</key>
    <string>AutoYou</string>
    <key>CFBundleName</key>
    <string>AutoYou</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>$APP_VERSION</string>
    <key>CFBundleVersion</key>
    <string>$APP_VERSION</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSLocalNetworkUsageDescription</key>
    <string>AutoYou uses the local network for pairing, WebRTC voice calls, and bundled services.</string>
    <key>NSCameraUsageDescription</key>
    <string>AutoYou uses the camera only when you enable video features.</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>AutoYou uses the microphone for voice calls and speech recognition.</string>
    <key>NSScreenCaptureUsageDescription</key>
    <string>AutoYou uses screen recording access only when you enable Remote Desktop or share your computer screen.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>AutoYou uses automation only when you ask Remote Desktop to list or focus windows.</string>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
EOF
    fi
    /usr/bin/plutil -replace CFBundleShortVersionString -string "$APP_VERSION" "${contents}/Info.plist"
    /usr/bin/plutil -replace CFBundleVersion -string "$APP_VERSION" "${contents}/Info.plist"

    local tray_logo_source="${SCRIPT_DIR}/../windows/AutoYouWindowsHost/Assets/TrayLogo.png"
    local app_icon_source="${SCRIPT_DIR}/../../assets/logo.png"
    local app_logo_source="${SCRIPT_DIR}/../../assets/logo.png"

    if [[ -f "$tray_logo_source" ]]; then
        cp "$tray_logo_source" "${resources_dir}/TrayLogo.png"
    else
        log_warning "Tray logo source not found at $tray_logo_source"
    fi

    if [[ -f "$app_icon_source" ]]; then
        cp "$app_icon_source" "${resources_dir}/AppIcon.png"

        local iconset_dir="${BUILD_DIR}/AppIcon.iconset"
        rm -rf "$iconset_dir"
        mkdir -p "$iconset_dir"

        sips -z 16 16 "$app_icon_source" --out "${iconset_dir}/icon_16x16.png" >/dev/null
        sips -z 32 32 "$app_icon_source" --out "${iconset_dir}/icon_16x16@2x.png" >/dev/null
        sips -z 32 32 "$app_icon_source" --out "${iconset_dir}/icon_32x32.png" >/dev/null
        sips -z 64 64 "$app_icon_source" --out "${iconset_dir}/icon_32x32@2x.png" >/dev/null
        sips -z 128 128 "$app_icon_source" --out "${iconset_dir}/icon_128x128.png" >/dev/null
        sips -z 256 256 "$app_icon_source" --out "${iconset_dir}/icon_128x128@2x.png" >/dev/null
        sips -z 256 256 "$app_icon_source" --out "${iconset_dir}/icon_256x256.png" >/dev/null
        sips -z 512 512 "$app_icon_source" --out "${iconset_dir}/icon_256x256@2x.png" >/dev/null
        sips -z 512 512 "$app_icon_source" --out "${iconset_dir}/icon_512x512.png" >/dev/null
        cp "$app_icon_source" "${iconset_dir}/icon_512x512@2x.png"

        if iconutil -c icns "$iconset_dir" -o "${resources_dir}/AppIcon.icns" >/dev/null 2>&1; then
            log "Generated AppIcon.icns from the square AutoYou app icon"
        else
            log_warning "Failed to generate AppIcon.icns from $app_icon_source"
        fi

        rm -rf "$iconset_dir"
    else
        log_warning "App icon source not found at $app_icon_source"
    fi

    if [[ -f "$app_logo_source" ]]; then
        cp "$app_logo_source" "${resources_dir}/AppLogo@1x.png"
    else
        log_warning "App logo source not found at $app_logo_source"
    fi
    
    log_success "App bundle structure created: $app_bundle"
}

# Copy frontend binary into app bundle
install_frontend_binary() {
    log "Installing frontend binary into app bundle..."
    
    local app_bundle="${BUILD_DIR}/AutoYou.app"
    local macos_dir="${app_bundle}/Contents/MacOS"
    
    # Look for built binary
    local binary_path=""
    
    if [[ -f "${BUILD_DIR}/executables/AutoYou" ]]; then
        binary_path="${BUILD_DIR}/executables/AutoYou"
    elif [[ -f "${XCODE_PROJECT%.xcodeproj}/build/Release/AutoYou" ]]; then
        binary_path="${XCODE_PROJECT%.xcodeproj}/build/Release/AutoYou"
    else
        log_error "Frontend binary not found"
        return 1
    fi
    
    cp "$binary_path" "${macos_dir}/AutoYou"
    chmod +x "${macos_dir}/AutoYou"
    
    # Sign with ad-hoc signature
    codesign -s - "${macos_dir}/AutoYou" 2>&1 || {
        log_warning "Could not sign binary (may be okay for development)"
    }
    
    log_success "Frontend binary installed"
}

# Copy backend runtime into app bundle
install_backend_runtime() {
    log "Installing backend runtime into app bundle..."
    
    local app_bundle="${BUILD_DIR}/AutoYou.app"
    local resources_dir="${app_bundle}/Contents/Resources"
    local backend_dir="${resources_dir}/backend"
    local backend_app_source=""
    local backend_dist_source=""
    local candidate=""

    for candidate in \
        "${BUILD_DIR}/backend/AutoYou.dist" \
        "${BUILD_DIR}/backend/AutoYouServer.dist" \
        "${BUILD_DIR}/backend/autoyou_app.dist"; do
        if [[ -d "$candidate" ]]; then
            backend_dist_source="$candidate"
            break
        fi
    done

    for candidate in \
        "${BUILD_DIR}/backend/AutoYou.app" \
        "${BUILD_DIR}/backend/AutoYouServer.app" \
        "${BUILD_DIR}/backend/autoyou_app.app"; do
        if [[ -d "$candidate" ]]; then
            backend_app_source="$candidate"
            break
        fi
    done
    
    # Create backend runtime directory
    mkdir -p "$backend_dir"
    rm -rf \
        "${backend_dir}/AutoYou.app" \
        "${backend_dir}/AutoYou.dist" \
        "${backend_dir}/AutoYouServer.app" \
        "${backend_dir}/AutoYouServer.dist" \
        "${backend_dir}/autoyou_app.app" \
        "${backend_dir}/autoyou_app.dist"
    
    # Copy the full compiled backend bundle so the host and backend keep the
    # same runtime layout (embedded resources, runtime/node, runtime/playwright).
    if [[ -n "$backend_dist_source" ]]; then
        cp -R "$backend_dist_source" "$backend_dir/"
        log "Copied backend dist bundle"
    elif [[ -n "$backend_app_source" ]]; then
        cp -R "$backend_app_source" "$backend_dir/"
        log "Copied backend app bundle"
    else
        log_warning "Compiled backend bundle not found at ${BUILD_DIR}/backend/{AutoYou,AutoYouServer,autoyou_app}.{app,dist}"
    fi

    log_success "Backend runtime installed"
}

# Copy release legal files into the standalone app bundle.
copy_release_legal_bundle() {
    log "Copying release legal bundle..."

    local app_bundle="${BUILD_DIR}/AutoYou.app"
    local legal_dir="${app_bundle}/Contents/Resources/Legal"
    local required_legal_file

    python3 "${PROJECT_ROOT}/scripts/copy_release_legal_artifacts.py" \
        --artifact autoyou-server-macos-default \
        --target "$legal_dir" \
        --generate

    for required_legal_file in LICENSE THIRD-PARTY-NOTICES.md NOTICE.txt sbom.cdx.json; do
        if [[ ! -f "${legal_dir}/${required_legal_file}" ]]; then
            log_error "Missing release legal file: ${legal_dir}/${required_legal_file}"
            return 1
        fi
    done

    log_success "Release legal bundle copied"
}

# Verify app bundle contents
verify_app_bundle() {
    log "Verifying app bundle contents..."
    
    local app_bundle="${BUILD_DIR}/AutoYou.app"
    local macos_dir="${app_bundle}/Contents/MacOS"
    
    if [[ ! -f "${macos_dir}/AutoYou" ]]; then
        log_error "Executable not found in app bundle"
        return 1
    fi
    
    if [[ ! -f "${app_bundle}/Contents/Info.plist" ]]; then
        log_error "Info.plist not found in app bundle"
        return 1
    fi
    
    # Check for valid Mach-O binary
    if ! file -b "${macos_dir}/AutoYou" | grep -q "Mach-O"; then
        log_error "Invalid binary in app bundle"
        return 1
    fi

    local bundled_backend_app="${app_bundle}/Contents/Resources/backend/AutoYou.app/Contents/MacOS/AutoYouServer"
    local bundled_backend_dist="${app_bundle}/Contents/Resources/backend/AutoYou.dist/AutoYouServer"
    local bundled_backend_app_legacy="${app_bundle}/Contents/Resources/backend/AutoYouServer.app/Contents/MacOS/AutoYouServer"
    local bundled_backend_dist_legacy="${app_bundle}/Contents/Resources/backend/AutoYouServer.dist/AutoYouServer"
    local bundled_backend_app_fallback="${app_bundle}/Contents/Resources/backend/autoyou_app.app/Contents/MacOS/AutoYouServer"
    local bundled_backend_dist_fallback="${app_bundle}/Contents/Resources/backend/autoyou_app.dist/AutoYouServer"
    if [[ ! -f "$bundled_backend_app" ]] && [[ ! -f "$bundled_backend_dist" ]]; then
        if [[ ! -f "$bundled_backend_app_legacy" ]] && [[ ! -f "$bundled_backend_dist_legacy" ]] && [[ ! -f "$bundled_backend_app_fallback" ]] && [[ ! -f "$bundled_backend_dist_fallback" ]]; then
            log_error "Bundled backend not found in app resources"
            return 1
        fi
    fi
    
    # List bundle structure (|| true: find exits 141/SIGPIPE when head -20 closes early)
    log "App bundle structure:"
    find "$app_bundle" -type f 2>/dev/null | head -20 | sed 's/^/  /' || true

    local bundle_size
    bundle_size=$(du -sh "$app_bundle" 2>/dev/null | cut -f1) || true
    log "Bundle size: ${bundle_size:-unknown}"

    log_success "App bundle verified"
}

# Main build flow
main() {
    parse_args "$@"
    
    log "Frontend Build Script"
    log "===================="
    log "Build directory: $BUILD_DIR"
    log "Scheme: $SCHEME"
    log "Configuration: $CONFIGURATION"
    
    mkdir -p "$BUILD_DIR"
    
    local start_time=$(date +%s)
    
    validate_xcode
    validate_project
    
    # Try building with different methods
    if ! build_with_spm; then
        if ! build_with_xcode; then
            log_error "Build failed with both SPM and xcodebuild"
            return 1
        fi
    fi
    
    create_app_bundle
    install_frontend_binary
    install_backend_runtime
    copy_release_legal_bundle
    verify_app_bundle
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    log_success "Frontend build completed in $((duration / 60))m $((duration % 60))s"
}

main "$@"
