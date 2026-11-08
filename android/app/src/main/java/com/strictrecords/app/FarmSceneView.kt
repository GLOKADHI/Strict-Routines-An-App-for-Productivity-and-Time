package com.strictrecords.app

import android.content.Context
import android.graphics.*
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.view.View
import kotlin.math.*

/** Composable vector landscape: terrain, plants, farmer and weather move independently. */
class FarmSceneView(context: Context) : View(context) {
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    private val path = Path()
    private val ui = Handler(Looper.getMainLooper())
    private val bounds = Rect()
    private var active = false
    private var elapsedAtLoad = 0L
    private var snapshot: FarmModel.Snapshot? = null
    private var projection = FarmModel.Projection(List(6) { FarmModel.Plot() }, emptyList(), FarmModel.Weather.CALM, 0)
    private var effect: FarmModel.Effect? = null
    private var effectAt = 0L
    private var transitionFrom: List<FarmModel.Plot>? = null
    private var targetPlots = projection.plots
    private var cropTransitionAt = 0L
    private var cropDuration = FarmWeatherModel.Policy.RECOVERY_MS
    private var weather = FarmWeatherModel.Controller()
    private var scope = ""
    var onSoundCue: ((FarmWeatherModel.SoundCue) -> Unit)? = null
    fun sceneSoundLevel() = weather.frame(SystemClock.elapsedRealtime(),!motion).weather.sound
    private fun shownPlots(now:Long):List<FarmModel.Plot> {
        val fraction=if(motion)((now-cropTransitionAt).toFloat()/cropDuration).coerceIn(0f,1f) else 1f
        return targetPlots.mapIndexed {i,plot->transitionFrom?.getOrNull(i)?.let {FarmModel.blendPlot(it,plot,fraction)}?:plot}
    }
    private var motion = true
    private var lastFrameAt=0L
    private var cloudTravel=0f
    private var rainTravelX=0f
    private var rainTravelY=0f
    private val frame = object : Runnable {
        override fun run() {
            if (!active || !isAttachedToWindow || windowVisibility != VISIBLE) return
            val visible = getGlobalVisibleRect(bounds) && bounds.height() > 0
            if (visible) invalidate()
            ui.postDelayed(this, if (!visible) 700L else if (!motion) 60000L else if (liveEffect() != null || weather.isTransitioning(SystemClock.elapsedRealtime())) 42L else 125L)
        }
    }
    init { importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_YES }
    fun bind(data: FarmModel.Snapshot, state: FarmModel.Projection, environment:FarmWeatherModel.Environment, accountScope:String,
             changes:List<FarmModel.Effect>, newEffect: FarmModel.Effect?) {
        val mono=SystemClock.elapsedRealtime()
        val newScope="$accountScope:${environment.date}"
        if(scope!=newScope) {scope=newScope;weather=weatherSession.controller(accountScope);transitionFrom=null}
        else if(targetPlots!=environment.plots) {
            transitionFrom=shownPlots(mono);cropTransitionAt=mono
            cropDuration=(FarmWeatherModel.Policy.RECOVERY_MS*(1.25f-environment.target.recovery*.5f)).toLong()
        }
        targetPlots=environment.plots
        snapshot = data; elapsedAtLoad = mono; projection = state
        weather.accept(environment,changes,mono,!motion)?.let {if(active)onSoundCue?.invoke(it)}
        if (newEffect != null) { effect = newEffect; effectAt = mono }
        contentDescription = "Living farm. Confirmed daily score ${data.balance}. ${environment.plots.count { it.growth > .7f }} mature plots. Recoverable game weather, not a forecast."
        invalidate()
    }
    fun setActive(value: Boolean) {
        active = value
        motion = Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f
        ui.removeCallbacks(frame)
        if (active && isAttachedToWindow) ui.post(frame)
        if (!value) { effect = null; transitionFrom = null; weather.silence(); invalidate() }
    }
    override fun onAttachedToWindow() { super.onAttachedToWindow(); if (active) { ui.removeCallbacks(frame); ui.post(frame) } }
    override fun onDetachedFromWindow() { ui.removeCallbacks(frame); super.onDetachedFromWindow() }
    override fun onWindowVisibilityChanged(visibility: Int) { super.onWindowVisibilityChanged(visibility); ui.removeCallbacks(frame); if (visibility == VISIBLE && active) ui.post(frame) }
    private fun liveEffect() = effect?.takeIf { SystemClock.elapsedRealtime() - effectAt < 11000L }
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        setMeasuredDimension(w, (w * .68f).toInt().coerceIn((210 * resources.displayMetrics.density).toInt(), (390 * resources.displayMetrics.density).toInt()))
    }
    private fun color(hex: String) = Color.parseColor(hex)
    private fun fill(c: Canvas, hex: String, l: Float, t: Float, r: Float, b: Float, radius: Float = 0f) {
        p.shader = null; p.color = color(hex); p.style = Paint.Style.FILL
        if (radius == 0f) c.drawRect(l,t,r,b,p) else c.drawRoundRect(l,t,r,b,radius,radius,p)
    }
    private fun circle(c: Canvas, x: Float, y: Float, radius: Float, hex: String) { p.shader=null; p.color=color(hex); p.style=Paint.Style.FILL; c.drawCircle(x,y,radius,p) }
    private fun line(c: Canvas, x: Float, y: Float, x2: Float, y2: Float, hex: String, stroke: Float) {
        p.shader=null; p.color=color(hex); p.strokeWidth=stroke; p.strokeCap=Paint.Cap.ROUND; p.style=Paint.Style.STROKE; c.drawLine(x,y,x2,y2,p); p.style=Paint.Style.FILL
    }
    private fun polygon(c: Canvas, hex: String, vararg xy: Float) {
        path.reset(); path.moveTo(xy[0],xy[1]); var i=2; while (i<xy.size) { path.lineTo(xy[i],xy[i+1]); i+=2 }; path.close()
        p.shader=null; p.color=color(hex); p.style=Paint.Style.FILL; c.drawPath(path,p)
    }
    private fun mix(a: Int,b: Int,t: Float): Int = Color.rgb((Color.red(a)+(Color.red(b)-Color.red(a))*t).toInt(),(Color.green(a)+(Color.green(b)-Color.green(a))*t).toInt(),(Color.blue(a)+(Color.blue(b)-Color.blue(a))*t).toInt())
    private fun sky(phase: FarmModel.Light): Pair<Int,Int> = when(phase) {
        FarmModel.Light.SUNRISE -> color("#7355A1") to color("#FFD699")
        FarmModel.Light.MORNING -> color("#148ED3") to color("#C4F0ED")
        FarmModel.Light.AFTERNOON -> color("#1D8CCB") to color("#BCE8C8")
        FarmModel.Light.SUNSET -> color("#65416C") to color("#FFB161")
        FarmModel.Light.NIGHT -> color("#08152F") to color("#284862")
    }
    override fun onDraw(c: Canvas) {
        super.onDraw(c)
        val data = snapshot
        val now = (data?.now ?: System.currentTimeMillis()) + if (active) SystemClock.elapsedRealtime()-elapsedAtLoad else 0L
        val light=FarmModel.lighting(now,data?.timezone ?: java.util.TimeZone.getDefault().id)
        val t = if (motion && active) SystemClock.elapsedRealtime()/1000f else 0f
        val event=liveEffect()
        val frame=weather.frame(SystemClock.elapsedRealtime(),!motion)
        val atmosphere=frame.weather
        val rewardRain=if(event?.weather==FarmModel.Weather.RAIN && event.amount>0) .26f else 0f
        val rainfall=max(atmosphere.rain,rewardRain)
        val stormWind=atmosphere.wind
        val frameAt=SystemClock.elapsedRealtime()
        val dt=if(motion && active && lastFrameAt>0)((frameAt-lastFrameAt)/1000f).coerceIn(0f,.15f) else 0f
        lastFrameAt=frameAt
        cloudTravel=(cloudTravel+dt*(4+stormWind*30))%1400f
        rainTravelX=(rainTravelX+dt*(30+stormWind*160))%1100f
        rainTravelY=(rainTravelY+dt*(160+rainfall*190))%660f
        val severe=atmosphere.damage>=FarmWeatherModel.resolve(FarmWeatherModel.Policy.DISASTER_SCORE).damage
        val night=light.phase==FarmModel.Light.NIGHT
        c.save(); c.scale(width/1000f,height/650f)
        val clip=Path().apply { addRoundRect(RectF(0f,0f,1000f,650f),32f,32f,Path.Direction.CW) }; c.clipPath(clip)
        val a=sky(light.phase); val b=sky(light.next)
        var top=mix(a.first,b.first,light.blend); var bottom=mix(a.second,b.second,light.blend)
        top=mix(top,color("#233146"),atmosphere.darkness)
        bottom=mix(bottom,color("#637A79"),atmosphere.darkness*.85f)
        p.shader=LinearGradient(0f,0f,0f,400f,top,bottom,Shader.TileMode.CLAMP); c.drawRect(0f,0f,1000f,650f,p); p.shader=null
        c.saveLayerAlpha(0f,0f,1000f,340f,(255*atmosphere.light).toInt())
        if(night) {
            for(i in 0..38) { val x=(i*173%980+10).toFloat(); val y=(i*71%235+12).toFloat(); circle(c,x,y,if(i%4==0) 2.5f else 1.3f,"#D8E5FA") }
            circle(c,815f,100f,42f,"#F7F0D6"); circle(c,833f,85f,38f,"#102643")
        } else {
            val sunY=if(light.phase==FarmModel.Light.SUNRISE || light.phase==FarmModel.Light.SUNSET) 220f else 92f
            circle(c,815f,sunY,62f,"#FFE9A0"); circle(c,815f,sunY,43f,"#FFF5C1")
            for(i in 0..11) { val ang=i*PI/6; line(c,815f+cos(ang).toFloat()*72,sunY+sin(ang).toFloat()*72,815f+cos(ang).toFloat()*88,sunY+sin(ang).toFloat()*88,"#FFE7A2",3f) }
        }
        c.restore()
        polygon(c,if(night) "#243D58" else "#77A5B1",0f,305f,135f,175f,290f,310f,460f,170f,650f,310f,820f,215f,1000f,300f,1000f,420f,0f,420f)
        polygon(c,if(night) "#172F43" else "#588F8A",0f,348f,215f,253f,440f,355f,690f,270f,1000f,365f,1000f,450f,0f,450f)
        // Coverage, opacity, wind speed and darkness vary continuously; no separate static scenes.
        val cloudColor=mix(color(if(night)"#4A5C73" else "#EDF9FA"),color("#38485B"),atmosphere.darkness)
        val cloudCount=4+(atmosphere.clouds*9).toInt()
        for(i in 0 until 13) {
            val coverage=(cloudCount-i).coerceIn(0,1).toFloat()
            val fade=if(i==cloudCount)atmosphere.clouds*9%1f else coverage
            if(fade>0) {
                val tinted=Color.argb((255*atmosphere.opacity*fade).toInt(),Color.red(cloudColor),Color.green(cloudColor),Color.blue(cloudColor))
                cloud(c,((i*127f+cloudTravel)%1400)-200f,55f+(i%4)*45f,
                    String.format("#%08X",tinted),.8f+atmosphere.clouds*.9f)
            }
        }
        fill(c,if(night) "#183D35" else "#5E994B",0f,340f,1000f,650f)
        // Rolling meadow, house, hedges and fence remain separate from foreground crop plots.
        p.color=color(if(night) "#244B38" else "#7DAA4C"); c.drawOval(-170f,315f,600f,520f,p)
        c.drawOval(470f,310f,1190f,510f,p)
        house(c,night)
        for(i in 0..8) tree(c,35f+i*119f,335f+(i%2)*17f,if(i%2==0) .8f else .6f,night)
        for(i in 0..9) { val x=i*119f; fill(c,"#8B6749",x,352f,x+10f,427f,3f) }
        fill(c,"#CBA575",0f,371f,1000f,379f); fill(c,"#A6855B",0f,401f,1000f,409f)
        // Increasing storm severity wets/erodes beds and can overflow the channel; recovery settles it.
        val wet=atmosphere.damage*.65f+rainfall*.1f
        fill(c,String.format("#%06X",0xFFFFFF and mix(color("#6C7148"),color("#3C433C"),wet)),0f,565f,1000f,601f)
        fill(c,if(night) "#153B50" else "#45AAB6",0f,596f-wet*24,1000f,650f)
        for(i in 0..15) {val x=(i*84+t*(18+stormWind*25))%1100-40;line(c,x,612f+(i%3)*11,x+40,612f+(i%3)*11,if(night)"#315B72" else "#A5E2D9",2f)}
        val shown=shownPlots(SystemClock.elapsedRealtime())
        for(i in 0..5)crop(c,95f+(i%3)*250,475f+(i/3)*100,shown[i],t,stormWind,i)
        if(severe) {
            for(i in 0..5) {val x=100f+i*155;line(c,x,568f,x+40,550f,"#69523B",5f)}
            // Shelter stays within the scene; no user data or crop record is removed.
            polygon(c,"#5A7152",767f,435f,879f,392f,988f,435f)
            line(c,780f,435f,780f,553f,"#76634B",7f);line(c,975f,435f,975f,553f,"#76634B",7f)
        }
        val tending=event?.action?.takeIf {event.amount>0 && atmosphere.damage<.4f}
        val action=if(frame.action!=FarmModel.Action.IDLE)frame.action else tending ?: if(night)FarmModel.Action.RESTING else if(light.phase==FarmModel.Light.SUNRISE)FarmModel.Action.PLANTING else FarmModel.Action.IDLE
        val progress=if(event!=null)((SystemClock.elapsedRealtime()-effectAt)/11000f).coerceIn(0f,1f) else 0f
        val farmerX=if(severe)890f else if(event!=null)865f-sin(progress*PI).toFloat()*190f else 870f
        robot(c,farmerX,518f,action,t,night)
        if(rainfall>.01f) {
            val count=(95*rainfall).toInt().coerceAtMost(95)
            for(i in 0 until count) {val x=((i*137+rainTravelX)%1100)-70;val y=(i*67+rainTravelY)%660
                line(c,x,y,x-4-stormWind*24,y+15+rainfall*14,"#B6E5F6",1f+rainfall*1.6f)
            }
            for(i in 0..8) {p.color=color("#B5E5EC");p.style=Paint.Style.STROKE;p.strokeWidth=1.2f;val r=(t*8+i*4)%25;c.drawOval(i*125f-r,600f-r*.18f,i*125f+r,600f+r*.18f,p);p.style=Paint.Style.FILL}
        }
        p.color=Color.argb((atmosphere.darkness*52).toInt(),20,33,41);c.drawRect(0f,340f,1000f,650f,p)
        if(frame.flash>0f) {
            c.saveLayerAlpha(0f,0f,1000f,650f,(255*frame.flash).toInt())
            p.color=Color.argb((frame.flash*50).toInt(),239,246,224);c.drawRect(0f,0f,1000f,650f,p)
            polygon(c,"#FFF4B0",370f,25f,330f,130f,366f,125f,335f,220f,418f,95f,378f,98f)
            c.restore()
        }
        // A subtle foreground vignette grounds the UI without flattening the scene.
        p.shader=LinearGradient(0f,540f,0f,650f,Color.TRANSPARENT,color("#4805191C"),Shader.TileMode.CLAMP); c.drawRect(0f,500f,1000f,650f,p); p.shader=null
        c.restore()
    }
    private fun cloud(c: Canvas,x: Float,y: Float,hex: String,s: Float) { c.save(); c.translate(x,y); c.scale(s,s); circle(c,0f,0f,28f,hex); circle(c,35f,-15f,37f,hex); circle(c,76f,0f,29f,hex); fill(c,hex,-18f,0f,90f,25f,18f); c.restore() }
    private fun house(c: Canvas, night: Boolean) {
        fill(c,if(night) "#45505A" else "#F4D8AC",590f,287f,733f,363f,3f)
        polygon(c,if(night) "#442D3F" else "#B45638",565f,292f,659f,227f,752f,292f)
        fill(c,"#6C4F43",702f,238f,716f,278f); fill(c,"#514139",645f,316f,675f,363f,3f)
        for(x in listOf(610f,695f)) fill(c,if(night) "#F8C76A" else "#A4CBD0",x,311f,x+20,333f,2f)
        line(c,578f,292f,748f,292f,"#E1B076",4f)
    }
    private fun tree(c: Canvas,x: Float,y: Float,s: Float,night: Boolean) { c.save();c.translate(x,y);c.scale(s,s);fill(c,"#76563E",-6f,-45f,6f,20f);circle(c,0f,-65f,43f,if(night)"#193E39" else "#397C42");circle(c,-23f,-48f,32f,if(night)"#23483B" else "#589D43");circle(c,25f,-48f,32f,if(night)"#214637" else "#75A94C");c.restore() }
    private fun crop(c: Canvas,x: Float,y: Float,plot: FarmModel.Plot,t: Float,wind: Float,index: Int) {
        p.shader=null;p.color=color(if(plot.moisture<.3f)"#9C7147" else "#664734");c.drawOval(x-88,y-14,x+111,y+30,p)
        for(i in 0..5) { p.color=color(if(plot.moisture<.3f)"#C8955E" else "#A7764B");c.drawOval(x-65+i*29f,y+3,x-46+i*29f,y+14,p) }
        if(plot.growth<=0f) { circle(c,x,y+6,5f,"#D3BA76");return }
        val growth=plot.growth; val height=(34+growth*100)*(.4f+.6f*plot.health)
        val sway=sin(t*1.3f+index).toFloat()*(3+wind*16)
        val dry=plot.moisture<.30f; val damaged=plot.health<.7f
        val stem=if(dry)"#9B8A3A" else if(damaged)"#718449" else "#3D8B45"
        val leaf=if(dry)"#BBA24E" else if(damaged)"#6D934D" else "#75C853"
        c.save();c.translate(x,y);c.rotate(if(damaged) 10f else 0f)
        line(c,0f,0f,sway,-height,stem,5f)
        for(j in 1..(if(plot.health<.3f)1 else if(plot.health<.55f)2 else 4)) { val yy=-height*j/5; val xx=sway*j/5; val len=16+growth*21; val side=if(j%2==0)1 else -1
            path.reset();path.moveTo(xx,yy);path.quadTo(xx+side*len,yy-25,xx+side*(len+8),yy-12);path.quadTo(xx+side*len,yy+11,xx,yy)
            p.style=Paint.Style.FILL;p.color=color(leaf);c.drawPath(path,p)
            line(c,xx,yy,xx+side*(len+3),yy-11,stem,1.6f)
        }
        if(growth>.65f && plot.health>.55f) { circle(c,-13f,-height*.55f,13f,if(growth>.9f)"#EA7240" else "#DBB94C");circle(c,19f,-height*.4f,15f,if(growth>.9f)"#E95E3E" else "#A7C64D");circle(c,-17f,-height*.6f,3f,"#FFD69B") }
        c.restore()
    }
    private fun robot(c: Canvas,x: Float,y: Float,action: FarmModel.Action,t: Float,night: Boolean) {
        c.save();c.translate(x,y)
        val rest=action==FarmModel.Action.RESTING
        val bounce=if(action==FarmModel.Action.CELEBRATING)sin(t*7)*7 else sin(t*2)*2
        c.translate(0f,bounce)
        p.color=color("#3609151B");c.drawOval(-50f,38f,55f,54f,p)
        val step=if(action in setOf(FarmModel.Action.IDLE,FarmModel.Action.RESTING))0f else sin(t*5)*8
        line(c,-18f,20f,-24f+step,43f,"#C8D8D7",14f);line(c,18f,20f,24f-step,43f,"#E8EFDF",14f)
        fill(c,"#263A46",-40f,37f,-10f,50f,8f);fill(c,"#263A46",12f,37f,43f,50f,8f)
        fill(c,"#DCE9DE",-38f,-38f,38f,25f,20f);fill(c,"#BA5143",-39f,-23f,-27f,4f,5f);fill(c,"#BA5143",27f,-23f,39f,4f,5f)
        fill(c,"#17343D",-22f,-22f,22f,9f,9f);circle(c,0f,-7f,9f,"#4AE0BB")
        val armWave=if(action==FarmModel.Action.CELEBRATING)-30f else if(action==FarmModel.Action.STORM_RESPONSE)-18f else 0f
        line(c,-39f,-22f,-60f,-3f+armWave,"#E3EADD",12f);line(c,39f,-22f,59f,-9f,"#E3EADD",12f)
        fill(c,"#C8DCD9",-47f,-109f,47f,-40f,27f);fill(c,"#112A37",-40f,-99f,40f,-51f,19f)
        if(rest) {line(c,-25f,-76f,-8f,-76f,"#48DDD6",4f);line(c,8f,-76f,25f,-76f,"#48DDD6",4f)}
        else { circle(c,-19f,-78f,9f,"#4CE0E6");circle(c,19f,-78f,9f,"#4CE0E6");circle(c,-17f,-80f,3f,"#D2FFFF");circle(c,21f,-80f,3f,"#D2FFFF") }
        line(c,-8f,-60f,8f,-60f,"#63BDBE",3f)
        // Farm hat and ear panels make StrictBot a character, rather than a logo overlay.
        fill(c,"#DEB76D",-58f,-113f,58f,-101f,6f);fill(c,"#EACB87",-32f,-136f,32f,-108f,12f);fill(c,"#7F6745",-32f,-116f,32f,-109f)
        fill(c,"#C75D4B",-55f,-90f,-44f,-69f,5f);fill(c,"#C75D4B",44f,-90f,55f,-69f,5f)
        if(action==FarmModel.Action.RECOVERING) {
            line(c,-60f,-3f,-80f,45f,"#B79A68",5f)
            fill(c,"#648F7A",-91f,35f,-70f,48f,3f) // Repair/tend soil; no nutrient animation for unrelated categories.
            circle(c,-70f,18f+sin(t*3)*3,4f,"#91C36F")
        } else if(action==FarmModel.Action.WATERING) {
            fill(c,"#4EAAB6",-93f,-7f,-58f,24f,7f);line(c,-90f,-1f,-115f,-16f,"#72CBD1",8f)
            p.color=color("#3B8396");p.style=Paint.Style.STROKE;p.strokeWidth=5f;c.drawOval(-64f,-6f,-42f,16f,p);p.style=Paint.Style.FILL
            for(i in 0..8) {val yy=((t*65+i*8)%65);circle(c,-116f-yy*.28f,-15f+yy,2.3f,"#A5E8ED")}
        } else if(action==FarmModel.Action.APPLYING_NUTRIENTS) {
            fill(c,"#C9AC6F",-89f,-6f,-55f,28f,6f);circle(c,-72f,7f,8f,"#609E57")
            for(i in 0..7)circle(c,-85f-i*7,-5f+((t*35+i*9)%60),2.5f,"#9AEF85")
        } else if(action==FarmModel.Action.PLANTING) {circle(c,-65f,12f,6f,"#E2C38A");line(c,59f,-9f,65f,41f,"#A48C62",5f)}
        if(night && !rest) {fill(c,"#E5C37B",51f,-2f,70f,23f,4f);circle(c,60f,10f,5f,"#FFF1B5")}
        c.restore()
    }
    companion object {private val weatherSession=FarmWeatherModel.Session()}
}
