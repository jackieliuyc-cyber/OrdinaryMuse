import Accelerate
import AVFoundation
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

private enum MuseTab {
    case extract
    case split
}

enum SeparationMode: String, CaseIterable {
    case local = "本地分离"
    case demucs = "模型分离"
}

private struct RecentJob: Identifiable {
    let id = UUID()
    let title: String
    let subtitle: String
    let progress: Double
    let highlighted: Bool
}

private struct StemTrack: Identifiable {
    let id = UUID()
    let title: String
    let color: Color
    let seed: Double
    var volume: Double
    var audioURL: URL?
}

struct ContentView: View {
    @StateObject private var extractor = AudioExtractionManager()
    @StateObject private var separator = StemSeparationManager()
    @StateObject private var demucsService = SeparationService()
    @State private var selectedTab: MuseTab = .extract
    @State private var separationMode: SeparationMode = .local
    @State private var showServerSettings = false
    @State private var showVideoImporter = false
    @State private var showAudioImporter = false
    @State private var splitTitle = "等待上传音频"
    @State private var extractedAudioURL: URL?
    @State private var splitAudioURL: URL?
    @State private var audioPlayer: AVAudioPlayer?
    @State private var isPlaying = false
    @State private var playbackProgress: Double = 0
    @State private var playbackTimer: Timer?
    @State private var audioDuration: TimeInterval = 0
    @State private var playingStemID: UUID?
    @State private var separationDone = false
    @State private var audioEngine: AVAudioEngine?
    @State private var stemPlayers: [AVAudioPlayerNode] = []
    @State private var stemBuffers: [AVAudioPCMBuffer] = []
    @State private var mixFramePosition: Int = 0
    @State private var showExportSheet = false
    @State private var exportURL: URL?
    @State private var showExportError = false

    @State private var recentJobs: [RecentJob] = [
        .init(title: "Live Rooftop Clip Audio Extract", subtitle: "Queued", progress: 0, highlighted: true),
        .init(title: "M500000ZHIUv4gWckF", subtitle: "Queued", progress: 0, highlighted: false),
        .init(title: "Golden Hour Session", subtitle: "Ready", progress: 1, highlighted: false)
    ]

    @State private var stems: [StemTrack] = [
        .init(title: "人声", color: .museBlue, seed: 0.15, volume: 0.88),
        .init(title: "鼓点", color: .museGold, seed: 0.38, volume: 0.88),
        .init(title: "贝斯", color: .museMint, seed: 0.61, volume: 0.88),
        .init(title: "其他", color: .museViolet, seed: 0.83, volume: 0.88)
    ]

    var body: some View {
        ZStack(alignment: .bottom) {
            MuseBackground()
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(spacing: 24) {
                    if selectedTab == .extract {
                        extractionView
                    } else {
                        splitView
                    }
                }
                .padding(.horizontal, 22)
                .padding(.top, 18)
                .padding(.bottom, 118)
            }

            MuseTabBar(selectedTab: $selectedTab)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .statusBarHidden(true)
        .preferredColorScheme(.dark)
        .fileImporter(
            isPresented: $showAudioImporter,
            allowedContentTypes: [.audio, .mp3, .wav, .mpeg4Audio],
            allowsMultipleSelection: false
        ) { result in
            handleAudioImport(result)
        }
        .sheet(isPresented: $showVideoImporter) {
            VideoPhotoPicker { url in
                extractor.selectVideo(url)
            }
            .ignoresSafeArea()
        }
        .onChange(of: extractor.state) { _, state in
            if case let .finished(url) = state {
                extractedAudioURL = url
                splitTitle = extractor.currentTitle
                loadAudio(from: url)
                recentJobs.insert(
                    RecentJob(title: extractor.currentTitle, subtitle: "Extracted M4A", progress: 1, highlighted: true),
                    at: 0
                )
            }
        }
        .onDisappear {
            stopPlayback()
            teardownMixEngine()
        }
        .sheet(isPresented: $showExportSheet) {
            if let url = exportURL {
                ShareSheet(url: url)
                    .presentationDetents([.medium])
            }
        }
        .alert("导出混音失败", isPresented: $showExportError) {
            Button("确定", role: .cancel) {}
        } message: {
            Text("无法生成混音文件，请重新分离后再试")
        }
    }

    private var extractionView: some View {
        VStack(spacing: 24) {
            HeaderBlock(title: "视频转音频", subtitle: "从视频文件中提取高保真音频轨道")

            ProgressOrb(progress: extractor.progress)
                .frame(height: 144)

            GlassPanel {
                VStack(alignment: .leading, spacing: 14) {
                    Text(extractor.currentTitle)
                        .font(.system(size: 16, weight: .bold, design: .rounded))
                        .foregroundStyle(Color.museText)
                        .lineLimit(1)

                    Text(extractionStatus)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Color.museMuted)

                    VStack(alignment: .leading, spacing: 8) {
                        Picker("导出格式", selection: $extractor.selectedFormat) {
                            ForEach(AudioExportFormat.allCases) { format in
                                Text(format.rawValue).tag(format)
                            }
                        }
                        .pickerStyle(.segmented)
                        .disabled(!canChangeExtractFormat)

                        Text("格式 \(extractor.selectedFormat.rawValue) · \(extractor.selectedFormat.detail)")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Color.museMuted)
                    }

                    Divider()
                        .overlay(Color.white.opacity(0.07))
                        .padding(.top, 6)

                    HStack(spacing: 14) {
                        GeometryReader { proxy in
                            Capsule()
                                .fill(.white.opacity(0.05))
                                .overlay(alignment: .leading) {
                                    Capsule()
                                        .fill(LinearGradient.museBlueGold)
                                        .frame(width: max(8, proxy.size.width * extractor.progress))
                                }
                        }
                        .frame(height: 8)

                        Text("\(Int(extractor.progress * 100))%")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(Color.museMuted)
                            .monospacedDigit()
                    }
                }
            }

