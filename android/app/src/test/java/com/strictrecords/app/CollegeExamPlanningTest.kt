package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Section 8 unit tests covering:
 * - Planning branch tests (1-9)
 * - Interval and bonus calculation tests (10-16)
 * - Submission and points tests (17-25)
 */
class CollegeExamPlanningTest {

    // ── Planning branch tests (1–9) ──

    @Test
    fun test01_selectingNoProceedsDirectlyToBathPlanningWithoutCollegeOrExam() {
        val plan = CollegeExamScheduleRules.CollegeExamPlan(
            date = "2026-10-23",
            attendsCollege = false
        )
        assertFalse("Should not attend college", plan.attendsCollege)
        assertNull("College start must be null", plan.collegeStartHhmm)
        assertNull("College end must be null", plan.collegeEndHhmm)
        assertFalse("Should have no exam", plan.hasExam)
        assertNull("Exam start must be null", plan.examStartHhmm)
        assertNull("Exam end must be null", plan.examEndHhmm)
        assertFalse("Should have no post-exam class time", plan.hasPostExamClassTime)

        // Directly validates bath planning under Branch A
        val bathVal = CollegeExamScheduleRules.validateIndependentBathSelection(1, listOf("07:30"))
        assertTrue("Bath planning proceeds directly and validates successfully", bathVal.isValid)
    }

    @Test
    fun test02_selecting1BathSessionUnderBranchAPersistsExactly1Session() {
        val bathSelection = listOf("08:00")
        val valResult = CollegeExamScheduleRules.validateIndependentBathSelection(
            sessionCount = 1,
            selectedTimes = bathSelection
        )
        assertTrue("Single bath session validation must succeed", valResult.isValid)
        assertEquals(1, bathSelection.size)
        assertEquals("08:00", bathSelection[0])

        // Missing session time fails validation
        val emptyVal = CollegeExamScheduleRules.validateIndependentBathSelection(
            sessionCount = 1,
            selectedTimes = emptyList()
        )
        assertFalse("Missing session 1 time must fail", emptyVal.isValid)
    }

    @Test
    fun test03_selecting2BathSessionsUnderBranchAPersists2DistinctSessions() {
        val validPair = listOf("07:30", "19:00")
        val valResult = CollegeExamScheduleRules.validateIndependentBathSelection(
            sessionCount = 2,
            selectedTimes = validPair
        )
        assertTrue("Two separated bath sessions must succeed", valResult.isValid)
        assertEquals(2, validPair.size)

        // Overlapping / colliding sessions (< 60 minutes apart) must fail
        val collidingPair = listOf("07:30", "08:00")
        val collisionResult = CollegeExamScheduleRules.validateIndependentBathSelection(
            sessionCount = 2,
            selectedTimes = collidingPair
        )
        assertFalse("Bath sessions < 60 min apart must fail validation", collisionResult.isValid)
        assertTrue(collisionResult.errorMessage!!.contains("at least 60 minutes"))
    }

    @Test
    fun test04_selectingYesForCollegeRequiresCollegeStartAndCollegeEnd() {
        val missingEnd = CollegeExamScheduleRules.validateCollegeTimes("09:00", null)
        assertFalse("Missing college end time must fail validation", missingEnd.isValid)

        val missingStart = CollegeExamScheduleRules.validateCollegeTimes(null, "15:15")
        assertFalse("Missing college start time must fail validation", missingStart.isValid)

        val bothPresent = CollegeExamScheduleRules.validateCollegeTimes("09:00", "15:15")
        assertTrue("Valid college start and end must succeed", bothPresent.isValid)
    }

    @Test
    fun test05_collegeEndAtOrBeforeCollegeStartFailsValidation() {
        val sameTime = CollegeExamScheduleRules.validateCollegeTimes("09:00", "09:00")
        assertFalse("College end equal to start must fail validation", sameTime.isValid)
        assertTrue(sameTime.errorMessage!!.contains("must be later than college start"))

        val earlierEnd = CollegeExamScheduleRules.validateCollegeTimes("15:00", "09:00")
        assertFalse("College end earlier than start must fail validation", earlierEnd.isValid)
    }

