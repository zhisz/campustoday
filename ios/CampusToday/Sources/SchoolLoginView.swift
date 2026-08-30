import SwiftUI
import WebKit

private let schoolPortalURL = URL(string: "https://fdm.jxust.edu.cn/portal/index.html")!

struct SchoolLoginView: View {
    @EnvironmentObject private var store: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var status = "正在打开学校统一身份认证…"
    @State private var cookieValue: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                HStack(spacing: 9) {
                    Image(systemName: cookieValue == nil ? "lock.shield" : "checkmark.shield.fill")
                        .foregroundStyle(cookieValue == nil ? .secondary : .green)
                    Text(status).font(.footnote).frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.horizontal, 16).padding(.vertical, 11)
                .background(Color(uiColor: .secondarySystemGroupedBackground))

                SchoolPortalWebView(status: $status, cookieValue: $cookieValue)

                Button(cookieValue == nil ? "请先完成学校登录" : "添加这个校园账号") {
                    guard let cookieValue else { return }
                    Task {
                        if await store.addAccount(cookie: cookieValue) { dismiss() }
                    }
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(cookieValue == nil || store.busy)
                .frame(maxWidth: .infinity).padding(16)
                .background(.bar)
            }
            .navigationTitle("登录学校门户")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } } }
        }
    }
}

private struct SchoolPortalWebView: UIViewRepresentable {
    @Binding var status: String
    @Binding var cookieValue: String?

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = true
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.load(URLRequest(url: schoolPortalURL, cachePolicy: .reloadIgnoringLocalCacheData))
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate {
        var parent: SchoolPortalWebView
        init(parent: SchoolPortalWebView) { self.parent = parent }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation?) {
            inspectCookies(in: webView)
            guard parent.cookieValue == nil, webView.url?.absoluteString.contains("/portal/index.html") == true else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                webView.evaluateJavaScript("document.getElementById('ampLoginBtn')?.click()")
            }
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation?, withError error: Error) {
            parent.status = "页面加载失败：\(error.localizedDescription)"
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation?, withError error: Error) {
            parent.status = "无法连接学校门户：\(error.localizedDescription)"
        }

        private func inspectCookies(in webView: WKWebView) {
            webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { cookies in
                guard let auth = cookies.first(where: { $0.name == "MOD_AUTH_CAS" && !$0.value.isEmpty }) else {
                    DispatchQueue.main.async { self.parent.status = "请在页面中完成学校登录" }
                    return
                }
                DispatchQueue.main.async {
                    self.parent.cookieValue = auth.value
                    self.parent.status = "登录成功，已安全读取会话信息"
                }
            }
        }
    }
}