            VStack(spacing: 14) {
                Button {
                    showVideoImporter = true
                } label: {
                    MuseButtonLabel(title: "上传视频", systemImage: "square.and.arrow.up", foreground: .black)
                }
                .buttonStyle(FilledMuseButtonStyle(fill: AnyShapeStyle(Color.museGold)))

                HStack(spacing: 14) {
                    Button {
                        extractor.extractAudio()
                    } label: {
                        MuseButtonLabel(title: "开始", systemImage: "play", foreground: .white)
                    }
                    .buttonStyle(FilledMuseButtonStyle(fill: AnyShapeStyle(Color.museBlue)))
                    .disabled(!canExtract)
                    .opacity(canExtract ? 1 : 0.42)

                    Button {
                        extractor.reset()
                        extractedAudioURL = nil
                    } label: {
                        Text("刷新")
                            .font(.system(size: 14, weight: .bold))
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(OutlineMuseButtonStyle())
                }

                if let url = extractedAudioURL {
                    HStack(spacing: 14) {
                        Button {
                            exportURL = url
                            showExportSheet = true
                        } label: {
                            MuseButtonLabel(title: "分享音频", systemImage: "square.and.arrow.up", foreground: .black)
                        }
                        .buttonStyle(FilledMuseButtonStyle(fill: AnyShapeStyle(Color.museGold)))

                        Button {
                            splitTitle = extractor.currentTitle
                            loadAudio(from: url)
                            withAnimation(.spring(response: 0.32, dampingFraction: 0.82)) {
                                selectedTab = .split
                            }
                        } label: {
                            MuseButtonLabel(title: "去分离", systemImage: "arrow.triangle.branch", foreground: .white)
                        }
                        .buttonStyle(FilledMuseButtonStyle(fill: AnyShapeStyle(Color.museBlue)))
                    }
                }
            }

            VStack(alignment: .leading, spacing: 14) {
                Text("最近提取")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(Color.museMuted)

                ForEach(recentJobs) { job in
                    RecentJobRow(job: job)
                }
            }
        }
    }

    private var splitView: some View {
        VStack(spacing: 20) {
            HeaderBlock(title: "音轨分离", subtitle: "分离单独的乐器和人声")

            Picker("分离模式", selection: $separationMode) {
                ForEach(SeparationMode.allCases, id: \.self) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 4)

            if separationMode == .demucs {
                GlassPanel {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Demucs 服务器")
                                .font(.system(size: 13, weight: .bold))
                                .foregroundStyle(Color.museText)
                            Text("基于 Demucs v4 模型，分离质量更高")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(Color.museMuted)
                        }
                        Spacer()
                        Button {
                            showServerSettings.toggle()
                        } label: {
                            Image(systemName: "gearshape")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(Color.museText)
                        }
                    }

                    if showServerSettings {
                        VStack(spacing: 8) {
                            HStack {
                                Text("服务器地址")
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(Color.museMuted)
                                Spacer()
                            }
                            TextField("http://12630gj35tf95.vicp.fun", text: $demucsService.serverURL)
                                .font(.system(size: 13, weight: .medium, design: .monospaced))
                                .foregroundStyle(Color.museText)
                                .padding(10)
                                .background(
                                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                                        .fill(Color.white.opacity(0.06))
                                )
                                .overlay {
                                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                                        .stroke(Color.white.opacity(0.08), lineWidth: 1)
                                }
                                .autocorrectionDisabled()
                                .textInputAutocapitalization(.never)

                            HStack(spacing: 8) {
                                ServerPresetButton(title: "本机", icon: "desktopcomputer") {
                                    demucsService.applyServerURL(demucsService.localURL)
                                }

                                ServerPresetButton(title: "局域网", icon: "wifi") {
                                    demucsService.applyServerURL(demucsService.lanURLPlaceholder)
                                }

                                ServerPresetButton(title: "粘贴隧道", icon: "link") {
                                    if let value = UIPasteboard.general.string, !value.isEmpty {
                                        demucsService.applyServerURL(value)
                                    }
                                }
                            }

                            Button {
                                Task {
                                    demucsService.applyServerURL(demucsService.serverURL)
                                    let ok = await demucsService.healthCheck()
                                    demucsService.statusText = ok ? "服务器连接正常" : "无法连接服务器"
                                }
                            } label: {
                                HStack(spacing: 6) {
                                    Image(systemName: "antenna.radiowaves.left.and.right")
                                        .font(.system(size: 12, weight: .semibold))
                                    Text("测试连接")
                                        .font(.system(size: 12, weight: .bold))
                                }
                                .foregroundStyle(Color.museText)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 10)
                                .background(
                                    Capsule().fill(Color.white.opacity(0.08))
                                )
                            }

                            if !demucsService.statusText.isEmpty {
                                Text(demucsService.statusText)
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(
                                        demucsService.statusText.contains("正常") ? Color.museMint : Color.museGold
                                    )
                            }
                        }
                        .padding(.top, 4)
                    }
                }
            }

            GlassPanel {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(splitTitle)
                                .font(.system(size: 15, weight: .bold, design: .rounded))
                                .foregroundStyle(Color.museText)
                            Text(splitStatus)
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(Color.museMuted)
                        }

                        Spacer()

                        Button { togglePlayback() } label: {
                            Image(systemName: isPlaying && playingStemID == nil ? "pause" : "play")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(splitAudioURL != nil ? Color.museText : Color.museMuted)
                        }
                        .disabled(splitAudioURL == nil || currentlyProcessing)
                    }

                    ForEach(stems.indices, id: \.self) { i in
                        StemWaveRow(
                            stem: $stems[i],
                            processing: currentlyProcessing,
                            playing: isPlaying && playingStemID == stems[i].id,
                            playbackActive: isPlaying && playingStemID == nil,
                            onPlay: { playStem(stems[i]) },
                            onVolumeChange: { vol in updateStemVolume(at: i, volume: vol) }
                        )
                    }

                    Divider()
                        .overlay(Color.white.opacity(0.07))

                    HStack(spacing: 10) {
                        Text(formatTime(audioDuration * playbackProgress))
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Color.museMuted)

                        GeometryReader { proxy in
                            Capsule()
                                .fill(.white.opacity(0.06))
                                .overlay(alignment: .leading) {
                                    Capsule()
                                        .fill(LinearGradient.museBlueGold)
                                        .frame(width: proxy.size.width * playbackProgress)
                                }
                                .contentShape(Rectangle())
                                .gesture(
                                    DragGesture(minimumDistance: 0)
                                        .onChanged { value in
                                            let ratio = max(0, min(1, value.location.x / proxy.size.width))
                                            seekTo(ratio)
                                        }
                                )
                        }
                        .frame(height: 14)

                        Text(formatTime(audioDuration))
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(Color.museMuted)
                    }
                }
            }

            VStack(spacing: 12) {
                Button {
                    if let url = extractedAudioURL {
                        splitTitle = extractor.currentTitle
                        loadAudio(from: url)
                    } else {
                        showAudioImporter = true
                    }
                } label: {
                    MuseButtonLabel(title: extractedAudioURL == nil ? "上传音频" : "使用已提取音频", systemImage: "waveform", foreground: .black)
                }
                .buttonStyle(FilledMuseButtonStyle(fill: AnyShapeStyle(Color.museGold)))
                .disabled(currentlyProcessing)

                Button {
                    startSeparation()
                } label: {
                    MuseButtonLabel(
                        title: currentlyProcessing
                            ? (separationMode == .demucs
                                ? "模型分离中 \(Int(currentProgress * 100))%"
                                : "分离中 \(Int(currentProgress * 100))%")
                            : "开始分离",
                        systemImage: "arrow.triangle.branch",
                        foreground: .white
                    )
                }
                .buttonStyle(FilledMuseButtonStyle(fill: AnyShapeStyle(
                    separationMode == .demucs ? Color.museViolet : Color.museBlue
                )))
                .disabled(splitAudioURL == nil || currentlyProcessing)
                .opacity(splitAudioURL == nil ? 0.42 : 1)

                if separationDone {
                    Button {
                        if let url = exportMixedAudio() {
                            exportURL = url
                            showExportSheet = true
                        } else {
                            showExportError = true
                        }
                    } label: {
                        MuseButtonLabel(title: "导出混音", systemImage: "square.and.arrow.up", foreground: .black)
                    }
                    .buttonStyle(FilledMuseButtonStyle(fill: AnyShapeStyle(Color.museGold)))
                }
            }

            if separationDone {
                VStack(alignment: .leading, spacing: 12) {
                    Text("已分离音轨")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(Color.museMuted)

                    ForEach(stems) { stem in
                        StemFileRow(
                            title: "\(splitTitle) - \(stem.title)",
                            subtitle: "M4A · \(formatTime(audioDuration))",
                            active: stem.audioURL != nil
                        )
                    }
                }
            }
        }
    }

    private var currentlyProcessing: Bool {
        separationMode == .local ? separator.isProcessing : demucsService.isProcessing
    }

    private var currentProgress: Double {
        separationMode == .local ? separator.progress : demucsService.progress
    }

    private var splitStatus: String {
        if separationMode == .demucs && demucsService.isProcessing {
            return demucsService.statusText.isEmpty ? "模型分离中..." : demucsService.statusText
        }
        if separator.isProcessing {
            return "分离中 \(Int(separator.progress * 100))%..."
        }
        if separationDone { return "分离完成" }
        if splitAudioURL != nil { return "已上传，可开始分离" }
        return "等待上传"
    }

    private var extractionStatus: String {
        switch extractor.state {
        case .idle: "等待上传"
        case .ready: "用户上传"
        case .exporting: "正在提取..."
        case .finished: "提取完成，可用于音轨分离"
        case let .failed(message): message
        }
    }

    private var canExtract: Bool {
        if case .ready = extractor.state { return true }
        return false
    }

    private var canChangeExtractFormat: Bool {
        if case .exporting = extractor.state { return false }
        return true
    }

    private func handleAudioImport(_ result: Result<[URL], Error>) {
        guard case let .success(urls) = result, let url = urls.first else { return }
        splitTitle = url.deletingPathExtension().lastPathComponent
        loadAudio(from: url)
    }

    private func loadAudio(from url: URL) {
        stopPlayback()
        teardownMixEngine()
        separationDone = false
        for i in stems.indices { stems[i].audioURL = nil }

        let didAccess = url.startAccessingSecurityScopedResource()
        defer { if didAccess { url.stopAccessingSecurityScopedResource() } }

        let dest = FileManager.default.temporaryDirectory
            .appendingPathComponent("ordinary-muse-split-\(UUID().uuidString)-\(url.lastPathComponent)")
        try? FileManager.default.copyItem(at: url, to: dest)
        splitAudioURL = dest

        do {
            let player = try AVAudioPlayer(contentsOf: dest)
            player.prepareToPlay()
            audioPlayer = player
            audioDuration = player.duration
            playbackProgress = 0
            playingStemID = nil
        } catch {
            splitAudioURL = nil
        }
    }

    // MARK: - Playback

    private func togglePlayback() {
        if separationDone && !stemPlayers.isEmpty {
            toggleMixPlayback()
            return
        }
        guard let player = audioPlayer else { return }
        if player.isPlaying {
            player.pause()
            isPlaying = false
            playbackTimer?.invalidate()
        } else {
            try? AVAudioSession.sharedInstance().setCategory(.playback)
            try? AVAudioSession.sharedInstance().setActive(true)
            player.play()
            isPlaying = true
            playingStemID = nil
            playbackTimer?.invalidate()
            playbackTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in
                guard let p = audioPlayer else { return }
                if p.isPlaying {
                    playbackProgress = p.currentTime / p.duration
                } else {
                    playbackProgress = 0
                    isPlaying = false
                    playbackTimer?.invalidate()
                }
            }
        }
    }

    private func seekTo(_ ratio: Double) {
        if !stemBuffers.isEmpty {
            let total = Int(stemBuffers[0].frameLength)
            mixFramePosition = max(0, min(total, Int(Double(total) * ratio)))
            playbackProgress = ratio
            if isPlaying { playMixFromPosition() }
            return
        }
        guard let player = audioPlayer else { return }
        player.currentTime = player.duration * ratio
        playbackProgress = ratio
    }

    private func stopPlayback() {
        playbackTimer?.invalidate()
        for p in stemPlayers { p.stop() }
        mixFramePosition = 0
        audioPlayer?.stop()
        audioPlayer = nil
        isPlaying = false
        playbackProgress = 0
        playingStemID = nil
    }

    // MARK: - Mix Engine

    private func setupMixEngine() {
        teardownMixEngine()
        let engine = AVAudioEngine()
        var players: [AVAudioPlayerNode] = []
        var buffers: [AVAudioPCMBuffer] = []

        for stem in stems {
            guard let url = stem.audioURL else { continue }
            do {
                let file = try AVAudioFile(forReading: url)
                let fmt = file.processingFormat
                guard let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(file.length)) else { continue }
                try file.read(into: buf)

                let player = AVAudioPlayerNode()
                engine.attach(player)
                engine.connect(player, to: engine.mainMixerNode, format: fmt)
                player.volume = Float(stem.volume)
                players.append(player)
                buffers.append(buf)
            } catch { continue }
        }
        guard players.count == stems.count else {
            for p in players { engine.detach(p) }
            return
        }

        do {
            try AVAudioSession.sharedInstance().setCategory(.playback)
            try AVAudioSession.sharedInstance().setActive(true)
            try engine.start()
        } catch { return }

        audioEngine = engine
        stemPlayers = players
        stemBuffers = buffers
        audioDuration = Double(buffers[0].frameLength) / buffers[0].format.sampleRate
        mixFramePosition = 0
        playbackProgress = 0
        audioPlayer?.stop()
        audioPlayer = nil
    }

    private func teardownMixEngine() {
        playbackTimer?.invalidate()
        for p in stemPlayers { p.stop() }
        audioEngine?.stop()
        stemPlayers = []
        stemBuffers = []
        audioEngine = nil
        mixFramePosition = 0
    }

    private func toggleMixPlayback() {
        if isPlaying {
            pauseMix()
        } else {
            playingStemID = nil
            updateStemVolumes()
            playMixFromPosition()
        }
    }

    private func playMixFromPosition() {
        guard !stemBuffers.isEmpty else { return }
        for (i, player) in stemPlayers.enumerated() {
            player.stop()
            let fullBuf = stemBuffers[i]
            let total = Int(fullBuf.frameLength)
            let start = min(mixFramePosition, total)
            let remaining = total - start
            guard remaining > 0,
                  let sub = AVAudioPCMBuffer(pcmFormat: fullBuf.format, frameCapacity: AVAudioFrameCount(remaining))
            else { continue }
            sub.frameLength = AVAudioFrameCount(remaining)
            let channels = Int(fullBuf.format.channelCount)
            for ch in 0..<channels {
                memcpy(sub.floatChannelData![ch],
                       fullBuf.floatChannelData![ch].advanced(by: start),
                       remaining * MemoryLayout<Float>.size)
            }
            player.scheduleBuffer(sub)
        }
        for p in stemPlayers { p.play() }
        isPlaying = true
        startMixTimer()
    }

    private func pauseMix() {
        if let player = stemPlayers.first,
           let nodeTime = player.lastRenderTime,
           nodeTime.isSampleTimeValid,
           let playerTime = player.playerTime(forNodeTime: nodeTime) {
            mixFramePosition += max(0, Int(playerTime.sampleTime))
        }
        for p in stemPlayers { p.stop() }
        isPlaying = false
        playbackTimer?.invalidate()
    }

    private func startMixTimer() {
        playbackTimer?.invalidate()
        playbackTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in
            guard !stemBuffers.isEmpty,
                  let player = stemPlayers.first,
                  let nodeTime = player.lastRenderTime,
                  nodeTime.isSampleTimeValid,
                  let playerTime = player.playerTime(forNodeTime: nodeTime) else { return }
            let currentFrame = mixFramePosition + max(0, Int(playerTime.sampleTime))
            let totalFrames = Int(stemBuffers[0].frameLength)
            if currentFrame >= totalFrames {
                for p in stemPlayers { p.stop() }
                isPlaying = false
                playingStemID = nil
                playbackProgress = 0
                mixFramePosition = 0
                playbackTimer?.invalidate()
                return
            }
            playbackProgress = Double(currentFrame) / Double(totalFrames)
        }
    }

    // MARK: - Stem Solo & Volume

    private func playStem(_ stem: StemTrack) {
        guard let url = stem.audioURL else { return }

        if isPlaying && playingStemID == stem.id {
            if !stemPlayers.isEmpty {
                pauseMix()
            } else {
                audioPlayer?.pause()
                playbackTimer?.invalidate()
                isPlaying = false
            }
            playingStemID = nil
            updateStemVolumes()
            return
        }

        if isPlaying {
            if !stemPlayers.isEmpty {
                pauseMix()
            } else {
                audioPlayer?.stop()
                playbackTimer?.invalidate()
                isPlaying = false
            }
        }

        playingStemID = stem.id

        if !stemPlayers.isEmpty {
            updateStemVolumes()
            playMixFromPosition()
        } else {
            playStemFile(url)
        }
    }

    private func playStemFile(_ url: URL) {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback)
            try AVAudioSession.sharedInstance().setActive(true)
            let player = try AVAudioPlayer(contentsOf: url)
            player.volume = Float(stems.first { $0.id == playingStemID }?.volume ?? 1)
            player.prepareToPlay()
            player.play()
            audioPlayer = player
            audioDuration = player.duration
            isPlaying = true
            playbackProgress = 0
            playbackTimer?.invalidate()
            playbackTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { _ in
                guard let p = audioPlayer else { return }
                if p.isPlaying {
                    playbackProgress = p.duration > 0 ? p.currentTime / p.duration : 0
                } else {
                    playbackProgress = 0
                    isPlaying = false
                    playingStemID = nil
                    playbackTimer?.invalidate()
                }
            }
        } catch {
            isPlaying = false
            playingStemID = nil
        }
    }

    private func updateStemVolumes() {
        for (i, player) in stemPlayers.enumerated() {
            if let soloID = playingStemID {
                player.volume = stems[i].id == soloID ? Float(stems[i].volume) : 0
            } else {
                player.volume = Float(stems[i].volume)
            }
        }
    }

    private func updateStemVolume(at index: Int, volume: Double) {
        guard stems.indices.contains(index) else { return }
        guard index < stemPlayers.count else {
            if playingStemID == stems[index].id {
                audioPlayer?.volume = Float(volume)
            }
            return
        }

        if let soloID = playingStemID {
            stemPlayers[index].volume = stems[index].id == soloID ? Float(volume) : 0
        } else {
            stemPlayers[index].volume = Float(volume)
        }
    }

    private func startSeparation() {
        guard let url = splitAudioURL else { return }
        stopPlayback()
        if separationMode == .local {
            startLocalSeparation(url: url)
        } else {
            startDemucseparation(url: url)
        }
    }

    private func startLocalSeparation(url: URL) {
        Task {
            do {
                let result = try await separator.separate(audioURL: url)
                stems[0].audioURL = result.vocals
                stems[1].audioURL = result.drums
                stems[2].audioURL = result.bass
                stems[3].audioURL = result.other
                separationDone = true
                setupMixEngine()
            } catch {
                splitTitle = "分离失败: \(error.localizedDescription)"
            }
        }
    }

    private func startDemucseparation(url: URL) {
        Task {
            do {
                let result = try await demucsService.separate(audioURL: url)
                stems[0].audioURL = result.vocals
                stems[1].audioURL = result.drums
                stems[2].audioURL = result.bass
                stems[3].audioURL = result.other
                separationDone = true
                setupMixEngine()
            } catch {
                splitTitle = "分离失败: \(error.localizedDescription)"
            }
        }
    }

    // MARK: - Export

    private func exportMixedAudio() -> URL? {
        var buffers = stemBuffers
        var sampleRate: Double = 44100

        if buffers.isEmpty {
            for stem in stems {
                guard let stemURL = stem.audioURL else { return nil }
                do {
                    let file = try AVAudioFile(forReading: stemURL)
                    sampleRate = file.processingFormat.sampleRate
                    guard let buf = AVAudioPCMBuffer(
                        pcmFormat: file.processingFormat,
                        frameCapacity: AVAudioFrameCount(file.length)
                    ) else { return nil }
                    try file.read(into: buf)
                    buffers.append(buf)
                } catch {
                    return nil
                }
            }
        } else {
            sampleRate = buffers[0].format.sampleRate
        }

        guard buffers.count == stems.count else { return nil }
        let frameCount = Int(buffers[0].frameLength)

        var mixed = [Float](repeating: 0, count: frameCount)
        for (i, buffer) in buffers.enumerated() {
            var vol = Float(stems[i].volume)
            guard let src = buffer.floatChannelData?[0] else { continue }
            let count = min(frameCount, Int(buffer.frameLength))
            var scaled = [Float](repeating: 0, count: count)
            vDSP_vsmul(src, 1, &vol, &scaled, 1, vDSP_Length(count))
            vDSP_vadd(mixed, 1, scaled, 1, &mixed, 1, vDSP_Length(count))
        }

        let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 1)!
        guard let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frameCount)) else { return nil }
        buf.frameLength = AVAudioFrameCount(frameCount)
        memcpy(buf.floatChannelData![0], mixed, frameCount * MemoryLayout<Float>.size)

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("ordinary-muse-mix-\(UUID().uuidString).m4a")

        do {
            let file = try AVAudioFile(forWriting: url, settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: sampleRate,
                AVNumberOfChannelsKey: 1,
                AVEncoderBitRateKey: 128_000,
            ], commonFormat: .pcmFormatFloat32, interleaved: false)
            try file.write(from: buf)
            return url
        } catch {
            return nil
        }
    }
    private func formatTime(_ seconds: TimeInterval) -> String {
        let m = Int(seconds) / 60
        let s = Int(seconds) % 60
        return String(format: "%d:%02d", m, s)
    }
}

