import AVFoundation
import Foundation

enum AudioExportFormat: String, CaseIterable, Identifiable {
    case m4a = "M4A"
    case wav = "WAV"
    case aiff = "AIFF"
    case caf = "CAF"

    var id: String { rawValue }

    var fileExtension: String {
        switch self {
        case .m4a: "m4a"
        case .wav: "wav"
        case .aiff: "aiff"
        case .caf: "caf"
        }
    }

    var fileType: AVFileType {
        switch self {
        case .m4a: .m4a
        case .wav: .wav
        case .aiff: .aiff
        case .caf: .caf
        }
    }

    var detail: String {
        switch self {
        case .m4a: "AAC High Quality"
        case .wav: "24-bit PCM"
        case .aiff: "24-bit PCM"
        case .caf: "32-bit Float PCM"
        }
    }

    func writerSettings(sampleRate: Double, channelCount: Int) -> [String: Any] {
        switch self {
        case .m4a:
            [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: sampleRate,
                AVNumberOfChannelsKey: channelCount,
                AVEncoderBitRateKey: channelCount == 1 ? 160_000 : 320_000,
                AVEncoderAudioQualityKey: AVAudioQuality.max.rawValue
            ]
        case .wav:
            [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: sampleRate,
                AVNumberOfChannelsKey: channelCount,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMBitDepthKey: 24,
                AVLinearPCMIsBigEndianKey: false,
                AVLinearPCMIsNonInterleaved: false
            ]
        case .aiff:
            [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: sampleRate,
                AVNumberOfChannelsKey: channelCount,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMBitDepthKey: 24,
                AVLinearPCMIsBigEndianKey: true,
                AVLinearPCMIsNonInterleaved: false
            ]
        case .caf:
            [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: sampleRate,
                AVNumberOfChannelsKey: channelCount,
                AVLinearPCMIsFloatKey: true,
                AVLinearPCMBitDepthKey: 32,
                AVLinearPCMIsBigEndianKey: false,
                AVLinearPCMIsNonInterleaved: false
            ]
        }
    }
}

@MainActor
final class AudioExtractionManager: ObservableObject {
    enum State: Equatable {
        case idle
        case ready(URL)
        case exporting
        case finished(URL)
        case failed(String)
    }

    @Published var state: State = .idle
    @Published var progress: Double = 0
    @Published var currentTitle = "选择一个本地视频"
    @Published var outputURL: URL?
    @Published var selectedFormat: AudioExportFormat = .m4a

    private var exportSession: AVAssetExportSession?
    private var progressTask: Task<Void, Never>?
    private var extractionTask: Task<Void, Never>?

    func selectVideo(_ url: URL) {
        state = .ready(url)
        progress = 0
        outputURL = nil
        currentTitle = url.deletingPathExtension().lastPathComponent
    }

    func reset() {
        exportSession?.cancelExport()
        progressTask?.cancel()
        extractionTask?.cancel()
        exportSession = nil
        progress = 0
        outputURL = nil
        currentTitle = "选择一个本地视频"
        state = .idle
    }

    func extractAudio() {
        guard case let .ready(inputURL) = state else { return }

        extractionTask?.cancel()
        extractionTask = Task { [weak self] in
            await self?.extractAudio(from: inputURL, format: self?.selectedFormat ?? .m4a)
        }
    }

