package space.troubadour.app.ui

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.Canvas
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import space.troubadour.app.net.Agent
import space.troubadour.app.net.Fleet
import space.troubadour.app.net.Room
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

// ---------------------------------------------------------------- one endpoint, polled

/** Loads one `GET /api/…`, refreshes it on a timer and posts back — the whole of each setup screen. */
class Ep(val agent: Agent?, val path: String, val every: Long = 6000) {
    var data by mutableStateOf(JSONObject())
    var error by mutableStateOf<String?>(null)
    var busy by mutableStateOf(false)
    var note by mutableStateOf<String?>(null)

    suspend fun load() {
        val a = agent ?: return
        try { data = a.get(path, 8); error = null } catch (e: Exception) { error = e.message }
    }
    fun fire(scope: CoroutineScope, p: String, body: Map<String, Any?> = emptyMap(), say: String? = null) {
        scope.launch {
            busy = true
            try { agent?.post(p, body, 25); error = null; if (say != null) note = say }
            catch (e: Exception) { error = e.message }
            busy = false
            load()
            if (say != null) { delay(2500); if (note == say) note = null }
        }
    }
}

@Composable
fun rememberEp(agent: Agent?, path: String, every: Long = 6000): Ep {
    val ep = remember(path) { Ep(agent, path, every) }
    LaunchedEffect(path) {
        while (true) { ep.load(); if (every <= 0) break; delay(every) }
    }
    return ep
}

private fun JSONObject.o(k: String): JSONObject = optJSONObject(k) ?: JSONObject()
private fun JSONObject.list(k: String): List<JSONObject> {
    val a = optJSONArray(k) ?: return emptyList()
    return (0 until a.length()).mapNotNull { a.optJSONObject(it) }
}
private fun JSONObject.strings(k: String): List<String> {
    val a = optJSONArray(k) ?: return emptyList()
    return (0 until a.length()).map { a.optString(it) }
}

@Composable
private fun Page(title: String, ep: Ep, onBack: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    BackHandler { onBack() }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, null, tint = Color.White) }
            Text(title, fontWeight = FontWeight.Bold, fontSize = 18.sp)
            Spacer(Modifier.weight(1f))
            if (ep.busy) CircularProgressIndicator(Modifier.size(18.dp), color = Color.White, strokeWidth = 2.dp)
        }
        content()
        ep.note?.let { Text(it, color = Theme.live, fontSize = 12.sp) }
        ep.error?.let { Text(it, color = Theme.bad, fontSize = 12.sp) }
        Spacer(Modifier.height(8.dp))
    }
}

// ---------------------------------------------------------------- the index

@Composable
fun SetupScreen(room: Room) {
    var screen by remember { mutableStateOf<String?>(null) }
    when (screen) {
        null -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            SetupRow("Room", "Name, members, who is primary", Icons.Default.GroupWork) { screen = "room" }
            SetupRow("Layout & Soundcheck", "Where the speakers are, and measuring it", Icons.Default.GridGoldenratio) { screen = "layout" }
            SetupRow("LEDs", "Reactive modes, colour, the whole room or one speaker", Icons.Default.Lightbulb) { screen = "led" }
            SetupRow("Sound & controls", "Tone, the volume ceiling, the control ring", Icons.Default.Tune) { screen = "sound" }
            SetupRow("Scenes", "Save the room, apply it, schedule it", Icons.Default.Theaters) { screen = "scenes" }
            SetupRow("Network", "Wi-Fi, radios, cell-to-cell link", Icons.Default.Wifi) { screen = "net" }
            SetupRow("Firmware", "What is flashed, install, roll back", Icons.Default.SystemUpdate) { screen = "fw" }
            SetupRow("System", "Live fleet, stats, sleep, housekeeping", Icons.Default.Speed) { screen = "sys" }
            SetupRow("The speaker's own page", "Everything, served by the cell", Icons.Default.Language) { screen = "web" }
            Text(room.agent?.let { "Talking to ${it.ip} · port 8080" } ?: "No agent", color = Theme.muted, fontSize = 11.sp)
        }
        "room" -> RoomSetup(room) { screen = null }
        "layout" -> LayoutSetup(room) { screen = null }
        "led" -> LedSetup(room) { screen = null }
        "sound" -> SoundSetup(room) { screen = null }
        "scenes" -> ScenesSetup(room) { screen = null }
        "net" -> NetworkSetup(room) { screen = null }
        "fw" -> FirmwareSetup(room) { screen = null }
        "sys" -> SystemSetup(room) { screen = null }
        else -> {
            BackHandler { screen = null }
            Column(Modifier.fillMaxSize()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = { screen = null }) { Icon(Icons.Default.ArrowBack, null, tint = Color.White) }
                    Text("Setup page", fontWeight = FontWeight.Bold)
                }
                AgentPage(room.agent, "/setup")
            }
        }
    }
}

@Composable
private fun SetupRow(title: String, subtitle: String, icon: androidx.compose.ui.graphics.vector.ImageVector, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(Theme.surface).clickable { onClick() }.padding(14.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        Icon(icon, null, tint = Color.White)
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
            Text(subtitle, color = Theme.muted, fontSize = 12.sp)
        }
        Icon(Icons.Default.ChevronRight, null, tint = Theme.muted)
    }
}

