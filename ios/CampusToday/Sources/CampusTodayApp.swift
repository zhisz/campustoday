import SwiftUI

@main
struct CampusTodayApp: App {
    @StateObject private var store = SessionStore()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .task { await store.bootstrap() }
                .task {
                    while !Task.isCancelled {
                        try? await Task.sleep(for: .seconds(15))
                        if scenePhase == .active { await store.syncAnnouncements() }
                    }
                }
                .onChange(of: scenePhase) { _, phase in
                    guard phase == .active else { return }
                    Task {
                        await store.checkVersion(showLatest: false)
                        if case .ready = store.gate { await store.syncAnnouncements() }
                    }
                }
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var store: SessionStore

    var body: some View {
        Group {
            switch store.gate {
            case .checking: ProgressView("正在检查最新版本…")
            case .failed(let reason): VersionFailureView(reason: reason)
            case .required(let release): RequiredUpdateView(release: release)
            case .ready:
                if store.authenticated { HomeView() } else { AuthView() }
            }
        }
        .tint(Color(red: 0.13, green: 0.34, blue: 0.84))
        .background(Color(uiColor: .systemGroupedBackground).ignoresSafeArea())
        .alert(item: $store.activeAnnouncement) { announcement in
            Alert(title: Text(announcement.title), message: Text(announcement.content), dismissButton: .default(Text("我知道了")) {
                Task { await store.markAnnouncementRead() }
            })
        }
        .alert("提示", isPresented: Binding(get: { store.message != nil }, set: { if !$0 { store.message = nil } })) {
            Button("知道了") { store.message = nil }
        } message: { Text(store.message ?? "") }
        .overlay(alignment: .top) {
            if store.busy { ProgressView().padding(10).background(.ultraThinMaterial, in: Capsule()).padding(.top, 8) }
        }
    }
}

struct RequiredUpdateView: View {
    let release: ReleaseInfo
    @Environment(\.openURL) private var openURL
    var body: some View {
        VStack(spacing: 18) {
            Image(systemName: "arrow.down.circle.fill").font(.system(size: 64)).foregroundStyle(.blue)
            Text("必须更新后才能继续").font(.largeTitle.bold()).multilineTextAlignment(.center)
            Text("当前版本已停止服务，请更新到 v\(release.versionName)。").foregroundStyle(.secondary)
            Button("立即更新") { openURL(release.downloadURL) }.buttonStyle(.borderedProminent).controlSize(.large)
            DeveloperSignature()
        }.padding(28)
    }
}

struct VersionFailureView: View {
    let reason: String
    @EnvironmentObject private var store: SessionStore
    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "wifi.exclamationmark").font(.system(size: 56)).foregroundStyle(.orange)
            Text("无法检查最新版本").font(.title.bold())
            Text("为确保使用安全版本，请联网后重试。\n\(reason)").foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("重新检查") { Task { await store.checkVersion(showLatest: false) } }.buttonStyle(.borderedProminent)
        }.padding(28)
    }
}

struct DeveloperSignature: View {
    var body: some View { Text("由 zhiSZ 开发").font(.caption).foregroundStyle(.tertiary).frame(maxWidth: .infinity) }
}
