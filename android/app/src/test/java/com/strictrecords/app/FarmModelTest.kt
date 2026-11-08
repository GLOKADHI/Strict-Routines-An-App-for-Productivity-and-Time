package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

class FarmModelTest {
    @Test fun cropTransitionsAreBoundedAndRestoreTheirExactTarget() {
        val start=FarmModel.Plot(.1f,.3f,.8f)
        val end=FarmModel.Plot(.5f,.8f,1f)
        assertEquals(start,FarmModel.blendPlot(start,end,-1f))
        val middle=FarmModel.blendPlot(start,end,.5f)
        assertEquals(.3f,middle.growth,.0001f)
        assertEquals(.55f,middle.moisture,.0001f)
        assertEquals(end,FarmModel.blendPlot(start,end,2f))
    }
    @Test fun navigationAllowsNewRewardsButProcessRestartAndAccountChangeHydrateSilently() {
        val event=FarmModel.Effect("new",1000L,FarmModel.Weather.RAIN,FarmModel.Action.WATERING,"Rewarded",8)
        val session=FarmModel.VisualSession()
        assertTrue(session.fresh("account:prod",emptyList(),emptySet(),true,1000L).isEmpty())
        assertEquals(listOf(event),session.fresh("account:prod",listOf(event),emptySet(),true,1000L))
        assertTrue(session.fresh("account:prod",listOf(event),setOf("new"),true,1000L).isEmpty())
        assertTrue(session.fresh("other:prod",listOf(event),emptySet(),true,1000L).isEmpty())
        assertTrue(session.fresh("account:dev",listOf(event),emptySet(),true,1000L).isEmpty())
        assertTrue(FarmModel.VisualSession().fresh("account:prod",listOf(event),emptySet(),true,1000L).isEmpty())
    }
    private val zone="Asia/Kolkata"
    private fun time(s:String,z:String=zone)=SimpleDateFormat("yyyy-MM-dd HH:mm:ss",Locale.US).apply {timeZone=TimeZone.getTimeZone(z)}.parse(s)!!.time
    private val now=time("2026-10-22 12:00:00")
    private fun tx(id:String="reward-1",points:Int=8,confirmed:Boolean=true,type:String="PROOF_APPROVED",origin:String="Android APK",at:Long=now-30000,partial:Boolean=false,reverses:String="",proof:Boolean=points>0,storm:Boolean=false,category:String="STUDY")=
        FarmModel.Transaction(id,at,"2026-10-22",type,"Synthetic activity",points,confirmed,origin,category,partial=partial,proofReward=proof,storm=storm,reverses=reverses)
    private fun snapshot(rows:List<FarmModel.Transaction> = emptyList(),slots:List<FarmModel.Slot> = emptyList(),balance:Int=0)=FarmModel.Snapshot("synthetic-account",rows,slots,balance,now,zone,"Offline fixture")
    private fun slot(id:String="slot-1",status:String="MISSED",required:Boolean=true,pending:Boolean=false,end:Long=now-60000)=FarmModel.Slot(id,"2026-10-22",end,status,required,pending)

