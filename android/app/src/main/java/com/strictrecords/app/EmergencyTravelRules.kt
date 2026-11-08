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
 * Pure logic rules for EMERGENCY TRAVEL BOOK — Advance Booking, Approval Workflow,
 * Dynamic Slot Modification, and Schedule Activation.
 */
object EmergencyTravelRules {

    const val DEFAULT_TIMEZONE = "Asia/Kolkata"
    const val MAX_HEARTS = 3
    const val DEFAULT_EVIDENCE_GRACE_HOURS = 2
    const val MAX_TRAVEL_DURATION_HOURS = 12
    const val MIN_TRAVEL_DURATION_MINUTES = 15

    // Supported Travel Types
    val SUPPORTED_TRAVEL_TYPES = listOf(
        "CAR_RIDE",
        "AUTO",
        "BUS",
        "TRAIN",
        "FLIGHT",
        "OTHER"
    )

    // Primary Travel Request Statuses
    object Status {
        const val DRAFT = "DRAFT"
        const val REQUEST_SUBMITTED = "REQUEST_SUBMITTED"
        const val AWAITING_APPROVAL = "AWAITING_APPROVAL"
        const val APPROVED = "APPROVED"
        const val REJECTED = "REJECTED"
        const val SCHEDULED = "SCHEDULED"
        const val ACTIVE = "ACTIVE"
        const val COMPLETED = "COMPLETED"
        const val AWAITING_EVIDENCE = "AWAITING_EVIDENCE"
        const val EVIDENCE_SUBMITTED = "EVIDENCE_SUBMITTED"
        const val EVIDENCE_VERIFIED = "EVIDENCE_VERIFIED"
        const val EXPIRED = "EXPIRED"
    }

    data class AuditEntry(
        val revision: Int,
        val action: String,
        val details: String,
        val timestampMs: Long,
        val actor: String
    ) {
        fun toJson(): JSONObject = JSONObject().apply {
            put("revision", revision)
            put("action", action)
            put("details", details)
            put("timestamp_ms", timestampMs)
            put("actor", actor)
        }

        companion object {
            fun fromJson(json: JSONObject): AuditEntry = AuditEntry(
                revision = json.optInt("revision", 1),
                action = json.optString("action", "UNKNOWN"),
                details = json.optString("details", ""),
                timestampMs = json.optLong("timestamp_ms", System.currentTimeMillis()),
                actor = json.optString("actor", "system")
            )
        }
    }