private struct ShareSheet: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [url], applicationActivities: nil)
    }

    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

private struct VideoPhotoPicker: UIViewControllerRepresentable {
    let onPick: (URL) -> Void

    func makeUIViewController(context: Context) -> PHPickerViewController {
        var configuration = PHPickerConfiguration(photoLibrary: .shared())
        configuration.filter = .videos
        configuration.selectionLimit = 1
        configuration.preferredAssetRepresentationMode = .current

        let picker = PHPickerViewController(configuration: configuration)
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ vc: PHPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(onPick: onPick)
    }

    final class Coordinator: NSObject, PHPickerViewControllerDelegate {
        let onPick: (URL) -> Void

        init(onPick: @escaping (URL) -> Void) {
            self.onPick = onPick
        }

        func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
            picker.dismiss(animated: true)
            guard let provider = results.first?.itemProvider else { return }

            let typeIdentifier = [
                UTType.movie.identifier,
                UTType.mpeg4Movie.identifier,
                UTType.quickTimeMovie.identifier,
                UTType.video.identifier
            ].first { provider.hasItemConformingToTypeIdentifier($0) } ?? UTType.movie.identifier

            provider.loadFileRepresentation(forTypeIdentifier: typeIdentifier) { url, _ in
                if let url, let destination = self.copyPickedVideoFile(from: url, typeIdentifier: typeIdentifier) {
                    DispatchQueue.main.async {
                        self.onPick(destination)
                    }
                    return
                }

                self.loadPickedVideoData(from: provider, typeIdentifier: typeIdentifier)
            }
        }