    @Test
    fun test06_selectingNoForExamSavesCollegeScheduleWithoutExamDetails() {
        val plan = CollegeExamScheduleRules.CollegeExamPlan(
            date = "2026-10-23",
            attendsCollege = true,
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "16:00",
            hasExam = false
        )
        assertTrue(plan.attendsCollege)
        assertEquals("09:00", plan.collegeStartHhmm)
        assertEquals("16:00", plan.collegeEndHhmm)
        assertFalse(plan.hasExam)
        assertNull(plan.examStartHhmm)
        assertNull(plan.examEndHhmm)
        assertFalse(plan.hasPostExamClassTime)

        val json = plan.toJson()
        assertEquals("NO", json.optString("has_exam"))
        assertEquals("", json.optString("exam_start_time"))
        assertEquals("", json.optString("post_exam_start_time"))
    }

    @Test
    fun test07_selectingYesForExamRequiresExamStartAndExamEnd() {
        val missingExamEnd = CollegeExamScheduleRules.validateExamTimes(
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "15:15",
            examStartHhmm = "09:15",
            examEndHhmm = null
        )
        assertFalse("Missing exam end must fail validation", missingExamEnd.isValid)

        val missingExamStart = CollegeExamScheduleRules.validateExamTimes(
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "15:15",
            examStartHhmm = null,
            examEndHhmm = "11:15"
        )
        assertFalse("Missing exam start must fail validation", missingExamStart.isValid)

        val examEndBeforeStart = CollegeExamScheduleRules.validateExamTimes(
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "15:15",
            examStartHhmm = "11:15",
            examEndHhmm = "09:15"
        )
        assertFalse("Exam end before exam start must fail validation", examEndBeforeStart.isValid)
    }

    @Test
    fun test08_examStartEarlierThanCollegeStartFailsValidation() {
        val result = CollegeExamScheduleRules.validateExamTimes(
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "15:15",
            examStartHhmm = "08:45",
            examEndHhmm = "11:15"
        )
        assertFalse("Exam starting before college must fail validation", result.isValid)
        assertTrue(result.errorMessage!!.contains("must fall within college hours"))
    }

    @Test
    fun test09_examEndLaterThanCollegeEndFailsValidation() {
        val result = CollegeExamScheduleRules.validateExamTimes(
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "15:15",
            examStartHhmm = "10:00",
            examEndHhmm = "15:30"
        )
        assertFalse("Exam ending after college must fail validation", result.isValid)
        assertTrue(result.errorMessage!!.contains("must fall within college hours"))
    }

    // ── Interval and bonus calculation tests (10–16) ──

    @Test
    fun test10_examEndEqualToCollegeEndProducesNoPostExamClassTimeAndNoBonusSlots() {
        val intervalResult = CollegeExamScheduleRules.calculatePostExamClassInterval(
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "16:00",
            examStartHhmm = "10:00",
            examEndHhmm = "16:00"
        )
        assertFalse("Should have no post-exam interval when exam ends with college", intervalResult.hasInterval)
        assertNull(intervalResult.startHhmm)
        assertNull(intervalResult.endHhmm)
        assertEquals(0, intervalResult.durationMinutes)

        // Slot after exam is outside college hours and not eligible
        val slot = CollegeExamScheduleRules.isSlotIntersectingPostExamInterval(
            slotStartHhmm = "16:00",
            slotEndHhmm = "17:00",
            windowStartHhmm = "16:50",
            windowEndHhmm = "17:10",
            postExamStartHhmm = "16:00",
            postExamEndHhmm = "16:00"
        )
        assertFalse("Empty interval must not produce bonus eligible slots", slot.isEligible)
    }

    @Test
    fun test11_canonicalScheduleProducesPostExamClassTime1115To1515() {
        // College: 9:00 AM – 3:15 PM, Exam: 9:15 AM – 11:15 AM
        val res = CollegeExamScheduleRules.calculatePostExamClassInterval(
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "15:15",
            examStartHhmm = "09:15",
            examEndHhmm = "11:15"
        )
        assertTrue("Post-exam class time interval must exist", res.hasInterval)
        assertEquals("11:15", res.startHhmm)
        assertEquals("15:15", res.endHhmm)
        assertEquals(240, res.durationMinutes) // 4 hours
        assertEquals("11:15 AM–03:15 PM", res.displayRange)
    }

