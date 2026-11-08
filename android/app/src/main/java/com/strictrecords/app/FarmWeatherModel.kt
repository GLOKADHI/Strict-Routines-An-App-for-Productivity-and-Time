package com.strictrecords.app

import kotlin.math.max

/** Visual policy only. The signed daily balance is supplied by getScore(), never written here. */
object FarmWeatherModel {
    data class Conditions(
        val clouds: Float, val opacity: Float, val darkness: Float, val light: Float,
        val wind: Float, val rain: Float, val lightning: Float, val sound: Float,
        val damage: Float, val recovery: Float
    ) {
        fun values() = listOf(clouds, opacity, darkness, light, wind, rain, lightning, sound, damage, recovery)
    }
    data class Knot(val score: Int, val weather: Conditions)
    object Policy {
        // Existing daily discipline scale: ceiling 100, no negative floor; penalties commonly 1 or 10.
        const val DISASTER_SCORE = -20
        const val GATHER_MS = 2500L
        const val CHARGE_MS = 1800L
        const val HEAVY_MS = 3000L
        const val SETTLE_MS = 6500L
        const val RECOVERY_MS = 8000L
        const val FLASH_MS = 180L
        const val THUNDER_DELAY_MS = 650L
        const val REPAIR_PER_POINT = .025f
        val knots = listOf(
            Knot(-40, Conditions(1f, 1f, .91f, .03f, 1f, 1f, 1f, 1f, 1f, 0f)),
            Knot(-20, Conditions(.98f, .98f, .82f, .07f, .9f, .94f, .8f, .9f, .85f, .05f)),
            Knot(-15, Conditions(.94f, .94f, .72f, .12f, .73f, .78f, .5f, .7f, .55f, .12f)),
            Knot(-10, Conditions(.87f, .88f, .58f, .2f, .5f, .53f, .2f, .48f, .28f, .22f)),
            Knot(-7, Conditions(.79f, .79f, .47f, .29f, .39f, .4f, .08f, .35f, .17f, .3f)),
            Knot(-5, Conditions(.7f, .7f, .36f, .4f, .3f, .28f, 0f, .22f, .1f, .4f)),
            Knot(-3, Conditions(.61f, .61f, .27f, .49f, .22f, .16f, 0f, .12f, .05f, .5f)),
            Knot(-1, Conditions(.52f, .52f, .19f, .59f, .15f, .07f, 0f, .04f, .02f, .6f)),
            Knot(0, Conditions(.46f, .46f, .14f, .65f, .12f, .03f, 0f, .02f, 0f, .65f)),
            Knot(1, Conditions(.4f, .4f, .11f, .7f, .1f, .01f, 0f, 0f, 0f, .7f)),
            Knot(10, Conditions(.23f, .27f, .04f, .87f, .08f, 0f, 0f, 0f, 0f, .88f)),
            Knot(20, Conditions(.12f, .18f, 0f, 1f, .06f, 0f, 0f, 0f, 0f, 1f))
        )
    }
    data class Environment(val score: Int, val date: String, val target: Conditions, val plots: List<FarmModel.Plot>)
    enum class Stage { STABLE, GATHERING, CHARGING, HEAVY_RAIN, SETTLING, RECOVERING }
    data class Frame(val weather: Conditions, val stage: Stage, val flash: Float, val action: FarmModel.Action)
    data class SoundCue(val thunder: Boolean, val delayMs: Long, val strength: Float)
    /** Retains no Views, Contexts, timers or audio; navigation can continue from the last visual state. */
    class Session {
        private val controllers=mutableMapOf<String,Controller>()
        fun controller(scope:String)=controllers.getOrPut(scope){Controller()}
    }

    private fun lerp(a: Float, b: Float, t: Float) = a + (b - a) * t
    fun blend(a: Conditions, b: Conditions, fraction: Float): Conditions {
        val t = fraction.coerceIn(0f, 1f)
        return Conditions(lerp(a.clouds,b.clouds,t),lerp(a.opacity,b.opacity,t),
            lerp(a.darkness,b.darkness,t),lerp(a.light,b.light,t),lerp(a.wind,b.wind,t),
            lerp(a.rain,b.rain,t),lerp(a.lightning,b.lightning,t),lerp(a.sound,b.sound,t),
            lerp(a.damage,b.damage,t),lerp(a.recovery,b.recovery,t))
    }
    fun resolve(score: Int): Conditions {
        val knots = Policy.knots
        if (score <= knots.first().score) return knots.first().weather
        if (score >= knots.last().score) return knots.last().weather
        val upper = knots.indexOfFirst { it.score >= score }
        val a = knots[upper-1]; val b = knots[upper]
        return blend(a.weather,b.weather,(score.toLong()-a.score).toFloat()/(b.score-a.score))
    }
    fun scoreDate(snapshot: FarmModel.Snapshot) = ScheduleClock.display(snapshot.now,snapshot.timezone,"yyyy-MM-dd")
    private val resets = setOf("INITIAL_RESET", "DAILY_RESET")

