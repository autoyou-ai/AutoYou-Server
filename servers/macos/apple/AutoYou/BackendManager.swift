import Foundation

#if canImport(Darwin)
import Darwin
#endif

private typealias RuntimePID = Int32

actor BackendManager {
    private var process: Process?
    private var shutdownToken: String?
    private var backendProcessGroupID: RuntimePID?
    private let onBackendExit: @Sendable (Int32) -> Void

    private let runtimeConfiguration: HostRuntimeConfiguration

    // Resolved once at init.
    // Backend resources live beside the backend executable itself:
    // - backend/AutoYou.dist/AutoYouServer
    // - backend/AutoYou.app/Contents/MacOS/AutoYouServer
    private let backendExecutableURL: URL?
    private let resourcesRootURL: URL?

    init(
        runtimeConfiguration: HostRuntimeConfiguration,
        onBackendExit: @escaping @Sendable (Int32) -> Void = { _ in }
    ) {
        self.runtimeConfiguration = runtimeConfiguration
        self.onBackendExit = onBackendExit

        if let resourcePath = Bundle.main.resourcePath {
            let backendLayouts: [(executablePath: String, resourcesRoot: String)] = [
                (
                    executablePath: "\(resourcePath)/backend/AutoYou.dist/AutoYouServer",
                    resourcesRoot: "\(resourcePath)/backend/AutoYou.dist"
                ),
                (
                    executablePath: "\(resourcePath)/backend/AutoYouServer.dist/AutoYouServer",
                    resourcesRoot: "\(resourcePath)/backend/AutoYouServer.dist"
                ),
                (
                    executablePath: "\(resourcePath)/backend/autoyou_app.dist/AutoYouServer",
                    resourcesRoot: "\(resourcePath)/backend/autoyou_app.dist"
                ),
                (
                    executablePath: "\(resourcePath)/backend/AutoYou.app/Contents/MacOS/AutoYouServer",
                    resourcesRoot: "\(resourcePath)/backend/AutoYou.app/Contents/MacOS"
                ),
                (
                    executablePath: "\(resourcePath)/backend/AutoYouServer.app/Contents/MacOS/AutoYouServer",
                    resourcesRoot: "\(resourcePath)/backend/AutoYouServer.app/Contents/MacOS"
                ),
                (
                    executablePath: "\(resourcePath)/backend/autoyou_app.app/Contents/MacOS/AutoYouServer",
                    resourcesRoot: "\(resourcePath)/backend/autoyou_app.app/Contents/MacOS"
                ),
            ]

            if let backendLayout = backendLayouts.first(where: {
                FileManager.default.fileExists(atPath: $0.executablePath)
            }) {
                self.backendExecutableURL = URL(fileURLWithPath: backendLayout.executablePath)
                self.resourcesRootURL = URL(fileURLWithPath: backendLayout.resourcesRoot, isDirectory: true)
            } else {
                self.backendExecutableURL = nil
                self.resourcesRootURL = nil
            }
        } else {
            self.backendExecutableURL = nil
            self.resourcesRootURL = nil
        }
    }

    @discardableResult
    func startBackendProcess() async -> Bool {
        guard process?.isRunning != true else {
            HostDiagnostics.log("Backend already running.", configuration: runtimeConfiguration)
            return true
        }

        guard let executableURL = backendExecutableURL,
              let resourcesRootURL = resourcesRootURL else {
            HostDiagnostics.logError(
                "Backend executable not found inside app bundle.",
                configuration: runtimeConfiguration
            )
            return false
        }

        HostDiagnostics.log("Starting backend: \(executableURL.path)", configuration: runtimeConfiguration)

        let token = UUID().uuidString
        let p = Process()
        self.backendProcessGroupID = nil
        p.executableURL = executableURL
        p.currentDirectoryURL = resourcesRootURL
        p.arguments = ["--run-server"]
        p.environment = buildEnvironment(resourcesRootURL: resourcesRootURL, shutdownToken: token)

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        p.standardOutput = stdoutPipe
        p.standardError = stderrPipe

        p.terminationHandler = { [cfg = runtimeConfiguration, onBackendExit] ended in
            HostDiagnostics.log(
                "Backend exited (code \(ended.terminationStatus)).",
                configuration: cfg
            )
            onBackendExit(ended.terminationStatus)
        }

        do {
            try p.run()
            #if canImport(Darwin)
            if Darwin.setpgid(p.processIdentifier, p.processIdentifier) == 0 {
                self.backendProcessGroupID = p.processIdentifier
            } else {
                self.backendProcessGroupID = nil
            }
            #endif
            self.process = p
            self.shutdownToken = token
            captureOutput(stdoutPipe: stdoutPipe, stderrPipe: stderrPipe)
            HostDiagnostics.log("Backend started (PID \(p.processIdentifier)).", configuration: runtimeConfiguration)
            return true
        } catch {
            HostDiagnostics.logError("Failed to start backend: \(error)", configuration: runtimeConfiguration)
            return false
        }
    }

    func stopBackendProcess() async {
        guard let process, process.isRunning else {
            self.process = nil
            self.shutdownToken = nil
            self.backendProcessGroupID = nil
            return
        }

        // 1. Graceful HTTP shutdown
        var req = URLRequest(url: runtimeConfiguration.adminURL.appendingPathComponent("shutdown"))
        req.httpMethod = "POST"
        req.timeoutInterval = 5
        if let token = shutdownToken {
            req.setValue(token, forHTTPHeaderField: "X-AutoYou-Shutdown-Token")
        }
        _ = try? await URLSession.shared.data(for: req)

        // 2. Wait up to 5 s
        for _ in 0..<50 {
            if !process.isRunning { break }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }

        // 3. SIGTERM
        if process.isRunning {
            #if canImport(Darwin)
            if let groupID = backendProcessGroupID {
                _ = Darwin.kill(-groupID, SIGTERM)
            } else {
                process.terminate()
            }
            #else
            process.terminate()
            #endif
            for _ in 0..<20 {
                if !process.isRunning { break }
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }

        // 4. SIGKILL
        if process.isRunning {
            #if canImport(Darwin)
            if let groupID = backendProcessGroupID {
                _ = Darwin.kill(-groupID, SIGKILL)
            }
            _ = Darwin.kill(process.processIdentifier, SIGKILL)
            #endif
        }

        if process.isRunning {
            for _ in 0..<20 {
                if !process.isRunning { break }
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }

        forceKillLingeringListeners(on: [
            runtimeConfiguration.adminPort,
            runtimeConfiguration.aiAgentPort,
            runtimeConfiguration.authPort,
            runtimeConfiguration.pagePort,
            8083,
        ])

        self.process = nil
        self.shutdownToken = nil
        self.backendProcessGroupID = nil
    }

    func isProcessRunning() -> Bool {
        process?.isRunning ?? false
    }

    // MARK: - Private helpers

    private func buildEnvironment(resourcesRootURL: URL, shutdownToken: String) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let explicitBindHost = ProcessInfo.processInfo.environment["AUTOYOU_BIND_HOST"]
            ?? ProcessInfo.processInfo.environment["AUTOYOU_HOST_BIND"]
        if explicitBindHost?.isEmpty == false {
            env["AUTOYOU_BIND_HOST"] = runtimeConfiguration.bindHost
        } else {
            env.removeValue(forKey: "AUTOYOU_BIND_HOST")
        }
        env["AUTOYOU_INSTANCE_NAME"]        = runtimeConfiguration.instanceName
        env["AUTOYOU_ADMIN_PORT"]           = "\(runtimeConfiguration.adminPort)"
        env["AUTOYOU_AI_PORT"]              = "\(runtimeConfiguration.aiAgentPort)"
        env["AUTOYOU_AI_AGENT_SERVER_PORT"] = "\(runtimeConfiguration.aiAgentPort)"
        env["AUTOYOU_AUTH_PORT"]            = "\(runtimeConfiguration.authPort)"
        env["AUTOYOU_PACKAGED_RUNTIME"]     = "1"
        env["AUTOYOU_PACKAGED_RESOURCES_ROOT"] = resourcesRootURL.path
        env["ADMIN_WEB_SERVICE_PORT"]       = "\(runtimeConfiguration.adminPort)"
        env["AI_AGENT_SERVER_PORT"]         = "\(runtimeConfiguration.aiAgentPort)"
        env["AUTH_SERVER_PORT"]             = "\(runtimeConfiguration.authPort)"
        env["AUTOYOU_SHUTDOWN_TOKEN"]       = shutdownToken
        env["AUTOYOU_PARENT_PID"]            = "\(ProcessInfo.processInfo.processIdentifier)"

        applyReleaseProfileEnvironment(resourcesRootURL: resourcesRootURL, environment: &env)

        let nodeExe = resourcesRootURL.appendingPathComponent("runtime/node/bin/node")
        if FileManager.default.fileExists(atPath: nodeExe.path) {
            env["AUTOYOU_NODE_EXE"] = nodeExe.path
        }

        let nodeServiceRoot = resourcesRootURL.appendingPathComponent("node")
        if FileManager.default.fileExists(atPath: nodeServiceRoot.path) {
            env["AUTOYOU_NODE_SERVICE_ROOT"] = nodeServiceRoot.path
        }

        let playwright = resourcesRootURL.appendingPathComponent("runtime/playwright")
        if FileManager.default.fileExists(atPath: playwright.path) {
            env["PLAYWRIGHT_BROWSERS_PATH"] = playwright.path
        }

        let bundledCACerts = resourcesRootURL.appendingPathComponent("certifi/cacert.pem")
        if FileManager.default.fileExists(atPath: bundledCACerts.path) {
            env["SSL_CERT_FILE"] = bundledCACerts.path
            env["REQUESTS_CA_BUNDLE"] = bundledCACerts.path
        }

        applyVendoredLibsodiumEnvironment(resourcesRootURL: resourcesRootURL, environment: &env)

        return env
    }

    private func applyVendoredLibsodiumEnvironment(resourcesRootURL: URL, environment: inout [String: String]) {
        #if arch(arm64)
        let archDirectory = "darwin-arm64"
        #elseif arch(x86_64)
        let archDirectory = "darwin-x86_64"
        #else
        let archDirectory = ""
        #endif

        guard !archDirectory.isEmpty else {
            return
        }

        let libsodiumDirectory = resourcesRootURL
            .appendingPathComponent("runtime_modules/shared/native/libsodium", isDirectory: true)
            .appendingPathComponent(archDirectory, isDirectory: true)
        let libsodiumDylib = libsodiumDirectory.appendingPathComponent("libsodium.dylib")
        guard FileManager.default.fileExists(atPath: libsodiumDylib.path) else {
            return
        }

        prependEnvironmentPath("DYLD_LIBRARY_PATH", value: libsodiumDirectory.path, environment: &environment)
    }

    private func prependEnvironmentPath(_ key: String, value: String, environment: inout [String: String]) {
        let existingEntries = (environment[key] ?? "")
            .split(separator: ":", omittingEmptySubsequences: true)
            .map(String.init)
        guard !existingEntries.contains(value) else {
            return
        }

        environment[key] = ([value] + existingEntries).joined(separator: ":")
    }

    private func applyReleaseProfileEnvironment(resourcesRootURL: URL, environment: inout [String: String]) {
        let metadata = readReleaseProfileMetadata(resourcesRootURL: resourcesRootURL)
        if let releaseProfile = metadata["releaseProfile"], !releaseProfile.isEmpty {
            environment["AUTOYOU_RELEASE_PROFILE"] = releaseProfile
        }

        if let dependencyProfile = metadata["dependencyProfile"], !dependencyProfile.isEmpty {
            environment["AUTOYOU_DEPENDENCY_PROFILE"] = dependencyProfile
        }

        if metadata["releaseProfile"]?.caseInsensitiveCompare("binary-default") == .orderedSame {
            environment["AUTOYOU_ENABLE_TELEGRAM_PARTNER"] = "true"
            environment["AUTOYOU_ENABLE_SIGNAL_PARTNER"] = "false"
            environment["AUTOYOU_ENABLE_WHATSAPP_PARTNER"] = "false"
            environment["AUTOYOU_ENABLE_CLOUD_PROVIDER_CONNECTORS"] = "false"
        }
    }

    private func readReleaseProfileMetadata(resourcesRootURL: URL) -> [String: String] {
        var candidates = [
            resourcesRootURL.appendingPathComponent("release-profile.json")
        ]

        if let appResourcePath = Bundle.main.resourcePath {
            candidates.append(URL(fileURLWithPath: appResourcePath, isDirectory: true).appendingPathComponent("release-profile.json"))
        }

        for candidate in candidates where FileManager.default.fileExists(atPath: candidate.path) {
            do {
                let data = try Data(contentsOf: candidate)
                guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    continue
                }

                var metadata: [String: String] = [:]
                for key in ["releaseProfile", "dependencyProfile"] {
                    if let value = object[key] as? String {
                        metadata[key] = value
                    }
                }
                return metadata
            } catch {
                HostDiagnostics.logError(
                    "Failed to read release metadata at \(candidate.path): \(error)",
                    configuration: runtimeConfiguration
                )
            }
        }

        return [:]
    }

    private func captureOutput(stdoutPipe: Pipe, stderrPipe: Pipe) {
        Self.capture(pipe: stdoutPipe, to: runtimeConfiguration.backendStdoutLogURL)
        Self.capture(pipe: stderrPipe, to: runtimeConfiguration.backendStderrLogURL)
    }

    private static func capture(pipe: Pipe, to url: URL) {
        DispatchQueue.global(qos: .utility).async {
            let handle = pipe.fileHandleForReading
            if !FileManager.default.fileExists(atPath: url.path) {
                FileManager.default.createFile(atPath: url.path, contents: nil)
            }
            guard let out = try? FileHandle(forWritingTo: url) else { return }
            try? out.seekToEnd()
            defer { try? out.close(); try? handle.close() }
            while true {
                let data = handle.availableData
                if data.isEmpty { break }
                try? out.write(contentsOf: data)
            }
        }
    }

    private func forceKillLingeringListeners(on ports: [Int]) {
        let uniquePorts = Array(Set(ports)).sorted()
        let lingeringPIDs = Set(uniquePorts.flatMap { listListeningPIDs(on: $0) })
        guard !lingeringPIDs.isEmpty else {
            return
        }

        HostDiagnostics.log(
            "Force-killing lingering backend listeners on ports \(uniquePorts): \(lingeringPIDs.sorted())",
            configuration: runtimeConfiguration
        )

        #if canImport(Darwin)
        for pid in lingeringPIDs {
            Darwin.kill(pid, SIGKILL)
        }
        #endif
    }

    private func listListeningPIDs(on port: Int) -> [RuntimePID] {
        let toolCandidates = ["/usr/sbin/lsof", "/usr/bin/lsof"]
        guard let toolPath = toolCandidates.first(where: { FileManager.default.fileExists(atPath: $0) }) else {
            return []
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: toolPath)
        process.arguments = ["-nP", "-t", "-iTCP:\(port)", "-sTCP:LISTEN"]

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        do {
            try process.run()
            let deadline = Date().addingTimeInterval(1.0)
            while process.isRunning && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.05)
            }
            if process.isRunning {
                process.terminate()
                return []
            }
            process.waitUntilExit()
        } catch {
            return []
        }

        guard process.terminationStatus == 0 || process.terminationStatus == 1 else {
            return []
        }

        let outputData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
        guard let output = String(data: outputData, encoding: .utf8) else {
            return []
        }

        return output
            .split(whereSeparator: \.isNewline)
            .compactMap { RuntimePID($0.trimmingCharacters(in: .whitespacesAndNewlines)) }
    }
}
