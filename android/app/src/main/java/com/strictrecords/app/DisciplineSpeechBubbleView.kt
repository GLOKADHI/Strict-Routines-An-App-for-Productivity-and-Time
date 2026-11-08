package com.strictrecords.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.util.TypedValue
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Speech bubble view displayed beside or above the walking discipline bot.
 * Features a dark card with glowing border and directional tail, displaying:
 * - State A: "No Entertainment apps unless sufficient points"
 * - State B: "Please turn on the accessibility" (with actionable settings shortcut)
 */
class DisciplineSpeechBubbleView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : FrameLayout(context, attrs, defStyleAttr) {

    private val density = context.resources.displayMetrics.density
    private fun dp(v: Float): Float = v * density
    private fun dp(v: Int): Int = (v * density).toInt()

    private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#0F172A") // Dark slate 900
        style = Paint.Style.FILL
    }

    private val borderPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.parseColor("#0284C7") // Default Sky Cyan
        style = Paint.Style.STROKE
        strokeWidth = dp(1.8f)
    }

    private val bubblePath = Path()
    private val bubbleRect = RectF()

    private val messageTextView: TextView
    private val actionTextView: TextView
    private val contentLayout: LinearLayout

    var onActionClicked: (() -> Unit)? = null

    init {
        setWillNotDraw(false)
        setPadding(dp(14), dp(10), dp(14), dp(14)) // extra bottom padding for tail

        contentLayout = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            layoutParams = LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT)
        }

        messageTextView = TextView(context).apply {
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12.5f)
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            maxLines = 2
            setLineSpacing(dp(2f), 1f)
            text = FloatingBotStateSelector.MSG_INSUFFICIENT_POINTS
        }

        actionTextView = TextView(context).apply {
            setTextColor(Color.parseColor("#F59E0B")) // Amber
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            text = "Tap to enable in Settings →"
            visibility = GONE
            setPadding(0, dp(3), 0, 0)
        }

        contentLayout.addView(messageTextView)
        contentLayout.addView(actionTextView)
        addView(contentLayout)

        setOnClickListener {
            onActionClicked?.invoke()
        }
    }

    fun setState(state: FloatingBotStateSelector.MessageState, customMessage: String = "") {
        when (state) {
            FloatingBotStateSelector.MessageState.ACCESSIBILITY_DISABLED -> {
                borderPaint.color = Color.parseColor("#F59E0B") // Amber border
                messageTextView.text = if (customMessage.isNotBlank()) customMessage else FloatingBotStateSelector.MSG_ACCESSIBILITY_DISABLED
                messageTextView.setTextColor(Color.parseColor("#FEF3C7")) // Warm light text
                actionTextView.visibility = VISIBLE
                actionTextView.text = "Tap to enable in Settings →"
            }
            FloatingBotStateSelector.MessageState.INSUFFICIENT_POINTS -> {
                borderPaint.color = Color.parseColor("#0284C7") // Cyan border
                messageTextView.text = if (customMessage.isNotBlank()) customMessage else FloatingBotStateSelector.MSG_INSUFFICIENT_POINTS
                messageTextView.setTextColor(Color.WHITE)
                actionTextView.visibility = GONE
            }
            FloatingBotStateSelector.MessageState.NONE -> {
                // Hidden or cleared
            }
        }
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        val tailHeight = dp(6f)
        val bodyBottom = h - tailHeight
        val cornerRadius = dp(10f)

        bubbleRect.set(dp(1.5f), dp(1.5f), w - dp(1.5f), bodyBottom)

        bubblePath.reset()
        bubblePath.addRoundRect(bubbleRect, cornerRadius, cornerRadius, Path.Direction.CW)

        // Add speech bubble tail pointing downward toward the bot
        val tailCenterX = dp(24f).coerceIn(cornerRadius + dp(8f), w - cornerRadius - dp(8f))
        val tailHalfWidth = dp(5f)

        bubblePath.moveTo(tailCenterX - tailHalfWidth, bodyBottom)
        bubblePath.lineTo(tailCenterX, h - dp(1f))
        bubblePath.lineTo(tailCenterX + tailHalfWidth, bodyBottom)

        canvas.drawPath(bubblePath, bgPaint)
        canvas.drawPath(bubblePath, borderPaint)

        super.onDraw(canvas)
    }
}
