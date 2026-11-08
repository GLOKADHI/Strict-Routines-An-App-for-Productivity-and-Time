package com.strictrecords.app

import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * GYM Mode — Temporary Music Access Rules (Pure Logic Engine)
 *
 * Rules:
 * 1. Activation Window: 17:00 <= currentTime < 20:30 (strictly before 8:30 PM).
 *    Rejected before 5:00 PM or at/after 8:30 PM.
 * 2. Session Linking: Each activation links to a specific Gym session for today.
 *    A single Gym session cannot be reused for multiple activations.
 * 3. Access Duration: Access expires at today's 20:30 cutoff, or an earlier manual switch-off.
 *    New sessions end at 8:30 PM, including an activation at 5:00 PM.
 * 4. Application Scope: Unlocks ONLY Samsung Music and Spotify.
 *    YouTube, Instagram, etc. remain strictly blocked.
 * 5. Three Lives System: Starts at 3/3 lives (♥ ♥ ♥).
 *    Reaching 0 lives disables activation until next weekly reset.
 * 6. Weekly Reset: Every Monday 12:00 AM (00:00) local timezone, reset to 3/3 lives.
 *    Capped at 3. Monotonic / rollback protected.
 * 7. Single Deduction Guarantee: Each failed Gym session deducts AT MOST one life.
 *    Inconclusive / pending reviews or service errors NEVER deduct lives.
 * 8. Proof Obligation: Separate from music expiry. Early exit revokes music but keeps
 *    proof obligation active.
 */
object GymModeRules {

    const val MAX_LIVES = ProductPolicy.GYM_MAX_LIVES
    const val ACTIVATION_START_HOUR = ProductPolicy.GYM_START_HOUR
    const val CUTOFF_HOUR = ProductPolicy.GYM_CUTOFF_HOUR
    const val CUTOFF_MINUTE = ProductPolicy.GYM_CUTOFF_MINUTE
    const val MAX_MUSIC_DURATION_MS = ProductPolicy.GYM_MUSIC_DURATION_MS
    const val DEFAULT_TIMEZONE = "Asia/Kolkata"

    val ALLOWED_MUSIC_PACKAGES = setOf(
        "com.sec.android.app.music",
        "com.spotify.music",
        "com.spotify.lite"
    )

    val ALLOWED_MUSIC_TARGET_KEYS = setOf(
        "samsung_music",
        "spotify"
    )

    data class GymModeSession(
        val sessionId: String,
        val gymActivityId: String,
        val activationTimeMs: Long,
        val accessExpiryMs: Long,
        val proofDeadlineMs: Long,
        val status: String, // ACTIVE, EXPIRED, FULFILLED, FAILED, CANCELLED
        val toggledOffEarly: Boolean = false,
        val toggledOffAtMs: Long? = null
    ) {
        val isMusicAccessActive: Boolean
            get() = status == "ACTIVE" && !toggledOffEarly

        fun toJson(): JSONObject = JSONObject().apply {
            put("session_id", sessionId)
            put("gym_activity_id", gymActivityId)
            put("activation_time_ms", activationTimeMs)
            put("access_expiry_ms", accessExpiryMs)
            put("proof_deadline_ms", proofDeadlineMs)
            put("status", status)
            put("toggled_off_early", toggledOffEarly)
            if (toggledOffAtMs != null) put("toggled_off_at_ms", toggledOffAtMs)
        }

        companion object {
            fun fromJson(json: JSONObject): GymModeSession {
                return GymModeSession(
                    sessionId = json.getString("session_id"),
                    gymActivityId = json.getString("gym_activity_id"),
                    activationTimeMs = json.getLong("activation_time_ms"),
                    accessExpiryMs = json.getLong("access_expiry_ms"),
                    proofDeadlineMs = json.optLong("proof_deadline_ms", json.getLong("access_expiry_ms")),
                    status = json.optString("status", "ACTIVE"),
                    toggledOffEarly = json.optBoolean("toggled_off_early", false),
                    toggledOffAtMs = if (json.has("toggled_off_at_ms")) json.getLong("toggled_off_at_ms") else null
                )
            }
        }
    }

    data class GymTiming(
        val scheduledStartMs: Long,
        val activationAvailableMs: Long,
        val scheduledExpiryMs: Long,
        val scheduledTimeFormatted: String,
        val activationAvailableFormatted: String,
        val scheduledExpiryFormatted: String
    )

