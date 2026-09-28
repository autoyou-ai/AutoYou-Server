#!/bin/bash
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS_SCRIPT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${MACOS_SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${MACOS_SCRIPT_DIR}/build"
TARGET_ARCH="x86_64"
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

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-dir)
            BUILD_DIR="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [--build-dir DIR]"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

APP_BUNDLE="${BUILD_DIR}/AutoYou.app"
CONTENTS_DIR="${APP_BUNDLE}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
BACKEND_DIR="${RESOURCES_DIR}/backend"
BACKEND_SOURCE=""

for candidate in \
    "${BUILD_DIR}/backend/AutoYou.dist" \
    "${BUILD_DIR}/backend/AutoYouServer.dist" \
    "${BUILD_DIR}/backend/autoyou_app.dist"; do
    if [[ -x "${candidate}/AutoYouServer" ]]; then
        BACKEND_SOURCE="$candidate"
        break
    fi
done

if [[ -z "$BACKEND_SOURCE" ]]; then
    echo "Compiled backend dist not found under ${BUILD_DIR}/backend"
    exit 1
fi

python3 "${PROJECT_ROOT}/scripts/check_release_legal_gates.py" --artifact-scope server --no-generate --strict-unknown-license || {
    echo "Release legal gate failed. Resolve open blockers before macOS Intel fallback release packaging." >&2
    exit 1
}

copy_release_legal_bundle() {
    local legal_dir="${RESOURCES_DIR}/Legal"

    python3 "${PROJECT_ROOT}/scripts/copy_release_legal_artifacts.py" \
        --artifact autoyou-server-macos-default \
        --target "$legal_dir" \
        --generate

    local required_legal_file
    for required_legal_file in LICENSE THIRD-PARTY-NOTICES.md NOTICE.txt sbom.cdx.json; do
        if [[ ! -f "${legal_dir}/${required_legal_file}" ]]; then
            echo "Missing release legal file: ${legal_dir}/${required_legal_file}"
            exit 1
        fi
    done
}

rm -rf "$APP_BUNDLE"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR" "$BACKEND_DIR" "${CONTENTS_DIR}/Frameworks"

INFO_PLIST="${MACOS_SCRIPT_DIR}/apple/AutoYou/Info.plist"
if [[ -f "$INFO_PLIST" ]]; then
    cp "$INFO_PLIST" "${CONTENTS_DIR}/Info.plist"
else
    cat > "${CONTENTS_DIR}/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>AutoYou</string>
    <key>CFBundleIdentifier</key>
    <string>com.autoyou.macos.host</string>
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
</dict>
</plist>
EOF
fi
/usr/bin/plutil -replace CFBundleShortVersionString -string "$APP_VERSION" "${CONTENTS_DIR}/Info.plist"
/usr/bin/plutil -replace CFBundleVersion -string "$APP_VERSION" "${CONTENTS_DIR}/Info.plist"

APP_ICON_SOURCE="${PROJECT_ROOT}/assets/logo.png"
if [[ -f "$APP_ICON_SOURCE" ]]; then
    cp "$APP_ICON_SOURCE" "${RESOURCES_DIR}/AppIcon.png"
    ICONSET_DIR="${BUILD_DIR}/AppIcon.iconset"
    rm -rf "$ICONSET_DIR"
    mkdir -p "$ICONSET_DIR"
    sips -z 16 16 "$APP_ICON_SOURCE" --out "${ICONSET_DIR}/icon_16x16.png" >/dev/null
    sips -z 32 32 "$APP_ICON_SOURCE" --out "${ICONSET_DIR}/icon_16x16@2x.png" >/dev/null
    sips -z 32 32 "$APP_ICON_SOURCE" --out "${ICONSET_DIR}/icon_32x32.png" >/dev/null
    sips -z 64 64 "$APP_ICON_SOURCE" --out "${ICONSET_DIR}/icon_32x32@2x.png" >/dev/null
    sips -z 128 128 "$APP_ICON_SOURCE" --out "${ICONSET_DIR}/icon_128x128.png" >/dev/null
    sips -z 256 256 "$APP_ICON_SOURCE" --out "${ICONSET_DIR}/icon_128x128@2x.png" >/dev/null
    sips -z 256 256 "$APP_ICON_SOURCE" --out "${ICONSET_DIR}/icon_256x256.png" >/dev/null
    sips -z 512 512 "$APP_ICON_SOURCE" --out "${ICONSET_DIR}/icon_256x256@2x.png" >/dev/null
    sips -z 512 512 "$APP_ICON_SOURCE" --out "${ICONSET_DIR}/icon_512x512.png" >/dev/null
    cp "$APP_ICON_SOURCE" "${ICONSET_DIR}/icon_512x512@2x.png"
    iconutil -c icns "$ICONSET_DIR" -o "${RESOURCES_DIR}/AppIcon.icns" >/dev/null 2>&1 || true
    rm -rf "$ICONSET_DIR"
