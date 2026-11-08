package com.strictrecords.app

import org.json.JSONObject
import java.io.DataInputStream
import java.io.DataOutputStream
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** Same versioned, length-bounded RFCOMM protocol as the Windows receiver. */
object BluetoothTransferProtocol {
    const val NAME = "STRICTROUTINES_BT"
    const val VERSION = 2
    const val MAX_BYTES = 16 * 1024 * 1024
    const val MAX_FRAME = 16384
    const val AUTO_MODE = "REGISTERED_DEVICE_V1"
    const val AUTO_UUID = "63b3f238-5317-4b5a-bc18-2bb6d482ca60"

    fun automaticKey(token: String, device: String, account: String): String =
        authenticate(LedgerSyncRules.sha256(token), "STRICTROUTINES_AUTO_V1\n$device\n$account")

    fun automaticReceiptText(receipt: JSONObject): String = receiptKeys.joinToString("\n") { receipt.get(it).toString() } +
        "\n$AUTO_MODE\n${receipt.optInt("pending_metadata_count")}\n${receipt.optInt("applied_record_count")}\n${receipt.optString("planning_sha256")}"

    fun acceptsAutomaticReceipt(receipt: JSONObject,payload: JSONObject,key: String,session: String,hash: String): Boolean =
        acceptsReceipt(receipt,payload,key,session,hash) && receipt.optString("auth_mode")==AUTO_MODE &&
            receipt.has("planning_payload") && LedgerSyncRules.sha256(receipt.getString("planning_payload"))==receipt.optString("planning_sha256") &&
            MessageDigest.isEqual(authenticate(key,automaticReceiptText(receipt)).toByteArray(Charsets.US_ASCII),
                receipt.optString("automatic_receipt_mac").toByteArray(Charsets.US_ASCII))

    /** Stable across object-key order and export time, but includes every ledger and metadata change. */
    fun contentFingerprint(payload: JSONObject): String {
        fun canonical(value: Any?): String = when (value) {
            null, JSONObject.NULL -> "null"
            is JSONObject -> value.keys().asSequence().toList().sorted().joinToString(",", "{", "}") {
                JSONObject.quote(it) + ":" + canonical(value.get(it))
            }
            is org.json.JSONArray -> (0 until value.length()).joinToString(",", "[", "]") { canonical(value.get(it)) }
            is String -> JSONObject.quote(value)
            else -> value.toString()
        }
        val copy = JSONObject(payload.toString()).apply { remove("exported_at") }
        return LedgerSyncRules.sha256(canonical(copy))
    }
    val receiptKeys = listOf("protocol", "version", "session_id", "package_id", "success", "scoring_date",
        "confirmed_score", "total_count", "transferred_count", "already_present_count", "rejected_count", "conflict_count", "ledger_sha256", "synced_at")

    fun authenticate(code: String, text: String): String = Mac.getInstance("HmacSHA256").run {
        init(SecretKeySpec(code.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        doFinal(text.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it.toInt() and 255) }
    }

    fun authText(session: String, challenge: String, hash: String, device: String, account: String, size: Int, count: Int): String {
        require(listOf(session, challenge, hash, device, account).all { Regex("[A-Za-z0-9_.:@-]{1,128}").matches(it) })
        return listOf(session, challenge, hash, device, account, size.toString(), count.toString()).joinToString("\n")
    }

    fun readFrame(input: DataInputStream): JSONObject {
        val size = input.readInt()
        require(size in 1..MAX_FRAME) { "Unsupported or oversized receiver message" }
        val bytes = ByteArray(size); input.readFully(bytes)
        return JSONObject(bytes.toString(Charsets.UTF_8))
    }

    fun writeFrame(output: DataOutputStream, value: JSONObject) {
        val bytes = value.toString().toByteArray(Charsets.UTF_8)
        require(bytes.size in 1..MAX_FRAME)
        output.writeInt(bytes.size); output.write(bytes); output.flush()
    }

    fun recordCount(payload: JSONObject): Int = JSONObject(payload.getJSONObject("ledger_snapshot").getString("payload_json"))
        .getJSONArray("score_events").length() + (payload.optJSONArray("proof_reviews")?.length() ?: 0) +
        (payload.optJSONArray("scheduled_activities")?.length() ?: 0) + (payload.optJSONArray("planning_requests")?.length() ?: 0)

    fun acceptsReceipt(receipt: JSONObject, payload: JSONObject, code: String, session: String, hash: String): Boolean {
        if (receiptKeys.any { !receipt.has(it) }) return false
        if (receipt.optString("protocol") != NAME || receipt.optInt("version") != VERSION ||
            receipt.optString("session_id") != session || receipt.optString("package_id") != hash || !receipt.optBoolean("success") ||
            receipt.optString("scoring_date") != JSONObject(payload.getJSONObject("ledger_snapshot").getString("payload_json")).optString("date") ||
            receipt.optInt("total_count", -1) != recordCount(payload) || receipt.optInt("rejected_count", -1) != 0 ||
            receipt.optInt("conflict_count", -1) != 0 ||
            receipt.optInt("transferred_count", -1) < 0 || receipt.optInt("already_present_count", -1) < 0 ||
            receipt.optInt("transferred_count") + receipt.optInt("already_present_count") != receipt.optInt("total_count")) return false
        val text = receiptKeys.joinToString("\n") { receipt.get(it).toString() }
        if (!MessageDigest.isEqual(authenticate(code, text).toByteArray(Charsets.US_ASCII), receipt.optString("receipt_mac").toByteArray(Charsets.US_ASCII))) return false
        return LedgerSyncRules.acceptsReceipt(receipt.optJSONObject("ledger_receipt"), payload.getJSONObject("ledger_snapshot")) &&
            receipt.optInt("confirmed_score", Int.MIN_VALUE) == receipt.getJSONObject("ledger_receipt").optInt("confirmed_balance") &&
            receipt.optString("ledger_sha256") == payload.getJSONObject("ledger_snapshot").optString("sha256")
    }
}
