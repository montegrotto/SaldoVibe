import Foundation

struct APIError: LocalizedError, Equatable {
    let message: String
    let status: Int
    var fieldErrors: [String: [String]] = [:]

    var errorDescription: String? { message }
    var isUnauthorized: Bool { status == 401 }
}

struct UploadFile {
    let name: String
    let mimeType: String
    let data: Data
}

/// Thin JSON client for /api/v1/. Auth header and company header are set once per session.
final class APIClient {
    let baseURL: URL
    var token: String?
    var companyId: Int?

    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    init(baseURL: URL, token: String? = nil, companyId: Int? = nil) {
        self.baseURL = baseURL
        self.token = token
        self.companyId = companyId
    }

    func get<T: Decodable>(_ path: String, query: [String: String] = [:]) async throws -> T {
        try await send(request(path, method: "GET", query: query))
    }

    func post<T: Decodable>(_ path: String, json: [String: Any] = [:]) async throws -> T {
        var request = request(path, method: "POST")
        request.httpBody = try JSONSerialization.data(withJSONObject: json)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try await send(request)
    }

    func postExpectingNoContent(_ path: String) async throws {
        _ = try await raw(request(path, method: "POST"))
    }

    func upload(_ path: String, file: UploadFile) async throws -> Attachment {
        let boundary = "saldovibe-\(UUID().uuidString)"
        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(file.name)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: \(file.mimeType)\r\n\r\n".data(using: .utf8)!)
        body.append(file.data)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        var request = request(path, method: "POST")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        return try await send(request)
    }

    /// Raw bytes, for attachment files and thumbnails.
    func data(_ path: String) async throws -> Data {
        try await raw(request(path, method: "GET")).0
    }

    // MARK: - plumbing

    private func request(_ path: String, method: String, query: [String: String] = [:]) -> URLRequest {
        var components = URLComponents(url: baseURL.appending(path: "api/v1/" + path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        var request = URLRequest(url: components.url!)
        request.httpMethod = method
        request.timeoutInterval = 60
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let companyId {
            request.setValue(String(companyId), forHTTPHeaderField: "X-Company-Id")
        }
        return request
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, _) = try await raw(request)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError(message: "Oväntat svar från servern (\(error.localizedDescription)).", status: 0)
        }
    }

    private func raw(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw APIError(message: "Kunde inte nå servern: \(error.localizedDescription)", status: 0)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError(message: "Oväntat svar från servern.", status: 0)
        }
        guard (200..<300).contains(http.statusCode) else {
            struct Payload: Decodable {
                let error: String
                let errors: [String: [String]]?
            }
            if let payload = try? decoder.decode(Payload.self, from: data) {
                throw APIError(message: payload.error, status: http.statusCode, fieldErrors: payload.errors ?? [:])
            }
            throw APIError(message: "Servern svarade \(http.statusCode).", status: http.statusCode)
        }
        return (data, http)
    }
}