// ---------------------------------------------------------------- room

@Composable
fun RoomSetup(room: Room, onBack: () -> Unit) {
    val ep = rememberEp(room.agent, "/api/room", 8000)
    val scope = rememberCoroutineScope()
    var renaming by remember { mutableStateOf(false) }
    var newName by remember { mutableStateOf("") }
    val cells = ep.data.list("cells")
    val roomName = ep.data.o("room_state").optString("room_name").ifEmpty { room.name }

    Page("Room", ep, onBack) {
        Card("This room") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(roomName, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                Spacer(Modifier.weight(1f))
                Pill("Rename") { newName = roomName; renaming = true }
            }
            Text(if (ep.data.optBoolean("i_am_primary")) "This speaker is the primary — it owns the room document and streams to the others."
                 else "A secondary. Membership changes are relayed to the primary.", color = Theme.muted, fontSize = 12.sp)
        }
        Card("Speakers in the room") {
            cells.forEach { c ->
                val serial = c.optString("serial")
                val isPri = c.optString("role").lowercase() == "primary"
                Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Puck(34, if (isPri) Color.White else null)
                    Column(Modifier.weight(1f)) {
                        Text(c.optString("name").ifEmpty { serial.takeLast(5) }, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                        Text(listOf(serial.takeLast(5), c.optString("ip"), if (isPri) "primary" else "secondary",
                            if (c.optBoolean("calibrated", true)) "" else "uncalibrated").filter { it.isNotEmpty() }.joinToString(" · "),
                            color = Theme.muted, fontSize = 12.sp)
                    }
                    if (!isPri) Pill("Primary") { ep.fire(scope, "/api/room/pin", mapOf("serial" to serial, "role" to "primary"), "Primary pinned") }
                    Pill("Find") { ep.fire(scope, "/api/ping", mapOf("serial" to serial)) }
                }
            }
        }
        val mine = cells.map { it.optString("serial") }.toSet()
        val others = Fleet.rooms.value.flatMap { it.cells }.filter { it.serial !in mine }
        if (others.isNotEmpty()) Card("Nearby, in another room") {
            others.forEach { c ->
                Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Puck(32)
                    Column(Modifier.weight(1f)) {
                        Text(c.title, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                        Text(c.ip, color = Theme.muted, fontSize = 12.sp)
                    }
                    Pill("Add", filled = true) {
                        scope.launch {
                            ep.busy = true
                            runCatching { Agent(c.ip).post("/api/room/join", mapOf("ip" to (room.agent?.ip ?: "")), 30) }
                                .onSuccess { ep.note = "${c.title} joined" }.onFailure { ep.error = it.message }
                            ep.busy = false; ep.load(); Fleet.refresh()
                        }
                    }
                }
            }
            Text("Adding asks that speaker to join this room; it keeps playing once the primary admits it.", color = Theme.muted, fontSize = 12.sp)
        }
        Card("Careful") {
            Pill("Leave this room") { ep.fire(scope, "/api/room/leave", emptyMap(), "Left the room") }
            Text("This speaker goes back to a room of its own. The others stay together.", color = Theme.muted, fontSize = 12.sp)
        }
    }
    if (renaming) TextPrompt("Room name", newName, { newName = it }, { renaming = false }) {
        ep.fire(scope, "/api/room/rename", mapOf("name" to newName), "Renamed"); renaming = false
    }
}

@Composable
private fun TextPrompt(title: String, value: String, onChange: (String) -> Unit, onCancel: () -> Unit, onSave: () -> Unit) {
    androidx.compose.ui.window.Dialog(onDismissRequest = onCancel) {
        Column(Modifier.clip(RoundedCornerShape(20.dp)).background(Theme.surface).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(title, fontWeight = FontWeight.Bold)
            OutlinedTextField(value, onChange, singleLine = true)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Cancel", modifier = Modifier.weight(1f)) { onCancel() }
                Pill("Save", filled = true, modifier = Modifier.weight(1f)) { onSave() }
            }
        }
    }
}

// ---------------------------------------------------------------- layout & soundcheck

