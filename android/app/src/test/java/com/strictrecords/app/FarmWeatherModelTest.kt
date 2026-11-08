package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

class FarmWeatherModelTest {
    private val zone="Asia/Kolkata"
    private val instant=SimpleDateFormat("yyyy-MM-dd HH:mm:ss",Locale.US).apply {timeZone=TimeZone.getTimeZone(zone)}.parse("2026-10-22 12:00:00")!!.time
    private fun env(score:Int,date:String="2026-10-22")=FarmWeatherModel.Environment(score,date,FarmWeatherModel.resolve(score),List(6){FarmModel.Plot(.8f)})
    private fun delta(id:String="deduction",points:Int=-1)=FarmModel.Effect(id,instant,FarmModel.Weather.STORM,FarmModel.Action.STORM_RESPONSE,"Fixture delta",points)
    private fun row(id:String,points:Int,at:Long=instant-1000,confirmed:Boolean=true,date:String="2026-10-22",type:String=if(points>0)"PROOF_APPROVED" else "GYM_SKIP")=
        FarmModel.Transaction(id,at,date,type,"Synthetic fixture",points,confirmed,"Android APK",proofReward=points>0)
    private fun snap(score:Int,events:List<FarmModel.Transaction> = emptyList())=FarmModel.Snapshot("fixture",events,emptyList(),score,instant,zone,"Fixture only")
    private fun project(s:FarmModel.Snapshot)=FarmWeatherModel.environment(s,FarmModel.project(s))
    private fun controller(score:Int)=FarmWeatherModel.Controller().apply {accept(env(score),emptyList(),0)}

