import Foundation

struct BackendShellStatus: Equatable {
    let menuText: String
    let tooltipText: String

    static func offline() -> BackendShellStatus {
        BackendShellStatus(menuText: "Status: Offline", tooltipText: "AutoYou is offline")
    }

    static func starting() -> BackendShellStatus {
        BackendShellStatus(
            menuText: "Status: Initializing, please wait...",
            tooltipText: "AutoYou is initializing; the Admin UI will open when ready"
        )
    }

    static func running() -> BackendShellStatus {
        BackendShellStatus(menuText: "Status: Running", tooltipText: "AutoYou is running")
    }

    static func waitingForPassword() -> BackendShellStatus {
        BackendShellStatus(menuText: "Status: Waiting for password", tooltipText: "AutoYou is waiting for the server password")
    }

    static func backendNeedsAttention() -> BackendShellStatus {
        BackendShellStatus(
            menuText: "Status: Backend needs attention",
            tooltipText: "AutoYou could not start. Review the log, resolve credential or configuration access, then restart AutoYou."
        )
    }
}

private struct StartupStatusPayload: Decodable {
    let status: String?
    let headline: String?
    let initialized: Bool?
    let instance: InstancePayload?
}

private struct InstancePayload: Decodable {
    let ports: PortsPayload?
}

private struct PortsPayload: Decodable {
    let admin: Int?
    let ai_agent: Int?
    let auth: Int?
    let page: Int?
}

@MainActor
final class StatusMonitor {
    private let backendManager: BackendManager
    private let runtimeConfiguration: HostRuntimeConfiguration
    private var monitorTimer: Timer?
    private(set) var activePagePort: Int
    private var lastStatus = BackendShellStatus.starting()
    private var statusCallback: ((BackendShellStatus) -> Void)?
    private var terminalBackendFailure = false

    let pollInterval: TimeInterval = 3.0

    init(backendManager: BackendManager, runtimeConfiguration: HostRuntimeConfiguration) {
        self.backendManager = backendManager
        self.runtimeConfiguration = runtimeConfiguration
        self.activePagePort = runtimeConfiguration.pagePort
    }

    func startMonitoring() {
        stopMonitoring()
        monitorTimer = Timer.scheduledTimer(withTimeInterval: pollInterval, repeats: true) { [weak self] _ in
            Task {
                await self?.checkStatus()
            }
        }

        Task {
            await checkStatus()
        }
    }

    func stopMonitoring() {
        monitorTimer?.invalidate()
        monitorTimer = nil
    }

    func registerStatusCallback(_ callback: @escaping (BackendShellStatus) -> Void) {
        statusCallback = callback
        callback(lastStatus)
    }

    func showBackendFailure() {
        terminalBackendFailure = true
        updateStatus(.backendNeedsAttention())
    }

    func clearBackendFailure() {
        terminalBackendFailure = false
    }

    private func checkStatus() async {
        if terminalBackendFailure {
            if await backendManager.isProcessRunning() {
                terminalBackendFailure = false
            } else {
                return
            }
        }
        if let startupPayload = await fetchStartupStatus() {
            if let pagePort = startupPayload.instance?.ports?.page {
                self.activePagePort = pagePort
            }
            updateStatus(interpret(startupPayload))
            return
        }

        if await isAdminStatusReachable() {
            updateStatus(.running())
            return
        }

        updateStatus(await backendManager.isProcessRunning() ? .starting() : .offline())
    }

    private func fetchStartupStatus() async -> StartupStatusPayload? {
        var request = URLRequest(url: runtimeConfiguration.adminURL.appendingPathComponent("api/login-startup-status"))
        request.timeoutInterval = 2

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse, (200..<300).contains(httpResponse.statusCode) else {
                return nil
            }
            return try JSONDecoder().decode(StartupStatusPayload.self, from: data)
        } catch {
            return nil
        }
    }

    private func isAdminStatusReachable() async -> Bool {
        var request = URLRequest(url: runtimeConfiguration.adminURL.appendingPathComponent("api/status"))
        request.timeoutInterval = 2

        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse else {
                return false
            }
            return (200..<300).contains(httpResponse.statusCode)
        } catch {
            return false
        }
    }

    private func interpret(_ payload: StartupStatusPayload) -> BackendShellStatus {
        let headline = (payload.headline ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedStatus = (payload.status ?? "").trimmingCharacters(in: .whitespacesAndNewlines)

        if headline.range(of: "waiting for password", options: .caseInsensitive) != nil {
            return .waitingForPassword()
        }

        if payload.initialized == true || normalizedStatus.caseInsensitiveCompare("complete") == .orderedSame {
            return .running()
        }

        if normalizedStatus.caseInsensitiveCompare("error") == .orderedSame, !headline.isEmpty {
            return BackendShellStatus(menuText: "Status: \(headline)", tooltipText: "AutoYou: \(headline)")
        }

        return .starting()
    }

    private func updateStatus(_ newStatus: BackendShellStatus) {
        if newStatus != lastStatus {
            lastStatus = newStatus
            statusCallback?(newStatus)
        }
    }
}
