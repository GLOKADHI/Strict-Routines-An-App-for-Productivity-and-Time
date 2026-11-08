package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

/**
 * Pure calculation rules and state machine for Official Event Booking & Scheduled Exit.
 * Synchronizes with Windows EXE via OFFICIAL_EVENT_BOOKING and OFFICIAL_EVENT_CANCEL.
 */
object OfficialEventBookingRules {

    // ── Canonical Lifecycle States ──────────────────────────────
    const val STATE_DRAFT = "DRAFT"
    const val STATE_NEEDS_INFO = "NEEDS_INFO"
    const val STATE_AWAITING_USER_CONFIRMATION = "AWAITING_USER_CONFIRMATION"
    const val STATE_CONFIRMED = "CONFIRMED"
    const val STATE_CONFLICT_DETECTED = "CONFLICT_DETECTED"
    const val STATE_REJECTED = "REJECTED"
    const val STATE_EXPIRED = "EXPIRED"
    const val STATE_CANCELLED = "CANCELLED"
    const val STATE_COMPLETED = "COMPLETED"

    // ── Candidate Shortlist Distinctions ────────────────────────
    const val SHORTLIST_CONFIRMED_INDIVIDUAL = "CONFIRMED_INDIVIDUAL"
    const val SHORTLIST_ANNOUNCED_UNVERIFIED_INDIVIDUAL = "ANNOUNCED_UNVERIFIED_INDIVIDUAL"
    const val SHORTLIST_NOT_APPLICABLE = "NOT_APPLICABLE"

    // ── Event Categories ────────────────────────────────────────
    const val CAT_COLLEGE_EXAM = "COLLEGE_EXAM"
    const val CAT_COLLEGE_INTERNAL_ASSESSMENT = "COLLEGE_INTERNAL_ASSESSMENT"
    const val CAT_ONLINE_ASSESSMENT = "ONLINE_ASSESSMENT"
    const val CAT_VIRTUAL_INTERVIEW = "VIRTUAL_INTERVIEW"
    const val CAT_CAMPUS_RECRUITMENT = "CAMPUS_RECRUITMENT"
    const val CAT_OTHER_OFFICIAL = "OTHER_OFFICIAL"

    // ── Event Modes ─────────────────────────────────────────────
    const val MODE_VIRTUAL = "VIRTUAL"
    const val MODE_IN_PERSON = "IN_PERSON"
    const val MODE_HYBRID = "HYBRID"

    // Default booking horizon: up to 30 days in the future
    const val MAX_BOOKING_HORIZON_DAYS = 30

    data class ValidationResult(
        val valid: Boolean,
        val status: String,
        val errors: List<String>,
        val warnings: List<String>,
        val shortlistStatus: String,
        val calculatedEndTime: String? = null,
        val durationMinutes: Int = 0
    )

    data class ExitWindowStatus(
        val inWindow: Boolean,
        val canExit: Boolean,
        val message: String,
        val windowStartIso: String = "",
        val windowEndIso: String = "",
        val timeRemainingMinutes: Long = 0L
    )

    /**
     * Strips AI Overview summaries (e.g. Gmail Gemini AI summaries) from raw evidence text
     * so that automated summaries are never mistaken for primary source proof.
     */
    fun cleanEvidenceText(rawText: String): String {
        if (rawText.isBlank()) return ""
        var cleaned = rawText
        val aiOverviewPatterns = listOf(
            Regex("""(?is)\*\s*AI Overview.*?(?=\n\s*\n[A-Z0-9]|\Z)"""),
            Regex("""(?is)AI Overview:?.*?(?=\n\s*\n[A-Z0-9]|\Z)"""),
            Regex("""(?is)Generative AI is experimental.*?\n""")
        )
        for (pattern in aiOverviewPatterns) {
            cleaned = pattern.replace(cleaned, "\n[AI Overview Block Excluded From Evidence]\n")
        }
        return cleaned.trim()
    }

