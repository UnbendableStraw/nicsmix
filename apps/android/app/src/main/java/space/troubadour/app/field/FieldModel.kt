package space.troubadour.app.field

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.Request
import org.json.JSONObject
import space.troubadour.app.net.Agent
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.random.Random

/**
 * Everything the sound field knows, on the CPU: layout (in pixels), the app's springs, the live
 * audio feed, the flurry/pulse animation and the two endpoints it drives. The renderer reads this
 * straight out on the GL thread; touches are queued onto the same thread, so nothing is shared
 * across threads except the StateFlows the UI collects.
 */
class FieldModel(val agent: Agent?) {

    // ---- config (the web field's cfg, trimmed) ----
    var particles = 24000
    var sceneScale = 0.62f
    var reactiveFrac = 0.5
    var simHz = 30.0
    var weightSpatial = 0.88
    var weightOmni = 2.0
    var handleRadius = 0.60
    var defaultHalfDeg = 95.0
    var minHalfDeg = 12.0
    var maxHalfDeg = 178.0
    var snapToCell = true
    var snapFrac = 0.13
    var liftFrac = 0.26
    var collapseFrac = 0.2
    var pulseScale = 2.0
    var pulseGain = 0.35
    var pulseEmitter = 0.06
    var pulseJitter = 0.25
    var puffGain = 0.005
    var puffBeat = 0.005
    var puffFloat = 0.9
    var puffVmax = 0.008
    var puffReach = 0.2
    var glow = 1.0
    var grain = 1.0
    var flurryGain = 1.0
    var haloGain = 1.0
    var pointScale = 1.0
    var pointGain = 1.0
    var radiusUnits = 0.7
    var showVolume = true
    var showRings = true
    var followLed = true

    // ---- layout, in device pixels ----
    var width = 1080.0; var height = 1000.0
    var cx = 540.0; var cy = 500.0
    var roomR = 400.0
    var density = 3.0
    private var placed = false

    fun layout(w: Int, h: Int, dens: Double) {
        if (w < 2 || h < 2) return
        density = dens
        val box = min(w.toDouble(), h.toDouble() / 0.94)
        val r = box * 0.385
        val ncx = w / 2.0
        val ncy = (h - box * 0.94) / 2.0 + 0.045 * box + r
        if (placed && (abs(r - roomR) > 0.5 || abs(ncx - cx) > 0.5 || abs(ncy - cy) > 0.5)) {
            val ux = (puckTx - cx) / roomR; val uy = -(puckTy - cy) / roomR
            width = w.toDouble(); height = h.toDouble(); cx = ncx; cy = ncy; roomR = r
            puckTx = cx + ux * roomR; puckTy = cy - uy * roomR
            puckX = puckTx; puckY = puckTy; puckVx = 0.0; puckVy = 0.0
        } else {
            width = w.toDouble(); height = h.toDouble(); cx = ncx; cy = ncy; roomR = r
        }
        if (!placed) { puckX = cx; puckY = cy; puckTx = cx; puckTy = cy; placed = true }
    }

    // ---- field state ----
    var puckX = 0.0; var puckY = 0.0
    var puckTx = 0.0; var puckTy = 0.0
    private var puckVx = 0.0; private var puckVy = 0.0
    var puckScale = 1.0; private var puckScaleV = 0.0
    var spatial = false
    var dir = 0.0; var dirT = 0.0; private var dirV = 0.0
    var half = 0.0; var halfT = 0.0; private var halfV = 0.0
    var weight = 2.0; private var weightV = 0.0
    var volume = 0.25; private var volT = 0.25
    var snapped: String? = null
    var time = 0.0

    // ---- audio ----
    var energy = 0.25; var bass = 0.0; var beat = 0.0
    val spec = DoubleArray(24)
    private val rawSpec = DoubleArray(24)
    private val emaSpec = DoubleArray(24)
    val flux = DoubleArray(24)
    private var seq = -1
    private var feedFps = 30.0
    private var lastRow = 0L

    // ---- the pulse texture rows waiting to be uploaded ----
    private val pulseQueue = ArrayDeque<ByteArray>()
    var pulseHead = 0
        private set
    fun drainPulseRows(): List<Pair<Int, ByteArray>> {
        if (pulseQueue.isEmpty()) return emptyList()
        val out = ArrayList<Pair<Int, ByteArray>>()
        synchronized(pulseQueue) {
            while (pulseQueue.isNotEmpty()) {
                out.add(pulseHead to pulseQueue.removeFirst())
                pulseHead = (pulseHead - 1 + PULSE_ROWS) % PULSE_ROWS
            }
        }
        return out
    }
    val pulseHeadNorm: Double
        get() {
            val age = min(max((System.nanoTime() - lastRow) / 1e9 * feedFps, 0.0), 1.0)
            return (pulseHead + 1 - age) / PULSE_ROWS
        }

