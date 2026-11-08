package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/**
 * Intelligent OCR parser and validator for Mi Fitness (Xiaomi Wear) Gym Workout screenshots.
 *
 * Extracts structured typed data:
 * - Workout date & start time
 * - Workout type (e.g. Freestyle)
 * - Workout duration (displayed & seconds)
 * - Workout calories vs Total calories (strictly separated)
 * - Average & Maximum heart rate
 * - Heart rate zones and durations
 * - Training effect (+9 Vitality score)
 * - Data source (e.g. Redmi Watch 5 Lite)
 *
 * Does not fabricate missing values. If OCR fails to detect a field, it is marked as NOT_DETECTED.
 */
object MiFitnessGymWorkoutValidator {

    data class FieldStatus(
        val status: String, // "EXTRACTED", "NOT_DETECTED", "AMBIGUOUS"
        val rawText: String = "",
        val confidence: Double? = null
    ) {
        fun toJson(): JSONObject = JSONObject().apply {
            put("status", status)
            put("raw_text", rawText)
            if (confidence != null) put("confidence", confidence) else put("confidence", JSONObject.NULL)
        }
    }

    data class GymWorkoutValidationResult(
        val workoutDate: String?, // Normalized ISO YYYY-MM-DD
        val workoutDateRaw: String?,
        val workoutTime: MiFitnessGymWorkoutRules.WorkoutClockTime?,
        val workoutType: String?,
        val durationSeconds: Int?,
        val durationDisplay: String?,
        val workoutCalories: Int?,
        val totalCalories: Int?,
        val avgHeartRate: Int?,
        val maxHeartRate: Int?,
        val heartRateZones: Map<String, String>,
        val trainingEffect: String?,
        val dataSource: String?,
        val fieldStatus: Map<String, FieldStatus>,
        val sourceImageId: String,
        val sourceIntegrityHash: String,
        val rawOcrText: String,
        val isConsistent: Boolean,
        val needsReview: Boolean,
        val discrepancyReasons: List<String>
    ) {
        fun toParsedWorkoutData(): MiFitnessGymWorkoutRules.ParsedWorkoutData {
            return MiFitnessGymWorkoutRules.ParsedWorkoutData(
                workoutDate = workoutDate,
                workoutDateRaw = workoutDateRaw,
                workoutTime = workoutTime,
                workoutType = workoutType,
                durationSeconds = durationSeconds,
                durationDisplay = durationDisplay,
                workoutCalories = workoutCalories,
                totalCalories = totalCalories,
                avgHeartRate = avgHeartRate,
                maxHeartRate = maxHeartRate,
                heartRateZones = heartRateZones,
                trainingEffect = trainingEffect,
                dataSource = dataSource,
                rawOcrText = rawOcrText,
                isAmbiguous = needsReview,
                ambiguityReason = discrepancyReasons.joinToString("; ")
            )
        }

        fun toJson(): JSONObject = JSONObject().apply {
            put("workout_date", workoutDate ?: JSONObject.NULL)
            put("workout_date_raw", workoutDateRaw ?: JSONObject.NULL)
            put("workout_time", workoutTime?.toJson() ?: JSONObject.NULL)
            put("workout_type", workoutType ?: JSONObject.NULL)
            put("duration_seconds", durationSeconds ?: JSONObject.NULL)
            put("duration_display", durationDisplay ?: JSONObject.NULL)
            put("workout_calories", workoutCalories ?: JSONObject.NULL)
            put("total_calories", totalCalories ?: JSONObject.NULL)
            put("avg_heart_rate", avgHeartRate ?: JSONObject.NULL)
            put("max_heart_rate", maxHeartRate ?: JSONObject.NULL)
            put("heart_rate_zones", JSONObject(heartRateZones))
            put("training_effect", trainingEffect ?: JSONObject.NULL)
            put("data_source", dataSource ?: JSONObject.NULL)
            put("source_image_id", sourceImageId)
            put("source_integrity_hash", sourceIntegrityHash)
            put("is_consistent", isConsistent)
            put("needs_review", needsReview)
            put("discrepancy_reasons", JSONArray(discrepancyReasons))

            val fs = JSONObject()
            fieldStatus.forEach { (k, v) -> fs.put(k, v.toJson()) }
            put("field_status", fs)
        }
    }