fi

TRAY_LOGO_SOURCE="${PROJECT_ROOT}/servers/windows/AutoYouWindowsHost/Assets/TrayLogo.png"
if [[ -f "$TRAY_LOGO_SOURCE" ]]; then
    cp "$TRAY_LOGO_SOURCE" "${RESOURCES_DIR}/TrayLogo.png"
fi

APP_LOGO_SOURCE="${PROJECT_ROOT}/assets/logo.png"
if [[ -f "$APP_LOGO_SOURCE" ]]; then
    cp "$APP_LOGO_SOURCE" "${RESOURCES_DIR}/AppLogo@1x.png"
fi

ditto --rsrc --extattr "$BACKEND_SOURCE" "${BACKEND_DIR}/AutoYou.dist"
copy_release_legal_bundle

RUNTIME_STDLIB_ROOT="${BACKEND_DIR}/AutoYou.dist/runtime_stdlib"
CONFIG_DIR="$(find "$RUNTIME_STDLIB_ROOT" -maxdepth 1 -type d -name 'config-*-darwin' -print -quit 2>/dev/null || true)"
if [[ -n "$CONFIG_DIR" && -f "${BACKEND_DIR}/AutoYou.dist/Python" ]]; then
    for libpython_link in "$CONFIG_DIR"/libpython*.a "$CONFIG_DIR"/libpython*.dylib; do
        [[ -L "$libpython_link" ]] || continue
        if [[ "$(readlink "$libpython_link")" == "../../../Python" ]]; then
            ln -sf "../../Python" "$libpython_link"
        fi
    done
fi

LAUNCHER_SOURCE="${BUILD_DIR}/AutoYouFallbackLauncher.m"
cat > "$LAUNCHER_SOURCE" <<'EOF'
#import <Cocoa/Cocoa.h>
#include <signal.h>
#include <unistd.h>

static NSString *AutoYouEnvValue(NSString *name, NSString *fallback) {
    const char *raw = getenv([name UTF8String]);
    if (raw != NULL && raw[0] != '\0') {
        return [NSString stringWithUTF8String:raw];
    }
    return fallback;
}

static NSString *AutoYouFirstEnvValue(NSArray<NSString *> *names, NSString *fallback) {
    for (NSString *name in names) {
        NSString *value = AutoYouEnvValue(name, nil);
        if ([value length] > 0) {
            return value;
        }
    }
    return fallback;
}

static NSString *AutoYouURLHost(NSString *bindHost) {
    if ([bindHost length] == 0 || [bindHost isEqualToString:@"0.0.0.0"] || [bindHost isEqualToString:@"::"]) {
        return @"127.0.0.1";
    }
    if ([bindHost containsString:@":"] && ![bindHost hasPrefix:@"["]) {
        return [NSString stringWithFormat:@"[%@]", bindHost];
    }
    return bindHost;
}

static BOOL AutoYouFindBackend(NSString *resourcesDir, NSString **backendPathOut, NSString **backendRootOut) {
    NSArray<NSString *> *roots = @[
        @"backend/AutoYou.dist",
        @"backend/AutoYouServer.dist",
        @"backend/autoyou_app.dist"
    ];
    NSFileManager *fileManager = [NSFileManager defaultManager];

    for (NSString *relativeRoot in roots) {
        NSString *rootPath = [resourcesDir stringByAppendingPathComponent:relativeRoot];
        NSString *backendPath = [rootPath stringByAppendingPathComponent:@"AutoYouServer"];
        if ([fileManager isExecutableFileAtPath:backendPath]) {
            if (backendPathOut != NULL) {
                *backendPathOut = backendPath;
            }
            if (backendRootOut != NULL) {
                *backendRootOut = rootPath;
            }
            return YES;
        }
    }

    return NO;
}