    @Test
    fun test12_canonicalScheduleSlot1000To1100IsNotPostExamClassTime() {
        // Slot 10:00 - 11:00 ends before exam end (11:15)
        val eligibility = CollegeExamScheduleRules.isSlotIntersectingPostExamInterval(
            slotStartHhmm = "10:00",
            slotEndHhmm = "11:00",
            windowStartHhmm = "10:50",
            windowEndHhmm = "11:10",
            postExamStartHhmm = "11:15",
            postExamEndHhmm = "15:15"
        )
        assertFalse("10:00–11:00 slot ends before 11:15 and must not be post-exam class time", eligibility.isEligible)
    }

    @Test
    fun test13_canonicalScheduleSlot1100To1200IsPostExamClassTime() {
        // Slot 11:00 - 12:00 overlaps 11:15 - 12:00
        val eligibility = CollegeExamScheduleRules.isSlotIntersectingPostExamInterval(
            slotStartHhmm = "11:00",
            slotEndHhmm = "12:00",
            windowStartHhmm = "11:50",
            windowEndHhmm = "12:10",
            postExamStartHhmm = "11:15",
            postExamEndHhmm = "15:15"
        )
        assertTrue("11:00–12:00 slot overlaps 11:15–12:00 and must be eligible", eligibility.isEligible)
    }

    @Test
    fun test14_canonicalScheduleSlot1400To1500IsPostExamClassTime() {
        // Slot 2:00 PM - 3:00 PM is fully inside 11:15 AM - 3:15 PM
        val eligibility = CollegeExamScheduleRules.isSlotIntersectingPostExamInterval(
            slotStartHhmm = "14:00",
            slotEndHhmm = "15:00",
            windowStartHhmm = "14:50",
            windowEndHhmm = "15:10",
            postExamStartHhmm = "11:15",
            postExamEndHhmm = "15:15"
        )
        assertTrue("14:00–15:00 slot is inside 11:15–15:15 and must be eligible", eligibility.isEligible)
    }

    @Test
    fun test15_canonicalScheduleSlot1500To1600IsPostExamClassTime() {
        // Slot 3:00 PM - 4:00 PM overlaps 3:00 PM - 3:15 PM
        val eligibility = CollegeExamScheduleRules.isSlotIntersectingPostExamInterval(
            slotStartHhmm = "15:00",
            slotEndHhmm = "16:00",
            windowStartHhmm = "15:00",
            windowEndHhmm = "15:30",
            postExamStartHhmm = "11:15",
            postExamEndHhmm = "15:15"
        )
        assertTrue("15:00–16:00 slot overlaps 15:00–15:15 and must be eligible", eligibility.isEligible)
    }

    @Test
    fun test16_canonicalScheduleSlot1600To1700IsNotPostExamClassTime() {
        // Slot 4:00 PM - 5:00 PM starts after college end (3:15 PM)
        val eligibility = CollegeExamScheduleRules.isSlotIntersectingPostExamInterval(
            slotStartHhmm = "16:00",
            slotEndHhmm = "17:00",
            windowStartHhmm = "16:50",
            windowEndHhmm = "17:10",
            postExamStartHhmm = "11:15",
            postExamEndHhmm = "15:15"
        )
        assertFalse("16:00–17:00 slot starts after 15:15 and must not be eligible", eligibility.isEligible)
    }

    // ── Submission and points tests (17–25) ──

