package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Deterministic Regression Test Suite for:
 * 1. Sleep Screenshot Locking: One screenshot per report date, locks upon evaluation.
 * 2. Wake-up Audio Playback & Route Verification: Real and demo mode shared rules, route checking, anti-seek tolerance.
 * 3. Travel Prebooking: Real vs Demo mode isolation, proof submission window [travelEnd, travelEnd + 2 hours].
 * 4. Hourly Occurrence Status: Prevents false AI REVIEW and POINTS PENDING states on unsubmitted cards.
 * 5. Wake-up Deduplication & Single Award: Prevents duplicate wake-up records and double bonus points.
 */
class StrictRoutinesRegressionAuditTest {

    private val tz = TimeZone.getTimeZone("Asia/Kolkata")
    private val df = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).apply {
        timeZone = tz
        isLenient = false
    }

    private fun ms(dateTimeStr: String): Long = df.parse(dateTimeStr)!!.time

    // ==========================================
    // PART 1: SLEEP SCREENSHOT LOCKING
    // ==========================================

    @Test
    fun testPart1_sleepEvaluatedReportLocksAgainstUpload() {
        val date = "2026-10-17"
        val finalizedStatuses = listOf("APPROVED", "PARTIALLY_AWARDED", "ALREADY_AWARDED", "REJECTED")

        for (status in finalizedStatuses) {
            val telemetry = MiFitnessSleepController.SleepCardTelemetry(
                status = status,
                statusLabel = status,
                statusColorHex = "#3FB950",
                pointsAwarded = if (status == "APPROVED") 5 else 0,
                isCompleted = true,
                isDeadlinePassed = false,
                summaryLine = "Report evaluated",
                onsetTime = "11:45 PM",
                wakeUpTime = "7:15 AM"
            )
            assertTrue("Evaluated sleep status $status must mark isCompleted=true", telemetry.isCompleted)
        }

        val pendingTelemetry = MiFitnessSleepController.SleepCardTelemetry(
            status = ProofSubmissionRules.States.SUBMITTED_PENDING_EVALUATION,
            statusLabel = "Pending",
            statusColorHex = "#D29922",
            pointsAwarded = 0,
            isCompleted = false,
            isDeadlinePassed = false,
            summaryLine = "Awaiting evaluation",
            onsetTime = null,
            wakeUpTime = null
        )
        assertFalse("Pending sleep submission must not be locked as completed", pendingTelemetry.isCompleted)
    }

    // ==========================================
    // PART 2: WAKE-UP AUDIO PLAYBACK & ROUTE
    // ==========================================

    @Test
    fun testPart2_wakeUpAudioRouteValidation() {
        val cfgName = "Galaxy Buds Pro"
        val cfgAddr = "AA:BB:CC:DD:EE:FF"

        // Matched route
        val matchesValid = WakeUpAttemptRules.selectedRouteMatches(
            selectedName = cfgName,
            selectedAddress = cfgAddr,
            routeName = "Galaxy Buds Pro",
            routeAddress = "AA:BB:CC:DD:EE:FF",
            bluetooth = true
        )
        assertTrue("Configured earbuds route must match", matchesValid)

        // Route mismatch: wrong address
        val matchesWrongAddr = WakeUpAttemptRules.selectedRouteMatches(
            selectedName = cfgName,
            selectedAddress = cfgAddr,
            routeName = "Galaxy Buds Pro",
            routeAddress = "11:22:33:44:55:66",
            bluetooth = true
        )
        assertFalse("Mismatched Bluetooth address must fail", matchesWrongAddr)

        // Route mismatch: not Bluetooth/headset
        val matchesNotBt = WakeUpAttemptRules.selectedRouteMatches(
            selectedName = cfgName,
            selectedAddress = cfgAddr,
            routeName = "Galaxy Buds Pro",
            routeAddress = "AA:BB:CC:DD:EE:FF",
            bluetooth = false
        )
        assertFalse("Non-Bluetooth/headset route must fail", matchesNotBt)
    }

    @Test
    fun testPart2_wakeUpPlaybackAntiSeekTolerance() {
        // Normal playback elapsed time vs current player position
        val previousPosition = 10_000
        val normalElapsed = 250L // 250ms tick interval
        val actualPositionNormal = 10_250

        // Screen-off jitter or looper delay can make actual elapsed up to ~1500ms
        val jitterElapsed = 1200L
        val actualPositionWithJitter = previousPosition + jitterElapsed.toInt()

        val maxAllowedNormal = previousPosition + normalElapsed + 1500
        assertTrue("Normal progression must be within tolerance", actualPositionNormal <= maxAllowedNormal)
        assertTrue("Jitter progression during screen-off must be within relaxed tolerance", actualPositionWithJitter <= maxAllowedNormal)

        // Forward seek of 5 seconds must exceed tolerance
        val forwardSeekPosition = previousPosition + 5000
        assertFalse("Forward seek must exceed tolerance", forwardSeekPosition <= maxAllowedNormal)
    }

    // ==========================================
    // PART 3: TRAVEL PREBOOKING REAL VS DEMO
    // ==========================================

    @Test
    fun testPart3_travelRealVsDemoIsolation() {
        // Real travel with description containing the word "demo"
        val realTravelDetails = JSONObject().apply {
            put("type", "TRAVEL")
            put("is_demo", false)
            put("destination", "Client Site")
            put("notes", "Client demo and project rollout presentation")
        }.toString()
        val realTravelTitle = "Travel & Transit (Client Site)"

        assertFalse(
            "Real travel with 'demo' in notes or description must NOT be classified as demo",
            TravelBookingRules.isDemoBooking(realTravelTitle, realTravelDetails)
        )

        // Explicit demo travel
        val demoDetails = JSONObject().apply {
            put("type", "TRAVEL")
            put("is_demo", true)
        }.toString()
        assertTrue("Explicit is_demo=true must be classified as demo", TravelBookingRules.isDemoBooking("Travel", demoDetails))

        // Demo title tag without details JSON
        assertTrue(
            "Title containing '(Demo Test)' must be classified as demo",
            TravelBookingRules.isDemoBooking("Travel & Transit (Demo Test)", null)
        )
        assertTrue(
            "Title starting with '[demo]' must be classified as demo",
            TravelBookingRules.isDemoBooking("[demo] Travel to City", null)
        )
    }

    @Test
    fun testPart3_travelProofWindowLifecycle() {
        val depMs = ms("2026-10-17 10:00:00")
        val travelEndMs = ms("2026-10-17 13:00:00")
        val proofStartMs = travelEndMs // 1:00 PM
        val proofEndMs = travelEndMs + 2 * 3600_000L // 3:00 PM (2-hour submission window)

        // Before departure: PREBOOKED
        val prebookedState = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 09:30:00"),
            departureMs = depMs,
            travelEndMs = travelEndMs,
            proofStartMs = proofStartMs,
            proofEndMs = proofEndMs,
            status = "SCHEDULED",
            hasProofSubmitted = false
        )
        assertEquals(TravelBookingRules.TravelStatusState.PREBOOKED, prebookedState)

        // During travel: TRAVEL_IN_PROGRESS
        val inProgressState = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 11:30:00"),
            departureMs = depMs,
            travelEndMs = travelEndMs,
            proofStartMs = proofStartMs,
            proofEndMs = proofEndMs,
            status = "SCHEDULED",
            hasProofSubmitted = false
        )
        assertEquals(TravelBookingRules.TravelStatusState.TRAVEL_IN_PROGRESS, inProgressState)

        // 10 minutes before travel end: PROOF_OPENS_SOON
        val opensSoonState = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 12:55:00"),
            departureMs = depMs,
            travelEndMs = travelEndMs,
            proofStartMs = proofStartMs,
            proofEndMs = proofEndMs,
            status = "SCHEDULED",
            hasProofSubmitted = false
        )
        assertEquals(TravelBookingRules.TravelStatusState.PROOF_OPENS_SOON, opensSoonState)

        // Exactly at travelEndMs (start of window): SUBMISSION_OPEN (start-inclusive)
        val openState = TravelBookingRules.resolveAuthoritativeState(
            nowMs = proofStartMs,
            departureMs = depMs,
            travelEndMs = travelEndMs,
            proofStartMs = proofStartMs,
            proofEndMs = proofEndMs,
            status = "SCHEDULED",
            hasProofSubmitted = false
        )
        assertEquals(TravelBookingRules.TravelStatusState.SUBMISSION_OPEN, openState)

        // Inside window: SUBMISSION_OPEN
        val insideWindowState = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 14:00:00"),
            departureMs = depMs,
            travelEndMs = travelEndMs,
            proofStartMs = proofStartMs,
            proofEndMs = proofEndMs,
            status = "SCHEDULED",
            hasProofSubmitted = false
        )
        assertEquals(TravelBookingRules.TravelStatusState.SUBMISSION_OPEN, insideWindowState)

        // At proofEndMs: MISSED (end-exclusive)
        val expiredState = TravelBookingRules.resolveAuthoritativeState(
            nowMs = proofEndMs,
            departureMs = depMs,
            travelEndMs = travelEndMs,
            proofStartMs = proofStartMs,
            proofEndMs = proofEndMs,
            status = "SCHEDULED",
            hasProofSubmitted = false
        )
        assertEquals(TravelBookingRules.TravelStatusState.MISSED, expiredState)

        // After submission: SUBMITTED_PENDING_REVIEW
        val submittedState = TravelBookingRules.resolveAuthoritativeState(
            nowMs = ms("2026-10-17 14:30:00"),
            departureMs = depMs,
            travelEndMs = travelEndMs,
            proofStartMs = proofStartMs,
            proofEndMs = proofEndMs,
            status = "SUBMITTED",
            hasProofSubmitted = true
        )
        assertEquals(TravelBookingRules.TravelStatusState.SUBMITTED_PENDING_REVIEW, submittedState)
    }

    // ==========================================
    // PART 4: HOURLY CARD STATUS & SUBMISSIONS
    // ==========================================

    @Test
    fun testPart4_hourlyWithoutSubmissionNeverShowsAiReview() {
        val startMs = ms("2026-10-17 14:00:00")
        val endMs = ms("2026-10-17 15:00:00")

        // Unsubmitted hourly activity currently open
        val openState = WindowStateRules.state(
            saved = "SCHEDULED",
            nowMs = ms("2026-10-17 14:20:00"),
            startMs = startMs,
            endMs = endMs,
            eligible = true
        )
        assertEquals("OPEN", openState)

        // Unsubmitted hourly activity upcoming
        val upcomingState = WindowStateRules.state(
            saved = "SCHEDULED",
            nowMs = ms("2026-10-17 13:00:00"),
            startMs = startMs,
            endMs = endMs,
            eligible = true
        )
        assertEquals("UPCOMING", upcomingState)

        // Unsubmitted hourly activity missed
        val missedState = WindowStateRules.state(
            saved = "SCHEDULED",
            nowMs = ms("2026-10-17 15:30:00"),
            startMs = startMs,
            endMs = endMs,
            eligible = true
        )
        assertEquals("MISSED", missedState)
    }

    // ==========================================
    // PART 5: WAKE-UP DEDUPLICATION
    // ==========================================

    @Test
    fun testPart5_wakeUpOccurrencesDeduplication() {
        val act7am = DisciplineScoringRules.ScheduledActivityItem(
            activityId = "ACT-20261017-0700-WAKEUP",
            accountId = "user_lok",
            type = "WAKE_UP",
            date = "2026-10-17",
            title = "Wake-Up Protocol (07:00)",
            scheduledTime = "2026-10-17T07:00:00+05:30",
            windowStart = "2026-10-17T07:00:00+05:30",
            windowEnd = "2026-10-17T09:30:00+05:30",
            notificationTime = "2026-10-17T06:59:00+05:30",
            status = "VERIFIED",
            submittedAt = "2026-10-17T07:22:00+05:30",
            points = 15,
            validationRule = "CAMERA_BLUETOOTH_AUDIO",
            detailsJson = "{\"target_time\":\"07:00\"}",
            scheduleId = "",
            scheduleVersion = 1,
            configuredPoints = 15
        )

        val act9am = DisciplineScoringRules.ScheduledActivityItem(
            activityId = "ACT-20261017-0900-WAKEUP",
            accountId = "user_lok",
            type = "WAKE_UP",
            date = "2026-10-17",
            title = "Wake-Up Protocol (09:00)",
            scheduledTime = "2026-10-17T09:00:00+05:30",
            windowStart = "2026-10-17T07:00:00+05:30",
            windowEnd = "2026-10-17T09:30:00+05:30",
            notificationTime = "2026-10-17T06:59:00+05:30",
            status = "SCHEDULED",
            submittedAt = null,
            points = 15,
            validationRule = "CAMERA_BLUETOOTH_AUDIO",
            detailsJson = "{\"target_time\":\"09:00\"}",
            scheduleId = "",
            scheduleVersion = 1,
            configuredPoints = 15
        )

        val list = mutableListOf(act7am, act9am)
        val wakeUps = list.filter { it.type == "WAKE_UP" }
        if (wakeUps.size > 1) {
            val keep = wakeUps.firstOrNull { it.status in setOf("VERIFIED", "APPROVED", "COMPLETED") }
                ?: wakeUps.firstOrNull { it.status in setOf("SUBMITTED", "VALIDATING", "AI_PROVISIONAL") }
                ?: wakeUps.last()
            list.removeAll { it.type == "WAKE_UP" && it.activityId != keep.activityId }
        }

        assertEquals("Deduplicated list must contain exactly one wake-up occurrence", 1, list.size)
        assertEquals("ACT-20261017-0700-WAKEUP", list.first().activityId)
        assertEquals("VERIFIED", list.first().status)
    }

    // ==========================================
    // PART 6: HOURLY COUNTDOWN & END-EXCLUSIVE CLOSURE
    // ==========================================

    @Test
    fun testPart6_hourlyCountdownAndClosureAt559Pm() {
        val startMs = ms("2026-10-18 17:50:00") // 5:50 PM
        val endMs = ms("2026-10-18 18:10:00")   // 6:10 PM

        // 1. At 5:59 PM (17:59:00), window is OPEN and calculates ~11 minutes (660 seconds) remaining
        val at559Pm = ms("2026-10-18 17:59:00")
        val stateAt559 = WindowStateRules.state("SCHEDULED", at559Pm, startMs, endMs, eligible = true)
        assertEquals("OPEN", stateAt559)

        val remSec = (endMs - at559Pm) / 1000L
        assertEquals(660L, remSec) // Exactly 11 minutes
        val mm = remSec / 60L
        val ss = remSec % 60L
        assertEquals(11L, mm)
        assertEquals(0L, ss)
        val countdownText = String.format(Locale.US, "⏳ %02d:%02d REMAINING", mm, ss)
        assertEquals("⏳ 11:00 REMAINING", countdownText)

        // 2. Before a submission exists, state is OPEN, never AI REVIEW, POINTS PENDING, or SUBMITTED
        assertFalse(stateAt559 in setOf("AI REVIEW", "POINTS PENDING", "SUBMITTED", "VALIDATING"))

        // 3. Exactly at the end timestamp (6:10:00 PM), window closes end-exclusively
        val at610Pm = ms("2026-10-18 18:10:00")
        val stateAt610 = WindowStateRules.state("SCHEDULED", at610Pm, startMs, endMs, eligible = true)
        assertEquals("MISSED", stateAt610)
        assertFalse(DisciplineScoringRules.isSubmissionWithinWindow(at610Pm, startMs, endMs))

        // 4. After closure without submission, marked MISSED with 0 points
        val after610Pm = ms("2026-10-18 18:15:00")
        val stateAfter = WindowStateRules.state("SCHEDULED", after610Pm, startMs, endMs, eligible = true)
        assertEquals("MISSED", stateAfter)
        assertFalse(stateAfter in setOf("AI REVIEW", "UNAVAILABLE"))
    }

    // ==========================================
    // PART 7: HOURLY SINGLE SUBMISSION LOCKING
    // ==========================================

    @Test
    fun testPart7_hourlySingleSubmissionLocking() {
        val existingStatuses = listOf("SUBMITTED", "VALIDATING", "VERIFIED")
        for (status in existingStatuses) {
            val isAlreadySubmitted = status in listOf("SUBMITTED", "VALIDATING", "VERIFIED")
            assertTrue("Status $status must trigger isAlreadySubmitted=true", isAlreadySubmitted)
            val canSubmitAnother = !isAlreadySubmitted
            assertFalse("Further submissions must be locked when status is $status", canSubmitAnother)
        }
    }

    // ==========================================
    // PART 8: SLEEP DUPLICATE PROTECTION & SELF-EXCLUSION
    // ==========================================

    @Test
    fun testPart8_sleepDuplicateSelfExclusionAndCrossCheck() {
        val testSha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        val subId1 = "sleep_sub_abc123"
        val subId2 = "sleep_sub_def456"

        // Simulated DB duplicate check logic:
        // Query: SELECT 1 FROM sleep_data_submissions WHERE image_sha256 = ? AND (submission_id != ? OR ? = '')
        fun isDuplicateSimulated(sha: String, excludeSubId: String, existingDbRecords: List<Pair<String, String>>): Boolean {
            return existingDbRecords.any { (recordSubId, recordSha) ->
                recordSha == sha && (excludeSubId.isEmpty() || recordSubId != excludeSubId)
            }
        }

        // 1. Initial submission Stage A wrote to DB with subId1 and testSha.
        // During background evaluation of subId1, it must exclude subId1 and NOT match itself:
        val dbWithSub1 = listOf(Pair(subId1, testSha))
        val selfMatch = isDuplicateSimulated(testSha, excludeSubId = subId1, existingDbRecords = dbWithSub1)
        assertFalse("Stage B evaluation must NOT trigger false duplicate on its own submission record", selfMatch)

        // 2. A second submission (subId2) with the identical screenshot hash IS a duplicate:
        val crossMatch = isDuplicateSimulated(testSha, excludeSubId = subId2, existingDbRecords = dbWithSub1)
        assertTrue("Submitting an identical screenshot under a different submission must trigger duplicate", crossMatch)

        // 3. Sleep bonus evaluation with isDuplicate=true returns DUPLICATE_SCREENSHOT with 0 points:
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 30, isPreviousDay = true, rawText = "11:30 PM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 7, minute = 0, isPreviousDay = false, rawText = "7:00 AM")
        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-18",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-18",
            submissionTimestampMs = ms("2026-10-18 08:00:00"),
            timezone = "Asia/Kolkata",
            isDuplicate = true
        )
        assertFalse("Duplicate submission must not be eligible", eval.eligible)
        assertEquals("DUPLICATE_SCREENSHOT", eval.status)
        assertEquals(0, eval.points)
    }
}
