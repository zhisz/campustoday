import Foundation
import UIKit

struct DeviceInfoPayload: Codable {
    let deviceId: String
    let appVersion: String
    let model: String
    let systemName: String
    let systemVersion: String
    enum CodingKeys: String, CodingKey {
        case model
        case deviceId = "device_id"
        case appVersion = "app_version"
        case systemName = "system_name"
        case systemVersion = "system_version"
    }

    static var current: DeviceInfoPayload {
        var system = utsname()
        uname(&system)
        let machine = withUnsafePointer(to: &system.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 1) { String(cString: $0) }
        }
        return DeviceInfoPayload(
            deviceId: UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString,
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0",
            model: machine,
            systemName: UIDevice.current.systemName,
            systemVersion: UIDevice.current.systemVersion
        )
    }
}

struct AccountCreateBody: Codable {
    let sessionCookie: String
    let device: DeviceInfoPayload
    enum CodingKeys: String, CodingKey { case device; case sessionCookie = "session_cookie" }
}

struct AutomationBody: Codable {
    let autoEnabled: Bool
    enum CodingKeys: String, CodingKey { case autoEnabled = "auto_enabled" }
}

struct CredentialsBody: Codable { let username: String; let password: String }
struct FeedbackBody: Codable { let category: String; let content: String }
struct DeleteUserBody: Codable { let password: String }
