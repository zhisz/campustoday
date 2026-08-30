import Foundation
import SwiftUI

enum VersionGateState: Equatable {
    case checking
    case ready
    case required(ReleaseInfo)
    case failed(String)

    static func == (lhs: VersionGateState, rhs: VersionGateState) -> Bool {
        switch (lhs, rhs) {
        case (.checking, .checking), (.ready, .ready): return true
        case let (.required(a), .required(b)): return a.versionCode == b.versionCode
        case let (.failed(a), .failed(b)): return a == b
        default: return false
        }
    }
}

@MainActor
final class SessionStore: ObservableObject {
    @Published var gate: VersionGateState = .checking
    @Published var authenticated = KeychainStore.token != nil
    @Published var accounts: [CampusAccount] = []
    @Published var details: [Int: AccountDetail] = [:]
    @Published var announcements: [Announcement] = []
    @Published var activeAnnouncement: Announcement?
    @Published var optionalUpdate: ReleaseInfo?
    @Published var busy = false
    @Published var message: String?

    private let api = APIClient.shared
    private var bootstrapped = false
    var currentVersionCode: Int { Int(Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "1") ?? 1 }

    func bootstrap() async {
        guard !bootstrapped else { return }
        bootstrapped = true
        await checkVersion(showLatest: false)
        guard case .ready = gate else { return }
        if authenticated { await refreshAccounts() }
    }

    func checkVersion(showLatest: Bool = true) async {
        let wasReady = gate == .ready
        if !wasReady { gate = .checking }
        do {
            let latest = try await api.release()
            if latest.versionCode > currentVersionCode {
                if latest.mandatory { gate = .required(latest) }
                else { optionalUpdate = latest; gate = .ready }
            } else {
                gate = .ready
                if showLatest { message = "当前已是最新版本" }
            }
        } catch {
            if wasReady { message = "检查更新失败：\(error.localizedDescription)" }
            else { gate = .failed(error.localizedDescription) }
        }
    }

    func authenticate(username: String, password: String, register: Bool) async {
        await perform {
            let result: AuthResponse = try await self.api.request(
                "POST", register ? "/api/v1/auth/register" : "/api/v1/auth/login",
                body: CredentialsBody(username: username.trimmingCharacters(in: .whitespacesAndNewlines), password: password),
                authenticated: false
            )
            KeychainStore.token = result.token
            self.authenticated = true
            await self.refreshAccounts(showBusy: false)
        }
    }

    func logout() async {
        let _: EmptyResponse? = try? await api.request("POST", "/api/v1/auth/logout")
        KeychainStore.token = nil
        authenticated = false
        accounts = []
        details = [:]
        announcements = []
    }

    func deleteUser(password: String) async -> Bool {
        var success = false
        await perform {
            let _: EmptyResponse = try await self.api.request("DELETE", "/api/v1/me", body: DeleteUserBody(password: password))
            KeychainStore.token = nil
            self.authenticated = false
            self.accounts = []
            self.details = [:]
            self.announcements = []
            success = true
        }
        return success
    }

    func refreshAccounts(showBusy: Bool = true) async {
        if showBusy { busy = true }
        defer { if showBusy { busy = false } }
        do {
            let response: AccountsResponse = try await api.request("GET", "/api/v1/accounts")
            accounts = response.accounts
            await syncAnnouncements()
        } catch { handle(error) }
    }

    func addAccount(cookie: String) async -> Bool {
        var success = false
        await perform {
            let response: AccountResponse = try await self.api.request(
                "POST", "/api/v1/accounts", body: AccountCreateBody(sessionCookie: cookie, device: .current)
            )
            self.accounts.removeAll { $0.id == response.account.id }
            self.accounts.append(response.account)
            self.message = response.account.identityVerified ? "已识别账号：\(response.account.name)" : "账号已添加，但当前会话无效"
            success = true
        }
        return success
    }

    func loadDetail(_ id: Int, showBusy: Bool = true) async {
        if showBusy { busy = true }
        defer { if showBusy { busy = false } }
        do {
            let detail: AccountDetail = try await api.request("GET", "/api/v1/accounts/\(id)")
            details[id] = detail
            replace(detail.account)
        } catch { handle(error) }
    }

    func toggle(_ account: CampusAccount) async {
        await perform {
            let response: AccountResponse = try await self.api.request(
                "PATCH", "/api/v1/accounts/\(account.id)", body: AutomationBody(autoEnabled: !account.autoEnabled)
            )
            self.replace(response.account)
            if var detail = self.details[account.id] {
                detail = AccountDetail(account: response.account, tasks: detail.tasks, history: detail.history,
                    month: detail.month, signedCount: detail.signedCount, automaticSuccesses: detail.automaticSuccesses,
                    schoolError: detail.schoolError)
                self.details[account.id] = detail
            }
        }
    }

    func checkSession(_ account: CampusAccount) async {
        await perform {
            let response: AccountResponse = try await self.api.request("POST", "/api/v1/accounts/\(account.id)/check")
            self.replace(response.account)
            self.message = response.account.sessionStatus == "VALID" ? "已刷新云端状态：\(response.account.name)" : "云端记录显示会话需要更新"
            await self.loadDetail(account.id, showBusy: false)
        }
    }

    func delete(_ account: CampusAccount) async -> Bool {
        var success = false
        await perform {
            let _: EmptyResponse = try await self.api.request("DELETE", "/api/v1/accounts/\(account.id)")
            self.accounts.removeAll { $0.id == account.id }
            self.details[account.id] = nil
            success = true
        }
        return success
    }

    func sendFeedback(category: String, content: String) async -> Bool {
        var success = false
        await perform {
            let _: FeedbackResponse = try await self.api.request("POST", "/api/v1/feedback",
                body: FeedbackBody(category: category, content: content))
            self.message = "反馈已实名提交，感谢你的建议"
            success = true
        }
        return success
    }

    func syncAnnouncements() async {
        guard authenticated else { return }
        do {
            let response: AnnouncementsResponse = try await api.request("GET", "/api/v1/announcements")
            announcements = response.announcements
            if activeAnnouncement == nil { activeAnnouncement = announcements.first { !$0.isRead } }
        } catch { handle(error, silently: true) }
    }

    func markAnnouncementRead() async {
        guard let current = activeAnnouncement else { return }
        if let index = announcements.firstIndex(where: { $0.id == current.id }) { announcements[index].isRead = true }
        activeAnnouncement = announcements.first { !$0.isRead }
        let _: EmptyResponse? = try? await api.request("POST", "/api/v1/announcements/\(current.id)/read")
    }

    private func replace(_ account: CampusAccount) {
        if let index = accounts.firstIndex(where: { $0.id == account.id }) { accounts[index] = account }
        else { accounts.append(account) }
    }

    private func perform(_ action: () async throws -> Void) async {
        busy = true
        defer { busy = false }
        do { try await action() } catch { handle(error) }
    }

    private func handle(_ error: Error, silently: Bool = false) {
        if let apiError = error as? APIError, case .unauthorized = apiError {
            KeychainStore.token = nil
            authenticated = false
            accounts = []
        }
        if !silently { message = error.localizedDescription }
    }
}
