package space.troubadour.app.ui

import android.annotation.SuppressLint
import android.graphics.Color as AColor
import android.net.Uri
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import coil.compose.AsyncImage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import space.troubadour.app.MainActivity
import space.troubadour.app.field.FieldCanvas
import space.troubadour.app.field.FieldModel
import space.troubadour.app.field.supportsCompute
import space.troubadour.app.net.Agent
import space.troubadour.app.net.Fleet
import space.troubadour.app.net.Room
import space.troubadour.app.net.RoomState

/**
 * One room, three tabs: **Room** (the sound field drawn natively, with playback under it),
 * **Speakers**, and **Setup** — native screens, with the cell's own page one tap away.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RoomScreen(room: Room, onBack: () -> Unit) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val tabs = listOf("Room" to Icons.Default.BlurOn, "Speakers" to Icons.Default.Speaker, "Setup" to Icons.Default.Tune)
    Scaffold(containerColor = Theme.bg,
        topBar = { TopAppBar(title = { Text(room.name, fontWeight = FontWeight.Bold) }, navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, null, tint = Color.White) } },
            colors = TopAppBarDefaults.topAppBarColors(containerColor = Theme.bg, titleContentColor = Color.White)) },
        bottomBar = { NavigationBar(containerColor = Theme.bg) { tabs.forEachIndexed { i, (l, ic) -> NavigationBarItem(selected = tab == i, onClick = { tab = i }, icon = { Icon(ic, null) }, label = { Text(l) },
            colors = NavigationBarItemDefaults.colors(selectedIconColor = Color.Black, indicatorColor = Color.White, selectedTextColor = Color.White, unselectedIconColor = Theme.muted, unselectedTextColor = Theme.muted)) } } }
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when (tab) {
                0 -> RoomMain(room)
                1 -> Speakers(room)
                else -> SetupScreen(room)
            }
        }
    }
}

/** Polls /api/state every 2 s; volume goes out while dragging at ~12 Hz plus the final value. */
class RoomModel(val agent: Agent?) {
    var state by mutableStateOf(RoomState()); var scenes by mutableStateOf(listOf<String>()); var lastScene by mutableStateOf<String?>(null)
    var volume by mutableDoubleStateOf(0.0); var error by mutableStateOf<String?>(null)
    private var dragging = false; private var lastSend = 0L
    suspend fun tick() {
        val a = agent ?: return
        try {
            state = RoomState.from(a.get("/api/state")); if (!dragging) volume = state.volume
            val s = a.get("/api/scenes"); scenes = s.optJSONObject("scenes")?.keys()?.asSequence()?.sorted()?.toList() ?: emptyList(); lastScene = s.optString("last").takeIf { it.isNotEmpty() && !s.isNull("last") }
            error = null
        } catch (e: Exception) { error = e.message }
    }
    suspend fun setVolume(v: Double, final: Boolean) {
        volume = v; dragging = !final
        val now = System.currentTimeMillis()
        if (final || now - lastSend > 80) { lastSend = now; runCatching { agent?.post("/api/volume", mapOf("source" to "all", "volume" to v)) } }
    }
}

