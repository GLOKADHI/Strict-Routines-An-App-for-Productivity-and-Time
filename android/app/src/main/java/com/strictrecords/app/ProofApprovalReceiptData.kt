package com.strictrecords.app

import android.database.Cursor
import org.json.JSONObject

/** A read-only projection. Opening a receipt never invokes approval or scoring writers. */
internal data class ProofApprovalReceiptData(
    val event: JSONObject,
    val evidence: JSONObject?,
    val activity: JSONObject?,
    val authoritative: Boolean,
    val credited: Boolean,
    val ledgerMessage: String,
    val timezone: String
) {
    val audit: JSONObject get() = event.optJSONObject("audit") ?: JSONObject()
    val ai: JSONObject? get() = audit.optJSONObject("taufa_ai")
    fun value(vararg keys: String): String =
        keys.firstNotNullOfOrNull { key ->
            audit.opt(key)?.takeUnless { it == JSONObject.NULL }?.toString()?.takeIf { it.isNotBlank() }
        }.orEmpty()

    val approved: Boolean get() = authoritative && event.optString("event_type") == "PROOF_APPROVED"
    val provenanceWarning: Boolean get() {
        // Honor an explicit audit-policy result first. Never rewrite historical provenance.
        if (audit.optBoolean("provenance_conflict") || audit.optBoolean("audit_requires_review")) return true
        val policy = audit.optJSONObject("audit_policy")
        val action = value("action").uppercase()
        val reviewer = value("reviewed_by")
        val source = value("approval_source").uppercase()
        val admin = value("Admin", "admin", "approved_by")
        if (policy?.has("provenance_consistent") == true) return !policy.optBoolean("provenance_consistent")
        // AI action and human authorization can legitimately coexist, but require a named authorizer.
        return (source == "AUTHENTICATED_HUMAN_ADMIN" && admin.isBlank()) ||
            (source == "AUTHENTICATED_HUMAN_ADMIN" && action == "AI_APPROVAL" &&
                reviewer.equals(admin, true) && reviewer.contains("AI", true)) ||
            (action == "AI_APPROVAL" && reviewer.isBlank()) ||
            (action.contains("REJECT") && approved)
    }
}

internal class ProofApprovalReceiptRepository(private val db: StrictRecordsLocalDb) {
    private fun row(cursor: Cursor): JSONObject = JSONObject().apply {
        for (i in 0 until cursor.columnCount) {
            put(cursor.getColumnName(i), when (cursor.getType(i)) {
                Cursor.FIELD_TYPE_NULL -> JSONObject.NULL
                Cursor.FIELD_TYPE_INTEGER -> cursor.getLong(i)
                Cursor.FIELD_TYPE_FLOAT -> cursor.getDouble(i)
                else -> cursor.getString(i)
            })
        }
    }

    fun load(selected: JSONObject): ProofApprovalReceiptData {
        val id = selected.optString("event_id")
        val stored = if (id.isBlank()) null else db.readableDatabase.rawQuery(
            "SELECT * FROM score_events WHERE event_id=?", arrayOf(id)
        ).use { if (it.moveToFirst()) row(it) else null }
        val event = stored ?: JSONObject(selected.toString())
        if (stored != null) event.put("audit",
            runCatching { JSONObject(event.optString("audit_json", "{}")) }.getOrDefault(JSONObject()))
        val audit = event.optJSONObject("audit") ?: JSONObject()
        val evidenceId = audit.optString("evidence_id").ifBlank { event.optString("evidence_id") }
        val submissionId = audit.optString("submission_id")
        val evidence = when {
            evidenceId.isNotBlank() -> db.readableDatabase.rawQuery(
                "SELECT * FROM proof_reviews WHERE evidence_id=?", arrayOf(evidenceId)
            ).use { if (it.moveToFirst()) row(it) else null }
            submissionId.isNotBlank() -> db.readableDatabase.rawQuery(
                "SELECT * FROM proof_reviews WHERE submission_id=? ORDER BY submitted_at DESC LIMIT 1",
                arrayOf(submissionId)
            ).use { if (it.moveToFirst()) row(it) else null }
            else -> null
        }
        val activityId = audit.optString("activity_id").ifBlank { evidence?.optString("activity_id").orEmpty() }
        val activity = if (activityId.isBlank()) null else db.readableDatabase.rawQuery(
            "SELECT * FROM scheduled_activities WHERE activity_id=?", arrayOf(activityId)
        ).use { if (it.moveToFirst()) row(it) else null }
        val points = event.optInt("points")
        val identityMatches = evidence != null &&
            (evidenceId.isBlank() || evidence.optString("evidence_id") == evidenceId) &&
            (submissionId.isBlank() || evidence.optString("submission_id") == submissionId)
        val amountMatches = !audit.has("points") || audit.optInt("points") == points
        val approvedStatus = evidence?.optString("status") in setOf("APPROVED", "VERIFIED", "AUTO_APPROVED")
        val eligible = identityMatches && approvedStatus &&
            db.hasApprovedRetainedProof(evidence!!.optString("evidence_id"))
        val credited = stored != null && event.optString("event_type") == "PROOF_APPROVED" &&
            points > 0 && amountMatches && eligible
        val message = when {
            stored == null -> "Ledger confirmation unavailable"
            !amountMatches || (evidence != null && !identityMatches) -> "Ledger reconciliation requires review"
            credited -> "Points Credited · confirmed in the local points ledger"
            event.optString("event_type") == "PROOF_APPROVED" -> "Approval Complete – Points Posting Pending"
            else -> "No confirmed approval credit"
        }
        return ProofApprovalReceiptData(event, evidence, activity, stored != null, credited, message,
            db.scheduleTimezone(event.optString("date").ifBlank { db.nowDate() }))
    }
}