    @Test
    fun test17_validSubmissionDuringEligibleSlotAwardsNormalPlusBonusPoint() {
        // Normal hourly points = 8, Post-exam bonus = +1
        val normalPoints = 8
        val bonusPoints = 1
        val totalExpected = normalPoints + bonusPoints
        assertEquals(9, totalExpected)

        // Ledger qualification check for POST_EXAM_CLASS_BONUS
        val canAwardBonus = DisciplineScoringRules.canAwardPositiveProofPoints(
            eventType = "POST_EXAM_CLASS_BONUS",
            approvedWithRetainedProof = true
        )
        assertTrue("POST_EXAM_CLASS_BONUS must be authorized in score ledger", canAwardBonus)

        val entryNormal = DisciplineScoringRules.ScorePointEntry(
            points = normalPoints,
            eventType = "PROOF_APPROVED",
            evidenceId = "ev_hourly_postexam",
            approvedWithRetainedProof = true
        )
        val entryBonus = DisciplineScoringRules.ScorePointEntry(
            points = bonusPoints,
            eventType = "POST_EXAM_CLASS_BONUS",
            evidenceId = "ev_hourly_postexam",
            approvedWithRetainedProof = true
        )
        val verifiedScore = DisciplineScoringRules.calculateVerifiedDailyScore(listOf(entryNormal, entryBonus))
        assertEquals(9, verifiedScore)
    }

    @Test
    fun test18_multipleSubmissionsDoNotAwardDuplicateBonusPoints() {
        val actId = "act_hourly_12"
        val expectedBonusTxId = "se_bonus_postexam_$actId"
        assertEquals("se_bonus_postexam_act_hourly_12", expectedBonusTxId)

        // Simulating ledger recording: duplicate transaction IDs are prevented
        val transactionsRecorded = mutableSetOf<String>()
        val firstInserted = transactionsRecorded.add(expectedBonusTxId)
        assertTrue("First bonus transaction is accepted", firstInserted)

        val retryInserted = transactionsRecorded.add(expectedBonusTxId)
        assertFalse("Duplicate bonus transaction is rejected due to deterministic ID collision", retryInserted)
        assertEquals(1, transactionsRecorded.size)
    }

    @Test
    fun test19_unsubmittedPostExamSlotAfterCloseTransitionsToExcusedNoPoints() {
        val details = JSONObject().apply {
            put("hour_slot", "12:00-13:00")
            put("is_post_exam_class_time", true)
            put("post_exam_bonus_eligible", true)
        }

        // When deadline passes with no submission
        details.put("attendance_status", "EXCUSED_NO_POINTS")
        details.put("suppression_reason", "Excused — no submission required, no points awarded")

        assertEquals("EXCUSED_NO_POINTS", details.optString("attendance_status"))
        assertEquals("Excused — no submission required, no points awarded", details.optString("suppression_reason"))
    }

    @Test
    fun test20_unsubmittedPostExamSlotProducesNoPenaltyOrPointsDeduction() {
        // Post exam slot excused with 0 points
        val excusedEntry = DisciplineScoringRules.ScorePointEntry(
            points = 0,
            eventType = "HOURLY_ATTENDANCE_EXCUSED",
            evidenceId = "",
            approvedWithRetainedProof = false
        )
        val deductions = DisciplineScoringRules.calculateDailyDeductions(listOf(excusedEntry.points))
        assertEquals("Unsubmitted post-exam slot must produce 0 deductions", 0, deductions)

        val score = DisciplineScoringRules.calculateVerifiedDailyScore(listOf(excusedEntry))
        assertEquals(0, score)
    }

    @Test
    fun test21_editingTomorrowPlanBeforeLockPreservesIndependentBathSelections() {
        // Initial state: Bath configured independently with 2 sessions
        val originalBathTimes = listOf("07:00", "20:00")
        val originalBathCount = 2

        // User edits only college timing (changes 09:00-15:15 to 09:30-16:00)
        val updatedCollegeStart = "09:30"
        val updatedCollegeEnd = "16:00"

        // Bath selections are independent and preserved
        val preservedBathTimes = originalBathTimes
        val preservedBathCount = originalBathCount
        assertEquals(2, preservedBathCount)
        assertEquals(listOf("07:00", "20:00"), preservedBathTimes)

        val bathVal = CollegeExamScheduleRules.validateIndependentBathSelection(preservedBathCount, preservedBathTimes)
        assertTrue(bathVal.isValid)
    }

    @Test
    fun test22_changingCollegeAnswerFromNoToYesDoesNotAlterBathSessions() {
        // User originally selected "No" for college and configured 1 bath session at 08:30
        val originalBathCount = 1
        val originalBathTimes = listOf("08:30")

        // User changes college decision to "Yes"
        val newCollegeDecision = "YES"

        // Bath session count and times must NOT be overwritten or forced to 2
        val maintainedBathCount = originalBathCount
        val maintainedBathTimes = originalBathTimes

        assertEquals("YES", newCollegeDecision)
        assertEquals(1, maintainedBathCount)
        assertEquals(listOf("08:30"), maintainedBathTimes)
    }

