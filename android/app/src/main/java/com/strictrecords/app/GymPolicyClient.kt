package com.strictrecords.app

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

/** The existing trusted backend owns daily choices, activations and the weekly balance. */
object GymPolicyClient {
    private val refreshing = AtomicBoolean(false)
    @Volatile private var receivedAt = 0L
    @Volatile private var receivedAccount = ""
    private fun account(db: StrictRecordsLocalDb) = db.getDeviceRegistration()?.optString("account_id").orEmpty().ifBlank { db.getConfigValue("account_id","user_lok") }
    private fun stateIsCurrent(db: StrictRecordsLocalDb,incoming: JSONObject): Boolean {
        val cached=state(db)
        val savedRevision=maxOf(decision(db,db.nowDate()).optInt("revision"),cached.optJSONObject("decision")?.optInt("revision") ?: 0)
        return PlanningSyncRules.mayReplaceState(savedRevision,incoming.optJSONObject("decision")?.optInt("revision") ?: 0,
            db.parseIsoToMs(cached.optString("server_time")),db.parseIsoToMs(incoming.optString("server_time")))
    }
    private fun key(db: StrictRecordsLocalDb, date: String) = "gym_daily_decision_${account(db)}_$date"
    fun pendingKey(db: StrictRecordsLocalDb, date: String) = "gym_decision_pending_${account(db)}_$date"
    private fun collegeKey(db: StrictRecordsLocalDb,date: String) = "college_authoritative_${account(db)}_$date"
    private fun collegePendingKey(db: StrictRecordsLocalDb,date: String) = "college_pending_${account(db)}_$date"
    fun collegeDecision(db: StrictRecordsLocalDb,date: String): JSONObject = runCatching { JSONObject(db.getConfigValue(collegeKey(db,date),"{}")) }.getOrDefault(JSONObject())
    fun collegePlan(db: StrictRecordsLocalDb,date: String,occurrence: Int,isCollege: Boolean): JSONObject? {
        if (db.getConfigValue("college_plan_locally_edited_$date","false") == "true") return null
        val record=collegeDecision(db,date)
        if (record.optBoolean("attends_college") != isCollege) return null
        return record.optJSONArray("bath_plans")?.optJSONObject(occurrence-1)
    }
    fun collegePending(db: StrictRecordsLocalDb,date: String) = db.getConfigValue(collegePendingKey(db,date),"").isNotBlank()
    fun pendingBluetoothRequests(db: StrictRecordsLocalDb): org.json.JSONArray {
        val result=org.json.JSONArray()
        fun add(kind: String,key: String) {
            val raw=db.getConfigValue(key,"")
            if (raw.isBlank()) return
            val body=runCatching { JSONObject(raw) }.getOrNull() ?: return
            if (!body.optBoolean("conflict") && body.optString("account_id")==account(db))
                result.put(JSONObject().put("kind",kind).put("payload",body))
        }
        for (date in listOf(db.nowDate(),db.tomorrowDate())) {
            add("GYM_DECISION",pendingKey(db,date));add("COLLEGE_DECISION",collegePendingKey(db,date))
            add("GYM_ACTIVATE","gym_activation_pending_${account(db)}_$date")
        }
        add("GYM_OFF","gym_off_pending_${account(db)}")
        return result
    }

