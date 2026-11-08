package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.math.max
import kotlin.math.min

/**
 * Pure calculation rules and state machine for Movie Pre-booking.
 *
 * Rules:
 * 1. Attendance lock envelope:
 *    - Attendance lock start = movie show start - 1 hour 30 minutes (90 minutes)
 *    - Attendance lock end = movie show end + 1 hour 30 minutes (90 minutes)
 *    Provides reasonable buffer for travel to cinema, entry, film, and departure.
 *
 * 2. Hourly attendance slot hold:
 *    - An hourly slot [slotStartMs, slotEndMs] is held/excused if its activity period
 *      falls within the attendance lock envelope:
 *      (slotStartMs >= lockStartMs && slotEndMs <= lockEndMs).
 *    - Standard 5:00 PM - 8:00 PM example:
 *      Envelope is 3:30 PM - 9:30 PM.
 *      Affected slots are:
 *        4:00–5:00 PM (Held/excused)
 *        5:00–6:00 PM (Held/excused)
 *        6:00–7:00 PM (Held/excused)
 *        7:00–8:00 PM (Held/excused)
 *        8:00–9:00 PM (Held/excused)
 *      Slots 3:00–4:00 PM and after 9:30 PM are NOT excused.
 *
 * 3. Movie proof-submission window:
 *    - Proof window opens = validated show finish - 5 minutes (start-inclusive)
 *    - Proof deadline = validated show finish + 1 hour (end-exclusive)
 *
 * 4. Ticket Reading & Validation:
 *    - Reads visible text content via OCR without requiring or decoding QR codes.
 *    - Extracts title, cinema, show date, start time, end time / duration, approx indicator, screen, seat, booking ID.
 *    - Validates ticket date against intended booking date side-by-side.
 *    - Flags expired/past shows, ambiguous years, or date mismatches.
 */
object MovieBookingRules {

    // ── Canonical Lifecycle States ──────────────────────────────
    const val STATE_TICKET_SUBMISSION_PENDING = "Ticket submission pending"
    const val STATE_TICKET_VERIFICATION_PENDING = "Ticket verification pending"
    const val STATE_NEEDS_REVIEW = "Needs review"
    const val STATE_MOVIE_BOOKING_CONFIRMED = "Movie booking confirmed"
    const val STATE_ATTENDANCE_HOLD_ACTIVE = "Attendance hold active"
    const val STATE_RESERVATION_COMPLETED = "Reservation completed"
    const val STATE_BOOKING_REJECTED = "Booking rejected"
    const val STATE_BOOKING_CANCELLED = "Booking cancelled"

    // 90 minutes buffer before start and after end
    const val BUFFER_BEFORE_START_MS = 90L * 60L * 1000L
    const val BUFFER_AFTER_END_MS = 90L * 60L * 1000L

    // Proof submission window: 5 min before show finish to 60 min after show finish
    const val PROOF_WINDOW_OPEN_BEFORE_END_MS = 5L * 60L * 1000L
    const val PROOF_WINDOW_DEADLINE_AFTER_END_MS = 60L * 60L * 1000L

    // Reliable movie runtime metadata dictionary (minutes) for known/recent releases
    val KNOWN_MOVIE_RUNTIMES: Map<String, Int> = mapOf(
        "dune: part two" to 166,
        "dune 2" to 166,
        "oppenheimer" to 180,
        "interstellar" to 169,
        "avatar: the way of water" to 192,
        "avatar 2" to 192,
        "kalki 2898 ad" to 181,
        "devara" to 178,
        "vettaiyan" to 163,
        "gladiator ii" to 148,
        "gladiator 2" to 148,
        "joker: folie a deux" to 138,
        "joker 2" to 138,
        "deadpool & wolverine" to 128,
        "pushpa 2: the rule" to 200,
        "pushpa 2" to 200,
        "inception" to 148,
        "the batman" to 176,
        "spider-man: across the spider-verse" to 140,
        "stree 2" to 149,
        "salaar" to 175,
        "animal" to 201,
        "leo" to 164,
        "jailer" to 168,
        "jawan" to 169,
        "pathaan" to 146,
        "fighter" to 166
    )

    data class AttendanceLock(
        val showDate: String,
        val movieStartMs: Long,
        val movieEndMs: Long,
        val movieStartTime: String,
        val movieEndTime: String,
        val lockStartMs: Long,
        val lockEndMs: Long,
        val lockStartTime: String,
        val lockEndTime: String,
        val durationMinutes: Int,
        val affectedHourlySlots: List<String>
    ) {
        val showStartMs: Long get() = movieStartMs
        val showEndMs: Long get() = movieEndMs
    }