    // ---- flurry blobs and the pulse field ----
    class Blob { var x = 0.0; var y = 0.0; var r = 0.4; var a = 0.0; var vx = 0.0; var vy = 0.0; var life = 0.0; var ph = 0.0 }
    val blobs = Array(24) { Blob() }
    private class Phase(var ang: Double, var w: Double, var p1: Double, var p2: Double, var w1: Double, var w2: Double, var off: Double)
    private val phases = ArrayList<Phase>()
    private val bands = DoubleArray(6)
    val pulseE = FloatArray(24)
    val pulseP = FloatArray(24)

    // ---- speakers ----
    class Ring(val x: Double, val y: Double, val active: Boolean, val serial: String)
    @Volatile var rings: List<Ring> = emptyList()
    @Volatile var listener: Pair<Double, Double>? = null
    private var refX = 0.0; private var refY = 0.0; private var refR = 1.15; private var refRot = 0.0
    private var cellsM: List<Triple<Pair<Double, Double>, Boolean, String>> = emptyList()
    private var listenerM: Pair<Double, Double>? = null

    // ---- palette ----
    var palette = FieldPalette.airplay()
    private var sourcePalette = FieldPalette.airplay()
    private var ledPalette: FieldPalette? = null

    // ---- input ----
    sealed class Grab {
        object Puck : Grab(); object Lift : Grab(); object Notch : Grab(); object Volume : Grab()
        class Dot(val sgn: Int) : Grab()
    }
    var grab: Grab? = null
    var liftDot: Pair<Double, Double>? = null
    private var grabOx = 0.0; private var grabOy = 0.0
    private var volGrabFinger = 0.0; private var volGrabBase = 0.0; private var volEngaged = false
    private var touched = 0L

    // ---- what the UI shows ----
    val volPct = MutableStateFlow(25)
    val widthDeg = MutableStateFlow(95)
    val aimDeg = MutableStateFlow(0)
    val spatialOn = MutableStateFlow(false)
    val live = MutableStateFlow(false)
    val sourceTitle = MutableStateFlow("Sound field")
    val error = MutableStateFlow<String?>(null)
    var haptic: (() -> Unit)? = null

    private val job = SupervisorJob()
    private val scope = CoroutineScope(Dispatchers.IO + job)
    private var running = false
    private var lastPost = 0L

    init {
        val rnd = Random(7)
        for (b in blobs) {
            b.x = rnd.nextDouble(-0.4, 0.4); b.y = rnd.nextDouble(-0.4, 0.4)
            b.r = 0.36 + rnd.nextDouble(0.09); b.life = rnd.nextDouble(10.0); b.ph = rnd.nextDouble(TAU)
        }
        for (k in 0 until 6) {
            val s = if (k % 2 == 0) -1.0 else 1.0
            phases.add(Phase(rnd.nextDouble(TAU), (0.25 + 0.35 * rnd.nextDouble()) * s,
                rnd.nextDouble(TAU), rnd.nextDouble(TAU),
                (0.4 + 0.5 * rnd.nextDouble()) * -s, 0.3 + 0.4 * rnd.nextDouble(), 0.10 + 0.12 * rnd.nextDouble()))
        }
    }

    // ---- geometry (the web field's, one for one) ----
    val fieldR: Double get() = max(roomR * 0.2, roomR - hypot(puckX - cx, puckY - cy))
    val puckR: Double get() = roomR * 0.165 * puckScale
    val handleR: Double get() = roomR * 0.052
    val notchHalfW: Double get() = roomR * 0.042
    val notchHalfL: Double get() = roomR * 0.115
    fun handleX(a: Double) = puckX + sin(a) * handleRadius * fieldR
    fun handleY(a: Double) = puckY - cos(a) * handleRadius * fieldR
    val liftX: Double get() = puckX
    val liftY: Double get() = puckY - (puckR + handleR + roomR * 0.015)
    private fun angleOf(x: Double, y: Double) = atan2(x - puckX, -(y - puckY))
    val omni: Double get() = min(max((weight - weightSpatial) / (weightOmni - weightSpatial), 0.0), 1.0)

