package com.strictrecords.app

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.regex.Pattern

/** Shared, account-scoped YouTube entitlement. Never writes scores or score history. */
object YouTubeRewardManager {
    const val HARD_MAX_ALLOWANCE_SECONDS = 1200
    const val ANDROID_PLATFORM_DISCLOSURE = "YouTube only · 20 minutes from activation. Android OS cannot terminate another app; existing target-scoped overlay enforcement resumes at expiry. Accessibility and independent restrictions still apply. Shared service confirmation is required."
    private val VIDEO_ID_REGEX = Pattern.compile("^[A-Za-z0-9_-]{11}$")
    private val YOUTUBE_HOSTS = setOf("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com")
    private val worker by lazy { Executors.newSingleThreadExecutor() }
    private val main by lazy { Handler(Looper.getMainLooper()) }
    private val busy = AtomicBoolean(false)
    private val lease = YouTubeRewardLease()
    @Volatile private var data = JSONObject()
    @Volatile private var verifiedScope = ""
    @Volatile private var lastAttempt = -30_000L
    @Volatile private var wasActive = false
    @Volatile private var boundaryAt = -10_000L
    private var expiryTask: Runnable? = null
    private data class Binding(val origin:String,val account:String,val device:String,val token:String) {
        val scope get() = "$origin|$account|$device|${LedgerSyncRules.sha256(token)}"
    }
    private fun binding(db:StrictRecordsLocalDb):Binding? = runCatching {
        val origin=db.getConfigValue("points_cloud_origin","").trimEnd('/')
        if(!PointsCloudSync.validOrigin(origin))return null
        db.readableDatabase.rawQuery("SELECT account_id,device_id,device_token FROM device_registration WHERE status='REGISTERED' LIMIT 1",null).use {
            if(!it.moveToFirst())return null
            Binding(origin,it.getString(0),it.getString(1),it.getString(2)).takeIf { b->b.account.isNotBlank()&&b.device.isNotBlank()&&b.token.isNotBlank() }
        }
    }.getOrNull()
    data class YouTubeReward(val rewardId:String,val accountId:String,val hourlyOccurrenceId:String,val submissionId:String,
        val validatedScore:Int,val isTimely:Boolean=true,val state:String,val selectedVideoId:String?=null,
        val allowedSeconds:Int=1200,val expiryTime:String?=null,val activeClientId:String?=null,val remainingSeconds:Int=0) {
        val isAvailable get()=state=="AVAILABLE"
        val isActive get()=state=="ACTIVE"
        val isConsumed get()=state in setOf("CONSUMED","EXPIRED","CANCELLED")
    }
    data class RewardStatusResponse(val success:Boolean,val activeSession:YouTubeReward?,val availableRewards:List<YouTubeReward>,val totalAvailable:Int,val error:String?=null)
    fun extractCanonicalVideoId(urlOrId: String?): String? {
        if (urlOrId.isNullOrBlank()) return null
        val clean = urlOrId.trim()

        // Direct 11-char ID
        if (VIDEO_ID_REGEX.matcher(clean).matches()) {
            return clean
        }

        return try {
            val uri = java.net.URI(clean)
            val host = uri.host?.lowercase(Locale.ROOT) ?: return null

            if (host !in YOUTUBE_HOSTS) return null

            // youtu.be/<id>
            if (host == "youtu.be") {
                val path = uri.path?.trimStart('/') ?: return null
                val candidate = path.split('/', '?', '#').firstOrNull() ?: ""
                return if (VIDEO_ID_REGEX.matcher(candidate).matches()) candidate else null
            }

            // youtube.com/watch?v=<id> or query parameter
            val query = uri.query
            if (!query.isNullOrBlank()) {
                val params = query.split('&')
                for (p in params) {
                    val parts = p.split('=', limit = 2)
                    if (parts.size == 2 && parts[0] == "v") {
                        val v = parts[1]
                        if (VIDEO_ID_REGEX.matcher(v).matches()) {
                            return v
                        }
                    }
                }
            }

            // youtube.com/embed/<id> or /v/<id> or /shorts/<id>
            val path = uri.path ?: ""
            val segments = path.split('/').filter { it.isNotBlank() }
            if (segments.size >= 2 && segments[0] in setOf("embed", "v", "shorts")) {
                val candidate = segments[1]
                if (VIDEO_ID_REGEX.matcher(candidate).matches()) {
                    return candidate
                }
            }

            null
        } catch (_: Exception) {
            null
        }
    }