    /** Persisted deltas and the current signed daily score are deliberately different inputs. */
    fun changes(snapshot: FarmModel.Snapshot): List<FarmModel.Effect> = FarmModel.unique(snapshot.events)
        .filter { it.confirmed && it.points != 0 && it.type !in resets && it.date == scoreDate(snapshot) && it.time > 0 && it.time <= snapshot.now }
        .map { FarmModel.Effect(it.id,it.time,if(it.points<0)FarmModel.Weather.STORM else FarmModel.Weather.RAIN,
            if(it.points<0)FarmModel.Action.STORM_RESPONSE else FarmModel.action(it.category),
            if(it.points<0)"Confirmed deduction · ${it.points} pts" else "Confirmed increase · +${it.points} pts",it.points) }

    /** Replay immutable rows to derive repair debt; clock time alone cannot heal damaged land. */
    fun environment(snapshot: FarmModel.Snapshot, base: FarmModel.Projection): Environment {
        val rows=FarmModel.unique(snapshot.events).filter {it.confirmed && it.time>0 && it.time<=snapshot.now}
        val reversed=rows.filter {it.reverses.isNotBlank()}.map {it.reverses}.toSet()
        var debt=0f; var repair=0f; var raw=0L; var date=""
        rows.filter {it.id !in reversed && it.type!="REVERSAL"}.forEach { row ->
            if(row.date!=date || row.type in resets) {raw=0L;date=row.date}
            if(row.type !in resets) {
                raw+=row.points
                // The same ceiling/no-floor policy, used only to reconstruct historical visual damage.
                val historical=resolve(raw.coerceIn(Int.MIN_VALUE.toLong(),100L).toInt()).damage
                if(row.points<0) {debt=max(debt,historical);repair=0f}
                if(row.points>0) {val earned=row.points*Policy.REPAIR_PER_POINT;debt=(debt-earned).coerceAtLeast(0f);repair=(repair+earned).coerceAtMost(1f)}
            }
        }
        val target=resolve(snapshot.balance) // Always the existing authoritative value, not the replay's raw sum.
        val remaining=max(debt,target.damage)
        val plots=base.plots.map { it.copy(
            growth=(it.growth*(1f-.72f*remaining)).coerceIn(0f,1f),
            health=minOf((it.health+repair).coerceAtMost(1f),1f-.88f*remaining),
            moisture=(it.moisture+repair*.08f).coerceAtMost(1f)) }
        return Environment(snapshot.balance,scoreDate(snapshot),target,plots)
    }

