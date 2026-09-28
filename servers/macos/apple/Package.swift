// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "AutoYou",
    platforms: [
        .macOS(.v11)
    ],
    dependencies: [
    ],
    targets: [
        .executableTarget(
            name: "AutoYou",
            dependencies: [],
            path: "AutoYou",
            sources: [
                "main.swift",
                "AppDelegate.swift",
                "BackendManager.swift",
                "HostDiagnostics.swift",
                "HostRuntimeConfiguration.swift",
                "StatusMonitor.swift",
                "TrayMenuController.swift"
            ]
        )
    ]
)
