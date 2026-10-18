package space.troubadour.app.field

import org.json.JSONObject
import java.util.stream.IntStream
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * The sound field as one still picture, drawn on the CPU — the iOS widget's FieldStill.swift, ported.
 *
 * A home-screen widget can't host the GL view, so this is the same passes in Kotlin: the scene fragment line
 * for line, the particle kernel stepped until the cloud settles into the field's shape, the points splatted on
 * top, then the glass orbs (through the app's lens table) and the speaker rings. The model is the app's own
 * FieldModel, fed one /api/state and one /api/viz/frame, so the shape, the speakers and the LED colour are
 * what the app shows. Only the motion is missing.
 */
object FieldStill {

    class Glyph(val side: Int, val alpha: FloatArray)
    class Image(val width: Int, val height: Int, val argb: IntArray)

    /** A headless model for a picture [w] x [h] pixels: the room centred and as big as the halo allows. */
    fun model(state: JSONObject?, frame: JSONObject?, w: Int, h: Int, density: Double): FieldModel {
        val m = FieldModel(null)
        m.showVolume = false
        m.layout(w, h, density)
        m.cx = w / 2.0; m.cy = h / 2.0; m.roomR = min(w, h) * 0.40
        m.puckX = m.cx; m.puckY = m.cy; m.puckTx = m.cx; m.puckTy = m.cy
        state?.let { m.applyState(it) }
        frame?.let { f -> repeat(12) { m.ingest(f) } }    // the feed smooths; let it reach the frame
        m.settle()
        m.advance(1.0 / 30)
        m.settle()
        return m
    }

    fun render(m: FieldModel, glyph: Glyph? = null, settleSteps: Int = 90): Image {
        val W = max(1, m.width.toInt()); val H = max(1, m.height.toInt())
        val g = Geo(m)
        val scene = FloatArray(W * H * 3)
        IntStream.range(0, H).parallel().forEach { y ->
            for (x in 0 until W) g.scene(x + 0.5f, y + 0.5f, scene, (y * W + x) * 3)
        }
        splatParticles(m, g, scene, W, H, settleSteps)
        val out = IntArray(W * H)
        IntStream.range(0, H).parallel().forEach { y ->
            val c = FloatArray(3)
            for (x in 0 until W) {
                g.overlay(x + 0.5f, y + 0.5f, scene, W, H, glyph, c)
                val r = (clamp(c[0], 0f, 1f) * 255 + 0.5f).toInt()
                val gg = (clamp(c[1], 0f, 1f) * 255 + 0.5f).toInt()
                val b = (clamp(c[2], 0f, 1f) * 255 + 0.5f).toInt()
                out[y * W + x] = (0xFF shl 24) or (r shl 16) or (gg shl 8) or b
            }
        }
        return Image(W, H, out)
    }

    private fun clamp(x: Float, a: Float, b: Float) = min(max(x, a), b)
    private fun smooth(e0: Float, e1: Float, x: Float): Float { val t = clamp((x - e0) / (e1 - e0), 0f, 1f); return t * t * (3 - 2 * t) }
    private fun mixf(a: Float, b: Float, t: Float) = a + (b - a) * t
    private fun len2(x: Float, y: Float) = sqrt(x * x + y * y)

    /** Geometry + uniforms in pixels (the renderer's uniform blocks). */
    private class Geo(m: FieldModel) {
        val cx = m.cx.toFloat(); val cy = m.cy.toFloat()
        val px = m.puckX.toFloat(); val py = m.puckY.toFloat()
        val R = m.roomR.toFloat(); val Rf = m.fieldR.toFloat(); val puckR = m.puckR.toFloat(); val handleR = m.handleR.toFloat()
        val time = m.time.toFloat()
        val sdf = m.sdfParams()
        val energy = m.energy.toFloat(); val bass = m.bass.toFloat(); val spatialMix = (1 - m.omni).toFloat()
        val halo = m.haloGain.toFloat(); val glow = m.glow.toFloat(); val grain = m.grain.toFloat(); val flurry = m.flurryGain.toFloat()
        val cDeep = m.palette.deep.copyOf(); val cCyan = m.palette.cyan.copyOf(); val cWhite = m.palette.white.copyOf()
        val cBlue = m.palette.blue.copyOf(); val cTeal = m.palette.teal.copyOf(); val cRoom = m.palette.room.copyOf(); val cRoomEdge = m.palette.roomEdge.copyOf()
        val blobs = FloatArray(96).also { a -> m.blobs.forEachIndexed { i, b -> if (i < 24) { a[i * 4] = b.x.toFloat(); a[i * 4 + 1] = b.y.toFloat(); a[i * 4 + 2] = b.r.toFloat(); a[i * 4 + 3] = b.a.toFloat() } } }
        val pulseE = m.pulseE.copyOf(); val pulseP = m.pulseP.copyOf()
        val omni = m.omni.toFloat()
        val hAx = m.handleX(m.dir + m.half).toFloat(); val hAy = m.handleY(m.dir + m.half).toFloat()
        val hBx = m.handleX(m.dir - m.half).toFloat(); val hBy = m.handleY(m.dir - m.half).toFloat()
        val ntx = m.handleX(m.dir).toFloat(); val nty = m.handleY(m.dir).toFloat()
        val dir = m.dir.toFloat(); val ntW = m.notchHalfW.toFloat(); val ntL = m.notchHalfL.toFloat()
        val lift = !m.spatial; val liftX = m.liftX.toFloat(); val liftY = m.liftY.toFloat()
        val rings = if (m.showRings) m.rings.take(8).map { r ->
            floatArrayOf(r.x.toFloat(), r.y.toFloat(), if (r.active) 1f else 0f, if (m.snapped == r.serial && r.serial.isNotEmpty()) 1f else 0f)
        } else emptyList()
        val listener = m.listener?.let { floatArrayOf(it.first.toFloat(), it.second.toFloat()) }

        /** The shared capped-torus SDF, exactly as the shaders have it. */
        fun sdf(x: Float, y: Float, z: Float): Float {
            val nx = sdf[0]; val ny = sdf[1]; val sinA = sdf[2]; val cosA = sdf[3]
            val ra = sdf[4]; val rb = sdf[5]; val capR = sdf[6]; val blend = sdf[7]
            val yr = x * nx + y * ny
            val xr = abs(x * (-ny) + y * nx)
            val k = if (cosA * xr > sinA * yr) xr * sinA + yr * cosA else sqrt(xr * xr + yr * yr)
            val k2 = sqrt(xr * xr + yr * yr)
            val pp = xr * xr + yr * yr + z * z
            val d1 = sqrt(max(0f, pp + ra * ra - 2 * ra * k)) - rb
            val d2 = sqrt(max(0f, pp + ra * ra - 2 * ra * k2)) - rb
            val dS = sqrt((x - sdf[8]) * (x - sdf[8]) + (y - sdf[9]) * (y - sdf[9]) + z * z) - capR
            val dE = sqrt((x - sdf[10]) * (x - sdf[10]) + (y - sdf[11]) * (y - sdf[11]) + z * z) - capR
            val h = clamp(0.5f * (dE - dS) / blend + 0.5f, 0f, 1f)
            val sm = mixf(dE, dS, h) - blend * h * (1 - h)
            return max(min(sm, d1), d2)
        }

        /** sceneFragment, writing rgb at out[o..o+2]. */
        fun scene(x: Float, y: Float, out: FloatArray, o: Int) {
            val Rr = max(R, 1f); val Rff = max(Rf, 1f); val pR = max(puckR, 1f)
            val dC = len2(x - cx, y - cy)
            val rr = dC / Rr
            val inRoom = 1 - smooth(Rr - 1, Rr + 1, dC)
            val fx = (x - px) / (2 * Rff); val fy = (y - py) / (2 * Rff)
            val d = sdf(fx, fy, 0f)
            val fr = len2(fx, fy)
            val px2f = 1 / (2 * Rff)

            val t0 = smooth(0.35f, 1.0f, rr)
            val shade = 1 - 0.28f * smooth(-0.2f, 1.0f, (y - cy) / Rr)
            var cr = mixf(cRoom[0], cRoomEdge[0], t0) * shade
            var cg = mixf(cRoom[1], cRoomEdge[1], t0) * shade
            var cb = mixf(cRoom[2], cRoomEdge[2], t0) * shade

            val inside = 1 - smooth(-6 * px2f, 2 * px2f, d)
            val spill = exp(-max(d, 0f) * 16)
            val core = exp(-fr * 7)
            var g = inside * (0.11f + 0.12f * exp(-fr * 3)) + spill * 0.12f * (1 - inside)
            g += exp(-abs(d) * 30) * 0.16f * inside
            g *= 0.80f + 0.45f * energy
            g += core * 0.22f * (1 + 0.5f * bass)

            var fl = 0f; var lr = 0f; var lg = 0f; var lb = 0f
            for (i in 0 until 24) {
                val bw = blobs[i * 4 + 3]
                if (bw <= 0f) continue
                val q = len2(fx - blobs[i * 4], fy - blobs[i * 4 + 1]) / max(blobs[i * 4 + 2], 0.001f)
                val w = bw * (1 - smooth(0.1f, 1.0f, q))
                fl += w
                val c = if (i % 2 == 0) cBlue else cTeal
                lr += c[0] * w; lg += c[1] * w; lb += c[2] * w
            }
            g += fl * 0.10f * flurry * inside
            g *= glow

            val a1 = clamp(g * 2, 0f, 1f); val a2 = clamp((g - 0.5f) * 2, 0f, 1f)
            val ar = mixf(mixf(cDeep[0], cCyan[0], a1), cWhite[0], a2)
            val ag = mixf(mixf(cDeep[1], cCyan[1], a1), cWhite[1], a2)
            val ab = mixf(mixf(cDeep[2], cCyan[2], a1), cWhite[2], a2)
            var gc = inside * (0.55f + 0.30f * exp(-fr * 2.2f)) + spill * 0.25f * (1 - inside)
            gc *= 0.85f + 0.3f * energy
            val c1 = clamp((gc - 0.55f) * 2.2f, 0f, 1f)
            var kr = mixf(cBlue[0], mixf(cCyan[0], cWhite[0], 0.35f), c1)
            var kg = mixf(cBlue[1], mixf(cCyan[1], cWhite[1], 0.35f), c1)
            var kb = mixf(cBlue[2], mixf(cCyan[2], cWhite[2], 0.35f), c1)
            val fd = max(fl, 0.001f); val ft = clamp(fl * 0.35f * flurry, 0f, 0.45f)
            kr = mixf(kr, lr / fd, ft); kg = mixf(kg, lg / fd, ft); kb = mixf(kb, lb / fd, ft)
            val cw = core * 0.6f * (1 + 0.4f * bass)
            kr += cWhite[0] * cw; kg += cWhite[1] * cw; kb += cWhite[2] * cw

            val sS = smooth(0f, 0.55f, g); val sC = smooth(0f, 0.5f, gc)
            val sr = mixf(cr, ar, sS); val sg = mixf(cg, ag, sS); val sb = mixf(cb, ab, sS)
            var qr = mixf(cr, kr, sC); var qg = mixf(cg, kg, sC); var qb = mixf(cb, kb, sC)
            val fk = 0.12f * flurry * inside
            qr += lr * fk; qg += lg * fk; qb += lb * fk
            cr = mixf(qr, sr, spatialMix); cg = mixf(qg, sg, spatialMix); cb = mixf(qb, sb, spatialMix)

            var acc = 0f
            for (k in 0 until 6) {
                val ex = pulseE[k * 4]; val ey = pulseE[k * 4 + 1]; val ez = pulseE[k * 4 + 2]; val ew = pulseE[k * 4 + 3]
                val dx = x - (px + ex * pR); val dy = y - (py + ey * pR)
                val dd = len2(dx, dy) / pR
                val th = atan2(dy, dx)
                val rad = ez * (1 + pulseP[k * 4 + 2] * cos(2 * th + pulseP[k * 4]) + pulseP[k * 4 + 3] * cos(3 * th + pulseP[k * 4 + 1]))
                val cov = 1 - smooth(rad - 0.22f, rad + 0.22f, dd)
                acc += cov * ew * (1 - acc)
            }
            acc *= halo * mixf(0.75f, 1.0f, spatialMix)
            val at = clamp(acc, 0f, 0.97f)
            cr = mixf(cr, mixf(cWhite[0], 1f, 0.5f), at); cg = mixf(cg, mixf(cWhite[1], 1f, 0.5f), at); cb = mixf(cb, mixf(cWhite[2], 1f, 0.5f), at)

            val outer = exp(-max(dC - Rr, 0f) / (0.05f * Rr)) * (1 - inRoom) * smooth(0f, 0.4f, g) * 0.35f
            cr += cCyan[0] * outer; cg += cCyan[1] * outer; cb += cCyan[2] * outer

            val n = grainNoise(x / 96 + time * 0.013f, y / 96 - time * 0.009f)
            val gk = 1 + (n - 0.5f) * 0.22f * grain
            cr *= gk; cg *= gk; cb *= gk

            val rim = smooth(0.90f, 0.985f, rr) * (1 - smooth(0.985f, 1.0f, rr))
            cr += 0.10f * rim; cg += 0.22f * rim; cb += 0.28f * rim
            val out1 = 1 - inRoom
            out[o] = mixf(cr, cCyan[0] * outer, out1); out[o + 1] = mixf(cg, cCyan[1] * outer, out1); out[o + 2] = mixf(cb, cCyan[2] * outer, out1)
        }

        /** The 128x128 white-noise texture, linear + repeat (a hash lattice here). */
        private fun grainNoise(u: Float, v: Float): Float {
            val x = u * 128 - 0.5f; val y = v * 128 - 0.5f
            val x0 = floor(x); val y0 = floor(y); val fx = x - x0; val fy = y - y0
            fun h(i: Float, j: Float): Float {
                val ii = i.toInt() and 127; val jj = j.toInt() and 127
                var q = (ii * 73856093) xor (jj * 19349663)
                q = (q xor (q ushr 13)) * 0x5bd1e995; q = q xor (q ushr 15)
                return (q and 0xffff) / 65535f
            }
            return mixf(mixf(h(x0, y0), h(x0 + 1, y0), fx), mixf(h(x0, y0 + 1), h(x0 + 1, y0 + 1), fx), fy)
        }

        // ---- overlay ----

        private fun lensAt(r: Float): Float {
            val x = clamp(r, 0f, 1f) * 63; val i = x.toInt(); val j = min(63, i + 1)
            return mixf(LENS[i], LENS[j], x - i)
        }
        private fun sample(s: FloatArray, W: Int, H: Int, x0: Float, y0: Float, out: FloatArray) {
            val x = clamp(x0 - 0.5f, 0f, (W - 1).toFloat()); val y = clamp(y0 - 0.5f, 0f, (H - 1).toFloat())
            val ix = x.toInt(); val iy = y.toInt(); val jx = min(W - 1, ix + 1); val jy = min(H - 1, iy + 1)
            val fx = x - ix; val fy = y - iy
            for (c in 0 until 3) {
                val a = mixf(s[(iy * W + ix) * 3 + c], s[(iy * W + jx) * 3 + c], fx)
                val b = mixf(s[(jy * W + ix) * 3 + c], s[(jy * W + jx) * 3 + c], fx)
                out[c] = mixf(a, b, fy)
            }
        }
        private fun glyphAlpha(g: Glyph, u: Float, v: Float): Float {
            val x = clamp(u * g.side - 0.5f, 0f, (g.side - 1).toFloat()); val y = clamp(v * g.side - 0.5f, 0f, (g.side - 1).toFloat())
            val ix = x.toInt(); val iy = y.toInt(); val jx = min(g.side - 1, ix + 1); val jy = min(g.side - 1, iy + 1)
            val fx = x - ix; val fy = y - iy
            return mixf(mixf(g.alpha[iy * g.side + ix], g.alpha[iy * g.side + jx], fx),
                        mixf(g.alpha[jy * g.side + ix], g.alpha[jy * g.side + jx], fx), fy)
        }

        /** glassOrb: mixes the orb into [col] where it covers (x, y). */
        private fun orb(x: Float, y: Float, ox: Float, oy: Float, radius: Float, halfLen: Float, rot: Float,
                        darken: Float, tint: Float, glyph: Glyph?, alphaMul: Float,
                        s: FloatArray, W: Int, H: Int, col: FloatArray, tmp: FloatArray) {
            val c = cos(-rot); val sn = sin(-rot)
            val dx = x - ox; val dy = y - oy
            val rr0 = max(radius, 0.5f)
            var lx = (dx * c - dy * sn) / rr0; var ly = (dx * sn + dy * c) / rr0
            var along = 0f
            if (halfLen > radius) { val ext = halfLen / radius - 1; along = clamp(ly, -ext, ext); ly -= along }
            val r = len2(lx, ly)
            val aa = 1 - smooth(1 - 2 / max(radius, 2f), 1f, r)
            if (aa <= 0.001f) return
            val dxn = if (r > 0.0001f) lx / r else 0f; val dyn = if (r > 0.0001f) ly / r else 0f
            val off = lensAt(r) * 2 - 0.5f
            val loX = dxn * off * radius; val loY = (along + dyn * off) * radius
            val cr = cos(rot); val sr = sin(rot)
            sample(s, W, H, ox + loX * cr - loY * sr, oy + loX * sr + loY * cr, tmp)
            var br = tmp[0]; var bg = tmp[1]; var bb = tmp[2]
            br = mixf(br, br * 0.30f + 0.02f, tint); bg = mixf(bg, bg * 0.62f + 0.16f, tint); bb = mixf(bb, bb * 1.0f + 0.38f, tint)
            val hx = lx + 0.34f; val hy = ly + 0.42f
            val spec = exp(-(hx * hx + hy * hy) * 5.5f)
            val dk = mixf(1f, 0.72f, darken * (1 - smooth(0.7f, 0.95f, r)))
            val sp = spec * (0.26f + 0.20f * darken)
            val rim = smooth(0.74f, 0.90f, r) * (1 - smooth(0.93f, 1.0f, r)) * (0.35f + 1.05f * darken)
            val edge = 1 - 0.18f * smooth(0.55f, 1.0f, r)
            var or = (br * dk + 0.86f * sp + 0.90f * rim) * edge
            var og = (bg * dk + 0.95f * sp + 0.98f * rim) * edge
            var ob = (bb * dk + 1.00f * sp + 1.00f * rim) * edge
            if (glyph != null) {
                val ga = glyphAlpha(glyph, 0.5f + 0.5f * lx * 1.30f, 0.5f + 0.5f * ly * 1.30f) * 0.95f
                or = mixf(or, 1f, ga); og = mixf(og, 1f, ga); ob = mixf(ob, 1f, ga)
            }
            val a = aa * alphaMul
            col[0] = mixf(col[0], or, a); col[1] = mixf(col[1], og, a); col[2] = mixf(col[2], ob, a)
        }
        private fun ringMask(x: Float, y: Float, rx: Float, ry: Float, radius: Float, w: Float): Float {
            val d = abs(len2(x - rx, y - ry) - radius)
            return 1 - smooth(w * 0.5f, w * 0.5f + 1.5f, d)
        }
        private fun mixInto(col: FloatArray, r: Float, g: Float, b: Float, t: Float) {
            col[0] = mixf(col[0], r, t); col[1] = mixf(col[1], g, t); col[2] = mixf(col[2], b, t)
        }

        /** overlayFragment (no volume rim on a widget). */
        fun overlay(x: Float, y: Float, s: FloatArray, W: Int, H: Int, glyph: Glyph?, col: FloatArray) {
            val i = (min(H - 1, y.toInt()) * W + min(W - 1, x.toInt())) * 3
            col[0] = s[i]; col[1] = s[i + 1]; col[2] = s[i + 2]
            val Rr = max(R, 1f); val scale = max(Rr / 138.6f, 0.5f)
            val tmp = FloatArray(3)
            val dsh = len2(x - px, y - py) / max(puckR * 2, 1f)
            mixInto(col, 0.02f, 0.08f, 0.14f, 0.40f * smooth(1.0f, 0.30f, dsh))
            if (omni < 0.999f) {
                val alpha = 1 - omni; val hr = max(handleR, 1f)
                orb(x, y, hAx, hAy, hr, 0f, 0f, 0.35f, 0f, null, alpha, s, W, H, col, tmp)
                orb(x, y, hBx, hBy, hr, 0f, 0f, 0.35f, 0f, null, alpha, s, W, H, col, tmp)
                orb(x, y, ntx, nty, ntW, ntL, dir, 0.40f, 0f, null, alpha, s, W, H, col, tmp)
            } else if (lift) {
                orb(x, y, liftX, liftY, max(handleR * 0.85f, 1f), 0f, 0f, 0.50f, 0f, null, 1f, s, W, H, col, tmp)
            }
            orb(x, y, px, py, puckR, 0f, 0f, 0.25f, 0.85f, glyph, 1f, s, W, H, col, tmp)
            val rr = Rr * 0.085f
            for (rg in rings) {
                if (len2(x - rg[0], y - rg[1]) > rr * 1.4f + 4) continue
                val snapped = rg[3]
                val rad = rr * mixf(1.0f, 1.18f, snapped)
                val a = ringMask(x, y, rg[0], rg[1], rad, mixf(2.0f, 2.6f, snapped) * scale)
                mixInto(col, 0.92f, 0.98f, 1.0f, a * mixf(mixf(0.16f, 0.26f, rg[2]), 0.75f, snapped))
                mixInto(col, 0.80f, 0.88f, 0.92f, ringMask(x, y, rg[0], rg[1], rr * 0.62f, 1.2f * scale) * 0.09f)
                mixInto(col, 0.78f, 0.88f, 0.92f, (1 - smooth(rad - 1, rad, len2(x - rg[0], y - rg[1]))) * 0.04f)
            }
            listener?.let { mixInto(col, 0.90f, 0.95f, 0.97f, ringMask(x, y, it[0], it[1], 4 * scale, 1.2f * scale) * 0.45f) }
        }
    }

    // ---- particles: the kernel on the CPU, then additive points ----

    private fun splatParticles(m: FieldModel, g: Geo, img: FloatArray, W: Int, H: Int, steps: Int) {
        val dens = m.density.toFloat()
        // as many points as keeps the app's density at this size (its 24k fill a ~414 px room on a 3x screen)
        val pts = (m.pointScale * max(0.7, m.roomR / m.density / 180)).toFloat()
        val sizeW = 0.9f * dens * pts; val sizeApp = 0.9f * 3 * max(0.7f, 138f / 180)
        val rel = (g.R / 414) * (g.R / 414) * (sizeApp / sizeW) * (sizeApp / sizeW)
        val count = clamp(24000 * rel, 1500f, 16000f).toInt()

        fun rand1(x: Double): Float { val v = sin(x) * 43758.5453; return (v - floor(v)).toFloat() }
        val pos = FloatArray(count * 3); val org = FloatArray(count * 3); val vel = FloatArray(count * 3)
        val life = FloatArray(count); val flt = FloatArray(count); val home = FloatArray(count); val rt = FloatArray(count)
        val seed = FloatArray(count)
        val rng = kotlin.random.Random(0x5eed)
        for (i in 0 until count) {
            val t = i * 0.125
            for (k in 0 until 3) { val v = rand1(t + k) - 0.5f; pos[i * 3 + k] = v; org[i * 3 + k] = v }
            life[i] = 10 + 90 * rand1(i * 0.37 + 1.3); seed[i] = rng.nextFloat()
        }
        val h = (1.0 / m.simHz).toFloat()
        val flux = FloatArray(24) { m.flux[it].toFloat() }
        val lowEdge = (1 - min(max(m.reactiveFrac, 0.05), 1.0)).toFloat()
        val gain = m.puffGain.toFloat(); val vmax = m.puffVmax.toFloat(); val float = m.puffFloat.toFloat(); val reach = m.puffReach.toFloat()
        val kick = (m.puffBeat * m.beat).toFloat()
        val nx = g.sdf[0]; val ny = g.sdf[1]
        val chunks = count / 512 + 1
        IntStream.range(0, chunks).parallel().forEach { ch ->
            val lo = ch * 512; val hi = min(count, lo + 512)
            for (step in 0 until steps) {
                val time = (m.time + step * h).toFloat()
                for (i in lo until hi) {
                    val o = i * 3
                    var x0 = pos[o]; var x1 = pos[o + 1]; var x2 = pos[o + 2]
                    var v0 = vel[o]; var v1 = vel[o + 1]; var v2 = vel[o + 2]
                    var lf = life[i] - h * 0.05f * 60
                    var fl = flt[i]; var hm = home[i]; var r = rt[i]
                    val sdr = seed[i] * 7.13f + 0.37f; val sd = sdr - floor(sdr)
                    val tt = clamp((sd - lowEdge) / 0.15f, 0f, 1f)
                    val react = 0.12f + 0.88f * tt * tt * (3 - 2 * tt)
                    val dist = len2(x0, x1)
                    if (dist > 0.02f) {
                        val ang = atan2(-x0 * ny + x1 * nx, x0 * nx + x1 * ny)
                        val ub = clamp((0.5f + 0.5f * ang / PI.toFloat()) * 24 - 0.5f, 0f, 23.999f)
                        val b0 = ub.toInt(); val b1 = min(23, b0 + 1); val tb = ub - b0
                        val f = 0.75f * (flux[b0] * (1 - tb) + flux[b1] * tb) + 0.25f * (flux[max(0, b0 - 1)] * (1 - tb) + flux[min(23, b1 + 1)] * tb)
                        val home0 = if (hm > 0.02f) hm else dist
                        val att = clamp(1 - (dist - home0) / max(0.05f, reach), 0f, 1f)
                        val imp = react * (gain * f + kick) * att
                        val vr = (v0 * x0 + v1 * x1) / dist
                        if (imp > 0.0002f && vr < vmax && dist < 0.6f) {
                            val add = min(imp, vmax - vr)
                            v0 += x0 / dist * add; v1 += x1 / dist * add
                            if (fl <= 0f && r <= 0f) hm = if (hm > 0.02f) hm + clamp(dist - hm, -0.02f, 0.02f) else dist
                            fl = max(fl, float * react); r = float * 1.5f
                        }
                    }
                    var n0: Float; var n1: Float; var n2: Float
                    if (lf > 0f) {
                        val eps = 0.001f
                        val p0 = x0 + v0; val p1 = x1 + v1; val p2 = x2 + v2
                        val d0 = g.sdf(p0, p1, p2)
                        var g0 = g.sdf(p0 + eps, p1, p2) - d0; var g1 = g.sdf(p0, p1 + eps, p2) - d0; var g2 = g.sdf(p0, p1, p2 + eps) - d0
                        val gl = sqrt(g0 * g0 + g1 * g1 + g2 * g2)
                        if (gl > 1e-9f) { g0 /= gl; g1 /= gl; g2 /= gl } else { g0 = 0f; g1 = 0f; g2 = 0f }
                        val ks = if (fl > 0.5f) 0.08f else if (fl > 0f) 0.08f + 0.92f * (1 - fl / 0.5f) else 1f
                        val k = -((if (d0 > 0f) 1.2f else 0.5f) * d0 * ks)
                        v0 += k * g0; v1 += k * g1; v2 += k * g2
                        n0 = x0 + k * g0 + v0; n1 = x1 + k * g1 + v1; n2 = x2 + k * g2 + v2
                    } else {
                        val sv = sin(lf + time * 0.01f)
                        n0 = org[o] * sv; n1 = org[o + 1] * sv; n2 = org[o + 2] * sv
                        lf = 100f
                    }
                    val l = sqrt(n0 * n0 + n1 * n1 + n2 * n2)
                    if (l > 1f) { n0 /= l; n1 /= l; n2 /= l }
                    if (r > 0f && hm > 0.02f) {
                        val d2 = len2(n0, n1)
                        if (d2 > 0.02f) { val gg = (hm - d2) * (if (fl > 0f) 0.012f else 0.04f); v0 += n0 / d2 * gg; v1 += n1 / d2 * gg }
                        if (fl <= 0f) r -= h
                    }
                    var damp = if (fl > 0f) 0.94f else 0.99f
                    if (fl > 0f && n0 * n0 + n1 * n1 > 0.42f) damp = 0.85f
                    pos[o] = n0; pos[o + 1] = n1; pos[o + 2] = n2
                    vel[o] = v0 * damp; vel[o + 1] = v1 * damp; vel[o + 2] = v2 * damp
                    life[i] = lf; flt[i] = if (fl > 0f) fl - h else 0f; home[i] = hm; rt[i] = r
                }
            }
        }
        // particleVertex / particleFragment: a cone of radius size/2, pale cyan, added on
        val gainP = m.pointGain.toFloat()
        for (i in 0 until count) {
            val sx = g.px + pos[i * 3] * 2 * g.Rf; val sy = g.py + pos[i * 3 + 1] * 2 * g.Rf
            val size = sizeW * (0.7f + 0.6f * seed[i])
            val e = min(PI.toFloat() / 3 * size * size / 4, 4f) * 0.75f * 0.5f * gainP
            val x = sx - 0.5f; val y = sy - 0.5f
            val ix = floor(x).toInt(); val iy = floor(y).toInt(); val fx = x - ix; val fy = y - iy
            val ws = floatArrayOf((1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy)
            for (k in 0 until 4) {
                val xx = ix + (k and 1); val yy = iy + (k shr 1)
                if (xx < 0 || yy < 0 || xx >= W || yy >= H) continue
                val o = (yy * W + xx) * 3; val w = ws[k] * e
                img[o] += 0.66f * w; img[o + 1] += 0.92f * w; img[o + 2] += 1.0f * w
            }
        }
    }

    /** Aurora Orb lens table (refraction offset by radius), from the decrypted app — same as the shader's. */
    private val LENS = floatArrayOf(
        0.251f,0.261f,0.271f,0.281f,0.291f,0.302f,0.312f,0.322f,0.332f,0.342f,0.352f,0.362f,0.372f,0.383f,0.393f,0.403f,
        0.413f,0.423f,0.433f,0.443f,0.453f,0.463f,0.472f,0.482f,0.492f,0.502f,0.512f,0.521f,0.531f,0.541f,0.550f,0.560f,
        0.569f,0.579f,0.588f,0.597f,0.606f,0.616f,0.625f,0.633f,0.642f,0.651f,0.659f,0.667f,0.676f,0.684f,0.691f,0.699f,
        0.706f,0.712f,0.719f,0.725f,0.730f,0.734f,0.738f,0.741f,0.742f,0.741f,0.738f,0.731f,0.719f,0.697f,0.657f,0.570f)
}