    /**
     * Parses key schedule details from text using regex heuristics.
     */
    fun parseScheduleDetails(rawText: String): JSONObject {
        val text = cleanEvidenceText(rawText)
        val result = JSONObject()

        // 1. Detect Category
        var category = CAT_OTHER_OFFICIAL
        val textLower = text.lowercase(Locale.US)
        when {
            textLower.contains("practical exam") || textLower.contains("internal assessment") || textLower.contains("lab exam") ->
                category = CAT_COLLEGE_INTERNAL_ASSESSMENT
            textLower.contains("semester exam") || textLower.contains("university exam") || textLower.contains("board exam") ->
                category = CAT_COLLEGE_EXAM
            textLower.contains("technical interview") || textLower.contains("virtual interview") || textLower.contains("video interview") ->
                category = CAT_VIRTUAL_INTERVIEW
            textLower.contains("campus recruitment") || textLower.contains("placement drive") || textLower.contains("hiring drive") ->
                category = CAT_CAMPUS_RECRUITMENT
            textLower.contains("online assessment") || textLower.contains("technical test") || textLower.contains("aptitude test") || textLower.contains("coding test") ->
                category = CAT_ONLINE_ASSESSMENT
            textLower.contains("interview") ->
                category = CAT_VIRTUAL_INTERVIEW
            textLower.contains("exam") || textLower.contains("examination") ->
                category = CAT_COLLEGE_EXAM
        }
        result.put("category", category)

        // 2. Organization / College
        var org = ""
        val orgPatterns = listOf(
            Regex("""(?i)\b(HCLTech|HCL|TCS|Infosys|Cognizant|Wipro|Amazon|Google|Microsoft|Accenture|Zoho|Capgemini)\b"""),
            Regex("""(?i)\b([A-Z][A-Za-z0-9&.\s]{2,30}?(?:University|College|Institute|Technologies|Solutions|Corporation|Pvt\s*Ltd|Ltd))\b"""),
            Regex("""(?i)\b(Sathyabama|VIT|SRM|Anna University|IIT|NIT)\b""")
        )
        for (p in orgPatterns) {
            val m = p.find(text)
            if (m != null) {
                org = m.groupValues[1].trim()
                break
            }
        }
        result.put("organization", org)

        // 3. Event Title
        var title = ""
        when {
            org.isNotBlank() && category == CAT_VIRTUAL_INTERVIEW -> title = "$org Technical Interview"
            org.isNotBlank() && category == CAT_ONLINE_ASSESSMENT -> title = "$org Online Assessment"
            org.isNotBlank() && category == CAT_CAMPUS_RECRUITMENT -> title = "$org Campus Recruitment"
            org.isNotBlank() && category.contains("EXAM") -> title = "$org Examination"
            else -> {
                val titleMatch = Regex("""(?i)(?:Subject|Regarding|Event|Title):\s*([^\n\r]{4,60})""").find(text)
                title = titleMatch?.groupValues?.get(1)?.trim() ?: if (org.isNotBlank()) "$org Official Event" else "Official Academic / Recruitment Event"
            }
        }
        result.put("title", title)

        // 4. Date extraction
        var eventDate = ""
        val datePatterns = listOf(
            Regex("""\b(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b"""),
            Regex("""\b(0[1-9]|[12]\d|3[01])[-/](0[1-9]|1[0-2])[-/](\d{4})\b"""),
            Regex("""\b(0[1-9]|[12]\d|3[01])\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[,\s]+(\d{4})\b""", RegexOption.IGNORE_CASE),
            Regex("""\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(0[1-9]|[12]\d|3[01])[,\s]+(\d{4})\b""", RegexOption.IGNORE_CASE)
        )

        for (p in datePatterns) {
            val m = p.find(text)
            if (m != null) {
                try {
                    val rawMatch = m.value
                    val parsedDate = parseFlexibleDate(rawMatch)
                    if (parsedDate != null) {
                        eventDate = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(parsedDate)
                        break
                    }
                } catch (_: Exception) {}
            }
        }
        result.put("event_date", eventDate)

        // 5. Time extraction (Start time, End time, Reporting time)
        var startTime = ""
        var endTime = ""
        var reportingTime = ""

        // Reporting time
        val repMatch = Regex("""(?i)(?:Reporting|Report)\s*(?:Time)?\s*[:\-]?\s*([0-1]?[0-9]|2[0-3]):?([0-5][0-9])?\s*(AM|PM)?""").find(text)
        if (repMatch != null) {
            reportingTime = normalizeTimeString(repMatch.value)
        }
        result.put("reporting_time", reportingTime)

        // Start and End time interval e.g. "9:00 AM - 11:00 AM" or "09:00 to 11:00"
        val intervalMatch = Regex("""(?i)\b([0-1]?[0-9]|2[0-3])(?::([0-5][0-9]))?\s*(AM|PM)?\s*(?:-|–|to)\s*([0-1]?[0-9]|2[0-3])(?::([0-5][0-9]))?\s*(AM|PM)?\b""").find(text)
        if (intervalMatch != null) {
            val parts = intervalMatch.value.split(Regex("""(?i)\s*(?:-|–|to)\s*"""))
            if (parts.size == 2) {
                startTime = normalizeTimeString(parts[0])
                endTime = normalizeTimeString(parts[1])
            }
        } else {
            // Find solitary time labeled as Start or Exam time
            val startMatch = Regex("""(?i)(?:Start(?:ing)?\s*Time|Time|Slot)\s*[:\-]?\s*([0-1]?[0-9]|2[0-3]):([0-5][0-9])\s*(AM|PM)?""").find(text)
            if (startMatch != null) {
                startTime = normalizeTimeString(startMatch.value)
            }
        }
        result.put("start_time", startTime)
        result.put("end_time", endTime)

        // Expected duration
        var durationMinutes = 0
        val durMatch = Regex("""(?i)\b(\d+)\s*(?:hours?|hrs?)\s*(?:(\d+)\s*(?:minutes?|mins?))?\b""").find(text)
        if (durMatch != null) {
            val hrs = durMatch.groupValues[1].toIntOrNull() ?: 0
            val mins = durMatch.groupValues[2].toIntOrNull() ?: 0
            durationMinutes = hrs * 60 + mins
        } else {
            val minMatch = Regex("""(?i)\b(\d+)\s*(?:minutes?|mins?)\b""").find(text)
            if (minMatch != null) {
                durationMinutes = minMatch.groupValues[1].toIntOrNull() ?: 0
            }
        }
        result.put("expected_duration_minutes", durationMinutes)

        // 6. Mode & Venue/Platform
        var mode = MODE_IN_PERSON
        var venue = ""
        when {
            textLower.contains("zoom") || textLower.contains("teams") || textLower.contains("google meet") || textLower.contains("hackerrank") || textLower.contains("hackerearth") || textLower.contains("amcat") || textLower.contains("online") -> {
                mode = MODE_VIRTUAL
                val platMatch = Regex("""(?i)\b(Zoom|Microsoft Teams|Teams|Google Meet|Webex|HackerRank|HackerEarth|Mettl|Superset|AMCAT)\b""").find(text)
                venue = platMatch?.value ?: "Online Meeting"
            }
            textLower.contains("in-person") || textLower.contains("offline") || textLower.contains("campus") || textLower.contains("hall") || textLower.contains("room") || textLower.contains("audi") -> {
                mode = MODE_IN_PERSON
                val venueMatch = Regex("""(?i)(?:Venue|Location|Room|Hall)\s*[:\-]?\s*([^\n\r,]{3,40})""").find(text)
                venue = venueMatch?.groupValues?.get(1)?.trim() ?: (if (org.isNotBlank()) "$org Campus" else "Campus Venue")
            }
        }
        result.put("event_mode", mode)
        result.put("venue_or_platform", venue)

        // 7. Shortlist Status
        // Distinguish general placement announcements from verified individual shortlisting
        var shortlistStatus = SHORTLIST_CONFIRMED_INDIVIDUAL
        val generalAnnouncementPhrases = listOf(
            "attached is the list of",
            "list of eligible students",
            "list of shortlisted candidates",
            "shortlisted students are requested",
            "all registered students",
            "eligible students must attend",
            "circular to all",
            "placement notice"
        )
        for (phrase in generalAnnouncementPhrases) {
            if (textLower.contains(phrase)) {
                shortlistStatus = SHORTLIST_ANNOUNCED_UNVERIFIED_INDIVIDUAL
                break
            }
        }
        result.put("shortlist_status", shortlistStatus)
        result.put("time_zone", "Asia/Kolkata")

        return result
    }

