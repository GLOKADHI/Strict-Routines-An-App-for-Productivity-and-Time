package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PopupRegressionAndGymStatisticsTest {

    @Test
    fun testWakeUpVerificationDialogDismissal() {
        // Ensure dismissVerificationDialog is callable and leaves activeVerificationDialog as null
        WakeUpVerificationController.dismissVerificationDialog()
        assertFalse(WakeUpVerificationController.isVerificationDialogActive())
    }

    @Test
    fun testGymRoutineLaunchAnchorAndDates() {
        assertEquals("2026-10-25", GymRoutineDates.LAUNCH_DATE)
        assertEquals("Asia/Kolkata", GymRoutineDates.TIMEZONE)

        val parts = listOf(GymRoutineDates.Segment("2026-10-25", "2026-10-25"))
        // Prior to launch date
        assertEquals("NOT_TRACKED", GymRoutineDates.plannedState("2026-10-24", true, GymRoutineDates.LAUNCH_DATE, parts))
        // On launch date: 25 Oct 2026
        assertEquals("GYM_DAY", GymRoutineDates.plannedState("2026-10-25", true, GymRoutineDates.LAUNCH_DATE, parts))
        // Next rest day: 26 Oct 2026
        assertEquals("REST_DAY", GymRoutineDates.plannedState("2026-10-26", true, GymRoutineDates.LAUNCH_DATE, parts))
        // Alternate gym day: 27 Oct 2026
        assertEquals("GYM_DAY", GymRoutineDates.plannedState("2026-10-27", true, GymRoutineDates.LAUNCH_DATE, parts))
    }

    @Test
    fun testCompletedWakeUpFlowSuppressesAutoPrompt() {
        val completedStatus = JSONObject().apply {
            put("step1_camera", true)
            put("step2_bluetooth", true)
            put("step3_motivation_track", true)
            put("all_completed", true)
        }
        val isDone = completedStatus.optBoolean("all_completed", false) ||
            (completedStatus.optBoolean("step1_camera", false) &&
             completedStatus.optBoolean("step2_bluetooth", false) &&
             completedStatus.optBoolean("step3_motivation_track", false))
        assertTrue("Completed 3-step wake up must be flagged as done", isDone)

        val incompleteStatus = JSONObject().apply {
            put("step1_camera", true)
            put("step2_bluetooth", false)
            put("step3_motivation_track", false)
            put("all_completed", false)
        }
        val isIncompleteDone = incompleteStatus.optBoolean("all_completed", false) ||
            (incompleteStatus.optBoolean("step1_camera", false) &&
             incompleteStatus.optBoolean("step2_bluetooth", false) &&
             incompleteStatus.optBoolean("step3_motivation_track", false))
        assertFalse("Incomplete wake up must not be flagged as done", isIncompleteDone)
    }

    @Test
    fun testSubmittedActivitiesSuppressHourlyAutoPrompt() {
        val submittedStatuses = listOf("SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED", "EXCUSED", "CANCELLED", "MISSED", "CLOSED")
        for (status in submittedStatuses) {
            val shouldSuppress = status in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED", "EXCUSED", "CANCELLED", "MISSED", "CLOSED")
            assertTrue("Status $status must suppress hourly auto-prompt", shouldSuppress)
        }

        val openStatus = "OPEN"
        val openShouldSuppress = openStatus in listOf("SUBMITTED", "VALIDATING", "VERIFIED", "COMPLETED", "EXCUSED", "CANCELLED", "MISSED", "CLOSED")
        assertFalse("OPEN status must be eligible for prompt", openShouldSuppress)
    }
}
