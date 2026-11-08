package com.strictrecords.app

import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

/**
 * Three-Heart Accountability Engine — Centralized, Reusable Infrastructure for
 * both Travel Mode and Gym Mode.
 *
 * Requirements:
 * 1. Independent 3-heart allowance for Travel Mode (3/3).
 * 2. Independent 3-heart allowance for Gym Mode (3/3).
 * 3. Never mix Travel and Gym balances.
 * 4. Deduct exactly one heart when:
 *    a) Evidence deadline passes without valid submission.
 *    b) Evidence is confirmed fake, fraudulent, or manipulated.
 * 5. Single deduction guarantee: AT MOST ONE deduction per activity occurrence.
 * 6. Technical failures, crashes, or pending reviews NEVER deduct hearts.
 * 7. When 0 hearts remain, the domain is locked from further activations.
 * 8. Administrator recovery/reset mechanism preserves audit history.
 */
object ThreeHeartAccountabilityEngine {

    const val INITIAL_HEARTS = 3
    const val MAX_HEARTS = 3

    object Domain {
        const val TRAVEL = "TRAVEL"
        const val GYM = "GYM"
    }

    object Action {
        const val INITIAL_ALLOCATION = "INITIAL_ALLOCATION"
        const val DEDUCTION = "DEDUCTION"
        const val VERIFICATION_PRESERVED = "VERIFICATION_PRESERVED"
        const val ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"
        const val ADMIN_RESET = "ADMIN_RESET"
    }

    data class HeartLedgerEntry(
        val ledgerId: String,
        val domain: String,
        val activityId: String,
        val evidenceId: String? = null,
        val action: String,
        val previousHearts: Int,
        val newHearts: Int,
        val heartsChanged: Int,
        val reason: String,
        val decision: String,
        val timestampMs: Long = System.currentTimeMillis()
    ) {
        fun toJson(): JSONObject = JSONObject().apply {
            put("ledger_id", ledgerId)
            put("domain", domain)
            put("activity_id", activityId)
            if (evidenceId != null) put("evidence_id", evidenceId)
            put("action", action)
            put("previous_hearts", previousHearts)
            put("new_hearts", newHearts)
            put("hearts_changed", heartsChanged)
            put("reason", reason)
            put("decision", decision)
            put("timestamp_ms", timestampMs)
        }

        companion object {
            fun fromJson(json: JSONObject): HeartLedgerEntry = HeartLedgerEntry(
                ledgerId = json.getString("ledger_id"),
                domain = json.getString("domain"),
                activityId = json.getString("activity_id"),
                evidenceId = json.optString("evidence_id").takeIf { it.isNotBlank() },
                action = json.getString("action"),
                previousHearts = json.getInt("previous_hearts"),
                newHearts = json.getInt("new_hearts"),
                heartsChanged = json.getInt("hearts_changed"),
                reason = json.getString("reason"),
                decision = json.getString("decision"),
                timestampMs = json.getLong("timestamp_ms")
            )
        }
    }

    /**
     * Formats the visual 3-heart badge:
     * 3 -> "♥ ♥ ♥ — 3/3 hearts"
     * 2 -> "♥ ♥ ♡ — 2/3 hearts"
     * 1 -> "♥ ♡ ♡ — 1/3 hearts"
     * 0 -> "♡ ♡ ♡ — 0/3 hearts"
     */
    fun formatHeartsBadge(hearts: Int, domainTitle: String = ""): String {
        val clamped = hearts.coerceIn(0, MAX_HEARTS)
        val glyphs = when (clamped) {
            3 -> "♥ ♥ ♥"
            2 -> "♥ ♥ ♡"
            1 -> "♥ ♡ ♡"
            else -> "♡ ♡ ♡"
        }
        val prefix = if (domainTitle.isNotBlank()) "$domainTitle: " else ""
        return "$prefix$glyphs ($clamped/$MAX_HEARTS hearts remaining)"
    }

    data class DeductionEvaluation(
        val shouldDeduct: Boolean,
        val newHearts: Int,
        val reason: String,
        val decision: String,
        val errorCode: String? = null
    )

    /**
     * Evaluates heart deduction with single-deduction guarantee.
     */
    fun evaluateDeduction(
        domain: String,
        activityId: String,
        currentHearts: Int,
        isDeadlineExpiredWithoutSubmission: Boolean,
        isFakeOrManipulated: Boolean,
        alreadyDeductedActivityIds: Set<String>,
        alreadyFulfilledActivityIds: Set<String>
    ): DeductionEvaluation {
        if (alreadyDeductedActivityIds.contains(activityId)) {
            return DeductionEvaluation(
                shouldDeduct = false,
                newHearts = currentHearts,
                reason = "A heart has already been deducted for this $domain activity occurrence (single deduction guarantee).",
                decision = "IGNORED_DUPLICATE_DEDUCTION",
                errorCode = "ALREADY_DEDUCTED"
            )
        }

        if (alreadyFulfilledActivityIds.contains(activityId)) {
            return DeductionEvaluation(
                shouldDeduct = false,
                newHearts = currentHearts,
                reason = "Evidence for this $domain activity was already verified and fulfilled. No heart deduction.",
                decision = "IGNORED_ALREADY_FULFILLED"
            )
        }

        if (currentHearts <= 0) {
            return DeductionEvaluation(
                shouldDeduct = false,
                newHearts = 0,
                reason = "No hearts remaining in $domain Mode (already exhausted at 0/3).",
                decision = "ALREADY_EXHAUSTED",
                errorCode = "HEARTS_EXHAUSTED"
            )
        }

        if (isFakeOrManipulated) {
            val newBalance = (currentHearts - 1).coerceAtLeast(0)
            return DeductionEvaluation(
                shouldDeduct = true,
                newHearts = newBalance,
                reason = "Evidence confirmed to be fabricated, manipulated, or deliberately reused to deceive the system.",
                decision = "DEDUCT_FRAUDULENT_EVIDENCE"
            )
        }

        if (isDeadlineExpiredWithoutSubmission) {
            val newBalance = (currentHearts - 1).coerceAtLeast(0)
            return DeductionEvaluation(
                shouldDeduct = true,
                newHearts = newBalance,
                reason = "The evidence submission deadline passed without any valid evidence submission.",
                decision = "DEDUCT_DEADLINE_EXPIRED"
            )
        }

        return DeductionEvaluation(
            shouldDeduct = false,
            newHearts = currentHearts,
            reason = "No heart deduction condition met.",
            decision = "NO_DEDUCTION"
        )
    }

    /**
     * Generates a new unique ledger ID.
     */
    fun generateLedgerId(domain: String, activityId: String): String {
        val cleanAct = activityId.replace(Regex("[^A-Za-z0-9]"), "").takeLast(12)
        val rand = UUID.randomUUID().toString().take(6).uppercase(Locale.US)
        return "HLG-$domain-$cleanAct-$rand"
    }
}