    /**
     * Validates proposed event booking.
     */
    fun validateEventBooking(event: JSONObject, existingEvents: List<JSONObject> = emptyList()): ValidationResult {
        val errors = mutableListOf<String>()
        val warnings = mutableListOf<String>()

        val title = event.optString("title", "").trim()
        val eventDate = event.optString("event_date", "").trim()
        val startTime = event.optString("start_time", "").trim()
        var endTime = event.optString("end_time", "").trim()
        var durationMinutes = event.optInt("expected_duration_minutes", 0)
        val shortlistStatus = event.optString("shortlist_status", SHORTLIST_CONFIRMED_INDIVIDUAL)

        if (title.isBlank()) {
            errors.add("Event title is required.")
        }

        // 1. Check Date
        val sdfDate = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { isLenient = false }
        val parsedEventDate: Date? = try {
            if (eventDate.isNotBlank()) sdfDate.parse(eventDate) else null
        } catch (_: Exception) { null }

        if (parsedEventDate == null) {
            errors.add("A valid event date (YYYY-MM-DD) is required.")
        } else {
            // Check horizon
            val todayCal = Calendar.getInstance().apply {
                set(Calendar.HOUR_OF_DAY, 0)
                set(Calendar.MINUTE, 0)
                set(Calendar.SECOND, 0)
                set(Calendar.MILLISECOND, 0)
            }
            val eventCal = Calendar.getInstance().apply { time = parsedEventDate }
            val diffDays = (eventCal.timeInMillis - todayCal.timeInMillis) / (24 * 60 * 60 * 1000L)

            if (diffDays < -1) {
                errors.add("Cannot book events in the past.")
            } else if (diffDays > MAX_BOOKING_HORIZON_DAYS) {
                warnings.add("Event is booked more than $MAX_BOOKING_HORIZON_DAYS days in advance.")
            }
        }

        // 2. Start Time
        if (startTime.isBlank()) {
            errors.add("Start time is missing. Please enter start time explicitly.")
        }

        // 3. End Time or Duration
        if (endTime.isBlank() && durationMinutes <= 0) {
            errors.add("End time or expected duration is required. Cannot infer arbitrary duration.")
        }

        // Calculate end time if only duration provided
        if (startTime.isNotBlank() && endTime.isBlank() && durationMinutes > 0) {
            endTime = calculateEndTimeFromDuration(startTime, durationMinutes)
        } else if (startTime.isNotBlank() && endTime.isNotBlank() && durationMinutes <= 0) {
            durationMinutes = calculateDurationMinutes(startTime, endTime)
        }

        // 4. Validate start < end
        if (startTime.isNotBlank() && endTime.isNotBlank()) {
            val startMins = parseTimeToDayMinutes(startTime)
            val endMins = parseTimeToDayMinutes(endTime)
            if (startMins != null && endMins != null) {
                if (endMins <= startMins) {
                    // Check if it's explicitly an overnight event or error
                    val isOvernight = event.optBoolean("is_overnight", false)
                    if (!isOvernight) {
                        errors.add("End time ($endTime) must be after start time ($startTime).")
                    }
                }
            }
        }

        // 5. Shortlist distinction warning
        if (shortlistStatus == SHORTLIST_ANNOUNCED_UNVERIFIED_INDIVIDUAL) {
            warnings.add("Placement announcement detected without individual shortlisted name confirmation. User confirmation required.")
        }

        // 6. Conflicting events check
        if (parsedEventDate != null && startTime.isNotBlank() && endTime.isNotBlank()) {
            val eventStartEpoch = getEpochMs(eventDate, startTime)
            val eventEndEpoch = getEpochMs(eventDate, endTime)
            val currentEventId = event.optString("event_id", "")

            for (other in existingEvents) {
                val otherId = other.optString("event_id", "")
                if (otherId == currentEventId) continue
                val otherStatus = other.optString("status", "")
                if (otherStatus in listOf(STATE_CANCELLED, STATE_REJECTED, STATE_EXPIRED)) continue

                val otherDate = other.optString("event_date", "")
                val otherStart = other.optString("start_time", "")
                val otherEnd = other.optString("end_time", "")

                if (otherDate == eventDate && otherStart.isNotBlank() && otherEnd.isNotBlank()) {
                    val oStart = getEpochMs(otherDate, otherStart)
                    val oEnd = getEpochMs(otherDate, otherEnd)
                    if (eventStartEpoch < oEnd && eventEndEpoch > oStart) {
                        warnings.add("Conflicts with existing booking '${other.optString("title")}' ($otherStart - $otherEnd).")
                    }
                }
            }
        }

        val isValid = errors.isEmpty()
        val finalStatus = when {
            !isValid -> STATE_NEEDS_INFO
            warnings.any { it.contains("Conflicts") } -> STATE_CONFLICT_DETECTED
            else -> STATE_AWAITING_USER_CONFIRMATION
        }

        return ValidationResult(
            valid = isValid,
            status = finalStatus,
            errors = errors,
            warnings = warnings,
            shortlistStatus = shortlistStatus,
            calculatedEndTime = endTime,
            durationMinutes = durationMinutes
        )
    }

