package space.troubadour.app.field

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.opengl.GLES20
import android.opengl.GLES30
import android.opengl.GLES31
import android.opengl.GLSurfaceView
import android.opengl.GLUtils
import android.util.Log
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * The four passes, on the GL thread. The particle simulation runs in a compute shader at the app's
 * fixed 30 Hz and the frame interpolates between steps, so motion is smooth at the display's rate
 * without changing the physics.
 */
class FieldRenderer(val model: FieldModel, private val density: Float) : GLSurfaceView.Renderer {

    private var sceneProg = 0
    private var blitProg = 0
    private var pointProg = 0
    private var overlayProg = 0
    private var simProg = 0
    private val locs = HashMap<Int, HashMap<String, Int>>()

    private var vao = 0
    private var ssbo = 0
    private var noiseTex = 0
    private var pulseTex = 0
    private var glyphTex = 0
    private var glyphName = ""
    private var fboLow = 0; private var texLow = 0
    private var fboFull = 0; private var texFull = 0
    private var w = 1; private var h = 1
    private var lowW = 1; private var lowH = 1
    private var count = 0
    private var lastNanos = 0L
    private var simAcc = 0.0
    private val fluxF = FloatArray(24)
    private val blobF = FloatArray(24 * 4)
    private val ringF = FloatArray(8 * 4)

    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        sceneProg = link(FieldShaders.FULLSCREEN_VS, FieldShaders.SCENE_FS)
        blitProg = link(FieldShaders.FULLSCREEN_VS, FieldShaders.BLIT_FS)
        pointProg = link(FieldShaders.PART_VS, FieldShaders.PART_FS)
        overlayProg = link(FieldShaders.FULLSCREEN_VS, FieldShaders.OVERLAY_FS)
        simProg = linkCompute(FieldShaders.SIM_CS)

        val a = IntArray(1)
        GLES30.glGenVertexArrays(1, a, 0); vao = a[0]
        GLES30.glBindVertexArray(vao)