    /** Called only after both receipt signatures and the exact planning hash pass. */
    fun acceptBluetoothPlanning(db: StrictRecordsLocalDb,receipt: JSONObject) {
        val payload=JSONObject(receipt.getString("planning_payload"))
        val results=payload.optJSONArray("results") ?: org.json.JSONArray()
        for (index in 0 until results.length()) {
            val ack=results.getJSONObject(index)
            val date=ack.optString("date")
            val kind=ack.optString("kind")
            val configKey=when(kind) {
                "GYM_DECISION" -> pendingKey(db,date)
                "COLLEGE_DECISION" -> collegePendingKey(db,date)
                "GYM_ACTIVATE" -> "gym_activation_pending_${account(db)}_$date"
                "GYM_OFF" -> "gym_off_pending_${account(db)}"
                else -> continue
            }
            val raw=db.getConfigValue(configKey,"")
            val original=runCatching { JSONObject(raw) }.getOrNull() ?: continue
            if (original.optString("request_id")!=ack.optString("request_id")) continue
            if (ack.optBoolean("accepted")) {
                db.setConfigValue(configKey,"")
                if (kind == "GYM_ACTIVATE") db.markGymModeActivationSyncResult(ack.optString("request_id"), true, canonicalSessionId = ack.optString("session_id"))
            } else {
                db.setConfigValue(configKey,original.put("conflict",true).put("error",ack.optString("error")).toString())
                if (kind == "GYM_ACTIVATE") db.markGymModeActivationSyncResult(ack.optString("request_id"), false, conflictReason = ack.optString("error"))
            }
        }
        payload.optJSONObject("planning")?.let { accept(db,JSONObject().put("success",true).put("planning",it)) }
        payload.optJSONObject("state")?.let { state ->
            if (state.optString("account_id")==account(db) && state.optString("date")==db.nowDate() && stateIsCurrent(db,state)) {
                db.setConfigValue("gym_authoritative_state_${account(db)}",state.toString())
                db.updateGymModeLivesAuthoritative(
                    state.optInt("lives_balance").coerceIn(0,ProductPolicy.GYM_MAX_LIVES),
                    db.parseIsoToMs(state.optString("server_time"))
                )
            }
        }
    }
    fun queueCollegeChoice(db: StrictRecordsLocalDb,date: String,answer: String) {
        val attends=answer == "YES"
        val plans=org.json.JSONArray()
        val configuredCount = db.getConfigValue("bath_session_count_$date", "").toIntOrNull()
        val count = when {
            configuredCount != null && configuredCount in 1..2 -> configuredCount
            attends -> db.getMaxBathOccurrences(date).coerceIn(1, 2)
            db.hasConfiguredNonCollegeBathTime(date) -> db.getMaxBathOccurrences(date).coerceIn(1, 2)
            else -> 0
        }
        for (occurrence in 1..count) {
            val window=BathWindowRules.calculateBathWindow(occurrence,attends,date,db.getPlannedBathTime(occurrence,attends,date),
                windowStartOverride=if (attends) null else (if (occurrence == 2) "12:00" else db.getNonCollegeBathWindowStart()),
                windowEndOverride=if (attends) null else (if (occurrence == 2) "22:00" else db.getNonCollegeBathWindowEnd()),timezone=db.scheduleTimezone(date),
                durationMinutes=db.getBathActivityDurationMinutes(occurrence,attends,date))
            plans.put(JSONObject().put("occurrence",occurrence).put("planned_time",window.plannedHhmm)
                // College-plan fields describe the allowed planning range, not the later photo window.
                .put("window_start",window.windowStartHhmm)
                .put("window_end",window.windowEndHhmm)
                .put("activity_duration_minutes",((window.reservedEndMs-window.reservedStartMs)/60_000L).toInt()))
        }
        val body=JSONObject().put("account_id",account(db)).put("date",date).put("attends_college",attends)
            .put("expected_revision",collegeDecision(db,date).optInt("revision")).put("request_id",java.util.UUID.randomUUID().toString())
            .put("timezone",db.scheduleTimezone()).put("bath_plans",plans)
        db.setConfigValue(collegePendingKey(db,date),body.toString())
    }