    private func extractAudio(from inputURL: URL, format: AudioExportFormat) async {
        let sourceURL = copyIntoSandboxIfNeeded(inputURL)
        let asset = AVURLAsset(url: sourceURL)

        guard !asset.tracks(withMediaType: .audio).isEmpty else {
            state = .failed("视频中没有可提取的音频轨道")
            return
        }

        let outputURL = makeOutputURL(for: currentTitle, format: format)
        try? FileManager.default.removeItem(at: outputURL)

        state = .exporting
        progress = 0

        do {
            try await transcode(asset: asset, outputURL: outputURL, format: format)
            guard !Task.isCancelled else { return }
            progress = 1
            self.outputURL = outputURL
            state = .finished(outputURL)
        } catch is CancellationError {
            state = .idle
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    private nonisolated func transcode(asset: AVURLAsset, outputURL: URL, format: AudioExportFormat) async throws {
        guard let audioTrack = asset.tracks(withMediaType: .audio).first else {
            throw NSError(domain: "OrdinaryMuseAudioExtraction", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "视频中没有可提取的音频轨道"
            ])
        }

        let sourceFormat = Self.audioFormat(for: audioTrack, exportFormat: format)
        let reader = try AVAssetReader(asset: asset)
        let readerOutput = AVAssetReaderTrackOutput(track: audioTrack, outputSettings: [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sourceFormat.sampleRate,
            AVNumberOfChannelsKey: sourceFormat.channelCount,
            AVLinearPCMIsFloatKey: true,
            AVLinearPCMBitDepthKey: 32,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false
        ])
        readerOutput.alwaysCopiesSampleData = false
        guard reader.canAdd(readerOutput) else {
            throw NSError(domain: "OrdinaryMuseAudioExtraction", code: 2, userInfo: [
                NSLocalizedDescriptionKey: "无法读取视频音频轨道"
            ])
        }
        reader.add(readerOutput)

        let writer = try AVAssetWriter(outputURL: outputURL, fileType: format.fileType)
        let writerInput = AVAssetWriterInput(
            mediaType: .audio,
            outputSettings: format.writerSettings(
                sampleRate: sourceFormat.sampleRate,
                channelCount: sourceFormat.channelCount
            )
        )
        writerInput.expectsMediaDataInRealTime = false
        guard writer.canAdd(writerInput) else {
            throw NSError(domain: "OrdinaryMuseAudioExtraction", code: 3, userInfo: [
                NSLocalizedDescriptionKey: "当前设备不支持导出 \(format.rawValue)"
            ])
        }
        writer.add(writerInput)

        guard reader.startReading() else {
            throw reader.error ?? NSError(domain: "OrdinaryMuseAudioExtraction", code: 4, userInfo: [
                NSLocalizedDescriptionKey: "音频读取启动失败"
            ])
        }
        guard writer.startWriting() else {
            throw writer.error ?? NSError(domain: "OrdinaryMuseAudioExtraction", code: 5, userInfo: [
                NSLocalizedDescriptionKey: "音频写入启动失败"
            ])
        }
        writer.startSession(atSourceTime: .zero)

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let queue = DispatchQueue(label: "ordinary-muse.audio.extract")
            let durationSeconds = max(CMTimeGetSeconds(asset.duration), 0.001)
            var didFinish = false

            func finish(_ result: Result<Void, Error>) {
                guard !didFinish else { return }
                didFinish = true
                switch result {
                case .success:
                    continuation.resume()
                case let .failure(error):
                    continuation.resume(throwing: error)
                }
            }

            writerInput.requestMediaDataWhenReady(on: queue) {
                while writerInput.isReadyForMoreMediaData {
                    if Task.isCancelled {
                        reader.cancelReading()
                        writer.cancelWriting()
                        finish(.failure(CancellationError()))
                        return
                    }

                    guard let sampleBuffer = readerOutput.copyNextSampleBuffer() else {
                        writerInput.markAsFinished()
                        writer.finishWriting {
                            if writer.status == .completed {
                                finish(.success(()))
                            } else {
                                finish(.failure(writer.error ?? reader.error ?? NSError(
                                    domain: "OrdinaryMuseAudioExtraction",
                                    code: 6,
                                    userInfo: [NSLocalizedDescriptionKey: "音频导出失败"]
                                )))
                            }
                        }
                        return
                    }

                    let timestamp = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
                    let ratio = min(0.98, max(0, CMTimeGetSeconds(timestamp) / durationSeconds))
                    Task { @MainActor [weak self] in
                        self?.progress = ratio
                    }

                    if !writerInput.append(sampleBuffer) {
                        reader.cancelReading()
                        writer.cancelWriting()
                        finish(.failure(writer.error ?? NSError(
                            domain: "OrdinaryMuseAudioExtraction",
                            code: 7,
                            userInfo: [NSLocalizedDescriptionKey: "音频写入失败"]
                        )))
                        return
                    }
                }
            }
        }
    }