    // ---- the app's spring ----
    private fun spring(x: Double, v: Double, target: Double, dt: Double): DoubleArray {
        var xx = x; var vv = v; val h = 1.0 / 240.0; var t = 0.0
        while (t < dt) { val a = -K * (xx - target) - CD * vv; vv += a * h; xx += vv * h; t += h }
        return doubleArrayOf(xx, vv)
    }
    private fun wrapPi(a: Double): Double {
        var x = (a + Math.PI) % TAU
        if (x < 0) x += TAU
        return x - Math.PI
    }

    /** One frame of CPU state: springs, palette, the pulse field and the flurry blobs. */
    fun advance(dt: Double) {
        time += dt
        var r = spring(puckX, puckVx, puckTx, dt); puckX = r[0]; puckVx = r[1]
        r = spring(puckY, puckVy, puckTy, dt); puckY = r[0]; puckVy = r[1]
        r = spring(puckScale, puckScaleV, if (grab is Grab.Puck) 1.1 else 1.0, dt); puckScale = r[0]; puckScaleV = r[1]
        dir = dirT + wrapPi(dir - dirT)
        r = spring(dir, dirV, dirT, dt); dir = r[0]; dirV = r[1]
        r = spring(half, halfV, halfT, dt); half = r[0]; halfV = r[1]
        r = spring(weight, weightV, if (spatial) weightSpatial else weightOmni, dt); weight = r[0]; weightV = r[1]
        volume += (volT - volume) * min(1.0, dt * 14)

        palette.blend(ledPalette ?: sourcePalette, min(1.0, dt * if (ledPalette != null) 2.4 else 1.6))

        if ((System.nanoTime() - lastRow) / 1e9 > 0.4) {
            pulseAcc += dt * 30
            while (pulseAcc >= 1) {
                for (i in 0 until 24) flux[i] *= 0.5
                writePulseRow()
                pulseAcc -= 1.0
            }
        }

        for (k in 0 until 6) {
            var lv = 0.0
            for (i in k * 4 until k * 4 + 4) lv += 0.55 * spec[i] + 0.9 * flux[i]
            lv = min(1.0, lv / 4)
            val tgt = lv + 0.6 * beat * (if (k < 2) 1.0 else 0.4)
            bands[k] += (if (tgt > bands[k]) 0.55 else 0.10) * (tgt - bands[k])
            val q = phases[k]
            q.ang += q.w * dt; q.p1 += q.w1 * dt; q.p2 += q.w2 * dt
            val rad = 1.18 + 0.22 * k / 5.0 + 0.9 * bands[k]
            val amp = q.off * (0.6 + bands[k])
            pulseE[k * 4] = (cos(q.ang) * amp).toFloat()
            pulseE[k * 4 + 1] = (sin(q.ang) * amp).toFloat()
            pulseE[k * 4 + 2] = rad.toFloat()
            pulseE[k * 4 + 3] = (0.22 + 0.30 * bands[k]).toFloat()
            pulseP[k * 4] = q.p1.toFloat(); pulseP[k * 4 + 1] = q.p2.toFloat()
            pulseP[k * 4 + 2] = (0.10 + 0.14 * bands[k]).toFloat()
            pulseP[k * 4 + 3] = (0.06 + 0.10 * bands[k]).toFloat()
        }

        val rnd = Random.Default
        for (b in blobs) {
            b.life -= dt
            if (b.life <= 0) {
                b.life = 10 * (0.5 + rnd.nextDouble())
                val a = rnd.nextDouble(TAU); val rr = rnd.nextDouble(0.42)
                b.x = cos(a) * rr; b.y = sin(a) * rr
                b.r = 0.36 + rnd.nextDouble(0.09); b.ph = rnd.nextDouble(TAU)
                val va = rnd.nextDouble(TAU)
                b.vx = cos(va) * 0.02; b.vy = sin(va) * 0.02
            }
            b.x += b.vx * dt; b.y += b.vy * dt
            val lf = min(max(b.life / 2, 0.0), 1.0) * min(max((10 - b.life) / 2, 0.0), 1.0)
            b.a = lf * (0.55 + 0.45 * sin(time * 0.7 + b.ph))
        }
        refreshRings()
        publishLabels()
    }
    private var pulseAcc = 0.0