    /**
     * Main entry point to validate and extract structured fields from OCR text and blocks.
     */
    fun validate(
        ocrText: String,
        blocksJson: JSONArray? = null,
        defaultYear: Int = 2026,
        claimedDate: String? = null,
        sourceImageId: String = "gym_evidence",
        sourceIntegrityHash: String = ""
    ): GymWorkoutValidationResult {
        val cleanText = ocrText.replace("\r", " ")
        val fieldStatus = mutableMapOf<String, FieldStatus>()
        val discrepancies = mutableListOf<String>()

        // 1. Claimed date parsing preference
        var prefDay: Int? = null
        var prefMonth: Int? = null
        if (!claimedDate.isNullOrBlank()) {
            val p = claimedDate.split("-")
            if (p.size >= 3) {
                prefMonth = p[1].toIntOrNull()
                prefDay = p[2].toIntOrNull()
            }
        }

        // 2. Workout Date & Time
        val (wDate, wDateRaw, wTime, dtStatus) = extractDateTime(cleanText, defaultYear, prefDay, prefMonth)
        fieldStatus["workoutDate"] = dtStatus
        if (wDate == null) {
            discrepancies.add("Workout date could not be identified from screenshot")
        }

        // 3. Workout Type
        val (wType, typeStatus) = extractWorkoutType(cleanText)
        fieldStatus["workoutType"] = typeStatus

        // 4. Duration
        val (durSec, durDisp, durStatus) = extractDuration(cleanText)
        fieldStatus["duration"] = durStatus
        if (durSec == null || durSec <= 0) {
            discrepancies.add("Workout duration could not be extracted or is non-positive")
        }

        // 5. Workout Calories vs Total Calories
        val (wkCal, totCal, calStatus) = extractCalories(cleanText)
        fieldStatus["calories"] = calStatus

        if (wkCal != null && totCal != null && wkCal > totCal) {
            discrepancies.add("Workout calories ($wkCal kcal) exceed total calories ($totCal kcal)")
        }

        // 6. Heart Rate (Avg & Max)
        val (avgHr, maxHr, hrStatus) = extractHeartRates(cleanText)
        fieldStatus["heartRate"] = hrStatus

        if (avgHr != null && maxHr != null && avgHr > maxHr) {
            discrepancies.add("Average heart rate ($avgHr BPM) exceeds maximum heart rate ($maxHr BPM)")
        }

        // 7. Heart Rate Zones
        val (zones, zonesStatus) = extractHeartRateZones(cleanText)
        fieldStatus["heartRateZones"] = zonesStatus

        // 8. Training Effect
        val (trainingEffect, teStatus) = extractTrainingEffect(cleanText)
        fieldStatus["trainingEffect"] = teStatus

        // 9. Data Source
        val (dataSource, dsStatus) = extractDataSource(cleanText)
        fieldStatus["dataSource"] = dsStatus

        val isConsistent = discrepancies.isEmpty()
        val needsReview = discrepancies.isNotEmpty()

        return GymWorkoutValidationResult(
            workoutDate = wDate,
            workoutDateRaw = wDateRaw,
            workoutTime = wTime,
            workoutType = wType,
            durationSeconds = durSec,
            durationDisplay = durDisp,
            workoutCalories = wkCal,
            totalCalories = totCal,
            avgHeartRate = avgHr,
            maxHeartRate = maxHr,
            heartRateZones = zones,
            trainingEffect = trainingEffect,
            dataSource = dataSource,
            fieldStatus = fieldStatus,
            sourceImageId = sourceImageId,
            sourceIntegrityHash = sourceIntegrityHash,
            rawOcrText = cleanText,
            isConsistent = isConsistent,
            needsReview = needsReview,
            discrepancyReasons = discrepancies
        )
    }

