// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

@available(macOS 13.0, *)
final class MacAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            throw NSError(domain: "AutoYouAudioCapture", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "No display is available for audio capture"])
        }
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48_000
        config.channelCount = 1
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let capture = SCStream(filter: SCContentFilter(display: display, excludingWindows: []),
                               configuration: config, delegate: self)
        try capture.addStreamOutput(self, type: .audio,
                                    sampleHandlerQueue: DispatchQueue(label: "com.autoyou.system-audio"))
        try await capture.startCapture()
        stream = capture
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid else { return }
        try? sampleBuffer.withAudioBufferList { list, _ in
            guard let description = sampleBuffer.formatDescription?.audioStreamBasicDescription,
                  let format = AVAudioFormat(standardFormatWithSampleRate: description.mSampleRate,
                                             channels: description.mChannelsPerFrame),
                  let buffer = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: list.unsafePointer),
                  let channels = buffer.floatChannelData else { return }
            let channelCount = Int(buffer.format.channelCount)
            let frames = Int(buffer.frameLength)
            guard channelCount > 0, frames > 0 else { return }
            var pcm = Data(count: frames * MemoryLayout<Int16>.size)
            pcm.withUnsafeMutableBytes { raw in
                let output = raw.bindMemory(to: Int16.self)
                for frame in 0..<frames {
                    var sample: Float = 0
                    for channel in 0..<channelCount {
                        sample += channels[channel][frame]
                    }
                    sample /= Float(channelCount)
                    output[frame] = Int16((max(-1, min(1, sample.isFinite ? sample : 0)) * 32767).rounded())
                }
            }
            FileHandle.standardOutput.write(pcm)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write(Data("ScreenCaptureKit stopped: \(error)\n".utf8))
        exit(1)
    }
}

@main
enum AutoYouAudioCapture {
    static func main() async {
        guard #available(macOS 13.0, *) else {
            FileHandle.standardError.write(Data("macOS 13 or newer is required\n".utf8))
            exit(1)
        }
        let capture = MacAudioCapture()
        do {
            try await capture.start()
            withExtendedLifetime(capture) { dispatchMain() }
        } catch {
            FileHandle.standardError.write(Data("ScreenCaptureKit could not start: \(error)\n".utf8))
            exit(1)
        }
    }
}