        private func copyPickedVideoFile(from url: URL, typeIdentifier: String) -> URL? {
            let fileExtension = url.pathExtension.isEmpty
                ? Self.fileExtension(for: typeIdentifier)
                : url.pathExtension
            let destination = FileManager.default.temporaryDirectory
                .appendingPathComponent("ordinary-muse-picked-\(UUID().uuidString)")
                .appendingPathExtension(fileExtension)

            do {
                try? FileManager.default.removeItem(at: destination)
                try FileManager.default.copyItem(at: url, to: destination)
                return destination
            } catch {
                return nil
            }
        }

        private func loadPickedVideoData(from provider: NSItemProvider, typeIdentifier: String) {
            provider.loadDataRepresentation(forTypeIdentifier: typeIdentifier) { data, _ in
                guard let data else { return }
                let destination = FileManager.default.temporaryDirectory
                    .appendingPathComponent("ordinary-muse-picked-\(UUID().uuidString)")
                    .appendingPathExtension(Self.fileExtension(for: typeIdentifier))

                do {
                    try? FileManager.default.removeItem(at: destination)
                    try data.write(to: destination, options: .atomic)
                    DispatchQueue.main.async {
                        self.onPick(destination)
                    }
                } catch {
                    return
                }
            }
        }

        private static func fileExtension(for typeIdentifier: String) -> String {
            UTType(typeIdentifier)?.preferredFilenameExtension ?? "mov"
        }
    }
}

