package com.strictrecords.app

/** Pure acceptance rules; a pending mutation is never a confirmed routine snapshot. */
object GymRoutineSyncRules {
    fun mayAcknowledge(expectedAccount: String, incomingAccount: String, expectedRequest: String, incomingRequest: String, committed: Boolean): Boolean =
        expectedAccount.isNotBlank() && expectedAccount == incomingAccount && expectedRequest.isNotBlank() && expectedRequest == incomingRequest && committed

    fun mayReplace(expectedAccount: String, incomingAccount: String, savedRevision: Long, incomingRevision: Long): Boolean =
        expectedAccount.isNotBlank() && incomingAccount == expectedAccount && savedRevision >= 0L && incomingRevision >= savedRevision

    fun automaticRefreshDue(nowElapsed: Long, lastAttemptElapsed: Long?): Boolean =
        lastAttemptElapsed == null || nowElapsed - lastAttemptElapsed !in 0L until 30_000L
}