@Composable
fun LayoutSetup(room: Room, onBack: () -> Unit) {
    val ep = rememberEp(room.agent, "/api/room", 10000)
    val cal = rememberEp(room.agent, "/api/calibration/status", 3000)
    val scope = rememberCoroutineScope()
    var pos by remember { mutableStateOf(mapOf<String, Pair<Double, Double>>()) }
    var rot by remember { mutableStateOf(mapOf<String, Double>()) }
    var circle by remember { mutableStateOf(listOf(0.0, 0.0, 1.15, 0.0)) }
    var listener by remember { mutableStateOf<Pair<Double, Double>?>(null) }
    var selected by remember { mutableStateOf<String?>(null) }
    var dirty by remember { mutableStateOf(false) }
    val cells = ep.data.list("cells")

    LaunchedEffect(ep.data) {
        if (dirty || cells.isEmpty()) return@LaunchedEffect
        val np = HashMap<String, Pair<Double, Double>>()
        val nr = HashMap<String, Double>()
        cells.forEach { c ->
            val s = c.optString("serial")
            val p = c.o("position")
            np[s] = p.optDouble("x", 0.0) to p.optDouble("y", 0.0)
            nr[s] = p.optDouble("rotation", 0.0)
        }
        pos = np; rot = nr
        val rs = ep.data.o("room_state")
        val rc = rs.o("reference_circle")
        circle = listOf(rc.optDouble("x_m", 0.0), rc.optDouble("y_m", 0.0), max(rc.optDouble("radius_m", 1.15), 0.2), rc.optDouble("rotation_deg", 0.0))
        val lp = rs.o("listener_position")
        listener = if (lp.has("x_m")) lp.optDouble("x_m") to lp.optDouble("y_m") else null
    }

    Page("Layout", ep, onBack) {
        Card(null) {
            var box by remember { mutableStateOf(Offset(1f, 1f)) }
            val scale = remember(pos, circle, box) {
                var span = circle[2] + 0.6
                pos.values.forEach { span = max(span, hypot(it.first - circle[0], it.second - circle[1]) + 0.5) }
                min(box.x, box.y) / (2 * max(span, 0.8)).toFloat()
            }
            Box(Modifier.fillMaxWidth().height(320.dp)
                .onSizeChanged { box = Offset(it.width.toFloat(), it.height.toFloat()) }
                .pointerInput(cells.size, scale) {
                detectDragGestures(
                    onDragStart = { off ->
                        selected = cells.map { it.optString("serial") }.minByOrNull { s ->
                            val p = pos[s] ?: return@minByOrNull Float.MAX_VALUE.toDouble()
                            val sx = box.x / 2 + (p.first - circle[0]) * scale
                            val sy = box.y / 2 - (p.second - circle[1]) * scale
                            hypot(off.x - sx, off.y - sy)
                        }
                    },
                    onDrag = { _, drag ->
                        val s = selected ?: return@detectDragGestures
                        val p = pos[s] ?: return@detectDragGestures
                        pos = pos + (s to (p.first + drag.x / scale to p.second - drag.y / scale))
                        dirty = true
                    }
                )
            }) {
                Canvas(Modifier.fillMaxSize()) {
                    val cx = size.width / 2
                    val cy = size.height / 2
                    for (r in 1..6) {
                        val rr = r * scale
                        if (rr > max(size.width, size.height)) break
                        drawCircle(Color.White.copy(alpha = .05f), rr, Offset(cx, cy), style = Stroke(1f))
                    }
                    drawCircle(Color.White.copy(alpha = .22f), (circle[2] * scale).toFloat(), Offset(cx, cy), style = Stroke(2f))
                    listener?.let {
                        val x = cx + ((it.first - circle[0]) * scale).toFloat()
                        val y = cy - ((it.second - circle[1]) * scale).toFloat()
                        drawCircle(Color.White.copy(alpha = .8f), 5f, Offset(x, y))
                    }
                    pos.forEach { (s, p) ->
                        val x = cx + ((p.first - circle[0]) * scale).toFloat()
                        val y = cy - ((p.second - circle[1]) * scale).toFloat()
                        val sel = s == selected
                        drawCircle(Color(0xFF101010), 17f, Offset(x, y))
                        drawCircle(if (sel) Theme.live else Color.White.copy(alpha = .5f), 17f, Offset(x, y), style = Stroke(if (sel) 2.5f else 1.5f))
                        val a = (rot[s] ?: 0.0) * Math.PI / 180
                        drawCircle(Color.White, 3f, Offset(x + (sin(a) * 21).toFloat(), y - (cos(a) * 21).toFloat()))
                    }
                }
            }
            Text(if (dirty) "Moved — save to send it to the speakers." else "Drag a speaker to move it. Tap one, then turn it below.",
                color = if (dirty) Theme.amber else Theme.muted, fontSize = 12.sp)
        }
        selected?.let { s ->
            Card(cells.firstOrNull { it.optString("serial") == s }?.optString("name")?.ifEmpty { s.takeLast(5) } ?: s.takeLast(5)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Facing", color = Theme.muted, fontSize = 14.sp)
                    Slider(value = (rot[s] ?: 0.0).toFloat(), onValueChange = { rot = rot + (s to it.toDouble()); dirty = true },
                        valueRange = 0f..360f, modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
                        colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Theme.surface2))
                    Text("${(rot[s] ?: 0.0).roundToInt()}°", fontSize = 12.sp)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Pill("Find") { ep.fire(scope, "/api/ping", mapOf("serial" to s)) }
                    Pill("Done") { selected = null }
                }
            }
        }
        Card("The room") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Save layout", filled = dirty, enabled = dirty, modifier = Modifier.weight(1f)) {
                    val body = mapOf(
                        "cells" to JSONObject(pos.mapValues { (s, p) ->
                            JSONObject(mapOf("x" to p.first, "y" to p.second, "rotation" to (rot[s] ?: 0.0)))
                        }),
                        "listener_position" to listener?.let { JSONObject(mapOf("x_m" to it.first, "y_m" to it.second)) })
                    dirty = false
                    ep.fire(scope, "/api/room/layout", body, "Layout saved")
                }
                Pill("Revert", enabled = dirty, modifier = Modifier.weight(1f)) { dirty = false; scope.launch { ep.load() } }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Fit a circle", modifier = Modifier.weight(1f)) { ep.fire(scope, "/api/room/layout", mapOf("auto_circle" to true, "auto_listener" to true), "Circle fitted") }
                Pill("Hear it", modifier = Modifier.weight(1f)) {
                    ep.fire(scope, "/api/room/preview", mapOf("reference_circle" to JSONObject(mapOf(
                        "x_m" to circle[0], "y_m" to circle[1], "radius_m" to circle[2], "rotation_deg" to circle[3]))))
                }
            }
            Text(String.format("circle (%.2f, %.2f) r %.2f m · rotation %.0f°", circle[0], circle[1], circle[2], circle[3]),
                color = Theme.muted, fontSize = 12.sp)
        }
        Card("Soundcheck") {
            val running = cal.data.optBoolean("running")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Measure ×3", filled = true, enabled = !running, modifier = Modifier.weight(1f)) { cal.fire(scope, "/api/calibration/run", mapOf("runs" to 3), "Measuring…") }
                Pill("Once", enabled = !running, modifier = Modifier.weight(1f)) { cal.fire(scope, "/api/calibration/run", mapOf("runs" to 1), "Measuring…") }
            }
            if (running) {
                val r = cal.data.optInt("run", 1); val n = cal.data.optInt("runs", 1)
                Text(if (n > 1) "Measuring, pass $r of $n…" else "Measuring…", color = Theme.amber, fontSize = 12.sp)
            }
            cal.data.optJSONObject("combine")?.let { c ->
                Text(String.format("residual %.3f · reference %s · %d runs", c.optDouble("residual", 0.0), c.optString("reference"), c.optInt("runs", 1)), fontSize = 12.sp)
                c.list("pairs").take(4).forEach { p ->
                    Text(String.format("%s–%s  %.2f m  ±%.2f  rec %.2f", p.optString("a"), p.optString("b"),
                        p.optDouble("distance_m", 0.0), p.optDouble("spread_m", 0.0), p.optDouble("reciprocity_m", 0.0)),
                        color = Theme.muted, fontSize = 11.sp)
                }
            }
            val log = cal.data.strings("log")
            if (log.isNotEmpty()) Text(log.takeLast(6).joinToString("\n"), color = Theme.muted, fontSize = 11.sp)
            Text("Each speaker chirps in turn and they listen to each other; three passes are combined on the median. Playback pauses and comes back on its own.",
                color = Theme.muted, fontSize = 12.sp)
        }
    }
}