@interface AutoYouFallbackAppDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSStatusItem *statusItem;
@property(nonatomic, strong) NSMenu *statusMenu;
@property(nonatomic, strong) NSMenuItem *statusMenuItem;
@property(nonatomic, strong) NSTask *backendTask;
@property(nonatomic, assign) pid_t backendProcessGroupID;
@property(nonatomic, assign) BOOL backendProcessGroupOwned;
@property(nonatomic, strong) NSTimer *statusTimer;
@property(nonatomic, strong) NSFileHandle *logHandle;
@property(nonatomic, copy) NSString *resourcesDir;
@property(nonatomic, copy) NSString *backendRoot;
@property(nonatomic, copy) NSString *backendPath;
@property(nonatomic, copy) NSString *bindHost;
@property(nonatomic, copy) NSString *adminPort;
@property(nonatomic, copy) NSString *aiPort;
@property(nonatomic, copy) NSString *authPort;
@property(nonatomic, copy) NSString *shutdownToken;
@property(nonatomic, strong) NSURL *adminURL;
@property(nonatomic, strong) NSURL *chatURL;
@property(nonatomic, assign) BOOL terminationRequested;
@property(nonatomic, assign) BOOL openedAdminOnce;
@end

@implementation AutoYouFallbackAppDelegate

- (instancetype)init {
    self = [super init];
    if (self) {
        _resourcesDir = [[[NSBundle mainBundle] resourcePath] copy];
        _bindHost = [AutoYouFirstEnvValue(@[@"AUTOYOU_BIND_HOST", @"AUTOYOU_HOST_BIND"], @"127.0.0.1") copy];
        _adminPort = [AutoYouFirstEnvValue(@[@"AUTOYOU_ADMIN_PORT", @"AUTOYOU_ADMIN_SPORT"], @"8001") copy];
        _aiPort = [AutoYouFirstEnvValue(@[@"AUTOYOU_AI_PORT", @"AUTOYOU_AI_AGENT_SERVER_PORT"], @"8081") copy];
        _authPort = [AutoYouFirstEnvValue(@[@"AUTOYOU_AUTH_PORT", @"AUTOYOU_AUTH_SERVER_PORT"], @"8002") copy];
        NSString *urlHost = AutoYouURLHost(_bindHost);
        _adminURL = [NSURL URLWithString:[NSString stringWithFormat:@"http://%@:%@", urlHost, _adminPort]];
        _chatURL = [_adminURL URLByAppendingPathComponent:@"chat"];
        _shutdownToken = [AutoYouEnvValue(@"AUTOYOU_SHUTDOWN_TOKEN", [[NSUUID UUID] UUIDString]) copy];
    }
    return self;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [self configureApplicationIcon];
    [self configureStatusItem];
    [self startBackend];
    [self startStatusMonitor];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    (void)sender;
    return NO;
}

- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)flag {
    (void)sender;
    (void)flag;
    [self openAdminUI:nil];
    return NO;
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    (void)sender;
    if (self.terminationRequested) {
        return NSTerminateNow;
    }

    self.terminationRequested = YES;
    [self.statusTimer invalidate];
    self.statusTimer = nil;
    [self updateStatus:@"Status: Stopping..." tooltip:@"AutoYou is stopping"];
    [self requestBackendShutdown];
    return NSTerminateLater;
}

- (void)configureApplicationIcon {
    NSArray<NSString *> *resourceNames = @[@"AppIcon.png", @"AppLogo@1x.png", @"TrayLogo.png", @"AppIcon.icns"];
    for (NSString *resourceName in resourceNames) {
        NSString *path = [self.resourcesDir stringByAppendingPathComponent:resourceName];
        NSImage *image = [[NSImage alloc] initWithContentsOfFile:path];
        if (image != nil) {
            [NSApp setApplicationIconImage:image];
            return;
        }
    }
}

- (NSImage *)loadTrayImage {
    NSArray<NSString *> *resourceNames = @[@"TrayLogo.png", @"AppIcon.png", @"AppLogo@1x.png", @"AppIcon.icns"];
    for (NSString *resourceName in resourceNames) {
        NSString *path = [self.resourcesDir stringByAppendingPathComponent:resourceName];
        NSImage *image = [[NSImage alloc] initWithContentsOfFile:path];
        if (image != nil) {
            [image setSize:NSMakeSize(18.0, 18.0)];
            [image setTemplate:YES];
            return image;
        }
    }

    if (@available(macOS 11.0, *)) {
        NSImage *symbol = [NSImage imageWithSystemSymbolName:@"desktopcomputer" accessibilityDescription:@"AutoYou"];
        [symbol setTemplate:YES];
        return symbol;
    }
    return nil;
}

