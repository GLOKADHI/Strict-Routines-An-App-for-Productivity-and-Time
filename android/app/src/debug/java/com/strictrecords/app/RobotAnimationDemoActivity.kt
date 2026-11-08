package com.strictrecords.app

import android.app.Activity
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.graphics.Color
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Debug-only rendering fixture. No DB, policy, attendance, permission or ledger writes. */
class RobotAnimationDemoActivity:Activity() {
    private lateinit var bot:DisciplineBotWalkingView
    private val main=Handler(Looper.getMainLooper())
    private var speech:StrictBotPopupSpeech?=null
    private val samples=JSONArray()
    private val intervals=JSONArray()
    private val states=linkedSetOf<String>()
    private var startedAt=0L
    private var previousAt=0L
    private var frames=0
    private var readyAt=-1L
    private var complete=false
    private var maxSlide=0f
    private var bothAir=0
    private var previous=arrayOfNulls<PremiumBotMotion.Leg>(2)
    private var simulate=false
    private fun dp(v:Int)=(v*resources.displayMetrics.density).toInt()
    override fun onCreate(savedInstanceState:Bundle?){
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        simulate=intent.getBooleanExtra("simulate_speech",false)
        val accessibility=intent.getBooleanExtra("warning",false)
        val message=if(accessibility)"Please turn on the accessibility" else "No Entertainment apps unless sufficient points"
        val decision=DisciplineScoringRules.evaluateRestrictionDecision(0,"com.google.android.youtube")
        val card=StrictBotOverlayController.buildStrictBotCardView(this,decision){finish()}
        val group=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL}
        val bubble=DisciplineSpeechBubbleView(this).apply{
            setState(if(accessibility)FloatingBotStateSelector.MessageState.ACCESSIBILITY_DISABLED else FloatingBotStateSelector.MessageState.INSUFFICIENT_POINTS,message)
            visibility=View.INVISIBLE
        }
        group.addView(bubble,LinearLayout.LayoutParams(dp(250),LinearLayout.LayoutParams.WRAP_CONTENT))
        bot=DisciplineBotWalkingView(this).apply{setPresentation(1.85f);setWarning(accessibility)}
        group.addView(bot,LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,dp(185)))
        val root=RestrictionPopupSurface(this,card,group)
        val label=TextView(this).apply{
            text="Animation check • Fixture score • No ledger changes"
            textSize=10f;setTextColor(Color.CYAN);gravity=Gravity.CENTER
        }
        root.addView(label,FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,dp(22),Gravity.TOP))
        setContentView(root)
        bot.onMotionFrame={m->
            val now=SystemClock.uptimeMillis()
            if(previousAt>0)intervals.put(now-previousAt)
            previousAt=now;frames++;states+=m.state.name
            if(!m.leg(0).planted && !m.leg(1).planted)bothAir++
            for(i in 0..1){
                val leg=m.leg(i);val old=previous[i]
                if(leg.planted && old?.planted==true)maxSlide=maxOf(maxSlide,kotlin.math.abs(leg.sole.x-old.sole.x))
                previous[i]=leg
            }
            val caption="Animation check • "+m.state.name+" • "+(if(simulate)"speech callback fixture" else "real TTS callbacks")
            if(label.text.toString()!=caption)label.text=caption
            if(frames%3==0){
                val pose=JSONObject().put("ms",now-startedAt).put("state",m.state.name)
                    .put("root_x",m.x).put("speed",m.speed).put("phase",m.phase).put("cycles",m.completedCycles)
                    .put("pelvis_yaw",m.pelvisYaw).put("torso_yaw",m.torsoYaw).put("head_yaw",m.headYaw)
                    .put("gesture",m.gestureProgress)
                for(i in 0..1){
                    val leg=m.leg(i)
                    pose.put("leg_$i",JSONObject().put("hip_x",leg.hip.x).put("hip_y",leg.hip.y)
                        .put("knee_x",leg.knee.x).put("knee_y",leg.knee.y)
                        .put("sole_x",leg.sole.x).put("sole_y",leg.sole.y).put("planted",leg.planted))
                }
                samples.put(pose)
            }
        }
        bot.post{
            startedAt=SystemClock.uptimeMillis()
            bot.playEntrance{
                readyAt=SystemClock.uptimeMillis()-startedAt
                bubble.visibility=View.VISIBLE;bubble.alpha=0f
                bubble.animate().alpha(1f).setDuration(220).start()
                if(simulate){
                    // Feed the same callback as TTS; do not change the user's sound preferences.
                    main.postDelayed({bot.setSpeaking(true)},600L)
                    main.postDelayed({bot.setSpeaking(false)},4100L)
                }else{
                    speech=StrictBotPopupSpeech(this,{bot.setSpeaking(it)},{})
                    speech?.speak(message,"rig-demo:"+startedAt,manual=true)
                }
            }
            main.postDelayed({complete=true;saveReport()},18_000L)
        }
    }
    private fun saveReport(){
        if(!::bot.isInitialized)return
        val report=JSONObject().put("complete",complete).put("device",android.os.Build.MODEL)
            .put("sdk",android.os.Build.VERSION.SDK_INT).put("speech_source",if(simulate)"SIMULATED_TTS_CALLBACK_NO_AUDIO" else "ACTUAL_TTS_CALLBACKS")
            .put("database_writes",false).put("policy_changes",false).put("frames",frames)
            .put("elapsed_ms",SystemClock.uptimeMillis()-startedAt).put("ready_at_ms",readyAt)
            .put("states",JSONArray(states.toList())).put("both_feet_airborne_frames",bothAir)
            .put("max_planted_foot_slide_units",maxSlide).put("draw_count",bot.drawCount)
            .put("average_ui_draw_cpu_ms",if(bot.drawCount>0)bot.drawNanos.toDouble()/bot.drawCount/1_000_000 else 0.0)
            .put("frame_intervals_ms",intervals).put("samples",samples)
        File(cacheDir,"robot-rig-demo").apply{mkdirs()}
            .resolve(if(intent.getBooleanExtra("warning",false))"warning.json" else "entrance.json").writeText(report.toString(2))
    }
    override fun onPause(){if(::bot.isInitialized)bot.stopAnimation();speech?.stop();super.onPause()}
    override fun onResume(){super.onResume();if(::bot.isInitialized)bot.startAnimation()}
    override fun onDestroy(){main.removeCallbacksAndMessages(null);saveReport();speech?.close();super.onDestroy()}
}
