package com.strictrecords.app

import android.content.Context
import android.graphics.Canvas
import android.os.SystemClock
import android.util.AttributeSet
import android.view.View
import kotlin.math.min

/** One lifecycle-owned clock drives both root travel and every articulated body part. */
class DisciplineBotWalkingView @JvmOverloads constructor(
    context:Context,attrs:AttributeSet?=null,defStyleAttr:Int=0
):View(context,attrs,defStyleAttr) {
    enum class BotState { WALKING,IDLE,WARNING }
    private var motion=PremiumBotMotion()
    private val renderer=PremiumBotRenderer()
    private var running=false
    private var posted=false
    private var lastFrame=0L
    private var presentationScale=1.6f
    private var anchorFraction=.5f
    private var readyAction:(()->Unit)?=null
    private var pendingEntrance=false
    internal var onMotionFrame:((PremiumBotMotion)->Unit)?=null
    internal var drawCount=0L;private set
    internal var drawNanos=0L;private set
    internal val model get()=motion
    val currentAnimState get()=motion.state
    val botState get()=when { motion.warning->BotState.WARNING;motion.isWalking->BotState.WALKING;else->BotState.IDLE }
    val facingRight get()=motion.facingRight
    var isWarningMode:Boolean
        get()=motion.warning
        set(value){setWarning(value)}
    fun setAnimState(state:PremiumBotMotion.BotAnimState){motion.setState(state);invalidate()}
    fun setWarning(value:Boolean){motion.warning=value;invalidate()}
    fun setSpeaking(value:Boolean){motion.setSpeaking(value);invalidate()}
    fun setPresentation(scale:Float,stationary:Boolean=false){
        presentationScale=scale.coerceIn(.5f,3.5f)
        if(stationary) motion.standInPlace()
        invalidate()
    }
    internal fun continueMotion(source:PremiumBotMotion){
        stopAnimation()
        motion=source;motion.setSpeaking(false)
        anchorFraction=.44f;pendingEntrance=false;readyAction=null
    }
    /** A new surface may inherit the model; it never adds a second body clock. */
    fun playEntrance(onReady:()->Unit) {
        if(pendingEntrance)return
        anchorFraction=.44f
        if(motion.presentationStarted){
            if(motion.isMoving || motion.state==PremiumBotMotion.BotAnimState.TURNING)
                readyAction=onReady
            else onReady()
            startAnimation()
            return
        }
        readyAction=onReady;pendingEntrance=true
        if(width>0)beginMeasuredEntrance()
        startAnimation()
    }
    internal fun restartEntrance(onReady:()->Unit){
        pendingEntrance=false;readyAction=onReady;anchorFraction=.44f
        motion.playEntrance(width*anchorFraction/scale()+23f,restart=true);startAnimation()
    }
    private fun beginMeasuredEntrance(){
        if(!pendingEntrance || width<=0)return
        pendingEntrance=false
        motion.playEntrance(width*anchorFraction/scale()+23f)
    }
    private fun scale():Float {
        val d=resources.displayMetrics.density
        val h=(height-18f*d).coerceAtLeast(10f)
        val w=(width-16f*d).coerceAtLeast(10f)
        return min(presentationScale*d,min(h/102f,w/86f)).coerceAtLeast(.01f)
    }
    private val frame=object:Runnable {
        override fun run(){
            posted=false
            if(!running || !isShown || windowVisibility!=VISIBLE){lastFrame=0L;return}
            beginMeasuredEntrance()
            val now=SystemClock.uptimeMillis()
            val dt=if(lastFrame==0L)0f else (now-lastFrame)/1000f
            lastFrame=now
            motion.update(dt)
            if(motion.state==PremiumBotMotion.BotAnimState.HIDDEN){invalidate();stopAnimation();return}
            if(motion.state==PremiumBotMotion.BotAnimState.STANDING) {
                val action=readyAction;readyAction=null;action?.invoke()
            }
            onMotionFrame?.invoke(motion)
            invalidate()
            scheduleFrame()
        }
    }
    private fun scheduleFrame(){
        if(!running || posted || !isAttachedToWindow || !isShown || windowVisibility!=VISIBLE)return
        posted=true
        postDelayed(frame,if(motion.state==PremiumBotMotion.BotAnimState.IDLE)66L else 33L)
    }
    fun startAnimation(){
        if(!running){running=true;lastFrame=0L}
        scheduleFrame()
    }
    fun stopAnimation(){running=false;posted=false;lastFrame=0L;removeCallbacks(frame)}
    override fun onAttachedToWindow(){super.onAttachedToWindow();startAnimation()}
    override fun onDetachedFromWindow(){stopAnimation();readyAction=null;onMotionFrame=null;super.onDetachedFromWindow()}
    override fun onVisibilityChanged(changedView:View,visibility:Int){
        super.onVisibilityChanged(changedView,visibility)
        if(visibility==VISIBLE && isAttachedToWindow){lastFrame=0L;startAnimation()}else stopAnimation()
    }
    override fun onWindowVisibilityChanged(visibility:Int){
        super.onWindowVisibilityChanged(visibility)
        if(visibility==VISIBLE && isAttachedToWindow)startAnimation()else stopAnimation()
    }
    override fun onSizeChanged(w:Int,h:Int,oldw:Int,oldh:Int){
        super.onSizeChanged(w,h,oldw,oldh)
        // Reflow the anchor only. Do not reset phase, feet or state on a window resize.
        beginMeasuredEntrance()
    }
    override fun onMeasure(widthMeasureSpec:Int,heightMeasureSpec:Int){
        val d=resources.displayMetrics.density
        setMeasuredDimension(resolveSize((190*d).toInt(),widthMeasureSpec),resolveSize((220*d).toInt(),heightMeasureSpec))
    }
    override fun onDraw(canvas:Canvas){
        super.onDraw(canvas)
        val start=SystemClock.elapsedRealtimeNanos()
        val s=scale();val d=resources.displayMetrics.density
        canvas.save()
        canvas.translate(width*anchorFraction+motion.x*s,height-12f*d)
        canvas.scale(s,s)
        renderer.draw(canvas,motion,motion.speaking)
        canvas.restore()
        drawCount++;drawNanos+=SystemClock.elapsedRealtimeNanos()-start
    }
}
