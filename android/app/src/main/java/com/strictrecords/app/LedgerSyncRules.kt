package com.strictrecords.app

import org.json.JSONObject
import java.security.MessageDigest

/** Integrity and receipt checks for the existing APK-to-PC ledger transfer. */
object LedgerSyncRules {
    fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it.toInt() and 0xff) }

    fun envelope(payload: JSONObject): JSONObject {
        val raw = payload.toString()
        return JSONObject().put("payload_json", raw).put("sha256", sha256(raw))
    }

    fun acceptsReceipt(receipt: JSONObject?, envelope: JSONObject): Boolean {
        if (receipt == null || !receipt.optBoolean("accepted") || receipt.optString("authority") != "ANDROID") return false
        if (listOf("account_id", "device_id", "date", "timezone", "is_dev_mode", "revision", "event_count", "confirmed_balance", "sha256").any { !receipt.has(it) }) return false
        val payload = JSONObject(envelope.getString("payload_json"))
        return receipt.optString("sha256") == envelope.optString("sha256") &&
            receipt.optString("account_id") == payload.optString("account_id") &&
            receipt.optString("device_id") == payload.optString("device_id") &&
            receipt.optString("date") == payload.optString("date") &&
            receipt.optString("timezone") == payload.optString("timezone") &&
            receipt.optBoolean("is_dev_mode") == payload.optBoolean("is_dev_mode") &&
            receipt.optLong("revision", -1) == payload.optLong("revision") &&
            receipt.optInt("event_count", -1) == payload.optInt("event_count") &&
            receipt.optInt("confirmed_balance", Int.MIN_VALUE) == payload.optInt("confirmed_score")
    }

    fun transactionTitle(type: String): String = when (type) {
        "MI_FITNESS_SLEEP_BONUS" -> "Mi Fitness sleep bonus"
        "INITIAL_RESET" -> "Initial balance reset"
        "DAILY_RESET" -> "Daily points reset"
        "PROOF_APPROVED" -> "Proof approved"
        "PC_SHUTDOWN_BONUS" -> "PC shutdown bonus"
        "OFFICIAL_EVENT_BOOKING" -> "Official event pre-booked"
        "OFFICIAL_EVENT_CANCEL" -> "Official event cancelled"
        else -> type.lowercase().replace('_', ' ').replaceFirstChar { it.uppercase() }
    }
}
