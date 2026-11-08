package com.strictrecords.app

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import java.util.Locale

/** Flexible reward card; its countdown is driven by the verified server lease. */
class YouTubeRewardCard(context:Context,private val db:StrictRecordsLocalDb):LinearLayout(context) {
    private val handler=Handler(Looper.getMainLooper())
    private val status=label(17f,Color.WHITE,true)
    private val detail=label(13f,Color.parseColor("#A7B5C6"))
    private val claim=Button(context)
    private val open=Button(context)
    private var lastFetch=-20_000L
    private var fetching=false
    private var reward:YouTubeRewardManager.YouTubeReward?=null
    private var active:YouTubeRewardManager.YouTubeReward?=null
    private val ticker=object:Runnable {
        override fun run() {
            if(!isAttachedToWindow)return
            drawState()
            if(SystemClock.elapsedRealtime()-lastFetch>=10_000)refresh()
            handler.postDelayed(this,1000)
        }
    }
    private fun dp(value:Int)=(resources.displayMetrics.density*value).toInt()
    private fun label(size:Float,color:Int,bold:Boolean=false)=TextView(context).apply {
        textSize=size;setTextColor(color);if(bold)setTypeface(typeface,Typeface.BOLD)
        layoutParams=LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.WRAP_CONTENT).apply{bottomMargin=dp(12)}
    }
    init {
        orientation=VERTICAL;setPadding(dp(20),dp(22),dp(20),dp(18))
        layoutParams=LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.WRAP_CONTENT).apply{bottomMargin=dp(18)}
        background=GradientDrawable(GradientDrawable.Orientation.TL_BR,intArrayOf(Color.parseColor("#182332"),Color.parseColor("#101820"))).apply{cornerRadius=dp(22).toFloat();setStroke(dp(1),Color.parseColor("#304153"))}
        addView(label(12f,Color.parseColor("#43E2B6"),true).apply{text="YOUTUBE ONLY · 20 MINUTES"})
        addView(status);addView(detail)
        claim.text="Claim Single YouTube Reward";claim.isAllCaps=false;claim.minHeight=dp(48)
        claim.setOnClickListener {
            val chosen=reward ?: return@setOnClickListener
            android.app.AlertDialog.Builder(context).setTitle("Start your 20 minutes?")
                .setMessage("Your Single YouTube Reward starts now and expires after 20 minutes across your authorized devices. Your actual points remain unchanged.")
                .setNegativeButton("Not yet",null).setPositiveButton("Activate") {_,_->
                    claim.isEnabled=false
                    YouTubeRewardManager.startSession(db,"",chosen.rewardId){result->
                        result.onFailure{detail.text=it.message};refresh()
                    }
                }.show()
        }
        addView(claim,LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.WRAP_CONTENT))
        open.text="Open YouTube";open.isAllCaps=false;open.minHeight=dp(48)
        open.setOnClickListener {
            if(YouTubeRewardManager.isYouTubeActive(context))runCatching{context.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse("https://www.youtube.com/")))}
        }
        addView(open,LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.WRAP_CONTENT))
        addView(label(12f,Color.parseColor("#91A3B8")).apply {
            text="Earned from an approved hourly proof scored at least 6/8, submitted at least 10 minutes before its submission deadline. One reward per hourly slot.\n\n${YouTubeRewardManager.ANDROID_PLATFORM_DISCLOSURE}"
            setPadding(0,dp(14),0,0)
        })
        status.text="Single YouTube Reward";detail.text="Checking shared reward state…";claim.isEnabled=false;open.visibility=GONE
    }
    private fun refresh() {
        if(fetching)return
        fetching=true;lastFetch=SystemClock.elapsedRealtime()
        YouTubeRewardManager.fetchRewardStatus(db,"",""){result->
            fetching=false
            if(!isAttachedToWindow)return@fetchRewardStatus
            result.onSuccess {
                active=it.activeSession;reward=it.availableRewards.firstOrNull()
                detail.text=if(active!=null)"Expires ${YouTubeRewardManager.expiryDisplay(active?.expiryTime)} · actual points unchanged"
                    else if(reward!=null)"Available · 20:00 · activate when you are ready."
                    else "No claimable reward. Validation and required approval must finish before a reward is earned."
                drawState()
            }.onFailure {active=null;reward=null;claim.isEnabled=false;open.visibility=GONE;status.text="Reward state unconfirmed";detail.text=it.message}
        }
    }
    private fun drawState() {
        val rem=active?.let{YouTubeRewardManager.calculateRemainingSeconds(it.expiryTime)} ?: 0
        if(active!=null && rem<=0){active=null;status.text="Reward expired · normal YouTube policy restored";claim.isEnabled=false;open.visibility=GONE;return}
        if(active!=null)status.text=String.format(Locale.US,"YOUTUBE REWARD ACTIVE — %02d:%02d%s",rem/60,rem%60,
            if(YouTubeRewardManager.isYouTubeActive(context)) "" else " · another device")
        else if(reward!=null)status.text="Single YouTube Reward · Available"
        claim.visibility=if(active==null)VISIBLE else GONE
        claim.isEnabled=reward!=null && active==null && !fetching
        open.visibility=if(active!=null && YouTubeRewardManager.isYouTubeActive(context))VISIBLE else GONE
    }
    override fun onAttachedToWindow(){super.onAttachedToWindow();handler.post(ticker)}
    override fun onDetachedFromWindow(){handler.removeCallbacks(ticker);super.onDetachedFromWindow()}
}
