package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LedgerSyncRulesTest {
    private fun snapshot(): JSONObject = LedgerSyncRules.envelope(JSONObject()
        .put("schema", 1).put("account_id", "user_lok").put("device_id", "android_fixture")
        .put("date", "2026-10-17").put("timezone", "Asia/Kolkata").put("is_dev_mode", false)
        .put("revision", 2).put("event_count", 1).put("confirmed_score", 4))

    private fun receipt(envelope: JSONObject): JSONObject = JSONObject(envelope.getString("payload_json"))
        .put("accepted", true).put("authority", "ANDROID").put("sha256", envelope.getString("sha256"))
        .put("confirmed_balance", 4)

    @Test fun staleWindowsFifteenCannotRaiseLocalFour() {
        assertEquals(4, ScoreSnapshotRules.displayScore(4, 15, true))
        assertEquals(0, ScoreSnapshotRules.displayScore(0, 15, true))
        assertEquals(-3, ScoreSnapshotRules.displayScore(-3, 15, true))
    }
    @Test fun aResponseNeedsTheExactManifestAndCheckpoint() {
        val snapshot = snapshot()
        assertTrue(LedgerSyncRules.acceptsReceipt(receipt(snapshot), snapshot))
        assertFalse(LedgerSyncRules.acceptsReceipt(null, snapshot))
        assertFalse(LedgerSyncRules.acceptsReceipt(JSONObject().put("shared_score", 4), snapshot))
        for (field in listOf("sha256", "account_id", "device_id", "date", "timezone", "revision", "event_count", "confirmed_balance")) {
            val invalid = receipt(snapshot)
            invalid.remove(field)
            assertFalse("Missing $field", LedgerSyncRules.acceptsReceipt(invalid, snapshot))
        }
    }
    @Test fun retryPreservesStableDigestAndReadableReasons() {
        assertEquals(snapshot().getString("sha256"), snapshot().getString("sha256"))
        assertEquals("Mi Fitness sleep bonus", LedgerSyncRules.transactionTitle("MI_FITNESS_SLEEP_BONUS"))
        assertEquals("Daily points reset", LedgerSyncRules.transactionTitle("DAILY_RESET"))
    }
}
