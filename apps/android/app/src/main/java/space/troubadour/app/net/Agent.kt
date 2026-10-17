package space.troubadour.app.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import okio.source
import org.json.JSONObject
import java.io.InputStream
import java.util.concurrent.TimeUnit

/** The agent's JSON over HTTP on port 8080 (docs/SETUP.md). OkHttp + org.json, nothing else. */
class Agent(val ip: String) {
    val base get() = "http://$ip:8080"
    companion object {
        val http: OkHttpClient = OkHttpClient.Builder().connectTimeout(4, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()
        private val JSON = "application/json".toMediaType()
    }
    class HttpError(val code: Int, msg: String) : Exception("HTTP $code: $msg")

    private fun decode(code: Int, body: String?): JSONObject {
        val o = try { JSONObject(body ?: "") } catch (_: Exception) { null }
        if (code >= 400) throw HttpError(code, o?.optString("error") ?: (body ?: ""))
        if (o == null) throw Exception("unexpected reply")
        if (o.has("error")) throw HttpError(code, o.getString("error"))
        return o
    }
    suspend fun get(path: String, timeoutSec: Long = 8, connectSec: Long = 4): JSONObject = withContext(Dispatchers.IO) {
        val c = http.newBuilder().connectTimeout(connectSec, TimeUnit.SECONDS).readTimeout(timeoutSec, TimeUnit.SECONDS).build()
        c.newCall(Request.Builder().url(base + path).build()).execute().use { decode(it.code, it.body?.string()) }
    }
    suspend fun post(path: String, body: Map<String, Any?> = emptyMap(), timeoutSec: Long = 12): JSONObject = withContext(Dispatchers.IO) {
        val c = http.newBuilder().readTimeout(timeoutSec, TimeUnit.SECONDS).build()
        val rb = JSONObject(body).toString().toRequestBody(JSON)
        c.newCall(Request.Builder().url(base + path).post(rb).build()).execute().use { decode(it.code, it.body?.string()) }
    }
    /** Firmware image upload from a content:// stream (the setup page does this too; here for the native button). */
    suspend fun upload(path: String, name: String, length: Long, open: () -> InputStream): JSONObject = withContext(Dispatchers.IO) {
        val rb = object : RequestBody() {
            override fun contentType() = "application/octet-stream".toMediaType()
            override fun contentLength() = length
            override fun writeTo(sink: BufferedSink) { open().source().use { sink.writeAll(it) } }
        }
        val c = http.newBuilder().readTimeout(30, TimeUnit.MINUTES).writeTimeout(30, TimeUnit.MINUTES).build()
        c.newCall(Request.Builder().url(base + path).header("X-Filename", java.net.URLEncoder.encode(name, "UTF-8")).post(rb).build()).execute().use { decode(it.code, it.body?.string()) }
    }
}

// ---- models (hand-decoded; the agent's JSON is loose) ----

data class Cell(val serial: String, val ip: String, val name: String?, val role: String?, val agent: String?, val isMe: Boolean, val calibrated: Boolean?) {
    val shortSerial get() = serial.takeLast(5)
    val title get() = name ?: "Cell $shortSerial"
    companion object {
        fun from(o: JSONObject): Cell? {
            val s = o.optString("serial", ""); val ip = o.optString("ip", "")
            if (s.isEmpty() || ip.isEmpty()) return null
            return Cell(s, ip, o.optString("name").takeIf { it.isNotEmpty() && !o.isNull("name") }, o.optString("role").takeIf { it.isNotEmpty() },
                o.optString("agent").takeIf { it.isNotEmpty() && !o.isNull("agent") }, o.optBoolean("isMe", false), if (o.has("calibrated") && !o.isNull("calibrated")) o.optBoolean("calibrated") else null)
        }
    }
}

data class Room(val id: String, val name: String, val primaryIp: String?, val cells: List<Cell>) {
    val agent: Agent? get() = (primaryIp ?: cells.firstOrNull()?.ip)?.let { Agent(it) }
    companion object {
        fun from(o: JSONObject): Room? {
            val id = o.optString("room_id", ""); if (id.isEmpty()) return null
            val cells = (0 until (o.optJSONArray("cells")?.length() ?: 0)).mapNotNull { Cell.from(o.getJSONArray("cells").getJSONObject(it)) }.filter { it.agent != null || it.isMe }
            return Room(id, o.optString("room_name").takeIf { it.isNotEmpty() && !o.isNull("room_name") } ?: (if (id == "?") "Not in a room" else "Room"),
                o.optString("primary_ip").takeIf { it.isNotEmpty() && !o.isNull("primary_ip") }, cells)
        }
    }
}

data class Source(val id: String, val active: Boolean, val playing: Boolean, val title: String, val artist: String, val album: String, val volume: Double, val hasArtwork: Boolean, val mute: Boolean) {
    val displayName get() = when (id) { "airplay2" -> "AirPlay"; "spotify" -> "Spotify"; "tidal" -> "TIDAL"; "hdmi" -> "HDMI"; else -> id.replaceFirstChar { it.uppercase() } }
    companion object {
        fun from(id: String, o: JSONObject) = Source(id, o.optBoolean("active"), o.optBoolean("playing"), o.optString("title", ""), o.optString("artist", ""), o.optString("album", ""),
            o.optDouble("volume", 0.0).let { if (it.isNaN()) 0.0 else it }, o.optBoolean("artwork", false), o.optBoolean("mute", false))
        fun isMusic(id: String) = !id.startsWith("Calibration") && id != "saplayer" && !id.startsWith("file")
    }
}

data class RoomState(val sources: List<Source> = emptyList(), val soundMode: String = "syng", val roomName: String = "", val viaPrimary: String? = null) {
    val active get() = sources.firstOrNull { it.active }
    val volume get() = active?.volume ?: sources.firstOrNull()?.volume ?: 0.0
    companion object {
        fun from(o: JSONObject): RoomState {
            val src = o.optJSONObject("sources") ?: JSONObject()
            val list = src.keys().asSequence().filter { Source.isMusic(it) }.map { Source.from(it, src.getJSONObject(it)) }.sortedBy { it.id }.toList()
            return RoomState(list, o.optJSONObject("settings")?.optString("sound_mode", "syng") ?: "syng", o.optJSONObject("room")?.optString("name", "") ?: "",
                o.optString("viaPrimary").takeIf { it.isNotEmpty() && !o.isNull("viaPrimary") })
        }
    }
}
