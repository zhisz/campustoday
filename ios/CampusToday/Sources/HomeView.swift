import SwiftUI

struct HomeView: View {
    @EnvironmentObject private var store: SessionStore
    @Environment(\.openURL) private var openURL
    @State private var showSchoolLogin = false
    @State private var showFeedback = false
    @State private var showAccountPrivacy = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    HStack(spacing: 10) {
                        Button("+ 添加校园账号") { showSchoolLogin = true }.buttonStyle(.borderedProminent).controlSize(.large).frame(maxWidth: .infinity)
                        Button("刷新") { Task { await store.refreshAccounts() } }.buttonStyle(.bordered).controlSize(.large)
                    }
                    Button("提交反馈") { showFeedback = true }.buttonStyle(.bordered).controlSize(.large).frame(maxWidth: .infinity)

                    if store.accounts.isEmpty {
                        ContentUnavailableView("还没有账号", systemImage: "person.crop.circle.badge.plus", description: Text("登录学校门户后即可添加。"))
                            .padding(.vertical, 45)
                    } else {
                        ForEach(store.accounts) { account in
                            NavigationLink { AccountDetailView(accountID: account.id) } label: { AccountCard(account: account) }.buttonStyle(.plain)
                        }
                    }
                    Button("检查 App 更新") { Task { await store.checkVersion() } }.buttonStyle(.bordered).controlSize(.large).frame(maxWidth: .infinity)
                    Button("Github开源地址") { openURL(URL(string: "https://github.com/zhisz/campustoday")!) }
                    Text("位置由服务器统一管理，本 App 不会读取或上传位置。").font(.caption).foregroundStyle(.secondary).padding(.top, 10)
                    DeveloperSignature().padding(.vertical, 12)
                }.padding(.horizontal, 20)
            }
            .navigationTitle("我的账号")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("账号与隐私", systemImage: "person.crop.circle.badge.checkmark") { showAccountPrivacy = true }
                        Button("退出登录", systemImage: "rectangle.portrait.and.arrow.right") { Task { await store.logout() } }
                    } label: { Image(systemName: "ellipsis.circle") }
                }
            }
            .sheet(isPresented: $showSchoolLogin) { SchoolLoginView() }
            .sheet(isPresented: $showFeedback) { FeedbackView() }
            .sheet(isPresented: $showAccountPrivacy) { AccountPrivacyView() }
            .refreshable { await store.refreshAccounts(showBusy: false) }
        }
    }
}

struct AccountCard: View {
    let account: CampusAccount
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading) {
                    Text(account.name).font(.title2.bold())
                    Text(account.device?.model ?? "Apple 设备").font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                StatusBadge(valid: account.sessionStatus == "VALID")
            }
            Divider()
            HStack {
                Text(account.autoEnabled ? "自动签到已开启" : "自动签到已关闭").font(.caption.bold()).foregroundStyle(.green)
                    .padding(.horizontal, 10).padding(.vertical, 6).background(Color.green.opacity(0.12), in: Capsule())
                Spacer()
                Image(systemName: account.autoEnabled ? "checkmark.circle.fill" : "circle").foregroundStyle(.green)
            }
            HStack { Spacer(); Text("查看签到任务与记录").foregroundStyle(.blue).font(.subheadline.bold()); Image(systemName: "chevron.right").foregroundStyle(.blue) }
        }.padding(18).background(.background, in: RoundedRectangle(cornerRadius: 20)).overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.secondary.opacity(0.12)))
    }
}

struct StatusBadge: View {
    let valid: Bool
    var body: some View {
        Text(valid ? "会话有效" : "会话失效").font(.caption.bold()).foregroundStyle(valid ? .green : .orange)
            .padding(.horizontal, 10).padding(.vertical, 6).background((valid ? Color.green : Color.orange).opacity(0.12), in: Capsule())
    }
}