    /**
     * Extracts date and time from OCR text.
     * Looks for combined "13/10/2026, 6:36 pm" or separate date and time.
     */
    private fun extractDateTime(
        text: String,
        defaultYear: Int,
        prefDay: Int?,
        prefMonth: Int?
    ): Tuple4<String?, String?, MiFitnessGymWorkoutRules.WorkoutClockTime?, FieldStatus> {
        // Pattern 1: Date and time together e.g. "13/10/2026, 6:36 pm"
        val combinedRegex = Regex(
            """(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})[,\s]+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm|AM|PM)?)"""
        )
        val m = combinedRegex.find(text)
        if (m != null) {
            val rawDate = m.groupValues[1]
            val rawTime = m.groupValues[2]
            val isoDate = MiFitnessGymWorkoutRules.normalizeDateToIso(rawDate, defaultYear, prefDay, prefMonth)
            val clockTime = MiFitnessGymWorkoutRules.parseClockTime(rawTime)
            if (isoDate != null) {
                return Tuple4(
                    isoDate,
                    rawDate,
                    clockTime,
                    FieldStatus("EXTRACTED", "${m.value} -> Date: $isoDate, Time: ${clockTime?.formatted12h}")
                )
            }
        }

        // Pattern 2: Text date with time e.g. "13 October 2026, 6:36 pm"
        val textDateRegex = Regex(
            """(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})[,\s]+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm|AM|PM)?)"""
        )
        val mText = textDateRegex.find(text)
        if (mText != null) {
            val rawDate = mText.groupValues[1]
            val rawTime = mText.groupValues[2]
            val isoDate = MiFitnessGymWorkoutRules.normalizeDateToIso(rawDate, defaultYear, prefDay, prefMonth)
            val clockTime = MiFitnessGymWorkoutRules.parseClockTime(rawTime)
            if (isoDate != null) {
                return Tuple4(
                    isoDate,
                    rawDate,
                    clockTime,
                    FieldStatus("EXTRACTED", "${mText.value} -> Date: $isoDate, Time: ${clockTime?.formatted12h}")
                )
            }
        }

        // Pattern 3: Separate date and separate time
        val dateOnlyRegex = Regex("""(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})""")
        val mDate = dateOnlyRegex.find(text)
        val rawDate = mDate?.groupValues?.get(1)
        val isoDate = rawDate?.let { MiFitnessGymWorkoutRules.normalizeDateToIso(it, defaultYear, prefDay, prefMonth) }

        val timeOnlyRegex = Regex("""\b(\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm|AM|PM))\b""")
        val mTime = timeOnlyRegex.find(text)
        val clockTime = mTime?.value?.let { MiFitnessGymWorkoutRules.parseClockTime(it) }

        return if (isoDate != null) {
            Tuple4(
                isoDate,
                rawDate,
                clockTime,
                FieldStatus("EXTRACTED", "Date: $isoDate, Time: ${clockTime?.formatted12h ?: "NOT_DETECTED"}")
            )
        } else {
            Tuple4(null, null, clockTime, FieldStatus("NOT_DETECTED", "No valid date found"))
        }
    }

