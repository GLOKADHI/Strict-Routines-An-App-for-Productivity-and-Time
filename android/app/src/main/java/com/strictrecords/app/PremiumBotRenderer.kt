package com.strictrecords.app

import android.graphics.*
import kotlin.math.*

/**
 * Premium articulated robot renderer for StrictRecords.
 * Faithfully matches the regal crowned robot reference design:
 * - 5-peak metallic gold crown with cyan diamond jewel
 * - Metallic pearlescent white armor with graphite under-suit
 * - Glossy curved visor with glowing cyan eyes and dynamic mouth equalizer
 * - Circular gold chest emblem with crown crest and cyan core
 * - Deep royal navy/charcoal cape with gold trim
 * - Articulated mechanical arms with distinct pointing finger gesture
 * - Sculpted thighs, knees with cyan rings, shins with cyan conduits
 * - Heavy-duty mechanical boots with tread and metallic gold toe caps
 * - Perspective turning blend (0f = walk profile, 1f = front-facing user stance)
 */
internal class PremiumBotRenderer {

    private val rect = RectF()
    private val path = Path()

    // Shaders and paints
    private val metal = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        shader = LinearGradient(-22f, -85f, 26f, -20f,
            intArrayOf(Color.WHITE, Color.rgb(228, 236, 242), Color.rgb(130, 150, 168), Color.rgb(248, 250, 252)),
            floatArrayOf(0f, 0.32f, 0.68f, 1f), Shader.TileMode.CLAMP)
    }
    private val metalLight = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        shader = LinearGradient(-15f, -60f, 15f, -10f,
            intArrayOf(Color.WHITE, Color.rgb(235, 242, 247), Color.rgb(180, 198, 212)),
            null, Shader.TileMode.CLAMP)
    }
    private val graphite = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(24, 34, 46) }
    private val graphiteLight = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(44, 58, 74) }
    private val darkLimb = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(50, 66, 82) }
    private val limb = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        shader = LinearGradient(-8f, 0f, 8f, 0f,
            intArrayOf(Color.rgb(140, 160, 176), Color.WHITE, Color.rgb(180, 200, 215)),
            null, Shader.TileMode.CLAMP)
    }
    private val gold = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        shader = LinearGradient(-20f, -96f, 20f, -70f,
            intArrayOf(Color.rgb(150, 85, 18), Color.rgb(255, 222, 130), Color.rgb(208, 140, 32), Color.rgb(255, 238, 160)),
            null, Shader.TileMode.CLAMP)
    }
    private val goldStroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1.2f
        color = Color.rgb(230, 175, 55)
    }
    private val cyan = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(0, 229, 255) }
    private val cyanGlow = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(120, 0, 180, 230) }
    private val cyanCore = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(0, 110, 150) }
    private val alertAmber = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(255, 150, 30) }
    private val shine = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(160, 255, 255, 255)
        style = Paint.Style.STROKE
        strokeWidth = 1f
    }
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.rgb(75, 95, 115)
        style = Paint.Style.STROKE
        strokeWidth = 0.8f
    }
    private val visor = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        shader = LinearGradient(-18f, -75f, 18f, -54f,
            intArrayOf(Color.rgb(22, 45, 62), Color.rgb(2, 8, 15), Color.rgb(5, 18, 28)),
            null, Shader.TileMode.CLAMP)
    }
    private val cape = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        shader = LinearGradient(-28f, -55f, 24f, -8f,
            intArrayOf(Color.rgb(12, 22, 38), Color.rgb(28, 38, 54), Color.rgb(7, 13, 24)),
            null, Shader.TileMode.CLAMP)
    }
    private val shadow = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(70, 0, 0, 0) }

    private fun round(c: Canvas, l: Float, t: Float, r: Float, b: Float, radius: Float, p: Paint) {
        rect.set(l, t, r, b)
        c.drawRoundRect(rect, radius, radius, p)
    }

    private fun segment(c: Canvas, ax: Float, ay: Float, bx: Float, by: Float, width: Float, p: Paint) {
        val oldStyle = p.style
        val oldWidth = p.strokeWidth
        val oldCap = p.strokeCap
        p.style = Paint.Style.STROKE
        p.strokeWidth = width
        p.strokeCap = Paint.Cap.ROUND
        c.drawLine(ax, ay, bx, by, p)
        p.style = oldStyle
        p.strokeWidth = oldWidth
        p.strokeCap = oldCap
    }

    fun draw(c: Canvas, m: PremiumBotMotion, speaking: Boolean) {
        if (m.state == PremiumBotMotion.BotAnimState.HIDDEN) return
        // Shadow belongs to the ground, not the chest's breathing transform.
        rect.set(-24f,-1.6f,24f,3f);c.drawOval(rect,shadow)
        drawCape(c,m,m.turnProgress)
        val left=m.leg(0);val right=m.leg(1)
        if(left.depth<right.depth){leg(c,m,0,true,m.turnProgress);leg(c,m,1,false,m.turnProgress)}
        else {leg(c,m,1,true,m.turnProgress);leg(c,m,0,false,m.turnProgress)}
        for(i in 0..1) if(m.arm(i).depth < -1f) arm(c,m,i)
        // Pelvis, chest and head have separate hierarchical pivots; no whole-image flip.
        c.save();c.translate(m.lateralSway,m.bodyBob)
        c.rotate(m.pelvisRoll,0f,-26f)
        c.scale(.65f+.35f*cos(m.pelvisYaw*PI.toFloat()/180f),1f)
        round(c,-9.5f,-28f,9.5f,-24f,3.5f,metalLight)
        round(c,-9.5f,-28f,9.5f,-24f,3.5f,edge);c.restore()
        c.save();c.translate(m.lateralSway,m.bodyBob);c.rotate(m.torsoRoll,0f,-38f)
        c.scale(.76f+.24f*cos(m.torsoYaw*PI.toFloat()/180f),1f)
        drawTorso(c,m,m.turnProgress);c.restore()
        c.save();c.translate(m.lateralSway*.55f,m.bodyBob*.3f)
        c.rotate(m.headTilt,0f,-53f)
        c.scale(.62f+.38f*cos(m.headYaw*PI.toFloat()/180f),1f)
        drawHead(c,m,speaking,m.turnProgress);c.restore()
        for(i in 0..1) if(m.arm(i).depth >= -1f) arm(c,m,i)
    }

    private fun drawCape(c: Canvas, m: PremiumBotMotion, turn: Float) {
        val front=cos(m.capeYaw*PI.toFloat()/180f)
        val shoulders=17f*(.55f+.45f*front)
        val tail=25f*(.65f+.35f*front)
        val cloth=sin(m.time*2.6f)*(.7f+1.1f*m.gaitStrength)
        val lag=m.capeLag
        c.save();c.translate(m.lateralSway,m.bodyBob*.7f)
        path.reset();path.moveTo(-shoulders,-49f)
        path.cubicTo(-tail+lag,-38f,-tail+lag*1.6f+cloth,-19f,-tail+2f+lag*1.8f,-8f)
        path.quadTo(lag,-6f,tail-2f+lag*.5f,-8f)
        path.cubicTo(tail+lag*.5f-cloth,-20f,tail+lag*.25f,-38f,shoulders,-49f)
        path.close();c.drawPath(path,cape);c.drawPath(path,goldStroke);c.restore()
    }

    private fun drawTorso(c: Canvas, m: PremiumBotMotion, turn: Float) {
        val halfW = 15f + 2.5f * turn
        // Midsection dark graphite under-suit
        round(c, -halfW, -51f, halfW, -25f, 8f, graphite)

        // White metallic chest armor plate
        round(c, -halfW + 0.8f, -51f, halfW - 0.8f, -32f, 7f, metal)
        round(c, -halfW + 0.8f, -51f, halfW - 0.8f, -32f, 7f, edge)
        round(c, -halfW + 3f, -49f, halfW - 3f, -44f, 2f, shine)

        // Shoulder armor follows the actual arm joints, rather than static chest marks.

        // Central chest core emblem with gold crown crest
        val emblemX = (1f - turn) * 1.5f
        c.drawCircle(emblemX, -41f, 8.2f, graphite)
        c.drawCircle(emblemX, -41f, 7.2f, gold)
        c.drawCircle(emblemX, -41f, 5.5f, cyanCore)
        c.drawCircle(emblemX, -41f, 4.4f, cyan)
        c.drawCircle(emblemX, -41f, 3.4f, graphite)

        // Crown crest inside emblem
        path.reset()
        path.moveTo(emblemX - 2.8f, -41f)
        path.lineTo(emblemX - 2.2f, -38.5f)
        path.lineTo(emblemX + 2.2f, -38.5f)
        path.lineTo(emblemX + 2.8f, -41f)
        path.lineTo(emblemX + 1.2f, -40f)
        path.lineTo(emblemX, -43f)
        path.lineTo(emblemX - 1.2f, -40f)
        path.close()
        c.drawPath(path, gold)

        // Abdominal segmentation lines & Pelvic belt
        segment(c, -11f, -30f, 11f, -30f, 2.2f, graphite)
        segment(c, -9f, -27f, -4f, -27f, 1.4f, cyan)
        segment(c, 4f, -27f, 9f, -27f, 1.4f, cyan)

        // Pelvis is drawn on its own hip pivot.
    }

    private fun drawHead(c: Canvas, m: PremiumBotMotion, speaking: Boolean, turn: Float) {
        c.save()
        val tilt = 0f // Stabilization is applied at the neck pivot by the skeleton.
        c.rotate(tilt, 0f, -54f)

        // Graphite neck joint
        round(c, -5f, -54f, 5f, -48f, 2.5f, graphite)

        // Ear cylinders with concentric gold ring and cyan jewel
        for (side in intArrayOf(-1, 1)) {
            val ex = side * 21.5f
            c.drawCircle(ex, -64.5f, 6.2f, graphite)
            c.drawCircle(ex, -64.5f, 5f, gold)
            c.drawCircle(ex, -64.5f, 3.2f, graphite)
            c.drawCircle(ex, -64.5f, 1.8f, cyan)
        }

        // Helmet main shell (pearly white rounded metallic)
        round(c, -21.5f, -79f, 21.5f, -51f, 11f, metal)
        round(c, -21.5f, -79f, 21.5f, -51f, 11f, edge)

        // Dark curved glass visor frame and visor
        round(c, -17.8f, -74.5f, 17.8f, -55f, 7.5f, graphite)
        round(c, -16.5f, -73.5f, 16.5f, -56.5f, 6.5f, visor)

        // Specular arc highlight across glass visor
        path.reset()
        path.moveTo(-15f, -71f)
        path.quadTo(-5f, -75f, 12f, -71f)
        c.drawPath(path, shine)

        // Glowing cyan visor eyes
        val blink = m.blink > .9f
        val eyeH = 6.2f * (1f - .9f * m.blink)
        val eyePaint = if (m.warning) alertAmber else cyan

        for (side in intArrayOf(-1, 1)) {
            val x = side * 7.5f + sin(m.headYaw * PI.toFloat() / 180f) * 3f
            round(c, x - 3.8f, -65.5f - eyeH / 2, x + 3.8f, -65.5f + eyeH / 2, 3f, cyanGlow)
            round(c, x - 2.8f, -65.5f - eyeH / 2, x + 2.8f, -65.5f + eyeH / 2, 2.5f, eyePaint)

            if (!blink) {
                // Specular reflection glint in eye
                c.drawCircle(x - 0.8f, -67f, 1.1f, shine)
            }
            if (m.warning) {
                // Alert brow wedge shape
                segment(c, x - side * 4f, -71f, x + side * 3.6f, -68.8f, 1.8f, alertAmber)
            }
        }

        // Mouth: Dynamic LED equalizer during speech, neat horizontal bar when idle
        if (speaking) {
            for (i in -2..2) {
                val bar = 1.2f + abs(sin(m.time * 16f + i * 1.3f)) * 3f
                round(c, i * 2.8f - 0.8f, -58f - bar, i * 2.8f + 0.8f, -58f, 0.6f, cyan)
            }
        } else {
            round(c, -3.8f, -59f, 3.8f, -57.5f, 0.7f, cyan)
        }

        // 5-Peak Regal Gold Crown
        path.reset()
        path.moveTo(-16f, -78f)
        path.lineTo(-19f, -87f)     // Left outer peak
        path.lineTo(-10f, -83f)
        path.lineTo(-8f, -90f)      // Left inner peak
        path.lineTo(-2f, -85f)
        path.lineTo(0f, -95f)       // Tall central peak
        path.lineTo(2f, -85f)
        path.lineTo(8f, -90f)       // Right inner peak
        path.lineTo(10f, -83f)
        path.lineTo(19f, -87f)      // Right outer peak
        path.lineTo(16f, -78f)
        path.close()
        c.drawPath(path, gold)

        // Crown base headband and trim
        segment(c, -15f, -79f, 15f, -79f, 2.6f, gold)
        c.drawPath(path, goldStroke)

        // Cyan diamond gemstone in center peak
        path.reset()
        path.moveTo(0f, -89.5f)
        path.lineTo(2f, -87f)
        path.lineTo(0f, -84.5f)
        path.lineTo(-2f, -87f)
        path.close()
        c.drawPath(path, cyan)

        // Gold sphere tips on main peaks
        c.drawCircle(-19f, -87f, 1.6f, gold)
        c.drawCircle(0f, -95f, 2f, gold)
        c.drawCircle(19f, -87f, 1.6f, gold)

        c.restore()
    }

    private fun leg(c: Canvas,m: PremiumBotMotion,index: Int,behind: Boolean,turn: Float) {
        val l=m.leg(index)
        val hx=l.hip.x-m.x;val kx=l.knee.x-m.x;val ax=l.ankle.x-m.x
        val armor=if(behind)graphiteLight else metalLight
        segment(c,hx,l.hip.y,kx,l.knee.y,8.2f,graphite)
        segment(c,hx,l.hip.y,kx,l.knee.y,6.7f,armor)
        c.drawCircle(kx,l.knee.y,4.2f,graphite);c.drawCircle(kx,l.knee.y,3.2f,gold)
        c.drawCircle(kx,l.knee.y,2.1f,cyan);c.drawCircle(kx,l.knee.y,1.1f,graphite)
        segment(c,kx,l.knee.y,ax,l.ankle.y,7.2f,armor)
        segment(c,kx+.6f,l.knee.y+1f,ax+.6f,l.ankle.y-1f,1.4f,cyan)
        val angle=l.toeAngle
        val a=angle*PI.toFloat()/180f
        // Rock around heel/toe contact without letting the sole penetrate the floor.
        val correction=max(0f,max(sin(a)*9f,sin(a)*-6.5f))
        c.save();c.translate(l.sole.x-m.x,l.sole.y-correction);c.rotate(angle)
        val footWidth=.68f+.32f*abs(sin(m.pelvisYaw*PI.toFloat()/180f))
        c.scale(footWidth,1f)
        round(c,-5f,-7f,5f,-3.8f,2f,graphite)
        segment(c,-4f,-5.5f,4f,-5.5f,1.2f,cyan)
        round(c,-6f,-5.4f,8.5f,-1.2f,2.6f,armor)
        round(c,-6.5f,-2.2f,9f,0f,1f,graphite)
        val toe=5.8f*(1f-turn)
        round(c,toe-2.3f,-4f,toe+2.8f,-.6f,1.3f,gold)
        c.restore()
    }

    private fun arm(c: Canvas,m: PremiumBotMotion,index: Int) {
        val a=m.arm(index);val armor=if(a.depth < -1f)graphiteLight else metalLight
        val sx=a.shoulder.x-m.x;val ex=a.elbow.x-m.x;val wx=a.wrist.x-m.x
        c.drawCircle(sx,a.shoulder.y,5.2f,graphite);c.drawCircle(sx,a.shoulder.y,4f,gold)
        segment(c,sx,a.shoulder.y,ex,a.elbow.y,7.1f,limb)
        c.drawCircle(ex,a.elbow.y,3.5f,graphite);c.drawCircle(ex,a.elbow.y,2.3f,gold)
        segment(c,ex,a.elbow.y,wx,a.wrist.y,7f,armor)
        segment(c,ex+.8f,a.elbow.y+1f,wx+.8f,a.wrist.y-1f,1.5f,cyan)
        c.save();c.translate(wx,a.wrist.y);c.rotate(a.handAngle)
        round(c,-3.6f,-1.5f,3.6f,2f,1.8f,graphite)
        round(c,-3.3f,1f,3.3f,4f,1.5f,armor)
        for(i in 0..3) {
            val fx=-2.7f+i*1.8f
            val curl=(if(i==3)a.indexCurl else a.fingerCurl)+1.5f*sin(m.time*1.5f+i*.3f)
            val q=curl*PI.toFloat()/180f
            val jx=fx-sin(q*.35f)*2.8f;val jy=3f+cos(q*.35f)*2.8f
            val tx=jx-sin(q)*2.6f;val ty=jy+cos(q)*2.6f
            segment(c,fx,3f,jx,jy,1.5f,armor)
            c.drawCircle(jx,jy,.85f,graphite)
            segment(c,jx,jy,tx,ty,1.3f,armor);c.drawCircle(tx,ty,.85f,gold)
        }
        segment(c,-3.1f,1.7f,-5f,3.2f,1.7f,armor)
        segment(c,-5f,3.2f,-4.4f,5.1f,1.5f,armor)
        c.restore()
    }
}
