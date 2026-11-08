package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Synthetic rows and the existing shared Android/Windows fixture; no application DB access. */
class FarmLedgerAdapterTest {
    private fun row(type: String = "PROOF_APPROVED", points: Int = 8,
                    audit: JSONObject = JSONObject(), retained: Boolean = false, device: String = "") =
        FarmLedgerAdapter.transaction("fixture-event", 1792636200000L, "2026-10-22", type,
            "Fixture activity", points, device, audit, retained)

    private fun authority(source: String = "AUTHENTICATED_HUMAN_ADMIN") =
        JSONObject().put("approval_source", source).put("approved_by", "fixture-admin")

    @Test fun retainedApprovalUsesTheExistingScoreEligibilityRule() {
        val audit = JSONObject().put("evidence_id", "fixture-proof").put("activity_category", "CODING")
        assertFalse(row(audit = audit).confirmed)
        assertFalse(row(audit = audit).proofReward)
        val approved = row(audit = audit, retained = true)
        assertTrue(approved.confirmed)
        assertTrue(approved.proofReward)
        assertEquals(FarmModel.Action.APPLYING_NUTRIENTS, FarmModel.action(approved.category))
    }

    @Test fun uncertainReviewsAndArbitraryDescriptionsNeverEstablishFraud() {
        listOf("AI_UNCERTAIN", "SERVICE_UNAVAILABLE", "IMAGE_UNCLEAR", "PENDING", "REJECTED").forEach { decision ->
            assertFalse(row("PROOF_REJECTED", -10, authority().put("decision", decision)).storm)
        }
        assertFalse(row("PROOF_REJECTED", -10, authority().put("reason", "fake fraud confirmed")).storm)
        assertFalse(row("FAKE_PROOF_PENALTY", -10, JSONObject().put("approval_source", "AI_MODEL")).storm)
    }

    @Test fun persistedFakePenaltyRequiresExplicitHumanAuthority() {
        assertTrue(row("FAKE_PROOF_PENALTY", -10, authority()).storm)
        assertTrue(row("PROOF_REJECTED", -10, authority().put("decision", "CONFIRMED_FAKE")).storm)
        assertFalse(row("FAKE_PROOF_PENALTY", -10, JSONObject()).storm)
        assertFalse(row("FAKE_PROOF_PENALTY", -10, authority().put("approved_by", "")).storm)
    }

    @Test fun accessibilityStormRequiresThePersistedEnforcementAuthority() {
        val actual = row("RESTRICTED_APP_PENALTY", -1, authority("ANDROID_ACCESSIBILITY_ENFORCEMENT"))
        assertTrue(actual.confirmed)
        assertTrue(actual.storm)
        assertFalse(row("RESTRICTED_APP_PENALTY", -1).storm)
        assertFalse(row("SERVICE_UNAVAILABLE", -1, authority()).storm)
        assertFalse(row("RESTRICTED_APP_PENALTY", -1, authority("BROWSER_EXTENSION_ENFORCEMENT")).storm)
    }

    @Test fun partialStatusUsesSavedReviewOrSleepRuleMetadata() {
        val proof = JSONObject().put("evidence_id", "fixture-proof")
            .put("taufa_ai", JSONObject().put("is_partial_points", true))
        assertTrue(row(points = 4, audit = proof, retained = true).partial)
        val sleep = row("MI_FITNESS_SLEEP_BONUS", 4,
            JSONObject().put("LateHourBands", 1).put("SubmissionId", "fixture-sleep").put("Reason", "Late onset band"))
        assertTrue(sleep.partial)
        assertTrue(sleep.proofReward)
        assertEquals("fixture-sleep", sleep.reference)
        assertEquals("Late onset band", sleep.reason)
        assertFalse(row("MI_FITNESS_SLEEP_BONUS", 4).partial) // A small value alone is not proof of a partial award.
    }

    @Test fun sourceIsReadFromMetadataRatherThanAssumedFromTaskType() {
        assertEquals("Origin unavailable", row("PC_SHUTDOWN_BONUS", 4).origin)
        assertEquals("Windows EXE", row(audit = JSONObject().put("origin_platform", "EXE")).origin)
        assertEquals("Android APK", row(audit = JSONObject().put("Device", "Android Device")).origin)
        assertEquals("Windows EXE", row(device = "windows-fixture").origin)
    }

    @Test fun auditedAdjustmentsDoNotCreateProofGrowthOrPenaltyStorms() {
        listOf("INITIAL_RESET", "REVERSAL", "ADMIN_SCORE_ADJUSTMENT", "SCORE_CORRECTION").forEach { type ->
            val entry = row(type, 5, authority().put("reverses_event_id", "old-fixture"), true)
            assertTrue(entry.confirmed)
            assertFalse(entry.proofReward)
            assertFalse(entry.storm)
            assertEquals("old-fixture", entry.reverses)
        }
    }

    @Test fun sharedLedgerFixtureStillReconcilesAtFourPointsWithoutInventedWindowsRows() {
        val fixture = generateSequence(File(System.getProperty("user.dir"))) { it.parentFile }
            .map { File(it, "tests/fixtures/ledger_zero_four_fifteen.json") }.first { it.isFile }
        val records = JSONObject(fixture.readText()).getJSONArray("android_score_events")
        val mapped = (0 until records.length()).map { index ->
            val r = records.getJSONObject(index)
            FarmLedgerAdapter.transaction(r.getString("event_id"), 1792636200000L, r.getString("date"),
                r.getString("event_type"), r.getString("description"), r.getInt("points"), "",
                r.getJSONObject("audit"), false)
        }
        assertEquals(4, mapped.filter { it.confirmed }.sumOf { it.points })
        assertEquals(1, mapped.count { it.proofReward })
        assertTrue(mapped.none { it.origin == "Windows EXE" })
        assertTrue(mapped.none { it.storm })
    }
}