    /**
     * Evaluates if the current moment is within the confirmed event's exit window.
     * Strict rules:
     * - Only active between start_time and end_time.
     * - Before start_time: not available.
     * - After end_time: expired.
     * - If exit_consumed == true: already used.
     * - Status must be CONFIRMED.
     */
    fun evaluateExitWindow(event: JSONObject, currentMs: Long = System.currentTimeMillis()): ExitWindowStatus {
        val status = event.optString("status", "")
        val exitConsumed = event.optBoolean("exit_consumed", false)
        val eventDate = event.optString("event_date", "")
        val startTime = event.optString("start_time", "")
        val endTime = event.optString("end_time", "")
        val timeZoneStr = event.optString("time_zone", "Asia/Kolkata")

        if (status != STATE_CONFIRMED) {
            return ExitWindowStatus(
                inWindow = false,
                canExit = false,
                message = "Booking is not confirmed (Current status: $status)."
            )
        }

        if (exitConsumed) {
            return ExitWindowStatus(
                inWindow = false,
                canExit = false,
                message = "The scheduled-event exit for this booking has already been used."
            )
        }

        if (eventDate.isBlank() || startTime.isBlank() || endTime.isBlank()) {
            return ExitWindowStatus(
                inWindow = false,
                canExit = false,
                message = "Event schedule times are incomplete."
            )
        }

        val startMs = getEpochMs(eventDate, startTime, timeZoneStr)
        val endMs = getEpochMs(eventDate, endTime, timeZoneStr)

        val windowStartIso = formatEpochToIso(startMs)
        val windowEndIso = formatEpochToIso(endMs)

        if (currentMs < startMs) {
            val minsUntil = (startMs - currentMs) / (60 * 1000L)
            return ExitWindowStatus(
                inWindow = false,
                canExit = false,
                message = "Scheduled-event exit is not yet available. Opens at $startTime (in $minsUntil mins).",
                windowStartIso = windowStartIso,
                windowEndIso = windowEndIso,
                timeRemainingMinutes = minsUntil
            )
        }

        if (currentMs >= endMs) {
            return ExitWindowStatus(
                inWindow = false,
                canExit = false,
                message = "The scheduled event window ended at $endTime and has expired.",
                windowStartIso = windowStartIso,
                windowEndIso = windowEndIso,
                timeRemainingMinutes = 0L
            )
        }

        // Inside window!
        val minsRemaining = (endMs - currentMs) / (60 * 1000L)
        return ExitWindowStatus(
            inWindow = true,
            canExit = true,
            message = "Exit for Scheduled Event is available ($minsRemaining minutes remaining).",
            windowStartIso = windowStartIso,
            windowEndIso = windowEndIso,
            timeRemainingMinutes = minsRemaining
        )
    }

