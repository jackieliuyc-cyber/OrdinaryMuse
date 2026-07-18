import Foundation

enum SeparationServiceError: LocalizedError {
    case serverUnavailable
    case uploadFailed
    case processingFailed(String)
    case downloadFailed
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .serverUnavailable: return "无法连接到分离服务器"
        case .uploadFailed: return "上传文件失败"
        case .processingFailed(let msg): return "处理失败: \(msg)"
        case .downloadFailed: return "下载分轨文件失败"
        case .invalidResponse: return "服务器响应无效"
        }
    }
}

@MainActor
final class SeparationService: ObservableObject {
    @Published var progress: Double = 0
    @Published var isProcessing = false
    @Published var statusText = ""
    @Published var serverURL: String = "http://12630gj35tf95.vicp.fun"
    let localURL = "http://127.0.0.1:8000"
    let lanURLPlaceholder = "http://192.168.1.100:8000"
    private let session: URLSession

    init() {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 600
        configuration.timeoutIntervalForResource = 600
        session = URLSession(configuration: configuration)
    }

    func healthCheck() async -> Bool {
        guard let url = endpoint("/api/v1/health") else { return false }
        do {
            log("health check: \(url.absoluteString)")
            let (_, response) = try await session.data(from: url)
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? -1
            log("health check response: \(statusCode)")
            return statusCode == 200
        } catch {
            log("health check failed: \(error.localizedDescription)")
            return false
        }
    }

    func separate(audioURL: URL) async throws -> StemURLs {
        isProcessing = true
        progress = 0
        statusText = "准备上传..."
        defer { isProcessing = false }

        let audioData = try Data(contentsOf: audioURL)
        let filename = audioURL.lastPathComponent
        log("start separation: file=\(filename), size=\(audioData.count) bytes, server=\(serverURL)")

        statusText = "上传中..."
        progress = 0.1
        let taskId = try await submitTask(audioData: audioData, filename: filename)
        log("submitted task: \(taskId)")

        statusText = "模型分离中..."
        progress = 0.2
        try await pollUntilComplete(taskId: taskId)

        statusText = "下载分轨..."
        progress = 0.8
        let urls = try await downloadStems(taskId: taskId)
        log("saved stems: vocals=\(urls.vocals.path), drums=\(urls.drums.path), bass=\(urls.bass.path), other=\(urls.other.path)")

        progress = 1.0
        statusText = "分离完成"
        return urls
    }

    func applyServerURL(_ rawValue: String) {
        serverURL = Self.normalizedBaseURL(rawValue)
    }