    @Test fun dailyTotalsMatchTransactionsAndKeepAuthorityBalance() {
        val s=snapshot(listOf(tx(),tx("deduction",-3,type="RESTRICTED_APP_PENALTY",storm=true),tx("win",5,origin="Windows EXE")),balance=64)
        val total=FarmModel.totals(s,FarmModel.range(FarmModel.Period.DAILY,now,zone))
        assertEquals(13,total.earned);assertEquals(3,total.deducted);assertEquals(10,total.net);assertEquals(64,s.balance)
        assertEquals(5,total.windows);assertEquals(5,total.android)
    }
    @Test fun pendingRewardsAndPendingDeductionsAreExcluded() {
        val s=snapshot(listOf(tx(confirmed=false),tx("proposal",-8,confirmed=false)))
        val totals=FarmModel.totals(s,FarmModel.range(FarmModel.Period.DAILY,now,zone))
        assertEquals(0,totals.net);assertEquals(2,totals.pending);assertTrue(FarmModel.project(s).effects.isEmpty())
    }
    @Test fun dailyBoundariesAreHalfOpenInScheduleTimezone() {
        val r=FarmModel.range(FarmModel.Period.DAILY,now,zone)
        assertEquals(time("2026-10-22 00:00:00"),r.start);assertEquals(time("2026-10-23 00:00:00"),r.end)
        val s=snapshot(listOf(tx(at=r.start),tx("end",at=r.end),tx("before",at=r.start-1)))
        assertEquals(8,FarmModel.totals(s,r).earned)
    }
    @Test fun weekUsesLocaleWeekStartAndShowsRange() {
        val us=FarmModel.range(FarmModel.Period.WEEKLY,now,zone,Locale.US)
        val uk=FarmModel.range(FarmModel.Period.WEEKLY,now,zone,Locale.UK)
        assertEquals(time("2026-10-18 00:00:00"),us.start);assertEquals(time("2026-10-19 00:00:00"),uk.start)
        assertTrue(uk.label.contains("–"))
    }
    @Test fun leapMonthAndYearTransitionHaveCorrectEnds() {
        val feb=FarmModel.range(FarmModel.Period.MONTHLY,time("2028-02-12 12:00:00"),zone)
        assertEquals(29*86400000L,feb.end-feb.start)
        val dec=FarmModel.range(FarmModel.Period.MONTHLY,time("2026-12-20 12:00:00"),zone)
        assertEquals(time("2027-01-01 00:00:00"),dec.end)
    }
    @Test fun daylightSavingDayUsesCalendarInsteadOfFixed24Hours() {
        val z="America/New_York"
        val spring=FarmModel.range(FarmModel.Period.DAILY,time("2026-03-08 12:00:00",z),z)
        assertEquals(23*3600000L,spring.end-spring.start)
    }
    @Test fun lightingFollowsLocalHourAcrossFivePhases() {
        listOf("05:45:00" to FarmModel.Light.SUNRISE,"09:10:00" to FarmModel.Light.MORNING,"14:30:00" to FarmModel.Light.AFTERNOON,"18:10:00" to FarmModel.Light.SUNSET,"21:45:00" to FarmModel.Light.NIGHT).forEach {(clock,phase)->
            assertEquals(phase,FarmModel.lighting(time("2026-10-22 $clock"),zone).phase)
        }
    }
    @Test fun lightingBlendsContinuouslyAtPhaseBoundary() {
        val a=FarmModel.lighting(time("2026-10-22 06:59:59"),zone)
        val b=FarmModel.lighting(time("2026-10-22 07:00:00"),zone)
        assertEquals(b.phase,a.next);assertTrue(a.blend>.99f);assertEquals(0f,b.blend,.0001f)
    }
    @Test fun duplicateSyncIsOneEconomicTransactionAndOneGrowthEvent() {
        val e=tx();val a=snapshot(listOf(e));val b=snapshot(listOf(e,e,e))
        assertEquals(FarmModel.project(a),FarmModel.project(b))
        assertEquals(8,FarmModel.totals(b,FarmModel.range(FarmModel.Period.DAILY,now,zone)).earned)
    }
    @Test fun conflictingDuplicateFailsClosedWithoutChoosingArbitraryPoints() {
        val s=snapshot(listOf(tx(),tx(points=80)))
        assertTrue(FarmModel.project(s).effects.isEmpty());assertEquals(0,FarmModel.totals(s,FarmModel.range(FarmModel.Period.DAILY,now,zone)).earned)
    }
    @Test fun partialRewardGrowsLessAndIsCountedSeparately() {
        val full=FarmModel.project(snapshot(listOf(tx())))
        val partSnapshot=snapshot(listOf(tx(points=4,partial=true)))
        val part=FarmModel.project(partSnapshot)
        assertTrue(part.plots.sumOf {it.growth.toDouble()}<full.plots.sumOf {it.growth.toDouble()})
        assertEquals(1,FarmModel.totals(partSnapshot,FarmModel.range(FarmModel.Period.DAILY,now,zone)).partial)
        assertTrue(part.effects.first().message.startsWith("Partial reward"))
    }
    @Test fun missedRequiredSlotDriesWithoutDeduction() {
        val s=snapshot(slots=listOf(slot()))
        val p=FarmModel.project(s)
        assertTrue(p.plots.any {it.moisture<.55f});assertEquals(0,FarmModel.totals(s,FarmModel.range(FarmModel.Period.DAILY,now,zone)).deducted)
        assertEquals(0,p.effects.first().amount)
    }
    @Test fun optionalExcusedCancelledAndPendingSlotsNeverCauseDrought() {
        val slots=listOf(slot("optional",required=false),slot("excused",status="EXCUSED"),slot("cancelled",status="CANCELLED"),slot("pending",pending=true),slot("future",end=now+10000))
        assertTrue(FarmModel.project(snapshot(slots=slots)).effects.isEmpty())
        assertEquals(1,FarmModel.totals(snapshot(slots=slots),FarmModel.range(FarmModel.Period.DAILY,now,zone)).excused)
    }
    @Test fun droughtIsDeterministicAndGradualAcrossRestart() {
        val current=snapshot(slots=listOf(slot()))
        val later=current.copy(now=now+86400000)
        assertTrue(FarmModel.project(later).plots.sumOf {it.moisture.toDouble()}<FarmModel.project(current).plots.sumOf {it.moisture.toDouble()})
        assertEquals(FarmModel.project(later),FarmModel.project(later.copy()))
    }
    @Test fun confirmedPenaltyDamagesButUncertainOrUncommittedCannotStorm() {
        val confirmed=snapshot(listOf(tx(points=-1,type="RESTRICTED_APP_PENALTY",storm=true)))
        assertEquals(FarmModel.Weather.STORM,FarmModel.project(confirmed).condition)
        assertTrue(FarmModel.project(confirmed).plots.any {it.health<1f})
        assertTrue(FarmModel.project(snapshot(listOf(tx(points=-1,confirmed=false,storm=true)))).effects.isEmpty())
        assertTrue(FarmModel.project(snapshot(listOf(tx(points=-1,type="PROOF_UNCERTAIN",storm=false)))).effects.isEmpty())
    }
    @Test fun oldEventsHydrateWithoutOneShotSoundsAndRepeatedIdsDoNotReplay() {
        val events=FarmModel.project(snapshot(listOf(tx()))).effects
        assertTrue(FarmModel.freshEffects(events,emptySet(),false,now).isEmpty())
        assertEquals(1,FarmModel.freshEffects(events,emptySet(),true,now).size)
        assertTrue(FarmModel.freshEffects(events,setOf("reward-1"),true,now).isEmpty())
        assertTrue(FarmModel.freshEffects(events,emptySet(),true,now+300000).isEmpty())
    }
    @Test fun noFutureOrInvalidTimestampEventCanChangeFarm() {
        assertTrue(FarmModel.project(snapshot(listOf(tx(at=now+1),tx("bad",at=Long.MIN_VALUE)))).effects.isEmpty())
    }
    @Test fun reversalRemovesOriginalGrowthButPreservesBothEconomicRows() {
        val s=snapshot(listOf(tx(),tx("reverse",-8,type="REVERSAL",reverses="reward-1",proof=false)))
        assertTrue(FarmModel.project(s).effects.isEmpty())
        val total=FarmModel.totals(s,FarmModel.range(FarmModel.Period.DAILY,now,zone))
        assertEquals(0,total.net);assertEquals(-8,total.adjustments)
    }
    @Test fun resetsAreExplicitAndDoNotBecomeRewardRain() {
        val s=snapshot(listOf(tx(),tx("reset",-8,type="DAILY_RESET",proof=false)))
        val total=FarmModel.totals(s,FarmModel.range(FarmModel.Period.DAILY,now,zone))
        assertEquals(8,total.net);assertEquals(-8,total.resets);assertEquals(0,total.ledgerChange)
        assertEquals(1,FarmModel.project(s).effects.size)
    }
    @Test fun knownActivitiesMapToFarmerActionsAndUnknownStaysNeutral() {
        assertEquals(FarmModel.Action.WATERING,FarmModel.action("BATH"))
        assertEquals(FarmModel.Action.APPLYING_NUTRIENTS,FarmModel.action("CODING"))
        assertEquals(FarmModel.Action.CELEBRATING,FarmModel.action("GYM"))
        assertEquals(FarmModel.Action.INSPECTING_CROPS,FarmModel.action("unknown"))
    }
    @Test fun repeatedMissedSlotCannotDryTwice() {
        val one=slot();assertEquals(FarmModel.project(snapshot(slots=listOf(one))),FarmModel.project(snapshot(slots=listOf(one,one))))
    }
    @Test fun stormIsRecoverableAndNightIsNeverDamage() {
        val s=snapshot(listOf(tx(points=-5,storm=true,type="FAKE_PROOF_PENALTY")))
        val p=FarmModel.project(s)
        assertTrue(p.plots.all {it.health>=.15f})
        val night=snapshot().copy(now=time("2026-10-22 23:45:00"))
        assertTrue(FarmModel.project(night).plots.all {it.health==1f})
    }
}
