package com.strictrecords.app

/** Pure ordering checks shared by network and Bluetooth planning receipts. */
object PlanningSyncRules {
    fun mayReplaceState(savedRevision: Int, incomingRevision: Int, savedTimeMs: Long, incomingTimeMs: Long): Boolean =
        incomingTimeMs > 0 && incomingRevision >= savedRevision &&
            (incomingRevision > savedRevision || incomingTimeMs >= savedTimeMs)
    fun ownsReceipt(currentRequest: String, acknowledgedRequest: String): Boolean =
        currentRequest.isNotBlank() && currentRequest == acknowledgedRequest
}
