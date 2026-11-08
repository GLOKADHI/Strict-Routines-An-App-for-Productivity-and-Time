package com.strictrecords.app

import kotlin.math.*

/** Root travel only; articulation consumes actual distance instead of another View animator. */
internal class BotRootMotion {
    var position=0f; private set
    var speed=0f; private set
    var acceleration=0f; private set
    var arrived=true; private set
    var braking=false; private set
    private var target=0f
    fun begin(from:Float,to:Float) { position=from;target=to;speed=0f;arrived=abs(to-from)<.001f;acceleration=0f }
    fun brake(){if(!arrived)target=position+sign(speed)*speed*speed/(2f*45f)}
    fun step(dt:Float):Float {
        if(arrived){acceleration=0f;return 0f}
        val before=position;val oldSpeed=speed;val remaining=abs(target-position)
        val stoppingSpeed=sqrt(2f*45f*remaining);braking=stoppingSpeed<CRUISE
        val wanted=min(CRUISE,stoppingSpeed)*sign(target-position)
        speed+=(wanted-speed).coerceIn(-65f*dt,65f*dt)
        if(abs(speed*dt)>=remaining || remaining<.002f){position=target;speed=0f;arrived=true}
        else position+=speed*dt
        acceleration=(speed-oldSpeed)/dt
        return position-before
    }
    companion object { const val CRUISE=24f }
}

