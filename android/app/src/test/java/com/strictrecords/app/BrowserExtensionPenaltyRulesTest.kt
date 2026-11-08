package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

class BrowserExtensionPenaltyRulesTest {
    private val now = 1_800_000_000_000L
    private fun proposal(): JSONObject {
        val evidence = JSONObject().put("episode_id","ext_ep_"+"a".repeat(32)).put("minute_index",1)
            .put("automatic_rule_version",BrowserExtensionPenaltyRules.VERSION).put("source","CHROME_MANAGEMENT_API")
            .put("grace_policy_minutes",5).put("disabled_detected_at",now/1000.0-360)
            .put("grace_deadline",now/1000.0-60).put("interval_start",now/1000.0-60).put("interval_end",now/1000.0)
            .put("verified_segments",JSONArray().put(JSONArray().put(now/1000.0-60).put(now/1000.0)))
            .put("installation",JSONObject().put("browser_key","chrome").put("installation_id","a".repeat(20))
                .put("target_id","iincmekgilffpjmmmcgloliadppioajc").put("helper_id","fnakpmohnkhnlhodhilbhecngkjgjkgn")
                .put("helper_profile","a835297c-6da1-4432-97a1-39388862179e").put("target_profile","626b1700-4ed1-405f-8f0b-c5536374ec61"))
        return JSONObject().put("proposal_id","tx_ext_ext_ep_"+"a".repeat(32)+"_m1")
            .put("event_type","BROWSER_EXTENSION_DISABLED").put("originating_device","windows_companion")
            .put("applicable_rule","CHROME_EXTENSION_ENFORCEMENT_RULE").put("proposed_points",-1)
            .put("date","2027-01-15").put("evidence",evidence)
    }
    private fun signed(batch: JSONObject): JSONObject {
        val raw = batch.toString()
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec("fixture-token".toByteArray(),"HmacSHA256"))
        return JSONObject().put("payload_json",raw).put("signature",mac.doFinal(raw.toByteArray()).joinToString("") { "%02x".format(it.toInt() and 255) })
    }
    private fun batch() = JSONObject().put("policy_version",BrowserExtensionPenaltyRules.VERSION).put("enabled",true)
        .put("device_id","android-fixture").put("issued_at_ms",now).put("proposals",JSONArray().put(proposal()))

    @Test fun completeScopedMinuteIsAccepted() { assertTrue(BrowserExtensionPenaltyRules.validProposal(proposal(),now)) }
    @Test fun unrelatedDeductionsCannotUseThisRule() {
        for(type in listOf("HOURLY_MISSED","SNOOZE","ADMIN_ADJUSTMENT")) assertFalse(BrowserExtensionPenaltyRules.validProposal(proposal().put("event_type",type),now))
    }
    @Test fun amountsAndIdentitiesAreStrict() {
        assertFalse(BrowserExtensionPenaltyRules.validProposal(proposal().put("proposed_points",-2),now))
        assertFalse(BrowserExtensionPenaltyRules.validProposal(proposal().put("proposal_id","other"),now))
    }
    @Test fun incompleteOrPreGraceEvidenceIsRejected() {
        for(length in listOf(59.0,61.0)) {
            val p=proposal();p.getJSONObject("evidence").put("verified_segments",JSONArray().put(JSONArray().put(now/1000.0-length).put(now/1000.0)))
            assertFalse(BrowserExtensionPenaltyRules.validProposal(p,now))
        }
    }
    @Test fun signedBatchRequiresPairedDeviceAndToken() {
        val envelope=signed(batch())
        assertNotNull(BrowserExtensionPenaltyRules.verifyBatch(envelope,"android-fixture","fixture-token",now))
        assertNull(BrowserExtensionPenaltyRules.verifyBatch(envelope,"other","fixture-token",now))
        assertNull(BrowserExtensionPenaltyRules.verifyBatch(envelope,"android-fixture","wrong",now))
    }
    @Test fun offStaleOrTamperedPolicyDoesNotCommit() {
        assertNull(BrowserExtensionPenaltyRules.verifyBatch(signed(batch().put("enabled",false)),"android-fixture","fixture-token",now))
        assertNull(BrowserExtensionPenaltyRules.verifyBatch(signed(batch()),"android-fixture","fixture-token",now+300001))
        val envelope=signed(batch());envelope.put("payload_json",envelope.getString("payload_json").replace("-1","-5"))
        assertNull(BrowserExtensionPenaltyRules.verifyBatch(envelope,"android-fixture","fixture-token",now))
    }
}
