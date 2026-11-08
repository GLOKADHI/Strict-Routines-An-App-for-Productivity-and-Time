package com.strictrecords.app

import android.app.Activity
import android.app.Instrumentation
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.Canvas
import android.media.session.MediaController
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.ContextThemeWrapper
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ScrollView
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Real Android Views + synthetic own media session. No scores, permissions, app selection or external players changed. */
object RestrictionPopupRegression {
    fun run(instrumentation: Instrumentation) {
        val output=File(instrumentation.targetContext.cacheDir,"restriction-popup-regression").apply { mkdirs() }
        val report=JSONObject().put("physical_device_test",false).put("media_fixture","synthetic own session; no audible playback")
        var session: MediaSession?=null
        try {
            val cases=JSONArray()
            for(pkg in listOf("com.spotify.music","com.sec.android.app.music","com.google.android.youtube")) {
                for(spec in listOf(Triple(360,740,1f),Triple(320,480,2f),Triple(740,360,1.3f))) {
                    instrumentation.runOnMainSync {
                        val config=Configuration(instrumentation.targetContext.resources.configuration).apply { fontScale=spec.third }
                        val context=ContextThemeWrapper(instrumentation.targetContext.createConfigurationContext(config),android.R.style.Theme_Material_NoActionBar)
                        val density=context.resources.displayMetrics.density
                        val width=(spec.first*density).toInt();val height=(spec.second*density).toInt()
                        val decision=DisciplineScoringRules.evaluateRestrictionDecision(0,pkg)
                        var homeActions=0
                        val card=StrictBotOverlayController.buildStrictBotCardView(context,decision) { homeActions++ }
                        val surface=RestrictionPopupSurface(context,card)
                        surface.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(height,View.MeasureSpec.EXACTLY))
                        surface.layout(0,0,width,height)
                        val buttons=mutableListOf<Button>()
                        fun visit(view: View) { if(view is Button) buttons.add(view);if(view is ViewGroup) for(i in 0 until view.childCount) visit(view.getChildAt(i)) }
                        visit(surface)
                        check(buttons.size==1 && buttons.single().text.toString()=="BACK TO HOME") { "Restriction must have exactly one Home action" }
                        check(card.width in 1..width && buttons.single().height>=48*density) { "Card/button size invalid" }
                        val scroll=surface.getChildAt(0) as ScrollView
                        scroll.scrollTo(0,card.height)
                        check(buttons.single().bottom-scroll.scrollY<=scroll.height) { "Home button is unreachable" }
                        val bitmap=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888)
                        surface.draw(Canvas(bitmap))
                        val filename="${TargetAppMonitoringPolicy.targetForPackage(pkg)!!.id}-${spec.first}x${spec.second}-font${spec.third}.png"
                        File(output,filename).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG,100,it) };bitmap.recycle()
                        buttons.single().performClick();check(homeActions==1)
                        cases.put(JSONObject().put("package",pkg).put("width_dp",spec.first).put("height_dp",spec.second).put("font_scale",spec.third)
                            .put("single_button",true).put("home_button_reachable",true).put("image",filename))
                    }
                }
            }
            report.put("layout_cases",cases)
            var pauseCalls=0;var controller: MediaController?=null
            val paused=java.util.concurrent.CountDownLatch(1)
            instrumentation.runOnMainSync {
                session=MediaSession(instrumentation.targetContext,"Restriction regression fixture").apply {
                    setCallback(object:MediaSession.Callback() {
                        override fun onPause() { pauseCalls++;setPlaybackState(PlaybackState.Builder().setState(PlaybackState.STATE_PAUSED,0,0f).setActions(PlaybackState.ACTION_PAUSE).build());paused.countDown() }
                    },Handler(Looper.getMainLooper()))
                    setPlaybackState(PlaybackState.Builder().setState(PlaybackState.STATE_PLAYING,0,1f).setActions(PlaybackState.ACTION_PAUSE).build())
                    isActive=true
                }
                controller=MediaController(instrumentation.targetContext,session!!.sessionToken)
                controller!!.transportControls.pause()
            }
            check(paused.await(3,java.util.concurrent.TimeUnit.SECONDS)) { "Synthetic pause callback timed out" }
            instrumentation.runOnMainSync { check(pauseCalls==1 && controller!!.playbackState?.state==PlaybackState.STATE_PAUSED) { "Synthetic media-session pause not acknowledged" } }
            report.put("synthetic_session_pause_confirmed",true).put("status","PASSED")
            File(output,"report.json").writeText(report.toString(2))
            instrumentation.finish(Activity.RESULT_OK,Bundle().apply { putString("restriction_popup",report.toString()) })
        } catch(error:Throwable) {
            report.put("status","FAILED").put("error",error.toString())
            File(output,"report.json").writeText(report.toString(2))
            instrumentation.finish(Activity.RESULT_CANCELED,Bundle().apply { putString("restriction_popup",report.toString()) })
        } finally { instrumentation.runOnMainSync { session?.release() } }
    }
}