// ---------------------------------------------------------------- LEDs

@Composable
fun LedSetup(room: Room, onBack: () -> Unit) {
    val ep = rememberEp(room.agent, "/api/led", 6000)
    val scope = rememberCoroutineScope()
    var wholeRoom by remember { mutableStateOf(true) }
    fun scope2(extra: Map<String, Any?>) = if (wholeRoom) extra + ("scope" to "group") else extra
    val modes = ep.data.strings("reactiveModes")
    val mode = if (ep.data.optBoolean("reactive")) ep.data.optString("mode") else null

    Page("LEDs", ep, onBack) {
        Card("Applies to") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Whole room", filled = wholeRoom, modifier = Modifier.weight(1f)) { wholeRoom = true }
                Pill("This speaker", filled = !wholeRoom, modifier = Modifier.weight(1f)) { wholeRoom = false }
            }
        }
        Card("Reactive") {
            modes.chunked(2).forEach { pair ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    pair.forEach { mName ->
                        Pill(mName.replaceFirstChar { it.uppercase() }, filled = mode == mName, modifier = Modifier.weight(1f)) {
                            ep.fire(scope, "/api/led/reactive", scope2(mapOf("mode" to mName)))
                        }
                    }
                    if (pair.size == 1) Spacer(Modifier.weight(1f))
                }
            }
            Pill("Off", filled = mode == null) { ep.fire(scope, "/api/led/reactive", scope2(mapOf("mode" to "off"))) }
            Text("The LEDs follow what is playing — and since b52 the sound field follows the LEDs, so the room and the screen glow together.",
                color = Theme.muted, fontSize = 12.sp)
        }
        Card("Palette") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Per speaker", filled = ep.data.optString("palette", "free") == "free", modifier = Modifier.weight(1f)) {
                    ep.fire(scope, "/api/led/palette", scope2(mapOf("palette" to "free")))
                }
                Pill("Shared", filled = ep.data.optString("palette") == "shared", modifier = Modifier.weight(1f)) {
                    ep.fire(scope, "/api/led/palette", scope2(mapOf("palette" to "shared")))
                }
            }
            Text("Shared puts the whole room on one colour.", color = Theme.muted, fontSize = 12.sp)
        }
        Card("Colour") {
            var r by remember { mutableFloatStateOf(0.2f) }
            var g by remember { mutableFloatStateOf(0.7f) }
            var b by remember { mutableFloatStateOf(1f) }
            var w by remember { mutableFloatStateOf(0f) }
            Box(Modifier.fillMaxWidth().height(28.dp).clip(RoundedCornerShape(8.dp)).background(Color(r, g, b)))
            ChannelRow("R", r) { r = it }
            ChannelRow("G", g) { g = it }
            ChannelRow("B", b) { b = it }
            ChannelRow("W", w) { w = it }
            Pill("Set this colour", filled = true) {
                ep.fire(scope, "/api/led/color", scope2(mapOf("r" to r.toDouble(), "g" to g.toDouble(), "b" to b.toDouble(), "w" to w.toDouble())), "Colour set")
            }
        }
    }
}

