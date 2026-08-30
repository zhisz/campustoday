import SwiftUI

struct AuthView: View {
    @EnvironmentObject private var store: SessionStore
    @State private var username = ""
    @State private var password = ""
    @State private var registering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Spacer()
            Text("CampusToday").font(.headline).foregroundStyle(.blue).padding(.horizontal, 14).padding(.vertical, 8)
                .background(Color.blue.opacity(0.1), in: Capsule())
            Text(registering ? "创建你的账号" : "欢迎回来").font(.system(size: 36, weight: .heavy))
            Text("管理你的校园签到账号").foregroundStyle(.secondary)
            TextField("用户名", text: $username).textContentType(.username).textInputAutocapitalization(.never)
                .padding().background(.background, in: RoundedRectangle(cornerRadius: 12))
            SecureField("密码（至少 8 位）", text: $password).textContentType(registering ? .newPassword : .password)
                .padding().background(.background, in: RoundedRectangle(cornerRadius: 12))
            Button(registering ? "注册并登录" : "登录") {
                Task { await store.authenticate(username: username, password: password, register: registering) }
            }.buttonStyle(.borderedProminent).controlSize(.large).frame(maxWidth: .infinity).disabled(username.isEmpty || password.count < 8)
            Button(registering ? "已有账号？直接登录" : "没有账号？立即注册") { registering.toggle() }.frame(maxWidth: .infinity)
            Spacer()
            DeveloperSignature()
        }.padding(26)
    }
}
