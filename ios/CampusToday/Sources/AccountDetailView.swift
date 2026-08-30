import SwiftUI

struct AccountDetailView: View {
    let accountID: Int
    @EnvironmentObject private var store: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var confirmDelete = false

    private var account: CampusAccount? { store.accounts.first { $0.id == accountID } }
    private var detail: AccountDetail? { store.details[accountID] }

    var body: some View {
        VStack(spacing: 10) {
            HStack(spacing: 10) {
                Button("刷新云端状态") { if let account { Task { await store.checkSession(account) } } }
                    .buttonStyle(.borderedProminent).controlSize(.large).frame(maxWidth: .infinity)
                Button("刷新") { Task { await store.loadDetail(accountID) } }.buttonStyle(.bordered).controlSize(.large)
            }

            if let account {
                VStack(spacing: 9) {
                    HStack { Text("账号状态").font(.headline); Spacer(); StatusBadge(valid: account.sessionStatus == "VALID") }
                    MetricRow(label: "自动成功", value: "\(detail?.automaticSuccesses ?? 0) 次")
                    MetricRow(label: "本月已签", value: "\(detail?.signedCount ?? 0) 次")
                    Button(account.autoEnabled ? "自动签到已开启" : "开启自动签到") { Task { await store.toggle(account) } }
                        .buttonStyle(.borderedProminent).tint(account.autoEnabled ? .green : .blue).frame(maxWidth: .infinity)
                }.padding(16).background(.background, in: RoundedRectangle(cornerRadius: 18))
            }

            SectionHeader("待签任务")
            if let task = detail?.tasks.first {
                CompactRow(title: task.name, detail: "\(task.state) · \(task.start) — \(task.end)")
            } else { CompactRow(title: "当前没有待签任务", detail: "") }

            SectionHeader("最近签到记录")
            ForEach(Array((detail?.history ?? []).prefix(3))) { item in
                CompactRow(title: item.name, detail: "\(item.date) · \(item.status) · \(item.automatic ? "自动签到" : "云端记录")")
            }
            if detail?.history.isEmpty != false { CompactRow(title: "暂无签到记录", detail: "") }
            if let error = detail?.schoolError, !error.isEmpty { Text(error).font(.caption).foregroundStyle(.orange) }

            Spacer(minLength: 0)
            Button("删除这个账号", role: .destructive) { confirmDelete = true }
        }
        .padding(.horizontal, 20)
        .navigationTitle(account?.name ?? "账号详情")
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadDetail(accountID) }
        .confirmationDialog("确定删除这个校园账号？", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("永久删除", role: .destructive) {
                guard let account else { return }
                Task { if await store.delete(account) { dismiss() } }
            }
        } message: { Text("历史执行记录仍会保留。") }
    }
}

struct MetricRow: View {
    let label: String; let value: String
    var body: some View { HStack { Text(label).foregroundStyle(.secondary); Spacer(); Text(value).bold() } }
}

struct SectionHeader: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View { Text(text).font(.headline).frame(maxWidth: .infinity, alignment: .leading).padding(.top, 4) }
}

struct CompactRow: View {
    let title: String; let detail: String
    var body: some View {
        HStack {
            Text(title).font(.subheadline.bold()).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
            if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2).frame(maxWidth: .infinity, alignment: .leading) }
        }.padding(12).background(.background, in: RoundedRectangle(cornerRadius: 14))
    }
}