    data class MovieTicketInfo(
        val movieTitle: String,
        val cinemaName: String,
        val showDate: String,
        val startTime: String,
        val endTime: String?,
        val durationMinutes: Int,
        val durationEstablishedReliably: Boolean,
        val durationSource: String, // "TICKET_SHOW_TIMES", "TICKET_EXPLICIT_RUNTIME", "KNOWN_METADATA", "UNRESOLVED"
        val screen: String,
        val seat: String,
        val bookingRef: String,
        val qrCodeDetected: Boolean,
        val qrCodeIgnoredForValidation: Boolean = true,
        val rawOcrText: String,
        val validationStatus: String,
        val validationMessage: String,
        val isApproximateEndTime: Boolean = false,
        val ticketQuantity: Int? = null,
        val language: String = "",
        val format: String = "",
        val rating: String = "",
        val bookingCode: String = "",
        val ticketDateRaw: String = "",
        val yearOmittedInTicket: Boolean = false,
        val intendedDate: String = "",
        val isDateMismatch: Boolean = false,
        val isExpired: Boolean = false,
        val proofWindowOpenMs: Long = 0L,
        val proofDeadlineMs: Long = 0L,
        val proofWindowOpenTime: String = "",
        val proofDeadlineTime: String = "",
        val reservationStartMs: Long = 0L,
        val reservationEndMs: Long = 0L,
        val reservationStartTime: String = "",
        val reservationEndTime: String = "",
        val affectedHourlySlots: List<String> = emptyList()
    ) {
        val seats: String get() = seat
        val bookingReference: String get() = bookingRef
        fun toJson(): JSONObject = JSONObject().apply {
            put("movie_title", movieTitle)
            put("cinema_name", cinemaName)
            put("show_date", showDate)
            put("ticket_date_raw", ticketDateRaw)
            put("year_omitted_in_ticket", yearOmittedInTicket)
            put("intended_date", intendedDate)
            put("is_date_mismatch", isDateMismatch)
            put("is_expired", isExpired)
            put("start_time", startTime)
            put("end_time", endTime ?: "")
            put("is_approximate_end_time", isApproximateEndTime)
            put("duration_minutes", durationMinutes)
            put("duration_established_reliably", durationEstablishedReliably)
            put("duration_source", durationSource)
            put("ticket_quantity", ticketQuantity ?: 0)
            put("screen", screen)
            put("seat", seat)
            put("booking_code", bookingCode)
            put("booking_ref", bookingRef)
            put("language", language)
            put("format", format)
            put("rating", rating)
            put("qr_code_detected", qrCodeDetected)
            put("qr_code_ignored_for_validation", qrCodeIgnoredForValidation)
            put("proof_window_open_ms", proofWindowOpenMs)
            put("proof_deadline_ms", proofDeadlineMs)
            put("proof_window_open_time", proofWindowOpenTime)
            put("proof_deadline_time", proofDeadlineTime)
            put("reservation_start_ms", reservationStartMs)
            put("reservation_end_ms", reservationEndMs)
            put("reservation_start_time", reservationStartTime)
            put("reservation_end_time", reservationEndTime)
            put("affected_slots", JSONArray(affectedHourlySlots))
            put("validation_status", validationStatus)
            put("validation_message", validationMessage)
        }
    }

    data class ConflictEvaluation(
        val hasConflict: Boolean,
        val conflictType: String = "", // "TRAVEL", "GYM", "BATH", "WAKEUP", "MOVIE", ""
        val conflictingActivityId: String = "",
        val conflictReason: String = ""
    )

    /**
     * Calculates the reserved attendance interval envelope and affected hourly activity slots.
     * Formula:
     * lockStartMs = movieStartMs - 1 hour 30 minutes
     * lockEndMs = movieEndMs + 1 hour 30 minutes
     */
    fun calculateAttendanceLock(
        showDate: String,
        startTime: String,
        endTime: String,
        timeZone: TimeZone = TimeZone.getTimeZone("Asia/Kolkata"),
        wakeTime: String = "07:00"
    ): AttendanceLock {
        val dtFormat = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            this.timeZone = timeZone
            isLenient = false
        }
        val timeFormat = SimpleDateFormat("HH:mm", Locale.US).apply {
            this.timeZone = timeZone
        }

        val startCal = Calendar.getInstance(timeZone).apply {
            time = dtFormat.parse("$showDate $startTime") ?: Date()
        }
        val movieStartMs = startCal.timeInMillis

        val endCal = Calendar.getInstance(timeZone).apply {
            time = dtFormat.parse("$showDate $endTime") ?: Date()
        }
        // Handle midnight crossing shows: if endTime <= startTime, end occurs on the following calendar day
        if (endCal.timeInMillis <= movieStartMs) {
            endCal.add(Calendar.DAY_OF_YEAR, 1)
        }
        val movieEndMs = endCal.timeInMillis

        val durationMinutes = ((movieEndMs - movieStartMs) / 60000L).toInt()

        val lockStartMs = movieStartMs - BUFFER_BEFORE_START_MS
        val lockEndMs = movieEndMs + BUFFER_AFTER_END_MS

        val lockStartTime = timeFormat.format(Date(lockStartMs))
        val lockEndTime = timeFormat.format(Date(lockEndMs))