    private fun publishLabels() {
        val vp = Math.round(volume * 100).toInt()
        if (vp != volPct.value) volPct.value = vp
        val wd = Math.round(halfT * 180 / Math.PI).toInt()
        if (wd != widthDeg.value) widthDeg.value = wd
        var rot = (90 - dirT * 180 / Math.PI) % 360
        if (rot < 0) rot += 360
        val ad = Math.round(rot).toInt() % 360
        if (ad != aimDeg.value) aimDeg.value = ad
        if (spatial != spatialOn.value) spatialOn.value = spatial
    }

    /** prepSDF: the capped-torus parameters for the current direction / spread / weight. */
    fun sdfParams(): FloatArray {
        val halfRad = if (spatial) half else Math.PI
        val spread = min(max(halfRad / Math.PI, 0.05), 1.0)
        val sinA = sin(spread * Math.PI); val cosA = cos(spread * Math.PI)
        val nx = sin(dir); val ny = -cos(dir)
        val w = weight * 0.25
        val ra = 0.5 - w; val rb = w
        val sp2 = min(max(halfRad / Math.PI, 0.0), 1.0)
        val t = min(max((sp2 - 0.5) / 0.2, 0.0), 1.0)
        val capR = rb * (t * t * (3 - 2 * t))
        val blend = if (sp2 > 0.5) 0.1 else 1e-5
        val ca = cos(halfRad); val sa = sin(halfRad)
        return floatArrayOf(
            nx.toFloat(), ny.toFloat(), sinA.toFloat(), cosA.toFloat(),
            ra.toFloat(), rb.toFloat(), capR.toFloat(), blend.toFloat(),
            (ra * (nx * ca - ny * sa)).toFloat(), (ra * (nx * sa + ny * ca)).toFloat(),
            (ra * (nx * ca + ny * sa)).toFloat(), (ra * (-nx * sa + ny * ca)).toFloat())
    }

    // ---- gestures (queued onto the GL thread by the view) ----
    fun begin(x: Double, y: Double) {
        touched = System.nanoTime()
        val hr = handleR * 2.2
        if (spatial) {
            if (near(x, y, handleX(dir), handleY(dir), hr * 1.3)) { grab = Grab.Notch; return }
            for (sgn in intArrayOf(1, -1)) {
                val a = dir + sgn * half
                if (near(x, y, handleX(a), handleY(a), hr)) { grab = Grab.Dot(sgn); return }
            }
        } else if (near(x, y, liftX, liftY, hr)) {
            grab = Grab.Lift; liftDot = liftX to liftY; return
        }
        if (near(x, y, puckX, puckY, puckR * 1.25)) {
            grab = Grab.Puck; grabOx = puckX - x; grabOy = puckY - y; haptic?.invoke(); return
        }
        val d = hypot(x - cx, y - cy)
        val a = atan2(y - cy, x - cx)
        // the volume rim: grab it anywhere, the volume then moves by how far the finger travels — never to
        // where it touched, and a tap changes nothing
        if (showVolume && d > roomR * 1.0 && d < roomR * 1.5 && abs(a - Math.PI / 2) < VOL_HALF + 0.12) {
            grab = Grab.Volume
            volGrabFinger = volFrom(x, y); volGrabBase = volT; volEngaged = false
        }
    }

    fun move(x: Double, y: Double) {
        val g = grab ?: return
        touched = System.nanoTime()
        when (g) {
            is Grab.Puck -> {
                clampPuck(x + grabOx, y + grabOy)
                val was = snapped
                snapped = null
                if (snapToCell) {
                    var best: Ring? = null
                    var bd = snapFrac * roomR
                    for (rg in rings) {
                        val d = hypot(rg.x - puckTx, rg.y - puckTy)
                        if (d < bd) { bd = d; best = rg }
                    }
                    if (best != null) { puckTx = best.x; puckTy = best.y; snapped = best.serial }
                }
                if (snapped != null && snapped != was) haptic?.invoke()
                send(false)
            }
            is Grab.Lift -> {
                liftDot = x to y
                if (hypot(x - puckX, y - puckY) > liftFrac * fieldR) {
                    spatial = true
                    dir = angleOf(x, y); dirT = dir
                    halfT = defaultHalfDeg * Math.PI / 180
                    half = min(halfT, 0.35); halfV = 0.0
                    grab = Grab.Notch; liftDot = null
                    haptic?.invoke()
                    send(false)
                }
            }
            is Grab.Notch -> { dirT = angleOf(x, y); send(false) }
            is Grab.Dot -> {
                if (hypot(x - puckX, y - puckY) < collapseFrac * fieldR) {
                    spatial = false; grab = null; haptic?.invoke(); send(true); return
                }
                val h = abs(wrapPi(angleOf(x, y) - dirT))
                halfT = min(max(h, minHalfDeg * Math.PI / 180), maxHalfDeg * Math.PI / 180)
                send(false)
            }
            is Grab.Volume -> {
                val delta = volFrom(x, y) - volGrabFinger
                if (!volEngaged) {
                    if (abs(delta) <= 0.012) return            // a small dead zone, so a tap stays a tap
                    volEngaged = true; volGrabFinger = volFrom(x, y); haptic?.invoke()
                    return
                }
                val v = min(max(volGrabBase + delta, 0.0), 1.0)
                if ((v * 20).toInt() != (volT * 20).toInt()) haptic?.invoke()
                volT = v; sendVolume(false)
            }
        }
    }

