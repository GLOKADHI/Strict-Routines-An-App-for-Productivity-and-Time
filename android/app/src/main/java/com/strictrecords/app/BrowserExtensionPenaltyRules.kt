package com.strictrecords.app

import org.json.JSONObject
import java.security.MessageDigest
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import kotlin.math.abs

/** Only this event has an automatic rule; other deductions keep their approval workflow. */
object BrowserExtensionPenaltyRules {
    const val VERSION = "extension-disabled-v1"

    fun verifyBatch(envelope: JSONObject, deviceId: String, token: String, nowMs: Long): JSONObject? = runCatching {
        require(token.isNotBlank() && deviceId.isNotBlank())
        val raw = envelope.getString("payload_json")
        require(raw.toByteArray().size <= 2_000_000)
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(token.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        val actual = mac.doFinal(raw.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it.toInt() and 255) }
        require(MessageDigest.isEqual(actual.toByteArray(), envelope.getString("signature").toByteArray()))
        val batch = JSONObject(raw)
        require(batch.getString("policy_version") == VERSION && batch.getBoolean("enabled"))
        require(batch.getString("device_id") == deviceId)
        require(abs(nowMs - batch.getLong("issued_at_ms")) <= 300_000L)
        val proposals = batch.getJSONArray("proposals")
        require(proposals.length() <= 200)
        for (index in 0 until proposals.length()) require(validProposal(proposals.getJSONObject(index), nowMs))
        batch
    }.getOrNull()

    fun validProposal(proposal: JSONObject, nowMs: Long): Boolean = runCatching {
        require(proposal.getString("event_type") == "BROWSER_EXTENSION_DISABLED")
        require(proposal.getString("originating_device") == "windows_companion")
        require(proposal.getString("applicable_rule") == "CHROME_EXTENSION_ENFORCEMENT_RULE")
        require(proposal.get("proposed_points") == -1)
        val evidence = proposal.optJSONObject("evidence") ?: JSONObject(proposal.getString("evidence_json"))
        require(evidence.getString("automatic_rule_version") == VERSION)
        require(evidence.getString("source") == "CHROME_MANAGEMENT_API")
        val episode = evidence.getString("episode_id")
        val minute = evidence.getInt("minute_index")
        require(evidence.get("minute_index") is Int && minute > 0)
        require(episode.matches(Regex("ext_ep_[a-f0-9]{32}")))
        require(proposal.getString("proposal_id") == "tx_ext_${episode}_m$minute")
        require(proposal.getString("date").matches(Regex("\\d{4}-\\d{2}-\\d{2}")))
        val grace = evidence.getInt("grace_policy_minutes")
        val detected = evidence.getDouble("disabled_detected_at")
        val deadline = evidence.getDouble("grace_deadline")
        require(grace in listOf(2, 5, 7, 10) && detected.isFinite() && detected > 0 && deadline.isFinite())
        require(abs(deadline - detected - grace * 60) < .001)
        val binding = evidence.getJSONObject("installation")
        require(binding.getString("browser_key") in listOf("chrome","edge","brave","comet","vivaldi","opera","arc"))
        require(binding.getString("installation_id").matches(Regex("[a-f0-9]{20}")))
        require(binding.getString("target_id") == "iincmekgilffpjmmmcgloliadppioajc")
        require(binding.getString("helper_id") == "fnakpmohnkhnlhodhilbhecngkjgjkgn")
        UUID.fromString(binding.getString("helper_profile")); UUID.fromString(binding.getString("target_profile"))
        val segments = evidence.getJSONArray("verified_segments")
        require(segments.length() in 1..120)
        var previous = deadline
        var total = 0.0
        for (index in 0 until segments.length()) {
            val segment = segments.getJSONArray(index)
            require(segment.length() == 2)
            val start = segment.getDouble(0); val end = segment.getDouble(1)
            require(start.isFinite() && end.isFinite() && start >= previous-.001 && end > start && end <= nowMs/1000.0+5)
            total += end-start
            previous = end
        }
        require(abs(total-60) < .001)
        require(evidence.getDouble("interval_start") == segments.getJSONArray(0).getDouble(0))
        require(evidence.getDouble("interval_end") == segments.getJSONArray(segments.length()-1).getDouble(1))
        true
    }.getOrDefault(false)
}
