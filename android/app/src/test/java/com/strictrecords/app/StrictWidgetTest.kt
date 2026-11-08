package com.strictrecords.app

import android.content.SharedPreferences
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.lang.reflect.Proxy

class StrictWidgetTest {
    private val start=10_000_000L
    private fun task(id: String="a", status: String="SCHEDULED", s: Long=start, eligible: Boolean=true) =
        StrictWidgetTask(id,"Attendance",s,s+1_200_000L,status,eligible=eligible)
    @Test fun minutesRoundUp() { assertEquals("1 min",StrictWidgetTime.remaining(1L,60_000L)) }
    @Test fun noNegativeCountdown() { assertEquals("0 min",StrictWidgetTime.remaining(99L,1L)) }
    @Test fun hoursHaveNoSeconds() { assertEquals("1h 46m",StrictWidgetTime.remaining(0L,6_360_000L)) }
    @Test fun openingIsInclusive() { assertEquals("OPEN",task().state(start)) }
    @Test fun closingIsExclusive() { assertEquals("MISSED",task().state(start+1_200_000L)) }
    @Test fun nextSelectsOpenBeforeFuture() { assertEquals("a",StrictWidgetTime.next(listOf(task("b",s=start+100),task()),start)?.id) }
    @Test fun excusedNeverNext() { assertNull(StrictWidgetTime.next(listOf(task(status="EXCUSED")),start)) }
    @Test fun pendingNeverNext() { assertNull(StrictWidgetTime.next(listOf(task(status="PENDING_SYNC")),start)) }
    @Test fun verifiedNeverNext() { assertNull(StrictWidgetTime.next(listOf(task(status="VERIFIED")),start)) }
    @Test fun cancelledRemoved() { assertTrue(StrictWidgetTime.relevant(listOf(task(status="CANCELLED"))).isEmpty()) }
    @Test fun duplicateOccurrencesRemoved() { assertEquals(1,StrictWidgetTime.relevant(listOf(task(),task())).size) }
    @Test fun blockedNotEligible() { assertNull(StrictWidgetTime.next(listOf(task(eligible=false)),start)) }
    @Test fun pendingPreserved() { assertEquals("PENDING",task(status="PENDING").state(start)) }
    @Test fun approvedPreserved() { assertEquals("APPROVED",task(status="APPROVED").state(start)) }
    @Test fun underReviewPreserved() { assertEquals("UNDER_REVIEW",task(status="UNDER_REVIEW").state(start)) }
    @Test fun rejectedPreserved() { assertEquals("REJECTED",task(status="REJECTED").state(start)) }
    @Test fun noMoreFrequentThanMinute() { assertEquals(start+60_000,StrictWidgetTime.nextRefresh(start,listOf(task(s=start+10)),true)) }
    @Test fun approachingBoundaryChosen() { assertEquals(start+120_000,StrictWidgetTime.nextRefresh(start,listOf(task(s=start+120_000)),true)) }
    @Test fun countdownFiveMinuteCadence() { assertEquals(start+300_000,StrictWidgetTime.nextRefresh(start,listOf(task(s=start+1_000_000)),true)) }
    @Test fun idleThirtyMinuteCadence() { assertEquals(start+1_800_000,StrictWidgetTime.nextRefresh(start,emptyList(),false)) }
    @Test fun nineKindsAndDestinations() {
        assertEquals(9,StrictWidgetKind.values().size)
        assertTrue(StrictWidgetKind.values().all { it.tab in setOf("TIMELINE","REWARDS","DEVICES") })
    }
    @Test fun optionsRoundTrip() {
        val o=StrictWidgetOptions(StrictWidgetKind.L3,false,false,false,true,true,true)
        assertEquals(o,StrictWidgetStore.decode(StrictWidgetStore.encode(o),StrictWidgetKind.S1))
    }
    @Test fun corruptOptionsRecover() { assertEquals(StrictWidgetOptions(StrictWidgetKind.L1),StrictWidgetStore.decode("broken",StrictWidgetKind.L1)) }
    @Test fun providerFamilyAllowsContentChanges() {
        assertEquals(StrictWidgetKind.S4,StrictWidgetOptions(StrictWidgetKind.S4).forProvider(StrictWidgetKind.S1).kind)
        assertEquals(StrictWidgetKind.M2,StrictWidgetOptions(StrictWidgetKind.M2).forProvider(StrictWidgetKind.M1).kind)
    }
    @Test fun providerFamilyRepairsWrongSizeWithoutLosingAppearance() {
        val repaired=StrictWidgetOptions(StrictWidgetKind.L3,light=true).forProvider(StrictWidgetKind.S1)
        assertEquals(StrictWidgetKind.S1,repaired.kind)
        assertTrue(repaired.light)
    }
    @Test fun storedCrossFamilyKindCannotChangeLauncherSize() {
        val store=StrictWidgetStore(preferences(mutableMapOf()))
        store.save(7,StrictWidgetOptions(StrictWidgetKind.L3,compact=true))
        val restored=store.load(7,StrictWidgetKind.M1)
        assertEquals(StrictWidgetKind.M1,restored.kind)
        assertTrue(restored.compact)
    }
    @Test fun resetPreservesNonInstanceWidgetKeys() {
        val values=mutableMapOf<String,Any?>("display_cache" to "saved", "future_widget_metadata" to "keep")
        val store=StrictWidgetStore(preferences(values))
        store.save(12,StrictWidgetOptions(StrictWidgetKind.S3,light=true))
        store.reset()
        assertEquals(mapOf("display_cache" to "saved", "future_widget_metadata" to "keep"),values)
    }
    @Test fun perInstancePersistenceAndResetIsolation() {
        val values=mutableMapOf<String,Any?>()
        val prefs=preferences(values)
        val first=StrictWidgetStore(prefs)
        first.save(1,StrictWidgetOptions(StrictWidgetKind.S3,light=true))
        first.save(2,StrictWidgetOptions(StrictWidgetKind.L1,compact=true))
        first.cache(JSONObject().put("score",32))
        val reopened=StrictWidgetStore(prefs)
        assertTrue(reopened.load(1,StrictWidgetKind.S3).light)
        assertTrue(reopened.load(2,StrictWidgetKind.L1).compact)
        reopened.remove(1)
        assertTrue(reopened.load(2,StrictWidgetKind.L1).compact)
        reopened.reset()
        assertFalse(reopened.load(2,StrictWidgetKind.L1).compact)
        assertEquals(32,reopened.cached()!!.getInt("score"))
        assertTrue(values.keys.none { it.contains("score_events") || it.contains("system_config") })
    }
    @Test fun backupRestoreRemapsIds() {
        val store=StrictWidgetStore(preferences(mutableMapOf()))
        store.save(4,StrictWidgetOptions(StrictWidgetKind.M2,light=true))
        store.move(4,19,StrictWidgetKind.M1)
        assertTrue(store.load(19,StrictWidgetKind.M1).light)
        assertEquals(StrictWidgetKind.M1,store.load(4,StrictWidgetKind.M1).kind)
    }
    @Test fun unavailableScoreIsNotZero() { assertNull(StrictWidgetSnapshot(JSONObject(),true).score) }
    @Test fun corruptScoreIsNotZero() { assertNull(StrictWidgetSnapshot(JSONObject().put("score","bad"),true).score) }
    @Test fun corruptCacheTaskIsIgnored() {
        assertTrue(StrictWidgetSnapshot(JSONObject().put("tasks",org.json.JSONArray().put(JSONObject().put("id","broken"))),true).tasks.isEmpty())
    }
    @Test fun finishedTasksDoNotScheduleBoundaryAlarms() {
        assertEquals(start+1_800_000,StrictWidgetTime.nextRefresh(start,listOf(task(status="VERIFIED",s=start+60_000)),true))
    }
    @Test fun headerOccurrenceIsNotRepeatedInUpcomingRows() {
        assertEquals(listOf("b"),StrictWidgetTime.rows(listOf(task(),task("b",s=start+3_600_000)),start,false).map { it.id })
    }
    @Test fun largeSchedulePrioritizesUpcomingOverMorningHistory() {
        val rows=StrictWidgetTime.rows(listOf(task("past",s=start-3_600_000),task(),task("future",s=start+3_600_000)),start,true)
        assertEquals(listOf("future"),rows.map { it.id })
    }
    @Test fun staticHourlyTitleIsReadableWithoutChangingItsRecord() {
        val original=task().copy(title="Hourly Discipline Check (13:00-14:00)")
        assertEquals("Hourly check",StrictWidgetTime.title(original))
        assertEquals("Hourly Discipline Check (13:00-14:00)",original.title)
    }
    @Test fun cacheDeduplicatesAndDropsCancelled() {
        val tasks=org.json.JSONArray()
        for(status in listOf("OPEN","OPEN","CANCELLED")) tasks.put(JSONObject().put("id",if(status=="CANCELLED") "b" else "a").put("title","Task").put("start",start).put("end",start+99).put("status",status))
        assertEquals(1,StrictWidgetSnapshot(JSONObject().put("tasks",tasks)).tasks.size)
    }
    private fun preferences(values: MutableMap<String,Any?>): SharedPreferences {
        lateinit var editor: SharedPreferences.Editor
        editor=Proxy.newProxyInstance(javaClass.classLoader,arrayOf(SharedPreferences.Editor::class.java)) { _,method,args ->
            when(method.name) {
                "putString" -> { values[args!![0] as String]=args[1]; editor }
                "remove" -> { values.remove(args!![0] as String); editor }
                "clear" -> { values.clear(); editor }
                "apply" -> null
                "commit" -> true
                else -> editor
            }
        } as SharedPreferences.Editor
        return Proxy.newProxyInstance(javaClass.classLoader,arrayOf(SharedPreferences::class.java)) { _,method,args ->
            when(method.name) {
                "getAll" -> values.toMap()
                "getString" -> values[args!![0] as String] ?: args[1]
                "contains" -> values.containsKey(args!![0] as String)
                "edit" -> editor
                else -> null
            }
        } as SharedPreferences
    }
}