        noiseTex = makeNoise()
        pulseTex = makePulse()
        glyphTex = makeGlyph(FieldPalette.glyphFor(model.source))
        glyphName = model.source
        seedParticles()
        GLES20.glDisable(GLES20.GL_DEPTH_TEST)
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        w = max(1, width); h = max(1, height)
        model.layout(w, h, density.toDouble())
        lowW = max(1, (w * model.sceneScale).toInt())
        lowH = max(1, (h * model.sceneScale).toInt())
        val old = intArrayOf(fboLow, fboFull)
        if (old[0] != 0) GLES20.glDeleteFramebuffers(2, old, 0)
        val oldT = intArrayOf(texLow, texFull)
        if (oldT[0] != 0) GLES20.glDeleteTextures(2, oldT, 0)
        val low = makeTarget(lowW, lowH); fboLow = low[0]; texLow = low[1]
        val full = makeTarget(w, h); fboFull = full[0]; texFull = full[1]
    }

    override fun onDrawFrame(gl: GL10?) {
        val now = System.nanoTime()
        val dt = if (lastNanos == 0L) 1.0 / 60 else min(0.05, (now - lastNanos) / 1e9)
        lastNanos = now
        model.advance(dt)

        for ((row, data) in model.drainPulseRows()) uploadPulseRow(row, data)
        if (model.source != glyphName) {
            val t = intArrayOf(glyphTex)
            GLES20.glDeleteTextures(1, t, 0)
            glyphTex = makeGlyph(FieldPalette.glyphFor(model.source))
            glyphName = model.source
        }

        val sdf = model.sdfParams()

        // ---- 1. the simulation, at the app's fixed step ----
        val step = 1.0 / model.simHz
        simAcc += dt
        var steps = 0
        if (simAcc >= step) {
            GLES20.glUseProgram(simProg)
            GLES30.glBindBufferBase(GLES31.GL_SHADER_STORAGE_BUFFER, 0, ssbo)
            u4(simProg, "uAxis", sdf[0], sdf[1], sdf[2], sdf[3])
            u4(simProg, "uTorus", sdf[4], sdf[5], sdf[6], sdf[7])
            u4(simProg, "uCaps", sdf[8], sdf[9], sdf[10], sdf[11])
            u4(simProg, "uCfg2", model.puffGain.toFloat(), model.puffVmax.toFloat(), model.puffFloat.toFloat(), model.puffReach.toFloat())
            for (i in 0 until 24) fluxF[i] = model.flux[i].toFloat()
            GLES20.glUniform1fv(loc(simProg, "uFlux[0]"), 24, fluxF, 0)
            GLES20.glUniform1i(loc(simProg, "uCount"), count)
            val groups = ceil(count / 64.0).toInt()
            while (simAcc >= step && steps < 2) {
                u4(simProg, "uCfg", step.toFloat(), model.time.toFloat(),
                    (model.puffBeat * model.beat).toFloat(), (1 - min(max(model.reactiveFrac, 0.05), 1.0)).toFloat())
                GLES31.glDispatchCompute(groups, 1, 1)
                simAcc -= step
                steps++
            }
            GLES31.glMemoryBarrier(GLES31.GL_SHADER_STORAGE_BARRIER_BIT or GLES31.GL_VERTEX_ATTRIB_ARRAY_BARRIER_BIT)
            if (simAcc > step) simAcc = step * 0.99
        }
        val lerp = min(max(simAcc / step, 0.0), 1.0)

        // ---- 2. the scene, at a fraction of the resolution ----
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, fboLow)
        GLES20.glViewport(0, 0, lowW, lowH)
        GLES20.glDisable(GLES20.GL_BLEND)
        GLES20.glUseProgram(sceneProg)
        val ss = lowW.toDouble() / w
        common(sceneProg, ss, lowW.toDouble(), lowH.toDouble(), lerp, sdf)
        for (i in 0 until 24) {
            val b = model.blobs[i]
            blobF[i * 4] = b.x.toFloat(); blobF[i * 4 + 1] = b.y.toFloat()
            blobF[i * 4 + 2] = b.r.toFloat(); blobF[i * 4 + 3] = b.a.toFloat()
        }
        GLES20.glUniform4fv(loc(sceneProg, "uBlob[0]"), 24, blobF, 0)
        GLES20.glUniform4fv(loc(sceneProg, "uHaloE[0]"), 6, model.pulseE, 0)
        GLES20.glUniform4fv(loc(sceneProg, "uHaloP[0]"), 6, model.pulseP, 0)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, noiseTex)
        GLES20.glUniform1i(loc(sceneProg, "uNoise"), 0)
        GLES20.glDrawArrays(GLES20.GL_TRIANGLES, 0, 3)

        // ---- 3. upscale, then the particles on top ----
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, fboFull)
        GLES20.glViewport(0, 0, w, h)
        GLES20.glUseProgram(blitProg)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texLow)
        GLES20.glUniform1i(loc(blitProg, "uSrc"), 0)
        GLES20.glDrawArrays(GLES20.GL_TRIANGLES, 0, 3)

        GLES20.glUseProgram(pointProg)
        common(pointProg, 1.0, w.toDouble(), h.toDouble(), lerp, sdf)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, pulseTex)
        GLES20.glUniform1i(loc(pointProg, "uPulseTex"), 0)
        GLES20.glEnable(GLES20.GL_BLEND)
        GLES20.glBlendFunc(GLES20.GL_ONE, GLES20.GL_ONE)
        GLES20.glDrawArrays(GLES20.GL_POINTS, 0, count)
        GLES20.glDisable(GLES20.GL_BLEND)

        // ---- 4. the orbs, the rings and the volume rim, into the window ----
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0)
        GLES20.glViewport(0, 0, w, h)
        GLES20.glUseProgram(overlayProg)
        overlayUniforms()
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texFull)
        GLES20.glUniform1i(loc(overlayProg, "uScene"), 0)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE1)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, glyphTex)
        GLES20.glUniform1i(loc(overlayProg, "uGlyph"), 1)
        GLES20.glDrawArrays(GLES20.GL_TRIANGLES, 0, 3)
    }

    // ---- uniforms ----

    private fun common(p: Int, scale: Double, rw: Double, rh: Double, lerp: Double, sdf: FloatArray) {
        u4(p, "uRes", rw.toFloat(), rh.toFloat(), (model.cx * scale).toFloat(), (model.cy * scale).toFloat())
        u4(p, "uGeom", (model.roomR * scale).toFloat(), (model.fieldR * scale).toFloat(),
            (model.puckR * scale).toFloat(), model.time.toFloat())
        u4(p, "uPuck", (model.puckX * scale).toFloat(), (model.puckY * scale).toFloat(), 0f, 0f)
        u4(p, "uAxis", sdf[0], sdf[1], sdf[2], sdf[3])
        u4(p, "uTorus", sdf[4], sdf[5], sdf[6], sdf[7])
        u4(p, "uCaps", sdf[8], sdf[9], sdf[10], sdf[11])
        u4(p, "uEnv", model.energy.toFloat(), model.bass.toFloat(), (1 - model.omni).toFloat(), model.haloGain.toFloat())
        u4(p, "uGains", model.glow.toFloat(), model.grain.toFloat(), model.flurryGain.toFloat(), density)
        u4(p, "uPts", (model.pointScale * max(0.7, model.roomR / density / 180)).toFloat(),
            model.pointGain.toFloat(), lerp.toFloat(), (1 - min(max(model.reactiveFrac, 0.05), 1.0)).toFloat())
        u4(p, "uPulse", model.pulseHeadNorm.toFloat(), model.pulseScale.toFloat(),
            model.pulseEmitter.toFloat(), model.pulseGain.toFloat())
        val pal = model.palette
        u4v(p, "cDeep", pal.deep); u4v(p, "cCyan", pal.cyan); u4v(p, "cLight", pal.light); u4v(p, "cWhite", pal.white)
        u4v(p, "cBlue", pal.blue); u4v(p, "cTeal", pal.teal); u4v(p, "cRoom", pal.room); u4v(p, "cRoomEdge", pal.roomEdge)
    }

    private fun overlayUniforms() {
        val p = overlayProg
        u4(p, "uO0", w.toFloat(), h.toFloat(), model.cx.toFloat(), model.cy.toFloat())
        u4(p, "uO1", model.roomR.toFloat(), model.puckR.toFloat(), model.handleR.toFloat(), model.volume.toFloat())
        u4(p, "uO2", model.puckX.toFloat(), model.puckY.toFloat(), 1f, model.omni.toFloat())
        val g = model.grab
        val dotA = if (g is FieldModel.Grab.Dot && g.sgn > 0) 1f else 0f
        val dotB = if (g is FieldModel.Grab.Dot && g.sgn < 0) 1f else 0f
        u4(p, "uO3", model.handleX(model.dir + model.half).toFloat(), model.handleY(model.dir + model.half).toFloat(), dotA, 0f)
        u4(p, "uO4", model.handleX(model.dir - model.half).toFloat(), model.handleY(model.dir - model.half).toFloat(), dotB, 0f)
        u4(p, "uO5", model.handleX(model.dir).toFloat(), model.handleY(model.dir).toFloat(),
            model.dir.toFloat(), if (g is FieldModel.Grab.Notch) 1f else 0f)
        u4(p, "uO6", model.notchHalfW.toFloat(), model.notchHalfL.toFloat(),
            if (g is FieldModel.Grab.Lift) 1f else 0f, if (model.showVolume) 1f else 0f)
        val lift = model.liftDot
        u4(p, "uO7", (lift?.first ?: model.liftX).toFloat(), (lift?.second ?: model.liftY).toFloat(), 1f, 0f)
        val rings = model.rings
        val n = if (model.showRings) min(rings.size, 8) else 0
        for (i in 0 until n) {
            val r = rings[i]
            ringF[i * 4] = r.x.toFloat(); ringF[i * 4 + 1] = r.y.toFloat()
            ringF[i * 4 + 2] = if (r.active) 1f else 0f
            ringF[i * 4 + 3] = if (model.snapped != null && model.snapped == r.serial) 1f else 0f
        }
        GLES20.glUniform4fv(loc(p, "uRings[0]"), 8, ringF, 0)
        val l = model.listener
        u4(p, "uO8", n.toFloat(), (l?.first ?: 0.0).toFloat(), (l?.second ?: 0.0).toFloat(), if (l != null) 1f else 0f)
    }

    private fun loc(p: Int, name: String): Int =
        locs.getOrPut(p) { HashMap() }.getOrPut(name) { GLES20.glGetUniformLocation(p, name) }
    private fun u4(p: Int, name: String, a: Float, b: Float, c: Float, d: Float) {
        val l = loc(p, name); if (l >= 0) GLES20.glUniform4f(l, a, b, c, d)
    }
    private fun u4v(p: Int, name: String, v: FloatArray) {
        val l = loc(p, name); if (l >= 0) GLES20.glUniform4f(l, v[0], v[1], v[2], 1f)
    }

    // ---- resources ----

    /** Spawn exactly as the app's kernel does, so the starting cloud looks the same. */
    private fun seedParticles() {
        count = max(1000, model.particles)
        val data = FloatArray(count * 16)
        fun rand1(x: Double): Double { val s = sin(x) * 43758.5453; return s - floor(s) }
        val rnd = java.util.Random(11)
        for (i in 0 until count) {
            val t = i * 0.125
            val x = (rand1(t) - 0.5).toFloat()
            val y = (rand1(t + 1) - 0.5).toFloat()
            val z = (rand1(t + 2) - 0.5).toFloat()
            val life = (10 + 90 * rand1(i * 0.37 + 1.3)).toFloat()
            val o = i * 16
            data[o] = x; data[o + 1] = y; data[o + 2] = z; data[o + 3] = life
            data[o + 8] = x; data[o + 9] = y; data[o + 10] = z; data[o + 11] = 0f
            data[o + 12] = rnd.nextFloat(); data[o + 13] = 0f; data[o + 14] = x; data[o + 15] = y
        }
        val bb = ByteBuffer.allocateDirect(data.size * 4).order(ByteOrder.nativeOrder())
        val fb: FloatBuffer = bb.asFloatBuffer()
        fb.put(data); fb.position(0)
        val b = IntArray(1)
        GLES20.glGenBuffers(1, b, 0)
        ssbo = b[0]
        GLES20.glBindBuffer(GLES31.GL_SHADER_STORAGE_BUFFER, ssbo)
        GLES20.glBufferData(GLES31.GL_SHADER_STORAGE_BUFFER, data.size * 4, bb, GLES20.GL_DYNAMIC_DRAW)
        GLES30.glBindBufferBase(GLES31.GL_SHADER_STORAGE_BUFFER, 0, ssbo)
        // the same buffer, read as vertex attributes when the points are drawn (64 bytes per particle)
        GLES20.glBindBuffer(GLES20.GL_ARRAY_BUFFER, ssbo)
        GLES20.glVertexAttribPointer(0, 4, GLES20.GL_FLOAT, false, 64, 0)
        GLES20.glEnableVertexAttribArray(0)
        GLES20.glVertexAttribPointer(1, 4, GLES20.GL_FLOAT, false, 64, 48)
        GLES20.glEnableVertexAttribArray(1)
    }

    private fun makeTarget(tw: Int, th: Int): IntArray {
        val t = IntArray(1); GLES20.glGenTextures(1, t, 0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, t[0])
        GLES20.glTexImage2D(GLES20.GL_TEXTURE_2D, 0, GLES20.GL_RGBA, tw, th, 0, GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, null)
        texParams(GLES20.GL_CLAMP_TO_EDGE, GLES20.GL_CLAMP_TO_EDGE)
        val f = IntArray(1); GLES20.glGenFramebuffers(1, f, 0)
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, f[0])
        GLES20.glFramebufferTexture2D(GLES20.GL_FRAMEBUFFER, GLES20.GL_COLOR_ATTACHMENT0, GLES20.GL_TEXTURE_2D, t[0], 0)
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0)
        return intArrayOf(f[0], t[0])
    }

    private fun texParams(wrapS: Int, wrapT: Int) {
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, wrapS)
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, wrapT)
    }

    private fun makeNoise(): Int {
        val side = 128
        val buf = ByteBuffer.allocateDirect(side * side).order(ByteOrder.nativeOrder())
        val rnd = java.util.Random(3)
        for (i in 0 until side * side) buf.put(rnd.nextInt(256).toByte())
        buf.position(0)
        val t = IntArray(1); GLES20.glGenTextures(1, t, 0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, t[0])
        GLES20.glTexImage2D(GLES20.GL_TEXTURE_2D, 0, GLES30.GL_R8, side, side, 0, GLES30.GL_RED, GLES20.GL_UNSIGNED_BYTE, buf)
        texParams(GLES20.GL_REPEAT, GLES20.GL_REPEAT)
        return t[0]
    }

    private fun makePulse(): Int {
        val buf = ByteBuffer.allocateDirect(FieldModel.PULSE_W * FieldModel.PULSE_ROWS).order(ByteOrder.nativeOrder())
        buf.position(0)
        val t = IntArray(1); GLES20.glGenTextures(1, t, 0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, t[0])
        GLES20.glTexImage2D(GLES20.GL_TEXTURE_2D, 0, GLES30.GL_R8, FieldModel.PULSE_W, FieldModel.PULSE_ROWS, 0,
            GLES30.GL_RED, GLES20.GL_UNSIGNED_BYTE, buf)
        texParams(GLES20.GL_CLAMP_TO_EDGE, GLES20.GL_REPEAT)
        return t[0]
    }

    private fun uploadPulseRow(row: Int, data: ByteArray) {
        val buf = ByteBuffer.allocateDirect(data.size).order(ByteOrder.nativeOrder())
        buf.put(data); buf.position(0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, pulseTex)
        GLES20.glPixelStorei(GLES20.GL_UNPACK_ALIGNMENT, 1)
        GLES20.glTexSubImage2D(GLES20.GL_TEXTURE_2D, 0, 0, row, FieldModel.PULSE_W, 1,
            GLES30.GL_RED, GLES20.GL_UNSIGNED_BYTE, buf)
    }

    /** The source's glyph on the puck, painted rather than shipped as an asset. */
    private fun makeGlyph(kind: String): Int {
        val side = 192
        val bmp = Bitmap.createBitmap(side, side, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        val p = Paint(Paint.ANTI_ALIAS_FLAG)
        p.color = 0xFFFFFFFF.toInt()
        p.style = Paint.Style.STROKE
        p.strokeWidth = side * 0.055f
        p.strokeCap = Paint.Cap.ROUND
        val cx = side / 2f
        when (kind) {
            "note" -> {
                p.style = Paint.Style.FILL
                c.drawOval(RectF(side * 0.26f, side * 0.56f, side * 0.50f, side * 0.74f), p)
                p.style = Paint.Style.STROKE
                c.drawLine(side * 0.50f, side * 0.65f, side * 0.50f, side * 0.26f, p)
                c.drawLine(side * 0.50f, side * 0.26f, side * 0.74f, side * 0.34f, p)
            }
            "tv" -> {
                c.drawRoundRect(RectF(side * 0.22f, side * 0.28f, side * 0.78f, side * 0.62f), side * 0.06f, side * 0.06f, p)
                c.drawLine(side * 0.38f, side * 0.74f, side * 0.62f, side * 0.74f, p)
                c.drawLine(cx, side * 0.62f, cx, side * 0.74f, p)
            }
            "wave" -> {
                val path = Path()
                path.moveTo(side * 0.18f, cx)
                var x = side * 0.18f
                var up = true
                while (x < side * 0.82f) {
                    val nx = x + side * 0.16f
                    path.quadTo((x + nx) / 2, if (up) side * 0.26f else side * 0.74f, nx, cx)
                    x = nx; up = !up
                }
                c.drawPath(path, p)
            }
            else -> {   // AirPlay: a triangle under two arcs
                for (i in 0 until 2) {
                    val r = side * (0.20f + 0.11f * i)
                    c.drawArc(RectF(cx - r, side * 0.40f - r, cx + r, side * 0.40f + r), -150f, 120f, false, p)
                }
                p.style = Paint.Style.FILL
                val path = Path()
                path.moveTo(cx, side * 0.50f)
                path.lineTo(side * 0.70f, side * 0.80f)
                path.lineTo(side * 0.30f, side * 0.80f)
                path.close()
                c.drawPath(path, p)
            }
        }
        val t = IntArray(1); GLES20.glGenTextures(1, t, 0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, t[0])
        GLUtils.texImage2D(GLES20.GL_TEXTURE_2D, 0, bmp, 0)
        texParams(GLES20.GL_CLAMP_TO_EDGE, GLES20.GL_CLAMP_TO_EDGE)
        bmp.recycle()
        return t[0]
    }

    // ---- shader plumbing ----

    private fun compile(type: Int, src: String): Int {
        val s = GLES20.glCreateShader(type)
        GLES20.glShaderSource(s, src)
        GLES20.glCompileShader(s)
        val ok = IntArray(1)
        GLES20.glGetShaderiv(s, GLES20.GL_COMPILE_STATUS, ok, 0)
        if (ok[0] == 0) {
            Log.e("Field", "shader: " + GLES20.glGetShaderInfoLog(s))
            GLES20.glDeleteShader(s)
            return 0
        }
        return s
    }
    private fun link(vs: String, fs: String): Int {
        val p = GLES20.glCreateProgram()
        GLES20.glAttachShader(p, compile(GLES20.GL_VERTEX_SHADER, vs))
        GLES20.glAttachShader(p, compile(GLES20.GL_FRAGMENT_SHADER, fs))
        GLES20.glLinkProgram(p)
        val ok = IntArray(1)
        GLES20.glGetProgramiv(p, GLES20.GL_LINK_STATUS, ok, 0)
        if (ok[0] == 0) Log.e("Field", "link: " + GLES20.glGetProgramInfoLog(p))
        return p
    }
    private fun linkCompute(cs: String): Int {
        val p = GLES20.glCreateProgram()
        GLES20.glAttachShader(p, compile(GLES31.GL_COMPUTE_SHADER, cs))
        GLES20.glLinkProgram(p)
        val ok = IntArray(1)
        GLES20.glGetProgramiv(p, GLES20.GL_LINK_STATUS, ok, 0)
        if (ok[0] == 0) Log.e("Field", "link(compute): " + GLES20.glGetProgramInfoLog(p))
        return p
    }
}
