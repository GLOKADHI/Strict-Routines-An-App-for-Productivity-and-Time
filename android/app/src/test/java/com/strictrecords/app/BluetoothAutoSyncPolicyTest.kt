package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class BluetoothAutoSyncPolicyTest {
    @Test fun retryDelayDoublesAndStopsAtFiveMinutes() {
        val policy = BluetoothAutoSyncPolicy()
        var now = 1000L
        for (delay in listOf(15000L, 30000L, 60000L, 120000L, 240000L, 300000L, 300000L)) {
            assertTrue(policy.isDue(now))
            policy.failed(now)
            assertFalse(policy.isDue(now + delay - 1))
            now += delay
        }
    }
    @Test fun successfulSyncAndExplicitResumeResetBackoff() {
        val policy = BluetoothAutoSyncPolicy()
        policy.failed(1000); policy.failed(1000); policy.succeeded(2000)
        assertFalse(policy.isDue(61999)); assertTrue(policy.isDue(62000))
        policy.retryNow(); assertTrue(policy.isDue(3000))
        policy.failed(3000); assertTrue(policy.isDue(18000))
    }
    @Test fun contentFingerprintIncludesMetadataAndIgnoresExportTimestampAndKeyOrder() {
        val first = JSONObject("""{"exported_at":"first","device_id":"fixture","proof_reviews":[{"status":"PENDING","points":0}]}""")
        val next = JSONObject("""{"proof_reviews":[{"points":0,"status":"PENDING"}],"device_id":"fixture","exported_at":"later"}""")
        assertEquals(BluetoothTransferProtocol.contentFingerprint(first), BluetoothTransferProtocol.contentFingerprint(next))
        next.getJSONArray("proof_reviews").getJSONObject(0).put("status", "REJECTED")
        assertNotEquals(BluetoothTransferProtocol.contentFingerprint(first), BluetoothTransferProtocol.contentFingerprint(next))
    }
    @Test fun automaticKeyIsBoundToRegistrationAndAccount() {
        val key = BluetoothTransferProtocol.automaticKey("fixture-device-token-only", "android-software-fixture", "user_lok")
        assertEquals("3e3fa6e6aecd9a7bf0b0197d508482e6ff5a167d5ab2d3a017842407b1d428d9", key)
        assertNotEquals(key, BluetoothTransferProtocol.automaticKey("fixture-device-token-only", "other-device", "user_lok"))
        assertNotEquals(key, BluetoothTransferProtocol.automaticKey("fixture-device-token-only", "android-software-fixture", "other-account"))
    }
}