// ---------------------------------------------------------------- tone, ceiling, ring

@Composable
fun SoundSetup(room: Room, onBack: () -> Unit) {
    val ep = rememberEp(room.agent, "/api/state", 8000)
    val settings = rememberEp(room.agent, "/api/settings", 0)
    val scope = rememberCoroutineScope()
    var bass by remember { mutableFloatStateOf(0f) }
    var tilt by remember { mutableFloatStateOf(0f) }
    var loaded by remember { mutableStateOf(false) }
    var limit by remember { mutableFloatStateOf(0f) }
    var steps by remember { mutableFloatStateOf(100f) }
    var clockwise by remember { mutableStateOf(true) }
    var uiSound by remember { mutableFloatStateOf(25f) }

    LaunchedEffect(ep.data) {
        if (loaded) return@LaunchedEffect
        val tone = ep.data.o("room").o("tone")
        if (tone.has("bass")) { bass = tone.optDouble("bass", 0.0).toFloat(); tilt = tone.optDouble("tilt", 0.0).toFloat(); loaded = true }
    }
    LaunchedEffect(settings.data) {
        limit = settings.data.optInt("volume_limit", 0).toFloat()
        val ring = settings.data.o("ring")
        steps = ring.optInt("steps", 100).toFloat()
        clockwise = ring.optBoolean("clockwise_up", true)
        uiSound = ring.optInt("ui_sound_pct", 25).toFloat()
    }

    Page("Sound & controls", ep, onBack) {
        Card("Tone") {
            ToneRow("Lows", bass, { bass = it }) { ep.fire(scope, "/api/tone", mapOf("bass" to bass.toDouble(), "tilt" to tilt.toDouble())) }
            ToneRow("Highs", tilt, { tilt = it }) { ep.fire(scope, "/api/tone", mapOf("bass" to bass.toDouble(), "tilt" to tilt.toDouble())) }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill("Flat") { bass = 0f; tilt = 0f; ep.fire(scope, "/api/tone", mapOf("bass" to 0.0, "tilt" to 0.0)) }
                Spacer(Modifier.weight(1f))
                Text(String.format("%+.0f / %+.0f", bass * 100, tilt * 100), color = Theme.muted, fontSize = 12.sp)
            }
        }
        Card("Volume ceiling") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Slider(value = limit, onValueChange = { limit = it }, valueRange = 0f..100f, steps = 19, modifier = Modifier.weight(1f),
                    onValueChangeFinished = { settings.fire(scope, "/api/settings", mapOf("volume_limit" to limit.toInt()), if (limit <= 0) "Ceiling off" else "Ceiling ${limit.toInt()}%") },
                    colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Theme.surface2))
                Text(if (limit <= 0) "off" else "${limit.toInt()}%", fontSize = 12.sp, modifier = Modifier.width(44.dp))
            }
            Text("The agent refuses anything above this, wherever the request comes from.", color = Theme.muted, fontSize = 12.sp)
        }
        Card("Control ring") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Steps per turn", color = Theme.muted, fontSize = 14.sp)
                Spacer(Modifier.weight(1f))
                Text("${steps.toInt()}", fontSize = 12.sp)
            }
            Slider(value = steps, onValueChange = { steps = it }, valueRange = 10f..400f,
                onValueChangeFinished = { settings.fire(scope, "/api/ring", mapOf("steps" to steps.toInt(), "clockwise_up" to clockwise, "ui_sound_pct" to uiSound.toInt()), "Ring updated") },
                colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Theme.surface2))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Clockwise turns it up", fontSize = 14.sp, modifier = Modifier.weight(1f))
                Switch(checked = clockwise, onCheckedChange = {
                    clockwise = it
                    settings.fire(scope, "/api/ring", mapOf("steps" to steps.toInt(), "clockwise_up" to it, "ui_sound_pct" to uiSound.toInt()), "Ring updated")
                })
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Touch sounds", color = Theme.muted, fontSize = 14.sp)
                Slider(value = uiSound, onValueChange = { uiSound = it }, valueRange = 0f..100f, modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
                    onValueChangeFinished = { settings.fire(scope, "/api/ring", mapOf("steps" to steps.toInt(), "clockwise_up" to clockwise, "ui_sound_pct" to uiSound.toInt()), "Ring updated") },
                    colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Theme.surface2))
                Text("${uiSound.toInt()}%", fontSize = 12.sp, modifier = Modifier.width(40.dp))
            }
        }
    }
}

// ---------------------------------------------------------------- scenes

