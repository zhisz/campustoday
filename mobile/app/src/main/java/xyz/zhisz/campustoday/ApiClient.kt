package xyz.zhisz.campustoday

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

class ApiException(val status: Int, message: String) : IOException(message)

class ApiClient(private val tokenProvider: () -> String?) {
    fun get(path: String) = request("GET", path)
    fun post(path: String, body: JSONObject = JSONObject()) = request("POST", path, body)
    fun patch(path: String, body: JSONObject) = request("PATCH", path, body)
    fun delete(path: String) = request("DELETE", path)

    private fun request(method: String, path: String, body: JSONObject? = null): JSONObject {
        val connection = URL(BuildConfig.API_BASE_URL + path).openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.connectTimeout = 10_000
        connection.readTimeout = 20_000
        connection.setRequestProperty("Accept", "application/json")
        connection.setRequestProperty("Content-Type", "application/json; charset=UTF-8")
        connection.setRequestProperty("X-App-Version-Code", BuildConfig.VERSION_CODE.toString())
        connection.setRequestProperty("X-App-Version-Name", BuildConfig.VERSION_NAME)
        tokenProvider()?.takeIf { it.isNotBlank() }?.let { connection.setRequestProperty("Authorization", "Bearer $it") }
        if (body != null) {
            connection.doOutput = true
            connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        }
        val status = connection.responseCode
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
        val json = runCatching { JSONObject(text) }.getOrElse { JSONObject() }
        connection.disconnect()
        if (status !in 200..299) throw ApiException(status, json.optString("error", "服务器请求失败（$status）"))
        return json
    }
}
