package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Android and Python read this same persisted fixture; this is not phone-user data. */
class SharedPointsLedgerFixtureTest {
    @Test fun androidExportsTheExactLedgerVerifiedByWindows() {
        val fixtureFile=generateSequence(File(System.getProperty("user.dir"))) { it.parentFile }
            .map { File(it,"tests/fixtures/ledger_zero_four_fifteen.json") }.first { it.isFile }
        val fixture=JSONObject(fixtureFile.readText())
        val events=fixture.getJSONArray("android_score_events")
        val entries=(0 until events.length()).map { i ->
            val event=events.getJSONObject(i)
            val entry=DisciplineScoringRules.ScorePointEntry(event.getString("event_type"),event.getInt("points"),event.optString("evidence_id"),false)
            val eligiblePoints=DisciplineScoringRules.calculateVerifiedDailyScore(listOf(entry),allowOverflow=true)
            event.put("eligible",event.getInt("points")==0||eligiblePoints==event.getInt("points"))
            entry
        }
        val score=DisciplineScoringRules.calculateVerifiedDailyScore(entries)
        assertEquals(4,score)
        assertEquals(score,ScoreSnapshotRules.displayScore(score,fixture.getInt("stale_windows_score"),true))
        val payload=JSONObject().put("schema",1).put("account_id",fixture.getString("account_id"))
            .put("device_id",fixture.getString("device_id")).put("date",fixture.getString("date"))
            .put("timezone",fixture.getString("timezone")).put("is_dev_mode",false).put("revision",1)
            .put("event_count",events.length()).put("score_events",events)
            .put("confirmed_score",score).put("raw_verified_points",DisciplineScoringRules.calculateVerifiedDailyScore(entries,allowOverflow=true))
        val envelope=LedgerSyncRules.envelope(payload)
        val result=JSONObject().put("fixture_only",true).put("android_calculated_score",score).put("ledger_snapshot",envelope)
        val output=File(fixtureFile.parentFile.parentFile.parentFile,".codex-work/points-ledger-android-shared-fixture.json")
        output.parentFile.mkdirs();output.writeText(result.toString(2))
    }
}