    /**
     * Extracts workout type (e.g. Freestyle, Outdoor running, Treadmill, Gym, Strength training).
     */
    private fun extractWorkoutType(text: String): Pair<String?, FieldStatus> {
        val knownTypes = listOf(
            "Freestyle",
            "Outdoor running",
            "Treadmill",
            "Walking",
            "Outdoor cycling",
            "Indoor cycling",
            "Gym",
            "Strength training",
            "Strength",
            "Weightlifting",
            "HIIT",
            "Cross training",
            "Yoga",
            "Pilates",
            "Badminton",
            "Swimming",
            "Free training",
            "Elliptical",
            "Rowing machine",
            "Jump rope"
        )

        // Check if any known type appears at start of a line or before the date
        for (type in knownTypes) {
            val reg = Regex("""(?m)^[^\w]*""" + Regex.escape(type) + """[^\w]*$""", RegexOption.IGNORE_CASE)
            if (reg.containsMatchIn(text)) {
                return Pair(type, FieldStatus("EXTRACTED", type))
            }
        }

        // Fallback: look for known type anywhere in text before Mi Fitness
        for (type in knownTypes) {
            val reg = Regex("""\b""" + Regex.escape(type) + """\b""", RegexOption.IGNORE_CASE)
            val match = reg.find(text)
            if (match != null) {
                return Pair(type, FieldStatus("EXTRACTED", match.value))
            }
        }

        // Fallback: first non-empty word in text if it looks like a workout category
        val firstLine = text.lines().map { it.trim() }.firstOrNull { it.isNotBlank() }
        if (!firstLine.isNullOrBlank() && firstLine.length in 3..25 && !firstLine.contains("/") && !firstLine.contains(":")) {
            return Pair(firstLine, FieldStatus("EXTRACTED", firstLine))
        }

        return Pair(null, FieldStatus("NOT_DETECTED", "Workout type not detected"))
    }

    /**
     * Extracts workout duration in seconds and formatted string.
     */
    private fun extractDuration(text: String): Tuple3<Int?, String?, FieldStatus> {
        // Pattern 1: Row-by-row stats line: "01:04:01 444 kcal 109 BPM \n Time Total kcal Avg HR"
        val rowRegex = Regex(
            """(\d{1,2}:\d{2}:\d{2})\s+\d+\s*(?:kcal)?\s+\d+\s*(?:BPM)?[\s\r\n]+Time\s+Total\s*kcal""",
            RegexOption.IGNORE_CASE
        )
        val mRow = rowRegex.find(text)
        if (mRow != null) {
            val hms = mRow.groupValues[1]
            val sec = MiFitnessGymWorkoutRules.parseDurationToSeconds(hms)
            if (sec != null && sec > 0) {
                return Tuple3(sec, hms, FieldStatus("EXTRACTED", "$hms (${sec}s)"))
            }
        }

        // Pattern 2: HH:MM:SS above or adjacent to "Time" or "Duration"
        val timeLabelRegex = Regex(
            """(\d{1,2}:\d{2}:\d{2})[\s\r\n]+(?:Time|Duration)\b""",
            RegexOption.IGNORE_CASE
        )
        val mLabel = timeLabelRegex.find(text)
        if (mLabel != null) {
            val hms = mLabel.groupValues[1]
            val sec = MiFitnessGymWorkoutRules.parseDurationToSeconds(hms)
            if (sec != null && sec > 0) {
                return Tuple3(sec, hms, FieldStatus("EXTRACTED", "$hms (${sec}s)"))
            }
        }

        // Pattern 3: Label followed by time: "Duration: 01:04:01"
        val preLabelRegex = Regex(
            """(?:Time|Duration)[\s\r\n:]*(\d{1,2}:\d{2}:\d{2})""",
            RegexOption.IGNORE_CASE
        )
        val mPre = preLabelRegex.find(text)
        if (mPre != null) {
            val hms = mPre.groupValues[1]
            val sec = MiFitnessGymWorkoutRules.parseDurationToSeconds(hms)
            if (sec != null && sec > 0) {
                return Tuple3(sec, hms, FieldStatus("EXTRACTED", "$hms (${sec}s)"))
            }
        }

        // Pattern 4: Text duration like "1 hr 4 mins 1 sec"
        val textDurRegex = Regex(
            """(\d+\s*(?:hr|hrs|hour|hours)\s*\d+\s*(?:min|mins|minute|minutes)(?:\s*\d+\s*(?:sec|secs|second|seconds))?)""",
            RegexOption.IGNORE_CASE
        )
        val mText = textDurRegex.find(text)
        if (mText != null) {
            val raw = mText.groupValues[1]
            val sec = MiFitnessGymWorkoutRules.parseDurationToSeconds(raw)
            if (sec != null && sec > 0) {
                return Tuple3(sec, raw, FieldStatus("EXTRACTED", "$raw (${sec}s)"))
            }
        }

        // Fallback: any standalone HH:MM:SS that is not a clock time
        val anyHms = Regex("""\b(0[0-9]:[0-5][0-9]:[0-5][0-9]|1[0-2]:[0-5][0-9]:[0-5][0-9])\b""").findAll(text)
        for (match in anyHms) {
            val cand = match.value
            // Ensure not preceded by date
            val sec = MiFitnessGymWorkoutRules.parseDurationToSeconds(cand)
            if (sec != null && sec > 0 && sec != 0) {
                return Tuple3(sec, cand, FieldStatus("EXTRACTED", "$cand (${sec}s)"))
            }
        }

        return Tuple3(null, null, FieldStatus("NOT_DETECTED", "Duration not detected"))
    }

