package space.troubadour.app.field

import android.app.ActivityManager
import android.content.Context
import android.opengl.GLSurfaceView
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import kotlin.math.atan2

/** ES 3.1 is what the compute shader needs; everything since about 2015 has it. */
fun supportsCompute(ctx: Context): Boolean {
    val am = ctx.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager ?: return false
    return am.deviceConfigurationInfo.reqGlEsVersion >= 0x30001
}

/**
 * The Metal view's counterpart: touches go straight to the model (queued onto the GL thread, which
 * is where the model lives), so a drag tracks the finger with no recogniser delay. Pinch sets the
 * width, two-finger twist aims, double-tap centres.
 */
class FieldSurfaceView(ctx: Context, val model: FieldModel) : GLSurfaceView(ctx) {
    private val renderer: FieldRenderer
    private var pointerId = -1
    private var twoFinger = false
    private var lastAngle = 0.0

    private val scale = ScaleGestureDetector(ctx, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
        override fun onScale(d: ScaleGestureDetector): Boolean {
            val f = d.scaleFactor.toDouble()
            queueEvent { model.widenBy(f) }
            return true
        }
    })
    private val taps = GestureDetector(ctx, object : GestureDetector.SimpleOnGestureListener() {
        override fun onDoubleTap(e: MotionEvent): Boolean {
            queueEvent { model.centrePuck() }
            return true
        }
    })

    init {
        setEGLContextClientVersion(3)
        renderer = FieldRenderer(model, resources.displayMetrics.density)
        setRenderer(renderer)
        renderMode = RENDERMODE_CONTINUOUSLY
        isClickable = true
        model.haptic = {
            post { performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP) }
        }
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        scale.onTouchEvent(e)
        taps.onTouchEvent(e)
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                pointerId = e.getPointerId(0)
                val x = e.x.toDouble(); val y = e.y.toDouble()
                queueEvent { model.begin(x, y) }
            }
            MotionEvent.ACTION_POINTER_DOWN -> {
                if (e.pointerCount >= 2) {
                    twoFinger = true
                    lastAngle = angle(e)
                    queueEvent { model.end() }
                }
            }
            MotionEvent.ACTION_MOVE -> {
                if (twoFinger && e.pointerCount >= 2) {
                    val a = angle(e)
                    var d = a - lastAngle
                    while (d > Math.PI) d -= 2 * Math.PI
                    while (d < -Math.PI) d += 2 * Math.PI
                    lastAngle = a
                    queueEvent { model.rotateBy(d) }
                } else if (!twoFinger) {
                    val i = e.findPointerIndex(pointerId)
                    if (i >= 0) {
                        val x = e.getX(i).toDouble(); val y = e.getY(i).toDouble()
                        queueEvent { model.move(x, y) }
                    }
                }
            }
            MotionEvent.ACTION_POINTER_UP -> {
                if (e.pointerCount <= 2) twoFinger = false
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                twoFinger = false
                pointerId = -1
                queueEvent { model.end() }
            }
        }
        return true
    }

    private fun angle(e: MotionEvent): Double =
        atan2((e.getY(1) - e.getY(0)).toDouble(), (e.getX(1) - e.getX(0)).toDouble())
}

@Composable
fun FieldCanvas(model: FieldModel, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    DisposableEffect(model) {
        model.start()
        onDispose { model.stop() }
    }
    AndroidView(
        modifier = modifier,
        factory = { FieldSurfaceView(ctx, model) },
        onRelease = { it.onPause() },
        update = { }
    )
}
