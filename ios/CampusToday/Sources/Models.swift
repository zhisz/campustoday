import Foundation

struct AppUser: Codable { let id: Int; let username: String }

struct AuthResponse: Codable {
    let token: String
    let expiresAt: String
    let user: AppUser
    enum CodingKeys: String, CodingKey { case token, user; case expiresAt = "expires_at" }
}

struct DeviceProfile: Codable {
    let deviceId: String?
    let model: String?
    let systemName: String?
    let systemVersion: String?
    let appVersion: String?
    enum CodingKeys: String, CodingKey {
        case model
        case deviceId = "device_id"
        case systemName = "system_name"
        case systemVersion = "system_version"
        case appVersion = "app_version"
    }
}

struct CampusAccount: Codable, Identifiable, Equatable {
    let id: Int
    let name: String
    let identityVerified: Bool
    var autoEnabled: Bool
    let sessionStatus: String
    let lastCheckedAt: String?
    let lastError: String?
    let device: DeviceProfile?
    enum CodingKeys: String, CodingKey {
        case id, name, device
        case identityVerified = "identity_verified"
        case autoEnabled = "auto_enabled"
        case sessionStatus = "session_status"
        case lastCheckedAt = "last_checked_at"
        case lastError = "last_error"
    }
}

struct AccountsResponse: Codable { let accounts: [CampusAccount] }
struct AccountResponse: Codable { let account: CampusAccount }

struct SignTask: Codable, Identifiable {
    var id: String { "\(name)-\(start)-\(end)" }
    let name: String
    let state: String
    let start: String
    let end: String
}

struct SignHistory: Codable, Identifiable {
    var id: String { "\(date)-\(name)-\(time)" }
    let date: String
    let name: String
    let status: String
    let time: String
    let publisher: String
    let automatic: Bool
}

struct AccountDetail: Codable {
    let account: CampusAccount
    let tasks: [SignTask]
    let history: [SignHistory]
    let month: String
    let signedCount: Int
    let automaticSuccesses: Int
    let schoolError: String?
    enum CodingKeys: String, CodingKey {
        case account, tasks, history, month
        case signedCount = "signed_count"
        case automaticSuccesses = "automatic_successes"
        case schoolError = "school_error"
    }
}

struct Announcement: Codable, Identifiable, Equatable {
    let id: Int
    let title: String
    let content: String
    let createdAt: String
    let startsAt: String?
    let endsAt: String?
    var isRead: Bool
    enum CodingKeys: String, CodingKey {
        case id, title, content
        case createdAt = "created_at"
        case startsAt = "starts_at"
        case endsAt = "ends_at"
        case isRead = "is_read"
    }
}

struct AnnouncementsResponse: Codable { let announcements: [Announcement] }

struct ReleaseInfo: Codable, Identifiable {
    var id: Int { versionCode }
    let versionCode: Int
    let versionName: String
    let downloadURL: URL
    let mandatory: Bool
    let releaseNotes: String?
    let platform: String?
    enum CodingKeys: String, CodingKey {
        case mandatory, platform
        case versionCode = "version_code"
        case versionName = "version_name"
        case downloadURL = "download_url"
        case releaseNotes = "release_notes"
    }
}

struct EmptyResponse: Codable { let ok: Bool? }
struct FeedbackResponse: Codable { let id: Int; let ok: Bool }

struct APIErrorPayload: Codable {
    let error: String
    let upgradeRequired: Bool?
    enum CodingKeys: String, CodingKey { case error; case upgradeRequired = "upgrade_required" }
}

enum APIError: LocalizedError {
    case server(Int, String)
    case unauthorized(String)
    case invalidResponse
    var errorDescription: String? {
        switch self {
        case let .server(_, message), let .unauthorized(message): message
        case .invalidResponse: "服务器返回了无法识别的数据"
        }
    }
}