private struct HeaderBlock: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(spacing: 6) {
            Text("凡响")
                .font(.system(size: 11, weight: .medium, design: .serif))
                .foregroundStyle(Color.museText.opacity(0.86))
                .overlay(alignment: .topTrailing) {
                    Image(systemName: "music.note")
                        .font(.system(size: 9))
                        .offset(x: 14, y: -3)
                }

            Text(title)
                .font(.system(size: 30, weight: .light))
                .foregroundStyle(Color.museText)
                .lineLimit(1)
                .minimumScaleFactor(0.72)

            Text(subtitle)
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(Color.museMuted)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct ProgressOrb: View {
    let progress: Double

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    RadialGradient(
                        colors: [.museBlue.opacity(0.2), .white.opacity(0.04), .clear],
                        center: .center,
                        startRadius: 10,
                        endRadius: 88
                    )
                )
                .blur(radius: 2)

            Circle()
                .stroke(.white.opacity(0.04), lineWidth: 9)
                .frame(width: 80, height: 80)

            Circle()
                .trim(from: 0, to: progress)
                .stroke(LinearGradient.museBlueGold, style: StrokeStyle(lineWidth: 9, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .frame(width: 80, height: 80)
                .shadow(color: .museBlue.opacity(0.45), radius: 9)

            HStack(alignment: .firstTextBaseline, spacing: 1) {
                Text("\(Int(progress * 100))")
                    .font(.system(size: 23, weight: .light))
                    .monospacedDigit()
                Text("%")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(Color.museMuted)
            }
            .foregroundStyle(Color.museText)
        }
    }
}