    data class EmergencyTravelRequest(
        val requestId: String,
        val accountId: String = "user_lok",
        val travelDate: String, // yyyy-MM-dd
        val departureTime: String, // HH:mm
        val departureMs: Long,
        val returnTime: String, // HH:mm
        val returnMs: Long,
        val durationMinutes: Int,
        val travelType: String = "CAR_RIDE",
        val purpose: String,
        val pickupLocation: String? = null,
        val destinationLocation: String? = null,
        val status: String = Status.AWAITING_APPROVAL,
        val approvalReason: String? = null,
        val reviewedBy: String? = null,
        val reviewedAtMs: Long? = null,
        val activatedAtMs: Long? = null,
        val completedAtMs: Long? = null,
        val evidenceDeadlineMs: Long = returnMs + (DEFAULT_EVIDENCE_GRACE_HOURS * 3600_000L),
        val isMateriallyModified: Boolean = false,
        val auditHistory: List<AuditEntry> = emptyList(),
        val createdAtMs: Long = System.currentTimeMillis(),
        val updatedAtMs: Long = System.currentTimeMillis()
    ) {
        fun toJson(): JSONObject = JSONObject().apply {
            put("request_id", requestId)
            put("account_id", accountId)
            put("travel_date", travelDate)
            put("departure_time", departureTime)
            put("departure_ms", departureMs)
            put("return_time", returnTime)
            put("return_ms", returnMs)
            put("duration_minutes", durationMinutes)
            put("travel_type", travelType)
            put("purpose", purpose)
            if (pickupLocation != null) put("pickup_location", pickupLocation)
            if (destinationLocation != null) put("destination_location", destinationLocation)
            put("status", status)
            if (approvalReason != null) put("approval_reason", approvalReason)
            if (reviewedBy != null) put("reviewed_by", reviewedBy)
            if (reviewedAtMs != null) put("reviewed_at_ms", reviewedAtMs)
            if (activatedAtMs != null) put("activated_at_ms", activatedAtMs)
            if (completedAtMs != null) put("completed_at_ms", completedAtMs)
            put("evidence_deadline_ms", evidenceDeadlineMs)
            put("is_materially_modified", isMateriallyModified)
            put("audit_history", JSONArray().apply {
                auditHistory.forEach { put(it.toJson()) }
            })
            put("created_at_ms", createdAtMs)
            put("updated_at_ms", updatedAtMs)
        }

        companion object {
            fun fromJson(json: JSONObject): EmergencyTravelRequest {
                val audits = mutableListOf<AuditEntry>()
                val arr = json.optJSONArray("audit_history")
                if (arr != null) {
                    for (i in 0 until arr.length()) {
                        arr.optJSONObject(i)?.let { audits.add(AuditEntry.fromJson(it)) }
                    }
                }
                return EmergencyTravelRequest(
                    requestId = json.getString("request_id"),
                    accountId = json.optString("account_id", "user_lok"),
                    travelDate = json.getString("travel_date"),
                    departureTime = json.getString("departure_time"),
                    departureMs = json.getLong("departure_ms"),
                    returnTime = json.getString("return_time"),
                    returnMs = json.getLong("return_ms"),
                    durationMinutes = json.getInt("duration_minutes"),
                    travelType = json.optString("travel_type", "CAR_RIDE"),
                    purpose = json.getString("purpose"),
                    pickupLocation = json.optString("pickup_location").takeIf { it.isNotBlank() },
                    destinationLocation = json.optString("destination_location").takeIf { it.isNotBlank() },
                    status = json.optString("status", Status.AWAITING_APPROVAL),
                    approvalReason = json.optString("approval_reason").takeIf { it.isNotBlank() },
                    reviewedBy = json.optString("reviewed_by").takeIf { it.isNotBlank() },
                    reviewedAtMs = if (json.has("reviewed_at_ms")) json.getLong("reviewed_at_ms") else null,
                    activatedAtMs = if (json.has("activated_at_ms")) json.getLong("activated_at_ms") else null,
                    completedAtMs = if (json.has("completed_at_ms")) json.getLong("completed_at_ms") else null,
                    evidenceDeadlineMs = json.optLong("evidence_deadline_ms", 0L),
                    isMateriallyModified = json.optBoolean("is_materially_modified", false),
                    auditHistory = audits,
                    createdAtMs = json.optLong("created_at_ms", System.currentTimeMillis()),
                    updatedAtMs = json.optLong("updated_at_ms", System.currentTimeMillis())
                )
            }
        }
    }

    data class ValidationResult(
        val valid: Boolean,
        val message: String,
        val departureMs: Long = 0L,
        val returnMs: Long = 0L,
        val durationMinutes: Int = 0
    )

