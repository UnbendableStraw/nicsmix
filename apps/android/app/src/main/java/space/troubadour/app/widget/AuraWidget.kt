package space.troubadour.app.widget

import android.content.Context
import android.graphics.Bitmap
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalSize
import androidx.glance.action.actionParametersOf
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.updateAll
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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import org.json.JSONObject
import space.troubadour.app.MainActivity
import space.troubadour.app.field.FieldPalette
import space.troubadour.app.field.FieldRenderer
import space.troubadour.app.field.FieldStill
import space.troubadour.app.net.Agent
import kotlin.math.roundToInt

// The sound field visualizer on the home screen: the app's own field (aura, particles, glass puck, speaker rings)
// in whatever colour the speakers' LEDs are glowing. A widget can't host the GL view, so FieldStill draws one
// frame on the CPU from /api/state + /api/viz/frame. Wider sizes add Centre, Classic/Spatial and width buttons.

private val AW = ColorProvider(Color.White); private val AM = ColorProvider(Color(0xFF8E8E8E))

class AuraWidget : GlanceAppWidget() {
    override val sizeMode = SizeMode.Responsive(setOf(DpSize(110.dp, 110.dp), DpSize(250.dp, 110.dp), DpSize(250.dp, 250.dp)))

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val snap = Snapshot.fetch(context)
        val (bmp, spatial, width) = render(context)
        provideContent { AuraContent(snap, bmp, spatial, width) }
    }

    private suspend fun render(ctx: Context): Triple<Bitmap, Boolean, Int> = withContext(Dispatchers.Default) {
        var state: JSONObject? = null; var frame: JSONObject? = null
        Snapshot.agentIp(ctx)?.let { ip ->
            val a = Agent(ip)
            coroutineScope {
                val st = async { runCatching { a.get("/api/state", 4, 3) }.getOrNull() }
                val fr = async { runCatching { a.get("/api/viz/frame", 4, 3) }.getOrNull() }
                state = st.await(); frame = fr.await()
            }
        }
        val dens = ctx.resources.displayMetrics.density.toDouble()
        // one square for every size: big enough for the large layout, small enough for RemoteViews' bitmap budget
        val side = (230 * dens).roundToInt().coerceIn(240, 640)
        val m = FieldStill.model(state, frame, side, side, dens)
        val img = FieldStill.render(m, glyph(FieldPalette.glyphFor(m.source)))
        Triple(Bitmap.createBitmap(img.argb, img.width, img.height, Bitmap.Config.ARGB_8888), m.spatial,
               Math.round(m.halfT * 180 / Math.PI).toInt())
    }

    private fun glyph(kind: String): FieldStill.Glyph {
        val b = FieldRenderer.glyphBitmap(kind, 96)
        val px = IntArray(96 * 96); b.getPixels(px, 0, 96, 0, 0, 96, 96); b.recycle()
        return FieldStill.Glyph(96, FloatArray(px.size) { ((px[it] ushr 24) and 0xFF) / 255f })
    }

    companion object {
        /** Redraw every visualizer (and room-plan) widget — after a field change in the app or a widget button. */
        suspend fun refresh(ctx: Context) { runCatching { AuraWidget().updateAll(ctx) }; runCatching { FieldWidget().updateAll(ctx) } }
    }
}
class AuraWidgetReceiver : GlanceAppWidgetReceiver() { override val glanceAppWidget: GlanceAppWidget = AuraWidget() }

@Composable
private fun AuraContent(s: Snapshot, bmp: Bitmap, spatial: Boolean, width: Int) {
    val size = LocalSize.current
    val mode = if (spatial) "Spatial · ${width}°" else "Classic"
    val root = GlanceModifier.fillMaxSize().background(Color.Black).cornerRadius(20.dp).clickable(actionStartActivity<MainActivity>())
    @Composable fun picture(mod: GlanceModifier) = Box(mod, contentAlignment = Alignment.Center) {
        Image(ImageProvider(bmp), "Sound field", GlanceModifier.fillMaxSize())
        if (!s.reachable && Snapshot.agentIp(androidx.glance.LocalContext.current) != null)
            Text("not reachable", style = TextStyle(color = AM, fontSize = 10.sp))
    }
    @Composable fun buttons() {
        Row(GlanceModifier.fillMaxWidth()) {
            AuraPill("Centre", true, GlanceModifier.defaultWeight(), "center")
            Spacer(GlanceModifier.width(6.dp))
            AuraPill(if (spatial) "Classic" else "Spatial", false, GlanceModifier.defaultWeight(), if (spatial) "classic" else "spatial")
            if (spatial) {
                Spacer(GlanceModifier.width(6.dp)); AuraPill("−", false, GlanceModifier.width(40.dp), "narrower")
                Spacer(GlanceModifier.width(6.dp)); AuraPill("+", false, GlanceModifier.width(40.dp), "wider")
            }
        }
    }
    when {
        size.width >= 200.dp && size.height >= 220.dp -> Column(root.padding(8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            picture(GlanceModifier.defaultWeight().fillMaxWidth())
            Text(s.room, style = TextStyle(color = AW, fontSize = 13.sp, fontWeight = FontWeight.Bold), maxLines = 1)
            Text(s.playing?.let { "$it · $mode" } ?: mode, style = TextStyle(color = AM, fontSize = 10.sp), maxLines = 1)
            Spacer(GlanceModifier.height(6.dp)); buttons()
        }
        size.width >= 200.dp -> Row(root.padding(6.dp), verticalAlignment = Alignment.CenterVertically) {
            picture(GlanceModifier.size(size.height - 12.dp))
            Spacer(GlanceModifier.width(8.dp))
            Column(GlanceModifier.defaultWeight().padding(end = 6.dp)) {
                Text(s.room, style = TextStyle(color = AW, fontSize = 13.sp, fontWeight = FontWeight.Bold), maxLines = 1)
                s.playing?.let { Text(it, style = TextStyle(color = AM, fontSize = 10.sp), maxLines = 1) }
                Text(mode, style = TextStyle(color = AM, fontSize = 10.sp))
                Spacer(GlanceModifier.height(6.dp)); buttons()
            }
        }
        else -> picture(root)
    }
}

@Composable
private fun AuraPill(text: String, filled: Boolean, modifier: GlanceModifier, action: String) {
    androidx.glance.Button(text = text, onClick = actionRunCallback<FieldAction>(actionParametersOf(ACTION to action)),
        modifier = modifier.height(30.dp), maxLines = 1,
        style = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.Medium, color = ColorProvider(if (filled) Color.Black else Color.White)),
        colors = androidx.glance.ButtonDefaults.buttonColors(backgroundColor = ColorProvider(if (filled) Color.White else Color(0xFF2A2A2A)),
                                                             contentColor = ColorProvider(if (filled) Color.Black else Color.White)))
}