    private fun acceptCollege(db: StrictRecordsLocalDb,record: JSONObject) {
        val date=record.optString("date")
        if (record.optString("account_id") != account(db) || date.isBlank() || record.optInt("revision") < collegeDecision(db,date).optInt("revision")) return
        val changed=record.toString()!=collegeDecision(db,date).toString()
        db.setConfigValue(collegeKey(db,date),record.toString())
        if (record.optInt("revision")==0 && !collegePending(db,date) && db.getCollegeAttendance(date) in listOf("YES","NO")) {
            queueCollegeChoice(db,date,db.getCollegeAttendance(date))
        }
        if (changed && !collegePending(db,date) && !record.isNull("attends_college") && record.has("attends_college")) {
            db.setConfigValue("college_plan_locally_edited_$date","false")
            val attends=record.getBoolean("attends_college")
            val plans=record.optJSONArray("bath_plans") ?: org.json.JSONArray()
            if (plans.length() in 1..2) db.setConfigValue("bath_session_count_$date", plans.length().toString())
            if (!attends && plans.length()==0) db.setConfigValue("bath_planned_time_non_college_$date","")
            for (index in 0 until plans.length()) {
                val plan=plans.getJSONObject(index)
                val configKey=if (attends) "bath_planned_time_${index+1}_$date" else if (index == 0) "bath_planned_time_non_college_$date" else "bath_planned_time_non_college_2_$date"
                db.setConfigValue(configKey,plan.getString("planned_time"))
                val duration=plan.optInt("activity_duration_minutes",if(attends && index == 0) 30 else 60)
                if(duration in 1..180) db.setConfigValue("bath_activity_duration_${index+1}_$date",duration.toString())
            }
            db.setCollegeAttendance(date,if (record.getBoolean("attends_college")) "YES" else "NO",fromServer=true)
            for(index in 0 until plans.length()) db.ensureBathOccurrence(date,index+1)
        }
    }
    fun decision(db: StrictRecordsLocalDb, date: String): JSONObject = runCatching {
        JSONObject(db.getConfigValue(key(db,date), "{}"))
    }.getOrDefault(JSONObject()).apply {
        if (!has("revision")) put("revision", 0)
        if (!has("status")) put("status", "GYM_DECISION_REQUIRED")
    }
    fun state(db: StrictRecordsLocalDb): JSONObject = runCatching {
        JSONObject(db.getConfigValue("gym_authoritative_state_${account(db)}", "{}"))
    }.getOrDefault(JSONObject())

