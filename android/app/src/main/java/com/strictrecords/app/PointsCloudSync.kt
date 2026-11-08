package com.strictrecords.app

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Internet transport for the existing APK-authoritative ledger. Never accepts AI balances. */
object PointsCloudSync {
    private const val JOB = 7341
    private val executor = Executors.newSingleThreadExecutor()
    private val busy = AtomicBoolean(false)
    private val eventFields = setOf("event_id","event_type","points","date","timestamp","eligible","rule_version","evidence_id","related_task_id","idempotency_key")
    private val auditFields = setOf("action","approval_source","approved_by","activity_id","submission_id","evidence_id","related_task_id")

    fun validOrigin(value: String): Boolean = try {
        val url = java.net.URI(value)
        url.scheme == "https" && !url.host.isNullOrBlank() && url.userInfo == null && url.query == null && url.fragment == null && (url.path.isNullOrBlank() || url.path == "/")
    } catch (_: Exception) { false }

    fun minimalEnvelope(original: JSONObject): JSONObject {
        val source = JSONObject(original.getString("payload_json"))
        val result = JSONObject()
        listOf("schema","account_id","device_id","date","timezone","revision","event_count","confirmed_score","raw_verified_points").forEach { result.put(it,source.get(it)) }
        result.put("is_dev_mode",false)
        val events = source.getJSONArray("score_events")
        val cleanEvents = JSONArray()
        for (i in 0 until events.length()) {
            val e=events.getJSONObject(i);val clean=JSONObject()
            eventFields.forEach { if(e.has(it)) clean.put(it,e.get(it)) }
            clean.put("idempotency_key",e.optString("idempotency_key").ifBlank { e.getString("event_id") })
            val audit=e.optJSONObject("audit") ?: JSONObject();val safe=JSONObject()
            auditFields.forEach { if(audit.has(it)) safe.put(it,audit.optString(it).take(256)) }
            val author=safe.optString("approved_by")
            if(author.isNotBlank() && author.lowercase() !in setOf("system","auto","unknown") && !author.startsWith("adminref_"))safe.put("approved_by","adminref_"+LedgerSyncRules.sha256(author).take(24))
            if(audit.optString("action")=="INITIAL_POINTS_RESET" && audit.optString("reason").isNotBlank()) safe.put("reason","Explicit user reset")
            clean.put("audit",safe);cleanEvents.put(clean)
        }
        result.put("score_events",cleanEvents)
        return LedgerSyncRules.envelope(result)
    }

    fun accepts(response:JSONObject,sent:JSONObject,previousRevision:Long):Boolean {
        val payload=JSONObject(sent.getString("payload_json"))
        return response.optString("account_id")==payload.optString("account_id") &&
            response.optString("date")==payload.optString("date") &&
            response.optString("accepted_source_sha256")==sent.optString("sha256") &&
            response.optLong("ledger_revision",-1)>=previousRevision &&
            response.optInt("confirmed_balance",Int.MIN_VALUE)==payload.optInt("confirmed_score") &&
            response.optString("score_authority")=="ANDROID"
    }

    fun schedule(context:Context) {
        val db=StrictRecordsLocalDb.getInstance(context)
        val endpoint=db.getConfigValue("points_cloud_origin","")
        val scheduler=context.getSystemService(Context.JOB_SCHEDULER_SERVICE) as JobScheduler
        if(!validOrigin(endpoint)){scheduler.cancel(JOB);return}
        scheduler.schedule(JobInfo.Builder(JOB,ComponentName(context,PointsCloudJobService::class.java))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPersisted(true)
            .setPeriodic(15*60*1000L).setBackoffCriteria(60_000L,JobInfo.BACKOFF_POLICY_EXPONENTIAL).build())
    }

    fun request(context:Context,finished:(Boolean)->Unit={}) {
        if(!busy.compareAndSet(false,true)){finished(false);return}
        val app=context.applicationContext
        executor.execute {
            var success=false
            try {
                val db=StrictRecordsLocalDb.getInstance(app)
                val origin=db.getConfigValue("points_cloud_origin","").trimEnd('/')
                if(!validOrigin(origin))return@execute
                val registration=db.getDeviceRegistration() ?: return@execute
                val device=registration.optString("device_id");val token=registration.optString("device_token")
                require(device.isNotBlank() && token.isNotBlank()) { "Existing StrictRecords device registration required" }
                val sent=minimalEnvelope(db.buildAuthoritativeLedgerSnapshot())
                val conn=URL("$origin/v1/points/checkpoint").openConnection() as HttpURLConnection
                try {
                    conn.requestMethod="POST";conn.connectTimeout=10_000;conn.readTimeout=20_000;conn.instanceFollowRedirects=false
                    conn.setRequestProperty("Content-Type","application/json")
                    conn.setRequestProperty("Authorization","Bearer $token");conn.setRequestProperty("X-Device-Id",device)
                    conn.doOutput=true;conn.outputStream.use{it.write(sent.toString().toByteArray(Charsets.UTF_8))}
                    if(conn.responseCode in listOf(401,403)){db.setConfigValue("points_cloud_status","AUTHENTICATION_REQUIRED");return@execute}
                    check(conn.responseCode==200){"Cloud backend unavailable or ledger reconciliation required"}
                    val text=conn.inputStream.use{input->val bytes=input.readBytesBounded(2_000_000);String(bytes,Charsets.UTF_8)}
                    val reply=JSONObject(text)
                    require(accepts(reply,sent,db.getConfigValue("points_cloud_revision","0").toLongOrNull()?:0L)) { "Cloud receipt did not match this ledger" }
                    // A receipt cannot overwrite the existing authoritative APK balance.
                    db.setConfigValue("points_cloud_revision",reply.getLong("ledger_revision").toString())
                    db.setConfigValue("points_cloud_last_sync",db.nowIso())
                    db.setConfigValue("points_cloud_pending",reply.optInt("pending_changes",0).toString())
                    db.setConfigValue("points_cloud_pending_proposals",reply.optJSONArray("pending_proposals")?.toString()?:"[]")
                    db.setConfigValue("points_cloud_reconciliation",reply.optJSONObject("reconciliation")?.optString("status")?:"NOT_SCHEDULED_YET")
                    db.setConfigValue("points_cloud_taufa",reply.optString("taufa_status","UNAVAILABLE"))
                    db.setConfigValue("points_cloud_status","SYNCED");success=true
                } finally { conn.disconnect() }
            } catch (_: Exception) {
                StrictRecordsLocalDb.getInstance(app).setConfigValue("points_cloud_status","OFFLINE_OR_RECONCILIATION_REQUIRED")
            } finally {busy.set(false);finished(success)}
        }
    }

    private fun java.io.InputStream.readBytesBounded(limit:Int):ByteArray {
        val out=java.io.ByteArrayOutputStream();val chunk=ByteArray(8192)
        while(true){val n=read(chunk);if(n<0)break;check(out.size()+n<=limit){"Response too large"};out.write(chunk,0,n)}
        return out.toByteArray()
    }
}

class PointsCloudJobService:JobService() {
    override fun onStartJob(params:JobParameters):Boolean {
        PointsCloudSync.request(applicationContext){success->jobFinished(params,!success)}
        return true
    }
    override fun onStopJob(params:JobParameters):Boolean=true
}