    fun end() {
        val g = grab ?: return
        grab = null; liftDot = null
        if (g is Grab.Volume) { if (volEngaged) sendVolume(true) } else if (g !is Grab.Lift) send(true)
    }

    fun rotateBy(delta: Double) { if (spatial) { touched = System.nanoTime(); dirT += delta; send(false) } }
    fun widenBy(factor: Double) {
        if (!spatial) return
        touched = System.nanoTime()
        halfT = min(max(halfT * factor, minHalfDeg * Math.PI / 180), maxHalfDeg * Math.PI / 180)
        send(false)
    }
    fun centrePuck() { touched = System.nanoTime(); puckTx = cx; puckTy = cy; snapped = null; haptic?.invoke(); send(true) }
    fun setSpatialOn(on: Boolean) {
        touched = System.nanoTime()
        spatial = on
        if (on) {
            if (halfT < 0.2) halfT = defaultHalfDeg * Math.PI / 180
            if (half < 0.05) half = min(halfT, 0.35)
        }
        haptic?.invoke(); send(true)
    }

    private fun near(x: Double, y: Double, px: Double, py: Double, r: Double) = hypot(x - px, y - py) <= r
    private fun clampPuck(x: Double, y: Double) {
        val dx = x - cx; val dy = y - cy
        val d = hypot(dx, dy); val m = roomR * 0.78
        if (d > m) { puckTx = cx + dx / d * m; puckTy = cy + dy / d * m } else { puckTx = x; puckTy = y }
    }
    private fun volFrom(x: Double, y: Double): Double {
        val a = atan2(y - cy, x - cx)
        val lo = Math.PI / 2 - VOL_HALF; val hi = Math.PI / 2 + VOL_HALF
        val aa = min(max(a, lo), hi)
        return (hi - aa) / (hi - lo)
    }

    // ---- the two endpoints ----
    private fun body(): Map<String, Any?> {
        val ux = (puckTx - cx) / roomR
        val uy = -(puckTy - cy) / roomR
        var rot = (90 - dirT * 180 / Math.PI) % 360
        if (rot < 0) rot += 360
        return mapOf("ux" to Math.round(ux * 1000) / 1000.0, "uy" to Math.round(uy * 1000) / 1000.0,
            "rot" to Math.round(rot * 10) / 10.0, "width" to Math.round(halfT * 180 / Math.PI * 10) / 10.0,
            "radius" to if (spatial) radiusUnits else 0.0, "spatial" to spatial, "snap" to (snapped ?: ""))
    }
    private fun send(final: Boolean) {
        val now = System.nanoTime()
        if (!final && (now - lastPost) / 1e9 < 0.11) return
        lastPost = now
        val b = body()
        scope.launch { try { agent?.post("/api/soundfield", b, 5) } catch (e: Exception) { error.value = e.message } }
    }
    private fun sendVolume(final: Boolean) {
        val now = System.nanoTime()
        if (!final && (now - lastPost) / 1e9 < 0.11) return
        lastPost = now
        val v = Math.round(volT * 1000) / 1000.0
        scope.launch { try { agent?.post("/api/volume", mapOf("source" to "all", "volume" to v), 5) } catch (_: Exception) {} }
    }

    // ---- live state ----
    fun start() {
        val a = agent ?: return
        if (running) return
        running = true
        scope.launch { streamViz() }
        scope.launch {
            while (isActive) {
                try { applyState(a.get("/api/state", 6)) } catch (e: Exception) { error.value = e.message }
                delay(3000)
            }
        }
    }
    fun stop() { running = false; job.children.forEach { it.cancel() } }