    /**
     * Extracts workout calories vs total calories.
     * Strictly preserves the distinction between workout calories and total calories.
     */
    private fun extractCalories(text: String): Tuple3<Int?, Int?, FieldStatus> {
        var totalCal: Int? = null
        var workoutCal: Int? = null

        // 1. Total Calories extraction
        // Pattern 1a: Row-by-row stats line: "01:04:01 444 kcal 109 BPM \n Time Total kcal Avg HR"
        val rowRegex = Regex(
            """\d{1,2}:\d{2}:\d{2}\s+(\d+)\s*(?:kcal)?\s+\d+\s*(?:BPM)?[\s\r\n]+Time\s+Total\s*kcal""",
            RegexOption.IGNORE_CASE
        )
        val mRow = rowRegex.find(text)
        if (mRow != null) {
            totalCal = mRow.groupValues[1].toIntOrNull()
        }

        // Pattern 1b: "444 kcal \n Total kcal"
        if (totalCal == null) {
            val totRegex1 = Regex("""(\d+)\s*(?:kcal)?[\s\r\n]+Total\s*(?:kcal|calories)""", RegexOption.IGNORE_CASE)
            val mTot1 = totRegex1.find(text)
            if (mTot1 != null) {
                totalCal = mTot1.groupValues[1].toIntOrNull()
            }
        }

        // Pattern 1c: "Total kcal: 444"
        if (totalCal == null) {
            val totRegex2 = Regex("""Total\s*(?:kcal|calories)[\s\r\n:]*(\d+)""", RegexOption.IGNORE_CASE)
            val mTot2 = totRegex2.find(text)
            if (mTot2 != null) {
                totalCal = mTot2.groupValues[1].toIntOrNull()
            }
        }

        // 2. Workout Calories extraction (prominent active calories)
        // Pattern 2a: Under Mi Fitness: "Mi Fitness \n 348 kcal"
        val miFitCalRegex = Regex("""Mi\s*Fitness[\s\r\n]+(\d+)\s*kcal""", RegexOption.IGNORE_CASE)
        val mMi = miFitCalRegex.find(text)
        if (mMi != null) {
            workoutCal = mMi.groupValues[1].toIntOrNull()
        }

        // Pattern 2b: Active calories label: "Active calories: 348 kcal"
        if (workoutCal == null) {
            val actRegex = Regex("""Active\s*(?:calories|kcal)[\s\r\n:]*(\d+)""", RegexOption.IGNORE_CASE)
            val mAct = actRegex.find(text)
            if (mAct != null) {
                workoutCal = mAct.groupValues[1].toIntOrNull()
            }
        }

        // Pattern 2c: First kcal number that is NOT total calories
        if (workoutCal == null) {
            val allKcals = Regex("""(\d+)\s*kcal""", RegexOption.IGNORE_CASE).findAll(text)
                .mapNotNull { it.groupValues[1].toIntOrNull() }
                .toList()
            if (allKcals.isNotEmpty()) {
                val candidate = allKcals.firstOrNull { it != totalCal }
                if (candidate != null) {
                    workoutCal = candidate
                } else if (totalCal == null) {
                    workoutCal = allKcals.first()
                }
            }
        }

        val statusStr = buildString {
            if (workoutCal != null) append("Workout: $workoutCal kcal ")
            if (totalCal != null) append("Total: $totalCal kcal")
        }.trim().ifBlank { "NOT_DETECTED" }

        val status = if (workoutCal != null || totalCal != null) "EXTRACTED" else "NOT_DETECTED"
        return Tuple3(workoutCal, totalCal, FieldStatus(status, statusStr))
    }

