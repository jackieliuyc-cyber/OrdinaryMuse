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
    @Published var serverURL: String = "http://30.165.116.246:8000"

    func healthCheck() async -> Bool {
        guard let url = URL(string: "\(serverURL)/api/v1/health") else { return false }
        do {
            log("health check: \(url.absoluteString)")
            let (_, response) = try await URLSession.shared.data(from: url)
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
        let stemDataMap = try await downloadStems(taskId: taskId)

        statusText = "保存文件..."
        progress = 0.9
        let urls = try saveStemFiles(stemDataMap)
        log("saved stems: vocals=\(urls.vocals.path), drums=\(urls.drums.path), bass=\(urls.bass.path), other=\(urls.other.path)")

        progress = 1.0
        statusText = "分离完成"
        return urls
    }

    private func submitTask(audioData: Data, filename: String) async throws -> String {
        guard let url = URL(string: "\(serverURL)/api/v1/separate/async") else {
            throw SeparationServiceError.serverUnavailable
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 60

        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")

        var body = Data()
        body.appendUTF8("--\(boundary)\r\n")
        body.appendUTF8("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        body.appendUTF8("Content-Type: application/octet-stream\r\n\r\n")
        body.append(audioData)
        body.appendUTF8("\r\n--\(boundary)--\r\n")

        let (data, response) = try await URLSession.shared.upload(for: request, from: body)
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
        guard let url = URL(string: "\(serverURL)/api/v1/task/\(taskId)") else {
            throw SeparationServiceError.serverUnavailable
        }

        while true {
            try await Task.sleep(nanoseconds: 3_000_000_000)
            let (data, response) = try await URLSession.shared.data(from: url)
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

    private func downloadStems(taskId: String) async throws -> [String: Data] {
        var result: [String: Data] = [:]
        let stemNames = ["vocals", "drums", "bass", "other"]

        for (i, name) in stemNames.enumerated() {
            guard let url = URL(string: "\(serverURL)/api/v1/task/\(taskId)/stem/\(name)") else {
                throw SeparationServiceError.serverUnavailable
            }
            let (data, response) = try await URLSession.shared.data(from: url)
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? -1
            log("download stem \(name): \(statusCode), bytes=\(data.count)")
            guard statusCode == 200 else {
                log("download stem \(name) failed body: \(Self.preview(data))")
                throw SeparationServiceError.downloadFailed
            }
            result[name] = data
            progress = 0.8 + Double(i + 1) / Double(stemNames.count) * 0.1
        }
        return result
    }

    private func saveStemFiles(_ stemDataMap: [String: Data]) throws -> StemURLs {
        let tmpDir = FileManager.default.temporaryDirectory
        let sessionID = UUID().uuidString

        func save(_ name: String) throws -> URL {
            let url = tmpDir.appendingPathComponent("demucs-\(name)-\(sessionID).wav")
            guard let data = stemDataMap[name] else {
                log("missing downloaded stem data: \(name)")
                throw SeparationServiceError.invalidResponse
            }
            try data.write(to: url)
            return url
        }

        let vocalsURL = try save("vocals")
        let drumsURL = try save("drums")
        let bassURL = try save("bass")
        let otherURL = try save("other")

        return StemURLs(vocals: vocalsURL, drums: drumsURL, bass: bassURL, other: otherURL)
    }

    private func log(_ message: String) {
        print("[DemucsService] \(message)")
    }

    private static func preview(_ data: Data, limit: Int = 500) -> String {
        guard !data.isEmpty else { return "<empty>" }
        let clipped = Data(data.prefix(limit))
        return String(data: clipped, encoding: .utf8) ?? "<\(data.count) binary bytes>"
    }
}

private extension Data {
    mutating func appendUTF8(_ string: String) {
        if let data = string.data(using: .utf8) { append(data) }
    }
}
