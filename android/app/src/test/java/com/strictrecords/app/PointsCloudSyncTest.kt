package com.strictrecords.app
import org.junit.Assert.*
import org.junit.Test
import org.json.JSONObject

class PointsCloudSyncTest {
    @Test fun onlyDedicatedHttpsOriginsAreAccepted(){
        assertTrue(PointsCloudSync.validOrigin("https://points.example.com"))
        for(value in listOf("http://points.example.com","https://user:secret@points.example.com","https://points.example.com/path","https://points.example.com?token=bad","file:///tmp/data"))assertFalse(value,PointsCloudSync.validOrigin(value))
    }
    @Test fun snapshotsStripPrivateUnnecessaryData(){
        val p=JSONObject("""{"schema":1,"account_id":"a","device_id":"d","date":"2026-10-30","timezone":"Asia/Kolkata","revision":1,"event_count":1,"confirmed_score":4,"raw_verified_points":4,"email":"secret","score_events":[{"event_id":"e","event_type":"MI_FITNESS_SLEEP_BONUS","points":4,"date":"2026-10-30","timestamp":"2026-10-30T10:00:00","eligible":true,"description":"personal","image_base64":"private-image","audit":{"access_token":"private-token","evidence_id":"ref"}}]}""")
        val result=PointsCloudSync.minimalEnvelope(LedgerSyncRules.envelope(p)).getString("payload_json")
        for(value in listOf("secret","personal","private-image","private-token"))assertFalse(result.contains(value))
        assertTrue(result.contains("evidence_id"))
    }
    @Test fun receiptMustMatchAccountSourceBalanceAndMonotonicRevision(){
        val p=JSONObject("""{"account_id":"a","date":"2026-10-30","confirmed_score":4}""")
        val envelope=LedgerSyncRules.envelope(p)
        val receipt=JSONObject().put("account_id","a").put("date","2026-10-30").put("accepted_source_sha256",envelope.getString("sha256")).put("ledger_revision",3).put("confirmed_balance",4).put("score_authority","ANDROID")
        assertTrue(PointsCloudSync.accepts(receipt,envelope,2))
        assertFalse(PointsCloudSync.accepts(receipt,envelope,4))
        receipt.put("confirmed_balance",99);assertFalse(PointsCloudSync.accepts(receipt,envelope,2))
        receipt.put("confirmed_balance",4).put("account_id","another");assertFalse(PointsCloudSync.accepts(receipt,envelope,2))
    }
}
