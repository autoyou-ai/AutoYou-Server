import Foundation

struct HostRuntimeConfiguration {
    let instanceName: String
    let bindHost: String
    let reachableHost: String
    let adminPort: Int
    let aiAgentPort: Int
    let authPort: Int
    let pagePort: Int

    let appSupportDirectory: URL
    let logsDirectory: URL

    static func load() -> HostRuntimeConfiguration {
        let environment = ProcessInfo.processInfo.environment
        let instanceName = HostRuntimeConfiguration.readString(
            environment: environment,
            keys: ["AUTOYOU_INSTANCE_NAME"],
            defaultValue: "default"
        )

        let bindHost = HostRuntimeConfiguration.readString(
            environment: environment,
            keys: ["AUTOYOU_BIND_HOST", "AUTOYOU_HOST_BIND"],
            defaultValue: "127.0.0.1"
        )

        let adminPort = HostRuntimeConfiguration.readInt(
            environment: environment,
            keys: ["AUTOYOU_ADMIN_PORT", "ADMIN_WEB_SERVICE_PORT"],
            defaultValue: 8001
        )

        let aiAgentPort = HostRuntimeConfiguration.readInt(
            environment: environment,
            keys: ["AUTOYOU_AI_PORT", "AUTOYOU_AI_AGENT_SERVER_PORT", "AI_AGENT_SERVER_PORT"],
            defaultValue: 8081
        )

        let authPort = HostRuntimeConfiguration.readInt(
            environment: environment,
            keys: ["AUTOYOU_AUTH_PORT", "AUTH_SERVER_PORT"],
            defaultValue: 8002
        )

        let pagePort = HostRuntimeConfiguration.readInt(
            environment: environment,
            keys: ["AUTOYOU_PAGE_PORT", "PAGE_SERVICE_PORT"],
            defaultValue: 8067
        )

        let appSupportDirectory: URL
        if let testRoot = environment["AUTOYOU_TEST_ROOT"]?.trimmingCharacters(in: .whitespacesAndNewlines),
           !testRoot.isEmpty {
            var testRootURL = URL(
                fileURLWithPath: (testRoot as NSString).expandingTildeInPath,
                isDirectory: true
            )
            if testRootURL.lastPathComponent.caseInsensitiveCompare("AutoYou") != .orderedSame {
                testRootURL.appendPathComponent("AutoYou", isDirectory: true)
            }
            appSupportDirectory = testRootURL
        } else {
            let baseAppSupport = (try? FileManager.default.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            )) ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support", isDirectory: true)
            appSupportDirectory = baseAppSupport.appendingPathComponent("AutoYou", isDirectory: true)
        }
        let logsDirectory = appSupportDirectory.appendingPathComponent("logs", isDirectory: true)
        let reachableHost = HostRuntimeConfiguration.normalizeReachableHost(bindHost)

        try? FileManager.default.createDirectory(
            at: logsDirectory,
            withIntermediateDirectories: true,
            attributes: nil
        )

        return HostRuntimeConfiguration(
            instanceName: HostRuntimeConfiguration.normalizeInstanceName(instanceName),
            bindHost: bindHost,
            reachableHost: reachableHost,
            adminPort: adminPort,
            aiAgentPort: aiAgentPort,
            authPort: authPort,
            pagePort: pagePort,
            appSupportDirectory: appSupportDirectory,
            logsDirectory: logsDirectory
        )
    }

    var instanceDisplayName: String {
        instanceName.caseInsensitiveCompare("default") == .orderedSame ? "AutoYou" : "AutoYou [\(instanceName)]"
    }

    var portSummary: String {
        "admin \(adminPort), ai \(aiAgentPort), auth \(authPort)"
    }

    var adminURL: URL {
        URL(string: "http://\(reachableHost):\(adminPort)/")!
    }

    var aiAgentURL: URL {
        var components = URLComponents()
        components.scheme = "http"
        components.host = "localhost"
        components.port = aiAgentPort
        components.path = "/dev-ui/"
        components.queryItems = [URLQueryItem(name: "app", value: "autoyou_agents")]
        return components.url!
    }

    var chatURL: URL {
        URL(string: "http://\(reachableHost):\(aiAgentPort)/")!
    }

    var backendStdoutLogURL: URL {
        logsDirectory.appendingPathComponent(buildBackendLogFileName(isErrorLog: false))
    }

    var backendStderrLogURL: URL {
        logsDirectory.appendingPathComponent(buildBackendLogFileName(isErrorLog: true))
    }

    var hostLogURL: URL {
        logsDirectory.appendingPathComponent("desktop_host.log")
    }

    var hostErrorLogURL: URL {
        logsDirectory.appendingPathComponent("desktop_host_error.log")
    }

    private func buildBackendLogFileName(isErrorLog: Bool) -> String {
        if instanceName.caseInsensitiveCompare("default") == .orderedSame && adminPort == 8001 {
            return isErrorLog ? "server_mode_error.log" : "server_mode.log"
        }

        let prefix = isErrorLog ? "server_mode_error" : "server_mode"
        return "\(prefix).\(sanitizeFileNameComponent(instanceName)).admin-\(adminPort).log"
    }

    private func sanitizeFileNameComponent(_ value: String) -> String {
        let invalidCharacters = CharacterSet(charactersIn: "/:\\?%*|\"<>")
            .union(.controlCharacters)
            .union(.illegalCharacters)
        let lowered = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let sanitized = lowered.unicodeScalars.map { scalar -> Character in
            if invalidCharacters.contains(scalar) {
                return "-"
            }
            if CharacterSet.whitespacesAndNewlines.contains(scalar) {
                return "-"
            }
            return Character(scalar)
        }

        let normalized = String(sanitized).trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        return normalized.isEmpty ? "default" : normalized
    }

    private static func normalizeInstanceName(_ rawValue: String) -> String {
        let candidate = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        return candidate.isEmpty ? "default" : candidate
    }

    private static func normalizeReachableHost(_ bindHost: String) -> String {
        switch bindHost {
        case "0.0.0.0", "::":
            return "127.0.0.1"
        default:
            return bindHost
        }
    }

    private static func readString(environment: [String: String], keys: [String], defaultValue: String) -> String {
        for key in keys {
            if let value = environment[key]?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty {
                return value
            }
        }

        return defaultValue
    }

    private static func readInt(environment: [String: String], keys: [String], defaultValue: Int) -> Int {
        for key in keys {
            if let value = environment[key], let parsed = Int(value.trimmingCharacters(in: .whitespacesAndNewlines)) {
                return parsed
            }
        }

        return defaultValue
    }
}