/** World-space ground contacts and a 2.5D skeleton, independent of Android rendering. */
class PremiumBotMotion {
    enum class BotAnimState { HIDDEN,ENTERING,WALKING,SLOWING_DOWN,STOPPING,TURNING,
        STANDING,GESTURING,SPEAKING,IDLE,EXITING }
    data class Point(val x:Float,val y:Float)
    data class V3(val x:Float,val y:Float,val z:Float) {
        operator fun plus(b:V3)=V3(x+b.x,y+b.y,z+b.z)
        operator fun minus(b:V3)=V3(x-b.x,y-b.y,z-b.z)
        operator fun times(v:Float)=V3(x*v,y*v,z*v)
        fun dot(b:V3)=x*b.x+y*b.y+z*b.z
        fun length()=sqrt(dot(this))
        fun unit()=this*(1f/length().coerceAtLeast(.00001f))
    }
    data class Leg(val hip:Point,val knee:Point,val ankle:Point,val planted:Boolean,
        val toeAngle:Float,val sole:Point,val depth:Float)
    data class Leg3D(val hip:V3,val knee:V3,val ankle:V3)
    data class Arm(val shoulder:Point,val elbow:Point,val wrist:Point,val handAngle:Float,
        val fingerCurl:Float,val indexCurl:Float,val depth:Float)
    private class Foot(var ground:V3) {
        var height=0f;var planted=true;var previous=-1f
        var start=ground;var target=ground;var roll=0f
    }
    private val root=BotRootMotion()
    private val feet=arrayOf(Foot(V3(-10f,0f,0f)),Foot(V3(10f,0f,0f)))
    private var accumulator=0f
    private var stateTime=0f
    private var stepStart=Array(2){V3(0f,0f,0f)}
    private var stepHeights=FloatArray(2)
    private var firstStopFoot=0
    private var started=false
    private var speech=false
    private var exiting=false
    private var turnTarget=0f
    private var turnFrom=FloatArray(3)
    var weightShift=0f;private set
    var state=BotAnimState.IDLE;private set
    val x get()=root.position
    val speed get()=root.speed
    val facingRight get()=speed>=0f
    val direction get()=if(facingRight)1f else -1f
    val isWalking get()=state in MOVING && abs(speed)>.05f
    val isMoving get()=state in MOVING || state==BotAnimState.STOPPING
    val presentationStarted get()=started
    val speaking get()=speech
    var warning=false
    var stationary:Boolean
        get()=!isMoving
        set(value){if(value && isMoving)requestStop()}
    var time=0f;private set
    var phase=0f;private set
    var distanceTravelled=0f;private set
    var completedCycles=0;private set
    var pelvisYaw=0f;private set
    var torsoYaw=0f;private set
    var headYaw=0f;private set
    val turnProgress get()=1f-abs(pelvisYaw)/WALK_YAW
    var gestureProgress=0f;private set
    private var gestureVelocity=0f
    val pointing get()=gestureProgress
    var warningBlend=0f;private set
    var capeLag=0f;private set
    var capeYaw=0f;private set
    val gaitStrength get()=(abs(speed)/BotRootMotion.CRUISE).coerceIn(0f,1f)
    val bodyBob get()=if(isMoving)-.65f*abs(sin(phase*4f*PI_F))*gaitStrength else .34f*sin(time*2.1f)
    val lateralSway get()=weightShift+if(isMoving).25f*sin(phase*2f*PI_F)*gaitStrength else .18f*sin(time*1.4f)
    val pelvisRoll get()=1.6f*sin(phase*2f*PI_F)*gaitStrength
    val torsoRoll get()=-pelvisRoll*.65f+.3f*sin(time*1.8f)*(1f-gaitStrength)
    val headTilt get()=-torsoRoll*.55f+.75f*sin(time*1.35f)*(1f-gaitStrength)
    val armSwing get()=23f*sin(phase*2f*PI_F)*gaitStrength
    val blink get():Float {
        val t=time%4.1f
        return if(t<3.88f)0f else sin(((t-3.88f)/.22f).coerceIn(0f,1f)*PI_F)
    }
    fun playEntrance(distance:Float=110f,restart:Boolean=false) {
        if(started && !restart)return
        started=true;speech=false;exiting=false;weightShift=0f;time=0f;phase=0f;distanceTravelled=0f;completedCycles=0
        gestureProgress=0f;gestureVelocity=0f;accumulator=0f;root.begin(-distance.coerceAtLeast(65f),0f)
        pelvisYaw=WALK_YAW;torsoYaw=WALK_YAW;headYaw=WALK_YAW;capeYaw=WALK_YAW;capeLag=0f
        for(i in 0..1){
            val side=if(i==0)-1f else 1f
            feet[i].ground=rotated(V3(side*8f,0f,0f),WALK_YAW)+V3(x+(if(i==0)5f else -5f),0f,0f)
            feet[i].height=0f;feet[i].previous=-1f;feet[i].planted=true;feet[i].roll=0f
        }
        change(BotAnimState.ENTERING)
    }
    /** Recreating a completed presentation does not replay its entrance. */
    fun standInPlace() {
        started=true;root.begin(0f,0f);pelvisYaw=0f;torsoYaw=0f;headYaw=0f
        feet.forEachIndexed{i,f->f.ground=V3(if(i==0)-10f else 10f,0f,0f);f.height=0f;f.planted=true;f.roll=0f}
        change(BotAnimState.IDLE)
    }
    fun setSpeaking(value:Boolean) {
        speech=value
        if(value && state==BotAnimState.IDLE)change(BotAnimState.GESTURING)
        if(!value && state==BotAnimState.SPEAKING)change(BotAnimState.IDLE)
    }
    fun setState(value:BotAnimState) {
        when(value){
            BotAnimState.ENTERING,BotAnimState.WALKING->playEntrance()
            BotAnimState.HIDDEN->{speech=false;change(value)}
            BotAnimState.SLOWING_DOWN,BotAnimState.STOPPING,BotAnimState.TURNING->requestStop()
            BotAnimState.STANDING,BotAnimState.IDLE->if(isMoving)requestStop() else change(value)
            BotAnimState.SPEAKING->setSpeaking(true)
            BotAnimState.GESTURING->if(!isMoving)change(value)
            BotAnimState.EXITING->{speech=false;exiting=true;if(isMoving)requestStop() else beginTurn(-WALK_YAW)}
        }
    }
    fun update(seconds:Float) {
        if(state==BotAnimState.HIDDEN || !seconds.isFinite())return
        accumulator+=seconds.coerceIn(0f,.25f)
        while(accumulator>=STEP){advance(STEP);accumulator-=STEP}
    }
    private fun change(next:BotAnimState){if(state!=next){state=next;stateTime=0f}}
    private fun advance(dt:Float) {
        time+=dt;stateTime+=dt
        if(state in MOVING){
            val travel=root.step(dt);distanceTravelled+=abs(travel)
            phase=(distanceTravelled/STRIDE)%1f;completedCycles=(distanceTravelled/STRIDE).toInt()
            if(state!=BotAnimState.EXITING){
                if(root.braking)change(BotAnimState.SLOWING_DOWN)
                else if(stateTime>.25f)change(BotAnimState.WALKING)
            }
            updateGait()
            if(root.arrived){if(state==BotAnimState.EXITING)change(BotAnimState.HIDDEN) else beginStopping()}
        }else when(state){
            BotAnimState.STOPPING->{
                val first=firstStopFoot;val other=1-first
                settleFoot(first,(stateTime/.34f).coerceIn(0f,1f),neutral(first,pelvisYaw),3f)
                if(stateTime>.34f)settleFoot(other,((stateTime-.34f)/.34f).coerceIn(0f,1f),neutral(other,pelvisYaw),3f)
                if(stateTime>=.68f)beginTurn(if(exiting)-WALK_YAW else 0f)
            }
            BotAnimState.TURNING->{
                pelvisYaw=turnFrom[0]+(turnTarget-turnFrom[0])*ease(stateTime/.84f)
                torsoYaw=turnFrom[1]+(turnTarget-turnFrom[1])*ease((stateTime-.10f)/.88f)
                headYaw=turnFrom[2]+(turnTarget-turnFrom[2])*ease((stateTime-.18f)/.90f)
                settleFoot(0,(stateTime/.45f).coerceIn(0f,1f),neutral(0,turnTarget),2.5f)
                if(stateTime>.5f)settleFoot(1,((stateTime-.5f)/.45f).coerceIn(0f,1f),neutral(1,turnTarget),2.5f)
                if(stateTime>=1.08f){
                    pelvisYaw=turnTarget;torsoYaw=turnTarget;headYaw=turnTarget
                    if(exiting){
                        feet.forEach{it.previous=-1f;it.target=it.ground};distanceTravelled=0f;phase=0f
                        root.begin(x,x-120f);change(BotAnimState.EXITING)
                    }else change(BotAnimState.STANDING)
                }
            }
            BotAnimState.STANDING->if(stateTime>.26f)change(BotAnimState.GESTURING)
            BotAnimState.GESTURING->if(stateTime>.85f)change(if(speech)BotAnimState.SPEAKING else BotAnimState.IDLE)
            else->Unit
        }
        val supports=feet.filter{it.planted}
        val balance=if(supports.size==1)(supports[0].ground.x-x).coerceIn(-4.8f,4.8f) else 0f
        weightShift=approach(weightShift,balance,10f,dt)
        val gesture=if(state==BotAnimState.GESTURING || state==BotAnimState.SPEAKING)1f else 0f
        // Critically damped motor response: no instantaneous hand velocity on a pose change.
        gestureVelocity+=((gesture-gestureProgress)*64f-gestureVelocity*16f)*dt
        gestureProgress=(gestureProgress+gestureVelocity*dt).coerceIn(0f,1f)
        warningBlend=approach(warningBlend,if(warning)1f else 0f,6f,dt)
        capeLag=approach(capeLag,-3.8f*speed/BotRootMotion.CRUISE-.018f*root.acceleration,4f,dt)
        capeYaw=approach(capeYaw,torsoYaw,5f,dt)
    }
    private fun requestStop(){
        if(state in MOVING && !root.arrived){root.brake();change(BotAnimState.SLOWING_DOWN)}
        else if(isMoving)beginStopping()
    }
    private fun beginTurn(target:Float){
        captureFeet();turnFrom=floatArrayOf(pelvisYaw,torsoYaw,headYaw);turnTarget=target;change(BotAnimState.TURNING)
    }
    private fun beginStopping() {
        if(state==BotAnimState.STOPPING || state==BotAnimState.TURNING)return
        if(!root.arrived)root.begin(x,x)
        firstStopFoot=if(feet[0].height>feet[1].height)0 else 1
        captureFeet();change(BotAnimState.STOPPING)
    }
    private fun captureFeet(){stepStart=Array(2){feet[it].ground};stepHeights=FloatArray(2){feet[it].height}}
    private fun neutral(i:Int,yaw:Float)=rotated(V3(if(i==0)-10f else 10f,0f,0f),yaw)+V3(x,0f,0f)
    private fun settleFoot(i:Int,t:Float,target:V3,lift:Float) {
        val f=feet[i];val distance=(target-stepStart[i]).length()
        if(t<=0f || distance<.025f && stepHeights[i]<.025f){if(distance<.025f && stepHeights[i]<.025f){f.height=0f;f.planted=true};return}
        f.ground=stepStart[i]+(target-stepStart[i])*ease(t)
        f.height=stepHeights[i]*(1f-ease(t))+sin(t*PI_F)*lift
        if(t>=1f){f.ground=target;f.height=0f}
        f.planted=t>=1f;f.roll=0f
    }
    private fun updateGait() {
        for(i in 0..1){
            val f=feet[i];val p=(phase+i*.5f)%1f
            if(p<STANCE){
                if(f.previous>=STANCE)f.ground=f.target
                f.height=0f;f.planted=true
                f.roll=if(p<.10f)7f*(1f-p/.10f) else if(p>.5f)-9f*(p-.5f)/.12f else 0f
            }else{
                if(f.previous<STANCE){
                    f.start=f.ground
                    val lead=abs(speed)*(STRIDE/BotRootMotion.CRUISE)*((1f-STANCE)+STANCE*.5f)
                    val lateral=rotated(V3(if(i==0)-8f else 8f,0f,0f),pelvisYaw)
                    f.target=V3(x+lead*direction+lateral.x,0f,lateral.z)
                }
                val t=((p-STANCE)/(1f-STANCE)).coerceIn(0f,1f)
                f.ground=f.start+(f.target-f.start)*ease(t)
                f.height=sin(t*PI_F)*6.8f;f.planted=false;f.roll=-12f*cos(t*PI_F)
            }
            f.previous=p
        }
    }
    fun leg3D(i:Int):Leg3D {
        val f=feet[i];val side=if(i==0)-1f else 1f
        val hip=rotated(V3(side*8f,-27f+bodyBob,0f),pelvisYaw)+V3(x+lateralSway,0f,0f)
        val ankle=V3(f.ground.x,-4f-f.height,f.ground.z)
        val hint=rotated(V3(side*.12f,0f,1f),pelvisYaw)
        return Leg3D(hip,solveJoint(hip,ankle,13f,13f,hint),ankle)
    }
    fun leg(i:Int):Leg {
        val v=leg3D(i);val f=feet[i]
        return Leg(project(v.hip),project(v.knee),project(v.ankle),f.planted,f.roll,
            project(V3(f.ground.x,-f.height,f.ground.z)),v.hip.z)
    }
    fun arm(i:Int):Arm {
        val side=if(i==0)-1f else 1f;val gesture=if(i==1)gestureProgress else 0f
        val swing=sin((phase+i*.5f)*2f*PI_F)*.65f*gaitStrength
        val shoulder=V3(side*18f,-47f+bodyBob,0f)
        val lift=(6f+104f*gesture)*PI_F/180f
        val elbow=shoulder+V3(side*sin(lift),cos(lift)*cos(swing),sin(swing)*(1f-gesture)).unit()*12f
        val foreAngle=(12f+128f*gesture+5f*cos((phase+i*.5f)*2f*PI_F)*gaitStrength*(1f-gesture)+1.2f*sin(time*1.8f))*PI_F/180f
        val wrist=elbow+V3(side*sin(foreAngle),cos(foreAngle),.30f*sin(swing)*(1f-gesture)).unit()*11f
        fun world(p:V3):V3{
            val roll=torsoRoll*PI_F/180f;val dy=p.y+38f
            val q=V3(p.x*cos(roll)-dy*sin(roll),p.x*sin(roll)+dy*cos(roll)-38f,p.z)
            return rotated(q,torsoYaw)+V3(x+lateralSway,0f,0f)
        }
        val a=project(world(shoulder));val b=project(world(elbow));val c=project(world(wrist))
        val angle=atan2(c.y-b.y,c.x-b.x)*180f/PI_F-90f+gesture*6f+3f*sin((phase+i*.5f)*2f*PI_F-.25f)*gaitStrength
        return Arm(a,b,c,angle,24f+61f*gesture,24f*(1f-gesture),world(shoulder).z)
    }
    companion object {
        const val STRIDE=20f
        const val WALK_YAW=64f
        private const val STANCE=.62f
        private const val STEP=1f/120f
        private const val PI_F=3.1415927f
        private val MOVING=setOf(BotAnimState.ENTERING,BotAnimState.WALKING,BotAnimState.SLOWING_DOWN,BotAnimState.EXITING)
        fun ease(t:Float):Float{val v=t.coerceIn(0f,1f);return v*v*(3f-2f*v)}
        private fun approach(a:Float,b:Float,rate:Float,dt:Float)=a+(b-a)*(1f-exp(-rate*dt))
        fun rotated(p:V3,degrees:Float):V3{
            val a=degrees*PI_F/180f;val c=cos(a);val s=sin(a)
            return V3(p.x*c+p.z*s,p.y,-p.x*s+p.z*c)
        }
        fun project(p:V3)=Point(p.x,p.y)
        fun solveJoint(hip:V3,foot:V3,upper:Float,lower:Float,hint:V3):V3{
            val delta=foot-hip;val actual=delta.length().coerceAtLeast(.00001f);val axis=delta*(1f/actual)
            val d=actual.coerceIn(abs(upper-lower)+.0001f,upper+lower-.0001f)
            val along=(upper*upper-lower*lower+d*d)/(2f*d)
            val bend=sqrt((upper*upper-along*along).coerceAtLeast(0f))
            var normal=hint-axis*hint.dot(axis)
            if(normal.length()<.0001f)normal=V3(1f,0f,0f)-axis*axis.x
            return hip+axis*along+normal.unit()*bend
        }
        fun solveKnee(hip:Point,foot:Point,thigh:Float,shin:Float,direction:Float):Point{
            val k=solveJoint(V3(hip.x,hip.y,0f),V3(foot.x,foot.y,0f),thigh,shin,V3(direction,0f,0f))
            return Point(k.x,k.y)
        }
    }
}
