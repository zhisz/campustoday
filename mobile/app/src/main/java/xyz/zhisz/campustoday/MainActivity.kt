package xyz.zhisz.campustoday

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

private const val PORTAL_URL = "https://fdm.jxust.edu.cn/portal/index.html"

data class CampusAccount(
    val id: Int, val name: String, val verified: Boolean, val autoEnabled: Boolean,
    val sessionStatus: String, val lastChecked: String?, val model: String
)

class AppController(private val activity: ComponentActivity) {
    private val store = SecureStore(activity)
    private val api = ApiClient { store.token }
    private val executor = Executors.newSingleThreadExecutor()

    var authenticated by mutableStateOf(!store.token.isNullOrBlank())
    var accounts by mutableStateOf<List<CampusAccount>>(emptyList())
    var selected by mutableStateOf<CampusAccount?>(null)
    var details by mutableStateOf<JSONObject?>(null)
    var busy by mutableStateOf(false)
    var message by mutableStateOf<String?>(null)
    var update by mutableStateOf<JSONObject?>(null)

    init { if (authenticated) loadAccounts() }

    fun auth(username: String, password: String, register: Boolean) = run {
        val body = JSONObject().put("username", username.trim()).put("password", password)
        val result = ApiClient { null }.post(if (register) "/api/v1/auth/register" else "/api/v1/auth/login", body)
        store.token = result.getString("token")
        authenticated = true
        message = null
        loadAccounts()
    }

    fun logout() {
        executor.execute { runCatching { api.post("/api/v1/auth/logout") } }
        store.token = null
        authenticated = false
        accounts = emptyList(); selected = null; details = null
    }

    fun loadAccounts() = run {
        val result = api.get("/api/v1/accounts")
        accounts = parseAccounts(result.getJSONArray("accounts"))
    }

    fun addCampusAccount(cookie: String, onDone: () -> Unit) = run {
        val body = JSONObject().put("session_cookie", cookie).put("device", deviceInfo(activity))
        val result = api.post("/api/v1/accounts", body)
        val account = parseAccount(result.getJSONObject("account"))
        accounts = accounts + account
        selected = account
        message = if (account.verified) "已识别账号：${account.name}" else "账号已添加，但当前会话无效"
        onDone()
        loadDetail(account.id)
    }

    fun loadDetail(id: Int) = run {
        details = api.get("/api/v1/accounts/$id")
        selected = parseAccount(details!!.getJSONObject("account"))
        accounts = accounts.map { if (it.id == id) selected!! else it }
    }

    fun toggle(account: CampusAccount) = run {
        val result = api.patch("/api/v1/accounts/${account.id}", JSONObject().put("auto_enabled", !account.autoEnabled))
        selected = parseAccount(result.getJSONObject("account"))
        accounts = accounts.map { if (it.id == account.id) selected!! else it }
        message = if (selected!!.autoEnabled) "已开启 ${selected!!.name} 的自动签到" else "已关闭自动签到"
    }

    fun check(account: CampusAccount) = run {
        val result = api.post("/api/v1/accounts/${account.id}/check")
        selected = parseAccount(result.getJSONObject("account"))
        accounts = accounts.map { if (it.id == account.id) selected!! else it }
        message = if (selected!!.sessionStatus == "VALID") "会话有效：${selected!!.name}" else "会话已失效，请重新添加账号"
        loadDetail(account.id)
    }

    fun delete(account: CampusAccount, onDone: () -> Unit) = run {
        api.delete("/api/v1/accounts/${account.id}")
        accounts = accounts.filterNot { it.id == account.id }
        selected = null; details = null; message = "账号已删除"
        onDone()
    }

    fun checkUpdate(showLatest: Boolean = true) = run {
        val latest = ApiClient { null }.get("/api/v1/app/version")
        if (latest.optInt("version_code") > BuildConfig.VERSION_CODE) update = latest
        else if (showLatest) message = "当前已是最新版本"
    }