    /** The agent's SSE feed: one frame per audio packet (~30/s), straight off the primary. */
    private suspend fun streamViz() {
        val a = agent ?: return
        val client = Agent.http.newBuilder().readTimeout(0, TimeUnit.MILLISECONDS).build()
        while (running) {
            try {
                val req = Request.Builder().url(a.base + "/api/viz/stream").header("Accept", "text/event-stream").build()
                client.newCall(req).execute().use { resp ->
                    val src = resp.body?.source() ?: return@use
                    while (running && !src.exhausted()) {
                        val line = src.readUtf8Line() ?: break
                        if (!line.startsWith("data: ")) continue
                        try { ingest(JSONObject(line.substring(6))) } catch (_: Exception) {}
                    }
                }
            } catch (_: Exception) {
            }
            if (running) delay(1500)
        }
    }

    @Synchronized
    private fun ingest(v: JSONObject) {
        energy += (v.optDouble("energy", 0.0) - energy) * 0.35
        bass += (v.optDouble("bass", 0.0) - bass) * 0.4
        val sp = v.optJSONArray("spec")
        val n = sp?.length() ?: 0
        for (i in 0 until 24) {
            val t = if (n > 0) sp!!.optDouble(min(n - 1, i * n / 24), 0.0) else 0.0
            rawSpec[i] = t
            spec[i] += (t - spec[i]) * 0.5
        }
        live.value = v.optBoolean("live", false)
        v.optString("src").takeIf { it.isNotEmpty() && it != "null" }?.let { setSource(it) }
        ledPalette = if (followLed) {
            val led = v.optJSONArray("led")
            if (led != null && led.length() >= 3)
                FieldPalette.from(led.optDouble(0), led.optDouble(1), led.optDouble(2), if (led.length() > 3) led.optDouble(3) else 0.0)
            else null
        } else null

        val newSeq = if (v.has("seq")) v.optInt("seq") else null
        val isNew = if (newSeq == null) n > 0 else newSeq != seq
        if (!isNew) return
        if (newSeq != null) seq = newSeq
        val fx = v.optJSONArray("flux")
        val fn = fx?.length() ?: 0
        for (i in 0 until 24) {
            var f: Double
            if (fn > 0) f = fx!!.optDouble(min(fn - 1, i * fn / 24), 0.0)
            else {
                f = max(0.0, rawSpec[i] - emaSpec[i]) * 3.0
                emaSpec[i] += (rawSpec[i] - emaSpec[i]) * 0.12
            }
            flux[i] = min(1.0, f)
        }
        val fallback = min(1.0, (flux[1] + flux[2] + flux[3]) / 3 * 1.6)
        beat = max(beat * 0.8, if (v.has("beat")) v.optDouble("beat", fallback) else fallback)
        writePulseRow()
        lastRow = System.nanoTime()
    }

    /** One row of the spectrogram the particles ride: mirrored (bass at the notch), lightly jittered. */
    private fun writePulseRow() {
        val row = ByteArray(PULSE_W)
        for (c in 0 until PULSE_W) {
            val u = c.toDouble() / (PULSE_W - 1)
            val bi = abs(u * 2 - 1) * 23
            val i0 = bi.toInt(); val i1 = min(23, i0 + 1); val f = bi - i0
            val base = flux[i0] * (1 - f) + flux[i1] * f
            val j = 1 - pulseJitter * 0.5 + pulseJitter * Random.nextDouble()
            row[c] = (min(255.0, max(0.0, base * j * 255))).toInt().toByte()
        }
        synchronized(pulseQueue) {
            pulseQueue.addLast(row)
            while (pulseQueue.size > 6) pulseQueue.removeFirst()
        }
    }

