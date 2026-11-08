package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar
import java.util.TimeZone

class MiFitnessGymWorkoutRulesTest {

    private val tz = "Asia/Kolkata"

    private fun makeTimestamp(dateStr: String, hour: Int, minute: Int, second: Int = 0): Long {
        val parts = dateStr.split("-")
        val cal = Calendar.getInstance(TimeZone.getTimeZone(tz)).apply {
            set(Calendar.YEAR, parts[0].toInt())
            set(Calendar.MONTH, parts[1].toInt() - 1)
            set(Calendar.DAY_OF_MONTH, parts[2].toInt())
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, second)
            set(Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    // =========================================================================
    // 1. BENCHMARK OCR EXTRACTION & VALIDATION
    // Reference image: Freestyle, 13/10/2026, 6:36 PM, 348 kcal workout, 444 kcal total,
    // 01:04:01 duration, 109 BPM avg, 150 BPM max, +9 Vitality score, Redmi Watch 5 Lite
    // =========================================================================
    @Test
    fun test01_benchmarkScreenshotExtraction() {
        val ocrSample = """
            Freestyle
            13/10/2026, 6:36 PM
            Mi Fitness
            348 kcal
            01:04:01    444 kcal    109 BPM
            Time        Total kcal  Avg HR
            Heart rate (BPM)
            109         150
            BPM         Max BPM
            Light       00:15:30
            Intensive   00:22:10
            Aerobic     00:18:45
            Anaerobic   00:06:12
            VO2 max     00:01:24
            Training effect
            +9 Vitality score
            Data source
            Redmi Watch 5 Lite
            Mi Fitness
        """.trimIndent()

        val parsed = MiFitnessGymWorkoutValidator.validate(
            ocrText = ocrSample,
            defaultYear = 2026,
            claimedDate = "2026-10-13",
            sourceImageId = "test_fixture_20261013"
        )

        // Date and Time
        assertEquals("2026-10-13", parsed.workoutDate)
        assertNotNull(parsed.workoutTime)
        assertEquals(18, parsed.workoutTime?.hour24)
        assertEquals(36, parsed.workoutTime?.minute)
        assertTrue(parsed.workoutTime?.isPm == true)
        assertEquals("6:36 PM", parsed.workoutTime?.formatted12h)

        // Workout Type
        assertEquals("Freestyle", parsed.workoutType)

        // Duration: 01:04:01 -> 3841 seconds
        assertEquals(3841, parsed.durationSeconds)
        assertEquals("01:04:01", parsed.durationDisplay)

        // Calories: Strict separation between workout active burn (348) and total (444)
        assertEquals(348, parsed.workoutCalories)
        assertEquals(444, parsed.totalCalories)
        assertTrue("Workout calories must be distinct from total calories", parsed.workoutCalories != parsed.totalCalories)

        // Heart Rate
        assertEquals(109, parsed.avgHeartRate)
        assertEquals(150, parsed.maxHeartRate)

        // Heart rate zones
        assertTrue(parsed.heartRateZones.containsKey("Light"))
        assertEquals("00:15:30", parsed.heartRateZones["Light"])
        assertTrue(parsed.heartRateZones.containsKey("Intensive"))
        assertEquals("00:22:10", parsed.heartRateZones["Intensive"])
        assertTrue(parsed.heartRateZones.containsKey("Aerobic"))
        assertEquals("00:18:45", parsed.heartRateZones["Aerobic"])

        // Training effect & device
        assertEquals("+9 Vitality score", parsed.trainingEffect)
        assertEquals("Redmi Watch 5 Lite", parsed.dataSource)

        // Consistency check
        assertTrue("Workout should be internally consistent", parsed.isConsistent)
        assertFalse(parsed.needsReview)
        assertTrue(parsed.discrepancyReasons.isEmpty())
    }

    // =========================================================================
    // 2. MATCHED SESSION EVALUATION & POINT AWARDING (+15 PTS)
    // =========================================================================
    @Test
    fun test02_matchingScheduledGymSession_awards15Points() {
        val parsedData = MiFitnessGymWorkoutRules.ParsedWorkoutData(
            workoutDate = "2026-10-13",
            workoutTime = MiFitnessGymWorkoutRules.WorkoutClockTime(hour24 = 18, minute = 36, isPm = true, rawText = "6:36 PM"),
            workoutType = "Freestyle",
            durationSeconds = 3841,
            durationDisplay = "01:04:01",
            workoutCalories = 348,
            totalCalories = 444,
            avgHeartRate = 109,
            maxHeartRate = 150,
            trainingEffect = "+9 Vitality score",
            dataSource = "Redmi Watch 5 Lite"
        )

        val windowStart = makeTimestamp("2026-10-13", 20, 30) // Window 20:30 - 21:30
        val windowEnd = makeTimestamp("2026-10-13", 21, 30)
        val submitTime = makeTimestamp("2026-10-13", 20, 45) // Submitted within window

        val eval = MiFitnessGymWorkoutRules.evaluateGymWorkout(
            parsed = parsedData,
            claimedDate = "2026-10-13",
            scheduledActivityId = "ACT-20261013-1830-GYM",
            plannedTimeHhmm = "18:30",
            windowStartMs = windowStart,
            windowEndMs = windowEnd,
            submissionTimestampMs = submitTime,
            timezone = tz
        )

        assertTrue("Should be eligible", eval.eligible)
        assertEquals("Must award standard 15 gym points", 15, eval.points)
        assertEquals("APPROVED", eval.status)
        assertEquals("APPROVED", eval.decision)
        assertFalse(eval.dateMismatch)
        assertFalse(eval.isDuplicate)
        assertEquals("WITHIN_WINDOW", eval.submissionWindowStatus)
        assertNotNull(eval.calculatedEndTimeIso)
    }

    // =========================================================================
    // 3. DATE MISMATCH HANDLING (13 OCT SCREENSHOT FOR 17 OCT SESSION)
    // Must flag for review and award 0 points until human review.
    // =========================================================================
    @Test
    fun test03_dateMismatch_flaggedForReview_zeroPoints() {
        val parsedData = MiFitnessGymWorkoutRules.ParsedWorkoutData(
            workoutDate = "2026-10-13",
            workoutTime = MiFitnessGymWorkoutRules.WorkoutClockTime(hour24 = 18, minute = 36, isPm = true, rawText = "6:36 PM"),
            workoutType = "Freestyle",
            durationSeconds = 3841,
            durationDisplay = "01:04:01",
            workoutCalories = 348,
            totalCalories = 444,
            avgHeartRate = 109,
            maxHeartRate = 150
        )

        // Session claimed is for 17 October 2026
        val eval = MiFitnessGymWorkoutRules.evaluateGymWorkout(
            parsed = parsedData,
            claimedDate = "2026-10-17",
            scheduledActivityId = "ACT-20261017-1830-GYM",
            plannedTimeHhmm = "18:30",
            timezone = tz
        )

        assertFalse("Date mismatch must not be eligible for automatic approval", eval.eligible)
        assertEquals("Must award 0 points on date mismatch", 0, eval.points)
        assertEquals("NEEDS_REVIEW", eval.status)
        assertEquals("FLAGGED_FOR_REVIEW", eval.decision)
        assertTrue("dateMismatch flag must be true", eval.dateMismatch)
        assertEquals("Workout date does not match the scheduled Gym session. This proof requires review.", eval.reason)
        assertTrue(eval.discrepancyReasons.any { it.contains("does not match scheduled session") })
    }

    // =========================================================================
    // 4. MISSING OR UNREADABLE DATE
    // =========================================================================
    @Test
    fun test04_missingWorkoutDate_requiresReview() {
        val parsedData = MiFitnessGymWorkoutRules.ParsedWorkoutData(
            workoutDate = null,
            workoutTime = MiFitnessGymWorkoutRules.WorkoutClockTime(18, 36, isPm = true),
            workoutType = "Freestyle",
            durationSeconds = 3600
        )

        val eval = MiFitnessGymWorkoutRules.evaluateGymWorkout(
            parsed = parsedData,
            claimedDate = "2026-10-13"
        )

        assertFalse(eval.eligible)
        assertEquals(0, eval.points)
        assertEquals("NEEDS_REVIEW", eval.status)
        assertTrue(eval.dateMismatch)
        assertTrue(eval.reason.contains("Workout date could not be extracted"))
    }

    // =========================================================================
    // 5. MISSING OR NON-POSITIVE DURATION
    // =========================================================================
    @Test
    fun test05_missingOrZeroDuration_requiresReview() {
        val zeroDurationData = MiFitnessGymWorkoutRules.ParsedWorkoutData(
            workoutDate = "2026-10-13",
            durationSeconds = 0
        )

        val evalZero = MiFitnessGymWorkoutRules.evaluateGymWorkout(
            parsed = zeroDurationData,
            claimedDate = "2026-10-13"
        )
        assertFalse(evalZero.eligible)
        assertEquals(0, evalZero.points)
        assertEquals("NEEDS_REVIEW", evalZero.status)
        assertTrue(evalZero.reason.contains("Workout duration is missing or non-positive"))

        val nullDurationData = MiFitnessGymWorkoutRules.ParsedWorkoutData(
            workoutDate = "2026-10-13",
            durationSeconds = null
        )

        val evalNull = MiFitnessGymWorkoutRules.evaluateGymWorkout(
            parsed = nullDurationData,
            claimedDate = "2026-10-13"
        )
        assertFalse(evalNull.eligible)
        assertEquals(0, evalNull.points)
        assertEquals("NEEDS_REVIEW", evalNull.status)
    }

    // =========================================================================
    // 6. WORKOUT CALORIES VS TOTAL CALORIES SEPARATION & CONSISTENCY
    // =========================================================================
    @Test
    fun test06_workoutCaloriesCannotExceedTotalCalories() {
        // Inconsistent: workout active calories (500) > total calories (400)
        val inconsistentData = MiFitnessGymWorkoutRules.ParsedWorkoutData(
            workoutDate = "2026-10-13",
            durationSeconds = 3600,
            workoutCalories = 500,
            totalCalories = 400
        )

        val eval = MiFitnessGymWorkoutRules.evaluateGymWorkout(
            parsed = inconsistentData,
            claimedDate = "2026-10-13"
        )
        assertFalse(eval.eligible)
        assertEquals(0, eval.points)
        assertEquals("NEEDS_REVIEW", eval.status)
        assertTrue(eval.discrepancyReasons.any { it.contains("Workout calories (500 kcal) exceed total calories (400 kcal)") })
    }

    // =========================================================================
    // 7. HEART RATE CONSISTENCY (AVG CANNOT EXCEED MAX)
    // =========================================================================
    @Test
    fun test07_avgHeartRateCannotExceedMaxHeartRate() {
        val inconsistentHrData = MiFitnessGymWorkoutRules.ParsedWorkoutData(
            workoutDate = "2026-10-13",
            durationSeconds = 3600,
            avgHeartRate = 160,
            maxHeartRate = 140
        )

        val eval = MiFitnessGymWorkoutRules.evaluateGymWorkout(
            parsed = inconsistentHrData,
            claimedDate = "2026-10-13"
        )
        assertFalse(eval.eligible)
        assertEquals(0, eval.points)
        assertEquals("NEEDS_REVIEW", eval.status)
        assertTrue(eval.discrepancyReasons.any { it.contains("Average heart rate (160 BPM) exceeds maximum heart rate (140 BPM)") })
    }

    // =========================================================================
    // 8. DUPLICATE IMAGE PREVENTION
    // =========================================================================
    @Test
    fun test08_duplicateImage_rejectedWithZeroPoints() {
        val parsedData = MiFitnessGymWorkoutRules.ParsedWorkoutData(
            workoutDate = "2026-10-13",
            durationSeconds = 3600
        )

        val eval = MiFitnessGymWorkoutRules.evaluateGymWorkout(
            parsed = parsedData,
            claimedDate = "2026-10-13",
            isDuplicate = true
        )

        assertFalse(eval.eligible)
        assertEquals(0, eval.points)
        assertEquals("REJECTED", eval.status)
        assertEquals("REJECTED", eval.decision)
        assertTrue(eval.isDuplicate)
        assertTrue(eval.reason.contains("Duplicate screenshot detected"))
    }

    // =========================================================================
    // 9. IDEMPOTENCY: ALREADY AWARDED OCCURRENCE
    // Re-evaluating or retrying must not award points a second time.
    // =========================================================================
    @Test
    fun test09_alreadyAwarded_awardsZeroAdditionalPoints() {
        val parsedData = MiFitnessGymWorkoutRules.ParsedWorkoutData(
            workoutDate = "2026-10-13",
            durationSeconds = 3600
        )

        val eval = MiFitnessGymWorkoutRules.evaluateGymWorkout(
            parsed = parsedData,
            claimedDate = "2026-10-13",
            alreadyAwarded = true
        )

        assertFalse(eval.eligible)
        assertEquals("No second point award allowed", 0, eval.points)
        assertEquals("ALREADY_AWARDED", eval.status)
        assertTrue(eval.alreadyAwarded)
    }

    // =========================================================================
    // 10. OVERNIGHT WORKOUT MIDNIGHT ROLLOVER
    // Workout starts at 23:45 on 2026-10-13 and runs for 45 minutes (2700s) -> ends on 2026-10-14 at 00:30.
    // =========================================================================
    @Test
    fun test10_midnightRollover_calculatedEndTimeCorrect() {
        val startTime = MiFitnessGymWorkoutRules.WorkoutClockTime(
            hour24 = 23,
            minute = 45,
            second = 0,
            isPm = true,
            rawText = "11:45 PM"
        )
        val durationSec = 45 * 60 // 2700 sec

        val endIso = MiFitnessGymWorkoutRules.calculateEndTimeIso(
            startDateIso = "2026-10-13",
            startTime = startTime,
            durationSeconds = durationSec,
            timezone = tz
        )

        assertNotNull(endIso)
        // Must roll over to the 14th of October at 00:30:00
        assertTrue("Should roll over to next calendar day: $endIso", endIso!!.startsWith("2026-10-14T00:30:00"))
    }

    // =========================================================================
    // 11. DATE FORMAT NORMALIZATION
    // =========================================================================
    @Test
    fun test11_dateFormatNormalization() {
        assertEquals("2026-10-13", MiFitnessGymWorkoutRules.normalizeDateToIso("13/10/2026"))
        assertEquals("2026-10-13", MiFitnessGymWorkoutRules.normalizeDateToIso("13-10-2026"))
        assertEquals("2026-10-13", MiFitnessGymWorkoutRules.normalizeDateToIso("10/13/2026"))
        assertEquals("2026-10-13", MiFitnessGymWorkoutRules.normalizeDateToIso("13 October 2026"))
        assertEquals("2026-10-13", MiFitnessGymWorkoutRules.normalizeDateToIso("October 13, 2026"))
        assertEquals("2026-10-13", MiFitnessGymWorkoutRules.normalizeDateToIso("13 Oct 2026"))
        assertEquals("2026-10-13", MiFitnessGymWorkoutRules.normalizeDateToIso("2026-10-13"))
        assertNull(MiFitnessGymWorkoutRules.normalizeDateToIso("invalid-date-string"))
    }

    // =========================================================================
    // 12. DURATION PARSING FORMATS
    // =========================================================================
    @Test
    fun test12_durationParsingFormats() {
        assertEquals(3841, MiFitnessGymWorkoutRules.parseDurationToSeconds("01:04:01"))
        assertEquals(241, MiFitnessGymWorkoutRules.parseDurationToSeconds("04:01"))
        assertEquals(3841, MiFitnessGymWorkoutRules.parseDurationToSeconds("1 hr 4 mins 1 sec"))
        assertEquals(3841, MiFitnessGymWorkoutRules.parseDurationToSeconds("1h 4m 1s"))
        assertEquals(3840, MiFitnessGymWorkoutRules.parseDurationToSeconds("64 mins"))
        assertEquals("01:04:01", MiFitnessGymWorkoutRules.formatDuration(3841, humanReadable = false))
        assertEquals("1 hr 4 min 1 sec", MiFitnessGymWorkoutRules.formatDuration(3841, humanReadable = true))
    }

    // =========================================================================
    // 13. DIFFERENT WORKOUT TYPES EXTRACTED
    // =========================================================================
    @Test
    fun test13_differentWorkoutTypesSupported() {
        val types = listOf("Outdoor running", "Treadmill", "Walking", "Gym", "Strength training", "Cross training")
        for (t in types) {
            val text = """
                $t
                13/10/2026, 6:36 PM
                00:45:00 300 kcal 120 BPM
                Time Total kcal Avg HR
            """.trimIndent()
            val res = MiFitnessGymWorkoutValidator.validate(text)
            assertEquals("Should extract workout type $t", t, res.workoutType)
            assertEquals("2026-10-13", res.workoutDate)
            assertEquals(2700, res.durationSeconds)
        }
    }
}
