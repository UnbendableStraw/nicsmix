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
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import coil.compose.AsyncImage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import space.troubadour.app.MainActivity
import space.troubadour.app.net.Agent
import space.troubadour.app.net.Fleet
import space.troubadour.app.net.Room
import space.troubadour.app.net.RoomState

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RoomScreen(room: Room, onBack: () -> Unit) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val tabs = listOf("Now Playing" to Icons.Default.GraphicEq, "Field" to Icons.Default.BlurOn, "Speakers" to Icons.Default.Speaker, "Setup" to Icons.Default.Tune)
    Scaffold(containerColor = Theme.bg,
        topBar = { TopAppBar(title = { Text(room.name, fontWeight = FontWeight.Bold) }, navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, null, tint = Color.White) } },
            colors = TopAppBarDefaults.topAppBarColors(containerColor = Theme.bg, titleContentColor = Color.White)) },
        bottomBar = { NavigationBar(containerColor = Theme.bg) { tabs.forEachIndexed { i, (l, ic) -> NavigationBarItem(selected = tab == i, onClick = { tab = i }, icon = { Icon(ic, null) }, label = { Text(l) },
            colors = NavigationBarItemDefaults.colors(selectedIconColor = Color.Black, indicatorColor = Color.White, selectedTextColor = Color.White, unselectedIconColor = Theme.muted, unselectedTextColor = Theme.muted)) } } }
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when (tab) {
                0 -> NowPlaying(room)
                1 -> AgentPage(room.agent, "/#fieldwrap")
                2 -> Speakers(room)
                else -> AgentPage(room.agent, "/setup")
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

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun NowPlaying(room: Room) {
    val m = remember(room.id) { RoomModel(room.agent) }
    val scope = rememberCoroutineScope()
    val haptic = LocalHapticFeedback.current
    var lastStep by remember { mutableIntStateOf(-1) }
    LaunchedEffect(room.id) { while (true) { m.tick(); delay(2000) } }
    val st = m.state; val act = st.active
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Card("Playback") {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                Box(Modifier.size(72.dp).clip(RoundedCornerShape(12.dp)).background(Theme.surface2), contentAlignment = Alignment.Center) {
                    if (act?.hasArtwork == true && m.agent != null) AsyncImage(model = "${m.agent.base}/api/artwork?source=${act.id}&k=${Uri.encode(act.title)}", contentDescription = null, modifier = Modifier.fillMaxSize())
                    else Icon(Icons.Default.MusicNote, null, tint = Theme.muted)
                }
                Column(Modifier.weight(1f)) {
                    Text(if (act != null && act.title.isNotEmpty()) act.title else (act?.displayName ?: "Nothing playing"), fontWeight = FontWeight.SemiBold, fontSize = 16.sp, maxLines = 2)
                    Text(listOf(act?.artist, act?.album).filter { !it.isNullOrEmpty() }.joinToString(" · "), color = Theme.muted, fontSize = 13.sp, maxLines = 1)
                    st.viaPrimary?.let { Text("via $it", color = Theme.muted, fontSize = 11.sp) }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                fun tr(a: String) = scope.launch { act?.let { runCatching { m.agent?.post("/api/transport", mapOf("source" to it.id, "action" to a)) }; m.tick() } }
                Pill("⏮", modifier = Modifier.weight(1f), enabled = act != null) { tr("previous") }
                Pill(if (act?.playing == true) "Pause" else "Play", filled = true, modifier = Modifier.weight(1f), enabled = act != null) { tr(if (act?.playing == true) "pause" else "play") }
                Pill("⏭", modifier = Modifier.weight(1f), enabled = act != null) { tr("next") }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                st.sources.forEach { s -> Pill(s.displayName, filled = s.active, modifier = Modifier.weight(1f)) { scope.launch { runCatching { m.agent?.post("/api/source", mapOf("id" to s.id, "on" to !s.active)) }; m.tick() } } }
            }
            Row { Text("Volume", color = Theme.muted, fontSize = 14.sp); Spacer(Modifier.weight(1f)); Text("${(m.volume * 100).toInt()}", fontWeight = FontWeight.SemiBold) }
            Slider(value = m.volume.toFloat(), onValueChange = { v ->
                val step = (v * 20).toInt(); if (step != lastStep) { lastStep = step; haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove) }
                scope.launch { m.setVolume(v.toDouble(), final = false) }
            }, onValueChangeFinished = { scope.launch { m.setVolume(m.volume, final = true) } },
                colors = SliderDefaults.colors(thumbColor = Color.White, activeTrackColor = Color.White, inactiveTrackColor = Theme.surface2))
            Pill(if (act?.mute == true) "Muted" else "Mute", filled = act?.mute == true) { scope.launch { runCatching { m.agent?.post("/api/mute", mapOf("on" to !(act?.mute ?: false))) }; m.tick() } }
        }
        Card("Sound mode") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Pill("Syng", filled = st.soundMode == "syng", modifier = Modifier.weight(1f)) { scope.launch { runCatching { m.agent?.post("/api/soundmode", mapOf("mode" to "syng")) }; m.tick() } }
                Pill("Direct", filled = st.soundMode == "direct", modifier = Modifier.weight(1f)) { scope.launch { runCatching { m.agent?.post("/api/soundmode", mapOf("mode" to "direct")) }; m.tick() } }
            }
            Text("Syng steers the sound across the speakers and their drivers. Direct: every speaker plays the whole mix through all three drivers at the same level.", color = Theme.muted, fontSize = 12.sp)
        }
        if (m.scenes.isNotEmpty()) Card("Scenes") {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                m.scenes.forEach { n -> Pill(n, filled = m.lastScene == n) { scope.launch { runCatching { m.agent?.post("/api/scenes/apply", mapOf("name" to n)) }; m.tick() } } }
            }
        }
        m.error?.let { Text(it, color = Theme.bad, fontSize = 12.sp) }
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

/** The agent's own pages (sound field, setup). Its file picker (firmware upload) is routed through the activity. */
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
