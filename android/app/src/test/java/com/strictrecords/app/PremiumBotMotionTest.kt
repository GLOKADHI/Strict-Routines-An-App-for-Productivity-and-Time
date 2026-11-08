package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.*

class PremiumBotMotionTest {
    private fun running(distance:Float=110f)=PremiumBotMotion().apply{playEntrance(distance)}
    @Test fun entranceVisitsEveryPhaseAndCompletesMoreThanThreeCycles(){
        val m=running();val states=mutableSetOf<PremiumBotMotion.BotAnimState>()
        repeat(1800){m.update(1f/120f);states+=m.state}
        assertEquals(PremiumBotMotion.BotAnimState.IDLE,m.state)
        assertEquals(11,PremiumBotMotion.BotAnimState.values().size)
        for(s in listOf("ENTERING","WALKING","SLOWING_DOWN","STOPPING","TURNING","STANDING","GESTURING","IDLE"))
            assertTrue("Missing $s",states.any{it.name==s})
        assertTrue(m.completedCycles>=3);assertEquals(0f,m.x,.0001f)
    }
    @Test fun bothLegsLiftAndAtLeastOneFootSupportsEveryFrame(){
        val m=running();var left=false;var right=false
        repeat(1300){
            m.update(1f/120f);val a=m.leg(0);val b=m.leg(1)
            left=left||a.sole.y < -3f;right=right||b.sole.y < -3f
            assertTrue("Both feet airborne in ${m.state}",a.planted || b.planted)
            assertTrue(a.sole.y<=.001f && b.sole.y<=.001f)
        }
        assertTrue(left && right)
    }
    @Test fun allPlantedFeetRemainLockedIncludingBrakingAndTurn(){
        val m=running();val old=arrayOfNulls<PremiumBotMotion.Leg>(2);var contacts=0
        repeat(1500){
            m.update(1f/120f)
            for(i in 0..1){
                val l=m.leg(i);val p=old[i]
                if(l.planted && p?.planted==true){
                    assertEquals("Sliding foot during ${m.state}",p.sole.x,l.sole.x,.001f)
                    assertEquals(0f,l.sole.y,.001f);contacts++
                }
                old[i]=l
            }
        }
        assertTrue(contacts>1000)
    }
    @Test fun bothLegSegmentsKeepTheirLengthsThroughoutTheActualSequence(){
        val m=running()
        repeat(1600){
            m.update(1f/120f)
            for(i in 0..1){
                val l=m.leg3D(i)
                assertEquals("Thigh in ${m.state}",13f,(l.knee-l.hip).length(),.004f)
                assertEquals("Shin in ${m.state}",13f,(l.ankle-l.knee).length(),.004f)
                assertTrue("Unreachable ankle",(l.ankle-l.hip).length()<26f)
            }
        }
    }
    @Test fun stoppingAndTurnHaveNoPoseDiscontinuity(){
        val m=running();var old=Array(2){m.leg(it)}
        repeat(1500){
            m.update(1f/120f)
            for(i in 0..1){
                val l=m.leg(i);val p=old[i]
                assertTrue("Ankle snapped in ${m.state}",hypot(l.ankle.x-p.ankle.x,l.ankle.y-p.ankle.y)<1.4f)
                assertTrue("Knee snapped in ${m.state}",hypot(l.knee.x-p.knee.x,l.knee.y-p.knee.y)<1.4f)
            }
            old=Array(2){m.leg(it)}
        }
    }
    @Test fun rootBrakesWithoutOvershootingAndGaitTracksActualTravel(){
        val m=running();var last=m.x;var braking=false;var lastBrake=100f
        repeat(1000){
            m.update(1f/120f)
            assertTrue(m.x>=last-.001f && m.x<=.001f)
            if(m.state==PremiumBotMotion.BotAnimState.SLOWING_DOWN){
                assertTrue(abs(m.speed)<=lastBrake+.01f);lastBrake=abs(m.speed);braking=true
            }
            assertEquals((m.distanceTravelled/PremiumBotMotion.STRIDE)%1f,m.phase,.0001f)
            last=m.x
        }
        assertTrue(braking);assertEquals(0f,m.speed,.0001f)
    }
    @Test fun frameRateDoesNotChangeTheMotionOrState(){
        val a=running();val b=running();val c=running()
        repeat(480){a.update(1f/60f)}
        repeat(240){b.update(1f/30f)}
        repeat(120){c.update(1f/15f)}
        assertEquals(a.state,b.state);assertEquals(a.state,c.state)
        assertEquals(a.x,b.x,.01f);assertEquals(a.phase,b.phase,.001f)
        assertEquals(a.headYaw,c.headYaw,.02f);assertEquals(a.gestureProgress,c.gestureProgress,.01f)
    }
    @Test fun hipsTorsoAndHeadTurnInOrderOnlyAfterTheFeetSettle(){
        val m=running();var separated=false
        repeat(1200){
            m.update(1f/120f)
            if(m.state==PremiumBotMotion.BotAnimState.TURNING){
                assertEquals(0f,m.speed,.0001f)
                assertTrue(m.pelvisYaw<=m.headYaw+.001f)
                if(m.headYaw-m.pelvisYaw>3f)separated=true
            }
        }
        assertTrue(separated)
        assertEquals(0f,m.pelvisYaw,.001f);assertEquals(0f,m.torsoYaw,.001f);assertEquals(0f,m.headYaw,.001f)
    }
    @Test fun standingLegsStayStillWhileTheHeadHandsAndCapeRemainAlive(){
        val m=running();repeat(1200){m.update(1f/120f)}
        val soles=Array(2){m.leg(it).sole};val phase=m.phase
        val tilt=m.headTilt;val hand=m.arm(1).indexCurl;val bob=m.bodyBob
        repeat(95){m.update(1f/120f)}
        for(i in 0..1){assertEquals(soles[i],m.leg(i).sole);assertTrue(m.leg(i).planted)}
        assertEquals(phase,m.phase,0f);assertTrue(abs(tilt-m.headTilt)>.01f || abs(bob-m.bodyBob)>.01f)
        assertTrue(m.arm(1).indexCurl>=0f);assertTrue(hand.isFinite() && m.capeLag.isFinite())
    }
    @Test fun speakingRaisesAndRelaxesTheHandWithoutRestartingTheWalk(){
        val m=running();repeat(1200){m.update(1f/120f)}
        val root=m.x;val phase=m.phase;val low=m.arm(1).wrist.y
        m.setSpeaking(true);repeat(180){m.update(1f/120f)}
        assertEquals(PremiumBotMotion.BotAnimState.SPEAKING,m.state)
        assertTrue(m.arm(1).wrist.y<low-15f);assertTrue(m.arm(1).indexCurl<2f)
        assertTrue(m.arm(1).fingerCurl>70f)
        m.setSpeaking(false);repeat(180){m.update(1f/120f)}
        assertEquals(PremiumBotMotion.BotAnimState.IDLE,m.state);assertTrue(m.gestureProgress<.01f)
        assertEquals(root,m.x,0f);assertEquals(phase,m.phase,0f)
    }
    @Test fun repeatedObservationsCannotRestartAnEntrance(){
        val m=running();repeat(90){m.update(1f/120f)}
        val root=m.x;val phase=m.phase
        m.playEntrance(200f)
        assertEquals(root,m.x,0f);assertEquals(phase,m.phase,0f)
        m.playEntrance(110f,restart=true);assertEquals(-110f,m.x,0f)
    }
    @Test fun hiddenAnimationDoesNotAdvanceAndWarningDoesNotFreezeTheGait(){
        val m=running();m.warning=true;repeat(120){m.update(1f/120f)}
        assertTrue(m.phase>0f);assertTrue(m.isWalking)
        m.setState(PremiumBotMotion.BotAnimState.HIDDEN);val t=m.time;val x=m.x
        repeat(100){m.update(.033f)}
        assertEquals(t,m.time,0f);assertEquals(x,m.x,0f)
    }