    private nonisolated static func audioFormat(for track: AVAssetTrack, exportFormat: AudioExportFormat) -> (sampleRate: Double, channelCount: Int) {
        guard let description = track.formatDescriptions.first,
              let basicDescription = CMAudioFormatDescriptionGetStreamBasicDescription(description as! CMAudioFormatDescription)
        else {
            return (44_100, 2)
        }

        let sampleRate = basicDescription.pointee.mSampleRate > 0 ? basicDescription.pointee.mSampleRate : 44_100
        let sourceChannelCount = basicDescription.pointee.mChannelsPerFrame > 0 ? Int(basicDescription.pointee.mChannelsPerFrame) : 2
        let normalizedChannelCount = min(max(sourceChannelCount, 1), 2)

        if exportFormat == .m4a {
            let aacSampleRate = sampleRate <= 44_100 ? 44_100.0 : 48_000.0
            return (aacSampleRate, normalizedChannelCount)
        }

        let normalizedSampleRate = (8_000...192_000).contains(sampleRate) ? sampleRate : 48_000
        return (normalizedSampleRate, normalizedChannelCount)
    }

    private func extractWithExportSession(sourceURL: URL, asset: AVURLAsset) {
        guard let session = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetAppleM4A) else {
            state = .failed("当前视频不支持音频导出")
            return
        }

        let outputURL = makeOutputURL(for: currentTitle, format: .m4a)

        try? FileManager.default.removeItem(at: outputURL)
        session.outputURL = outputURL
        session.outputFileType = .m4a
        session.shouldOptimizeForNetworkUse = true
        exportSession = session
        state = .exporting
        progress = 0

        progressTask?.cancel()
        progressTask = Task { [weak self, weak session] in
            while !Task.isCancelled {
                guard let session else { return }
                await MainActor.run {
                    self?.progress = Double(session.progress)
                }
                try? await Task.sleep(nanoseconds: 120_000_000)
            }
        }

        session.exportAsynchronously { [weak self] in
            Task { @MainActor in
                self?.progressTask?.cancel()
                switch session.status {
                case .completed:
                    self?.progress = 1
                    self?.outputURL = outputURL
                    self?.state = .finished(outputURL)
                case .failed:
                    self?.state = .failed(session.error?.localizedDescription ?? "音频导出失败")
                case .cancelled:
                    self?.state = .idle
                default:
                    break
                }
            }
        }
    }

    private func copyIntoSandboxIfNeeded(_ url: URL) -> URL {
        let didAccess = url.startAccessingSecurityScopedResource()
        defer {
            if didAccess { url.stopAccessingSecurityScopedResource() }
        }

        let destination = FileManager.default.temporaryDirectory
            .appendingPathComponent("ordinary-muse-source-\(UUID().uuidString)-\(url.lastPathComponent)")
        do {
            try FileManager.default.copyItem(at: url, to: destination)
            return destination
        } catch {
            return url
        }
    }

    private func makeOutputURL(for title: String, format: AudioExportFormat) -> URL {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let folder = documents.appendingPathComponent("Ordinary Muse/Extracted Audio", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

        let safeName = title
            .components(separatedBy: CharacterSet.alphanumerics.union(.whitespaces).inverted)
            .joined(separator: "-")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let baseName = safeName.isEmpty ? "ordinary-muse-audio" : safeName
        return folder
            .appendingPathComponent("\(baseName)-audio-\(Int(Date().timeIntervalSince1970))")
            .appendingPathExtension(format.fileExtension)
    }
}