    fun parseDateTimeToEpoch(dateStr: String, timeStr: String, timezone: String = DEFAULT_TIMEZONE): Long? {
        val cleanDate = dateStr.trim()
        val cleanTime = timeStr.trim()
        val df = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US).apply {
            timeZone = TimeZone.getTimeZone(timezone)
            isLenient = false
        }
        return runCatching { df.parse("$cleanDate $cleanTime")?.time }.getOrNull()
    }

    /**
     * Validates an emergency travel request input.
     * Unlike regular pre-booking, emergency travel can be booked shortly before departure
     * (e.g. 5:40 PM for a 6:00 PM departure).
     */
    fun validateRequestInput(
        travelDate: String,
        departureTime: String,
        returnTime: String,
        purpose: String,
        travelType: String,
        nowMs: Long,
        timezone: String = DEFAULT_TIMEZONE
    ): ValidationResult {
        if (purpose.trim().length < 3) {
            return ValidationResult(false, "Travel purpose is required (minimum 3 characters).")
        }

        val depMs = parseDateTimeToEpoch(travelDate, departureTime, timezone)
            ?: return ValidationResult(false, "Invalid departure date or time format (use YYYY-MM-DD and HH:mm).")
        val retMs = parseDateTimeToEpoch(travelDate, returnTime, timezone)
            ?: return ValidationResult(false, "Invalid return date or time format (use YYYY-MM-DD and HH:mm).")

        // Emergency departure allows requests shortly before departure or up to 5 min past planned departure
        if (depMs < (nowMs - 5 * 60 * 1000L)) {
            return ValidationResult(false, "Planned departure time cannot be in the past.")
        }

        if (retMs <= depMs) {
            return ValidationResult(false, "Expected return time must be strictly after departure time.")
        }

        val durationMinutes = ((retMs - depMs) / 60_000L).toInt()
        if (durationMinutes < MIN_TRAVEL_DURATION_MINUTES) {
            return ValidationResult(false, "Minimum travel duration is $MIN_TRAVEL_DURATION_MINUTES minutes.")
        }

        val maxMinutes = MAX_TRAVEL_DURATION_HOURS * 60
        if (durationMinutes > maxMinutes) {
            return ValidationResult(false, "Maximum travel duration is $MAX_TRAVEL_DURATION_HOURS hours.")
        }

        return ValidationResult(
            valid = true,
            message = "Emergency travel request valid ($durationMinutes min).",
            departureMs = depMs,
            returnMs = retMs,
            durationMinutes = durationMinutes
        )
    }

    /**
     * Checks if a new or modified request conflicts with an existing active/approved travel request.
     */
    fun hasConflict(
        departureMs: Long,
        returnMs: Long,
        existingRequests: List<EmergencyTravelRequest>,
        excludeRequestId: String? = null
    ): Pair<Boolean, String> {
        for (req in existingRequests) {
            if (excludeRequestId != null && req.requestId == excludeRequestId) continue
            if (req.status in setOf(Status.REJECTED, Status.EXPIRED, Status.COMPLETED, Status.EVIDENCE_VERIFIED)) continue

            // Check interval overlap: [departureMs, returnMs) overlaps [req.departureMs, req.returnMs)
            val overlaps = departureMs < req.returnMs && returnMs > req.departureMs
            if (overlaps) {
                return Pair(true, "Conflicting travel slot exists from ${req.departureTime} to ${req.returnTime} (ID: ${req.requestId}).")
            }
        }
        return Pair(false, "No conflict.")
    }

    /**
     * Evaluates administrator approval or policy auto-approval.
     */
    fun evaluateApproval(
        request: EmergencyTravelRequest,
        adminApproved: Boolean?,
        adminReason: String?,
        policyAutoApprove: Boolean = false,
        actor: String = "admin"
    ): EmergencyTravelRequest {
        val nowMs = System.currentTimeMillis()
        val newStatus = when {
            adminApproved == true -> Status.APPROVED
            adminApproved == false -> Status.REJECTED
            policyAutoApprove -> Status.APPROVED
            else -> Status.AWAITING_APPROVAL
        }

        val actionName = when (newStatus) {
            Status.APPROVED -> "APPROVAL_GRANTED"
            Status.REJECTED -> "APPROVAL_REJECTED"
            else -> "AWAITING_REVIEW"
        }

        val newAudit = request.auditHistory + AuditEntry(
            revision = request.auditHistory.size + 1,
            action = actionName,
            details = adminReason ?: (if (policyAutoApprove) "Auto-approved by policy" else "Administrator decision"),
            timestampMs = nowMs,
            actor = actor
        )

        return request.copy(
            status = newStatus,
            approvalReason = adminReason ?: (if (policyAutoApprove) "Auto-approved by policy" else request.approvalReason),
            reviewedBy = if (newStatus in setOf(Status.APPROVED, Status.REJECTED)) actor else null,
            reviewedAtMs = if (newStatus in setOf(Status.APPROVED, Status.REJECTED)) nowMs else null,
            isMateriallyModified = false,
            auditHistory = newAudit,
            updatedAtMs = nowMs
        )
    }

    /**
     * Dynamic Travel Slot Modification.
     * Material modifications (duration increase > 15m or shift > 15m) re-trigger approval workflow.
     */
    fun modifyTravelSlot(
        existing: EmergencyTravelRequest,
        newDepartureTime: String,
        newReturnTime: String,
        reason: String,
        nowMs: Long,
        timezone: String = DEFAULT_TIMEZONE,
        existingRequests: List<EmergencyTravelRequest> = emptyList()
    ): Pair<EmergencyTravelRequest?, String> {
        val validation = validateRequestInput(
            travelDate = existing.travelDate,
            departureTime = newDepartureTime,
            returnTime = newReturnTime,
            purpose = existing.purpose,
            travelType = existing.travelType,
            nowMs = nowMs,
            timezone = timezone
        )
        if (!validation.valid) {
            return Pair(null, validation.message)
        }

        val (conflict, conflictMsg) = hasConflict(
            departureMs = validation.departureMs,
            returnMs = validation.returnMs,
            existingRequests = existingRequests,
            excludeRequestId = existing.requestId
        )
        if (conflict) {
            return Pair(null, conflictMsg)
        }

        val depShiftMinutes = Math.abs((validation.departureMs - existing.departureMs) / 60_000L)
        val retShiftMinutes = Math.abs((validation.returnMs - existing.returnMs) / 60_000L)
        val durationDiff = validation.durationMinutes - existing.durationMinutes

        // Material change check: shift > 15 minutes or duration increased
        val isMaterial = depShiftMinutes > 15L || retShiftMinutes > 15L || durationDiff > 0

        // If previously approved and change is material, renewed approval is required
        val newStatus = if (existing.status == Status.APPROVED && isMaterial) {
            Status.AWAITING_APPROVAL
        } else {
            existing.status
        }

        val auditDetails = "Slot modified: ${existing.departureTime}-${existing.returnTime} -> $newDepartureTime-$newReturnTime. Reason: $reason"
        val newAudit = existing.auditHistory + AuditEntry(
            revision = existing.auditHistory.size + 1,
            action = if (isMaterial) "MATERIAL_SLOT_MODIFICATION" else "MINOR_SLOT_ADJUSTMENT",
            details = auditDetails,
            timestampMs = nowMs,
            actor = "user"
        )

        val updated = existing.copy(
            departureTime = newDepartureTime,
            departureMs = validation.departureMs,
            returnTime = newReturnTime,
            returnMs = validation.returnMs,
            durationMinutes = validation.durationMinutes,
            evidenceDeadlineMs = validation.returnMs + (DEFAULT_EVIDENCE_GRACE_HOURS * 3600_000L),
            status = newStatus,
            isMateriallyModified = isMaterial,
            auditHistory = newAudit,
            updatedAtMs = nowMs
        )

        val statusMsg = if (newStatus == Status.AWAITING_APPROVAL) {
            "Travel slot updated. Material schedule modification requires renewed administrator approval."
        } else {
            "Travel slot updated successfully."
        }

        return Pair(updated, statusMsg)
    }

    /**
     * Determines whether Travel Mode can be activated at nowMs.
     */
    fun canActivate(
        nowMs: Long,
        request: EmergencyTravelRequest?,
        travelHeartsRemaining: Int
    ): Pair<Boolean, String> {
        if (travelHeartsRemaining <= 0) {
            return Pair(false, "Zero hearts remaining in Travel Mode. Travel activation is disabled under the 3-heart discipline policy.")
        }
        if (request == null) {
            return Pair(false, "No emergency travel request found.")
        }
        if (request.status !in setOf(Status.APPROVED, Status.SCHEDULED)) {
            return Pair(false, "Travel request is not approved (current status: ${request.status}). Unapproved requests cannot bypass restrictions.")
        }

        // Available 5 minutes before scheduled departure up to scheduled return
        val availableStartMs = request.departureMs - (5 * 60 * 1000L)
        if (nowMs < availableStartMs) {
            val sdf = SimpleDateFormat("h:mm a", Locale.US).apply {
                timeZone = TimeZone.getTimeZone(DEFAULT_TIMEZONE)
            }
            return Pair(false, "Travel Mode activation opens at ${sdf.format(Date(availableStartMs))} (5 minutes before departure).")
        }

        if (nowMs >= request.returnMs) {
            return Pair(false, "The approved travel allocation expired at ${request.returnTime}.")
        }

        return Pair(true, "OK")
    }

    /**
     * Resolves the authoritative state transition for a travel request at nowMs.
     */
    fun resolveState(nowMs: Long, request: EmergencyTravelRequest): EmergencyTravelRequest {
        var req = request
        // 1. If approved and within travel window, transition to SCHEDULED / ACTIVE
        if (req.status in setOf(Status.APPROVED, Status.SCHEDULED) && nowMs >= req.departureMs && nowMs < req.returnMs) {
            req = req.copy(status = Status.ACTIVE, activatedAtMs = req.activatedAtMs ?: nowMs, updatedAtMs = nowMs)
        }

        // 2. If active and return time has arrived, auto-expire travel mode and move to AWAITING_EVIDENCE
        if (req.status == Status.ACTIVE && nowMs >= req.returnMs) {
            req = req.copy(status = Status.AWAITING_EVIDENCE, completedAtMs = req.completedAtMs ?: nowMs, updatedAtMs = nowMs)
        }

        // 3. If awaiting evidence and evidence deadline has passed, transition to EXPIRED
        if (req.status == Status.AWAITING_EVIDENCE && nowMs > req.evidenceDeadlineMs) {
            req = req.copy(status = Status.EXPIRED, updatedAtMs = nowMs)
        }

        return req
    }

    fun generateRequestId(dateStr: String, departureTime: String): String {
        val cleanDate = dateStr.replace("-", "")
        val cleanTime = departureTime.replace(":", "")
        val randomSuffix = UUID.randomUUID().toString().take(4).uppercase(Locale.US)
        return "ETR-$cleanDate-$cleanTime-$randomSuffix"
    }
}