    @Test fun supportLegReceivesWeightWhileTheOtherFootPivots(){
        val m=running();var shifted=false
        repeat(1300){
            m.update(1f/120f)
            if(m.state==PremiumBotMotion.BotAnimState.TURNING && abs(m.weightShift)>2f)
                shifted=true
        }
        assertTrue(shifted)
        assertTrue(abs(m.weightShift)<.01f)
    }
    @Test fun exitTurnsAndStepsAwayBeforeBecomingHidden(){
        val m=running();repeat(1300){m.update(1f/120f)}
        m.setState(PremiumBotMotion.BotAnimState.EXITING)
        var stepped=false
        repeat(1200){
            m.update(1f/120f)
            if(m.state==PremiumBotMotion.BotAnimState.EXITING){
                stepped=stepped||m.speed<-.1f
                assertTrue(m.leg(0).planted||m.leg(1).planted)
                for(i in 0..1){
                    val l=m.leg3D(i)
                    assertEquals(13f,(l.knee-l.hip).length(),.005f)
                    assertEquals(13f,(l.ankle-l.knee).length(),.005f)
                }
            }
        }
        assertTrue(stepped);assertEquals(PremiumBotMotion.BotAnimState.HIDDEN,m.state)
    }

    @Test fun walkingArmsCounterSwingAndHandsFollowTheirForearms(){
        val m=running(160f);val left=mutableListOf<Float>();val right=mutableListOf<Float>()
        repeat(480){
            m.update(1f/120f)
            if(m.state==PremiumBotMotion.BotAnimState.WALKING && m.speed>23f){
                val a=m.arm(0);val b=m.arm(1)
                left+=(a.elbow.x-a.shoulder.x);right+=(b.elbow.x-b.shoulder.x)
                assertTrue(a.handAngle.isFinite() && b.handAngle.isFinite())
            }
        }
        assertTrue(left.size>120)
        assertTrue(left.maxOrNull()!!-left.minOrNull()!!>8f)
        assertTrue(right.maxOrNull()!!-right.minOrNull()!!>8f)
        val la=left.average();val ra=right.average()
        val covariance=left.indices.sumOf{(left[it]-la)*(right[it]-ra)}/left.size
        assertTrue("Arms must counter-swing rather than move as one image",covariance < -5.0)
    }
    @Test fun pointingHandAndFingersBlendWithoutPoseThresholds(){
        val m=running();repeat(1200){m.update(1f/120f)}
        var old=m.arm(1);m.setSpeaking(true)
        repeat(250){
            m.update(1f/120f);val a=m.arm(1)
            assertTrue(hypot(a.wrist.x-old.wrist.x,a.wrist.y-old.wrist.y)<1.2f)
            assertTrue(abs(a.indexCurl-old.indexCurl)<3f)
            assertTrue(abs(a.fingerCurl-old.fingerCurl)<4f)
            old=a
        }
        assertTrue(m.arm(1).indexCurl<1f && m.arm(1).fingerCurl>80f)
    }
}