    /**
     * Extracts average and maximum heart rate in BPM.
     */
    private fun extractHeartRates(text: String): Tuple3<Int?, Int?, FieldStatus> {
        var avgHr: Int? = null
        var maxHr: Int? = null

        // 1. Avg HR from stats row: "01:04:01 444 kcal 109 BPM \n Time Total kcal Avg HR"
        val rowRegex = Regex(
            """\d{1,2}:\d{2}:\d{2}\s+\d+\s*(?:kcal)?\s+(\d+)\s*(?:BPM)?[\s\r\n]+Time\s+Total\s*kcal\s+Avg\s*HR""",
            RegexOption.IGNORE_CASE
        )
        val mRow = rowRegex.find(text)
        if (mRow != null) {
            avgHr = mRow.groupValues[1].toIntOrNull()
        }

        // Pattern 1b: "109 BPM \n Avg HR"
        if (avgHr == null) {
            val avgRegex = Regex("""(\d{2,3})\s*(?:BPM)?[\s\r\n]+Avg\s*HR""", RegexOption.IGNORE_CASE)
            val mAvg = avgRegex.find(text)
            if (mAvg != null) {
                avgHr = mAvg.groupValues[1].toIntOrNull()
            }
        }

        // Pattern 1c: "Avg HR: 109 BPM"
        if (avgHr == null) {
            val avgRegex2 = Regex("""Avg\s*HR[\s\r\n:]*(\d{2,3})""", RegexOption.IGNORE_CASE)
            val mAvg2 = avgRegex2.find(text)
            if (mAvg2 != null) {
                avgHr = mAvg2.groupValues[1].toIntOrNull()
            }
        }

        // 2. Max HR from Heart rate card: "150 Max BPM" or "Max BPM 150"
        val maxRegex = Regex("""(\d{2,3})\s*(?:Max\s*BPM|Max\s*HR)""", RegexOption.IGNORE_CASE)
        val mMax = maxRegex.find(text)
        if (mMax != null) {
            maxHr = mMax.groupValues[1].toIntOrNull()
        } else {
            val maxRegex2 = Regex("""(?:Max\s*BPM|Max\s*HR)[\s\r\n:]*(\d{2,3})""", RegexOption.IGNORE_CASE)
            val mMax2 = maxRegex2.find(text)
            if (mMax2 != null) {
                maxHr = mMax2.groupValues[1].toIntOrNull()
            }
        }

        // Pattern 2c: In Heart rate section row-by-row:
        // "Heart rate (BPM) \n 109 150 \n BPM Max BPM"
        if (avgHr == null || maxHr == null) {
            val hrCardRow = Regex(
                """Heart\s*rate[^\n\r]*[\s\r\n]+(\d{2,3})\s+(\d{2,3})[\s\r\n]+BPM\s+Max\s*BPM""",
                RegexOption.IGNORE_CASE
            )
            val mCard = hrCardRow.find(text)
            if (mCard != null) {
                if (avgHr == null) avgHr = mCard.groupValues[1].toIntOrNull()
                if (maxHr == null) maxHr = mCard.groupValues[2].toIntOrNull()
            }
        }

        val statusStr = buildString {
            if (avgHr != null) append("Avg: $avgHr BPM ")
            if (maxHr != null) append("Max: $maxHr BPM")
        }.trim().ifBlank { "NOT_DETECTED" }

        val status = if (avgHr != null || maxHr != null) "EXTRACTED" else "NOT_DETECTED"
        return Tuple3(avgHr, maxHr, FieldStatus(status, statusStr))
    }

