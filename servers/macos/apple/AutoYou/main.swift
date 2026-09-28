import Cocoa

// AutoYou macOS Host - application entry point.
//
// Swift 6 treats top-level synchronous code in main.swift as nonisolated,
// but the process always starts on the main thread.
// MainActor.assumeIsolated() asserts that fact to the compiler so we can
// legally call the @MainActor-isolated AppDelegate initialiser.
// app.run() is called inside the block so the delegate stays alive for the
// entire duration of the event loop.
MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.run()
}