    private fun applyState(d: JSONObject) {
        error.value = null
        val room = d.optJSONObject("room")
        room?.optJSONObject("reference")?.let { ref ->
            val r = ref.optDouble("radius_m", 0.0)
            if (r > 0.05) { refX = ref.optDouble("x_m", 0.0); refY = ref.optDouble("y_m", 0.0); refR = r; refRot = ref.optDouble("rotation_deg", 0.0) }
        }
        val fleet = d.optJSONArray("fleet")
        val cells = ArrayList<Triple<Pair<Double, Double>, Boolean, String>>()
        for (i in 0 until (fleet?.length() ?: 0)) {
            val c = fleet!!.optJSONObject(i) ?: continue
            val p = c.optJSONObject("position") ?: continue
            if (!p.has("x") || !p.has("y")) continue
            cells.add(Triple(p.optDouble("x") to p.optDouble("y"), !c.isNull("activeSource"), c.optString("serial", "")))
        }
        cellsM = cells
        val lis = room?.optJSONObject("listener")
        listenerM = if (lis != null && lis.has("x_m")) lis.optDouble("x_m") to lis.optDouble("y_m") else null
        d.optJSONObject("sources")?.let { src ->
            src.keys().forEach { k -> if (src.optJSONObject(k)?.optBoolean("active") == true) setSource(k) }
        }
        refreshRings()
        if ((System.nanoTime() - touched) / 1e9 < 2.5 || grab != null) return
        val f = room?.optJSONObject("field") ?: return
        d.optJSONObject("sources")?.let { src ->
            src.keys().forEach { k ->
                val s = src.optJSONObject(k)
                if (s?.optBoolean("active") == true) { volT = s.optDouble("volume", volT); volume = volT }
            }
        }
        val sp = f.optBoolean("spatial", false)
        if (sp != spatial) spatial = sp
        if (f.has("width")) {
            halfT = min(max(f.optDouble("width"), minHalfDeg), maxHalfDeg) * Math.PI / 180
            if (half < 0.05) half = halfT
        }
        if (f.has("rot")) {
            dirT = (90 - f.optDouble("rot")) * Math.PI / 180
            if (abs(wrapPi(dir - dirT)) > Math.PI * 0.9) dir = dirT
        }
        if (f.has("ux") && f.has("uy")) {
            val x = cx + f.optDouble("ux") * roomR
            val y = cy - f.optDouble("uy") * roomR
            if (hypot(x - puckTx, y - puckTy) > 0.5) clampPuck(x, y)
        }
        snapped = f.optString("snap").takeIf { it.isNotEmpty() }
    }

    private fun toUnits(xm: Double, ym: Double): Pair<Double, Double> {
        val dx = (xm - refX) / refR; val dy = (ym - refY) / refR
        val a = -refRot * Math.PI / 180
        return (dx * cos(a) - dy * sin(a)) to (dx * sin(a) + dy * cos(a))
    }
    private fun refreshRings() {
        val edge = roomR - roomR * 0.085 * 0.3
        rings = cellsM.mapNotNull { (pos, active, serial) ->
            val u = toUnits(pos.first, pos.second)
            val x = cx + u.first * roomR; val y = cy - u.second * roomR
            if (hypot(x - cx, y - cy) > edge) null else Ring(x, y, active, serial)
        }
        listener = listenerM?.let { val u = toUnits(it.first, it.second); (cx + u.first * roomR) to (cy - u.second * roomR) }
    }

    var source = "airplay"
        private set
    private fun setSource(id: String) {
        val s = id.lowercase()
        val name = when {
            s.startsWith("spotify") -> "spotify"
            s.startsWith("tidal") -> "tidal"
            s.contains("hdmi") -> "hdmi"
            s.contains("pd") || s.contains("pure") -> "puredata"
            s.contains("usb") -> "usb"
            else -> "airplay"
        }
        if (name == source) return
        source = name
        sourcePalette = FieldPalette.named(name)
        sourceTitle.value = when (name) {
            "spotify" -> "Spotify"
            "tidal" -> "TIDAL"
            "hdmi" -> "HDMI"
            "usb" -> "USB"
            "puredata" -> "Pure Data"
            else -> "AirPlay"
        }
    }

    companion object {
        const val PULSE_W = 64
        const val PULSE_ROWS = 128
        const val TAU = Math.PI * 2
        const val VOL_HALF = 52.0 * Math.PI / 180
        private const val K = 218.566
        private const val CD = 29.568
    }
}

