import SwiftUI

struct AccountPrivacyView: View {
    @EnvironmentObject private var store: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var showingDeletion = false
    @State private var password = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("隐私") {
                    Button("查看隐私政策") { openURL(URL(string: "https://campustoday.zhisz.xyz/privacy")!) }
                    LabeledContent("手机定位", value: "不读取、不上传")
                    LabeledContent("登录令牌", value: "Keychain 加密保存")
                }
                Section("账号") {
                    Button("退出登录") {
                        Task { await store.logout(); dismiss() }
                    }
                    Button("永久删除 App 账号", role: .destructive) { showingDeletion = true }
                }
            }
            .navigationTitle("账号与隐私")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
            .sheet(isPresented: $showingDeletion) {
                NavigationStack {
                    Form {
                        Section {
                            Text("这会永久删除 App 用户、登录令牌、所添加的校园账号、相关签到记录和反馈，无法恢复。")
                                .foregroundStyle(.secondary)
                            SecureField("输入当前 App 账号密码", text: $password)
                                .textContentType(.password)
                        }
                        Section {
                            Button("确认永久删除", role: .destructive) {
                                Task {
                                    if await store.deleteUser(password: password) {
                                        showingDeletion = false
                                        dismiss()
                                    }
                                }
                            }
                            .disabled(password.count < 8 || store.busy)
                        }
                    }
                    .navigationTitle("删除账号")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { showingDeletion = false } } }
                }
                .presentationDetents([.medium])
            }
        }
    }
}
