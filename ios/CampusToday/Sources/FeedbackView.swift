import SwiftUI

struct FeedbackView: View {
    @EnvironmentObject private var store: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var category = "使用建议"
    @State private var content = ""

    var body: some View {
        NavigationStack {
            Form {
                Section { Text("反馈会附带你的 App 用户名，方便管理员跟进。").foregroundStyle(.secondary) }
                Section("分类") { TextField("分类", text: $category) }
                Section("反馈内容") { TextEditor(text: $content).frame(minHeight: 150) }
            }
            .navigationTitle("提交实名反馈")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("提交") {
                        Task { if await store.sendFeedback(category: String(category.prefix(40)), content: String(content.prefix(2000))) { dismiss() } }
                    }.disabled(content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }
}