    fun calculateTiming(scheduledHhmm: String, dateStr: String, timezone: String = DEFAULT_TIMEZONE): GymTiming {
        val cleanTime = scheduledHhmm.trim().ifBlank { "18:00" }
        val timeParts = cleanTime.split(":")
        val hour = timeParts.getOrNull(0)?.toIntOrNull() ?: 18
        val minute = timeParts.getOrNull(1)?.toIntOrNull() ?: 0

        val cal = Calendar.getInstance(TimeZone.getTimeZone(timezone))
        val dateParts = dateStr.split("-")
        if (dateParts.size == 3) {
            cal.set(Calendar.YEAR, dateParts[0].toInt())
            cal.set(Calendar.MONTH, dateParts[1].toInt() - 1)
            cal.set(Calendar.DAY_OF_MONTH, dateParts[2].toInt())
        }
        cal.set(Calendar.HOUR_OF_DAY, hour)
        cal.set(Calendar.MINUTE, minute)
        cal.set(Calendar.SECOND, 0)
        cal.set(Calendar.MILLISECOND, 0)

        val startMs = cal.timeInMillis
        val availMs = startMs - (5 * 60 * 1000L) // Exactly 5 minutes before scheduled start
        val expiryMs = startMs + (2 * 60 * 60 * 1000L) // Exactly 2 hours after scheduled start

        val sdf = SimpleDateFormat("h:mm a", Locale.US).apply { timeZone = TimeZone.getTimeZone(timezone) }
        return GymTiming(
            scheduledStartMs = startMs,
            activationAvailableMs = availMs,
            scheduledExpiryMs = expiryMs,
            scheduledTimeFormatted = sdf.format(Date(startMs)),
            activationAvailableFormatted = sdf.format(Date(availMs)),
            scheduledExpiryFormatted = sdf.format(Date(expiryMs))
        )
    }

    data class ActivationResult(
        val success: Boolean,
        val message: String,
        val session: GymModeSession? = null,
        val errorCode: String? = null
    )

    /**
     * Checks if GYM Mode can be activated at the given local time.
     * Valid interval: 17:00 <= currentTime < 20:30 (start-inclusive, end-exclusive).
     */
    fun isWithinActivationWindow(
        nowMs: Long,
        scheduledHhmm: String? = null,
        dateStr: String? = null,
        timezone: String = DEFAULT_TIMEZONE
    ): Boolean {
        if (!scheduledHhmm.isNullOrBlank() && !dateStr.isNullOrBlank()) {
            val timing = calculateTiming(scheduledHhmm, dateStr, timezone)
            return nowMs >= timing.activationAvailableMs && nowMs < timing.scheduledExpiryMs
        }
        val cal = Calendar.getInstance(TimeZone.getTimeZone(timezone)).apply {
            timeInMillis = nowMs
        }
        val hour = cal.get(Calendar.HOUR_OF_DAY)
        val minute = cal.get(Calendar.MINUTE)
        if (hour < ACTIVATION_START_HOUR) return false
        if (hour > CUTOFF_HOUR) return false
        if (hour == CUTOFF_HOUR && minute >= CUTOFF_MINUTE) return false
        return true
    }