@Composable
fun ScenesSetup(room: Room, onBack: () -> Unit) {
    val ep = rememberEp(room.agent, "/api/scenes", 10000)
    val scope = rememberCoroutineScope()
    var saving by remember { mutableStateOf(false) }
    var newName by remember { mutableStateOf("") }
    val scenes = ep.data.o("scenes")
    val names = scenes.keys().asSequence().sorted().toList()
    val dayNames = listOf("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

    LaunchedEffect(Unit) {
        val off = -java.util.TimeZone.getDefault().getOffset(System.currentTimeMillis()) / 60000
        runCatching { room.agent?.post("/api/scenes/tz", mapOf("offset" to off)) }
    }

    Page("Scenes", ep, onBack) {
        Card("Saved") {
            if (names.isEmpty()) Text("No scenes yet. Set the room up the way you like it, then save it below.", color = Theme.muted, fontSize = 12.sp)
            names.forEach { n ->
                val sc = scenes.o(n)
                val at = sc.optString("at").takeIf { it.isNotEmpty() && !sc.isNull("at") }
                val days = sc.optJSONArray("days")?.let { a -> (0 until a.length()).map { a.optInt(it) } } ?: emptyList()
                Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Column(Modifier.weight(1f)) {
                        Text(n, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                        Text(when {
                            at == null -> "not scheduled"
                            days.isEmpty() || days.size == 7 -> "every day at $at"
                            else -> days.sorted().joinToString(" ") { dayNames[it % 7] } + " at $at"
                        }, color = Theme.muted, fontSize = 12.sp)
                    }
                    Pill("Apply", filled = ep.data.optString("last") == n) { ep.fire(scope, "/api/scenes/apply", mapOf("name" to n), "Applied $n") }
                    Pill("Delete") { ep.fire(scope, "/api/scenes/delete", mapOf("name" to n), "Deleted $n") }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    dayNames.forEachIndexed { i, d ->
                        val on = days.contains(i)
                        Pill(d, filled = on, modifier = Modifier.weight(1f)) {
                            val next = if (on) days - i else days + i
                            ep.fire(scope, "/api/scenes/schedule", mapOf("name" to n, "at" to (at ?: "20:00"), "days" to org.json.JSONArray(next)), "Schedule set")
                        }
                    }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("07:00", "18:00", "20:00", "22:00").forEach { t ->
                        Pill(t, filled = at == t, modifier = Modifier.weight(1f)) {
                            ep.fire(scope, "/api/scenes/schedule", mapOf("name" to n, "at" to t, "days" to org.json.JSONArray(days)), "$n at $t")
                        }
                    }
                    Pill("Off", modifier = Modifier.weight(1f)) { ep.fire(scope, "/api/scenes/schedule", mapOf("name" to n), "Schedule cleared") }
                }
            }
        }
        Card("Save the room as it is") {
            Pill("Save a scene…", filled = true) { newName = ""; saving = true }
            Text("Takes what is playing, the volume, sound mode, the LEDs and the sound field as they are now.", color = Theme.muted, fontSize = 12.sp)
        }
        ep.data.optString("tz_now").takeIf { it.isNotEmpty() }?.let { Text("The speakers think it is $it.", color = Theme.muted, fontSize = 11.sp) }
    }
    if (saving) TextPrompt("Name this scene", newName, { newName = it }, { saving = false }) {
        if (newName.isNotEmpty()) ep.fire(scope, "/api/scenes/save", mapOf("name" to newName), "Saved $newName")
        saving = false
    }
}

// ---------------------------------------------------------------- network

@Composable
fun NetworkSetup(room: Room, onBack: () -> Unit) {
    val ep = rememberEp(room.agent, "/api/net", 10000)
    val wifi = rememberEp(room.agent, "/api/wifi", 10000)
    val link = rememberEp(room.agent, "/api/link", 6000)
    val scope = rememberCoroutineScope()
    var joining by remember { mutableStateOf<String?>(null) }
    var psk by remember { mutableStateOf("") }

    Page("Network", ep, onBack) {
        Card("Wi-Fi") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(wifi.data.optString("ssid").ifEmpty { "Not connected" }, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                Spacer(Modifier.weight(1f))
                Text(wifi.data.optString("ip"), color = Theme.muted, fontSize = 12.sp)
            }
            wifi.data.list("services").take(10).forEach { s ->
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(if (s.optBoolean("connected")) Icons.Default.Wifi else Icons.Default.WifiFind, null,
                        tint = if (s.optBoolean("connected")) Color.White else Theme.muted)
                    Text(s.optString("name").ifEmpty { "(hidden)" }, fontSize = 14.sp, maxLines = 1, modifier = Modifier.weight(1f))
                    if (s.optBoolean("connected")) Pill("Forget") { wifi.fire(scope, "/api/wifi/forget", mapOf("ssid" to s.optString("name")), "Forgotten") }
                    else Pill("Join") { joining = s.optString("name"); psk = "" }
                }
            }
            Pill("Scan again") { scope.launch { runCatching { room.agent?.get("/api/wifi/scan", 25) }; wifi.load() } }
        }
        Card("Radios and addresses") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Wi-Fi off", modifier = Modifier.weight(1f)) { ep.fire(scope, "/api/net/power", mapOf("tech" to "wifi", "on" to false), "Wi-Fi off") }
                Pill("Wi-Fi on", modifier = Modifier.weight(1f)) { ep.fire(scope, "/api/net/power", mapOf("tech" to "wifi", "on" to true), "Wi-Fi on") }
                Pill("Eth on", modifier = Modifier.weight(1f)) { ep.fire(scope, "/api/net/power", mapOf("tech" to "ethernet", "on" to true), "Ethernet on") }
            }
            ep.data.list("interfaces").forEach { n ->
                Row(Modifier.fillMaxWidth()) {
                    Text(n.optString("ifname"), fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
                    Spacer(Modifier.weight(1f))
                    Text(n.optString("ip").ifEmpty { "—" }, color = Theme.muted, fontSize = 12.sp)
                }
            }
        }
        Card("Cell-to-cell link") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("SyFi", filled = link.data.optString("mode", "syfi") == "syfi", modifier = Modifier.weight(1f)) { link.fire(scope, "/api/link", mapOf("mode" to "syfi"), "Switching to SyFi…") }
                Pill("Wired", filled = link.data.optString("mode") == "wired", modifier = Modifier.weight(1f)) { link.fire(scope, "/api/link", mapOf("mode" to "wired"), "Switching to wired…") }
            }
            if (link.data.optBoolean("busy")) Text("Working…", color = Theme.amber, fontSize = 12.sp)
            link.data.list("members").forEach { mm ->
                val ptp = mm.o("ptp")
                val state = ptp.optString("state").ifEmpty { if (ptp.optBoolean("running")) "running" else "—" }
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Box(Modifier.size(7.dp).clip(RoundedCornerShape(4.dp)).background(if (mm.optBoolean("time_sync_good")) Theme.live else Theme.amber))
                    Text(mm.optString("name").ifEmpty { mm.optString("serial").takeLast(5) }, fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
                    Spacer(Modifier.weight(1f))
                    Text(mm.optString("wired_ip").ifEmpty { "no wire" }, color = Theme.muted, fontSize = 11.sp)
                    Text(if (ptp.has("offset_ns")) String.format("%s %.0fµs", state, ptp.optDouble("offset_ns") / 1000) else state, color = Theme.muted, fontSize = 11.sp)
                }
            }
            Text("Wired moves the audio and the shared clock onto Ethernet (ntp time-sync + PTP). Every speaker needs a wired address first.",
                color = Theme.muted, fontSize = 12.sp)
            link.error?.let { Text(it, color = Theme.bad, fontSize = 12.sp) }
        }
    }
    joining?.let { ssid ->
        TextPrompt("Password for $ssid", psk, { psk = it }, { joining = null }) {
            wifi.fire(scope, "/api/wifi/connect", mapOf("ssid" to ssid, "psk" to psk), "Joining $ssid…")
            joining = null
        }
    }
}