    fun openUpdate() {
        update?.optString("download_url")?.takeIf { it.startsWith("https://") }?.let {
            activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(it)))
        }
    }

    private fun run(block: () -> Unit) {
        busy = true
        executor.execute {
            try { block() }
            catch (exc: Exception) {
                activity.runOnUiThread {
                    if (exc is ApiException && exc.status == 401) { store.token = null; authenticated = false }
                    message = exc.message ?: "操作失败"
                }
            } finally { activity.runOnUiThread { busy = false } }
        }
    }

    private fun parseAccounts(array: JSONArray) = (0 until array.length()).map { parseAccount(array.getJSONObject(it)) }
    private fun parseAccount(value: JSONObject): CampusAccount {
        val device = value.optJSONObject("device") ?: JSONObject()
        return CampusAccount(value.getInt("id"), value.optString("name", "未命名账号"), value.optBoolean("identity_verified"),
            value.optBoolean("auto_enabled"), value.optString("session_status", "UNKNOWN"),
            value.optString("last_checked_at").takeIf { it.isNotBlank() && it != "null" }, device.optString("model", "Android 设备"))
    }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val controller = AppController(this)
        setContent { CampusTheme { CampusApp(controller) } }
    }
}

@Composable
fun CampusTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = lightColorScheme(primary = Color(0xFF2257D7), secondary = Color(0xFF198754),
        background = Color(0xFFF5F7FB), surface = Color.White, onBackground = Color(0xFF17233B)), content = content)
}

@Composable
fun CampusApp(controller: AppController) {
    var screen by remember { mutableStateOf(if (controller.authenticated) "home" else "auth") }
    LaunchedEffect(controller.authenticated) { screen = if (controller.authenticated) "home" else "auth" }
    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        when (screen) {
            "auth" -> AuthScreen(controller)
            "school" -> SchoolLoginScreen(controller) { screen = "home" }
            "detail" -> DetailScreen(controller, onBack = { screen = "home" })
            else -> HomeScreen(controller, onAdd = { screen = "school" }, onAccount = {
                controller.selected = it; controller.loadDetail(it.id); screen = "detail"
            })
        }
        if (controller.busy) Box(Modifier.fillMaxSize().background(Color(0x55000000)), contentAlignment = Alignment.Center) {
            CircularProgressIndicator(color = Color.White)
        }
        controller.message?.let { msg -> Snackbar(Modifier.align(Alignment.BottomCenter).padding(18.dp), action = {
            TextButton(onClick = { controller.message = null }) { Text("知道了") }
        }) { Text(msg) } }
        controller.update?.let { latest -> AlertDialog(onDismissRequest = { controller.update = null },
            title = { Text("发现新版本 v${latest.optString("version_name")}") },
            text = { Text(latest.optString("release_notes", "优化使用体验")) },
            confirmButton = { Button(onClick = { controller.openUpdate() }) { Text("前往下载") } },
            dismissButton = { TextButton(onClick = { controller.update = null }) { Text("稍后") } }) }
    }
}

@Composable
fun AuthScreen(controller: AppController) {
    var username by remember { mutableStateOf("") }; var password by remember { mutableStateOf("") }; var register by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().padding(26.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.Center) {
        Surface(color = Color(0xFFE8EFFF), shape = RoundedCornerShape(50)) { Text("CampusToday", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold, modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp)) }
        Spacer(Modifier.height(24.dp)); Text(if (register) "创建你的账号" else "欢迎回来", fontSize = 34.sp, fontWeight = FontWeight.ExtraBold)
        Text("管理你的校园签到账号", color = Color(0xFF6B778C), modifier = Modifier.padding(top = 8.dp, bottom = 28.dp))
        OutlinedTextField(username, { username = it }, Modifier.fillMaxWidth(), label = { Text("用户名") }, singleLine = true)
        Spacer(Modifier.height(12.dp)); OutlinedTextField(password, { password = it }, Modifier.fillMaxWidth(), label = { Text("密码（至少 8 位）") }, singleLine = true, visualTransformation = PasswordVisualTransformation())
        Button(onClick = { controller.auth(username, password, register) }, enabled = username.isNotBlank() && password.length >= 8, modifier = Modifier.fillMaxWidth().padding(top = 22.dp).height(52.dp)) { Text(if (register) "注册并登录" else "登录") }
        TextButton(onClick = { register = !register }, modifier = Modifier.align(Alignment.CenterHorizontally)) { Text(if (register) "已有账号？直接登录" else "没有账号？立即注册") }
        DeveloperSignature()
    }
}

@Composable
fun Header(title: String, subtitle: String, trailing: @Composable RowScope.() -> Unit = {}) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) { Text(title, fontSize = 27.sp, fontWeight = FontWeight.ExtraBold); Text(subtitle, color = Color(0xFF738096), fontSize = 14.sp) }
        trailing()
    }
}