    @Test fun minusTenIsCloudyAndNotCatastrophic() {
        val w=FarmWeatherModel.resolve(-10)
        assertTrue(w.clouds>.8f);assertTrue(w.damage<.4f)
        assertTrue(w.damage<FarmWeatherModel.resolve(FarmWeatherModel.Policy.DISASTER_SCORE).damage)
    }
    @Test fun severityIsContinuousAndMonotonicAcrossEveryScoreStep() {
        for(score in -40 until 100) {
            val a=FarmWeatherModel.resolve(score);val b=FarmWeatherModel.resolve(score+1)
            assertTrue(b.clouds<=a.clouds);assertTrue(b.darkness<=a.darkness);assertTrue(b.wind<=a.wind)
            assertTrue(b.rain<=a.rain);assertTrue(b.damage<=a.damage);assertTrue(b.light>=a.light)
            assertTrue(kotlin.math.abs(a.clouds-b.clouds)<.07f)
        }
    }
    @Test fun minusTenToMinusNineIsASubtleCloudRecovery() {
        val a=FarmWeatherModel.resolve(-10);val b=FarmWeatherModel.resolve(-9)
        assertTrue(b.clouds<a.clouds);assertTrue(a.clouds-b.clouds<.05f)
        assertTrue(b.darkness<a.darkness);assertTrue(b.wind<a.wind)
    }
    @Test fun minusNineToMinusFiveToMinusOneProgressivelyRevealsLight() {
        val stages=listOf(-9,-5,-1).map {FarmWeatherModel.resolve(it)}
        assertTrue(stages.zipWithNext().all {(a,b)->b.clouds<a.clouds && b.rain<a.rain && b.light>a.light})
    }
    @Test fun zeroAndOneArePartlySunnyRatherThanPerfectlyClear() {
        for(score in listOf(0,1)) {val w=FarmWeatherModel.resolve(score);assertTrue(w.clouds>.3f);assertTrue(w.light in .5f.. .8f);assertEquals(0f,w.lightning,0f)}
        assertTrue(FarmWeatherModel.resolve(20).light>FarmWeatherModel.resolve(1).light)
    }
    @Test fun extremesAreBoundedWithoutIntegerOverflow() {
        for(score in listOf(Int.MIN_VALUE,-100,-20,0,100,Int.MAX_VALUE))assertTrue(FarmWeatherModel.resolve(score).values().all {it in 0f..1f})
    }
    @Test fun deductionFromPositiveScoreSequencesCloudsFlashThenHeavyRain() {
        val c=controller(80)
        val cue=c.accept(env(79),listOf(delta()),1000)!!
        assertEquals(FarmWeatherModel.Stage.GATHERING,c.frame(2000).stage)
        val flashTime=1000+FarmWeatherModel.Policy.GATHER_MS
        assertEquals(FarmWeatherModel.Stage.CHARGING,c.frame(flashTime).stage)
        assertTrue(c.frame(flashTime).flash>0)
        assertEquals(FarmWeatherModel.Stage.HEAVY_RAIN,c.frame(6000).stage)
        assertTrue(c.frame(7500).weather.rain>.6f)
        assertTrue(cue.thunder);assertEquals(FarmWeatherModel.Policy.THUNDER_DELAY_MS,cue.delayMs-FarmWeatherModel.Policy.GATHER_MS)
    }
    @Test fun smallDeductionSettlesBackToPositiveScoreWeather() {
        val c=controller(80);c.accept(env(79),listOf(delta()),1000)
        assertEquals(FarmWeatherModel.resolve(79),c.frame(30000).weather)
        assertEquals(FarmWeatherModel.Stage.STABLE,c.frame(30000).stage)
    }
    @Test fun deepNegativeScoreSustainsDisasterAfterTransientEnds() {
        val c=controller(10);c.accept(env(-25),listOf(delta(points=-35)),1000)
        assertTrue(c.frame(30000).weather.damage>=.85f)
        assertEquals(FarmModel.Action.STORM_RESPONSE,c.frame(30000).action)
        assertEquals(FarmWeatherModel.resolve(-25),c.frame(30000).weather)
    }
    @Test fun positiveRewardImprovesDeepStormButDoesNotEraseIt() {
        val c=controller(-25);val before=c.frame(1000).weather
        c.accept(env(-24),listOf(delta("reward",1)),1000)
        assertEquals(before,c.frame(1000).weather)
        val after=c.frame(10000).weather
        assertTrue(after.clouds<before.clouds);assertTrue(after.damage>=.85f)
        assertTrue(after.rain>.9f)
    }
    @Test fun zeroCrossingInterpolatesWithoutDiscontinuousFlashing() {
        val c=controller(-1);c.accept(env(0),listOf(delta("zero",1)),1000)
        val middle=c.frame(5000)
        assertTrue(middle.weather.clouds>FarmWeatherModel.resolve(0).clouds)
        assertEquals(0f,middle.flash,0f)
        assertEquals(FarmWeatherModel.resolve(0),c.frame(10000).weather)
    }
    @Test fun interruptionReversesFromCurrentVisualStateWithoutJump() {
        val c=controller(-10);c.accept(env(-5),listOf(delta("reward",5)),1000)
        val current=c.frame(4000).weather
        c.accept(env(-7),listOf(delta("new-deduction",-2)),4000)
        assertEquals(current,c.frame(4000).weather)
        assertTrue(c.frame(6000).weather.darkness>current.darkness)
    }
    @Test fun rapidRewardsRetargetRecoveryFromCurrentFrame() {
        val c=controller(-10)
        c.accept(env(-9),listOf(delta("r1",1)),1000)
        val prior=c.frame(3000).weather
        c.accept(env(-8),listOf(delta("r2",1)),3000)
        assertEquals(prior,c.frame(3000).weather)
        assertEquals(FarmWeatherModel.resolve(-8),c.frame(12000).weather)
    }
    @Test fun repeatedTransactionAndClockRefreshCannotRestartTransition() {
        val c=controller(20);val e=delta()
        c.accept(env(19),listOf(e),1000)
        val count=c.transitions
        assertNull(c.accept(env(19),listOf(e,e),2000));assertNull(c.accept(env(19),emptyList(),3000))
        assertEquals(count,c.transitions);assertEquals(FarmWeatherModel.Stage.CHARGING,c.frame(4000).stage)
    }
    @Test fun rapidDeductionsUseOneTimelineAndOneThunderCue() {
        val c=controller(0)
        assertNotNull(c.accept(env(-1),listOf(delta("d1")),1000))
        assertNull(c.accept(env(-2),listOf(delta("d2")),1500))
        assertNull(c.accept(env(-20),listOf(delta("d3",-18)),2000))
        assertEquals(1,c.transitions);assertEquals(FarmWeatherModel.resolve(-20),c.frame(30000).weather)
    }
    @Test fun firstHydrationAndNewProcessRestoreWithoutHistoricThunder() {
        val c=FarmWeatherModel.Controller()
        assertNull(c.accept(env(-20),listOf(delta(points=-20)),1000))
        assertEquals(FarmWeatherModel.resolve(-20),c.frame(1000).weather)
        assertEquals(0f,c.frame(1000).flash,0f);assertEquals(0,c.transitions)
        assertNull(FarmWeatherModel.Controller().accept(env(-10),emptyList(),2000))
    }
    @Test fun navigationUsesProcessSessionWhileAccountsAndDemoStaySeparate() {
        val session=FarmWeatherModel.Session()
        assertSame(session.controller("user:prod"),session.controller("user:prod"))
        assertNotSame(session.controller("user:prod"),session.controller("user:dev"))
        assertNotSame(session.controller("user:prod"),session.controller("other:prod"))
        assertNotSame(session.controller("user:prod"),FarmWeatherModel.Session().controller("user:prod"))
    }
    @Test fun dailyResetChangesStableWeatherWithoutPretendingRewardOrPenalty() {
        val c=controller(-20)
        assertNull(c.accept(env(0,"2026-10-23"),emptyList(),1000))
        assertEquals(FarmWeatherModel.resolve(0),c.frame(1000).weather);assertEquals(0,c.transitions)
    }
    @Test fun pendingFailedFutureConflictingAndOldDayRecordsCannotTriggerWeather() {
        val rows=listOf(row("pending",8,confirmed=false),row("future",-1,at=instant+1),row("yesterday",-1,date="2026-10-21"),row("reset",-10,type="INITIAL_RESET"),row("conflict",-1),row("conflict",-2))
        assertTrue(FarmWeatherModel.changes(snap(0,rows)).isEmpty())
    }
    @Test fun ordinaryCommittedDeductionTriggersWithoutClaimingFraud() {
        val changes=FarmWeatherModel.changes(snap(40,listOf(row("gym-skip",-10))))
        assertEquals(1,changes.size);assertEquals(-10,changes.single().amount)
        assertFalse(changes.single().message.contains("fake",ignoreCase=true))
    }
    @Test fun mixedBurstRetainsTheDeductionSequenceAndItsTrueFinalBalance() {
        val c=controller(-10)
        assertNotNull(c.accept(env(-5),listOf(delta("reward",8),delta("deduction",-3)),1000))
        assertEquals(1,c.transitions)
        assertEquals(FarmWeatherModel.Stage.GATHERING,c.frame(2000).stage)
        assertEquals(FarmWeatherModel.resolve(-5),c.frame(30000).weather)
    }
    @Test fun scoreCeilingAndGenuineDeltaStayDistinct() {
        val c=controller(100)
        assertNotNull(c.accept(env(100),listOf(delta(points=-1)),1000))
        assertEquals(FarmWeatherModel.resolve(100),c.frame(30000).weather)
    }
    @Test fun negativeScoreDamagesAllPlotsButMinusTenDoesNotDevastateAll() {
        val base=FarmModel.Projection(List(6){FarmModel.Plot(.8f)},emptyList(),FarmModel.Weather.CALM,48)
        val severe=FarmWeatherModel.environment(snap(-20),base)
        val moderate=FarmWeatherModel.environment(snap(-10),base)
        assertTrue(severe.plots.all {it.health<.3f && it.growth<.4f})
        assertTrue(moderate.plots.all {it.health>.6f && it.growth>.5f})
    }
    @Test fun earnedRecoveryRepairsGraduallyAndZeroDoesNotInstantlyRestoreAllCrops() {
        val damage=row("damage",-20,instant-3000)
        val severe=project(snap(-20,listOf(damage)))
        val small=project(snap(-19,listOf(damage,row("reward",1,instant-2000))))
        val zero=project(snap(0,listOf(damage,row("reward",20,instant-2000))))
        assertTrue(small.plots.first().health>severe.plots.first().health)
        assertTrue(zero.plots.all {it.health<1f})
        assertTrue(zero.plots.first().health>small.plots.first().health)
    }
    @Test fun enoughRealPositiveProgressRestoresViableFarm() {
        val damage=row("damage",-20,instant-3000)
        val recovered=project(snap(20,listOf(damage,row("recovery",40,instant-2000))))
        assertTrue(recovered.plots.all {it.health==1f})
        assertEquals(FarmWeatherModel.resolve(20),recovered.target)
    }
    @Test fun clockTimeAloneCannotHealAndDuplicateEventsCannotRepairTwice() {
        val s=snap(-10,listOf(row("damage",-20,instant-3000),row("reward",10,instant-2000)))
        val a=project(s);val b=project(s.copy(now=instant+3600000))
        assertEquals(a.plots,b.plots)
        assertEquals(a,project(s.copy(events=s.events+s.events)))
    }
    @Test fun latestAuthoritativeBalanceWinsOverVisualHistoryReplay() {
        val s=snap(-25,listOf(row("old-reward",80)))
        assertEquals(FarmWeatherModel.resolve(-25),project(s).target)
        assertEquals(-25,project(s).score)
    }
    @Test fun weatherNeverMutatesOriginalScoreOrRows() {
        val s=snap(-5,listOf(row("deduction",-10),row("reward",5)))
        val original=s.copy(events=s.events.toList())
        repeat(10) {val e=project(s);val c=controller(0);c.accept(e,FarmWeatherModel.changes(s),1000);c.frame(5000)}
        assertEquals(original,s)
    }
    @Test fun reducedMotionAndSoundOffKeepWeatherButSuppressAudioAndFlash() {
        val c=controller(0)
        assertNull(c.accept(env(-20),listOf(delta(points=-20)),1000,true))
        val frame=c.frame(3500,true)
        assertEquals(FarmWeatherModel.resolve(-20),frame.weather);assertEquals(0f,frame.flash,0f)
        assertFalse(FarmWeatherModel.soundAllowed(false,true,true,true,false))
        assertFalse(FarmWeatherModel.soundAllowed(true,false,true,true,false))
        assertFalse(FarmWeatherModel.soundAllowed(true,true,false,true,false))
        assertFalse(FarmWeatherModel.soundAllowed(true,true,true,false,false))
        assertFalse(FarmWeatherModel.soundAllowed(true,true,true,true,true))
        assertTrue(FarmWeatherModel.soundAllowed(true,true,true,true,false))
    }
    @Test fun backgroundSilenceSuppressesPendingFlash() {
        val c=controller(20);c.accept(env(19),listOf(delta()),1000);c.silence()
        assertEquals(0f,c.frame(3500).flash,0f)
    }
    @Test fun flashHasShortFalloffAndNoRapidStrobeLoop() {
        val c=controller(20);c.accept(env(19),listOf(delta()),1000)
        assertEquals(1f,c.frame(3500).flash,.0001f)
        assertTrue(c.frame(3590).flash in .4f.. .6f)
        assertEquals(0f,c.frame(3700).flash,0f)
        assertEquals(0f,c.frame(4000).flash,0f)
    }
    @Test fun changingLightningFrequencyAfterLongUptimeCannotAliasIntoStrobing() {
        val c=controller(-20)
        val start=1000000L
        c.accept(env(-30),listOf(delta(points=-10)),start)
        var beginnings=0;var lit=false
        for(t in start until start+13800L step 20L) {
            val next=c.frame(t).flash>0f
            if(next && !lit)beginnings++
            lit=next
        }
        assertEquals(1,beginnings)
    }
    @Test fun timeOfDayRemainsIndependentOfScoreWeather() {
        for((hour,phase) in listOf(5 to FarmModel.Light.SUNRISE,9 to FarmModel.Light.MORNING,14 to FarmModel.Light.AFTERNOON,18 to FarmModel.Light.SUNSET,22 to FarmModel.Light.NIGHT)) {
            val time=instant+(hour-12)*3600000L
            val before=FarmModel.lighting(time,zone)
            val c=controller(-20);c.accept(env(-10),listOf(delta("reward",10)),1000);c.frame(5000)
            assertEquals(phase,before.phase);assertEquals(before,FarmModel.lighting(time,zone))
        }
    }
}