- (void)configureStatusItem {
    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSSquareStatusItemLength];
    NSStatusBarButton *button = [self.statusItem button];
    NSImage *image = [self loadTrayImage];
    if (image != nil) {
        [button setImage:image];
    } else {
        [button setTitle:@"A"];
    }
    [button setTarget:self];
    [button setAction:@selector(handleStatusItemClick:)];
    [button sendActionOn:(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)];
    [button setToolTip:@"AutoYou is initializing; the Admin UI will open when ready"];

    self.statusMenu = [[NSMenu alloc] initWithTitle:@"AutoYou"];
    self.statusMenuItem = [[NSMenuItem alloc] initWithTitle:@"Status: Initializing, please wait..." action:nil keyEquivalent:@""];
    [self.statusMenuItem setEnabled:NO];
    [self.statusMenu addItem:self.statusMenuItem];
    [self.statusMenu addItem:[NSMenuItem separatorItem]];

    NSMenuItem *adminItem = [[NSMenuItem alloc] initWithTitle:@"Admin UI (Settings)" action:@selector(openAdminUI:) keyEquivalent:@"a"];
    [adminItem setTarget:self];
    [adminItem setAttributedTitle:[[NSAttributedString alloc] initWithString:[adminItem title] attributes:@{NSFontAttributeName: [NSFont boldSystemFontOfSize:[NSFont systemFontSize]]}]];
    [self.statusMenu addItem:adminItem];

    NSMenuItem *chatItem = [[NSMenuItem alloc] initWithTitle:@"Open Chat" action:@selector(openChat:) keyEquivalent:@"c"];
    [chatItem setTarget:self];
    [self.statusMenu addItem:chatItem];

    NSMenuItem *restartItem = [[NSMenuItem alloc] initWithTitle:@"Restart AI Agent" action:@selector(restartAiAgent:) keyEquivalent:@"r"];
    [restartItem setTarget:self];
    [self.statusMenu addItem:restartItem];

    [self.statusMenu addItem:[NSMenuItem separatorItem]];
    NSMenuItem *exitItem = [[NSMenuItem alloc] initWithTitle:@"Exit" action:@selector(exitApplication:) keyEquivalent:@"q"];
    [exitItem setTarget:self];
    [self.statusMenu addItem:exitItem];
}

- (void)handleStatusItemClick:(id)sender {
    (void)sender;
    NSEvent *event = [NSApp currentEvent];
    BOOL rightClick = ([event type] == NSEventTypeRightMouseUp);
    BOOL controlClick = ([event type] == NSEventTypeLeftMouseUp) && (([event modifierFlags] & NSEventModifierFlagControl) != 0);
    if (rightClick || controlClick) {
        [self showContextMenu];
        return;
    }
    [self openAdminUI:nil];
}

- (void)showContextMenu {
    [self.statusItem setMenu:self.statusMenu];
    [[self.statusItem button] performClick:nil];
    [self.statusItem setMenu:nil];
}

- (void)updateStatus:(NSString *)menuText tooltip:(NSString *)tooltipText {
    [self.statusMenuItem setTitle:menuText];
    [[self.statusItem button] setToolTip:tooltipText];
}

- (NSFileHandle *)openLogHandle {
    NSString *testRoot = AutoYouEnvValue(@"AUTOYOU_TEST_ROOT", nil);
    NSString *logDir = nil;
    if ([testRoot length] > 0) {
        logDir = [[testRoot stringByAppendingPathComponent:@"AutoYou"] stringByAppendingPathComponent:@"logs"];
    } else {
        logDir = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/AutoYou"];
    }

    NSFileManager *fileManager = [NSFileManager defaultManager];
    [fileManager createDirectoryAtPath:logDir withIntermediateDirectories:YES attributes:nil error:nil];
    NSString *logPath = [logDir stringByAppendingPathComponent:@"AutoYou-fallback-host.log"];
    if (![fileManager fileExistsAtPath:logPath]) {
        [fileManager createFileAtPath:logPath contents:nil attributes:nil];
    }
    NSFileHandle *handle = [NSFileHandle fileHandleForWritingAtPath:logPath];
    [handle seekToEndOfFile];
    return handle;
}

