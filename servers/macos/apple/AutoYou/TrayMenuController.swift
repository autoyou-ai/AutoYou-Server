import Cocoa

@MainActor
final class TrayMenuController: NSObject {
    private let statusItem: NSStatusItem
    private let statusMonitor: StatusMonitor
    private let runtimeConfiguration: HostRuntimeConfiguration
    private let menu = NSMenu()
    private var statusMenuItem: NSMenuItem?
    private var startupBrowserTask: Task<Void, Never>?
    private var startupBrowserOpened = false

    init(statusItem: NSStatusItem, statusMonitor: StatusMonitor, runtimeConfiguration: HostRuntimeConfiguration) {
        self.statusItem = statusItem
        self.statusMonitor = statusMonitor
        self.runtimeConfiguration = runtimeConfiguration
        super.init()

        configureStatusButton()
        rebuildMenu(statusText: "Status: Initializing, please wait...", isRunning: false)
        statusMonitor.registerStatusCallback { [weak self] status in
            self?.apply(status)
        }
    }

    func showStoppingStatus() {
        apply(BackendShellStatus(menuText: "Status: Stopping...", tooltipText: "AutoYou is stopping"))
    }

    func showInitializingStatus() {
        statusMonitor.clearBackendFailure()
        apply(.starting())
    }

    func showOfflineStatus() {
        statusMonitor.clearBackendFailure()
        apply(.offline())
    }

    func showBackendFailureStatus() {
        statusMonitor.showBackendFailure()
    }

    func openAdminUIWhenReady() {
        guard !startupBrowserOpened, startupBrowserTask == nil else {
            return
        }

        startupBrowserTask = Task { [weak self] in
            guard let self else { return }
            let deadline = Date().addingTimeInterval(120)
            while Date() < deadline && !Task.isCancelled {
                if await self.isAdminUIReachable() {
                    self.startupBrowserOpened = true
                    self.openAdminUI(nil)
                    self.startupBrowserTask = nil
                    return
                }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
            self.startupBrowserTask = nil
        }
    }

    private func configureStatusButton() {
        guard let button = statusItem.button else {
            return
        }

        if let image = loadTrayImage() {
            image.size = NSSize(width: 18, height: 18)
            image.isTemplate = true
            button.image = image
        } else if let image = NSImage(systemSymbolName: "desktopcomputer", accessibilityDescription: "AutoYou") {
            image.isTemplate = true
            button.image = image
        } else {
            button.title = "A"
        }

        button.target = self
        button.action = #selector(handleStatusItemClick(_:))
        button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        button.toolTip = "AutoYou is initializing; the Admin UI will open when ready"
    }

    private func loadTrayImage() -> NSImage? {
        guard let resourceURL = Bundle.main.resourceURL else {
            return nil
        }

        for resourceName in ["TrayLogo.png", "AppIcon.png", "AppLogo@1x.png"] {
            let candidateURL = resourceURL.appendingPathComponent(resourceName)
            if let image = NSImage(contentsOf: candidateURL) {
                return image
            }
        }

        return nil
    }

    private func rebuildMenu(statusText: String, isRunning: Bool) {
        menu.removeAllItems()

        statusMenuItem = NSMenuItem(title: statusText, action: nil, keyEquivalent: "")
        statusMenuItem?.isEnabled = false
        if let statusMenuItem {
            menu.addItem(statusMenuItem)
        }

        menu.addItem(NSMenuItem.separator())

        let adminItem = makeMenuItem(title: "Admin UI (Settings)", action: #selector(openAdminUI(_:)), keyEquivalent: "a")
        adminItem.attributedTitle = NSAttributedString(
            string: adminItem.title,
            attributes: [.font: NSFont.boldSystemFont(ofSize: NSFont.systemFontSize)]
        )
        menu.addItem(adminItem)

        if isRunning {
            menu.addItem(makeMenuItem(title: "Open Chat", action: #selector(openChat(_:)), keyEquivalent: "c"))
            menu.addItem(makeMenuItem(title: "Restart AI Agent", action: #selector(restartAiAgent(_:)), keyEquivalent: "r"))
            menu.addItem(makeMenuItem(title: "Agent Websites", action: #selector(openAgentWebsites(_:)), keyEquivalent: "w"))
        }

        menu.addItem(NSMenuItem.separator())
        menu.addItem(makeMenuItem(title: "Exit", action: #selector(exitApplication(_:)), keyEquivalent: "q"))
    }

    private func makeMenuItem(title: String, action: Selector, keyEquivalent: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: keyEquivalent)
        item.target = self
        return item
    }

    private func apply(_ status: BackendShellStatus) {
        let isRunning = status.menuText.caseInsensitiveCompare("Status: Running") == .orderedSame
        rebuildMenu(statusText: status.menuText, isRunning: isRunning)
        statusItem.button?.toolTip = status.tooltipText
    }

    private func showContextMenu() {
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil  // remove after display so left-click still opens Admin UI
    }

    @objc private func handleStatusItemClick(_ sender: Any?) {
        let currentEvent = NSApp.currentEvent
        let isRightClick = currentEvent?.type == .rightMouseUp
        let isControlClick = currentEvent?.type == .leftMouseUp && currentEvent?.modifierFlags.contains(.control) == true

        if isRightClick || isControlClick {
            showContextMenu()
            return
        }

        openAdminUI(sender)
    }

    @objc func openAdminUI(_ sender: Any?) {
        NSWorkspace.shared.open(runtimeConfiguration.adminURL)
    }

    private func isAdminUIReachable() async -> Bool {
        var request = URLRequest(url: runtimeConfiguration.adminURL)
        request.timeoutInterval = 2
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                return false
            }
            return (200..<500).contains(httpResponse.statusCode)
        } catch {
            return false
        }
    }

    @objc func openChat(_ sender: Any?) {
        NSWorkspace.shared.open(runtimeConfiguration.chatURL)
    }

    @objc func openAgentWebsites(_ sender: Any?) {
        let port = statusMonitor.activePagePort
        let url = URL(string: "http://\(runtimeConfiguration.reachableHost):\(port)/agent-websites")!
        NSWorkspace.shared.open(url)
    }

    @objc func restartAiAgent(_ sender: Any?) {
        let restartURL = runtimeConfiguration.adminURL.appendingPathComponent("ai-agent-server/restart")
        var request = URLRequest(url: restartURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        statusMenuItem?.title = "Status: Restarting AI…"
        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            DispatchQueue.main.async {
                if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 {
                    self?.statusMenuItem?.title = "Status: AI Agent restarting…"
                } else {
                    self?.statusMenuItem?.title = "Status: Restart failed"
                }
            }
        }.resume()
    }

    @objc private func exitApplication(_ sender: Any?) {
        startupBrowserTask?.cancel()
        showStoppingStatus()
        statusMonitor.stopMonitoring()
        NSApplication.shared.terminate(sender)
    }
}
