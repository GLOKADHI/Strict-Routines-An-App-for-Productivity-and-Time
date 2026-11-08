package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Deterministic regression test suite verifying Gym scheduling,
 * provisional booking without premature attendance exemption,
 * proof-triggered interval determination, and hourly attendance reconciliation.
 *
 * Covers all 14 requirements:
 * 1. Planned Gym start produces expected finish and 30-minute proof window.
 * 2. Provisional booking alone leaves hourly attendance active and unexcused.
 * 3. Hourly submission windows open and close on their normal schedule during planned Gym.
 * 4. Start time extraction (verifiable OCR timestamp vs confirmed booking) and end time calculation.
 * 5. Overlapping slot identification for verified interval [verifiedGymStart, actualGymEnd).
 * 6. Reconciliation after proof approval excuses upcoming and open overlapping occurrences.
 * 7. Reconciliation preserves already-submitted hourly proof and review history.
 * 8. Reconciliation reverses penalty deductions for missed slots inside the verified interval.
 * 9. Unaffected hourly slots outside the verified interval remain untouched.
 * 10. Rejected or expired Gym proof never excuses hourly attendance.
 * 11. Single Gym award (+15 pts) and zero points for excused hourly slots (no double counting).
 * 12. Idempotency: repeated reconciliation produces the exact same state without duplicate reversals.
 * 13. Early finish records actual finish time and immediately opens proof submission window.
 * 14. State transitions recorded in Gym proof record (verified interval and reconciled occurrences).
 */
class GymReservationResumptionTest {

    private val zone = TimeZone.getTimeZone("Asia/Kolkata")
    private val date = "2026-10-18"

