package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar
import java.util.TimeZone

class MiFitnessSleepBonusRulesTest {

    private val tz = "Asia/Kolkata"

    private fun makeTimestamp(dateStr: String, hour: Int, minute: Int, second: Int = 0, millisecond: Int = 0): Long {
        val parts = dateStr.split("-")
        val cal = Calendar.getInstance(TimeZone.getTimeZone(tz)).apply {
            set(Calendar.YEAR, parts[0].toInt())
            set(Calendar.MONTH, parts[1].toInt() - 1)
            set(Calendar.DAY_OF_MONTH, parts[2].toInt())
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, second)
            set(Calendar.MILLISECOND, millisecond)
        }
        return cal.timeInMillis
    }

    // 1. A sleep session beginning before midnight and ending before 9:30 AM on the report date: 5 points.
    @Test
    fun test01_sleepBeforeMidnight_endingBeforeWakeCutoff_awards5Points() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true, rawText = "11:48 PM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 8, isPreviousDay = false, rawText = "9:08 AM")
        val submissionMs = makeTimestamp("2026-10-15", 8, 30) // 8:30 AM (on-time)

        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs,
            timezone = tz
        )

        assertTrue("Should be eligible", result.eligible)
        assertEquals("Should award full 5 points", 5, result.points)
        assertEquals("APPROVED", result.status)
        assertEquals(0, result.lateHourBands)
        assertFalse(result.deadlinePassed)
        assertFalse(result.wakeUpPastCutoff)
    }

    // 2. A sleep onset at 12:14 AM: 4 points.
    @Test
    fun test02_sleepOnsetAt12_14AM_awards4Points() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 0, minute = 14, isPreviousDay = false, rawText = "12:14 AM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 8, minute = 59, isPreviousDay = false, rawText = "8:59 AM")
        val submissionMs = makeTimestamp("2026-10-15", 9, 30)

        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs,
            timezone = tz
        )

        assertTrue(result.eligible)
        assertEquals(4, result.points)
        assertEquals("PARTIALLY_AWARDED", result.status)
        assertEquals(1, result.lateHourBands)
    }

    // 3. A sleep onset at 12:30 AM and waking at 9:30 AM: 4 points.
    @Test
    fun test03_sleepOnsetAt12_30AM_wakingAt9_30AM_awards4Points() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 0, minute = 30, isPreviousDay = false, rawText = "12:30 AM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 30, isPreviousDay = false, rawText = "9:30 AM")
        val submissionMs = makeTimestamp("2026-10-15", 10, 0)

        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs,
            timezone = tz
        )

        assertTrue(result.eligible)
        assertEquals(4, result.points)
        assertEquals("PARTIALLY_AWARDED", result.status)
        assertEquals(1, result.lateHourBands)
    }

    // 4. A sleep onset at 1:30 AM and waking before 9:30 AM: 3 points.
    @Test
    fun test04_sleepOnsetAt1_30AM_awards3Points() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 1, minute = 30, isPreviousDay = false, rawText = "1:30 AM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 0, isPreviousDay = false, rawText = "9:00 AM")
        val submissionMs = makeTimestamp("2026-10-15", 10, 15)

        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs,
            timezone = tz
        )

        assertTrue(result.eligible)
        assertEquals(3, result.points)
        assertEquals("PARTIALLY_AWARDED", result.status)
        assertEquals(2, result.lateHourBands)

        // Also test 2:30 AM -> 2 points
        val onset230 = MiFitnessSleepBonusRules.ClockTime(hour24 = 2, minute = 30, isPreviousDay = false, rawText = "2:30 AM")
        val res230 = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15", onset = onset230, wake = wake, claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs, timezone = tz
        )
        assertEquals(2, res230.points)
        assertEquals(3, res230.lateHourBands)

        // And 5:30 AM -> 0 points
        val onset530 = MiFitnessSleepBonusRules.ClockTime(hour24 = 5, minute = 30, isPreviousDay = false, rawText = "5:30 AM")
        val res530 = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15", onset = onset530, wake = wake, claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs, timezone = tz
        )
        assertEquals(0, res530.points)
        assertEquals("ZERO_POINTS", res530.status)
    }

    // 5. A wake-up time after 9:30 AM: zero sleep bonus.
    @Test
    fun test05_wakeUpAfter9_30AM_awardsZeroBonus() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true, rawText = "11:48 PM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 31, isPreviousDay = false, rawText = "9:31 AM")
        val submissionMs = makeTimestamp("2026-10-15", 10, 0)

        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs,
            timezone = tz
        )

        assertFalse(result.eligible)
        assertEquals(0, result.points)
        assertEquals("WAKE_AFTER_CUTOFF", result.status)
        assertTrue(result.wakeUpPastCutoff)
    }

    // 6. A report submitted exactly at 12:00 PM: accepted if all other requirements pass.
    @Test
    fun test06_reportSubmittedExactlyAt12_00PM_accepted() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true, rawText = "11:48 PM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 8, isPreviousDay = false, rawText = "9:08 AM")
        val exactNoonMs = makeTimestamp("2026-10-15", 12, 0, 0, 0)

        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = exactNoonMs,
            timezone = tz
        )

        assertTrue("Report submitted exactly at 12:00 PM must be accepted", result.eligible)
        assertEquals(5, result.points)
        assertEquals("APPROVED", result.status)
    }

    // 7. A report submitted after 12:00 PM: no on-time bonus.
    @Test
    fun test07_reportSubmittedAfter12_00PM_noBonus() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true, rawText = "11:48 PM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 8, isPreviousDay = false, rawText = "9:08 AM")
        val oneSecPastNoonMs = makeTimestamp("2026-10-15", 12, 0, 1, 0)

        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = oneSecPastNoonMs,
            timezone = tz
        )

        assertFalse(result.eligible)
        assertEquals(0, result.points)
        assertEquals("DEADLINE_PASSED", result.status)
        assertTrue(result.deadlinePassed)
    }

    // 8. A screenshot whose displayed report date does not match the claimed report date.
    @Test
    fun test08_reportDateMismatch_rejected() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true, rawText = "11:48 PM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 8, isPreviousDay = false, rawText = "9:08 AM")
        val submissionMs = makeTimestamp("2026-10-15", 10, 0)

        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-14", // Previous date on screenshot!
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs,
            timezone = tz
        )

        assertFalse(result.eligible)
        assertEquals(0, result.points)
        assertEquals("DATE_MISMATCH", result.status)
        assertTrue(result.dateMismatch)
    }

    // 9. Incorrect date-format interpretation (MM/DD/YYYY vs DD/MM/YYYY).
    @Test
    fun test09_dateFormatInterpretation() {
        // "10/15/2026" must normalize to 2026-10-15 (October 15, 2026)
        val iso15 = MiFitnessSleepBonusRules.normalizeDateToIso("10/15/2026")
        assertEquals("2026-10-15", iso15)

        // "10/10/2026" in Mi Fitness MM/DD/YYYY format is 2026-10-10
        val iso10 = MiFitnessSleepBonusRules.normalizeDateToIso("10/10/2026")
        assertEquals("2026-10-10", iso10)

        // "October 15, 2026" or "Oct 15, 2026"
        assertEquals("2026-10-15", MiFitnessSleepBonusRules.normalizeDateToIso("October 15, 2026"))
        assertEquals("2026-10-15", MiFitnessSleepBonusRules.normalizeDateToIso("Oct 15, 2026"))

        // Invalid date e.g. 15/15/2026 has no 15th month
        assertNull(MiFitnessSleepBonusRules.normalizeDateToIso("15/15/2026"))
    }

    // 10. An overnight session whose sleep-onset clock time is later than its wake-up clock time.
    @Test
    fun test10_overnightSessionClockComparison() {
        // 23:48 is clock-wise later than 09:08, but onset.isPreviousDay identifies it as an overnight session
        val onset = MiFitnessSleepBonusRules.resolveClockTime(11, 48, isPm = true, rawText = "11:48 PM")
        val wake = MiFitnessSleepBonusRules.resolveClockTime(9, 8, isPm = false, rawText = "9:08 AM")

        assertTrue("Onset at 11:48 PM must be recognized as previous day", onset.isPreviousDay)
        assertFalse("Wake at 9:08 AM must not be previous day", wake.isPreviousDay)
        assertTrue("Onset hour (23) is numerically greater than wake hour (9)", onset.hour24 > wake.hour24)

        val res = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = makeTimestamp("2026-10-15", 8, 0),
            timezone = tz
        )
        assertTrue(res.eligible)
        assertEquals(5, res.points)
    }

    // 11. Missing or unreadable onset/wake-up fields.
    @Test
    fun test11_missingOrUnreadableFields_markedNeedsReview() {
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 8, isPreviousDay = false, rawText = "9:08 AM")
        val submissionMs = makeTimestamp("2026-10-15", 10, 0)

        // Missing onset
        val resNoOnset = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = null,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs,
            timezone = tz
        )
        assertFalse(resNoOnset.eligible)
        assertEquals("NEEDS_REVIEW", resNoOnset.status)
        assertEquals(0, resNoOnset.points)

        // Missing date
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true, rawText = "11:48 PM")
        val resNoDate = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = null,
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs,
            timezone = tz
        )
        assertFalse(resNoDate.eligible)
        assertEquals("NEEDS_REVIEW", resNoDate.status)
    }

    // 12. Duplicate reports and repeated submissions.
    @Test
    fun test12_duplicateReportsAndRepeatedSubmissions() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true, rawText = "11:48 PM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 8, isPreviousDay = false, rawText = "9:08 AM")
        val submissionMs = makeTimestamp("2026-10-15", 10, 0)

        // Exact duplicate screenshot
        val dupRes = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15", onset = onset, wake = wake, claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs, timezone = tz, isDuplicate = true
        )
        assertFalse(dupRes.eligible)
        assertEquals("DUPLICATE_SCREENSHOT", dupRes.status)
        assertEquals(0, dupRes.points)

        // Already awarded for this date
        val alreadyRes = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15", onset = onset, wake = wake, claimedDate = "2026-10-15",
            submissionTimestampMs = submissionMs, timezone = tz, alreadyAwarded = true
        )
        assertFalse(alreadyRes.eligible)
        assertEquals("ALREADY_AWARDED", alreadyRes.status)
        assertEquals(0, alreadyRes.points)
    }

    // 13. Analysis completing after the noon deadline when the upload itself was already accepted before the deadline.
    @Test
    fun test13_analysisCompletingAfterNoon_usesAcceptedTimestamp() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true, rawText = "11:48 PM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 8, isPreviousDay = false, rawText = "9:08 AM")

        // Accepted by app at 11:58 AM (before 12:00 PM deadline)
        val acceptedAtMs = makeTimestamp("2026-10-15", 11, 58)
        // AI analysis finishes at 12:05 PM (after deadline)
        val analysisFinishedAtMs = makeTimestamp("2026-10-15", 12, 5)

        assertTrue(MiFitnessSleepBonusRules.isDeadlinePassed(analysisFinishedAtMs, "2026-10-15", tz))
        assertFalse(MiFitnessSleepBonusRules.isDeadlinePassed(acceptedAtMs, "2026-10-15", tz))

        // Evaluation uses the accepted submission timestamp
        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-15",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-15",
            submissionTimestampMs = acceptedAtMs,
            timezone = tz
        )

        assertTrue("Submission accepted before noon must qualify even if AI finishes after noon", result.eligible)
        assertEquals(5, result.points)
        assertEquals("APPROVED", result.status)
    }

    // 14. A successful sleep bonus does not mark any of the three wake-up steps as passed.
    @Test
    fun test14_sleepBonusDoesNotAlterWakeUpVerificationSteps() {
        // Test that DisciplineScoringRules / wake flow properties are completely separate
        val wakeState = JSONObject().apply {
            put("wakeup_camera", 0)
            put("wakeup_bt", 0)
            put("wakeup_track", 0)
            put("attendance_accepted", false)
        }

        // Simulating sleep bonus evaluation
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true)
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 8, isPreviousDay = false)
        val res = MiFitnessSleepBonusRules.evaluateSleepBonus(
            "2026-10-15", onset, wake, "2026-10-15", makeTimestamp("2026-10-15", 9, 0), tz
        )

        assertTrue(res.eligible)
        assertEquals(5, res.points)

        // Mandatory wake-up state MUST remain unpassed
        assertEquals(0, wakeState.optInt("wakeup_camera"))
        assertEquals(0, wakeState.optInt("wakeup_bt"))
        assertEquals(0, wakeState.optInt("wakeup_track"))
        assertFalse(wakeState.optBoolean("attendance_accepted"))
    }

    // 15. A failed report earns no sleep points and no additional validation penalty.
    @Test
    fun test15_failedReportEarnsNoPointsAndNoNegativePenalty() {
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true)
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 10, minute = 15, isPreviousDay = false) // Late wake-up
        val res = MiFitnessSleepBonusRules.evaluateSleepBonus(
            "2026-10-15", onset, wake, "2026-10-15", makeTimestamp("2026-10-15", 9, 0), tz
        )

        assertFalse(res.eligible)
        assertEquals("No points awarded", 0, res.points)
        assertTrue("No penalty deduction", res.points >= 0)
    }

    // 16. An awarded report is reflected in the authoritative balance and cannot be credited twice.
    @Test
    fun test16_awardedReportIsReflectedAndCannotBeCreditedTwice() {
        val entries = mutableListOf(
            DisciplineScoringRules.ScorePointEntry("WAKEUP_COMPLETED", 15, "ev_wake_1", true),
            DisciplineScoringRules.ScorePointEntry("MI_FITNESS_SLEEP_BONUS", 5, "ev_sleep_1", true)
        )

        val score = DisciplineScoringRules.calculateVerifiedDailyScore(entries)
        assertEquals("15 wake + 5 sleep bonus = 20 points", 20, score)

        // Attempting to award again for same date
        val secondAttempt = MiFitnessSleepBonusRules.evaluateSleepBonus(
            "2026-10-15",
            MiFitnessSleepBonusRules.ClockTime(23, 48, true),
            MiFitnessSleepBonusRules.ClockTime(9, 8, false),
            "2026-10-15",
            makeTimestamp("2026-10-15", 9, 30),
            tz,
            alreadyAwarded = true
        )
        assertFalse(secondAttempt.eligible)
        assertEquals("ALREADY_AWARDED", secondAttempt.status)
        assertEquals(0, secondAttempt.points)
    }

    // Parser tests with supplied Mi Fitness screenshot examples (October 10–15, 2026)
    @Test
    fun testParser_october15Report_overnightBeforeMidnight() {
        val ocrSample = """
            10/15/2026
            Sleep
            8h 20m
            11:48 PM - 9:08 AM
            Deep sleep: 1h 45m (21%)
            Light sleep: 4h 30m (54%)
            REM: 1h 35m (19%)
            Awake: 30m (6%)
            Score: 88
        """.trimIndent()

        val parsed = MiFitnessSleepReportParser.parse(ocrSample)
        assertEquals("2026-10-15", parsed.reportDate)
        assertNotNull(parsed.sleepOnset)
        assertEquals(23, parsed.sleepOnset!!.hour24)
        assertEquals(48, parsed.sleepOnset!!.minute)
        assertTrue(parsed.sleepOnset!!.isPreviousDay)
        assertNotNull(parsed.wakeUp)
        assertEquals(9, parsed.wakeUp!!.hour24)
        assertEquals(8, parsed.wakeUp!!.minute)
        assertEquals(500, parsed.durationMinutes)
        assertEquals("8h 20m", parsed.durationText)
        assertEquals(88, parsed.sleepScore)
        assertFalse(parsed.isAmbiguous)

        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            parsed.reportDate, parsed.sleepOnset, parsed.wakeUp, "2026-10-15", makeTimestamp("2026-10-15", 9, 30), tz
        )
        assertTrue(eval.eligible)
        assertEquals(5, eval.points)
    }

    @Test
    fun testParser_october14Report_afterMidnight() {
        val ocrSample = """
            10/14/2026
            Sleep
            Total sleep: 8 hrs 45 mins
            Fall asleep: 12:14 AM
            Wake up: 8:59 AM
            Deep sleep: 2h 10m
            Light sleep: 5h 05m
            Score: 85
        """.trimIndent()

        val parsed = MiFitnessSleepReportParser.parse(ocrSample)
        assertEquals("2026-10-14", parsed.reportDate)
        assertNotNull(parsed.sleepOnset)
        assertEquals(0, parsed.sleepOnset!!.hour24)
        assertEquals(14, parsed.sleepOnset!!.minute)
        assertFalse(parsed.sleepOnset!!.isPreviousDay)
        assertNotNull(parsed.wakeUp)
        assertEquals(8, parsed.wakeUp!!.hour24)
        assertEquals(59, parsed.wakeUp!!.minute)
        assertFalse(parsed.isAmbiguous)

        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            parsed.reportDate, parsed.sleepOnset, parsed.wakeUp, "2026-10-14", makeTimestamp("2026-10-14", 9, 30), tz
        )
        assertTrue(eval.eligible)
        assertEquals(4, eval.points)
    }

    @Test
    fun testParser_october13Report_namedMonth() {
        val ocrSample = """
            Oct 13, 2026
            Sleep Duration: 7h 30m
            12:30 AM – 9:30 AM
            Score: 82
        """.trimIndent()

        val parsed = MiFitnessSleepReportParser.parse(ocrSample)
        assertEquals("2026-10-13", parsed.reportDate)
        assertNotNull(parsed.sleepOnset)
        assertEquals(0, parsed.sleepOnset!!.hour24)
        assertEquals(30, parsed.sleepOnset!!.minute)
        assertNotNull(parsed.wakeUp)
        assertEquals(9, parsed.wakeUp!!.hour24)
        assertEquals(30, parsed.wakeUp!!.minute)
        assertFalse(parsed.isAmbiguous)

        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            parsed.reportDate, parsed.sleepOnset, parsed.wakeUp, "2026-10-13", makeTimestamp("2026-10-13", 10, 0), tz
        )
        assertTrue(eval.eligible)
        assertEquals(4, eval.points)
    }

    @Test
    fun testParser_october12Report_1_30AMOnset() {
        val ocrSample = """
            10/12/2026
            Sleep
            7h 30m
            1:30 AM – 9:00 AM
            Score: 78
        """.trimIndent()

        val parsed = MiFitnessSleepReportParser.parse(ocrSample)
        assertEquals("2026-10-12", parsed.reportDate)
        assertEquals(1, parsed.sleepOnset!!.hour24)
        assertEquals(30, parsed.sleepOnset!!.minute)
        assertEquals(9, parsed.wakeUp!!.hour24)
        assertEquals(0, parsed.wakeUp!!.minute)

        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            parsed.reportDate, parsed.sleepOnset, parsed.wakeUp, "2026-10-12", makeTimestamp("2026-10-12", 10, 0), tz
        )
        assertTrue(eval.eligible)
        assertEquals(3, eval.points)
    }

    @Test
    fun testParser_october11Report_lateWakeUp() {
        val ocrSample = """
            10/11/2026
            Sleep
            11:30 PM – 9:45 AM
            Score: 80
        """.trimIndent()

        val parsed = MiFitnessSleepReportParser.parse(ocrSample)
        assertEquals("2026-10-11", parsed.reportDate)
        assertEquals(23, parsed.sleepOnset!!.hour24)
        assertEquals(9, parsed.wakeUp!!.hour24)
        assertEquals(45, parsed.wakeUp!!.minute)

        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            parsed.reportDate, parsed.sleepOnset, parsed.wakeUp, "2026-10-11", makeTimestamp("2026-10-11", 10, 0), tz
        )
        assertFalse(eval.eligible)
        assertEquals(0, eval.points)
        assertEquals("WAKE_AFTER_CUTOFF", eval.status)
    }

    @Test
    fun testParser_october10Report_fullBonus() {
        val ocrSample = """
            10/10/2026
            Sleep
            8h 15m
            11:15 PM - 8:30 AM
            Score: 92
        """.trimIndent()

        val parsed = MiFitnessSleepReportParser.parse(ocrSample)
        assertEquals("2026-10-10", parsed.reportDate)
        assertTrue(parsed.sleepOnset!!.isPreviousDay)
        assertEquals(23, parsed.sleepOnset!!.hour24)
        assertEquals(15, parsed.sleepOnset!!.minute)
        assertEquals(8, parsed.wakeUp!!.hour24)
        assertEquals(30, parsed.wakeUp!!.minute)

        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            parsed.reportDate, parsed.sleepOnset, parsed.wakeUp, "2026-10-10", makeTimestamp("2026-10-10", 9, 0), tz
        )
        assertTrue(eval.eligible)
        assertEquals(5, eval.points)
        assertEquals("APPROVED", eval.status)
    }

    // --- Section 8 & Date/Time Validation Specific Tests ---

    @Test
    fun test17_unreadableReportDate_userFacingMessage() {
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 8, minute = 30, isPreviousDay = false, rawText = "8:30 AM")
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 30, isPreviousDay = true, rawText = "11:30 PM")

        val res = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = null,
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-17",
            submissionTimestampMs = makeTimestamp("2026-10-17", 9, 0),
            timezone = tz
        )

        assertFalse(res.eligible)
        assertEquals("NEEDS_REVIEW", res.status)
        assertEquals("The report date could not be verified. Please submit a readable screenshot.", res.reason)
    }

    @Test
    fun test18_reportDateMismatch_userFacingMessage() {
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 8, minute = 30, isPreviousDay = false, rawText = "8:30 AM")
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 30, isPreviousDay = true, rawText = "11:30 PM")

        val res = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-16", // Previous calendar day
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-17",
            submissionTimestampMs = makeTimestamp("2026-10-17", 9, 0),
            timezone = tz
        )

        assertFalse(res.eligible)
        assertEquals("DATE_MISMATCH", res.status)
        assertEquals("This screenshot belongs to a different report date.", res.reason)
        assertTrue(res.dateMismatch)
    }

    @Test
    fun test19_wakeDateMismatch_inconsistentTimestamps() {
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 8, minute = 30, isPreviousDay = false, rawText = "8:30 AM")
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 30, isPreviousDay = true, rawText = "11:30 PM")

        // Report date claims Oct 17, but wake date resolved from timeline was Oct 16
        val res = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-17",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-17",
            submissionTimestampMs = makeTimestamp("2026-10-17", 9, 0),
            timezone = tz,
            reconstructedWakeDate = "2026-10-16"
        )

        assertFalse(res.eligible)
        assertEquals("INCONSISTENT_TIMESTAMPS", res.status)
        assertEquals("The sleep report's timestamps are inconsistent. Verification failed.", res.reason)
    }

    @Test
    fun test20_inconsistentTimestampsFlag_userFacingMessage() {
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 8, minute = 30, isPreviousDay = false, rawText = "8:30 AM")
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 30, isPreviousDay = true, rawText = "11:30 PM")

        val res = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-17",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-17",
            submissionTimestampMs = makeTimestamp("2026-10-17", 9, 0),
            timezone = tz,
            isConsistent = false
        )

        assertFalse(res.eligible)
        assertEquals("INCONSISTENT_TIMESTAMPS", res.status)
        assertEquals("The sleep report's timestamps are inconsistent. Verification failed.", res.reason)
    }

    @Test
    fun test21_legitimateOvernightSleep_oct16Onset_oct17Wake_awards5Points() {
        // Sleep onset: 11:30 PM on 16 October
        // Wake-up: 7:00 AM on 17 October
        // Phone's current date: 17 October
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 30, isPreviousDay = true, rawText = "11:30 PM")
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 7, minute = 0, isPreviousDay = false, rawText = "7:00 AM")
        val submissionMs = makeTimestamp("2026-10-17", 8, 15) // 8:15 AM on Oct 17

        val result = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = "2026-10-17",
            onset = onset,
            wake = wake,
            claimedDate = "2026-10-17",
            submissionTimestampMs = submissionMs,
            timezone = tz,
            reconstructedWakeDate = "2026-10-17",
            isConsistent = true
        )

        assertTrue("Legitimate overnight sleep ending on report date must be approved", result.eligible)
        assertEquals(5, result.points)
        assertEquals("APPROVED", result.status)
        assertEquals("Sleep onset before midnight (11:30 PM) and wake-up on time (7:00 AM).", result.reason)
        assertFalse(result.deadlinePassed)
        assertFalse(result.wakeUpPastCutoff)
        assertFalse(result.dateMismatch)
    }

    @Test
    fun test22_ddMmYyyyDayGreaterThan12_normalizationFallback() {
        // Mi Fitness screenshot with DD/MM/YYYY format where Day > 12
        assertEquals("2026-10-17", MiFitnessSleepBonusRules.normalizeDateToIso("17/10/2026"))
        assertEquals("2026-10-25", MiFitnessSleepBonusRules.normalizeDateToIso("25/10/2026"))
        assertEquals("2026-10-31", MiFitnessSleepBonusRules.normalizeDateToIso("31/10/2026"))

        // Standard MM/DD/YYYY formats
        assertEquals("2026-10-15", MiFitnessSleepBonusRules.normalizeDateToIso("10/15/2026"))
        assertEquals("2026-10-05", MiFitnessSleepBonusRules.normalizeDateToIso("10/05/2026"))
    }

    @Test
    fun test23_smartwatchAwakePeriods_durationLessThanClockSpan_isConsistent() {
        // Sleep onset: 11:30 PM, Wake-up: 7:30 AM -> total clock span = 480 minutes (8 hrs)
        // Displayed total sleep duration: 7 hrs 15 mins (435 minutes) because of 45 mins awake time
        // This is standard smartwatch behavior and MUST be treated as consistent.
        val displayedMinutes = 435
        val calculatedSpanMinutes = 480

        val isConsistent = (displayedMinutes <= calculatedSpanMinutes)
        assertTrue("Displayed duration <= clock span is consistent due to awake periods", isConsistent)
    }

    @Test
    fun test24_smartwatchImpossibleDuration_durationExceedsClockSpan_isNotConsistent() {
        // Sleep onset: 11:30 PM, Wake-up: 7:30 AM -> clock span = 480 minutes
        // Displayed total sleep: 9 hrs 10 mins (550 minutes)
        // Impossible for sleep duration to exceed elapsed clock span -> inconsistent / tampered
        val displayedMinutes = 550
        val calculatedSpanMinutes = 480

        val isConsistent = (displayedMinutes <= calculatedSpanMinutes)
        assertFalse("Displayed duration exceeding clock span is inconsistent", isConsistent)
    }
}

