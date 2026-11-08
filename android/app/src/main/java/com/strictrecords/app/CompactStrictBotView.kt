package com.strictrecords.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.os.SystemClock
import android.util.AttributeSet
import android.view.View
import kotlin.math.sin

/**
 * Compact, expressive crowned StrictRecords robot head view for the floating countdown overlay.
 *
 * Implements:
 * - High-resolution crowned robot head render
 * - Gentle idle breathing movement (subtle vertical bobbing & scale)
 * - Eye blinking every ~4 seconds
 * - Dynamic visor eye glow matching urgency state (Cyan -> Amber -> Red)
 * - Alert eye expression during critical & completed states
 * - Low-resource: pauses animation when invisible or detached
 */
class CompactStrictBotView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    enum class UrgencyState {
        NORMAL,     // Cyan (#00D2FF) - calm expression
        WARNING,    // Amber (#FF9500) - concerned expression
        CRITICAL,   // Red (#FF3B30) - alert expression
        COMPLETED   // Red (#FF3B30) - alert expression
    }

    private var headBitmap: Bitmap? = null
    private val srcRect = Rect()
    private val dstRect = RectF()

    private var urgencyState = UrgencyState.NORMAL
    private var isAnimating = false
    private var lastFrameTime = 0L

    // Paints
    private val bitmapPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val eyeGlowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val eyeCorePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    init {
        loadBitmap()
    }

    private fun loadBitmap() {
        try {
            val bm = BitmapFactory.decodeResource(resources, R.drawable.strictbot_head)
            if (bm != null) {
                headBitmap = bm
                srcRect.set(0, 0, bm.width, bm.height)
            }
        } catch (_: Exception) {}
    }

    fun setUrgencyState(state: UrgencyState) {
        if (urgencyState != state) {
            urgencyState = state
            invalidate()
        }
    }

    fun startAnimation() {
        if (!isAnimating) {
            isAnimating = true
            lastFrameTime = SystemClock.uptimeMillis()
            post(animatorRunnable)
        }
    }

    fun stopAnimation() {
        isAnimating = false
        removeCallbacks(animatorRunnable)
    }

    private val animatorRunnable = object : Runnable {
        override fun run() {
            if (!isAnimating) return
            invalidate()
            postDelayed(this, 33L) // ~30 fps is smooth and very battery efficient
        }
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (visibility == VISIBLE) {
            startAnimation()
        }
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        stopAnimation()
    }

    override fun onVisibilityChanged(changedView: View, visibility: Int) {
        super.onVisibilityChanged(changedView, visibility)
        if (visibility == VISIBLE && isAttachedToWindow) {
            startAnimation()
        } else {
            stopAnimation()
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val bm = headBitmap ?: return
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return

        val now = SystemClock.uptimeMillis()

        // 1. Gentle idle breathing movement (subtle vertical oscillation ~ 1dp)
        val density = resources.displayMetrics.density
        val breathPeriod = 2600.0 // 2.6 seconds per breath cycle
        val breathPhase = (now % breathPeriod) / breathPeriod * 2.0 * Math.PI
        val breathY = (sin(breathPhase) * 1.2f * density).toFloat()

        // Subtle scale breathing (0.985 to 1.015)
        val scale = 1.0f + (sin(breathPhase) * 0.015f).toFloat()

        canvas.save()
        canvas.translate(w / 2f, h / 2f + breathY)
        canvas.scale(scale, scale)

        // Draw head bitmap centered
        val aspect = bm.width.toFloat() / bm.height.toFloat()
        val drawW: Float
        val drawH: Float
        if (w / h > aspect) {
            drawH = h * 0.92f
            drawW = drawH * aspect
        } else {
            drawW = w * 0.92f
            drawH = drawW / aspect
        }
        dstRect.set(-drawW / 2f, -drawH / 2f, drawW / 2f, drawH / 2f)
        canvas.drawBitmap(bm, srcRect, dstRect, bitmapPaint)

        // 2. Eye expression & Blinking logic
        // Blink cycle every 4.2 seconds; eyes shut for ~140ms
        val blinkPeriod = 4200L
        val blinkTime = now % blinkPeriod
        val isBlinking = blinkTime in 0L..140L

        // Coordinates relative to dstRect
        // In 512x512 head bitmap:
        // Left eye center: ~31.5% from left, ~37.5% from top
        // Right eye center: ~66.0% from left, ~36.9% from top
        val eyeW = drawW * 0.088f
        val baseEyeH = drawH * 0.055f
        val eyeH = if (isBlinking) 1.5f * density else baseEyeH

        val leftEyeX = dstRect.left + drawW * 0.315f
        val leftEyeY = dstRect.top + drawH * 0.375f
        val rightEyeX = dstRect.left + drawW * 0.660f
        val rightEyeY = dstRect.top + drawH * 0.369f

        // Pick eye colors according to urgency state
        val (glowColor, coreColor) = when (urgencyState) {
            UrgencyState.NORMAL -> Pair(Color.argb(140, 0, 210, 255), Color.rgb(0, 229, 255))
            UrgencyState.WARNING -> Pair(Color.argb(160, 255, 149, 0), Color.rgb(255, 179, 0))
            UrgencyState.CRITICAL, UrgencyState.COMPLETED -> {
                // Subtle alert pulse in critical state
                val alertPhase = (now % 800L) / 800.0 * 2.0 * Math.PI
                val alertAlpha = (160 + sin(alertPhase) * 60).toInt().coerceIn(100, 240)
                Pair(Color.argb(alertAlpha, 255, 59, 48), Color.rgb(255, 69, 58))
            }
        }

        eyeGlowPaint.color = glowColor
        eyeCorePaint.color = coreColor

        if (!isBlinking) {
            // Draw outer eye glow
            canvas.drawRoundRect(
                leftEyeX - eyeW * 0.65f, leftEyeY - eyeH * 0.65f,
                leftEyeX + eyeW * 0.65f, leftEyeY + eyeH * 0.65f,
                eyeH, eyeH, eyeGlowPaint
            )
            canvas.drawRoundRect(
                rightEyeX - eyeW * 0.65f, rightEyeY - eyeH * 0.65f,
                rightEyeX + eyeW * 0.65f, rightEyeY + eyeH * 0.65f,
                eyeH, eyeH, eyeGlowPaint
            )

            // Draw bright eye core
            canvas.drawRoundRect(
                leftEyeX - eyeW * 0.45f, leftEyeY - eyeH * 0.45f,
                leftEyeX + eyeW * 0.45f, leftEyeY + eyeH * 0.45f,
                eyeH * 0.7f, eyeH * 0.7f, eyeCorePaint
            )
            canvas.drawRoundRect(
                rightEyeX - eyeW * 0.45f, rightEyeY - eyeH * 0.45f,
                rightEyeX + eyeW * 0.45f, rightEyeY + eyeH * 0.45f,
                eyeH * 0.7f, eyeH * 0.7f, eyeCorePaint
            )
        } else {
            // Closed eye line during blink
            val shutPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = coreColor
                strokeWidth = 2f * density
                style = Paint.Style.STROKE
                strokeCap = Paint.Cap.ROUND
            }
            canvas.drawLine(leftEyeX - eyeW * 0.45f, leftEyeY, leftEyeX + eyeW * 0.45f, leftEyeY, shutPaint)
            canvas.drawLine(rightEyeX - eyeW * 0.45f, rightEyeY, rightEyeX + eyeW * 0.45f, rightEyeY, shutPaint)
        }

        canvas.restore()
    }
}
