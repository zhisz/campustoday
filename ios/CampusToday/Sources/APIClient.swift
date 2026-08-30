import Foundation

final class APIClient {
    static let shared = APIClient()
    private let baseURL = URL(string: "https://campustoday.zhisz.xyz")!
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    private var versionCode: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "1"
    }
    private var versionName: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0"
    }

    func request<Response: Decodable, Body: Encodable>(
        _ method: String, _ path: String, body: Body? = nil, authenticated: Bool = true
    ) async throws -> Response {
        guard let url = URL(string: path, relativeTo: baseURL) else { throw APIError.invalidResponse }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.setValue("ios", forHTTPHeaderField: "X-App-Platform")
        request.setValue(versionCode, forHTTPHeaderField: "X-App-Version-Code")
        request.setValue(versionName, forHTTPHeaderField: "X-App-Version-Name")
        if authenticated, let token = KeychainStore.token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body { request.httpBody = try encoder.encode(body) }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard 200..<300 ~= http.statusCode else {
            let payload = try? decoder.decode(APIErrorPayload.self, from: data)
            let message = payload?.error ?? "服务器请求失败（\(http.statusCode)）"
            if http.statusCode == 401 { throw APIError.unauthorized(message) }
            throw APIError.server(http.statusCode, message)
        }
        return try decoder.decode(Response.self, from: data)
    }

    func request<Response: Decodable>(_ method: String, _ path: String, authenticated: Bool = true) async throws -> Response {
        try await request(method, path, body: Optional<String>.none, authenticated: authenticated)
    }

    func release() async throws -> ReleaseInfo {
        try await request("GET", "/api/v1/app/version?platform=ios", authenticated: false)
    }
}
