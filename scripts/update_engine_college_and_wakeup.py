import sys

engine_path = 'android/app/src/main/java/com/strictrecords/app/StrictRecordsLocalEngine.kt'
with open(engine_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update hourly slot generation for college day
target_slots = 'val slots = HourlyWindowRules.slots(date, wakeTarget, scheduleZone)'
replacement_slots = '''val isCollegeDay = getCollegeAttendance(date) == "YES"
            val slots = if (isCollegeDay) {
                HourlyWindowRules.collegeDaySlots(date, scheduleZone)
            } else {
                HourlyWindowRules.slots(date, wakeTarget, scheduleZone)
            }'''

assert target_slots in content, "target_slots not found"
content = content.replace(target_slots, replacement_slots, 1)
print("Updated collegeDaySlots in StrictRecordsLocalEngine.kt")

# 2. In reconcileHourlyWakeUpConflicts, prevent suppressing college 09:00-10:00 first hourly window
target_reconcile = '''                    val slotTargetMs = parseIsoToMs(schedIso)
                    val isMorningSlot = slotTargetMs != Long.MIN_VALUE && slotTargetMs <= morningSlotsCutoffMs'''

replacement_reconcile = '''                    val slotTargetMs = parseIsoToMs(schedIso)
                    val isCollegeDayNow = getCollegeAttendance(date) == "YES"
                    val isCollegeFirstHourly = isCollegeDayNow && (details.optString("hour_slot") == "09:00-10:00" || actId.contains("0900-HOURLY"))
                    val isMorningSlot = !isCollegeFirstHourly && slotTargetMs != Long.MIN_VALUE && slotTargetMs <= morningSlotsCutoffMs'''

assert target_reconcile in content, "target_reconcile not found"
content = content.replace(target_reconcile, replacement_reconcile, 1)
print("Updated reconcileHourlyWakeUpConflicts in StrictRecordsLocalEngine.kt")

# 3. Add isWakeUpVerificationCompleted and finalizeWakeUpVerification
target_methods = '    fun completeBath(date: String, isDevMode: Boolean, mode: String, completedHhmm: String = nowTime()) {'

new_methods = '''    fun isWakeUpVerificationCompleted(date: String = nowDate()): Boolean {
        val st = getDayState(date, false).optString("wakeup_status", "").uppercase(Locale.US)
        if (st in listOf("VERIFIED", "COMPLETED", "APPROVED")) return true

        val flow = getWakeUpFlowStatus(date, false)
        if (flow.optBoolean("all_completed", false) || (flow.optBoolean("step1_camera", false) && (st in listOf("VERIFIED", "COMPLETED", "PENDING_SERVER")))) {
            return true
        }

        val hasApprovedReview = readableDatabase.rawQuery(
            "SELECT 1 FROM proof_reviews WHERE (task_code IN ('WAKE_UP', 'WAKEUP') OR activity_id LIKE '%WAKEUP%') AND date = ? AND status IN ('APPROVED', 'VERIFIED')",
            arrayOf(date)
        ).use { it.moveToFirst() }
        if (hasApprovedReview) return true

        val hasVerifiedAct = readableDatabase.rawQuery(
            "SELECT 1 FROM scheduled_activities WHERE date = ? AND (type = 'WAKE_UP' OR activity_id LIKE '%WAKEUP%') AND status IN ('VERIFIED', 'COMPLETED', 'APPROVED')",
            arrayOf(date)
        ).use { it.moveToFirst() }
        return hasVerifiedAct
    }

    fun finalizeWakeUpVerification(date: String = nowDate(), isDevMode: Boolean = false): Pair<Boolean, String> {
        ensureDay(date, isDevMode)
        val state = getDayState(date, isDevMode)
        val flow = getWakeUpFlowStatus(date, isDevMode)
        val attemptId = flow.optString("attempt_id")
        val camera = flow.optJSONObject("liveness_metadata") ?: JSONObject()
        val photoPath = state.optString("wakeup_camera_path")
        val photo = File(photoPath)

        // Mandatory check: Camera face/liveness verification must have passed
        if (!flow.optBoolean("step1_camera") || !photo.isFile || photo.length() <= 0) {
            setWakeConfig("wakeup_submission_error", date, isDevMode, "CAMERA_PROOF_INCOMPLETE")
            return Pair(false, "Camera face verification is still pending. Complete the 20-second camera verification first.")
        }

        val step2Done = flow.optBoolean("step2_bluetooth", false)
        val step3Done = flow.optBoolean("step3_motivation_track", false)

        if (isDevMode) {
            val store = writableDatabase
            store.beginTransaction()
            try {
                store.execSQL(
                    "UPDATE daily_state SET wakeup_track=1, wakeup_track_pct=100, wakeup_status='DEMO_COMPLETED', wakeup_completed_at=? WHERE date=? AND is_dev_mode=1",
                    arrayOf(nowTime(), date)
                )
                setWakeConfig("wakeup_submission_error", date, isDevMode, "")
                store.setTransactionSuccessful()
            } finally {
                store.endTransaction()
            }
            logStructured(date, true, "WAKEUP_DEMO_COMPLETED", "Wake-up verification completed in Demo Test Mode (audio/earbuds optional); zero score impact.")
            return Pair(true, "Demo verification complete! (Zero score impact • No points awarded)")
        }

        // Production verification
        val evidenceId = getConfigValue("wakeup_evidence_id_$date", "").ifBlank {
            "ev_wake_${date.replace("-", "")}_${System.currentTimeMillis() % 100000}".also {
                setConfigValue("wakeup_evidence_id_$date", it)
            }
        }
        val playback = runCatching { JSONObject(getWakeConfig("wakeup_playback_telemetry", date, isDevMode, "{}")) }.getOrDefault(JSONObject())
        val payload = JSONObject().apply {
            put("date", date)
            put("attempt_id", attemptId)
            put("completed_at", formatMsToIso(authoritativeNowMs()))
            put("bt_device", getEarbudsConfig())
            put("track_name", "Motivation Track (Optional)")
            put("completion_pct", if (step3Done) 100.0 else 0.0)
            put("evidence_id", evidenceId)
            put("photo_liveness_passed", true)
            put("earbuds_route_passed", step2Done)
            put("active_audio_output_verified", step2Done)
            put("full_track_passed", step3Done)
            put("audio_optional", true)
            put("earbuds_optional", true)
            put("playback_session", playback)
            put("liveness_metadata", camera)
            if (photo.isFile) {
                put("image_base64", Base64.encodeToString(photo.readBytes(), Base64.NO_WRAP))
            }
        }
        val eventId = "evt_wake_" + UUID.nameUUIDFromBytes(attemptId.toByteArray()).toString()
        val store = writableDatabase
        store.beginTransaction()
        try {
            enqueueSyncEvent("WAKEUP_VERIFIED", payload, idempotencyKey = eventId)
            store.execSQL(
                "UPDATE daily_state SET wakeup_track=1, wakeup_track_pct=100, wakeup_status='PENDING_SERVER', wakeup_completed_at=? WHERE date=? AND is_dev_mode=0",
                arrayOf(nowTime(), date)
            )
            setConfigValue("wakeup_submission_event_$attemptId", eventId)
            setWakeConfig("wakeup_submission_error", date, isDevMode, "")
            store.setTransactionSuccessful()
        } finally {
            store.endTransaction()
        }
        reconcileHourlyWakeUpConflicts(date)
        if (isDeviceRegistered()) Thread { runCatching { flushSyncEventsQueue(appContext) } }.start()
        logStructured(date, false, "WAKEUP_STEPS_COMPLETED", "Wake-up verified (audio/earbuds optional); awaiting server sync")
        return Pair(true, "Wake-up verification completed successfully.")
    }

''' + target_methods

assert target_methods in content, "target_methods not found"
content = content.replace(target_methods, new_methods, 1)
print("Added isWakeUpVerificationCompleted and finalizeWakeUpVerification to StrictRecordsLocalEngine.kt")

with open(engine_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Saved StrictRecordsLocalEngine.kt")
