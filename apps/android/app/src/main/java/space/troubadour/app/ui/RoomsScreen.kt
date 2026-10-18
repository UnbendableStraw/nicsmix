package space.troubadour.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.Canvas
import kotlinx.coroutines.launch
import androidx.compose.ui.platform.LocalContext
import androidx.glance.appwidget.updateAll
import space.troubadour.app.net.Fleet
import space.troubadour.app.widget.FieldWidget
import space.troubadour.app.widget.LedWidget
import space.troubadour.app.net.Room
import kotlin.math.cos
import kotlin.math.sin

/** The Syng app's palette. */
object Theme {
    val bg = Color.Black
    val surface = Color(0xFF1A1A1A)
    val surface2 = Color(0xFF2A2A2A)
    val muted = Color(0xFF8E8E8E)
    val live = Color(0xFF79DE96)
    val bad = Color(0xFFE0514C)
    val amber = Color(0xFFE3A24C)
}

@Composable
fun Card(title: String? = null, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(Theme.surface).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (title != null) Text(title.uppercase(), color = Theme.muted, fontSize = 12.sp, letterSpacing = 1.2.sp, fontWeight = FontWeight.Medium)
        content()
    }
}

/** White capsule = primary / selected; grey capsule = secondary (the app's buttons). */
@Composable
fun Pill(text: String, filled: Boolean = false, modifier: Modifier = Modifier, enabled: Boolean = true, onClick: () -> Unit) {
    Button(onClick = onClick, enabled = enabled, modifier = modifier, shape = CircleShape, contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp),
        colors = ButtonDefaults.buttonColors(containerColor = if (filled) Color.White else Theme.surface2, contentColor = if (filled) Color.Black else Color.White,
            disabledContainerColor = Theme.surface2.copy(alpha = .5f), disabledContentColor = Theme.muted)) {
        Text(text, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, maxLines = 1)
    }
}

/** A Cell as the app draws it: black puck, three amber dots. */
@Composable
fun Puck(size: Int = 40, ring: Color? = null) {
    Canvas(Modifier.size(size.dp)) {
        val r = this.size.minDimension / 2
        drawCircle(Brush.radialGradient(listOf(Color(0xFF3B3B3B), Color(0xFF070707)), center = Offset(r * .84f, r * .72f), radius = r * 1.2f), r)
        for (i in 0..2) { val a = i * 2 * Math.PI / 3 - Math.PI / 2; drawCircle(Theme.amber, r * .12f, Offset(r + (cos(a) * r * .4).toFloat(), r + (sin(a) * r * .4).toFloat())) }
        if (ring != null) drawCircle(ring, r + 3.dp.toPx(), style = androidx.compose.ui.graphics.drawscope.Stroke(1.5.dp.toPx()))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RoomsScreen(onOpen: (Room) -> Unit) {
    val rooms by Fleet.rooms.collectAsState()
    val status by Fleet.status.collectAsState()
    val scanning by Fleet.scanning.collectAsState()
    var manual by remember { mutableStateOf(false) }
    var ip by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    Scaffold(containerColor = Theme.bg, topBar = {
        TopAppBar(title = { Text("Rooms", fontWeight = FontWeight.Bold) }, colors = TopAppBarDefaults.topAppBarColors(containerColor = Theme.bg, titleContentColor = Color.White),
            actions = { TextButton(onClick = { manual = !manual }) { Text("Add by address", color = Theme.muted) }; TextButton(onClick = { scope.launch { Fleet.refresh(force = true) } }) { Text(if (scanning) "…" else "Rescan", color = Color.White) } })
    }) { pad ->
        LazyColumn(Modifier.padding(pad).fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            if (rooms.isEmpty()) item { Card { Text(if (scanning) "Looking for your Cells…" else (status ?: "No Cells found yet. Same Wi-Fi as the speakers? Or plug one in over USB-C and add 10.86.0.1."), color = Theme.muted, fontSize = 14.sp) } }
            items(rooms, key = { it.id }) { r ->
                Box(Modifier.clickable { Fleet.lastRoomAgent = r.agent?.ip; scope.launch { FieldWidget().updateAll(ctx); space.troubadour.app.widget.AuraWidget().updateAll(ctx); LedWidget().updateAll(ctx) }; onOpen(r) }) {
                    Card {
                        Row(verticalAlignment = Alignment.Bottom) {
                            Text(r.name, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                            Spacer(Modifier.width(8.dp))
                            Text("${r.cells.size} speaker${if (r.cells.size == 1) "" else "s"}", color = Theme.muted, fontSize = 14.sp)
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy((-8).dp)) { r.cells.take(8).forEach { Puck(34, if (it.role == "primary") Color.White else null) } }
                        Text(r.cells.joinToString(" · ") { it.title }, color = Theme.muted, fontSize = 12.sp, maxLines = 2)
                    }
                }
            }
            if (manual) item {
                Card("Add a speaker by address") {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(ip, { ip = it }, Modifier.weight(1f), placeholder = { Text("192.168.1.23 or 10.86.0.1") }, singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                        Pill("Add", filled = true) { Fleet.addSeed(ip); ip = ""; manual = false }
                    }
                }
            }
        }
    }
}