@Composable
fun HomeScreen(controller: AppController, onAdd: () -> Unit, onAccount: (CampusAccount) -> Unit) {
    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp).verticalScroll(rememberScrollState())) {
        Spacer(Modifier.height(24.dp)); Header("我的账号", "只显示你添加的校园账号") { TextButton(onClick = { controller.logout() }) { Text("退出") } }
        Row(Modifier.fillMaxWidth().padding(vertical = 18.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(onClick = onAdd, modifier = Modifier.weight(1f)) { Text("+添加校园账号") }
            OutlinedButton(onClick = { controller.loadAccounts() }) { Text("刷新") }
        }
        if (controller.accounts.isEmpty()) EmptyCard("还没有账号", "点击上方按钮，在学校页面完成登录即可添加。")
        controller.accounts.forEach { account -> AccountCard(account, onClick = { onAccount(account) }, onToggle = { controller.toggle(account) }) }
        OutlinedButton(onClick = { controller.checkUpdate() }, Modifier.fillMaxWidth().padding(top = 12.dp)) { Text("检查 App 更新") }
        Text("位置由服务器统一管理，本 App 不会读取或上传位置。", color = Color(0xFF7B8798), fontSize = 12.sp, modifier = Modifier.padding(vertical = 20.dp))
        DeveloperSignature()
    }
}

@Composable
fun AccountCard(account: CampusAccount, onClick: () -> Unit, onToggle: () -> Unit) {
    Card(onClick = onClick, modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp), shape = RoundedCornerShape(18.dp), colors = CardDefaults.cardColors(containerColor = Color.White)) {
        Column(Modifier.padding(18.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) { Column(Modifier.weight(1f)) { Text(account.name, fontSize = 20.sp, fontWeight = FontWeight.Bold); Text(account.model, color = Color(0xFF758197), fontSize = 13.sp) }; StatusBadge(account.sessionStatus) }
            HorizontalDivider(Modifier.padding(vertical = 14.dp), color = Color(0xFFEDF0F5))
            Row(verticalAlignment = Alignment.CenterVertically) { Text("自动签到", Modifier.weight(1f), fontWeight = FontWeight.Medium); Switch(account.autoEnabled, onCheckedChange = { onToggle() }) }
        }
    }
}

@Composable
fun StatusBadge(status: String) {
    val valid = status == "VALID"; Surface(color = if (valid) Color(0xFFE6F5EA) else Color(0xFFFFEEE5), shape = RoundedCornerShape(50)) {
        Text(if (valid) "会话有效" else if (status == "INVALID") "会话失效" else "待检测", color = if (valid) Color(0xFF157A35) else Color(0xFFAA4B13), fontSize = 12.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp))
    }
}

@Composable
fun SchoolLoginScreen(controller: AppController, onBack: () -> Unit) {
    val context = LocalContext.current; var hint by remember { mutableStateOf("正在为你打开学校统一身份认证…") }
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.padding(18.dp)) { Header("登录学校门户", hint) { TextButton(onClick = onBack) { Text("取消") } } }
        AndroidView(factory = { WebView(context).apply {
            settings.javaScriptEnabled = true; settings.domStorageEnabled = true
            CookieManager.getInstance().setAcceptCookie(true); CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView, url: String) {
                    super.onPageFinished(view, url)
                    val signedIn = CookieManager.getInstance().getCookie("https://fdm.jxust.edu.cn").orEmpty().contains("MOD_AUTH_CAS=")
                    if (signedIn) hint = "已检测到学校登录，请点击底部按钮添加"
                    else if (url.contains("/portal/index.html")) view.postDelayed({
                        view.evaluateJavascript("(function(){var b=document.getElementById('ampLoginBtn');if(b){b.click();return 'clicked';}return 'missing';})()", null)
                    }, 1600)
                }
            }
            CookieManager.getInstance().removeAllCookies { loadUrl(PORTAL_URL) }
        } }, modifier = Modifier.weight(1f).fillMaxWidth())
        Button(onClick = {
            val cookie = CookieManager.getInstance().getCookie("https://fdm.jxust.edu.cn").orEmpty()
            if (cookie.contains("MOD_AUTH_CAS=")) controller.addCampusAccount(cookie, onBack) else hint = "尚未检测到有效登录，请先完成学校登录"
        }, Modifier.fillMaxWidth().padding(16.dp).height(52.dp)) { Text("已完成登录，添加账号") }
    }
}