    fun calculateViewingAllowance(durationSeconds:Int?):Int = HARD_MAX_ALLOWANCE_SECONDS
    fun isSubmissionTimely(acceptedAtIso:String?,scheduledTimeIso:String?):Boolean = runCatching {
        parseIso(acceptedAtIso!!) <= parseIso(scheduledTimeIso!!) - 600_000L
    }.getOrDefault(false)
    private fun parseIso(value:String):Long {
        val clean=value.trim().replace(' ', 'T')
        for(pattern in listOf("yyyy-MM-dd'T'HH:mm:ss.SSSXXX","yyyy-MM-dd'T'HH:mm:ssXXX","yyyy-MM-dd'T'HH:mm:ss")) {
            val format=java.text.SimpleDateFormat(pattern,Locale.US).apply{isLenient=false;timeZone=java.util.TimeZone.getTimeZone("Asia/Kolkata")}
            val position=java.text.ParsePosition(0);val date=format.parse(clean,position)
            if(date!=null && position.index==clean.length)return date.time
        }
        throw IllegalArgumentException("Invalid persisted timestamp")
    }
    fun expiryDisplay(iso:String?):String = runCatching {
        java.text.SimpleDateFormat("h:mm:ss a 'IST'",Locale.US).apply{timeZone=java.util.TimeZone.getTimeZone("Asia/Kolkata")}.format(java.util.Date(parseIso(iso!!)))
    }.getOrDefault("Unconfirmed")
    fun parseRewardJson(obj:JSONObject):YouTubeReward = YouTubeReward(
        rewardId=obj.optString("reward_id"),accountId=obj.optString("account_id"),hourlyOccurrenceId=obj.optString("hourly_occurrence_id"),
        submissionId=obj.optString("submission_id"),validatedScore=obj.optInt("validated_score"),state=obj.optString("state"),
        expiryTime=obj.optLong("expiry_ms").takeIf{it>0}?.let{java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX",Locale.US).apply{timeZone=java.util.TimeZone.getTimeZone("UTC")}.format(java.util.Date(it))},
        activeClientId=obj.optString("active_client_id"),remainingSeconds=obj.optInt("remaining_seconds"))
    fun calculateRemainingSeconds(expiryTimeIso:String?):Int {
        val active=data.optJSONObject("active_session") ?: return 0
        if(expiryTimeIso != parseRewardJson(active).expiryTime)return 0
        return (lease.remaining(verifiedScope,SystemClock.elapsedRealtime())/1000).toInt()
    }
    private fun schema(db:StrictRecordsLocalDb) {
        db.writableDatabase.execSQL("CREATE TABLE IF NOT EXISTS youtube_reward_acceptances(account_id TEXT NOT NULL,submission_id TEXT NOT NULL,activity_id TEXT NOT NULL,accepted_at TEXT NOT NULL,submission_deadline TEXT NOT NULL,PRIMARY KEY(account_id,submission_id))")
        db.writableDatabase.execSQL("CREATE TABLE IF NOT EXISTS youtube_reward_review_receipts(account_id TEXT NOT NULL,submission_id TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(account_id,submission_id))")
    }
    fun captureRemoteReview(db:StrictRecordsLocalDb,result:JSONObject) {
        val item=result.optJSONObject("youtube_reward_submission") ?: return
        val b=binding(db)
        // Even before cloud configuration, retain only the currently paired
        // account's authenticated PC review for later authoritative attestation.
        val account=db.readableDatabase.rawQuery("SELECT account_id FROM device_registration WHERE status='REGISTERED' LIMIT 1",null).use{if(it.moveToFirst())it.getString(0) else ""}
        if(account.isBlank() || item.optString("account_id")!=account || b!=null && b.account!=account)return
        schema(db)
        val sub=item.getString("submission_id")
        val old=db.readableDatabase.rawQuery("SELECT payload_json FROM youtube_reward_review_receipts WHERE account_id=? AND submission_id=?",arrayOf(account,sub)).use{if(it.moveToFirst())JSONObject(it.getString(0)) else null}
        if(old!=null && listOf("accepted_at","submission_deadline","activity_id").any{old.optString(it)!=item.optString(it)})return
        db.writableDatabase.execSQL("INSERT OR REPLACE INTO youtube_reward_review_receipts VALUES(?,?,?)",arrayOf(account,sub,item.toString()))
    }
    // Called inside the existing durable acceptance transaction. Evaluation delays
    // and schedule edits cannot replace either timestamp later.
    fun captureAcceptance(db:StrictRecordsLocalDb,submission:String,activity:String,accepted:String) {
        schema(db)
        db.readableDatabase.rawQuery("SELECT account_id,type,window_end FROM scheduled_activities WHERE activity_id=?",arrayOf(activity)).use {
            if(!it.moveToFirst() || it.getString(1)!="HOURLY")return
            val account=it.getString(0).orEmpty();val deadline=it.getString(2).orEmpty()
            if(account.isBlank() || deadline.isBlank())return
            parseIso(accepted);parseIso(deadline)
            db.writableDatabase.execSQL("INSERT OR IGNORE INTO youtube_reward_acceptances VALUES(?,?,?,?,?)",arrayOf(account,submission,activity,accepted,deadline))
        }
    }
    private fun candidates(db:StrictRecordsLocalDb,account:String):JSONArray {
        schema(db);val items=JSONArray()
        db.readableDatabase.rawQuery("SELECT a.*,p.status,p.points_awarded,p.evaluator_output_json FROM youtube_reward_acceptances a JOIN proof_evaluations p ON p.submission_id=a.submission_id WHERE a.account_id=? AND p.is_dev_mode=0 ORDER BY a.accepted_at DESC",arrayOf(account)).use { c->
            while(c.moveToNext()) {
                fun str(key:String)=c.getString(c.getColumnIndexOrThrow(key)).orEmpty()
                val output=runCatching{JSONObject(str("evaluator_output_json"))}.getOrDefault(JSONObject())
                val taufa=output.optJSONObject("taufa_ai") ?: JSONObject()
                val originalStatus=str("status")
                val approved=originalStatus in setOf("APPROVED","VERIFIED")
                val status=when {
                    approved -> "VALID"
                    originalStatus in setOf("REJECTED","INVALID") -> "INVALID"
                    originalStatus == "INSUFFICIENT_EVIDENCE" -> "INSUFFICIENT_EVIDENCE"
                    originalStatus in setOf("FLAGGED_FOR_REVIEW","NEEDS_REVIEW","AWAITING_ADMIN_REVIEW") -> "NEEDS_REVIEW"
                    originalStatus in setOf("RETRYABLE_FAILURE","PROCESSING_ERROR") -> "PROCESSING_ERROR"
                    else -> "PENDING"
                }
                val dup=output.optString("duplicate_status") in setOf("EXACT_DUPLICATE","HIGH_SIMILARITY","POSSIBLE_DUPLICATE")
                items.put(JSONObject().apply {
                    put("submission_id",str("submission_id"));put("activity_id",str("activity_id"));put("accepted_at",str("accepted_at"))
                    put("submission_deadline",str("submission_deadline"));put("task_type","HOURLY");put("validation_status",status)
                    put("approved",approved);put("validator","TAUFA_AI");put("validated_score",taufa.optInt("score",0))
                    put("category_match",taufa.optBoolean("category_match"));put("description_match",taufa.optBoolean("description_match"))
                    put("evidence_match",taufa.optBoolean("evidence_match"));put("duplicate_detected",dup)
                })
            }
        }
        // A completed authenticated PC review may resolve a local NEEDS_REVIEW
        // result; it carries the original acceptance and frozen window deadline.
        val merged=linkedMapOf<String,JSONObject>()
        for(i in 0 until items.length()){val item=items.getJSONObject(i);merged[item.getString("submission_id")]=item}
        db.readableDatabase.rawQuery("SELECT submission_id,payload_json FROM youtube_reward_review_receipts WHERE account_id=?",arrayOf(account)).use{c->
            while(c.moveToNext())merged[c.getString(0)]=JSONObject(c.getString(1))
        }
        return JSONArray().apply{merged.values.forEach{put(it)}}
    }
    private fun send(b:Binding,path:String,payload:JSONObject?=null):JSONObject {
        val conn=URL(b.origin+"/v1/rewards/youtube/"+path).openConnection() as HttpURLConnection
        try {
            conn.instanceFollowRedirects=false;conn.connectTimeout=5000;conn.readTimeout=10000
            conn.setRequestProperty("Authorization","Bearer ${b.token}");conn.setRequestProperty("X-Device-Id",b.device)
            if(payload!=null){conn.requestMethod="POST";conn.doOutput=true;conn.setRequestProperty("Content-Type","application/json");conn.outputStream.use{it.write(payload.toString().toByteArray())}}
            val code=conn.responseCode
            val input=if(code in 200..299)conn.inputStream else conn.errorStream
            val raw=input?.bufferedReader()?.use { r->
                val s=StringBuilder();val buffer=CharArray(4096)
                while(true){val n=r.read(buffer);if(n<0)break;s.append(buffer,0,n);require(s.length<=500_000)};s.toString()
            }.orEmpty()
            val reply=JSONObject(raw);check(code==200 && reply.optBoolean("success")){reply.optString("error","Shared reward service unavailable")}
            check(reply.optString("account_id")==b.account){"Reward account mismatch"}
            return reply
        } finally {conn.disconnect()}
    }
    fun tick(context:Context) {
        val db=StrictRecordsLocalDb.getInstance(context)
        isYouTubeActive(context)
        if(SystemClock.elapsedRealtime()-lastAttempt>=10_000)request(context,db)
    }
    fun isYouTubeActive(context:Context?):Boolean {
        if(context==null)return false
        val b=binding(StrictRecordsLocalDb.getInstance(context))
        val active=lease.remaining(b?.scope.orEmpty(),SystemClock.elapsedRealtime())>0 &&
            b!=null && data.optJSONObject("active_session")?.optString("active_client_id")==b.device
        if(!active && wasActive)changed(context,false)
        return active
    }
    fun remainingSeconds(context:Context?):Int {
        if(context==null)return 0
        val b=binding(StrictRecordsLocalDb.getInstance(context)) ?: return 0
        if(data.optJSONObject("active_session")?.optString("active_client_id")!=b.device)return 0
        return (lease.remaining(b.scope,SystemClock.elapsedRealtime())/1000L).toInt().coerceAtLeast(0)
    }
    internal fun historyFreeBoundary():Boolean = SystemClock.elapsedRealtime()-boundaryAt in 0..2000L
    private fun changed(context:Context,active:Boolean) {
        wasActive=active;boundaryAt=SystemClock.elapsedRealtime()
        main.post {
            // A reward boundary must not rearm a dismissed shield, generate
            // events or trigger a restriction on Instagram/music/another app.
            BackgroundMusicRestrictionManager.onRestrictionStateChanged(context, if (active) "youtube_reward_activated" else "youtube_reward_expired")
            val service=StrictAccessibilityService.activeInstance
            val pkg=service?.confirmedForegroundTargetPackage()
            if(pkg!=null && TargetAppMonitoringPolicy.targetForPackage(pkg)?.id=="youtube") {
                UsageAccessMonitor.onScreenUnavailable()
                StrictBotOverlayController.prepareDemoAccessRecheck(context)
                service?.onYouTubeRewardChanged()
            }
            // The existing bounded monitor reads reward eligibility each tick;
            // its actual-score cache needs no global invalidation for a reward.
        }
    }
    private fun receipt(context:Context,db:StrictRecordsLocalDb,b:Binding,reply:JSONObject,started:Long) {
        check(binding(db)?.scope==b.scope){"Reward account changed"}
        val active=reply.optJSONObject("active_session")
        val ok=active!=null && active.optString("account_id")==b.account && active.optString("state")=="ACTIVE" &&
            lease.accept(b.scope,active.optLong("activation_ms"),active.optLong("expiry_ms"),reply.getLong("server_now_ms"),started,SystemClock.elapsedRealtime())
        if(!ok)lease.clear()
        data=reply;verifiedScope=b.scope
        val permitted=ok && active?.optString("active_client_id")==b.device
        if(permitted!=wasActive)changed(context,permitted)
        expiryTask?.let{main.removeCallbacks(it)}
        if(ok) {
            expiryTask=Runnable{isYouTubeActive(context)}
            main.postDelayed(expiryTask!!,lease.remaining(b.scope,SystemClock.elapsedRealtime()).coerceAtLeast(1))
        }
    }
    private fun request(context:Context,db:StrictRecordsLocalDb,action:String="state",reward:String?=null,callback:((Result<RewardStatusResponse>)->Unit)?=null) {
        val b=binding(db)
        lastAttempt=SystemClock.elapsedRealtime()
        if(b==null){callback?.invoke(Result.failure(IllegalStateException("Shared HTTPS reward service is not configured. Real points are unchanged.")));return}
        if(!busy.compareAndSet(false,true)){callback?.invoke(Result.failure(IllegalStateException("Reward synchronization in progress; retry shortly.")));return}
        lastAttempt=SystemClock.elapsedRealtime()
        worker.execute {
            try {
                if(action=="state") {
                    val all=candidates(db,b.account)
                    for(start in 0 until all.length() step 100) {
                        val batch=JSONArray();for(i in start until minOf(start+100,all.length()))batch.put(all.get(i))
                        send(b,"reconcile",JSONObject().put("submissions",batch))
                    }
                }
                val started=SystemClock.elapsedRealtime()
                val reply=send(b,action,if(action=="state")null else JSONObject().put("reward_id",reward))
                receipt(context,db,b,reply,started)
                val active=reply.optJSONObject("active_session")?.takeIf{lease.remaining(b.scope,SystemClock.elapsedRealtime())>0}?.let{parseRewardJson(it).copy(remainingSeconds=(lease.remaining(b.scope,SystemClock.elapsedRealtime())/1000).toInt())}
                val available=reply.optJSONArray("available_rewards") ?: JSONArray()
                val list=(0 until available.length()).map{parseRewardJson(available.getJSONObject(it))}
                main.post{callback?.invoke(Result.success(RewardStatusResponse(true,active,list,list.size)))}
            } catch(e:Exception) {
                lease.clear();if(wasActive)changed(context,false)
                main.post{callback?.invoke(Result.failure(e))}
            } finally {busy.set(false)}
        }
    }
    fun fetchRewardStatus(db:StrictRecordsLocalDb,serverEndpoint:String,accountId:String,callback:(Result<RewardStatusResponse>)->Unit) =
        request(db.appContextForRewards(),db,callback=callback)
    fun startSession(db:StrictRecordsLocalDb,serverEndpoint:String,rewardId:String,clientId:String="ANDROID",callback:(Result<JSONObject>)->Unit) {
        request(db.appContextForRewards(),db,"activate",rewardId){ result->callback(result.map{data}) }
    }
    fun stopOrExpireSession(db:StrictRecordsLocalDb,serverEndpoint:String,rewardId:String,clientId:String="ANDROID",isStop:Boolean=true,callback:(Result<JSONObject>)->Unit) {
        request(db.appContextForRewards(),db,if(isStop)"stop" else "state",rewardId){ result->callback(result.map{data}) }
    }
}