private struct GlassPanel<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        content
            .padding(20)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 32, style: .continuous)
                    .fill(Color.musePanel.opacity(0.72))
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 32, style: .continuous))
            )
            .overlay {
                RoundedRectangle(cornerRadius: 32, style: .continuous)
                    .stroke(.white.opacity(0.14), lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.3), radius: 34, y: 18)
    }
}

private struct MuseButtonLabel: View {
    let title: String
    let systemImage: String
    let foreground: Color

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: systemImage)
                .font(.system(size: 15, weight: .bold))
            Text(title)
                .font(.system(size: 14, weight: .bold))
        }
        .foregroundStyle(foreground)
        .frame(maxWidth: .infinity)
    }
}

private struct ServerPresetButton: View {
    let title: String
    let icon: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: icon)
                    .font(.system(size: 11, weight: .semibold))
                Text(title)
                    .font(.system(size: 11, weight: .bold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.82)
            }
            .foregroundStyle(Color.museText)
            .frame(maxWidth: .infinity, minHeight: 32)
            .background(Capsule().fill(Color.white.opacity(0.07)))
            .overlay {
                Capsule().stroke(Color.white.opacity(0.08), lineWidth: 1)
            }
        }
    }
}

private struct FilledMuseButtonStyle: ButtonStyle {
    let fill: AnyShapeStyle

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.vertical, 14)
            .background(Capsule().fill(fill))
            .overlay {
                Capsule()
                    .stroke(.white.opacity(0.34), lineWidth: 1)
                    .blendMode(.plusLighter)
            }
            .overlay(alignment: .top) {
                Capsule()
                    .fill(.white.opacity(0.18))
                    .frame(height: 1.2)
                    .padding(.horizontal, 18)
                    .padding(.top, 1)
            }
            .shadow(color: .white.opacity(0.08), radius: 10, y: -1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.spring(response: 0.24, dampingFraction: 0.8), value: configuration.isPressed)
    }
}

