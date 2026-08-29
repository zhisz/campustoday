package xyz.zhisz.campustoday

import android.content.Context
import android.os.Build
import android.provider.Settings
import org.json.JSONObject

fun deviceInfo(context: Context) = JSONObject().apply {
    put("device_id", Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID) ?: "unknown")
    put("app_version", BuildConfig.VERSION_NAME)
    put("model", listOf(Build.MANUFACTURER, Build.MODEL).filter { it.isNotBlank() }.joinToString(" ").trim())
    put("system_name", "Android")
    put("system_version", Build.VERSION.RELEASE)
}