/** The eight colours the field is painted with (the app's Auras_* histograms, or the live LED colour). */
class FieldPalette(
    val deep: FloatArray, val cyan: FloatArray, val light: FloatArray, val white: FloatArray,
    val blue: FloatArray, val teal: FloatArray, val room: FloatArray, val roomEdge: FloatArray
) {
    fun blend(t: FieldPalette, k: Double) {
        val f = min(max(k, 0.0), 1.0).toFloat()
        for (p in arrayOf(deep to t.deep, cyan to t.cyan, light to t.light, white to t.white,
                          blue to t.blue, teal to t.teal, room to t.room, roomEdge to t.roomEdge)) {
            for (i in 0 until 3) p.first[i] += (p.second[i] - p.first[i]) * f
        }
    }
    companion object {
        private fun c(r: Double, g: Double, b: Double) = floatArrayOf(r.toFloat(), g.toFloat(), b.toFloat(), 1f)
        fun airplay() = FieldPalette(c(0.0,0.36,0.54), c(0.14,0.64,0.84), c(0.54,0.84,0.95), c(0.90,0.98,1.0),
            c(0.10,0.42,0.86), c(0.08,0.60,0.70), c(0.10,0.26,0.33), c(0.07,0.19,0.23))
        fun named(n: String): FieldPalette = when (n) {
            "spotify" -> FieldPalette(c(0.0,0.30,0.18), c(0.13,0.56,0.33), c(0.55,0.86,0.66), c(0.92,1.0,0.95),
                c(0.08,0.46,0.30), c(0.12,0.62,0.40), c(0.08,0.22,0.15), c(0.06,0.16,0.11))
            "tidal" -> FieldPalette(c(0.0,0.33,0.33), c(0.10,0.58,0.58), c(0.55,0.86,0.86), c(0.90,1.0,1.0),
                c(0.05,0.45,0.50), c(0.08,0.60,0.58), c(0.08,0.24,0.24), c(0.06,0.17,0.17))
            "hdmi" -> FieldPalette(c(0.10,0.08,0.40), c(0.30,0.28,0.80), c(0.65,0.62,0.95), c(0.95,0.93,1.0),
                c(0.20,0.20,0.75), c(0.35,0.25,0.70), c(0.12,0.10,0.30), c(0.08,0.07,0.22))
            "puredata" -> FieldPalette(c(0.45,0.17,0.08), c(0.93,0.48,0.33), c(1.0,0.78,0.62), c(1.0,0.96,0.92),
                c(0.85,0.35,0.20), c(0.95,0.55,0.30), c(0.30,0.16,0.10), c(0.22,0.11,0.07))
            "usb" -> FieldPalette(c(0.25,0.25,0.26), c(0.55,0.55,0.56), c(0.80,0.80,0.82), c(0.98,0.98,1.0),
                c(0.40,0.42,0.48), c(0.50,0.52,0.55), c(0.20,0.21,0.22), c(0.14,0.15,0.16))
            else -> airplay()
        }
        /** Which glyph the puck wears for a source (painted by the renderer, no assets). */
        fun glyphFor(source: String): String = when (source) {
            "spotify", "tidal" -> "note"
            "hdmi" -> "tv"
            "usb", "puredata" -> "wave"
            else -> "airplay"
        }
        /** A whole palette out of one live colour — what the speaker's LEDs are glowing (agent b52). */
        fun from(r: Double, g: Double, b: Double, w: Double): FieldPalette? {
            val mx = max(r, max(g, b)); val mn = min(r, min(g, b)); val d = mx - mn
            if (mx <= 0.03) return null
            var h = 0.0
            if (d > 1e-6) h = when (mx) {
                r -> ((g - b) / d + if (g < b) 6.0 else 0.0) / 6
                g -> ((b - r) / d + 2) / 6
                else -> ((r - g) / d + 4) / 6
            }
            var sat = (d / mx) * (1 - 0.5 * w)
            if (sat <= 0.12) return null
            sat = min(max(sat, 0.45), 1.0)
            fun p(sm: Double, v: Double, dh: Double = 0.0): FloatArray {
                val hh = (((h + dh) % 1) + 1) % 1
                val i = (hh * 6).toInt() % 6
                val f = hh * 6 - floor(hh * 6)
                val s = min(max(sat * sm, 0.0), 1.0)
                val pp = v * (1 - s); val q = v * (1 - s * f); val t = v * (1 - s * (1 - f))
                return when (i) {
                    0 -> c(v, t, pp)
                    1 -> c(q, v, pp)
                    2 -> c(pp, v, t)
                    3 -> c(pp, q, v)
                    4 -> c(t, pp, v)
                    else -> c(v, pp, q)
                }
            }
            return FieldPalette(p(1.00, 0.54), p(0.83, 0.84), p(0.43, 0.95), p(0.10, 1.00),
                p(0.88, 0.86, -0.045), p(0.89, 0.70, 0.035), p(0.70, 0.33), p(0.70, 0.23))
        }
    }
}
