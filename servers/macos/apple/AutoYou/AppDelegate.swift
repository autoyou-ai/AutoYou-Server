import Cocoa

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var backendManager: BackendManager?
    private var statusMonitor: StatusMonitor?
    private var trayMenuController: TrayMenuController?
    private var terminationRequested = false
    private var backendRecoveryTask: Task<Void, Never>?
    private let maxBackendRecoveryAttempts = 3
    private let backendRecoveryWindow: TimeInterval = 60
    private var backendRecoveryAttempts = 0
    private var backendRecoveryWindowStartedAt: Date?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = HostRuntimeConfiguration.load()

        configureApplicationIcon()
        _ = NSApplication.shared.setActivationPolicy(.regular)
        HostDiagnostics.log(
            "Initializing \(configuration.instanceDisplayName) on \(configuration.adminURL.absoluteString)",
            configuration: configuration
        )

        let backendManager = BackendManager(
            runtimeConfiguration: configuration,
            onBackendExit: { [weak self] exitCode in
                Task { @MainActor in
                    self?.handleUnexpectedBackendExit(exitCode)
                }
            }
        )
        let statusMonitor = StatusMonitor(
            backendManager: backendManager,
            runtimeConfiguration: configuration
        )
        let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)

        self.backendManager = backendManager
        self.statusMonitor = statusMonitor
        self.statusItem = statusItem
        trayMenuController = TrayMenuController(
            statusItem: statusItem,
            statusMonitor: statusMonitor,
            runtimeConfiguration: configuration
        )

        Task {
            let started = await backendManager.startBackendProcess()
            statusMonitor.startMonitoring()
            trayMenuController?.openAdminUIWhenReady()
            if started {
                return
            } else {
                handleUnexpectedBackendExit(-1)
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        trayMenuController?.openAdminUI(nil)
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !terminationRequested else {
            return .terminateLater
        }

        terminationRequested = true
        trayMenuController?.showStoppingStatus()

        Task {
            await performGracefulShutdown()
        }

        return .terminateLater
    }

    private func performGracefulShutdown() async {
        backendRecoveryTask?.cancel()
        statusMonitor?.stopMonitoring()
        await backendManager?.stopBackendProcess()
        NSApplication.shared.reply(toApplicationShouldTerminate: true)
    }

    private func handleUnexpectedBackendExit(_ exitCode: Int32) {
        guard !terminationRequested else {
            return
        }

        guard exitCode != 0 else {
            HostDiagnostics.log(
                "Backend stopped normally (code 0); automatic recovery is not needed.",
                configuration: HostRuntimeConfiguration.load()
            )
            trayMenuController?.showOfflineStatus()
            return
        }

        let now = Date()
        if let startedAt = backendRecoveryWindowStartedAt,
           now.timeIntervalSince(startedAt) <= backendRecoveryWindow {
            // Keep counting failures from the same short-lived startup cycle.
        } else {
            backendRecoveryAttempts = 0
            backendRecoveryWindowStartedAt = now
        }

        guard backendRecoveryAttempts < maxBackendRecoveryAttempts else {
            HostDiagnostics.logError(
                "Backend failed \(backendRecoveryAttempts) times within \(Int(backendRecoveryWindow)) seconds; automatic recovery paused.",
                configuration: HostRuntimeConfiguration.load()
            )
            trayMenuController?.showBackendFailureStatus()
            return
        }

        backendRecoveryAttempts += 1
        let attempt = backendRecoveryAttempts
        let delayNanoseconds = UInt64(attempt) * 1_000_000_000
        HostDiagnostics.log(
            "Backend recovery attempt \(attempt)/\(maxBackendRecoveryAttempts) scheduled after exit code \(exitCode).",
            configuration: HostRuntimeConfiguration.load()
        )
        trayMenuController?.showInitializingStatus()
        backendRecoveryTask?.cancel()
        backendRecoveryTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: delayNanoseconds)
            guard let self, !self.terminationRequested, !Task.isCancelled,
                  let backendManager = self.backendManager else {
                return
            }

            if !(await backendManager.startBackendProcess()) {
                self.handleUnexpectedBackendExit(-1)
            }
        }
    }

    private func configureApplicationIcon() {
        guard let resourceURL = Bundle.main.resourceURL else {
            return
        }

        for resourceName in ["AppIcon.png", "AppLogo@1x.png", "TrayLogo.png"] {
            let candidateURL = resourceURL.appendingPathComponent(resourceName)
            if let image = NSImage(contentsOf: candidateURL) {
                NSApplication.shared.applicationIconImage = image
                return
            }
        }
    }
}
