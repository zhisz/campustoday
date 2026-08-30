# CampusToday for iOS

原生 SwiftUI 客户端，对应 Android 客户端的账号注册/登录、学校门户登录、多校园账号、设备信息识别、云端签到任务与最近记录、云端状态刷新、自动签到开关、公告弹窗、实名反馈和强制版本检查功能。

## 构建

1. 使用 Xcode 16 或更高版本打开 `CampusToday.xcodeproj`。
2. 在 CampusToday Target 的 Signing & Capabilities 中选择开发团队；Bundle ID 默认为 `xyz.zhisz.campustoday`。
3. 连接运行 iOS 17 或更高版本的 iPhone，选择设备后运行。
4. 归档分发时使用 Apple Developer 证书和对应的 Provisioning Profile。

工程不申请定位权限，也不会从 iPhone 读取或上传位置。App 令牌保存在 Keychain；学校门户使用不持久化 Web 会话，提取到的登录会话只发送给 CampusToday 服务端。
