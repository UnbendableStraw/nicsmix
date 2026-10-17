package space.troubadour.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.ValueCallback
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.graphics.Color
import androidx.lifecycle.lifecycleScope
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import kotlinx.coroutines.launch
import space.troubadour.app.net.Agent
import space.troubadour.app.net.Fleet
import space.troubadour.app.net.RoomState
import space.troubadour.app.ui.RoomScreen
import space.troubadour.app.ui.RoomsScreen
import space.troubadour.app.ui.Theme

class MainActivity : ComponentActivity() {
    /** The setup page's firmware upload uses <input type=file>; WebView needs the activity to open the picker. */
    var fileChooser: ValueCallback<Array<Uri>>? = null
    val pickFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        fileChooser?.onReceiveValue(if (uri != null) arrayOf(uri) else null); fileChooser = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Fleet.start(this)
        handleShortcut(intent)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Color.White, onPrimary = Color.Black, background = Theme.bg, surface = Theme.surface, onSurface = Color.White, secondary = Theme.muted)) {
                val nav = rememberNavController()
                NavHost(navController = nav, startDestination = "rooms") {
                    composable("rooms") { RoomsScreen(onOpen = { nav.navigate("room/${Uri.encode(it.id)}") }) }
                    composable("room/{id}") { b ->
                        val id = Uri.decode(b.arguments?.getString("id") ?: "")
                        val rooms by Fleet.rooms.collectAsState()
                        rooms.firstOrNull { it.id == id }?.let { RoomScreen(it, onBack = { nav.popBackStack() }) }
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); handleShortcut(intent) }

    /** Launcher shortcuts: act on the last room this phone controlled, no UI. */
    private fun handleShortcut(i: Intent?) {
        val ip = Fleet.lastRoomAgent ?: return
        when (i?.action) {
            "space.troubadour.PAUSE" -> lifecycleScope.launch {
                runCatching {
                    val st = RoomState.from(Agent(ip).get("/api/state"))
                    st.active?.let { Agent(ip).post("/api/transport", mapOf("source" to it.id, "action" to if (it.playing) "pause" else "play")) }
                }
            }
            "space.troubadour.MUTE" -> lifecycleScope.launch {
                runCatching { val st = RoomState.from(Agent(ip).get("/api/state")); Agent(ip).post("/api/mute", mapOf("on" to !(st.active?.mute ?: false))) }
            }
        }
    }
}
