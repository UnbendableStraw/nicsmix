package space.troubadour.app.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.Button
import androidx.glance.ButtonDefaults
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalSize
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import space.troubadour.app.MainActivity
import space.troubadour.app.net.Agent
import kotlin.math.max
import kotlin.math.min

// Home-screen widgets for the room the app last opened (the visualizer is in AuraWidget.kt): the sound field (puck / spatial dots, with buttons to
// centre, switch classic / spatial, widen or narrow, nudge) and the LEDs (current mode, a button per reactive mode
// and off). Narrow sizes are the picture alone; tapping anything that isn't a button opens the app.

private val WHITE = ColorProvider(Color.White); private val BLACK = ColorProvider(Color.Black)
private val MUTED = ColorProvider(Color(0xFF8E8E8E)); private val SURFACE2 = ColorProvider(Color(0xFF2A2A2A)); private val AMBER = ColorProvider(Color(0xFFE3A24C))
private val SIZES = SizeMode.Responsive(setOf(DpSize(110.dp, 110.dp), DpSize(250.dp, 110.dp), DpSize(250.dp, 180.dp)))
val ACTION = ActionParameters.Key<String>("action")
val MODE = ActionParameters.Key<String>("mode")

@Composable
private fun Pill(text: String, filled: Boolean, modifier: GlanceModifier = GlanceModifier, onClick: androidx.glance.action.Action) {
    Button(text = text, onClick = onClick, modifier = modifier.height(30.dp),
        style = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.Medium, color = if (filled) BLACK else WHITE),
        colors = ButtonDefaults.buttonColors(backgroundColor = if (filled) WHITE else SURFACE2, contentColor = if (filled) BLACK else WHITE), maxLines = 1)
}

// ---- sound field ----

class FieldWidget : GlanceAppWidget() {
    override val sizeMode = SIZES
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val snap = Snapshot.fetch(context)
        provideContent { FieldContent(snap) }
    }
}
class FieldWidgetReceiver : GlanceAppWidgetReceiver() { override val glanceAppWidget: GlanceAppWidget = FieldWidget() }

@Composable
private fun FieldContent(s: Snapshot) {
    val wide = LocalSize.current.width >= 200.dp
    val pic = (LocalSize.current.height.value * 3).toInt().coerceIn(120, 600)
    Row(GlanceModifier.fillMaxSize().background(Color.Black).cornerRadius(20.dp).padding(10.dp).clickable(actionStartActivity<MainActivity>()), verticalAlignment = Alignment.CenterVertically) {
        Box(GlanceModifier.size(LocalSize.current.height - 20.dp), contentAlignment = Alignment.Center) {
            Image(ImageProvider(s.picture(pic)), null, GlanceModifier.fillMaxSize())
            if (!s.reachable) Text("not reachable", style = TextStyle(color = MUTED, fontSize = 10.sp))
        }
        if (wide) {
            Spacer(GlanceModifier.width(10.dp))
            Column(GlanceModifier.defaultWeight()) {
                Text(s.room, style = TextStyle(color = WHITE, fontSize = 13.sp, fontWeight = FontWeight.Bold), maxLines = 1)
                Text(if (s.field.spatial) "Spatial · ${s.field.width.toInt()}°" else "Classic", style = TextStyle(color = MUTED, fontSize = 10.sp))
                Spacer(GlanceModifier.height(6.dp))
                Row(GlanceModifier.fillMaxWidth()) {
                    Pill("Centre", true, GlanceModifier.defaultWeight(), actionRunCallback<FieldAction>(actionParametersOf(ACTION to "center")))
                    Spacer(GlanceModifier.width(6.dp))
                    Pill(if (s.field.spatial) "Classic" else "Spatial", true, GlanceModifier.defaultWeight(), actionRunCallback<FieldAction>(actionParametersOf(ACTION to if (s.field.spatial) "classic" else "spatial")))
                }
                Spacer(GlanceModifier.height(6.dp))
                Row(GlanceModifier.fillMaxWidth()) {
                    for ((i, a) in listOf("−" to "narrower", "+" to "wider", "↑" to "up", "↓" to "down").withIndex()) {
                        if (i > 0) Spacer(GlanceModifier.width(6.dp))
                        Pill(a.first, false, GlanceModifier.defaultWeight(), actionRunCallback<FieldAction>(actionParametersOf(ACTION to a.second)))
                    }
                }
                Spacer(GlanceModifier.height(6.dp))
                Row(GlanceModifier.fillMaxWidth()) {
                    Pill("←", false, GlanceModifier.defaultWeight(), actionRunCallback<FieldAction>(actionParametersOf(ACTION to "left")))
                    Spacer(GlanceModifier.width(6.dp))
                    Pill("→", false, GlanceModifier.defaultWeight(), actionRunCallback<FieldAction>(actionParametersOf(ACTION to "right")))
                    Spacer(GlanceModifier.defaultWeight()); Spacer(GlanceModifier.defaultWeight())
                }
            }
        }
    }
}

class FieldAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val act = parameters[ACTION] ?: return
        val ip = Snapshot.agentIp(context) ?: return
        val a = Agent(ip)
        val f = FieldState.from(runCatching { a.get("/api/state", 4, 3) }.getOrNull()?.optJSONObject("room")?.optJSONObject("field"))
        val step = 0.2
        val body: Map<String, Any?> = when (act) {
            "center" -> mapOf("ux" to 0, "uy" to 0)
            "classic" -> mapOf("spatial" to false)
            "spatial" -> mapOf("spatial" to true)
            "narrower" -> mapOf("width" to max(20.0, f.width - 15))
            "wider" -> mapOf("width" to min(180.0, f.width + 15))
            "up" -> mapOf("uy" to min(1.0, f.uy + step))
            "down" -> mapOf("uy" to max(-1.0, f.uy - step))
            "left" -> mapOf("ux" to max(-1.0, f.ux - step))
            "right" -> mapOf("ux" to min(1.0, f.ux + step))
            else -> return
        }
        runCatching { a.post("/api/soundfield", body, 6) }
        AuraWidget.refresh(context)              // both field widgets show the new field
    }
}

// ---- LEDs ----

class LedWidget : GlanceAppWidget() {
    override val sizeMode = SIZES
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val snap = Snapshot.fetch(context)
        provideContent { LedContent(snap) }
    }
}
class LedWidgetReceiver : GlanceAppWidgetReceiver() { override val glanceAppWidget: GlanceAppWidget = LedWidget() }

private val NAMES = mapOf("pulse" to "Pulse", "party" to "Party", "flow" to "Flow", "groove" to "Groove", "bloom" to "Bloom", "shimmer" to "Shimmer", "spectrum" to "Spectrum", "vu" to "VU", "beat" to "Beat")

@Composable
private fun LedContent(s: Snapshot) {
    val wide = LocalSize.current.width >= 200.dp
    Row(GlanceModifier.fillMaxSize().background(Color.Black).cornerRadius(20.dp).padding(12.dp).clickable(actionStartActivity<MainActivity>()), verticalAlignment = Alignment.CenterVertically) {
        Column(GlanceModifier.width(96.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("●", style = TextStyle(color = if (s.ledMode == null) MUTED else AMBER, fontSize = 34.sp))
            Text(s.room, style = TextStyle(color = WHITE, fontSize = 13.sp, fontWeight = FontWeight.Bold), maxLines = 1)
            Text(s.ledMode?.let { NAMES[it] ?: it } ?: "Lights off", style = TextStyle(color = if (s.ledMode == null) MUTED else AMBER, fontSize = 11.sp))
            if (!s.reachable) Text("not reachable", style = TextStyle(color = MUTED, fontSize = 10.sp))
        }
        if (wide) {
            Spacer(GlanceModifier.width(10.dp))
            val modes = s.ledModes.take(7) + "off"
            Column(GlanceModifier.defaultWeight()) {
                modes.chunked(4).forEachIndexed { ri, row ->
                    if (ri > 0) Spacer(GlanceModifier.height(6.dp))
                    Row(GlanceModifier.fillMaxWidth()) {
                        row.forEachIndexed { i, m ->
                            if (i > 0) Spacer(GlanceModifier.width(6.dp))
                            Pill(if (m == "off") "Off" else (NAMES[m] ?: m), (s.ledMode ?: "off") == m, GlanceModifier.defaultWeight(), actionRunCallback<LedAction>(actionParametersOf(MODE to m)))
                        }
                        repeat(4 - row.size) { Spacer(GlanceModifier.width(6.dp)); Spacer(GlanceModifier.defaultWeight()) }
                    }
                }
            }
        }
    }
}

class LedAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val mode = parameters[MODE] ?: return
        val ip = Snapshot.agentIp(context) ?: return
        runCatching { Agent(ip).post("/api/led/reactive", mapOf("mode" to mode, "scope" to "group"), 20) }
        LedWidget().update(context, glanceId)
    }
}