@Composable
fun DetailScreen(controller: AppController, onBack: () -> Unit) {
    val account = controller.selected ?: return
    val data = controller.details
    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp).verticalScroll(rememberScrollState())) {
        Spacer(Modifier.height(22.dp)); Header(account.name, "签到任务与记录") { TextButton(onClick = onBack) { Text("返回") } }
        Row(Modifier.fillMaxWidth().padding(vertical = 16.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(onClick = { controller.check(account) }, Modifier.weight(1f)) { Text("检测会话") }
            OutlinedButton(onClick = { controller.loadDetail(account.id) }) { Text("刷新") }
        }
        Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = Color.White)) { Column(Modifier.padding(18.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) { Text("账号状态", Modifier.weight(1f), fontWeight = FontWeight.Bold); StatusBadge(account.sessionStatus) }
            InfoRow("自动签到", if (account.autoEnabled) "已开启" else "已关闭"); InfoRow("自动成功", "${data?.optInt("automatic_successes", 0) ?: 0} 次"); InfoRow("本月已签", "${data?.optInt("signed_count", 0) ?: 0} 次")
            Button(onClick = { controller.toggle(account) }, Modifier.fillMaxWidth().padding(top = 10.dp), colors = ButtonDefaults.buttonColors(containerColor = if (account.autoEnabled) Color(0xFFB42318) else MaterialTheme.colorScheme.primary)) { Text(if (account.autoEnabled) "关闭自动签到" else "开启自动签到") }
        } }
        SectionTitle("待签任务"); JsonItems(data?.optJSONArray("tasks"), emptyText = "当前没有待签任务", titleKey = "name", detail = { "${it.optString("state")}  ·  ${it.optString("start")} — ${it.optString("end")}" })
        SectionTitle("最近签到记录"); JsonItems(data?.optJSONArray("history"), emptyText = "暂无签到记录", titleKey = "name", detail = { "${it.optString("date")}  ·  ${it.optString("status")}  ·  ${if (it.optBoolean("automatic")) "自动签到" else "学校记录"}" })
        TextButton(onClick = { controller.delete(account, onBack) }, Modifier.align(Alignment.CenterHorizontally).padding(vertical = 12.dp)) { Text("删除这个账号", color = Color(0xFFB42318)) }
    }
}

@Composable fun InfoRow(label: String, value: String) { Row(Modifier.fillMaxWidth().padding(top = 14.dp)) { Text(label, Modifier.weight(1f), color = Color(0xFF758197)); Text(value, fontWeight = FontWeight.SemiBold) } }
@Composable fun SectionTitle(text: String) { Text(text, fontSize = 19.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 24.dp, bottom = 10.dp)) }

@Composable
fun JsonItems(items: JSONArray?, emptyText: String, titleKey: String, detail: (JSONObject) -> String) {
    if (items == null || items.length() == 0) EmptyCard(emptyText, "") else (0 until items.length()).forEach { index ->
        val item = items.getJSONObject(index); Card(Modifier.fillMaxWidth().padding(bottom = 9.dp), colors = CardDefaults.cardColors(containerColor = Color.White)) { Column(Modifier.padding(16.dp)) { Text(item.optString(titleKey, "未命名"), fontWeight = FontWeight.Bold); Text(detail(item), color = Color(0xFF738096), fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp)) } }
    }
}

@Composable fun EmptyCard(title: String, detail: String) { Surface(Modifier.fillMaxWidth(), color = Color(0xFFEFF3F8), shape = RoundedCornerShape(16.dp)) { Column(Modifier.padding(22.dp), horizontalAlignment = Alignment.CenterHorizontally) { Text(title, fontWeight = FontWeight.Bold); if (detail.isNotBlank()) Text(detail, color = Color(0xFF758197), fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp)) } } }

@Composable fun DeveloperSignature() { Text("由 zhiSZ 开发", color = Color(0xFF98A2B3), fontSize = 12.sp, modifier = Modifier.fillMaxWidth().padding(vertical = 18.dp), textAlign = androidx.compose.ui.text.style.TextAlign.Center) }