- (void)startBackend {
    if (self.terminationRequested || (self.backendTask != nil && [self.backendTask isRunning])) {
        return;
    }

    NSString *backendPath = nil;
    NSString *backendRoot = nil;
    if (!AutoYouFindBackend(self.resourcesDir, &backendPath, &backendRoot)) {
        [self updateStatus:@"Status: Backend missing" tooltip:@"AutoYou packaged backend was not found"];
        return;
    }
    self.backendPath = backendPath;
    self.backendRoot = backendRoot;
    self.logHandle = [self openLogHandle];

    NSTask *task = [[NSTask alloc] init];
    [task setExecutableURL:[NSURL fileURLWithPath:self.backendPath]];
    [task setCurrentDirectoryURL:[NSURL fileURLWithPath:self.backendRoot]];
    [task setArguments:@[
        @"--admin", self.adminPort,
        @"--ai-agent", self.aiPort,
        @"--auth", self.authPort
    ]];

    NSMutableDictionary<NSString *, NSString *> *environment = [[[NSProcessInfo processInfo] environment] mutableCopy];
    environment[@"AUTOYOU_PACKAGED_RUNTIME"] = @"1";
    environment[@"AUTOYOU_PACKAGED_RESOURCES_ROOT"] = self.backendRoot;
    environment[@"AUTOYOU_ADMIN_PORT"] = self.adminPort;
    environment[@"AUTOYOU_AI_PORT"] = self.aiPort;
    environment[@"AUTOYOU_AUTH_PORT"] = self.authPort;
    if (AutoYouEnvValue(@"AUTOYOU_BIND_HOST", nil) != nil || AutoYouEnvValue(@"AUTOYOU_HOST_BIND", nil) != nil) {
        environment[@"AUTOYOU_BIND_HOST"] = self.bindHost;
    } else {
        [environment removeObjectForKey:@"AUTOYOU_BIND_HOST"];
    }
    environment[@"AUTOYOU_SHUTDOWN_TOKEN"] = self.shutdownToken;
    environment[@"AUTOYOU_PARENT_PID"] = [NSString stringWithFormat:@"%d", (int)getpid()];
    environment[@"PYTHONDONTWRITEBYTECODE"] = @"1";
    [task setEnvironment:environment];

    if (self.logHandle != nil) {
        [task setStandardOutput:self.logHandle];
        [task setStandardError:self.logHandle];
    }

    __weak typeof(self) weakSelf = self;
    [task setTerminationHandler:^(NSTask *finishedTask) {
        dispatch_async(dispatch_get_main_queue(), ^{
            __strong typeof(weakSelf) strongSelf = weakSelf;
            if (strongSelf == nil) {
                return;
            }
            if (!strongSelf.terminationRequested) {
                [strongSelf updateStatus:@"Status: Initializing, please wait..." tooltip:@"AutoYou is initializing; retrying the server"];
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
                    if (!strongSelf.terminationRequested && (strongSelf.backendTask == nil || ![strongSelf.backendTask isRunning])) {
                        [strongSelf startBackend];
                    }
                });
            }
            (void)finishedTask;
        });
    }];

    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        [self updateStatus:@"Status: Initializing, please wait..." tooltip:[error localizedDescription] ?: @"AutoYou server could not be launched; retrying"];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            if (!self.terminationRequested && (self.backendTask == nil || ![self.backendTask isRunning])) {
                [self startBackend];
            }
        });
        return;
    }

    self.backendTask = task;
    self.backendProcessGroupID = (pid_t)[task processIdentifier];
    self.backendProcessGroupOwned = (setpgid(self.backendProcessGroupID, self.backendProcessGroupID) == 0);
    [self updateStatus:@"Status: Initializing, please wait..." tooltip:@"AutoYou is initializing; the Admin UI will open when ready"];
}

- (void)startStatusMonitor {
    self.statusTimer = [NSTimer scheduledTimerWithTimeInterval:2.0 target:self selector:@selector(pollStatus) userInfo:nil repeats:YES];
    [self pollStatus];
}

- (void)pollStatus {
    NSURL *statusURL = [self.adminURL URLByAppendingPathComponent:@"api/status"];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:statusURL];
    [request setHTTPMethod:@"GET"];
    [request setTimeoutInterval:1.5];

    NSURLSessionDataTask *task = [[NSURLSession sharedSession] dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        (void)data;
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.terminationRequested) {
                return;
            }
            NSInteger statusCode = 0;
            if ([response isKindOfClass:[NSHTTPURLResponse class]]) {
                statusCode = [(NSHTTPURLResponse *)response statusCode];
            }
            if (error == nil && statusCode >= 200 && statusCode < 300) {
                [self updateStatus:@"Status: Running" tooltip:@"AutoYou is running"];
                if (!self.openedAdminOnce) {
                    self.openedAdminOnce = YES;
                    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
                        [self openAdminUI:nil];
                    });
                }
            } else {
                [self updateStatus:@"Status: Initializing, please wait..." tooltip:@"AutoYou is initializing; the Admin UI will open when ready"];
            }
        });
    }];
    [task resume];
}