    /**
     * Calculates the 8:30 PM cutoff epoch milliseconds for the current day in timezone.
     */
    fun getCutoffTimeMs(nowMs: Long, timezone: String = DEFAULT_TIMEZONE): Long {
        val cal = Calendar.getInstance(TimeZone.getTimeZone(timezone)).apply {
            timeInMillis = nowMs
            set(Calendar.HOUR_OF_DAY, CUTOFF_HOUR)
            set(Calendar.MINUTE, CUTOFF_MINUTE)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    /**
     * Calculates access expiry at today's 8:30 PM cutoff.
     */
    fun calculateAccessExpiry(
        activationTimeMs: Long,
        scheduledHhmm: String? = null,
        dateStr: String? = null,
        timezone: String = DEFAULT_TIMEZONE
    ): Long {
        if (!scheduledHhmm.isNullOrBlank() && !dateStr.isNullOrBlank()) {
            val timing = calculateTiming(scheduledHhmm, dateStr, timezone)
            return timing.scheduledExpiryMs
        }
        val standardExpiry = activationTimeMs + MAX_MUSIC_DURATION_MS
        val cutoff = getCutoffTimeMs(activationTimeMs, timezone)
        return minOf(standardExpiry, cutoff)
    }

    /**
     * Validates whether activation is permitted and generates the session if valid.
     */
    fun canActivate(
        nowMs: Long,
        livesBalance: Int,
        hasActiveSession: Boolean,
        gymActivityId: String,
        previouslyActivatedGymIds: Set<String>,
        scheduledHhmm: String? = null,
        dateStr: String? = null,
        timezone: String = DEFAULT_TIMEZONE,
        hasStrictSession: Boolean = false
    ): Pair<Boolean, String> {
        if (hasStrictSession) {
            return Pair(false, "Strict Session is active. Gym Mode cannot bypass Strict Session restrictions.")
        }
        if (livesBalance <= 0) {
            val nextResetMs = calculateNextWeeklyResetMs(nowMs, timezone)
            val sdf = SimpleDateFormat("EEEE, MMM d 'at' h:mm a", Locale.US).apply {
                timeZone = TimeZone.getTimeZone(timezone)
            }
            return Pair(false, "No lives remaining. GYM Mode is disabled until reset on ${sdf.format(Date(nextResetMs))}.")
        }

        if (hasActiveSession) {
            return Pair(false, "A GYM Mode session is already active.")
        }

        if (gymActivityId.isBlank()) {
            return Pair(false, "A valid Gym session must be selected before activating GYM Mode.")
        }

        if (previouslyActivatedGymIds.contains(gymActivityId)) {
            return Pair(false, "This Gym session has already been used for a GYM Mode activation today.")
        }

        if (!scheduledHhmm.isNullOrBlank() && !dateStr.isNullOrBlank()) {
            val timing = calculateTiming(scheduledHhmm, dateStr, timezone)
            if (nowMs < timing.activationAvailableMs) {
                return Pair(false, "Activation available at ${timing.activationAvailableFormatted} (5 minutes before scheduled gym time).")
            }
            if (nowMs >= timing.scheduledExpiryMs) {
                return Pair(false, "The scheduled gym session expired at ${timing.scheduledExpiryFormatted}.")
            }
        } else {
            val cal = Calendar.getInstance(TimeZone.getTimeZone(timezone)).apply {
                timeInMillis = nowMs
            }
            val hour = cal.get(Calendar.HOUR_OF_DAY)
            val minute = cal.get(Calendar.MINUTE)

            if (hour < ACTIVATION_START_HOUR) {
                return Pair(false, "GYM Mode activation is only permitted after 5:00 PM (until 8:30 PM).")
            }
            if (hour > CUTOFF_HOUR || (hour == CUTOFF_HOUR && minute >= CUTOFF_MINUTE)) {
                return Pair(false, "The GYM Mode activation window has closed for today at 8:30 PM.")
            }
        }

        return Pair(true, "OK")
    }

    /**
     * Checks if music access is currently active for an active session at nowMs.
     */
    fun isMusicAccessActive(nowMs: Long, session: GymModeSession?): Boolean {
        if (session == null) return false
        if (session.status != "ACTIVE") return false
        if (session.toggledOffEarly) return false
        return nowMs < session.accessExpiryMs
    }

    /**
     * Checks if a target app is allowed under GYM Mode.
     * ONLY Samsung Music and Spotify are allowed. YouTube, Instagram, etc. are NEVER allowed.
     */
    fun isTargetAllowedUnderGymMode(packageNameOrApp: String, isGymModeMusicActive: Boolean): Boolean {
        if (!isGymModeMusicActive) return false
        val clean = packageNameOrApp.trim().lowercase(Locale.ROOT)
        if (clean in ALLOWED_MUSIC_PACKAGES) return true
        if (clean in ALLOWED_MUSIC_TARGET_KEYS) return true
        if (clean == "samsung music" || clean == "spotify") return true
        return false
    }

    /**
     * Formats the 3-life badge string:
     * 3 -> "♥ ♥ ♥ — 3/3 lives"
     * 2 -> "♥ ♥ ♡ — 2/3 lives"
     * 1 -> "♥ ♡ ♡ — 1/3 lives"
     * 0 -> "♡ ♡ ♡ — 0/3 lives"
     */
    fun formatLivesBadge(lives: Int): String {
        val clamped = lives.coerceIn(0, MAX_LIVES)
        val hearts = when (clamped) {
            3 -> "♥ ♥ ♥"
            2 -> "♥ ♥ ♡"
            1 -> "♥ ♡ ♡"
            else -> "♡ ♡ ♡"
        }
        return "$hearts — $clamped/$MAX_LIVES lives"
    }

    /**
     * Computes the ISO-8601 week key (e.g. "2026-W42") in the configured timezone.
     */
    fun getIsoWeekKey(timeMs: Long, timezone: String = DEFAULT_TIMEZONE): String {
        val cal = Calendar.getInstance(TimeZone.getTimeZone(timezone), Locale.US).apply {
            firstDayOfWeek = Calendar.MONDAY
            minimalDaysInFirstWeek = 4
            timeInMillis = timeMs
        }
        val week = cal.get(Calendar.WEEK_OF_YEAR)
        var year = cal.get(Calendar.YEAR)
        // Handle week 1 rollover in late December or week 52/53 in early January
        val month = cal.get(Calendar.MONTH)
        if (month == Calendar.DECEMBER && week == 1) year++
        else if (month == Calendar.JANUARY && week >= 52) year--
        return String.format(Locale.US, "%04d-W%02d", year, week)
    }

    /**
     * Calculates the timestamp of the next Monday 12:00 AM (00:00:00.000) local time.
     */
    fun calculateNextWeeklyResetMs(fromMs: Long, timezone: String = DEFAULT_TIMEZONE): Long {
        val cal = Calendar.getInstance(TimeZone.getTimeZone(timezone), Locale.US).apply {
            firstDayOfWeek = Calendar.MONDAY
            timeInMillis = fromMs
            set(Calendar.HOUR_OF_DAY, 0)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }

        // Advance to next Monday
        var daysToAdd = (Calendar.MONDAY - cal.get(Calendar.DAY_OF_WEEK) + 7) % 7
        if (daysToAdd == 0 && fromMs >= cal.timeInMillis) {
            daysToAdd = 7
        }
        cal.add(Calendar.DAY_OF_YEAR, daysToAdd)
        return cal.timeInMillis
    }

    /**
     * Checks if a weekly reset should be performed.
     * True if current time is in a different ISO week and current time >= Monday 00:00.
     * Protects against backward clock manipulation (nowMs < lastResetTimestampMs).
     */
    fun shouldPerformWeeklyReset(
        lastResetWeekKey: String,
        lastResetTimestampMs: Long,
        nowMs: Long,
        timezone: String = DEFAULT_TIMEZONE
    ): Boolean {
        // Clock rollback protection: if current time is before last recorded reset time, refuse reset
        if (nowMs < lastResetTimestampMs) {
            return false
        }
        val currentWeekKey = getIsoWeekKey(nowMs, timezone)
        return currentWeekKey != lastResetWeekKey
    }

    /**
     * Evaluates life deduction:
     * Exactly one life deducted if:
     * 1. Proof deadline expired without submission.
     * 2. Proof confirmed fake or manipulated.
     * Guaranteed: AT MOST ONE deduction per gym session.
     */
    fun shouldDeductLife(
        gymActivityId: String,
        isFakeOrManipulated: Boolean,
        isProofDeadlineExpiredWithoutSubmission: Boolean,
        alreadyDeductedGymIds: Set<String>,
        alreadyFulfilledGymIds: Set<String>
    ): Pair<Boolean, String> {
        if (alreadyDeductedGymIds.contains(gymActivityId)) {
            return Pair(false, "Life already deducted for this Gym session (single deduction guarantee).")
        }
        if (alreadyFulfilledGymIds.contains(gymActivityId)) {
            return Pair(false, "Gym proof already fulfilled for this session. No deduction.")
        }
        if (isFakeOrManipulated) {
            return Pair(true, "Gym proof confirmed fake, fraudulent, or manipulated.")
        }
        if (isProofDeadlineExpiredWithoutSubmission) {
            return Pair(true, "Gym proof submission deadline expired without acceptable evidence.")
        }
        return Pair(false, "No deduction condition met.")
    }
}