private struct OutlineMuseButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(Color.museText)
            .padding(.vertical, 14)
            .background(Capsule().fill(.black.opacity(0.13)))
            .overlay {
                Capsule().stroke(.white.opacity(0.18), lineWidth: 1)
            }
            .overlay(alignment: .top) {
                Capsule()
                    .fill(.white.opacity(0.1))
                    .frame(height: 1)
                    .padding(.horizontal, 18)
                    .padding(.top, 1)
            }
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
    }
}

private struct RecentJobRow: View {
    let job: RecentJob

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(job.title)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(Color.museText)
                    .lineLimit(1)
                Text(job.subtitle)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Color.museMuted)
            }

            Spacer()

            Text("\(Int(job.progress * 100))%")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(Color.museMuted)
                .monospacedDigit()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .fill(job.highlighted ? Color.museBlue.opacity(0.08) : Color.white.opacity(0.055))
        )
        .overlay {
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .stroke(job.highlighted ? Color.museBlue.opacity(0.8) : Color.white.opacity(0.04), lineWidth: 1)
        }
    }
}

private struct StemWaveRow: View {
    @Binding var stem: StemTrack
    let processing: Bool
    var playing: Bool = false
    var playbackActive: Bool = false
    var onPlay: (() -> Void)?
    var onVolumeChange: ((Double) -> Void)?

    var body: some View {
        VStack(spacing: 6) {
            HStack(spacing: 10) {
                Text(stem.title)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Color.museMuted)
                    .frame(width: 34, alignment: .leading)

                WaveformView(
                    color: stem.color,
                    seed: stem.seed,
                    dimmed: processing,
                    playing: playbackActive || playing,
                    volume: stem.volume
                )
                .frame(height: 34)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(Color.musePanel.opacity(0.36))
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                )
                .overlay {
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .stroke(
                            LinearGradient(
                                colors: [.white.opacity(0.18), stem.color.opacity(0.28), .white.opacity(0.04)],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            ),
                            lineWidth: 1
                        )
                }
                .overlay(alignment: .top) {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(.white.opacity(0.11))
                        .frame(height: 1)
                        .padding(.horizontal, 10)
                        .padding(.top, 1)
                }
                .shadow(color: stem.color.opacity(processing ? 0.04 : 0.18), radius: 12, y: 4)
                .overlay {
                    if processing {
                        Text("处理中...")
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(Color.museMuted)
                    }
                }

                Button { onPlay?() } label: {
                    Image(systemName: playing ? "pause.fill" : "play.fill")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(stem.audioURL != nil ? Color.museText : Color.museMuted)
                        .frame(width: 30, height: 30)
                        .background(
                            Circle()
                                .fill(playing ? Color.museBlue.opacity(0.26) : .white.opacity(0.065))
                                .background(.ultraThinMaterial, in: Circle())
                        )
                        .overlay {
                            Circle()
                                .stroke(.white.opacity(playing ? 0.3 : 0.16), lineWidth: 1)
                        }
                        .shadow(color: playing ? Color.museBlue.opacity(0.35) : .clear, radius: 10)
                }
                .buttonStyle(.plain)
                .contentShape(Circle())
                .disabled(stem.audioURL == nil)
                .opacity(stem.audioURL == nil ? 0.42 : 1)
            }

            HStack(spacing: 6) {
                Image(systemName: stem.volume > 0 ? "speaker.wave.2" : "speaker.slash")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(Color.museMuted)

                Slider(value: $stem.volume, in: 0...1)
                    .tint(Color.museBlue)
                    .onChange(of: stem.volume) { _, newValue in
                        onVolumeChange?(newValue)
                    }

                Text("\(Int(stem.volume * 100))")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(Color.museMuted)
                    .monospacedDigit()
                    .frame(width: 26, alignment: .trailing)
            }
            .opacity(processing ? 0.48 : 1)
        }
    }
}

