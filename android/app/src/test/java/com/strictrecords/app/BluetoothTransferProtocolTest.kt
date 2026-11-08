package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.*

class BluetoothTransferProtocolTest {
    private fun payload(): JSONObject {
        val ledger = JSONObject().put("schema",1).put("account_id","user_lok").put("device_id","android_fixture")
            .put("date","2026-10-18").put("timezone","Asia/Kolkata").put("is_dev_mode",false)
            .put("revision",1).put("event_count",1).put("confirmed_score",4)
            .put("score_events",JSONArray().put(JSONObject().put("event_id","sleep-four")))
        return JSONObject().put("ledger_snapshot",LedgerSyncRules.envelope(ledger))
            .put("proof_reviews",JSONArray()).put("scheduled_activities",JSONArray())
    }
    private fun receipt(p: JSONObject): JSONObject {
        val envelope = p.getJSONObject("ledger_snapshot")
        val ledger = JSONObject(envelope.getString("payload_json")).put("accepted",true).put("authority","ANDROID")
            .put("confirmed_balance",4).put("sha256",envelope.getString("sha256"))
        val r = JSONObject().put("protocol",BluetoothTransferProtocol.NAME).put("version",2)
            .put("session_id","session").put("package_id","package").put("success",true)
            .put("scoring_date","2026-10-18").put("confirmed_score",4).put("total_count",1)
            .put("transferred_count",1).put("already_present_count",0).put("rejected_count",0).put("conflict_count",0)
            .put("ledger_sha256",envelope.getString("sha256")).put("ledger_receipt",ledger).put("synced_at","2026-10-18T10:00:00+00:00")
        sign(r)
        return r
    }
    private fun sign(r: JSONObject) {
        r.put("receipt_mac",BluetoothTransferProtocol.authenticate("123456",BluetoothTransferProtocol.receiptKeys.joinToString("\n") { r.get(it).toString() }))
    }
    private fun accepts(r: JSONObject,p: JSONObject) = BluetoothTransferProtocol.acceptsReceipt(r,p,"123456","session","package")

    @Test fun validCommitAndDuplicateReceiptAreAccepted() {
        val p=payload();val r=receipt(p)
        assertTrue(accepts(r,p))
        r.put("transferred_count",0).put("already_present_count",1);sign(r)
        assertTrue(accepts(r,p))
    }
    @Test fun receiptWithPointsBreakdownIsAccepted() {
        val p = payload()
        p.put("points_breakdown", JSONObject().put("wake_up", 10).put("bath", 15).put("hourly", 40).put("gym", 20).put("focus", 10).put("penalties", -5).put("confirmed_score", 90))
        val r = receipt(p)
        r.put("points_breakdown", JSONObject().put("wake_up", 10).put("bath", 15).put("hourly", 40).put("gym", 20).put("focus", 10).put("penalties", -5).put("confirmed_score", 90))
        sign(r)
        assertTrue(accepts(r, p))
    }
    @Test fun forgedBalanceOrUnsignedReceiptIsRejected() {
        val p=payload();val r=receipt(p)
        r.put("confirmed_score",99)
        assertFalse(accepts(r,p))
        sign(r);assertFalse(accepts(r,p))
        val missing=receipt(p);missing.remove("receipt_mac");assertFalse(accepts(missing,p))
    }
    @Test fun incompleteOrConflictingCountsCannotShowSuccess() {
        val p=payload()
        for (field in listOf("total_count","rejected_count","conflict_count","transferred_count","already_present_count")) {
            val r=receipt(p);r.put(field,7);sign(r);assertFalse(field,accepts(r,p))
        }
    }
    @Test fun receiptIsBoundToSessionPackageAndLedger() {
        val p=payload()
        for (field in listOf("session_id","package_id","ledger_sha256","protocol","scoring_date")) {
            val r=receipt(p);r.put(field,"changed");sign(r);assertFalse(field,accepts(r,p))
        }
        val r=receipt(p);r.getJSONObject("ledger_receipt").put("revision",99);assertFalse(accepts(r,p))
    }
    @Test fun frameRoundTripPreservesUtf8AndRejectsTruncation() {
        val buffer=ByteArrayOutputStream()
        BluetoothTransferProtocol.writeFrame(DataOutputStream(buffer),JSONObject().put("message","Verified âœ“"))
        val bytes=buffer.toByteArray()
        assertEquals("Verified âœ“",BluetoothTransferProtocol.readFrame(DataInputStream(ByteArrayInputStream(bytes))).getString("message"))
        try { BluetoothTransferProtocol.readFrame(DataInputStream(ByteArrayInputStream(bytes.copyOf(bytes.size-1))));fail("Truncated frame accepted") } catch (_: EOFException) { }
    }
    @Test fun oversizedAndNegativeFramesAreRejectedBeforeAllocation() {
        for (length in listOf(-1,0,16385)) {
            val buffer=ByteArrayOutputStream();DataOutputStream(buffer).writeInt(length)
            try { BluetoothTransferProtocol.readFrame(DataInputStream(ByteArrayInputStream(buffer.toByteArray())));fail("Invalid length accepted") } catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun authenticationUsesTheSamePythonWireVector() {
        val text=BluetoothTransferProtocol.authText("session","challenge","package","android_fixture","user_lok",123,3)
        assertEquals("session\nchallenge\npackage\nandroid_fixture\nuser_lok\n123\n3",text)
        // Generated independently by Python hmac.new(..., hashlib.sha256).
        assertEquals("cfb627e5c432f995e1f91a86b90afe62dd95047b0440c90aaced186fbc7314b8",BluetoothTransferProtocol.authenticate("123456",text))
    }
    @Test fun countIncludesMetadataAndOnlyOneLedgerCopy() {
        val p=payload().put("proof_reviews",JSONArray().put(JSONObject())).put("scheduled_activities",JSONArray().put(JSONObject()))
        assertEquals(3,BluetoothTransferProtocol.recordCount(p))
    }
    @Test fun automaticPlanningSnapshotRequiresItsExactSignedHash() {
        val p=payload();val r=receipt(p)
        val planning=JSONObject().put("results",JSONArray()).put("planning",JSONObject()).toString()
        r.put("auth_mode",BluetoothTransferProtocol.AUTO_MODE).put("planning_payload",planning)
            .put("planning_sha256",LedgerSyncRules.sha256(planning)).put("pending_metadata_count",0).put("applied_record_count",1)
        r.put("automatic_receipt_mac",BluetoothTransferProtocol.authenticate("123456",BluetoothTransferProtocol.automaticReceiptText(r)))
        assertTrue(BluetoothTransferProtocol.acceptsAutomaticReceipt(r,p,"123456","session","package"))
        r.put("planning_payload","{\"gym_planned\":true}")
        assertFalse(BluetoothTransferProtocol.acceptsAutomaticReceipt(r,p,"123456","session","package"))
        r.put("planning_sha256",LedgerSyncRules.sha256(r.getString("planning_payload")))
        assertFalse(BluetoothTransferProtocol.acceptsAutomaticReceipt(r,p,"123456","session","package"))
    }
}