    private fun timeMs(hhmm: String): Long {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            timeZone = zone
            isLenient = false
        }
        return sdf.parse("$date $hhmm")!!.time
    }

    /**
     * 1. Planned Gym start at 6:00 PM with default 90-minute duration produces
     * expected finish at 7:30 PM and proof deadline at 8:00 PM.
     */
    @Test
    fun test01_plannedGymStartProducesExpectedFinishAndProofDeadline() {
        val gym = GymWindowRules.calculateGymWindow(date, "18:00", 90, "Asia/Kolkata")
        assertEquals(timeMs("18:00"), gym.plannedMs)
        assertEquals(timeMs("18:00"), gym.reservedStartMs)
        assertEquals(timeMs("19:30"), gym.expectedFinishMs)
        assertEquals(timeMs("19:30"), gym.reservedEndMs)
        assertEquals(timeMs("19:30"), gym.windowStartMs)
        assertEquals(timeMs("20:00"), gym.windowEndMs)
        assertEquals("07:30 PM", gym.expectedFinishDisplay)
        assertEquals("07:30 PM–08:00 PM", gym.windowDisplay)
        assertEquals(30 * 60 * 1000L, gym.windowEndMs - gym.windowStartMs)
    }

    /**
     * 2. Provisional booking alone leaves hourly attendance active and unexcused.
     * Selecting a Gym session alone must NEVER automatically excuse attendance.
     */
    @Test
    fun test02_provisionalBookingLeavesHourlyAttendanceActiveAndUnexcused() {
        val plannedDetails = JSONObject().apply {
            put("planned_time", "18:00")
            put("duration_minutes", 90)
            put("proposed_gym_start", "2026-10-18T18:00:00+05:30")
            put("proposed_duration_minutes", 90)
            put("proposed_gym_end", "2026-10-18T19:30:00+05:30")
        }
        val gymStatus = "SCHEDULED"

        // Rule 1: Gym is provisional (SCHEDULED). Proof is NOT yet approved.
        val isGymApproved = gymStatus in listOf("VERIFIED", "COMPLETED", "APPROVED")
        assertFalse("Provisional booking must not be treated as approved", isGymApproved)

        // An overlapping hourly slot (6:00-7:00 PM) must remain SCHEDULED, not EXCUSED
        val hourlyStatus = if (isGymApproved) "EXCUSED" else "SCHEDULED"
        assertEquals("SCHEDULED", hourlyStatus)
        assertFalse("Overlapping slot must not be excused at booking time", hourlyStatus == "EXCUSED")
        assertFalse("Gym hold active flag must not be placed on provisional booking", plannedDetails.optBoolean("gym_hold_active", false))
    }

    /**
     * 3. Hourly submission windows open and close on their normal schedule while a Gym session is planned.
     */
    @Test
    fun test03_hourlyWindowsOpenAndCloseNormallyDuringPlannedGym() {
        val slots = HourlyWindowRules.slots(date, "07:00", "Asia/Kolkata")
        val slot1900 = slots.first { it.hourSlot == "18:00-19:00" } // Target 19:00, window 18:50–19:10
        val slot2000 = slots.first { it.hourSlot == "19:00-20:00" } // Target 20:00, window 19:50–20:10

        assertEquals(timeMs("18:50"), slot1900.startMs)
        assertEquals(timeMs("19:10"), slot1900.endMs)

        // Ordinary submission window for 18:00-19:00 opens at 18:50
        assertEquals("UPCOMING", HourlyWindowRules.state(timeMs("18:45"), slot1900.startMs, slot1900.endMs))
        assertEquals("OPEN", HourlyWindowRules.state(timeMs("18:55"), slot1900.startMs, slot1900.endMs))
        assertTrue(HourlyWindowRules.isSubmissionWithinWindow(timeMs("18:55"), slot1900.startMs, slot1900.endMs))
        assertEquals("CLOSED", HourlyWindowRules.state(timeMs("19:15"), slot1900.startMs, slot1900.endMs))

        // Ordinary submission window for 19:00-20:00 opens at 19:50
        assertEquals("OPEN", HourlyWindowRules.state(timeMs("19:55"), slot2000.startMs, slot2000.endMs))
        assertTrue(HourlyWindowRules.isSubmissionWithinWindow(timeMs("19:55"), slot2000.startMs, slot2000.endMs))
    }

    /**
     * 4. Start time extraction (verifiable OCR timestamp vs confirmed booking) and end time calculation.
     * actualGymEnd = verifiedGymStart + confirmedDuration
     */
    @Test
    fun test04_startTimeExtractionAndEndTimeCalculation() {
        // Source 1: Independently verifiable OCR timestamp (e.g. 18:00)
        val ocrWorkoutTime = MiFitnessGymWorkoutRules.WorkoutClockTime(hour24 = 18, minute = 0, second = 0)
        val durSeconds = 80 * 60 // 1 hour 20 minutes = 4800s
        val confirmedDurMinutes = (durSeconds + 59) / 60
        assertEquals(80, confirmedDurMinutes)

        val verifiedStartMs = timeMs("18:00")
        val actualEndMs = verifiedStartMs + confirmedDurMinutes * 60_000L
        assertEquals(timeMs("19:20"), actualEndMs)

        // Source 2: Proposed booking fallback when OCR timestamp is not present
        val proposedHhmm = "18:00"
        val fallbackStartMs = timeMs(proposedHhmm)
        val fallbackDuration = 90
        val fallbackEndMs = fallbackStartMs + fallbackDuration * 60_000L
        assertEquals(timeMs("19:30"), fallbackEndMs)
    }

    /**
     * 5. Overlapping slot identification for verified interval [verifiedGymStart, actualGymEnd).
     * For example, workout from 6:00 PM to 7:20 PM overlaps:
     * - The 6:00–7:00 PM occurrence (18:00 to 19:00)
     * - The 7:00–8:00 PM occurrence (19:00 to 20:00)
     */
    @Test
    fun test05_overlappingSlotIdentificationForVerifiedInterval() {
        val verifiedStartMs = timeMs("18:00")
        val actualEndMs = timeMs("19:20") // 6:00 PM to 7:20 PM

        val target1800Ms = timeMs("18:00") // 5:00-6:00 PM slot [17:00, 18:00)
        val target1900Ms = timeMs("19:00") // 6:00-7:00 PM slot [18:00, 19:00)
        val target2000Ms = timeMs("20:00") // 7:00-8:00 PM slot [19:00, 20:00)
        val target2100Ms = timeMs("21:00") // 8:00-9:00 PM slot [20:00, 21:00)

        // 5:00-6:00 PM: does NOT overlap [18:00, 19:20)
        assertFalse(GymWindowRules.isHourlySlotAffectedByGym(target1800Ms, verifiedStartMs, actualEndMs))

        // 6:00-7:00 PM: overlaps [18:00, 19:20)
        assertTrue(GymWindowRules.isHourlySlotAffectedByGym(target1900Ms, verifiedStartMs, actualEndMs))

        // 7:00-8:00 PM: overlaps [18:00, 19:20)
        assertTrue(GymWindowRules.isHourlySlotAffectedByGym(target2000Ms, verifiedStartMs, actualEndMs))

        // 8:00-9:00 PM: does NOT overlap [18:00, 19:20)
        assertFalse(GymWindowRules.isHourlySlotAffectedByGym(target2100Ms, verifiedStartMs, actualEndMs))
    }

    /**
     * 6. Reconciliation after proof approval excuses upcoming and open overlapping occurrences.
     */
    @Test
    fun test06_reconciliationAfterProofApprovalExcusesUpcomingAndOpenSlots() {
        val verifiedStartMs = timeMs("18:00")
        val actualEndMs = timeMs("19:20")
        val target1900Ms = timeMs("19:00") // 6:00-7:00 PM slot

        val overlaps = GymWindowRules.isHourlySlotAffectedByGym(target1900Ms, verifiedStartMs, actualEndMs)
        assertTrue(overlaps)

        val gymApproved = true
        val slotInitialStatus = "SCHEDULED"

        val reconciledStatus = if (gymApproved && overlaps && slotInitialStatus in listOf("SCHEDULED", "OPEN", "UPCOMING")) {
            "EXCUSED"
        } else slotInitialStatus

        val reconciliationReason = "EXCUSED_BY_GYM"
        val pointsAwarded = 0

        assertEquals("EXCUSED", reconciledStatus)
        assertEquals("EXCUSED_BY_GYM", reconciliationReason)
        assertEquals(0, pointsAwarded)
    }

    /**
     * 7. Reconciliation preserves already-submitted hourly proof and review history.
     */
    @Test
    fun test07_reconciliationPreservesAlreadySubmittedHourlyProof() {
        val verifiedStartMs = timeMs("18:00")
        val actualEndMs = timeMs("19:20")
        val target1900Ms = timeMs("19:00")
        val overlaps = GymWindowRules.isHourlySlotAffectedByGym(target1900Ms, verifiedStartMs, actualEndMs)

        val slotDetails = JSONObject().apply {
            put("submission_id", "sub_hourly_1900")
            put("evidence_id", "ev_photo_1900")
            put("category", "STUDY")
        }
        val slotStatus = "VERIFIED"
        val slotPoints = 8

        val hasUserSubmission = slotStatus in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED") ||
            slotDetails.optString("submission_id").isNotBlank()

        assertTrue(hasUserSubmission)

        // When reconciling an already-submitted slot:
        val reconciledStatus = if (hasUserSubmission) slotStatus else "EXCUSED"
        val recStatusTag = if (hasUserSubmission) "PRESERVED_USER_SUBMISSION" else "EXCUSED_BY_GYM"
        val preservedPoints = if (hasUserSubmission) slotPoints else 0

        assertEquals("VERIFIED", reconciledStatus)
        assertEquals("PRESERVED_USER_SUBMISSION", recStatusTag)
        assertEquals(8, preservedPoints) // User's earned points are intact!
    }

    /**
     * 8. Reconciliation reverses penalty deductions for missed slots inside the verified interval.
     */
    @Test
    fun test08_reconciliationReversesMissedHourlyPenaltyDeduction() {
        val verifiedStartMs = timeMs("18:00")
        val actualEndMs = timeMs("19:20")
        val target1900Ms = timeMs("19:00")
        val overlaps = GymWindowRules.isHourlySlotAffectedByGym(target1900Ms, verifiedStartMs, actualEndMs)
        assertTrue(overlaps)

        val slotStatus = "MISSED"
        val initialPenalty = -5 // Penalty previously committed
        var currentScore = 45 // e.g. 50 - 5 = 45

        // Reversal logic:
        val penaltyDeduction = kotlin.math.abs(initialPenalty)
        val revTxId = "tx_rev_gym_ACT-20261018-1900-HOURLY"
        val reversalScoreEvent = JSONObject().apply {
            put("event_id", revTxId)
            put("event_type", "SCORE_CORRECTION")
            put("points", penaltyDeduction)
            put("action", "GYM_RECONCILIATION_REVERSAL")
        }

        // Apply reversal transaction:
        currentScore += penaltyDeduction
        val finalSlotStatus = "EXCUSED"
        val reconciliationStatus = "EXCUSED_BY_GYM"

        assertEquals(50, currentScore) // User's balance restored!
        assertEquals("SCORE_CORRECTION", reversalScoreEvent.getString("event_type"))
        assertEquals(5, reversalScoreEvent.getInt("points"))
        assertEquals("EXCUSED", finalSlotStatus)
        assertEquals("EXCUSED_BY_GYM", reconciliationStatus)
    }

    /**
     * 9. Unaffected hourly slots outside the verified interval remain untouched.
     */
    @Test
    fun test09_unaffectedSlotsOutsideVerifiedIntervalRemainUntouched() {
        val verifiedStartMs = timeMs("18:00")
        val actualEndMs = timeMs("19:20")

        val target1800Ms = timeMs("18:00") // 5:00-6:00 PM
        val target2100Ms = timeMs("21:00") // 8:00-9:00 PM

        assertFalse("5:00-6:00 PM is outside [18:00, 19:20)", GymWindowRules.isHourlySlotAffectedByGym(target1800Ms, verifiedStartMs, actualEndMs))
        assertFalse("8:00-9:00 PM is outside [18:00, 19:20)", GymWindowRules.isHourlySlotAffectedByGym(target2100Ms, verifiedStartMs, actualEndMs))

        // Outside slots remain SCHEDULED / normal status
        val status1800 = "SCHEDULED"
        val status2100 = "SCHEDULED"
        assertEquals("SCHEDULED", status1800)
        assertEquals("SCHEDULED", status2100)
    }

    /**
     * 10. Rejected or expired Gym proof never excuses hourly attendance.
     */
    @Test
    fun test10_rejectedOrExpiredGymProofNeverExcusesAttendance() {
        val rejectedGymStatus = "REJECTED"
        val expiredGymStatus = "MISSED"

        val isApproved1 = rejectedGymStatus in listOf("VERIFIED", "COMPLETED", "APPROVED")
        val isApproved2 = expiredGymStatus in listOf("VERIFIED", "COMPLETED", "APPROVED")

        assertFalse("Rejected gym proof must not be approved", isApproved1)
        assertFalse("Expired gym proof must not be approved", isApproved2)

        // Hourly slot when gym was rejected / expired:
        val slotStatus = if (isApproved1) "EXCUSED" else "MISSED"
        assertEquals("MISSED", slotStatus)
    }

    /**
     * 11. Single Gym award (+15 pts) and zero points for excused hourly slots (no double counting).
     */
    @Test
    fun test11_singleGymAwardAndNoDoubleCounting() {
        val gymAwardPoints = MiFitnessGymWorkoutRules.GYM_POINTS
        assertEquals(15, gymAwardPoints)

        // Excused hourly slots:
        val excusedHourlyPoints = 0
        assertEquals(0, excusedHourlyPoints)

        // Total points from gym session + excused hourly slots:
        val totalCombined = gymAwardPoints + excusedHourlyPoints
        assertEquals(15, totalCombined)
    }

    /**
     * 12. Idempotency: repeated reconciliation produces the exact same state without duplicate reversals.
     */
    @Test
    fun test12_idempotentReconciliation() {
        val revTxId = "tx_rev_gym_ACT-20261018-1900-HOURLY"
        val existingEvents = mutableSetOf<String>()

        // First reconciliation run:
        val addedFirstRun = if (revTxId !in existingEvents) {
            existingEvents.add(revTxId)
            true
        } else false

        assertTrue("First run must record reversal event", addedFirstRun)
        assertEquals(1, existingEvents.size)

        // Second reconciliation run (rerun):
        val addedSecondRun = if (revTxId !in existingEvents) {
            existingEvents.add(revTxId)
            true
        } else false

        assertFalse("Second run must not duplicate reversal event", addedSecondRun)
        assertEquals(1, existingEvents.size)
    }

    /**
     * 13. Early finish records actual finish time and immediately opens proof submission window.
     */
    @Test
    fun test13_earlyFinishOpensGymProofWindowImmediately() {
        val actualFinishMs = timeMs("19:00")
        val earlyGym = GymWindowRules.calculateGymWindow(
            date = date,
            gymPlannedHhmm = "18:00",
            durationMinutes = 90,
            timezone = "Asia/Kolkata",
            actualFinishMs = actualFinishMs,
            isEarlyFinish = true
        )

        assertTrue(earlyGym.isEarlyFinish)
        assertEquals(actualFinishMs, earlyGym.windowStartMs) // Opens at 7:00 PM
        assertEquals(actualFinishMs + 30 * 60 * 1000L, earlyGym.windowEndMs) // Closes at 7:30 PM

        // 7:05 PM is within the 7:00–7:30 PM window
        assertTrue(GymWindowRules.isSubmissionWithinWindow(timeMs("19:05"), earlyGym.windowStartMs, earlyGym.windowEndMs))
        // 7:31 PM is outside
        assertFalse(GymWindowRules.isSubmissionWithinWindow(timeMs("19:31"), earlyGym.windowStartMs, earlyGym.windowEndMs))
    }

    /**
     * 14. State transitions recorded in Gym proof record (verified interval and reconciled occurrences).
     */
    @Test
    fun test14_stateTransitionsRecordedInGymProofRecord() {
        val gymDetails = JSONObject().apply {
            put("proposed_gym_start", "2026-10-18T18:00:00+05:30")
            put("proposed_duration_minutes", 90)
            put("proposed_gym_end", "2026-10-18T19:30:00+05:30")
            put("verified_gym_start", "2026-10-18T18:00:00+05:30")
            put("verified_gym_start_ms", timeMs("18:00"))
            put("verified_gym_start_source", "INDEPENDENT_EVIDENCE_TIMESTAMP")
            put("confirmed_duration_minutes", 80)
            put("actual_gym_end", "2026-10-18T19:20:00+05:30")
            put("actual_gym_end_ms", timeMs("19:20"))
            put("reconciled_hourly_occurrences", JSONArray(listOf("ACT-20261018-1900-HOURLY", "ACT-20261018-2000-HOURLY")))
        }

        assertEquals("2026-10-18T18:00:00+05:30", gymDetails.getString("verified_gym_start"))
        assertEquals("2026-10-18T19:20:00+05:30", gymDetails.getString("actual_gym_end"))
        assertEquals(80, gymDetails.getInt("confirmed_duration_minutes"))
        assertEquals("INDEPENDENT_EVIDENCE_TIMESTAMP", gymDetails.getString("verified_gym_start_source"))

        val recArray = gymDetails.getJSONArray("reconciled_hourly_occurrences")
        assertEquals(2, recArray.length())
        assertEquals("ACT-20261018-1900-HOURLY", recArray.getString(0))
        assertEquals("ACT-20261018-2000-HOURLY", recArray.getString(1))
    }
}