/** The field fills the screen; what is playing lives in a compact strip under it. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RoomMain(room: Room) {
    val ctx = LocalContext.current
    val m = remember(room.id) { RoomModel(room.agent) }
    val field = remember(room.id) {
        FieldModel(room.agent).also { f ->
            // the widgets draw the agent's field: redraw them once a change has settled (debounced 2 s)
            var pending: kotlinx.coroutines.Job? = null
            f.onSettled = {
                pending?.cancel()
                pending = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.Default).launch {
                    delay(2000); space.troubadour.app.widget.AuraWidget.refresh(ctx.applicationContext)
                }
            }
        }
    }
    val scope = rememberCoroutineScope()
    var sheet by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(room.id) { while (true) { m.tick(); delay(2000) } }

    val vol by field.volPct.collectAsState()
    val spatial by field.spatialOn.collectAsState()
    val widthDeg by field.widthDeg.collectAsState()
    val aimDeg by field.aimDeg.collectAsState()
    val live by field.live.collectAsState()
    val fieldErr by field.error.collectAsState()
    val st = m.state
    val act = st.active

    Column(Modifier.fillMaxSize()) {
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (supportsCompute(ctx)) FieldCanvas(field, Modifier.fillMaxSize())
            else AgentPage(room.agent, "/#fieldwrap")
        }
        Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Box(Modifier.size(46.dp).clip(RoundedCornerShape(10.dp)).background(Theme.surface2), contentAlignment = Alignment.Center) {
                    if (act?.hasArtwork == true && m.agent != null)
                        AsyncImage(model = "${m.agent.base}/api/artwork?source=${act.id}&k=${Uri.encode(act.title)}", contentDescription = null, modifier = Modifier.fillMaxSize())
                    else Icon(Icons.Default.MusicNote, null, tint = Theme.muted)
                }
                Column(Modifier.weight(1f)) {
                    Text(if (act != null && act.title.isNotEmpty()) act.title else (act?.displayName ?: "Nothing playing"),
                        fontWeight = FontWeight.SemiBold, fontSize = 15.sp, maxLines = 1)
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                        Box(Modifier.size(6.dp).clip(RoundedCornerShape(3.dp)).background(if (live) Theme.live else Theme.muted.copy(alpha = .4f)))
                        Text(listOf(act?.artist, act?.album).filter { !it.isNullOrEmpty() }.joinToString(" · ").ifEmpty { "drag the puck to move the sound" },
                            color = Theme.muted, fontSize = 12.sp, maxLines = 1)
                    }
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("$vol", fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                    Text(if (spatial) "$widthDeg° · $aimDeg°" else "all around", color = Theme.muted, fontSize = 11.sp)
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                fun tr(a: String) = scope.launch { act?.let { runCatching { m.agent?.post("/api/transport", mapOf("source" to it.id, "action" to a)) }; m.tick() } }
                Pill("⏮", modifier = Modifier.weight(1f), enabled = act != null) { tr("previous") }
                Pill(if (act?.playing == true) "Pause" else "Play", filled = true, modifier = Modifier.weight(1f), enabled = act != null) { tr(if (act?.playing == true) "pause" else "play") }
                Pill("⏭", modifier = Modifier.weight(1f), enabled = act != null) { tr("next") }
            }
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Centre") { field.centrePuck() }
                Pill(if (spatial) "All around" else "Spatial", filled = spatial) { field.setSpatialOn(!spatial) }
                Pill(if (st.soundMode == "direct") "Direct" else "Syng") { sheet = "mode" }
                if (m.scenes.isNotEmpty()) Pill("Scenes") { sheet = "scenes" }
                Pill(if (act?.mute == true) "Muted" else "Mute", filled = act?.mute == true) {
                    scope.launch { runCatching { m.agent?.post("/api/mute", mapOf("on" to !(act?.mute ?: false))) }; m.tick() }
                }
                st.sources.forEach { s ->
                    Pill(s.displayName, filled = s.active) {
                        scope.launch { runCatching { m.agent?.post("/api/source", mapOf("id" to s.id, "on" to !s.active)) }; m.tick() }
                    }
                }
            }
            (fieldErr ?: m.error)?.let { Text(it, color = Theme.bad, fontSize = 11.sp, maxLines = 1) }
        }
    }

    if (sheet != null) {
        ModalBottomSheet(onDismissRequest = { sheet = null }, containerColor = Theme.bg) {
            Column(Modifier.padding(16.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                if (sheet == "mode") {
                    Card("Sound mode") {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Pill("Syng", filled = st.soundMode == "syng", modifier = Modifier.weight(1f)) {
                                scope.launch { runCatching { m.agent?.post("/api/soundmode", mapOf("mode" to "syng")) }; m.tick() }
                            }
                            Pill("Direct", filled = st.soundMode == "direct", modifier = Modifier.weight(1f)) {
                                scope.launch { runCatching { m.agent?.post("/api/soundmode", mapOf("mode" to "direct")) }; m.tick() }
                            }
                        }
                        Text("Syng steers the sound across the speakers and their drivers. Direct: every speaker plays the whole mix through all three drivers at the same level.",
                            color = Theme.muted, fontSize = 12.sp)
                    }
                    Card("Volume") {
                        Slider(value = m.volume.toFloat(), onValueChange = { scope.launch { m.setVolume(it.toDouble(), false) } },
                            onValueChangeFinished = { scope.launch { m.setVolume(m.volume, true) } },
                            colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Theme.surface2))
                        Text("The rim at the bottom of the field does this too — drag from where the volume already sits.",
                            color = Theme.muted, fontSize = 12.sp)
                    }
                } else {
                    Card("Scenes") {
                        m.scenes.forEach { n ->
                            Pill(n, filled = m.lastScene == n, modifier = Modifier.fillMaxWidth()) {
                                scope.launch { runCatching { m.agent?.post("/api/scenes/apply", mapOf("name" to n)) }; m.tick(); sheet = null }
                            }
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun Speakers(room: Room) {
    val scope = rememberCoroutineScope()
    var renaming by remember { mutableStateOf<String?>(null) }; var newName by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Card("Speakers") {
            room.cells.forEach { c ->
                Row(Modifier.fillMaxWidth().combinedClickable(onClick = {}, onLongClick = { renaming = c.serial; newName = c.name ?: "" }).padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Puck(40, if (c.role == "primary") Color.White else null)
                    Column(Modifier.weight(1f)) {
                        Text(c.title, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                        Text("${c.shortSerial} · ${c.ip} · ${c.role ?: ""}${if (c.calibrated == false) " · uncalibrated" else ""}", color = Theme.muted, fontSize = 12.sp)
                    }
                    Pill("Ping", modifier = Modifier.width(80.dp)) { scope.launch { runCatching { room.agent?.post("/api/ping", mapOf("serial" to c.serial)) } } }
                }
            }
            Pill("Ping all") { scope.launch { runCatching { room.agent?.post("/api/ping", mapOf("all" to true)) } } }
            Text("Long-press a speaker to name it.", color = Theme.muted, fontSize = 12.sp)
        }
    }
    renaming?.let { serial ->
        Dialog(onDismissRequest = { renaming = null }) {
            Column(Modifier.clip(RoundedCornerShape(20.dp)).background(Theme.surface).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("Name this speaker", fontWeight = FontWeight.Bold)
                OutlinedTextField(newName, { newName = it }, singleLine = true, placeholder = { Text("Kitchen window") })
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Pill("Cancel", modifier = Modifier.weight(1f)) { renaming = null }
                    Pill("Save", filled = true, modifier = Modifier.weight(1f)) { scope.launch { runCatching { room.agent?.post("/api/cell/name", mapOf("serial" to serial, "name" to newName)) }; Fleet.refresh(); renaming = null } }
                }
            }
        }
    }
}

/** The agent's own page, kept as the escape hatch behind Setup. */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun AgentPage(agent: Agent?, path: String) {
    val ctx = LocalContext.current
    AndroidView(factory = { c ->
        WebView(c).apply {
            setBackgroundColor(AColor.BLACK)
            settings.javaScriptEnabled = true; settings.domStorageEnabled = true; settings.allowFileAccess = true
            settings.mediaPlaybackRequiresUserGesture = false
            webViewClient = WebViewClient()
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(w: WebView?, cb: ValueCallback<Array<Uri>>?, p: FileChooserParams?): Boolean {
                    val act = ctx as? MainActivity ?: return false
                    act.fileChooser?.onReceiveValue(null); act.fileChooser = cb
                    act.pickFile.launch(arrayOf("*/*")); return true
                }
            }
            if (agent != null) loadUrl(agent.base + path)
        }
    }, update = { w -> if (agent != null && (w.url == null || w.url == "about:blank")) w.loadUrl(agent.base + path) }, modifier = Modifier.fillMaxSize())
}
