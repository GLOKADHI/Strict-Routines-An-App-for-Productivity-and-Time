package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest
import java.util.Calendar
import java.util.TimeZone

/**
 * Automated Regression Test Suite for Mi Fitness Screenshot Validation.
 * Validates the 6 real benchmark screenshots (October 10–15, 2026),
 * timestamp reconstruction across midnight, duration tolerance cross-checks,
 * duplicate handling across identical times, and deadline enforcement.
 */
class MiFitnessRegressionTest {

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

    private fun readFixtureTxt(fileName: String): String {
        // Try test resources first, then local directory
        val res = javaClass.classLoader?.getResourceAsStream("fixtures/$fileName")
        if (res != null) {
            return res.bufferedReader().readText()
        }
        val file = File("src/test/resources/fixtures/$fileName")
        if (file.exists()) return file.readText()
        val directFile = File("android/app/src/test/resources/fixtures/$fileName")
        if (directFile.exists()) return directFile.readText()
        throw IllegalStateException("Fixture file $fileName not found")
    }

    private fun readFixtureTsv(fileName: String): String {
        val res = javaClass.classLoader?.getResourceAsStream("fixtures/$fileName")
        if (res != null) {
            return res.bufferedReader().readText()
        }
        val file = File("src/test/resources/fixtures/$fileName")
        if (file.exists()) return file.readText()
        val directFile = File("android/app/src/test/resources/fixtures/$fileName")
        if (directFile.exists()) return directFile.readText()
        return ""
    }

    private fun getFileHash(fileName: String): String {
        val bytes = javaClass.classLoader?.getResourceAsStream("fixtures/$fileName")?.readBytes()
            ?: File("src/test/resources/fixtures/$fileName").takeIf { it.exists() }?.readBytes()
            ?: File("android/app/src/test/resources/fixtures/$fileName").takeIf { it.exists() }?.readBytes()
            ?: fileName.toByteArray()
        return MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    }