    private fun request(db: StrictRecordsLocalDb, path: String, body: JSONObject? = null): JSONObject {
        val registration = db.getDeviceRegistration() ?: error("Register this phone with the PC first")
        val endpoint = registration.optString("server_endpoint").trimEnd('/')
        require(endpoint.isNotBlank()) { "The registered PC address is unavailable" }
        val connection = URL(endpoint + path).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 4000; connection.readTimeout = 5000
            connection.setRequestProperty("X-Device-Id", registration.getString("device_id"))
            connection.setRequestProperty("X-Device-Token", registration.getString("device_token"))
            connection.setRequestProperty("Content-Type", "application/json")
            if (body != null) {
                connection.requestMethod = "POST"; connection.doOutput = true
                connection.outputStream.bufferedWriter().use { it.write(body.toString()) }
            }
            val response = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
            return response?.bufferedReader()?.use { JSONObject(it.readText()) }
                ?: JSONObject().put("success", false).put("error", "PC did not confirm this request")
        } finally { connection.disconnect() }
    }
    private fun accept(db: StrictRecordsLocalDb, response: JSONObject) {
        if (!response.optBoolean("success")) return
        response.optJSONObject("decision")?.let { incoming ->
            val date = incoming.optString("date")
            if (incoming.optString("account_id")==account(db) && date.isNotBlank() && incoming.optInt("revision") >= decision(db, date).optInt("revision"))
                db.setConfigValue(key(db,date), incoming.toString())
        }
        response.optJSONObject("planning")?.let { planning ->
            for (date in planning.keys()) {
                val day=planning.getJSONObject(date)
                day.optJSONObject("gym")?.let { accept(db,JSONObject().put("success",true).put("decision",it)) }
                day.optJSONObject("college")?.let { acceptCollege(db,it) }
            }
        }
        response.optJSONObject("college_decision")?.let { acceptCollege(db,it) }
        response.optJSONObject("state")?.let { state ->
            if (state.optString("date") != db.nowDate() || state.optString("account_id") != account(db) || !stateIsCurrent(db,state)) return@let
            db.setConfigValue("gym_authoritative_state_${account(db)}", state.toString())
            db.updateGymModeLivesAuthoritative(
                state.optInt("lives_balance").coerceIn(0,ProductPolicy.GYM_MAX_LIVES),
                db.parseIsoToMs(state.optString("server_time"))
            )
            state.optJSONObject("decision")?.let { incomingDec ->
                if (db.getConfigValue(pendingKey(db, db.nowDate()), "").isBlank()) {
                    db.setConfigValue(key(db,db.nowDate()), incomingDec.toString())
                }
            }
            receivedAt = android.os.SystemClock.elapsedRealtime()
            receivedAccount = account(db)
        }
    }
    fun refresh(db: StrictRecordsLocalDb) {
        if (!db.isDeviceRegistered() || !refreshing.compareAndSet(false,true)) return
        try {
            val offKey="gym_off_pending_${account(db)}"
            val pendingOff=db.getConfigValue(offKey,"")
            if (pendingOff.isNotBlank()) {
                val response=request(db,"/api/gym_mode/deactivate",JSONObject(pendingOff))
                if (response.optBoolean("success")) {
                    if (db.getConfigValue(offKey,"")==pendingOff) db.setConfigValue(offKey,"")
                    accept(db,response)
                }
            }
            for (date in listOf(db.nowDate(), db.tomorrowDate())) {
                val pendingKey = pendingKey(db,date)
                val pending = db.getConfigValue(pendingKey, "")
                if (pending.isNotBlank()) {
                    val body = JSONObject(pending)
                    if (!body.optBoolean("conflict")) {
                        val result = request(db, "/api/gym/decision", body)
                        if (result.optBoolean("success")) { accept(db,result); if(db.getConfigValue(pendingKey,"")==pending) db.setConfigValue(pendingKey, "") }
                        else if (result.optString("code") in listOf("REVISION_CONFLICT","ADMIN_REQUIRED","HISTORICAL_DATE")) {
                            body.put("conflict",true).put("error",result.optString("error"))
                            if(db.getConfigValue(pendingKey,"")==pending) db.setConfigValue(pendingKey,body.toString())
                        }
                    }
                }
                val actPendingKey = "gym_activation_pending_${account(db)}_$date"
                val pendingAct = db.getConfigValue(actPendingKey, "")
                if (pendingAct.isNotBlank()) {
                    val body = JSONObject(pendingAct)
                    if (!body.optBoolean("conflict")) {
                        val actResp = request(db, "/api/gym_mode/reconcile_offline", body)
                        if (actResp.optBoolean("success")) {
                            if (db.getConfigValue(actPendingKey, "") == pendingAct) db.setConfigValue(actPendingKey, "")
                            db.markGymModeActivationSyncResult(body.optString("request_id"), true, canonicalSessionId = actResp.optString("session_id"))
                            accept(db, actResp)
                        } else if (actResp.optString("code") in listOf("CONFLICT", "REVISION_CONFLICT", "NO_LIVES", "WINDOW_CLOSED", "UNAUTHORIZED")) {
                            body.put("conflict", true).put("error", actResp.optString("error"))
                            if (db.getConfigValue(actPendingKey, "") == pendingAct) db.setConfigValue(actPendingKey, body.toString())
                            db.markGymModeActivationSyncResult(body.optString("request_id"), false, conflictReason = actResp.optString("error"))
                        }
                    }
                }
                val collegeKey=collegePendingKey(db,date)
                val pendingCollege=db.getConfigValue(collegeKey,"")
                if (pendingCollege.isNotBlank()) {
                    val body=JSONObject(pendingCollege)
                    if (!body.optBoolean("conflict")) {
                        val response=request(db,"/api/planning/college",body)
                        if (response.optBoolean("success")) {
                            if (db.getConfigValue(collegeKey,"")==pendingCollege) db.setConfigValue(collegeKey,"")
                            accept(db,response)
                        } else if (response.optString("code") in listOf("REVISION_CONFLICT","UNAUTHORIZED","HISTORICAL_DATE") && db.getConfigValue(collegeKey,"")==pendingCollege)
                            db.setConfigValue(collegeKey,body.put("conflict",true).put("error",response.optString("error")).toString())
                    }
                }
            }
            accept(db, request(db, "/api/gym_mode/status"))
        } catch (_: Exception) {
            // Pending choices and offline activations remain safely queued for retry.
        } finally { refreshing.set(false) }
    }
    fun save(db: StrictRecordsLocalDb, date: String, planned: Boolean): JSONObject {
        val currentDec = decision(db, date)
        val expectedRev = currentDec.optInt("revision")
        val body = JSONObject().put("date",date).put("gym_planned",planned).put("timezone",db.scheduleTimezone())
            .put("account_id",account(db))
            .put("expected_revision",expectedRev).put("request_id",java.util.UUID.randomUUID().toString())
        val localDec = JSONObject().apply {
            put("account_id", account(db))
            put("date", date)
            put("gym_planned", planned)
            put("status", if (planned) "GYM_PLANNED" else "GYM_NOT_PLANNED")
            put("revision", expectedRev)
            put("sync_status", "PENDING_PC_SYNC")
            put("timezone", db.scheduleTimezone())
        }
        db.setConfigValue(key(db, date), localDec.toString())
        db.setConfigValue(pendingKey(db,date),body.toString())
        if (!planned && date == db.nowDate()) {
            db.deactivateGymModeEarly()
        }
        return try {
            val response = request(db,"/api/gym/decision",body)
            if (response.optBoolean("success")) {
                accept(db,response); if(db.getConfigValue(pendingKey(db,date),"")==body.toString()) db.setConfigValue(pendingKey(db,date),"")
            } else if (response.optString("code") in listOf("REVISION_CONFLICT","ADMIN_REQUIRED")) {
                val original=body.toString()
                body.put("conflict",true).put("error",response.optString("error"))
                if(db.getConfigValue(pendingKey(db,date),"")==original) db.setConfigValue(pendingKey(db,date),body.toString())
            }
            response
        } catch (_: Exception) {
            JSONObject()
                .put("success", true)
                .put("offline_saved", true)
                .put("decision", localDec)
                .put("message", if (planned) "Saved locally (Gym planned) · PC sync queued. Offline GYM Mode available after 5:00 PM." else "Saved locally (No Gym) · PC sync queued.")
        }
    }
    fun isMusicAllowed(db: StrictRecordsLocalDb, nowMs: Long): Boolean {
        val elapsed = android.os.SystemClock.elapsedRealtime()
        if (receivedAt == 0L || receivedAccount != account(db) || elapsed - receivedAt !in 0L until ProductPolicy.GYM_STATUS_LEASE_MS ||
            db.getConfigValue(pendingKey(db,db.nowDate()), "").isNotBlank() ||
            db.getConfigValue("gym_music_locally_off", "false") == "true") return false
        val state = state(db)
        val choice = state.optJSONObject("decision") ?: return false
        val session = state.optJSONObject("active_session") ?: return false
        return state.optString("date") == db.nowDate() && choice.optBoolean("gym_planned",false) && state.optBoolean("is_active") &&
            nowMs >= session.optLong("activation_time_ms", Long.MAX_VALUE) && nowMs < session.optLong("access_expiry_ms",0)
    }
    fun activeSession(db: StrictRecordsLocalDb, nowMs: Long): GymModeRules.GymModeSession? =
        if (isMusicAllowed(db,nowMs)) state(db).optJSONObject("active_session")?.let { GymModeRules.GymModeSession.fromJson(it) } else null

    fun activate(db: StrictRecordsLocalDb, activityId: String): GymModeRules.ActivationResult = try {
        refresh(db)
        check(db.getConfigValue(pendingKey(db,db.nowDate()), "").isBlank()) { "Confirm the daily Gym decision with the PC first" }
        val response = request(db,"/api/gym_mode/activate", JSONObject().put("gym_activity_id",activityId)
            .put("expected_revision",decision(db,db.nowDate()).optInt("revision")).put("request_id","gym_activate_$activityId"))
        if (response.optBoolean("success")) {
            db.setConfigValue("gym_music_locally_off","false"); accept(db,response)
            val active=activeSession(db,db.authoritativeNowMs())
            GymModeRules.ActivationResult(active!=null,if(active!=null) "GYM Mode confirmed by the PC" else "This activation is no longer active; no music access granted",active)
        } else { accept(db,JSONObject().put("success",true).put("state",response.optJSONObject("state")))
            GymModeRules.ActivationResult(false,response.optString("error","Activation was not confirmed")) }
    } catch (error: Exception) { GymModeRules.ActivationResult(false,error.message ?: "PC unavailable; no music exception granted") }

    fun deactivate(db: StrictRecordsLocalDb): Boolean {
        db.setConfigValue("gym_music_locally_off","true")
        val session=state(db).optJSONObject("active_session")?.optString("session_id").orEmpty()
        if (session.isNotBlank()) db.setConfigValue("gym_off_pending_${account(db)}",JSONObject()
            .put("account_id",account(db)).put("session_id",session).put("request_id","gym_off_$session").toString())
        Thread({ refresh(db) },"GymMode-Off").start()
        return true
    }
}