    /** One interruptible timeline shared by particles, lighting, character and sound coordination. */
    class Controller {
        private data class Segment(val start: Long,val duration: Long,val from: Conditions,val to: Conditions,val stage: Stage)
        private var segments=emptyList<Segment>()
        private var target=resolve(0)
        private var date:String?=null
        private var score=0
        private var flashAt=Long.MIN_VALUE
        private var restoredAt=0L
        private val seen=mutableSetOf<String>()
        var transitions=0; private set
        private fun ease(t: Float)=t.coerceIn(0f,1f).let {it*it*(3f-2f*it)}
        fun accept(environment: Environment, events: List<FarmModel.Effect>, now: Long, reduced: Boolean=false): SoundCue? {
            if(date==null || date!=environment.date) {
                date=environment.date;score=environment.score;target=environment.target;segments=emptyList();flashAt=Long.MIN_VALUE;restoredAt=now
                seen.addAll(events.map {it.id});return null
            }
            val fresh=events.filter {it.id !in seen}.distinctBy {it.id}
            seen.addAll(fresh.map {it.id})
            val changed=environment.score!=score
            val current=frame(now,reduced).weather
            val oldScore=score;score=environment.score;target=environment.target
            if(fresh.isEmpty() && !changed)return null // Clock ticks and repeated sync do not restart the timeline.
            val negative=fresh.any {it.amount<0}
            val inStorm=frame(now,reduced).stage in setOf(Stage.GATHERING,Stage.CHARGING,Stage.HEAVY_RAIN)
            if(negative && inStorm) {
                // Coalesce bursts into the existing timeline and update its final target; no second thunder cue.
                segments=segments.mapIndexed {index,s->if(index==segments.lastIndex)s.copy(to=target) else s}
                return null
            }
            transitions++
            if(negative) {
                val severity=target
                val gather=blend(current,severity,.3f).copy(
                    clouds=max(current.clouds+.08f,max(.73f,severity.clouds)).coerceAtMost(1f),
                    opacity=max(current.opacity+.08f,max(.7f,severity.opacity)).coerceAtMost(1f),
                    darkness=max(current.darkness+.12f,max(.4f,severity.darkness)).coerceAtMost(1f),
                    light=minOf(current.light*.8f,.37f,severity.light),wind=max(.3f,severity.wind),lightning=0f)
                val charge=gather.copy(darkness=max(.65f,severity.darkness),wind=max(.55f,severity.wind),rain=max(.25f,severity.rain),lightning=max(.35f,severity.lightning),sound=max(.4f,severity.sound))
                val heavy=charge.copy(rain=max(.78f,severity.rain),wind=max(.68f,severity.wind),damage=severity.damage)
                var start=now
                segments=listOf(
                    Segment(start,Policy.GATHER_MS,current,gather,Stage.GATHERING).also {start+=it.duration},
                    Segment(start,Policy.CHARGE_MS,gather,charge,Stage.CHARGING).also {start+=it.duration},
                    Segment(start,Policy.HEAVY_MS,charge,heavy,Stage.HEAVY_RAIN).also {start+=it.duration},
                    Segment(start,Policy.SETTLE_MS,heavy,target,Stage.SETTLING))
                flashAt=now+Policy.GATHER_MS
                return if(reduced)null else SoundCue(true,Policy.GATHER_MS+Policy.THUNDER_DELAY_MS,max(.4f,target.sound))
            }
            flashAt=Long.MIN_VALUE
            val recovering=environment.score>oldScore
            segments=listOf(Segment(now,Policy.RECOVERY_MS,current,target,if(recovering)Stage.RECOVERING else Stage.SETTLING))
            return if(reduced || fresh.isEmpty())null else SoundCue(false,0L,.3f)
        }
        fun frame(now: Long, reduced: Boolean=false): Frame {
            val segment=segments.firstOrNull {now<it.start+it.duration} ?: segments.lastOrNull()
            val active=segment!=null && now<segment.start+segment.duration
            val weather=if(segment==null || !active || reduced)target else blend(segment.from,segment.to,ease((now-segment.start).toFloat()/segment.duration))
            val stage=if(active && !reduced)segment!!.stage else Stage.STABLE
            val eventFlash=if(flashAt==Long.MIN_VALUE)0f else (1f-(now-flashAt).toFloat()/Policy.FLASH_MS).takeIf {now>=flashAt}?.coerceIn(0f,1f)?:0f
            val period=(24000L-12000L*weather.lightning).toLong()
            val phase=(now-restoredAt).coerceAtLeast(0)%period
            // A varying interval during a tween can alias into repeated flashes. Only the fixed,
            // stable target gets recurring lightning; transitions have one explicitly timed flash.
            val ambientFlash=if(stage==Stage.STABLE && weather.lightning>0f && now-restoredAt>=period && phase<Policy.FLASH_MS) (1f-phase.toFloat()/Policy.FLASH_MS)*weather.lightning*.55f else 0f
            val action=when {
                stage==Stage.GATHERING || stage==Stage.CHARGING -> FarmModel.Action.INSPECTING_CROPS
                weather.damage>=resolve(Policy.DISASTER_SCORE).damage -> FarmModel.Action.STORM_RESPONSE
                stage==Stage.RECOVERING -> FarmModel.Action.RECOVERING
                weather.damage>.1f -> FarmModel.Action.INSPECTING_CROPS
                else -> FarmModel.Action.IDLE
            }
            return Frame(weather,stage,if(reduced)0f else max(eventFlash,ambientFlash),action)
        }
        fun isTransitioning(now:Long)=segments.lastOrNull()?.let {now<it.start+it.duration}==true
        fun silence() { flashAt=Long.MIN_VALUE }
    }

    fun soundAllowed(enabled:Boolean,foreground:Boolean,normalRinger:Boolean,mediaAudible:Boolean,reduced:Boolean)=
        enabled && foreground && normalRinger && mediaAudible && !reduced
}