    /**
     * Builds sync payload for the event to dispatch via OFFICIAL_EVENT_BOOKING.
     */
    fun buildSyncPayload(event: JSONObject, accountId: String = "user_lok"): JSONObject {
        return JSONObject().apply {
            put("event_id", event.optString("event_id", "evt_" + UUID.randomUUID().toString().replace("-", "")))
            put("account_id", event.optString("account_id", accountId))
            put("title", event.optString("title"))
            put("category", event.optString("category", CAT_OTHER_OFFICIAL))
            put("organization", event.optString("organization"))
            put("event_date", event.optString("event_date"))
            put("start_time", event.optString("start_time"))
            put("end_time", event.optString("end_time"))
            put("expected_duration_minutes", event.optInt("expected_duration_minutes", 0))
            put("time_zone", event.optString("time_zone", "Asia/Kolkata"))
            put("event_mode", event.optString("event_mode", MODE_IN_PERSON))
            put("venue_or_platform", event.optString("venue_or_platform"))
            put("reporting_time", event.optString("reporting_time"))
            put("shortlist_status", event.optString("shortlist_status", SHORTLIST_CONFIRMED_INDIVIDUAL))
            put("status", event.optString("status", STATE_CONFIRMED))
            put("version", event.optLong("version", 1L))
            put("user_entered_fields", event.optJSONArray("user_entered_fields") ?: JSONArray())
            put("evidence_references", event.optJSONArray("evidence_references") ?: JSONArray())
            put("created_at", event.optString("created_at", SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())))
        }
    }

    // ── Helper Utilities ────────────────────────────────────────

    private fun parseFlexibleDate(raw: String): Date? {
        val formats = listOf(
            "yyyy-MM-dd",
            "dd-MM-yyyy",
            "dd/MM/yyyy",
            "d MMMM yyyy",
            "dd MMMM yyyy",
            "d MMM yyyy",
            "dd MMM yyyy",
            "MMMM d, yyyy",
            "MMM d, yyyy"
        )
        for (fmt in formats) {
            try {
                val sdf = SimpleDateFormat(fmt, Locale.US).apply { isLenient = false }
                return sdf.parse(raw)
            } catch (_: Exception) {}
        }
        return null
    }

    private fun normalizeTimeString(raw: String): String {
        val m = Regex("""(?i)\b([0-1]?[0-9]|2[0-3])(?::([0-5][0-9]))?\s*(AM|PM)?\b""").find(raw) ?: return raw.trim()
        var hr = m.groupValues[1].toInt()
        val min = if (m.groupValues[2].isNotBlank()) m.groupValues[2].toInt() else 0
        val ampm = m.groupValues[3].uppercase(Locale.US)

        if (ampm == "PM" && hr < 12) hr += 12
        if (ampm == "AM" && hr == 12) hr = 0

        return "%02d:%02d".format(hr, min)
    }

    private fun parseTimeToDayMinutes(timeStr: String): Int? {
        val parts = timeStr.trim().split(":")
        if (parts.size != 2) return null
        val h = parts[0].toIntOrNull() ?: return null
        val m = parts[1].toIntOrNull() ?: return null
        return h * 60 + m
    }

    private fun calculateEndTimeFromDuration(startTime: String, durationMins: Int): String {
        val startDayMins = parseTimeToDayMinutes(startTime) ?: return ""
        val endDayMins = (startDayMins + durationMins) % (24 * 60)
        val h = endDayMins / 60
        val m = endDayMins % 60
        return "%02d:%02d".format(h, m)
    }

    private fun calculateDurationMinutes(startTime: String, endTime: String): Int {
        val s = parseTimeToDayMinutes(startTime) ?: return 0
        val e = parseTimeToDayMinutes(endTime) ?: return 0
        return if (e >= s) e - s else (24 * 60 - s + e)
    }

    private fun getEpochMs(dateStr: String, timeStr: String, timeZoneStr: String = "Asia/Kolkata"): Long {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            timeZone = TimeZone.getTimeZone(timeZoneStr)
        }
        return try {
            sdf.parse("$dateStr $timeStr")?.time ?: 0L
        } catch (_: Exception) {
            0L
        }
    }

    private fun formatEpochToIso(epochMs: Long): String {
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
        return sdf.format(Date(epochMs))
    }
}
