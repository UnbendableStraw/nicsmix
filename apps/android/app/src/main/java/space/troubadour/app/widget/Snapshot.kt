package space.troubadour.app.widget

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.Shader
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import org.json.JSONObject
import space.troubadour.app.net.Agent
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.sin

/** What the widgets draw, from /api/state, /api/room and /api/led of the room the app last opened. */
data class FieldState(val ux: Double = 0.0, val uy: Double = 0.0, val rot: Double = 90.0, val width: Double = 95.0, val radius: Double = 0.0, val spatial: Boolean = false) {
    companion object { fun from(o: JSONObject?) = if (o == null) FieldState() else FieldState(o.optDouble("ux", 0.0), o.optDouble("uy", 0.0), o.optDouble("rot", 90.0), o.optDouble("width", 95.0), o.optDouble("radius", 0.0), o.optBoolean("spatial", false)) }
}
data class Speaker(val x: Double, val y: Double, val primary: Boolean)
data class Snapshot(
    val room: String = "Troubadour", val field: FieldState = FieldState(), val speakers: List<Speaker> = emptyList(),
    val listenerX: Double = 0.0, val listenerY: Double = 0.0, val ledMode: String? = null,
    val ledModes: List<String> = listOf("pulse", "party", "flow", "groove", "spectrum", "vu", "beat"), val playing: String? = null, val reachable: Boolean = false
) {
    companion object {
        fun agentIp(ctx: Context): String? = ctx.getSharedPreferences("fleet", Context.MODE_PRIVATE).getString("lastAgent", null)

        suspend fun fetch(ctx: Context): Snapshot {
            val ip = agentIp(ctx) ?: return Snapshot()
            val a = Agent(ip)
            return coroutineScope {
                val st = async { runCatching { a.get("/api/state", 4, 3) }.getOrNull() }
                val rm = async { runCatching { a.get("/api/room", 4, 3) }.getOrNull() }
                val ld = async { runCatching { a.get("/api/led", 4, 3) }.getOrNull() }
                var s = Snapshot()
                st.await()?.let { o ->
                    var playing: String? = null
                    o.optJSONObject("sources")?.let { src -> for (k in src.keys()) { val v = src.getJSONObject(k); if (v.optBoolean("active") && v.optBoolean("playing")) playing = v.optString("title").ifEmpty { k } } }
                    val room = o.optJSONObject("room")
                    s = s.copy(reachable = true, field = FieldState.from(room?.optJSONObject("field")), playing = playing, room = room?.optString("name")?.ifEmpty { null } ?: s.room)
                }
                rm.await()?.let { o ->
                    val rs = o.optJSONObject("room_state") ?: JSONObject()
                    val rc = rs.optJSONObject("reference_circle") ?: JSONObject()
                    val cx = rc.optDouble("x_m", 0.0); val cy = rc.optDouble("y_m", 0.0); val r = max(rc.optDouble("radius_m", 1.0), 0.1)
                    val lp = rs.optJSONObject("listener_position") ?: JSONObject()
                    val sp = mutableListOf<Speaker>()
                    o.optJSONArray("cells")?.let { arr -> for (i in 0 until arr.length()) { val c = arr.getJSONObject(i); val p = c.optJSONObject("position") ?: continue; if (!p.has("x")) continue
                        sp.add(Speaker((p.optDouble("x") - cx) / r, (p.optDouble("y") - cy) / r, c.optString("role").lowercase() == "primary")) } }
                    s = s.copy(speakers = sp, listenerX = (lp.optDouble("x_m", cx) - cx) / r, listenerY = (lp.optDouble("y_m", cy) - cy) / r)
                }
                ld.await()?.let { o ->
                    val modes = o.optJSONArray("reactiveModes")?.let { arr -> (0 until arr.length()).map { arr.getString(it) } } ?: s.ledModes
                    s = s.copy(ledMode = if (o.optBoolean("reactive")) o.optString("mode").ifEmpty { null } else null, ledModes = if (modes.isEmpty()) s.ledModes else modes)
                }
                s
            }
        }
    }

    /** The room from above: circle, speakers as pucks, listener, the puck or the two spatial dots. */
    fun picture(px: Int): Bitmap {
        val bmp = Bitmap.createBitmap(px, px, Bitmap.Config.ARGB_8888); val c = Canvas(bmp)
        val R = px / 2f * 0.86f; val cx = px / 2f; val cy = px / 2f
        fun X(x: Double) = (cx + x * R).toFloat()
        fun Y(y: Double) = (cy - y * R).toFloat()
        val p = Paint(Paint.ANTI_ALIAS_FLAG)
        p.shader = RadialGradient(cx, cy, R, Color.rgb(41, 41, 41), Color.rgb(15, 15, 15), Shader.TileMode.CLAMP); c.drawCircle(cx, cy, R, p); p.shader = null
        p.style = Paint.Style.STROKE; p.strokeWidth = 1.5f; p.color = Color.rgb(77, 77, 77); c.drawCircle(cx, cy, R, p); p.style = Paint.Style.FILL
        if (field.spatial) {
            val rr = max(0.15, if (field.radius == 0.0) 0.7 else field.radius)
            for (sgn in listOf(-1.0, 1.0)) {
                val a = Math.toRadians(field.rot + sgn * field.width); val x = X(cos(a) * rr); val y = Y(sin(a) * rr)
                p.shader = RadialGradient(x, y, R * 0.14f, Color.WHITE, Color.argb(0, 90, 150, 255), Shader.TileMode.CLAMP); c.drawCircle(x, y, R * 0.14f, p); p.shader = null
            }
        } else {
            val x = X(field.ux); val y = Y(field.uy)
            p.shader = RadialGradient(x, y, R * 0.3f, Color.argb(230, 140, 190, 255), Color.argb(0, 50, 100, 230), Shader.TileMode.CLAMP); c.drawCircle(x, y, R * 0.3f, p); p.shader = null
            p.color = Color.WHITE; c.drawCircle(x, y, R * 0.07f, p)
        }
        p.style = Paint.Style.STROKE; p.color = Color.rgb(180, 180, 180); c.drawCircle(X(listenerX), Y(listenerY), 3f * px / 120f, p); p.style = Paint.Style.FILL
        for (s in speakers) {
            val x = X(s.x); val y = Y(s.y); val pr = R * 0.1f
            p.shader = RadialGradient(x - pr * 0.2f, y - pr * 0.3f, pr * 1.2f, Color.rgb(72, 72, 72), Color.rgb(10, 10, 10), Shader.TileMode.CLAMP); c.drawCircle(x, y, pr, p); p.shader = null
            if (s.primary) { p.style = Paint.Style.STROKE; p.color = Color.WHITE; c.drawCircle(x, y, pr + 2f, p); p.style = Paint.Style.FILL }
            p.color = Color.rgb(227, 162, 76)
            for (i in 0..2) { val a = i * 2 * Math.PI / 3 - Math.PI / 2; c.drawCircle(x + (cos(a) * pr * 0.45).toFloat(), y + (sin(a) * pr * 0.45).toFloat(), pr * 0.11f, p) }
        }
        return bmp
    }
}