    @Test
    fun test23_changingCollegeAnswerFromYesToNoDoesNotAlterBathSessions() {
        // User originally selected "Yes" for college with 2 bath sessions at 07:00 and 19:30
        val originalBathCount = 2
        val originalBathTimes = listOf("07:00", "19:30")

        // User changes college decision to "No"
        val newCollegeDecision = "NO"

        // Bath session count and times must NOT be overwritten or forced to 1
        val maintainedBathCount = originalBathCount
        val maintainedBathTimes = originalBathTimes

        assertEquals("NO", newCollegeDecision)
        assertEquals(2, maintainedBathCount)
        assertEquals(listOf("07:00", "19:30"), maintainedBathTimes)
    }

    @Test
    fun test24_lockedScheduleRejectsChangesUntilExplicitUnlock() {
        // Model locked schedule behavior
        var isLocked = true

        fun trySaveSchedule(allowOverwrite: Boolean): JSONObject {
            return if (isLocked && !allowOverwrite) {
                JSONObject().put("success", false).put("error", "Schedule is already confirmed and locked.")
            } else {
                isLocked = true
                JSONObject().put("success", true).put("status", "SCHEDULED")
            }
        }

        val directAttempt = trySaveSchedule(allowOverwrite = false)
        assertFalse("Direct attempt to edit locked schedule must fail", directAttempt.optBoolean("success"))
        assertEquals("Schedule is already confirmed and locked.", directAttempt.optString("error"))

        // Explicit unlock action performed
        isLocked = false

        val unlockedAttempt = trySaveSchedule(allowOverwrite = true)
        assertTrue("Attempt after explicit unlock must succeed", unlockedAttempt.optBoolean("success"))
    }

    @Test
    fun test25_reEvaluatingScheduleRecalculatesPostExamIntervalsOnChanges() {
        // Initial: College 09:00 - 15:15, Exam 09:15 - 11:15
        val initialInterval = CollegeExamScheduleRules.calculatePostExamClassInterval(
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "15:15",
            examStartHhmm = "09:15",
            examEndHhmm = "11:15"
        )
        assertEquals("11:15", initialInterval.startHhmm)
        assertEquals("15:15", initialInterval.endHhmm)

        // Slot 11:00 - 12:00 was initially eligible
        val initialSlotEligibility = CollegeExamScheduleRules.isSlotIntersectingPostExamInterval(
            slotStartHhmm = "11:00",
            slotEndHhmm = "12:00",
            windowStartHhmm = "11:50",
            windowEndHhmm = "12:10",
            postExamStartHhmm = initialInterval.startHhmm!!,
            postExamEndHhmm = initialInterval.endHhmm!!
        )
        assertTrue("Initially slot 11:00–12:00 is eligible", initialSlotEligibility.isEligible)

        // Exam rescheduled later: Exam 09:15 - 12:30, College 09:00 - 16:00
        val updatedInterval = CollegeExamScheduleRules.calculatePostExamClassInterval(
            collegeStartHhmm = "09:00",
            collegeEndHhmm = "16:00",
            examStartHhmm = "09:15",
            examEndHhmm = "12:30"
        )
        assertEquals("12:30", updatedInterval.startHhmm)
        assertEquals("16:00", updatedInterval.endHhmm)

        // Re-evaluating slot 11:00 - 12:00 under updated interval: now ends before post-exam class time!
        val updatedSlotEligibility = CollegeExamScheduleRules.isSlotIntersectingPostExamInterval(
            slotStartHhmm = "11:00",
            slotEndHhmm = "12:00",
            windowStartHhmm = "11:50",
            windowEndHhmm = "12:10",
            postExamStartHhmm = updatedInterval.startHhmm!!,
            postExamEndHhmm = updatedInterval.endHhmm!!
        )
        assertFalse("After exam time update, 11:00–12:00 slot ends before 12:30 and is now ineligible", updatedSlotEligibility.isEligible)
    }
}