// ---------------------------------------------------------------- firmware

@Composable
fun FirmwareSetup(room: Room, onBack: () -> Unit) {
    val ep = rememberEp(room.agent, "/api/firmware", 4000)
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            ep.busy = true
            runCatching {
                val size = ctx.contentResolver.openFileDescriptor(uri, "r")?.use { it.statSize } ?: 0L
                val name = uri.lastPathSegment?.substringAfterLast('/') ?: "firmware.img.gz"
                room.agent?.upload("/api/firmware/upload", name, size) { ctx.contentResolver.openInputStream(uri)!! }
            }.onSuccess { ep.note = "Uploaded" }.onFailure { ep.error = it.message }
            ep.busy = false; ep.load()
        }
    }
    val running = ep.data.o("running")
    val image = ep.data.optJSONObject("image")

    Page("Firmware", ep, onBack) {
        Card("Running now") {
            InfoRow("Artifact", running.optString("artifact").ifEmpty { "—" })
            InfoRow("Version", running.optString("version").ifEmpty { "—" })
            InfoRow("Partition", running.optInt("part", 0).toString())
            InfoRow("Agent", "b" + running.optInt("agent", 0))
            ep.data.optJSONObject("other")?.let { InfoRow("Other slot", it.optString("artifact").ifEmpty { it.optString("version") }) }
        }
        if (image != null) Card("Image ready to install") {
            InfoRow("File", image.optString("name"))
            InfoRow("Size", String.format("%.0f MB", image.optDouble("bytes", 0.0) / 1048576))
            Pill("Install on this speaker", filled = true) { ep.fire(scope, "/api/firmware/install", mapOf("reboot" to true, "carry" to true), "Installing") }
            Pill("Install across the room") { ep.fire(scope, "/api/firmware/room", mapOf("action" to "install"), "Room update started") }
        }
        Card("Get an image") {
            Pill("Upload from this phone…") { picker.launch(arrayOf("*/*")) }
            Text("A .img.gz from Files or Downloads. The speaker writes it to the spare slot; nothing changes until you install.",
                color = Theme.muted, fontSize = 12.sp)
        }
        val log = ep.data.strings("log")
        if (ep.data.optBoolean("busy") || log.isNotEmpty()) Card(if (ep.data.optBoolean("busy")) "Working" else "Last run") {
            if (ep.data.has("progress")) LinearProgressIndicator(progress = { ep.data.optDouble("progress", 0.0).toFloat().coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth(), color = Color.White, trackColor = Theme.surface2)
            Text(log.takeLast(8).joinToString("\n"), color = Theme.muted, fontSize = 11.sp)
        }
        Card("Careful") {
            Pill("Roll back to the other slot") { ep.fire(scope, "/api/firmware/rollback", emptyMap(), "Rolling back") }
            Text("Switches which partition boots next. The speaker restarts.", color = Theme.muted, fontSize = 12.sp)
        }
    }
}