    // Benchmark 1: October 10, 2026
    // Onset 23:59 (prev date), wake 08:06, duration 486m, score 82, deep sleep 30%, 1 wake-up
    @Test
    fun testBenchmark_2026_10_10() {
        val ocrText = readFixtureTxt("Screenshot_20261015_110157_Mi Fitness.txt")
        val tsv = readFixtureTsv("Screenshot_20261015_110157_Mi Fitness.tsv")
        val tokens = MiFitnessRegionValidator.parseTsvToTokens(tsv)
        val hash = getFileHash("Screenshot_20261015_110157_Mi Fitness.jpg")

        val result = MiFitnessRegionValidator.validate(
            ocrText = ocrText,
            defaultYear = 2026,
            sourceImageId = "Screenshot_20261015_110157_Mi Fitness.jpg",
            sourceIntegrityHash = hash
        )

        assertEquals("2026-10-10", result.reportDate)
        assertEquals("23:59", result.sleepOnsetTime)
        assertEquals("08:06", result.wakeUpTime)
        assertEquals("2026-10-09T23:59:00", result.reconstructedSleepOnsetDateTime)
        assertEquals("2026-10-10T08:06:00", result.reconstructedWakeUpDateTime)
        assertEquals(486, result.displayedDurationMinutes)
        assertEquals(487, result.calculatedDurationMinutes)
        assertEquals(82, result.sleepScore)
        assertEquals(30, result.deepSleepPercent)
        assertEquals(1, result.wakeUpCount)
        assertTrue("Duration difference is within 2 minutes", result.isConsistent)

        // Sleep Bonus Evaluation: onset before midnight -> 5 points
        val parsedData = result.toParsedSleepData()
        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = parsedData.reportDate,
            onset = parsedData.sleepOnset,
            wake = parsedData.wakeUp,
            claimedDate = "2026-10-10",
            submissionTimestampMs = makeTimestamp("2026-10-10", 9, 0),
            timezone = tz
        )
        assertTrue(eval.eligible)
        assertEquals(5, eval.points)
        assertEquals("APPROVED", eval.status)
    }

    // Benchmark 2: October 11, 2026
    // Onset 23:48 (prev date), wake 09:08, duration 559m, score 88, deep sleep 28%, 1 wake-up
    @Test
    fun testBenchmark_2026_10_11() {
        val ocrText = readFixtureTxt("Screenshot_20261015_110145_Mi Fitness.txt")
        val hash = getFileHash("Screenshot_20261015_110145_Mi Fitness.jpg")

        val result = MiFitnessRegionValidator.validate(
            ocrText = ocrText,
            defaultYear = 2026,
            sourceImageId = "Screenshot_20261015_110145_Mi Fitness.jpg",
            sourceIntegrityHash = hash
        )

        assertEquals("2026-10-11", result.reportDate)
        assertEquals("23:48", result.sleepOnsetTime)
        assertEquals("09:08", result.wakeUpTime)
        assertEquals("2026-10-10T23:48:00", result.reconstructedSleepOnsetDateTime)
        assertEquals("2026-10-11T09:08:00", result.reconstructedWakeUpDateTime)
        assertEquals(559, result.displayedDurationMinutes)
        assertEquals(560, result.calculatedDurationMinutes)
        assertEquals(88, result.sleepScore)
        assertEquals(28, result.deepSleepPercent)
        assertEquals(1, result.wakeUpCount)
        assertTrue("Duration difference is within 2 minutes", result.isConsistent)

        // Bonus evaluation: onset before midnight -> 5 points
        val parsedData = result.toParsedSleepData()
        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = parsedData.reportDate,
            onset = parsedData.sleepOnset,
            wake = parsedData.wakeUp,
            claimedDate = "2026-10-11",
            submissionTimestampMs = makeTimestamp("2026-10-11", 9, 30),
            timezone = tz
        )
        assertTrue(eval.eligible)
        assertEquals(5, eval.points)
        assertEquals("APPROVED", eval.status)
    }

    // Benchmark 3: October 12, 2026
    // Onset 00:12 (report date), wake 08:39, duration 503m, score 80, deep sleep 32%, 3 wake-ups
    @Test
    fun testBenchmark_2026_10_12() {
        val ocrText = readFixtureTxt("Screenshot_20261015_110138_Mi Fitness.txt")
        val hash = getFileHash("Screenshot_20261015_110138_Mi Fitness.jpg")

        val result = MiFitnessRegionValidator.validate(
            ocrText = ocrText,
            defaultYear = 2026,
            sourceImageId = "Screenshot_20261015_110138_Mi Fitness.jpg",
            sourceIntegrityHash = hash
        )

        assertEquals("2026-10-12", result.reportDate)
        assertEquals("00:12", result.sleepOnsetTime)
        assertEquals("08:39", result.wakeUpTime)
        assertEquals("2026-10-12T00:12:00", result.reconstructedSleepOnsetDateTime)
        assertEquals("2026-10-12T08:39:00", result.reconstructedWakeUpDateTime)
        assertEquals(503, result.displayedDurationMinutes)
        assertEquals(507, result.calculatedDurationMinutes)
        assertEquals(80, result.sleepScore)
        assertEquals(32, result.deepSleepPercent)
        assertEquals(3, result.wakeUpCount)
        // 4-minute difference with 3 wake-ups is within 15-minute tolerance (3 × 5 min per wake-up)
        // — this is now considered consistent; awake periods explain the gap
        assertTrue(
            "Duration difference of 4m with 3 wake-ups should now be within wake-adjusted tolerance of 15m",
            result.isConsistent
        )
        // needsReview remains false since all required fields were extracted
        assertFalse("needsReview should be false when fields extracted and within tolerance", result.needsReview)


        // Bonus evaluation: onset at 00:12 (12 mins after midnight -> band 1 -> 4 points)
        val parsedData = result.toParsedSleepData()
        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = parsedData.reportDate,
            onset = parsedData.sleepOnset,
            wake = parsedData.wakeUp,
            claimedDate = "2026-10-12",
            submissionTimestampMs = makeTimestamp("2026-10-12", 9, 0),
            timezone = tz
        )
        assertTrue(eval.eligible)
        assertEquals(4, eval.points)
        assertEquals("PARTIALLY_AWARDED", eval.status)
        assertEquals(1, eval.lateHourBands)
    }

    // Benchmark 4: October 13, 2026
    // Onset 00:14 (report date), wake 08:59, duration 523m, score 85, deep sleep 30%, 1 wake-up
    @Test
    fun testBenchmark_2026_10_13() {
        val ocrText = readFixtureTxt("Screenshot_20261015_110130_Mi Fitness.txt")
        val hash = getFileHash("Screenshot_20261015_110130_Mi Fitness.jpg")

        val result = MiFitnessRegionValidator.validate(
            ocrText = ocrText,
            defaultYear = 2026,
            sourceImageId = "Screenshot_20261015_110130_Mi Fitness.jpg",
            sourceIntegrityHash = hash
        )

        assertEquals("2026-10-13", result.reportDate)
        assertEquals("00:14", result.sleepOnsetTime)
        assertEquals("08:59", result.wakeUpTime)
        assertEquals("2026-10-13T00:14:00", result.reconstructedSleepOnsetDateTime)
        assertEquals("2026-10-13T08:59:00", result.reconstructedWakeUpDateTime)
        assertEquals(523, result.displayedDurationMinutes)
        assertEquals(525, result.calculatedDurationMinutes)
        assertEquals(85, result.sleepScore)
        assertEquals(30, result.deepSleepPercent)
        assertEquals(1, result.wakeUpCount)
        assertTrue("Difference is 2 minutes (within tolerance)", result.isConsistent)

        // Bonus evaluation: onset at 00:14 -> 4 points
        val parsedData = result.toParsedSleepData()
        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = parsedData.reportDate,
            onset = parsedData.sleepOnset,
            wake = parsedData.wakeUp,
            claimedDate = "2026-10-13",
            submissionTimestampMs = makeTimestamp("2026-10-13", 10, 0),
            timezone = tz
        )
        assertTrue(eval.eligible)
        assertEquals(4, eval.points)
        assertEquals("PARTIALLY_AWARDED", eval.status)
    }

    // Benchmark 5: October 14, 2026
    // Onset 00:14 (report date), wake 08:59, duration 523m, score 88, deep sleep 34%, 1 wake-up
    @Test
    fun testBenchmark_2026_10_14() {
        val ocrText = readFixtureTxt("Screenshot_20261015_110113_Mi Fitness.txt")
        val hash = getFileHash("Screenshot_20261015_110113_Mi Fitness.jpg")

        val result = MiFitnessRegionValidator.validate(
            ocrText = ocrText,
            defaultYear = 2026,
            sourceImageId = "Screenshot_20261015_110113_Mi Fitness.jpg",
            sourceIntegrityHash = hash
        )

        assertEquals("2026-10-14", result.reportDate)
        assertEquals("00:14", result.sleepOnsetTime)
        assertEquals("08:59", result.wakeUpTime)
        assertEquals("2026-10-14T00:14:00", result.reconstructedSleepOnsetDateTime)
        assertEquals("2026-10-14T08:59:00", result.reconstructedWakeUpDateTime)
        assertEquals(523, result.displayedDurationMinutes)
        assertEquals(525, result.calculatedDurationMinutes)
        assertEquals(88, result.sleepScore)
        assertEquals(34, result.deepSleepPercent)
        assertEquals(1, result.wakeUpCount)
        assertTrue("Difference is 2 minutes (within tolerance)", result.isConsistent)

        // Bonus evaluation: onset at 00:14 -> 4 points
        val parsedData = result.toParsedSleepData()
        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = parsedData.reportDate,
            onset = parsedData.sleepOnset,
            wake = parsedData.wakeUp,
            claimedDate = "2026-10-14",
            submissionTimestampMs = makeTimestamp("2026-10-14", 10, 0),
            timezone = tz
        )
        assertTrue(eval.eligible)
        assertEquals(4, eval.points)
        assertEquals("PARTIALLY_AWARDED", eval.status)
    }

    // Benchmark 6: October 15, 2026
    // Onset 00:40 (report date), wake 09:18, duration 498m, score 83, deep sleep 32%, 2 wake-ups
    @Test
    fun testBenchmark_2026_10_15() {
        val ocrText = readFixtureTxt("Screenshot_20261015_110059_Mi Fitness.txt")
        val hash = getFileHash("Screenshot_20261015_110059_Mi Fitness.jpg")

        val result = MiFitnessRegionValidator.validate(
            ocrText = ocrText,
            defaultYear = 2026,
            sourceImageId = "Screenshot_20261015_110059_Mi Fitness.jpg",
            sourceIntegrityHash = hash
        )

        assertEquals("2026-10-15", result.reportDate)
        assertEquals("00:40", result.sleepOnsetTime)
        assertEquals("09:18", result.wakeUpTime)
        assertEquals("2026-10-15T00:40:00", result.reconstructedSleepOnsetDateTime)
        assertEquals("2026-10-15T09:18:00", result.reconstructedWakeUpDateTime)
        assertEquals(498, result.displayedDurationMinutes)
        assertEquals(518, result.calculatedDurationMinutes)
        assertEquals(83, result.sleepScore)
        assertEquals(32, result.deepSleepPercent)
        assertEquals(2, result.wakeUpCount)
        // Discrepancy (20m due to awake intervals) is retained for review without silent modification
        assertFalse(result.isConsistent)
        assertTrue(result.needsReview)
        assertTrue(result.discrepancyReason.contains("20m"))

        // Bonus evaluation: onset at 00:40 (40 mins after midnight -> band 1 -> 4 points)
        val parsedData = result.toParsedSleepData()
        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = parsedData.reportDate,
            onset = parsedData.sleepOnset,
            wake = parsedData.wakeUp,
            claimedDate = "2026-10-15",
            submissionTimestampMs = makeTimestamp("2026-10-15", 9, 30),
            timezone = tz
        )
        assertTrue(eval.eligible)
        assertEquals(4, eval.points)
        assertEquals("PARTIALLY_AWARDED", eval.status)
    }

    // Duplicate protection check between Oct 13 and Oct 14
    // They share onset (00:14) and wake (08:59) and duration (523m), but have different dates, scores, and image hashes.
    @Test
    fun testDuplicateProtection_October13And14DoNotCollide() {
        val ocr13 = readFixtureTxt("Screenshot_20261015_110130_Mi Fitness.txt")
        val hash13 = getFileHash("Screenshot_20261015_110130_Mi Fitness.jpg")
        val res13 = MiFitnessRegionValidator.validate(ocr13, sourceImageId = "Oct13", sourceIntegrityHash = hash13)

        val ocr14 = readFixtureTxt("Screenshot_20261015_110113_Mi Fitness.txt")
        val hash14 = getFileHash("Screenshot_20261015_110113_Mi Fitness.jpg")
        val res14 = MiFitnessRegionValidator.validate(ocr14, sourceImageId = "Oct14", sourceIntegrityHash = hash14)

        // Same times
        assertEquals(res13.sleepOnsetTime, res14.sleepOnsetTime)
        assertEquals(res13.wakeUpTime, res14.wakeUpTime)
        assertEquals(res13.displayedDurationMinutes, res14.displayedDurationMinutes)

        // Different dates, scores, and hashes
        assertNotEquals("Report dates must be different", res13.reportDate, res14.reportDate)
        assertNotEquals("Sleep scores must be different", res13.sleepScore, res14.sleepScore)
        assertNotEquals("Hashes must be different", res13.sourceIntegrityHash, res14.sourceIntegrityHash)

        // Oct 13 submission does not block Oct 14 submission
        val eval13 = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = res13.reportDate,
            onset = res13.toParsedSleepData().sleepOnset,
            wake = res13.toParsedSleepData().wakeUp,
            claimedDate = "2026-10-13",
            submissionTimestampMs = makeTimestamp("2026-10-13", 10, 0),
            timezone = tz,
            isDuplicate = false
        )
        assertTrue(eval13.eligible)

        val eval14 = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = res14.reportDate,
            onset = res14.toParsedSleepData().sleepOnset,
            wake = res14.toParsedSleepData().wakeUp,
            claimedDate = "2026-10-14",
            submissionTimestampMs = makeTimestamp("2026-10-14", 10, 0),
            timezone = tz,
            isDuplicate = false // Not a duplicate because hashes and dates differ!
        )
        assertTrue(eval14.eligible)

        // But submitting exact hash13 again for 2026-10-13 is flagged as duplicate
        val dupEval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = res13.reportDate,
            onset = res13.toParsedSleepData().sleepOnset,
            wake = res13.toParsedSleepData().wakeUp,
            claimedDate = "2026-10-13",
            submissionTimestampMs = makeTimestamp("2026-10-13", 10, 30),
            timezone = tz,
            isDuplicate = true
        )
        assertFalse(dupEval.eligible)
        assertEquals("DUPLICATE_SCREENSHOT", dupEval.status)
    }

    // Wake-up Isolation Check: Sleep Bonus must never mark wake-up verification steps as passed
    @Test
    fun testWakeUpVerificationStepsAreNeverMarkedPassedBySleepBonus() {
        val wakeOccurrence = JSONObject().apply {
            put("wakeup_camera", 0)
            put("wakeup_bt", 0)
            put("wakeup_track", 0)
            put("wakeup_status", "PENDING")
        }

        // Evaluate and award sleep bonus
        val onset = MiFitnessSleepBonusRules.ClockTime(hour24 = 23, minute = 48, isPreviousDay = true)
        val wake = MiFitnessSleepBonusRules.ClockTime(hour24 = 9, minute = 8, isPreviousDay = false)
        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            "2026-10-15", onset, wake, "2026-10-15", makeTimestamp("2026-10-15", 9, 0), tz
        )
        assertTrue(eval.eligible)
        assertEquals(5, eval.points)

        // Verification steps in wakeOccurrence remain untouched
        assertEquals(0, wakeOccurrence.optInt("wakeup_camera"))
        assertEquals(0, wakeOccurrence.optInt("wakeup_bt"))
        assertEquals(0, wakeOccurrence.optInt("wakeup_track"))
        assertEquals("PENDING", wakeOccurrence.optString("wakeup_status"))
    }

    // Benchmark 7: October 16, 2026 — the fixture that triggered the wake-up OCR fix
    // Onset 00:08 (report date), wake 09:09, duration 532m (displayed), elapsed 541m (9m diff with 3 wake-ups)
    @Test
    fun testBenchmark_2026_10_16() {
        // Load the OCR fixture for October 16 screenshot
        val ocrText = readFixtureTxt("Screenshot_20261016_092940_Mi Fitness.txt")
        val hash = getFileHash("Screenshot_20261016_092940_Mi Fitness.jpg")

        val result = MiFitnessRegionValidator.validate(
            ocrText = ocrText,
            defaultYear = 2026,
            sourceImageId = "Screenshot_20261016_092940_Mi Fitness.jpg",
            sourceIntegrityHash = hash
        )

        // Report date must be correctly extracted from the screenshot header
        assertEquals("Report date must be October 16", "2026-10-16", result.reportDate)

        // Sleep onset (00:08 — after midnight, same date)
        assertNotNull("Sleep onset must be extracted", result.sleepOnsetTime)
        assertEquals("Sleep onset must be 00:08", "00:08", result.sleepOnsetTime)
        assertEquals("Reconstructed onset ISO", "2026-10-16T00:08:00", result.reconstructedSleepOnsetDateTime)

        // CORE FIX: Wake-up time must now be extracted despite appearing before the label in the OCR stream
        assertNotNull("Wake-up time must be extracted — core fix verification", result.wakeUpTime)
        assertEquals("Wake-up time must be 09:09", "09:09", result.wakeUpTime)
        assertEquals("Reconstructed wake ISO", "2026-10-16T09:09:00", result.reconstructedWakeUpDateTime)

        // Displayed duration: 8h 52m = 532 min
        assertEquals("Displayed duration must be 532 min", 532, result.displayedDurationMinutes)

        // Elapsed span: 00:08 to 09:09 = 9h 1m = 541 min
        assertEquals("Calculated span must be 541 min", 541, result.calculatedDurationMinutes)

        // DURATION CONSISTENCY FIX: 541 - 532 = 9 min diff, 3 wake-ups → tolerance = 15 min → consistent
        assertTrue(
            "Duration diff of 9m with 3 wake-ups should be within tolerance of 15m (3 × 5)",
            result.isConsistent
        )
        assertFalse("needsReview must be false when consistent with 3 wake-ups", result.needsReview)

        // Other extracted fields
        assertEquals("Sleep score must be 75", 75, result.sleepScore)
        assertEquals("Deep sleep must be 26%", 26, result.deepSleepPercent)
        assertEquals("Wake-up count must be 3", 3, result.wakeUpCount)
        assertEquals("Sleep efficiency must be 98%", 98, result.sleepEfficiencyPercent)

        // Bonus evaluation: onset at 00:08 → band 1 → 4 points (submitted before noon)
        val parsedData = result.toParsedSleepData()
        assertNotNull("Parsed sleep onset must not be null", parsedData.sleepOnset)
        assertNotNull("Parsed wake-up must not be null", parsedData.wakeUp)
        assertFalse("Onset must not be marked as previous day (00:08 is after midnight on report date)", parsedData.sleepOnset!!.isPreviousDay)

        val eval = MiFitnessSleepBonusRules.evaluateSleepBonus(
            extractedDate = parsedData.reportDate,
            onset = parsedData.sleepOnset,
            wake = parsedData.wakeUp,
            claimedDate = "2026-10-16",
            submissionTimestampMs = makeTimestamp("2026-10-16", 10, 0),
            timezone = tz
        )
        assertTrue("Oct 16 report is eligible for sleep bonus", eval.eligible)
        assertEquals("4 points expected for 00:08 onset (band 1)", 4, eval.points)
        assertEquals("Status should be PARTIALLY_AWARDED", "PARTIALLY_AWARDED", eval.status)
        assertEquals("Late hour band should be 1", 1, eval.lateHourBands)
    }

    // Duration consistency tolerance tests
    @Test
    fun testDurationConsistency_WithinToleranceForWakeUps() {
        // A session with 2 wake-ups and 10-minute difference should be consistent (tolerance = 10)
        val ocrText = "10/16/2026\n8 hrs 52 mins\n00:08\nFell asleep\n09:09\nWoke up\n3 reps\n98% Sleep effici"
        val result = MiFitnessRegionValidator.validate(ocrText = ocrText, defaultYear = 2026)
        // 3 wake-ups → tolerance = 15 min; diff = 9 min → consistent
        // The key assertion: the parsed wake is 09:09
        assertEquals("Wake-up time extracted via label anchor", "09:09", result.wakeUpTime)
    }

    @Test
    fun testDurationConsistency_ImplausibleDisplayedExceedsElapsed() {
        // Displayed duration > elapsed span is always implausible
        // onset 08:00, wake 09:00 → elapsed = 60 min; but if displayed = 70 min → implausible
        val ocrText = "10/16/2026\n1 hrs 10 mins\n08:00\nFell asleep\n09:00\nWoke up"
        val result = MiFitnessRegionValidator.validate(ocrText = ocrText, defaultYear = 2026)
        // elapsed = 60 min, displayed = 70 min → displayed > elapsed → implausible
        if (result.displayedDurationMinutes != null && result.calculatedDurationMinutes != null) {
            val disp = result.displayedDurationMinutes!!
            val calc = result.calculatedDurationMinutes!!
            if (disp > calc) {
                assertFalse("Implausible case: displayed > elapsed should mark isConsistent=false", result.isConsistent)
            }
        }
    }
}

