import Foundation

enum HostDiagnostics {
    static func log(_ message: String, configuration: HostRuntimeConfiguration) {
        append(message: "[INFO] \(timestamp()) \(message)\n", to: configuration.hostLogURL)
    }

    static func logError(_ message: String, configuration: HostRuntimeConfiguration) {
        append(message: "[ERROR] \(timestamp()) \(message)\n", to: configuration.hostErrorLogURL)
    }

    private static func append(message: String, to url: URL) {
        do {
            let data = Data(message.utf8)

            if FileManager.default.fileExists(atPath: url.path) {
                let handle = try FileHandle(forWritingTo: url)
                defer { try? handle.close() }
                try handle.seekToEnd()
                try handle.write(contentsOf: data)
            } else {
                try data.write(to: url, options: .atomic)
            }
        } catch {
            fputs("AutoYou logging failure: \(error)\n", stderr)
        }
    }

    private static func timestamp() -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: Date())
    }
}