        // Derive affected hourly slots from existing hourly slot definitions
        val allSlots = runCatching { HourlyWindowRules.slots(showDate, wakeTime, timeZone.id) }.getOrDefault(emptyList())
        val affected = mutableListOf<String>()
        for (slot in allSlots) {
            if (isHourlySlotCoveredByMovie(slot.targetMs, lockStartMs, lockEndMs)) {
                affected.add(slot.hourSlot)
            }
        }

        return AttendanceLock(
            showDate = showDate,
            movieStartMs = movieStartMs,
            movieEndMs = movieEndMs,
            movieStartTime = startTime,
            movieEndTime = endTime,
            lockStartMs = lockStartMs,
            lockEndMs = lockEndMs,
            lockStartTime = lockStartTime,
            lockEndTime = lockEndTime,
            durationMinutes = durationMinutes,
            affectedHourlySlots = affected
        )
    }

    /**
     * Calculates the movie proof-submission window.
     * Proof window opens: validated show finish - 5 minutes (start-inclusive)
     * Proof deadline: validated show finish + 1 hour (end-exclusive)
     */
    fun calculateProofWindow(
        movieEndMs: Long
    ): Pair<Long, Long> {
        val openMs = movieEndMs - PROOF_WINDOW_OPEN_BEFORE_END_MS
        val deadlineMs = movieEndMs + PROOF_WINDOW_DEADLINE_AFTER_END_MS
        return Pair(openMs, deadlineMs)
    }

    /**
     * Determines whether a submission time falls within the movie proof window.
     * Opening is start-inclusive; deadline is end-exclusive.
     */
    fun isWithinProofWindow(nowMs: Long, openMs: Long, deadlineMs: Long): Boolean {
        return nowMs >= openMs && nowMs < deadlineMs
    }

    /**
     * Determines whether an hourly attendance slot is held/excused under a confirmed movie reservation.
     * Slot activity period: [slotTargetMs - 1 hour, slotTargetMs].
     * Excused iff the entire activity interval is within the attendance lock envelope:
     * (slotTargetMs - 3600_000L >= lockStartMs) && (slotTargetMs <= lockEndMs).
     */
    fun isHourlySlotCoveredByMovie(slotTargetMs: Long, lockStartMs: Long, lockEndMs: Long): Boolean {
        val slotStartMs = slotTargetMs - 3600_000L
        val slotEndMs = slotTargetMs
        return isHourlySlotCoveredByMovie(slotStartMs, slotEndMs, lockStartMs, lockEndMs)
    }

    fun isHourlySlotCoveredByMovie(
        slotStartMs: Long,
        slotEndMs: Long,
        lockStartMs: Long,
        lockEndMs: Long
    ): Boolean {
        return slotStartMs >= lockStartMs && slotEndMs <= lockEndMs
    }

    /**
     * Extracts and validates movie ticket metadata from visible OCR text without relying on QR decoding.
     */
    fun parseMovieTicketOcr(
        rawOcrText: String,
        targetDate: String? = null,
        timeZone: TimeZone = TimeZone.getTimeZone("Asia/Kolkata"),
        nowMs: Long = System.currentTimeMillis()
    ): MovieTicketInfo {
        val clean = rawOcrText.trim()
        val timeFormat = SimpleDateFormat("HH:mm", Locale.US).apply { this.timeZone = timeZone }
        val dtFormat = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            this.timeZone = timeZone
            isLenient = false
        }

        if (clean.isBlank()) {
            return MovieTicketInfo(
                movieTitle = "",
                cinemaName = "",
                showDate = targetDate.orEmpty(),
                startTime = "",
                endTime = null,
                durationMinutes = 0,
                durationEstablishedReliably = false,
                durationSource = "UNRESOLVED",
                screen = "",
                seat = "",
                bookingRef = "",
                qrCodeDetected = false,
                qrCodeIgnoredForValidation = true,
                rawOcrText = rawOcrText,
                validationStatus = STATE_NEEDS_REVIEW,
                validationMessage = "No text could be extracted from the ticket image."
            )
        }

        val lines = clean.lines().map { it.trim() }.filter { it.isNotBlank() }

        // 1. Language, Format, Certificate extraction (e.g. "Tamil | 2D | UA13+")
        var extractedLanguage = ""
        var extractedFormat = ""
        var extractedRating = ""
        val langFormatRegex = Regex("(?i)^([A-Za-z]+)\\s*\\|\\s*(2D|3D|IMAX\\s*2D|IMAX\\s*3D|4DX|ICE|ScreenX|70mm)\\s*(?:\\|\\s*([A-Za-z0-9+/]+))?")
        for (l in lines) {
            val match = langFormatRegex.find(l)
            if (match != null) {
                extractedLanguage = match.groupValues[1]
                extractedFormat = match.groupValues[2]
                extractedRating = match.groupValues.getOrElse(3) { "" }
                break
            }
        }

        // 2. Movie Title extraction
        var extractedTitle = ""
        val candidateTitleLines = lines.take(7).filter { l ->
            val lower = l.lowercase(Locale.US)
            !lower.contains("booking") &&
            !lower.contains("order") &&
            !lower.contains("ticket") &&
            !lower.contains("pvr") &&
            !lower.contains("inox") &&
            !lower.contains("cinepolis") &&
            !lower.contains("amc") &&
            !lower.contains("screen") &&
            !lower.contains("audi") &&
            !lower.contains("seat") &&
            !lower.contains("share") &&
            !lower.contains("meal") &&
            !lower.contains("theatre") &&
            !lower.contains("theater") &&
            !lower.contains("direction") &&
            !lower.contains("confirmed") &&
            !lower.contains("scan") &&
            !lower.contains("qr") &&
            !l.contains("|") &&
            !l.matches(Regex("^[0-9\\W]+$"))
        }

        // Match against known titles first if available
        for (known in KNOWN_MOVIE_RUNTIMES.keys) {
            val foundLine = lines.firstOrNull { it.contains(known, ignoreCase = true) }
            if (foundLine != null) {
                extractedTitle = known.split(" ").joinToString(" ") { word ->
                    word.replaceFirstChar { if (it.isLowerCase()) it.titlecase(Locale.US) else it.toString() }
                }
                break
            }
        }
        if (extractedTitle.isBlank() && candidateTitleLines.isNotEmpty()) {
            extractedTitle = candidateTitleLines.first()
        }

        // 3. Cinema / Theatre Name extraction
        var extractedCinema = ""
        val cinemaKeywords = listOf("PVR", "INOX", "Cinépolis", "Cinepolis", "SPI", "Carnival", "Miraj", "MovieMax", "Prasads", "AGS", "AMC", "Regal", "Cinemark", "Multiplex", "Cinemas", "Theatre", "Theater")
        for (l in lines) {
            for (kw in cinemaKeywords) {
                if (l.contains(kw, ignoreCase = true) && !l.contains("meal", ignoreCase = true)) {
                    extractedCinema = l
                    break
                }
            }
            if (extractedCinema.isNotBlank()) break
        }

        // 4. Show Date extraction
        var extractedDate = ""
        var rawTicketDate = ""
        var yearOmittedInTicket = false

        val isoDateRegex = Regex("\\b(\\d{4})[-/](\\d{2})[-/](\\d{2})\\b")
        val dmyDateRegex = Regex("\\b(\\d{1,2})[/.-](\\d{1,2})[/.-](\\d{4})\\b")
        val monthNames = "(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        val namedDateRegex1 = Regex("(?i)\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+($monthNames)(?:,?\\s+(\\d{4}))?\\b")
        val namedDateRegex2 = Regex("(?i)\\b($monthNames)\\s+(\\d{1,2})(?:st|nd|rd|th)?(?:,?\\s+(\\d{4}))?\\b")

        val currentCal = Calendar.getInstance(timeZone).apply { timeInMillis = nowMs }
        val currentYear = currentCal.get(Calendar.YEAR)

        val isoMatch = isoDateRegex.find(clean)
        if (isoMatch != null) {
            extractedDate = "${isoMatch.groupValues[1]}-${isoMatch.groupValues[2]}-${isoMatch.groupValues[3]}"
            rawTicketDate = isoMatch.value
        }
        if (extractedDate.isBlank()) {
            val dmyMatch = dmyDateRegex.find(clean)
            if (dmyMatch != null) {
                val d = dmyMatch.groupValues[1].padStart(2, '0')
                val m = dmyMatch.groupValues[2].padStart(2, '0')
                val y = dmyMatch.groupValues[3]
                extractedDate = "$y-$m-$d"
                rawTicketDate = dmyMatch.value
            }
        }
        if (extractedDate.isBlank()) {
            val namedMatch1 = namedDateRegex1.find(clean)
            if (namedMatch1 != null) {
                val day = namedMatch1.groupValues[1].padStart(2, '0')
                val monthStr = namedMatch1.groupValues[2].take(3).lowercase(Locale.US)
                val monthNum = parseMonthNumber(monthStr)
                val yearGroup = namedMatch1.groupValues[3]
                rawTicketDate = namedMatch1.value.trim()
                if (yearGroup.isNotBlank()) {
                    extractedDate = "$yearGroup-$monthNum-$day"
                } else {
                    yearOmittedInTicket = true
                    // Resolve year from targetDate if month and day match
                    if (!targetDate.isNullOrBlank()) {
                        val tParts = targetDate.split("-")
                        if (tParts.size == 3 && tParts[1] == monthNum && tParts[2] == day) {
                            extractedDate = "${tParts[0]}-$monthNum-$day"
                        } else if (tParts.size == 3) {
                            // Target date exists with different month/day; use target date's year for side-by-side comparison
                            extractedDate = "${tParts[0]}-$monthNum-$day"
                        }
                    } else {
                        extractedDate = "$currentYear-$monthNum-$day"
                    }
                }
            }
        }
        if (extractedDate.isBlank()) {
            val namedMatch2 = namedDateRegex2.find(clean)
            if (namedMatch2 != null) {
                val monthStr = namedMatch2.groupValues[1].take(3).lowercase(Locale.US)
                val day = namedMatch2.groupValues[2].padStart(2, '0')
                val monthNum = parseMonthNumber(monthStr)
                val yearGroup = namedMatch2.groupValues[3]
                rawTicketDate = namedMatch2.value.trim()
                if (yearGroup.isNotBlank()) {
                    extractedDate = "$yearGroup-$monthNum-$day"
                } else {
                    yearOmittedInTicket = true
                    if (!targetDate.isNullOrBlank()) {
                        val tParts = targetDate.split("-")
                        if (tParts.size == 3 && tParts[1] == monthNum && tParts[2] == day) {
                            extractedDate = "${tParts[0]}-$monthNum-$day"
                        } else if (tParts.size == 3) {
                            extractedDate = "${tParts[0]}-$monthNum-$day"
                        }
                    } else {
                        extractedDate = "$currentYear-$monthNum-$day"
                    }
                }
            }
        }

        // 5. Show Start & End Time / Approx / Duration extraction
        var extractedStartTime = ""
        var extractedEndTime: String? = null
        var isApproximate = clean.contains("(approx", ignoreCase = true) ||
            clean.contains("approx)", ignoreCase = true) ||
            clean.contains("approximate", ignoreCase = true)
        var extractedDurationMinutes = 0
        var durationEstablished = false
        var durationSource = "UNRESOLVED"

        // Interval pattern: e.g. "03:50 PM - 06:41 PM" or "5:00 PM to 8:00 PM"
        val rangeRegex = Regex("(?i)\\b(\\d{1,2}:\\d{2}\\s*(?:AM|PM|am|pm)?)\\s*(?:-|to|–|—)\\s*(\\d{1,2}:\\d{2}\\s*(?:AM|PM|am|pm)?)\\b")
        val rangeMatch = rangeRegex.find(clean)
        if (rangeMatch != null) {
            val t1 = normalizeTimeTo24H(rangeMatch.groupValues[1].trim())
            val t2 = normalizeTimeTo24H(rangeMatch.groupValues[2].trim())
            if (t1.isNotBlank() && t2.isNotBlank()) {
                extractedStartTime = t1
                extractedEndTime = t2
                durationSource = "TICKET_SHOW_TIMES"
                durationEstablished = true
            }
        }

        // Single start time fallback
        if (extractedStartTime.isBlank()) {
            val singleTimeRegex = Regex("(?i)(?:Time|Show|At|Starts|Timing)?\\s*:?\\s*\\b(\\d{1,2}:\\d{2}\\s*(?:AM|PM|am|pm)?)\\b")
            val singleMatch = singleTimeRegex.find(clean)
            if (singleMatch != null) {
                extractedStartTime = normalizeTimeTo24H(singleMatch.groupValues[1].trim())
            }
        }

        // Look for explicit runtime in ticket: e.g. "2h 45m", "165 mins"
        val runtimeRegex = Regex("(?i)\\b(\\d+)\\s*(?:h|hr|hrs|hours?)\\s*(?:(\\d+)\\s*(?:m|min|mins|minutes?)?)?\\b")
        val runtimeMatch = runtimeRegex.find(clean)
        if (runtimeMatch != null) {
            val hours = runtimeMatch.groupValues[1].toIntOrNull() ?: 0
            val mins = runtimeMatch.groupValues[2].toIntOrNull() ?: 0
            val totalMins = (hours * 60) + mins
            if (totalMins in 45..360) {
                extractedDurationMinutes = totalMins
                if (!durationEstablished) {
                    durationSource = "TICKET_EXPLICIT_RUNTIME"
                    durationEstablished = true
                }
            }
        } else {
            val minsOnlyRegex = Regex("(?i)\\b(\\d{2,3})\\s*(?:m|min|mins|minutes)\\b")
            val minsOnlyMatch = minsOnlyRegex.find(clean)
            if (minsOnlyMatch != null) {
                val totalMins = minsOnlyMatch.groupValues[1].toIntOrNull() ?: 0
                if (totalMins in 45..360) {
                    extractedDurationMinutes = totalMins
                    if (!durationEstablished) {
                        durationSource = "TICKET_EXPLICIT_RUNTIME"
                        durationEstablished = true
                    }
                }
            }
        }

        // Compute durationMinutes if start & end are present
        if (extractedStartTime.isNotBlank() && extractedEndTime != null && extractedDurationMinutes == 0) {
            val m1 = timeToMinutes(extractedStartTime)
            var m2 = timeToMinutes(extractedEndTime)
            if (m2 <= m1) m2 += 1440 // Midnight crossing
            extractedDurationMinutes = m2 - m1
        }

        // Calculate end time if start & explicit runtime are present
        if (extractedStartTime.isNotBlank() && extractedEndTime == null && extractedDurationMinutes > 0) {
            val m1 = timeToMinutes(extractedStartTime)
            val m2 = (m1 + extractedDurationMinutes) % 1440
            extractedEndTime = minutesToTime(m2)
        }

        // Fallback to known film dictionary
        if (!durationEstablished && extractedTitle.isNotBlank()) {
            val titleLower = extractedTitle.lowercase(Locale.US)
            val matchedRuntime = KNOWN_MOVIE_RUNTIMES.entries.firstOrNull {
                titleLower.contains(it.key) || it.key.contains(titleLower)
            }?.value
            if (matchedRuntime != null) {
                extractedDurationMinutes = matchedRuntime
                durationEstablished = true
                durationSource = "KNOWN_METADATA"
                if (extractedStartTime.isNotBlank() && extractedEndTime == null) {
                    val m1 = timeToMinutes(extractedStartTime)
                    val m2 = (m1 + extractedDurationMinutes) % 1440
                    extractedEndTime = minutesToTime(m2)
                }
            }
        }

        // 6. Ticket Quantity, Screen, Seat, Booking Codes
        var ticketQuantity: Int? = null
        val ticketQtyRegex = Regex("(?i)\\b(\\d+)\\s*(?:tickets?|tix|seats?)\\b")
        val qtyMatch = ticketQtyRegex.find(clean)
        if (qtyMatch != null) {
            ticketQuantity = qtyMatch.groupValues[1].toIntOrNull()
        }

        val screenRegex = Regex("(?i)(?:\\bSCREEN\\s*([A-Za-z0-9]+)|\\bAudi(?:torium)?\\s*[:#-]?\\s*([0-9]+)\\b)")
        val screenMatch = screenRegex.find(clean)
        val screen = screenMatch?.let { m ->
            val s1 = m.groupValues.getOrNull(1)?.ifBlank { null }
            val s2 = m.groupValues.getOrNull(2)?.ifBlank { null }
            s1?.let { "SCREEN $it" } ?: s2?.let { "AUDI $it" }
        } ?: ""

        val explicitSeatRegex = Regex("(?i)\\bSeats?\\s*[:#-]?\\s*([A-Za-z0-9, -]+)")
        var seat = explicitSeatRegex.find(clean)?.groupValues?.get(1)?.trim() ?: ""
        if (seat.isBlank()) {
            val seatPatternRegex = Regex("(?i)\\b([A-Z]{1,3}\\s*-\\s*[A-Z]?[0-9]{1,3})\\b")
            for (m in seatPatternRegex.findAll(clean)) {
                val cand = m.groupValues[1].trim()
                if (!cand.startsWith("AM", ignoreCase = true) && !cand.startsWith("PM", ignoreCase = true)) {
                    seat = cand
                    break
                }
            }
        }

        var bookingCode = ""
        val codeRegex = Regex("(?i)\\bBooking\\s*code\\s*[:#-]?\\s*([A-Za-z0-9]+)\\b")
        bookingCode = codeRegex.find(clean)?.groupValues?.get(1)?.trim() ?: ""

        var bookingRef = ""
        val refRegex = Regex("(?i)\\bBooking\\s*ID\\s*[:#-]?\\s*([A-Za-z0-9]+)\\b")
        bookingRef = refRegex.find(clean)?.groupValues?.get(1)?.trim() ?: ""
        if (bookingRef.isBlank()) {
            val genRefRegex = Regex("(?i)\\b(?:Order ID|Ticket No|Ref No|Confirmation)\\s*[:#-]?\\s*([A-Za-z0-9]+)\\b")
            bookingRef = genRefRegex.find(clean)?.groupValues?.get(1)?.trim() ?: ""
        }
        if (bookingRef.isBlank() && bookingCode.isNotBlank()) {
            bookingRef = bookingCode
        }

        // 7. Date Validation against intended date
        val intended = targetDate?.trim().orEmpty()
        var isDateMismatch = false
        var isExpired = false
        var ambiguousYear = false

        if (intended.isNotBlank() && extractedDate.isNotBlank()) {
            // Check day and month
            if (yearOmittedInTicket) {
                val tParts = intended.split("-")
                val eParts = extractedDate.split("-")
                if (tParts.size == 3 && eParts.size == 3) {
                    if (tParts[1] != eParts[1] || tParts[2] != eParts[2]) {
                        isDateMismatch = true
                    }
                }
            } else if (extractedDate != intended) {
                isDateMismatch = true
            }
        } else if (intended.isBlank() && yearOmittedInTicket) {
            ambiguousYear = true
        }

        // 8. Expired Check
        var movieEndMs = 0L
        if (extractedDate.isNotBlank() && extractedEndTime != null) {
            val startCal = Calendar.getInstance(timeZone).apply {
                time = runCatching { dtFormat.parse("$extractedDate $extractedStartTime") }.getOrNull() ?: Date(0)
            }
            val endCal = Calendar.getInstance(timeZone).apply {
                time = runCatching { dtFormat.parse("$extractedDate $extractedEndTime") }.getOrNull() ?: Date(0)
            }
            if (endCal.timeInMillis <= startCal.timeInMillis) {
                endCal.add(Calendar.DAY_OF_YEAR, 1)
            }
            movieEndMs = endCal.timeInMillis
            if (movieEndMs > 0L && movieEndMs <= nowMs) {
                isExpired = true
            }
        }

        // 9. Schedule calculations (Attendance Lock & Proof Window)
        var lock: AttendanceLock? = null
        var proofOpenMs = 0L
        var proofDeadMs = 0L
        var proofOpenStr = ""
        var proofDeadStr = ""
        if (extractedDate.isNotBlank() && extractedStartTime.isNotBlank() && extractedEndTime != null) {
            lock = calculateAttendanceLock(extractedDate, extractedStartTime, extractedEndTime, timeZone)
            val pWin = calculateProofWindow(lock.movieEndMs)
            proofOpenMs = pWin.first
            proofDeadMs = pWin.second
            proofOpenStr = timeFormat.format(Date(proofOpenMs))
            proofDeadStr = timeFormat.format(Date(proofDeadMs))
        }

        // 10. Final validation decision
        val isValid = extractedTitle.isNotBlank() &&
            extractedDate.isNotBlank() &&
            extractedStartTime.isNotBlank() &&
            extractedEndTime != null &&
            durationEstablished &&
            extractedDurationMinutes in 45..360 &&
            !isDateMismatch &&
            !isExpired &&
            !ambiguousYear

        val (valStatus, valMsg) = when {
            isExpired -> Pair(STATE_BOOKING_REJECTED, "Movie show has already finished ($extractedDate $extractedEndTime). Cannot pre-book an expired show.")
            isDateMismatch -> Pair(STATE_NEEDS_REVIEW, "Show date mismatch: Ticket indicates ${if (rawTicketDate.isNotBlank()) rawTicketDate else extractedDate}, but intended booking date is $intended. Contradictory ticket evidence cannot be overridden.")
            ambiguousYear -> Pair(STATE_NEEDS_REVIEW, "Ticket omits year ($rawTicketDate). Please specify the intended booking date to resolve the year.")
            extractedStartTime.isBlank() -> Pair(STATE_NEEDS_REVIEW, "Show start time could not be reliably extracted from ticket. Needs manual review.")
            !durationEstablished -> Pair(STATE_NEEDS_REVIEW, "Movie end time or duration could not be reliably determined. Needs review before attendance hold can be confirmed.")
            extractedDate.isBlank() -> Pair(STATE_NEEDS_REVIEW, "Show date could not be identified from ticket. Needs review.")
            isValid -> Pair(STATE_MOVIE_BOOKING_CONFIRMED, "Movie ticket successfully validated: $extractedTitle at $extractedStartTime–$extractedEndTime (duration: ${extractedDurationMinutes}m). Attendance lock scheduled.")
            else -> Pair(STATE_NEEDS_REVIEW, "Ticket details are ambiguous. Needs review.")
        }

        return MovieTicketInfo(
            movieTitle = extractedTitle.ifBlank { "Movie" },
            cinemaName = extractedCinema,
            showDate = extractedDate,
            startTime = extractedStartTime,
            endTime = extractedEndTime,
            durationMinutes = extractedDurationMinutes,
            durationEstablishedReliably = durationEstablished,
            durationSource = durationSource,
            screen = screen,
            seat = seat,
            bookingRef = bookingRef,
            qrCodeDetected = false,
            qrCodeIgnoredForValidation = true,
            rawOcrText = rawOcrText,
            validationStatus = valStatus,
            validationMessage = valMsg,
            isApproximateEndTime = isApproximate,
            ticketQuantity = ticketQuantity,
            language = extractedLanguage,
            format = extractedFormat,
            rating = extractedRating,
            bookingCode = bookingCode,
            ticketDateRaw = rawTicketDate,
            yearOmittedInTicket = yearOmittedInTicket,
            intendedDate = intended,
            isDateMismatch = isDateMismatch,
            isExpired = isExpired,
            proofWindowOpenMs = proofOpenMs,
            proofDeadlineMs = proofDeadMs,
            proofWindowOpenTime = proofOpenStr,
            proofDeadlineTime = proofDeadStr,
            reservationStartMs = lock?.lockStartMs ?: 0L,
            reservationEndMs = lock?.lockEndMs ?: 0L,
            reservationStartTime = lock?.lockStartTime ?: "",
            reservationEndTime = lock?.lockEndTime ?: "",
            affectedHourlySlots = lock?.affectedHourlySlots ?: emptyList()
        )
    }

    /**
     * Resolves the authoritative display state based on deterministic current time and validated lock envelope.
     */
    fun resolveAuthoritativeState(
        nowMs: Long,
        lockStartMs: Long,
        lockEndMs: Long,
        currentStatus: String = "",
        currentDbStatus: String = currentStatus
    ): String {
        val status = currentDbStatus.ifBlank { currentStatus }
        if (status in listOf(STATE_BOOKING_REJECTED, "REJECTED")) return STATE_BOOKING_REJECTED
        if (status in listOf(STATE_BOOKING_CANCELLED, "CANCELLED")) return STATE_BOOKING_CANCELLED
        if (status in listOf(STATE_NEEDS_REVIEW, "NEEDS_REVIEW")) return STATE_NEEDS_REVIEW
        if (status in listOf(STATE_TICKET_SUBMISSION_PENDING, STATE_TICKET_VERIFICATION_PENDING, "SUBMITTED", "PENDING")) {
            return STATE_TICKET_VERIFICATION_PENDING
        }

        return when {
            nowMs >= lockEndMs -> STATE_RESERVATION_COMPLETED
            nowMs in lockStartMs..lockEndMs -> STATE_ATTENDANCE_HOLD_ACTIVE
            nowMs < lockStartMs -> STATE_MOVIE_BOOKING_CONFIRMED
            else -> STATE_RESERVATION_COMPLETED
        }
    }

    /**
     * Checks if a proposed movie reservation conflicts with existing schedule items.
     */
    fun checkScheduleConflicts(
        lockStartMs: Long,
        lockEndMs: Long,
        existingTravelIntervals: List<Pair<Long, Long>> = emptyList(), // [depMs, endMs]
        existingMovieIntervals: List<Pair<Long, Long>> = emptyList(),  // [lockStartMs, lockEndMs]
        existingGymIntervals: List<Pair<Long, Long>> = emptyList(),
        gymInterval: Pair<Long, Long>? = null,
        bathIntervals: List<Pair<Long, Long>> = emptyList(),
        wakeUpInterval: Pair<Long, Long>? = null
    ): ConflictEvaluation {
        // Check other confirmed movie bookings
        for (m in existingMovieIntervals) {
            if (intervalsOverlap(lockStartMs, lockEndMs, m.first, m.second)) {
                return ConflictEvaluation(
                    hasConflict = true,
                    conflictType = "MOVIE",
                    conflictReason = "Conflicts with an existing confirmed movie reservation covering this time interval."
                )
            }
        }

        // Check travel
        for (t in existingTravelIntervals) {
            if (intervalsOverlap(lockStartMs, lockEndMs, t.first, t.second)) {
                return ConflictEvaluation(
                    hasConflict = true,
                    conflictType = "TRAVEL",
                    conflictReason = "Conflicts with pre-booked travel interval."
                )
            }
        }

        // Check Wake-up
        if (wakeUpInterval != null && intervalsOverlap(lockStartMs, lockEndMs, wakeUpInterval.first, wakeUpInterval.second)) {
            return ConflictEvaluation(
                hasConflict = true,
                conflictType = "WAKEUP",
                conflictReason = "Conflicts with mandatory Wake-Up verification period."
            )
        }

        // Check Gym
        val allGym = if (gymInterval != null) existingGymIntervals + gymInterval else existingGymIntervals
        for (g in allGym) {
            if (intervalsOverlap(lockStartMs, lockEndMs, g.first, g.second)) {
                return ConflictEvaluation(
                    hasConflict = true,
                    conflictType = "GYM",
                    conflictReason = "Conflicts with scheduled Gym workout session."
                )
            }
        }

        // Check Bath
        for (b in bathIntervals) {
            if (intervalsOverlap(lockStartMs, lockEndMs, b.first, b.second)) {
                return ConflictEvaluation(
                    hasConflict = true,
                    conflictType = "BATH",
                    conflictReason = "Conflicts with planned Bath protocol."
                )
            }
        }

        return ConflictEvaluation(hasConflict = false)
    }

    private fun intervalsOverlap(s1: Long, e1: Long, s2: Long, e2: Long): Boolean {
        return max(s1, s2) < min(e1, e2)
    }

    private fun parseMonthNumber(m: String): String = when (m.lowercase(Locale.US)) {
        "jan" -> "01"
        "feb" -> "02"
        "mar" -> "03"
        "apr" -> "04"
        "may" -> "05"
        "jun" -> "06"
        "jul" -> "07"
        "aug" -> "08"
        "sep" -> "09"
        "oct" -> "10"
        "nov" -> "11"
        "dec" -> "12"
        else -> ""
    }

    private fun normalizeTimeTo24H(timeStr: String): String {
        val clean = timeStr.trim().uppercase(Locale.US)
        val isPm = clean.contains("PM")
        val isAm = clean.contains("AM")
        val digits = clean.replace(Regex("[^0-9:]"), "").trim()
        val parts = digits.split(":")
        if (parts.size != 2) return ""
        var h = parts[0].toIntOrNull() ?: return ""
        val m = parts[1].toIntOrNull() ?: return ""
        if (isPm && h < 12) h += 12
        if (isAm && h == 12) h = 0
        return String.format(Locale.US, "%02d:%02d", h.coerceIn(0, 23), m.coerceIn(0, 59))
    }

    private fun timeToMinutes(hhmm: String): Int {
        val parts = hhmm.split(":")
        if (parts.size != 2) return 0
        val h = parts[0].toIntOrNull() ?: 0
        val m = parts[1].toIntOrNull() ?: 0
        return (h * 60) + m
    }

    private fun minutesToTime(mins: Int): String {
        val normalized = (mins % 1440 + 1440) % 1440
        val h = normalized / 60
        val m = normalized % 60
        return String.format(Locale.US, "%02d:%02d", h, m)
    }
}