    /**
     * Extracts heart-rate zones and their durations:
     * - Light
     * - Intensive
     * - Aerobic
     * - Anaerobic
     * - VO2 max
     */
    private fun extractHeartRateZones(text: String): Pair<Map<String, String>, FieldStatus> {
        val zonesMap = mutableMapOf<String, String>()
        val zoneDefs = listOf(
            "Light" to listOf("Light"),
            "Intensive" to listOf("Intensive", "Inten\\s*sive"),
            "Aerobic" to listOf("Aerobic", "Aero\\s*bic"),
            "Anaerobic" to listOf("Anaerobic", "Anae\\s*robic"),
            "VO2 max" to listOf("VO[2₂]\\s*max", "VO[2₂]max")
        )

        for ((canonicalName, aliases) in zoneDefs) {
            for (alias in aliases) {
                val reg = Regex(
                    """(?:^|[\s\r\n])""" + alias + """[^\d\r\n]*(\d{1,2}:\d{2}:\d{2})""",
                    RegexOption.IGNORE_CASE
                )
                val m = reg.find(text)
                if (m != null) {
                    zonesMap[canonicalName] = m.groupValues[1]
                    break
                }
            }
        }

        val status = if (zonesMap.isNotEmpty()) "EXTRACTED" else "NOT_DETECTED"
        val summary = zonesMap.entries.joinToString(", ") { "${it.key}: ${it.value}" }.ifBlank { "Zones not detected" }
        return Pair(zonesMap, FieldStatus(status, summary))
    }

    /**
     * Extracts training effect (e.g. "+9 Vitality score").
     */
    private fun extractTrainingEffect(text: String): Pair<String?, FieldStatus> {
        val reg = Regex(
            """Training\s*effect[^\n\r]*[\s\r\n]+(?:\+)?(\d+)[^\n\r]*Vitality\s*score""",
            RegexOption.IGNORE_CASE
        )
        val m = reg.find(text)
        if (m != null) {
            val score = m.groupValues[1]
            val full = "+$score Vitality score"
            return Pair(full, FieldStatus("EXTRACTED", full))
        }

        val reg2 = Regex("""(?:\+)?(\d+)\s*Vitality\s*score""", RegexOption.IGNORE_CASE)
        val m2 = reg2.find(text)
        if (m2 != null) {
            val score = m2.groupValues[1]
            val full = "+$score Vitality score"
            return Pair(full, FieldStatus("EXTRACTED", full))
        }

        return Pair(null, FieldStatus("NOT_DETECTED", "Training effect not detected"))
    }

    /**
     * Extracts device data source (e.g. "Redmi Watch 5 Lite").
     */
    private fun extractDataSource(text: String): Pair<String?, FieldStatus> {
        // Pattern 1: Under "Data source" header
        val dsRegex = Regex("""Data\s*source[\s\r\n]+([^\r\n]+)""", RegexOption.IGNORE_CASE)
        val m = dsRegex.find(text)
        if (m != null) {
            val candidate = m.groupValues[1].trim()
            if (candidate.isNotBlank() && !candidate.equals("Mi Fitness", ignoreCase = true)) {
                return Pair(candidate, FieldStatus("EXTRACTED", candidate))
            }
        }

        // Pattern 2: Known wearable devices
        val knownDevices = listOf(
            "Redmi Watch 5 Lite",
            "Redmi Watch 5 Active",
            "Redmi Watch 4",
            "Redmi Watch 3",
            "Xiaomi Smart Band 8",
            "Xiaomi Smart Band 9",
            "Xiaomi Watch S3",
            "Xiaomi Watch 2"
        )
        for (device in knownDevices) {
            if (text.contains(device, ignoreCase = true)) {
                return Pair(device, FieldStatus("EXTRACTED", device))
            }
        }

        return Pair(null, FieldStatus("NOT_DETECTED", "Data source not detected"))
    }

    private data class Tuple3<A, B, C>(val a: A, val b: B, val c: C)
    private data class Tuple4<A, B, C, D>(val a: A, val b: B, val c: C, val d: D)
}