    private func submitTask(audioData: Data, filename: String) async throws -> String {
        guard let url = endpoint("/api/v1/separate/async") else {
            throw SeparationServiceError.serverUnavailable
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 600

        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")

        var body = Data()
        body.appendUTF8("--\(boundary)\r\n")
        body.appendUTF8("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        body.appendUTF8("Content-Type: application/octet-stream\r\n\r\n")
        body.append(audioData)
        body.appendUTF8("\r\n--\(boundary)--\r\n")

        let (data, response) = try await session.upload(for: request, from: body)
        let statusCode = (response as? HTTPURLResponse)?.statusCode ?? -1
        log("upload response: \(statusCode), body=\(Self.preview(data))")
        guard statusCode == 200 else {
            throw SeparationServiceError.uploadFailed
        }

        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let taskId = json["task_id"] as? String else {
            throw SeparationServiceError.invalidResponse
        }
        return taskId
    }

    private func pollUntilComplete(taskId: String) async throws {
        guard let url = endpoint("/api/v1/task/\(taskId)") else {
            throw SeparationServiceError.serverUnavailable
        }

        while true {
            try await Task.sleep(nanoseconds: 3_000_000_000)
            let (data, response) = try await session.data(from: url)
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? -1
            log("task \(taskId) poll response: \(statusCode), body=\(Self.preview(data))")

            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let status = json["status"] as? String else {
                throw SeparationServiceError.invalidResponse
            }

            switch status {
            case "completed":
                if let time = json["processing_time"] as? String {
                    statusText = "模型处理完成（\(time)）"
                }
                return
            case "failed":
                let error = json["error"] as? String ?? "未知错误"
                let detail = json["error_detail"] as? String
                log("task \(taskId) failed: \(error)\(detail.map { ", detail=\($0)" } ?? "")")
                throw SeparationServiceError.processingFailed(error)
            default:
                progress = min(0.75, progress + 0.05)
                statusText = "模型分离中..."
            }
        }
    }

    private func downloadStems(taskId: String) async throws -> StemURLs {
        var result: [String: URL] = [:]
        let stemNames = ["vocals", "drums", "bass", "other"]
        let progressPerStem = 0.1 / Double(stemNames.count)
        let sessionID = UUID().uuidString

        for (i, name) in stemNames.enumerated() {
            guard var components = endpointComponents("/api/v1/task/\(taskId)/stem/\(name)") else {
                throw SeparationServiceError.serverUnavailable
            }
            components.queryItems = [URLQueryItem(name: "format", value: "m4a")]
            guard let url = components.url else {
                throw SeparationServiceError.serverUnavailable
            }

            let progressStart = 0.8 + Double(i) * progressPerStem
            statusText = "下载 \(name)（\(i + 1)/\(stemNames.count)）..."
            progress = progressStart
            log("start download stem \(name): \(url.absoluteString)")

            let delegate = StemDownloadProgressDelegate { [weak self] received, expected in
                guard expected > 0 else { return }
                let ratio = min(1, Double(received) / Double(expected))
                Task { @MainActor [weak self] in
                    self?.progress = progressStart + ratio * progressPerStem
                    let receivedMB = Double(received) / 1_048_576
                    let expectedMB = Double(expected) / 1_048_576
                    self?.statusText = String(
                        format: "下载 %@（%d/%d）%.1f/%.1f MB",
                        name,
                        i + 1,
                        stemNames.count,
                        receivedMB,
                        expectedMB
                    )
                }
            }

            var request = URLRequest(url: url)
            request.timeoutInterval = 600
            let (temporaryURL, response) = try await session.download(for: request, delegate: delegate)
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? -1
            let bytes = (try? FileManager.default.attributesOfItem(atPath: temporaryURL.path)[.size] as? NSNumber)?
                .intValue ?? 0
            log("download stem \(name): \(statusCode), bytes=\(bytes)")
            guard statusCode == 200 else {
                let errorData = (try? Data(contentsOf: temporaryURL)) ?? Data()
                log("download stem \(name) failed body: \(Self.preview(errorData))")
                throw SeparationServiceError.downloadFailed
            }

            let destination = FileManager.default.temporaryDirectory
                .appendingPathComponent("demucs-\(name)-\(sessionID).m4a")
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: temporaryURL, to: destination)
            result[name] = destination
            progress = progressStart + progressPerStem
        }

        guard let vocalsURL = result["vocals"],
              let drumsURL = result["drums"],
              let bassURL = result["bass"],
              let otherURL = result["other"] else {
            throw SeparationServiceError.invalidResponse
        }

        return StemURLs(vocals: vocalsURL, drums: drumsURL, bass: bassURL, other: otherURL)
    }

    private func log(_ message: String) {
        print("[DemucsService] \(message)")
    }

    private func endpoint(_ path: String) -> URL? {
        URL(string: "\(Self.normalizedBaseURL(serverURL))\(path)")
    }

    private func endpointComponents(_ path: String) -> URLComponents? {
        URLComponents(string: "\(Self.normalizedBaseURL(serverURL))\(path)")
    }

    private static func normalizedBaseURL(_ rawValue: String) -> String {
        var value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)

        if var components = URLComponents(string: value),
           components.scheme != nil,
           components.host != nil {
            let removableSuffixes = ["/api/v1/health", "/api/v1", "/docs"]
            for suffix in removableSuffixes where components.path.hasSuffix(suffix) {
                components.path = String(components.path.dropLast(suffix.count))
                break
            }
            components.query = nil
            components.fragment = nil
            value = components.string ?? value
        }

        while value.hasSuffix("/") {
            value.removeLast()
        }
        return value
    }

    private static func preview(_ data: Data, limit: Int = 500) -> String {
        guard !data.isEmpty else { return "<empty>" }
        let clipped = Data(data.prefix(limit))
        return String(data: clipped, encoding: .utf8) ?? "<\(data.count) binary bytes>"
    }
}

private final class StemDownloadProgressDelegate: NSObject, URLSessionDownloadDelegate {
    private let onProgress: @Sendable (Int64, Int64) -> Void

    init(onProgress: @escaping @Sendable (Int64, Int64) -> Void) {
        self.onProgress = onProgress
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        onProgress(totalBytesWritten, totalBytesExpectedToWrite)
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {}
}

private extension Data {
    mutating func appendUTF8(_ string: String) {
        if let data = string.data(using: .utf8) { append(data) }
    }
}
