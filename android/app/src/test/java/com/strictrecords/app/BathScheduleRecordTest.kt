package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class BathScheduleRecordTest {
    private val date="2026-10-21"
    private val zone="Asia/Kolkata"
    private fun window(planned:String)=BathWindowRules.calculateBathWindow(1,false,date,planned,timezone=zone)
    private fun details(window:BathWindowRules.BathWindow)=JSONObject().put("planned_time",window.plannedHhmm)
        .put("reserved_end",window.reservedEndIso).put("held_hourly_act_id",window.heldHourlyActId)
        .put("held_hourly_target_ms",window.heldHourlyTargetMs).put("occurrence",1)
    private fun saved(window:BathWindowRules.BathWindow, data:JSONObject=details(window))=
        BathScheduleRecord.fromSaved(date,window.actId,window.plannedIso,window.proofWindowStartIso,
            window.proofWindowEndIso,window.notificationIso,data,zone)

    @Test fun screenshotTimesRemainDistinct() {
        val record=saved(window("14:10"))
        assertEquals("2:10 PM",record.plannedDisplay)
        assertEquals("2:10–3:10 PM",record.activityIntervalDisplay)
        assertEquals("3:40–4:00 PM",record.proofWindowDisplay)
        assertEquals("2:00–3:00 PM",record.heldHourlySlotDisplay)
        assertEquals("ACT-20261021-1500-HOURLY",record.assignedSlot.occurrenceId)
        assertEquals(90*60_000L,record.proofWindowStartMs-record.plannedMs)
        assertEquals(20*60_000L,record.proofWindowEndMs-record.proofWindowStartMs)
        assertEquals(record.proofWindowStartMs-60_000L,record.reminderMs)
    }
    @Test fun proofOpeningNeverClaimsToBeBathStart() {
        val record=saved(window("14:10"))
        assertEquals("Photo submission opens at 3:40 PM · Proof due by 4:00 PM",record.proofStatus(false))
        assertEquals("Photo submission is open · Proof due by 4:00 PM",record.proofStatus(true))
        for(event in listOf("OPENING","OPENED","WARN1M","CLOSED")) {
            val text=record.reminderMessage(event)
            assertFalse(text.contains("Bath session starts"))
            assertTrue(text.contains("4:00 PM"))
        }
    }
    @Test fun currentAssignmentsFollowTheHourContainingPlannedStart() {
        for((planned,id) in mapOf("12:10" to "1300","12:20" to "1300","12:40" to "1300","14:10" to "1500")) {
            assertEquals("ACT-20261021-$id-HOURLY",saved(window(planned)).assignedSlot.occurrenceId)
        }
    }
    @Test fun minuteThirtyBoundaryHasNoAssignmentDiscontinuity() {
        assertEquals("ACT-20261021-1300-HOURLY",saved(window("12:30")).assignedSlot.occurrenceId)
        assertEquals("ACT-20261021-1300-HOURLY",saved(window("12:31")).assignedSlot.occurrenceId)
    }
    @Test fun onlyCanonicalOccurrenceAndTimestampMatch() {
        val record=saved(window("14:10"))
        val slots=HourlyWindowRules.slots(date,"07:00",zone)
        val matches=slots.filter { record.matchesHourly(it.id,it.targetMs) }
        assertEquals(1,matches.size)
        assertEquals(record.assignedSlot.occurrenceId,matches.single().id)
        assertFalse(record.matchesHourly("duplicate-id",record.assignedSlot.targetMs))
        assertFalse(record.matchesHourly(record.assignedSlot.occurrenceId,record.assignedSlot.targetMs+3_600_000))
    }
    @Test fun proofColumnsRemainAuthoritativeWhileMutableAssignmentIsRecomputed() {
        val first=window("14:10");val later=window("15:40")
        val data=details(first).put("held_hourly_act_id",later.heldHourlyActId)
            .put("held_hourly_target_ms",later.heldHourlyTargetMs)
        val record=BathScheduleRecord.fromSaved(date,first.actId,first.plannedIso,
            later.proofWindowStartIso,later.proofWindowEndIso,later.notificationIso,data,zone)
        assertEquals(first.plannedMs,record.plannedMs)
        assertEquals(later.proofWindowStartMs,record.proofWindowStartMs)
        assertEquals(first.heldHourlyActId,record.assignedSlot.occurrenceId)
        val historical=BathScheduleRecord.fromSaved(date,first.actId,first.plannedIso,
            later.proofWindowStartIso,later.proofWindowEndIso,later.notificationIso,data,zone,preserveSavedAssignment=true)
        assertEquals(later.heldHourlyActId,historical.assignedSlot.occurrenceId)
        assertEquals(data.getString("held_hourly_act_id"),later.heldHourlyActId)
    }
    @Test fun legacyFallbackIsDeterministicAndReadOnly() {
        val first=window("14:10");val data=JSONObject().put("planned_time","14:10")
        val before=data.toString()
        val a=saved(first,data);val b=saved(first,data)
        assertEquals(a,b);assertEquals(a.reservationId,b.reservationId)
        assertEquals(before,data.toString())
    }
    @Test fun mutableInvalidSavedIdentityRepairsCanonicallyWithoutChangingHistoricalView() {
        val first=window("14:10");val data=details(first).put("held_hourly_act_id","ACT-20261021-1800-HOURLY")
        val record=saved(first,data)
        assertTrue(record.assignmentValid)
        assertTrue(record.matchesHourly(first.heldHourlyActId,first.heldHourlyTargetMs))
        assertEquals(first.heldHourlySlotDisplay,record.heldHourlySlotDisplay)
        val historical=BathScheduleRecord.fromSaved(date,first.actId,first.plannedIso,first.proofWindowStartIso,
            first.proofWindowEndIso,first.notificationIso,data,zone,preserveSavedAssignment=true)
        assertFalse(historical.assignmentValid)
        assertEquals("NONE",historical.overlapWithHourly(first.proofWindowStartMs,first.proofWindowEndMs))
    }
    @Test fun reservationsCarryCalendarDatesAcrossMidnight() {
        val record=BathWindowRules.resolveBathReservedHourlySlot(date,"23:40",zone)
        assertEquals("ACT-20261022-0000-HOURLY",record.occurrenceId)
        assertEquals(3_600_000L,record.slotEndMs-record.slotStartMs)
        val midnight=BathWindowRules.resolveBathReservedHourlySlot(date,"00:10",zone)
        assertEquals("2026-10-21",ScheduleClock.display(midnight.slotStartMs,zone,"yyyy-MM-dd"))
        assertEquals(3_600_000L,midnight.slotEndMs-midnight.slotStartMs)
    }
    @Test fun completedSubmittedAndHistoricalMissesAreProtected() {
        for(status in BathScheduleRecord.protectedStatuses) assertFalse(BathScheduleRecord.canExcuseHourly(status,null,0))
        assertFalse(BathScheduleRecord.canExcuseHourly("OPEN","2026-10-21T14:00:00+05:30",0))
        assertFalse(BathScheduleRecord.canExcuseHourly("SCHEDULED",null,8))
        assertTrue(BathScheduleRecord.canExcuseHourly("SCHEDULED",null,0))
        assertTrue(BathScheduleRecord.canExcuseHourly("EXCUSED",null,0))
    }
    @Test fun stalePendingOrMissedSyncCannotReplaceExcusedWithoutEvidence() {
        val data=JSONObject().put("suppressed_by_bath",true)
        for(incoming in listOf("SCHEDULED","UPCOMING","OPEN","MISSED","PENDING_SYNC","EXCUSED"))
            assertTrue(BathScheduleRecord.preserveAssignmentOnSync("EXCUSED",data,incoming))
        assertFalse(BathScheduleRecord.preserveAssignmentOnSync("VERIFIED",data,"MISSED"))
        assertFalse(BathScheduleRecord.preserveAssignmentOnSync("EXCUSED",JSONObject(),"MISSED"))
    }

    @Test fun importedPlanningRangeIsConvertedBeforePersistenceNotWhileDrawing() {
        val window=window("14:10")
        val incoming=JSONObject().put("activity_id",window.actId).put("date",date).put("type","BATH")
            .put("scheduled_time",window.plannedIso).put("window_start","${date}T13:00:00+05:30")
            .put("window_end","${date}T17:30:00+05:30").put("details_json","{}")
        val original=incoming.toString()
        val normalized=BathScheduleRecord.normalizeIncoming(incoming,zone)
        assertEquals(window.proofWindowStartIso,normalized.getString("window_start"))
        assertEquals(window.proofWindowEndIso,normalized.getString("window_end"))
        assertEquals(window.notificationIso,normalized.getString("notification_time"))
        assertEquals(original,incoming.toString())
        val details=JSONObject(normalized.getString("details_json"))
        val saved=BathScheduleRecord.fromSaved(date,window.actId,normalized.getString("scheduled_time"),
            normalized.getString("window_start"),normalized.getString("window_end"),normalized.getString("notification_time"),details,zone)
        assertEquals("2:10–3:10 PM",saved.activityIntervalDisplay)
        assertEquals("3:40–4:00 PM",saved.proofWindowDisplay)
        assertEquals("ACT-20261021-1500-HOURLY",saved.assignedSlot.occurrenceId)
    }
    @Test fun screenshotLegacyAssignmentIsRepairedFromActual1422Activity() {
        val bath=window("14:22")
        val stale=details(bath).put("held_hourly_act_id","ACT-20261021-1400-HOURLY")
            .put("held_hourly_target_ms",bath.heldHourlyTargetMs-3_600_000L)
            .put("held_hourly_slot_display","1:00–2:00 PM")
            .put("attendance_reservation_policy","BATH_ASSIGNED_HOUR_V1")
        val original=stale.toString()
        val record=saved(bath,stale)
        assertEquals("2:00–3:00 PM",record.heldHourlySlotDisplay)
        assertEquals("3:52–4:12 PM",record.proofWindowDisplay)
        val hourly=HourlyWindowRules.slots(date,"07:00",zone)
        val previous=hourly.single { it.id=="ACT-20261021-1400-HOURLY" }
        val overlapping=hourly.single { it.id=="ACT-20261021-1500-HOURLY" }
        assertEquals("NONE",record.overlapWithHourly(previous.startMs,previous.endMs))
        assertEquals("FULL",record.overlapWithHourly(overlapping.startMs,overlapping.endMs))
        val corrected=record.assignmentDetails(stale)
        assertEquals("ACT-20261021-1500-HOURLY",corrected.getString("held_hourly_act_id"))
        assertEquals(BathWindowRules.ATTENDANCE_POLICY,corrected.getString("attendance_reservation_policy"))
        assertEquals(original,stale.toString())
    }

    @Test fun changedDurationChangesReservationIdentityAndAllActualWindowDecisions() {
        val short=BathWindowRules.calculateBathWindow(1,false,date,"14:22",durationMinutes=20)
        val long=BathWindowRules.calculateBathWindow(1,false,date,"14:22",durationMinutes=120)
        val a=BathScheduleRecord.fromWindow(short,zone)
        val b=BathScheduleRecord.fromWindow(long,zone)
        assertNotEquals(a.reservationId,b.reservationId)
        val hourly=HourlyWindowRules.slots(date,"07:00",zone)
        val target=hourly.single { it.id=="ACT-20261021-1500-HOURLY" }
        assertEquals("NONE",a.overlapWithHourly(target.startMs,target.endMs))
        assertEquals("FULL",b.overlapWithHourly(target.startMs,target.endMs))
        assertEquals(30*60_000L,b.proofWindowStartMs-b.activityEndMs)
    }

    @Test fun normalizingExplicitDurationDoesNotRestoreAnOldSixtyMinuteInterval() {
        val bath=BathWindowRules.calculateBathWindow(1,false,date,"14:22",durationMinutes=120)
        val incoming=JSONObject().put("activity_id",bath.actId).put("date",date).put("type","BATH")
            .put("scheduled_time",bath.plannedIso).put("window_start",date+"T13:00:00+05:30")
            .put("window_end",date+"T17:30:00+05:30")
            .put("details_json",JSONObject().put("activity_duration_minutes",120)
                .put("held_hourly_act_id","ACT-20261021-1400-HOURLY").toString())
        val normalized=BathScheduleRecord.normalizeIncoming(incoming,zone)
        val result=JSONObject(normalized.getString("details_json"))
        assertEquals(bath.reservedEndIso,result.getString("reserved_end"))
        assertEquals(bath.proofWindowStartIso,normalized.getString("window_start"))
        assertEquals(bath.proofWindowEndIso,normalized.getString("window_end"))
        assertEquals(120,result.getInt("activity_duration_minutes"))
        assertEquals(bath.heldHourlyActId,result.getString("held_hourly_act_id"))
    }

    @Test fun normalizationRetainsValidLegacyActivityDurationFromSavedEndpoints() {
        val bath=BathWindowRules.calculateBathWindow(1,false,date,"14:22",durationMinutes=90)
        val incoming=JSONObject().put("activity_id",bath.actId).put("date",date).put("type","BATH")
            .put("scheduled_time",bath.plannedIso).put("window_start",date+"T13:00:00+05:30")
            .put("window_end",date+"T17:30:00+05:30")
            .put("details_json",JSONObject().put("reserved_start",bath.reservedStartIso)
                .put("reserved_end",bath.reservedEndIso).toString())
        val normalized=BathScheduleRecord.normalizeIncoming(incoming,zone)
        val result=JSONObject(normalized.getString("details_json"))
        assertEquals(90,result.getInt("activity_duration_minutes"))
        assertEquals(bath.proofWindowStartIso,normalized.getString("window_start"))
    }
}