@Composable
private fun ChannelRow(label: String, v: Float, set: (Float) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = Theme.muted, fontSize = 13.sp, modifier = Modifier.width(26.dp))
        Slider(value = v, onValueChange = set, modifier = Modifier.weight(1f),
            colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Theme.surface2))
    }
}

@Composable
private fun ToneRow(label: String, v: Float, set: (Float) -> Unit, done: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = Theme.muted, fontSize = 14.sp, modifier = Modifier.width(52.dp))
        Slider(value = v, onValueChange = set, valueRange = -1f..1f, modifier = Modifier.weight(1f),
            onValueChangeFinished = done,
            colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Theme.surface2))
    }
}

@Composable
private fun InfoRow(k: String, v: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(k, color = Theme.muted, fontSize = 12.sp)
        Spacer(Modifier.weight(1f))
        Text(v, fontSize = 12.sp, maxLines = 1)
    }
}

// ---------------------------------------------------------------- system

@Composable
fun SystemSetup(room: Room, onBack: () -> Unit) {
    val ep = rememberEp(room.agent, "/api/fleet/live", 8000)
    val stats = rememberEp(room.agent, "/api/stats", 10000)
    val house = rememberEp(room.agent, "/api/housekeeping", 0)
    val cloud = rememberEp(room.agent, "/api/cloudblock/all", 0)
    val scope = rememberCoroutineScope()
    var sleepMinutes by remember { mutableFloatStateOf(0f) }

    Page("System", ep, onBack) {
        Card("Speakers, live") {
            ep.data.list("cells").forEach { c ->
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box(Modifier.size(7.dp).clip(RoundedCornerShape(4.dp)).background(
                        if (!c.optBoolean("ok", true)) Theme.bad else if (c.optBoolean("sync")) Theme.live else Theme.amber))
                    Text(c.optString("serial").takeLast(5), fontWeight = FontWeight.SemiBold, fontSize = 12.sp)
                    Text(c.optString("role"), color = Theme.muted, fontSize = 11.sp)
                    Spacer(Modifier.weight(1f))
                    if (c.has("rssi") && !c.isNull("rssi")) Text("${c.optInt("rssi")} dBm", color = Theme.muted, fontSize = 11.sp)
                    Text(if (c.optBoolean("syfi")) "SyFi" else "—", color = Theme.muted, fontSize = 11.sp)
                    Text("b" + c.optInt("agent", 0), color = Theme.muted, fontSize = 11.sp)
                }
            }
        }
        Card("Sleep timer") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(if (sleepMinutes <= 0) "Off" else "${sleepMinutes.toInt()} min", fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                Spacer(Modifier.weight(1f))
                Pill("Set", filled = true) { ep.fire(scope, "/api/sleep", mapOf("minutes" to sleepMinutes.toInt()), if (sleepMinutes <= 0) "Sleep off" else "Sleeping in ${sleepMinutes.toInt()} min") }
            }
            Slider(value = sleepMinutes, onValueChange = { sleepMinutes = it }, valueRange = 0f..120f, steps = 23,
                colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Theme.surface2))
        }
        Card("Syng's cloud") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Block, room-wide", modifier = Modifier.weight(1f)) { cloud.fire(scope, "/api/cloudblock", mapOf("on" to true, "scope" to "group"), "Blocked") }
                Pill("Unblock", modifier = Modifier.weight(1f)) { cloud.fire(scope, "/api/cloudblock", mapOf("on" to false, "scope" to "group"), "Unblocked") }
            }
            val cells = cloud.data.list("cells")
            if (cells.isNotEmpty()) Text("${cells.count { it.optBoolean("blocked") }} of ${cells.size} blocked", color = Theme.muted, fontSize = 12.sp)
            Text("Blocked, the speakers keep the room document we write and Syng's cloud cannot overwrite it.", color = Theme.muted, fontSize = 12.sp)
        }
        Card("Housekeeping") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Journal", modifier = Modifier.weight(1f)) { house.fire(scope, "/api/housekeeping", mapOf("action" to "vacuum_journal"), "Journal trimmed") }
                Pill("Coredumps", modifier = Modifier.weight(1f)) { house.fire(scope, "/api/housekeeping", mapOf("action" to "clear_coredumps"), "Coredumps cleared") }
                Pill("Load", modifier = Modifier.weight(1f)) { scope.launch { house.load() } }
            }
            if (house.data.has("journal_bytes")) Text(String.format("journal %.0f MB · coredumps %.0f MB",
                house.data.optDouble("journal_bytes", 0.0) / 1048576, house.data.optDouble("coredump_bytes", 0.0) / 1048576),
                color = Theme.muted, fontSize = 12.sp)
        }
        stats.data.keys().asSequence().sorted().forEach { k ->
            val o = stats.data.o(k)
            Card(k) {
                o.keys().asSequence().sorted().take(8).forEach { kk ->
                    Row(Modifier.fillMaxWidth()) {
                        Text(kk, color = Theme.muted, fontSize = 11.sp)
                        Spacer(Modifier.weight(1f))
                        Text(o.opt(kk)?.toString()?.take(24) ?: "—", fontSize = 11.sp, maxLines = 1)
                    }
                }
            }
        }
    }
}