private struct WaveformView: View {
    let color: Color
    let seed: Double
    let dimmed: Bool
    var playing: Bool = false
    var volume: Double = 1

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30)) { timeline in
            Canvas { context, size in
                let count = max(34, Int(size.width / 6))
                let barWidth = size.width / CGFloat(count) * 0.58
                let time = timeline.date.timeIntervalSinceReferenceDate
                let loudness = max(0.08, min(1, volume))

                for index in 0..<count {
                    let x = CGFloat(index) * size.width / CGFloat(count)
                    let phase = Double(index) / Double(count)
                    let envelope = 0.22 + 0.78 * abs(sin((phase + seed) * .pi * 3.2))
                    let harmonic = 0.55 + 0.45 * abs(sin(phase * 18 + seed * 9))
                    let frequency = 2.4 + seed * 3.2 + Double(index % 7) * 0.22
                    let beat = playing ? 0.72 + 0.34 * abs(sin(time * frequency + phase * .pi * 7)) : 1
                    let shimmer = playing ? 0.9 + 0.18 * sin(time * (frequency * 1.8) + seed * 11) : 1
                    let dynamicHeight = envelope * harmonic * beat * shimmer * (0.55 + loudness * 0.45)
                    let height = max(3, size.height * dynamicHeight * 0.74)
                    let rect = CGRect(x: x, y: (size.height - height) / 2, width: barWidth, height: height)
                    let path = Path(roundedRect: rect, cornerRadius: 2)
                    context.fill(path, with: .color(color.opacity(dimmed ? 0.46 : 1)))
                }
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .shadow(color: color.opacity(dimmed ? 0.1 : 0.35), radius: 14)
    }
}

private struct StemFileRow: View {
    let title: String
    let subtitle: String
    let active: Bool

    var body: some View {
        HStack(spacing: 14) {
            Circle()
                .fill(active ? Color.museGold : Color.white.opacity(0.05))
                .frame(width: 8, height: 8)

            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(Color.museText)
                    .lineLimit(1)
                Text(subtitle)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Color.museMuted)
            }

            Spacer()

            Image(systemName: "arrow.down.to.line")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(Color.museText)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .fill(.white.opacity(0.055))
        )
        .overlay {
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .stroke(.white.opacity(0.04), lineWidth: 1)
        }
    }
}

private struct MuseTabBar: View {
    @Binding var selectedTab: MuseTab

    var body: some View {
        GeometryReader { proxy in
            VStack(spacing: 0) {
                HStack(spacing: 16) {
                    tabButton(tab: .extract, title: "提取", icon: "water.waves")
                    tabButton(tab: .split, title: "分离", icon: "arrow.triangle.branch")
                }
                .padding(.horizontal, 28)
                .padding(.top, 14)
                .padding(.bottom, 14)

                Color.clear
                    .frame(height: proxy.safeAreaInsets.bottom)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
            .background(
                Color.musePanel.opacity(0.94)
                    .overlay(alignment: .top) {
                        Rectangle().fill(.white.opacity(0.08)).frame(height: 1)
                    }
            )
        }
        .frame(height: 92)
        .ignoresSafeArea(.container, edges: .bottom)
    }

    private func tabButton(tab: MuseTab, title: String, icon: String) -> some View {
        Button {
            withAnimation(.spring(response: 0.32, dampingFraction: 0.82)) {
                selectedTab = tab
            }
        } label: {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.system(size: 15, weight: .semibold))
                Text(title)
                    .font(.system(size: 14, weight: .bold))
            }
            .foregroundStyle(selectedTab == tab ? .white : Color.museText)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 13)
            .background {
                if selectedTab == tab {
                    Capsule()
                        .fill(Color.museBlue)
                        .overlay {
                            Capsule()
                                .stroke(.white.opacity(0.28), lineWidth: 1)
                                .blendMode(.plusLighter)
                        }
                        .overlay(alignment: .top) {
                            Capsule()
                                .fill(.white.opacity(0.18))
                                .frame(height: 1)
                                .padding(.horizontal, 16)
                                .padding(.top, 1)
                        }
                        .shadow(color: .museBlue.opacity(0.35), radius: 18)
                }
            }
        }
    }
}

private struct MuseBackground: View {
    var body: some View {
        ZStack {
            Color.museInk
            LinearGradient(
                colors: [
                    .black.opacity(0.82),
                    .museBlue.opacity(0.08),
                    .black.opacity(0.62)
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            RadialGradient(colors: [.museGold.opacity(0.11), .clear], center: .topTrailing, startRadius: 0, endRadius: 420)
            RadialGradient(colors: [.museBlue.opacity(0.18), .clear], center: .center, startRadius: 80, endRadius: 520)
        }
    }
}

private extension Color {
    static let museInk = Color(red: 0.035, green: 0.04, blue: 0.055)
    static let musePanel = Color(red: 0.085, green: 0.095, blue: 0.12)
    static let museText = Color(red: 0.9, green: 0.92, blue: 0.96)
    static let museMuted = Color(red: 0.55, green: 0.59, blue: 0.68)
    static let museBlue = Color(red: 0.13, green: 0.51, blue: 1.0)
    static let museGold = Color(red: 1.0, green: 0.79, blue: 0.18)
    static let museViolet = Color(red: 0.46, green: 0.22, blue: 0.62)
    static let museMint = Color(red: 0.08, green: 0.82, blue: 0.72)
}

private extension LinearGradient {
    static let museBlueGold = LinearGradient(
        colors: [.museBlue, .museGold],
        startPoint: .leading,
        endPoint: .trailing
    )
}