- (void)openAdminUI:(id)sender {
    (void)sender;
    [[NSWorkspace sharedWorkspace] openURL:self.adminURL];
}

- (void)openChat:(id)sender {
    (void)sender;
    [[NSWorkspace sharedWorkspace] openURL:self.chatURL];
}

- (void)restartAiAgent:(id)sender {
    (void)sender;
    NSURL *restartURL = [self.adminURL URLByAppendingPathComponent:@"ai-agent-server/restart"];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:restartURL];
    [request setHTTPMethod:@"POST"];
    [request setTimeoutInterval:10.0];
    [self updateStatus:@"Status: Restarting AI..." tooltip:@"Requesting AI Agent restart"];
    NSURLSessionDataTask *task = [[NSURLSession sharedSession] dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        (void)data;
        dispatch_async(dispatch_get_main_queue(), ^{
            NSInteger statusCode = 0;
            if ([response isKindOfClass:[NSHTTPURLResponse class]]) {
                statusCode = [(NSHTTPURLResponse *)response statusCode];
            }
            if (error == nil && statusCode >= 200 && statusCode < 300) {
                [self updateStatus:@"Status: AI Agent restarting..." tooltip:@"AI Agent restart requested"];
            } else {
                [self updateStatus:@"Status: Restart failed" tooltip:@"AI Agent restart request failed"];
            }
        });
    }];
    [task resume];
}

- (void)exitApplication:(id)sender {
    [NSApp terminate:sender];
}

- (void)requestBackendShutdown {
    if (self.backendTask == nil || ![self.backendTask isRunning]) {
        [NSApp replyToApplicationShouldTerminate:YES];
        return;
    }

    NSURL *shutdownURL = [self.adminURL URLByAppendingPathComponent:@"shutdown"];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:shutdownURL];
    [request setHTTPMethod:@"POST"];
    [request setTimeoutInterval:5.0];
    [request setValue:self.shutdownToken forHTTPHeaderField:@"x-autoyou-shutdown-token"];

    NSURLSessionDataTask *task = [[NSURLSession sharedSession] dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        (void)data;
        (void)response;
        (void)error;
        dispatch_async(dispatch_get_main_queue(), ^{
            [self finishTerminationAfterGracePeriod];
        });
    }];
    [task resume];
}

- (void)finishTerminationAfterGracePeriod {
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(8.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        if (self.backendTask != nil && [self.backendTask isRunning]) {
            if (self.backendProcessGroupOwned) {
                kill(-self.backendProcessGroupID, SIGTERM);
            } else {
                [self.backendTask terminate];
            }
        }
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(5.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            if (self.backendTask != nil && [self.backendTask isRunning]) {
                if (self.backendProcessGroupOwned) {
                    kill(-self.backendProcessGroupID, SIGKILL);
                }
                kill((pid_t)[self.backendTask processIdentifier], SIGKILL);
            }
            [self.logHandle closeFile];
            [NSApp replyToApplicationShouldTerminate:YES];
        });
    });
}

@end

static AutoYouFallbackAppDelegate *AutoYouDelegate;

int main(int argc, const char *argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        NSApplication *application = [NSApplication sharedApplication];
        AutoYouDelegate = [[AutoYouFallbackAppDelegate alloc] init];
        [application setDelegate:AutoYouDelegate];
        [application run];
    }
    return 0;
}
EOF

clang -arch "$TARGET_ARCH" -mmacosx-version-min=11.0 -fobjc-arc -framework Cocoa "$LAUNCHER_SOURCE" -o "${MACOS_DIR}/AutoYou"
chmod +x "${MACOS_DIR}/AutoYou"
rm -f "$LAUNCHER_SOURCE"

if command -v lipo >/dev/null 2>&1; then
    archs="$(lipo -archs "${MACOS_DIR}/AutoYou" 2>/dev/null || true)"
    if [[ " $archs " != *" $TARGET_ARCH "* ]]; then
        echo "Fallback host architecture mismatch: ${archs:-unknown}"
        exit 1
    fi
fi

echo "Intel fallback app bundle created: $APP_BUNDLE"
