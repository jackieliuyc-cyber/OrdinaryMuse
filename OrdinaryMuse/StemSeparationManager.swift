import Accelerate
import AVFoundation

struct StemURLs {
    let vocals: URL
    let drums: URL
    let bass: URL
    let other: URL
}

@MainActor
final class StemSeparationManager: ObservableObject {
    @Published var progress: Double = 0
    @Published var isProcessing = false

    func separate(audioURL: URL) async throws -> StemURLs {
        isProcessing = true
        progress = 0
        defer { isProcessing = false }

        let file = try AVAudioFile(forReading: audioURL)
        let format = file.processingFormat
        let sampleRate = format.sampleRate
        let frameCount = AVAudioFrameCount(file.length)

        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else {
            throw StemError.bufferFailed
        }
        try file.read(into: buffer)
        guard let channelData = buffer.floatChannelData else { throw StemError.noData }

        let channels = Int(format.channelCount)
        let samples = Int(frameCount)

        var mono = [Float](repeating: 0, count: samples)
        for ch in 0..<channels {
            vDSP_vadd(mono, 1, channelData[ch], 1, &mono, 1, vDSP_Length(samples))
        }
        if channels > 1 {
            var s = 1.0 / Float(channels)
            vDSP_vsmul(mono, 1, &s, &mono, 1, vDSP_Length(samples))
        }

        let result: StemURLs = try await withCheckedThrowingContinuation { [weak self] cont in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    let urls = try Self.processSTFT(mono: mono, sampleRate: sampleRate, samples: samples) { p in
                        DispatchQueue.main.async { self?.progress = p }
                    }
                    cont.resume(returning: urls)
                } catch {
                    cont.resume(throwing: error)
                }
            }
        }

        progress = 1.0
        return result
    }

    // MARK: - FFT Processing

    private static func processSTFT(
        mono: [Float], sampleRate: Double, samples: Int,
        onProgress: @escaping (Double) -> Void
    ) throws -> StemURLs {
        let fftSize = 8192
        let hopSize = fftSize / 4
        let halfFFT = fftSize / 2
        let log2n = vDSP_Length(log2(Float(fftSize)))

        guard let setup = vDSP_create_fftsetup(log2n, FFTRadix(kFFTRadix2)) else {
            throw StemError.fftFailed
        }
        defer { vDSP_destroy_fftsetup(setup) }

        var window = [Float](repeating: 0, count: fftSize)
        vDSP_hann_window(&window, vDSP_Length(fftSize), Int32(vDSP_HANN_NORM))

        let numFrames = max(1, (samples - fftSize) / hopSize + 1)
        let freqRes = Float(sampleRate) / Float(fftSize)

        // Precompute frequency masks
        var vm = [Float](repeating: 0, count: halfFFT)
        var dm = [Float](repeating: 0, count: halfFFT)
        var bm = [Float](repeating: 0, count: halfFFT)
        var om = [Float](repeating: 0, count: halfFFT)

        for bin in 0..<halfFFT {
            let f = Float(bin) * freqRes

            let b = pow(
                1.25 * gauss(f, center: 58, sigma: 48)
                + 0.8 * band(f, lo: 38, hi: 190, roll: 34),
                1.45
            )
            let d = pow(
                1.15 * gauss(f, center: 74, sigma: 42)
                + 0.7 * gauss(f, center: 210, sigma: 120)
                + 0.46 * band(f, lo: 900, hi: 3200, roll: 520)
                + 0.85 * sigmoid((f - 5600) / 1300),
                1.32
            )
            let v = pow(
                1.1 * band(f, lo: 170, hi: 5200, roll: 180)
                + 0.45 * gauss(f, center: 850, sigma: 420)
                + 0.35 * gauss(f, center: 2450, sigma: 850),
                1.38
            )
            let o = pow(
                0.9 * band(f, lo: 95, hi: 7200, roll: 300)
                + 0.42 * band(f, lo: 1800, hi: 9800, roll: 900)
                + 0.18,
                1.18
            )

            let total = v + d + b + o
            if total > 1e-4 {
                vm[bin] = v / total; dm[bin] = d / total
                bm[bin] = b / total; om[bin] = o / total
            } else {
                vm[bin] = 0.25; dm[bin] = 0.25
                bm[bin] = 0.25; om[bin] = 0.25
            }
        }

        var vOut = [Float](repeating: 0, count: samples)
        var dOut = [Float](repeating: 0, count: samples)
        var bOut = [Float](repeating: 0, count: samples)
        var oOut = [Float](repeating: 0, count: samples)
        var wAccum = [Float](repeating: 0, count: samples)

        for frame in 0..<numFrames {
            let off = frame * hopSize
            let end = min(off + fftSize, samples)
            let len = end - off

            var windowed = [Float](repeating: 0, count: fftSize)
            for i in 0..<len { windowed[i] = mono[off + i] * window[i] }

            var rp = [Float](repeating: 0, count: halfFFT)
            var ip = [Float](repeating: 0, count: halfFFT)

            windowed.withUnsafeBufferPointer { buf in
                buf.baseAddress!.withMemoryRebound(to: DSPComplex.self, capacity: halfFFT) { ptr in
                    var sc = DSPSplitComplex(realp: &rp, imagp: &ip)
                    vDSP_ctoz(ptr, 2, &sc, 1, vDSP_Length(halfFFT))
                }
            }

            var sc = DSPSplitComplex(realp: &rp, imagp: &ip)
            vDSP_fft_zrip(setup, &sc, 1, log2n, FFTDirection(FFT_FORWARD))

            func masked(_ mask: [Float]) -> [Float] {
                var mr = [Float](repeating: 0, count: halfFFT)
                var mi = [Float](repeating: 0, count: halfFFT)
                vDSP_vmul(rp, 1, mask, 1, &mr, 1, vDSP_Length(halfFFT))
                vDSP_vmul(ip, 1, mask, 1, &mi, 1, vDSP_Length(halfFFT))

                var ms = DSPSplitComplex(realp: &mr, imagp: &mi)
                vDSP_fft_zrip(setup, &ms, 1, log2n, FFTDirection(FFT_INVERSE))

                var out = [Float](repeating: 0, count: fftSize)
                out.withUnsafeMutableBufferPointer { buf in
                    buf.baseAddress!.withMemoryRebound(to: DSPComplex.self, capacity: halfFFT) { ptr in
                        vDSP_ztoc(&ms, 1, ptr, 2, vDSP_Length(halfFFT))
                    }
                }
                var scale = 1.0 / Float(fftSize * 2)
                vDSP_vsmul(out, 1, &scale, &out, 1, vDSP_Length(fftSize))
                vDSP_vmul(out, 1, window, 1, &out, 1, vDSP_Length(fftSize))
                return out
            }

            let vF = masked(vm), dF = masked(dm), bF = masked(bm), oF = masked(om)

            for i in 0..<len {
                vOut[off + i] += vF[i]; dOut[off + i] += dF[i]
                bOut[off + i] += bF[i]; oOut[off + i] += oF[i]
                wAccum[off + i] += window[i] * window[i]
            }

            if frame % 40 == 0 {
                onProgress(Double(frame) / Double(numFrames) * 0.9)
            }
        }

        for i in 0..<samples where wAccum[i] > 1e-3 {
            vOut[i] /= wAccum[i]; dOut[i] /= wAccum[i]
            bOut[i] /= wAccum[i]; oOut[i] /= wAccum[i]
        }

        onProgress(0.92)
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 1)!
        let vocalsURL = try writeStem(vOut, format: fmt, name: "vocals")
        onProgress(0.95)
        let drumsURL = try writeStem(dOut, format: fmt, name: "drums")
        onProgress(0.97)
        let bassURL = try writeStem(bOut, format: fmt, name: "bass")
        onProgress(0.99)
        let otherURL = try writeStem(oOut, format: fmt, name: "other")

        return StemURLs(vocals: vocalsURL, drums: drumsURL, bass: bassURL, other: otherURL)
    }

    private static func writeStem(_ data: [Float], format: AVAudioFormat, name: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("stem-\(name)-\(UUID().uuidString).wav")
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(data.count))!
        buf.frameLength = AVAudioFrameCount(data.count)
        memcpy(buf.floatChannelData![0], data, data.count * MemoryLayout<Float>.size)
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: format.sampleRate,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 24,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let file = try AVAudioFile(forWriting: url, settings: settings)
        try file.write(from: buf)
        return url
    }

    // MARK: - Mask Helpers

    private static func gauss(_ x: Float, center: Float, sigma: Float) -> Float {
        let d = (x - center) / sigma
        return exp(-d * d * 0.5)
    }

    private static func band(_ x: Float, lo: Float, hi: Float, roll: Float) -> Float {
        sigmoid((x - lo) / roll) * sigmoid((hi - x) / roll)
    }

    private static func sigmoid(_ x: Float) -> Float {
        1.0 / (1.0 + exp(-x * 5))
    }

    enum StemError: LocalizedError {
        case bufferFailed, noData, fftFailed
        var errorDescription: String? {
            switch self {
            case .bufferFailed: "无法创建音频缓冲区"
            case .noData: "无法读取音频数据"
            case .fftFailed: "FFT 初始化失败"
            }
        }
    }
}
