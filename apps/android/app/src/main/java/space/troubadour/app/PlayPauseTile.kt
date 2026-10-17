package space.troubadour.app

import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import space.troubadour.app.net.Agent
import space.troubadour.app.net.Fleet
import space.troubadour.app.net.RoomState

/** Quick Settings tile: play/pause the last room this phone controlled. Long-press opens the app. */
class PlayPauseTile : TileService() {
    private val job = Job(); private val scope = CoroutineScope(Dispatchers.Main + job)

    override fun onStartListening() { super.onStartListening(); Fleet.start(applicationContext); refresh() }
    override fun onDestroy() { job.cancel(); super.onDestroy() }

    override fun onClick() {
        val ip = Fleet.lastRoomAgent ?: run { qsTile?.apply { state = Tile.STATE_UNAVAILABLE; label = "Open Troubadour first"; updateTile() }; return }
        scope.launch {
            runCatching {
                val st = RoomState.from(Agent(ip).get("/api/state"))
                val a = st.active ?: return@runCatching
                Agent(ip).post("/api/transport", mapOf("source" to a.id, "action" to if (a.playing) "pause" else "play"))
            }
            refresh()
        }
    }

    private fun refresh() {
        val t = qsTile ?: return
        val ip = Fleet.lastRoomAgent
        if (ip == null) { t.state = Tile.STATE_UNAVAILABLE; t.label = "Troubadour"; t.updateTile(); return }
        scope.launch {
            val st = runCatching { RoomState.from(Agent(ip).get("/api/state", 4)) }.getOrNull()
            val a = st?.active
            t.state = if (a?.playing == true) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
            t.label = when { st == null -> "Troubadour (offline)"; a == null -> "Nothing playing"; a.playing -> "Playing · ${a.displayName}"; else -> "Paused · ${a.displayName}" }
            t.updateTile()
        }
    }
}
