package com.strictrecords.app

import android.app.AlertDialog
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.*
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/** Isolated Home component; all authoritative data flows through FarmRepository. */
class FarmDashboardView(context: Context, private val db: StrictRecordsLocalDb, val devMode: Boolean,
                        private val openHistory: () -> Unit, private val openDevices: () -> Unit) : LinearLayout(context) {
    private val prefs = context.getSharedPreferences("farm_presentation_v1", Context.MODE_PRIVATE)
    private val ui = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private val sound = FarmSoundController(context)
    private val repository = FarmRepository(db)
    private val scene = FarmSceneView(context).apply {
        onSoundCue = { cue -> sound.schedule(cue,this@FarmDashboardView.soundEnabled,this@FarmDashboardView.foreground) { sceneSoundLevel() } }
    }
    private val condition = label("Your farm is waking up…", 14f, GREEN, true)
    private val greeting = label("", 12f, TEXT)
    private val dateLabel = label("", 14f, CYAN, true)
    private val statistics = LinearLayout(context).apply { orientation = VERTICAL }
    private val history = LinearLayout(context).apply { orientation = VERTICAL }
    private val progress = label("Seed → sprout → growing → mature → harvest", 12f, MUTED)
    private val sync = label("Reading the Android ledger…", 12f, MUTED)
    private val summary = label("", 12f, MUTED)
    private val periodButtons = mutableMapOf<FarmModel.Period, TextView>()
    private var period = runCatching { FarmModel.Period.valueOf(prefs.getString("period", "DAILY")!!) }.getOrDefault(FarmModel.Period.DAILY)
    private var anchor = prefs.getLong("anchor", 0).takeIf { it > 0 } ?: db.authoritativeNowMs()
    private var followToday = prefs.getBoolean("follow_today", true)
    private var data: FarmModel.Snapshot? = null
    private var projection: FarmModel.Projection? = null
    private var foreground = false
    private var closed = false
    private var inFlight = false
    private var scoped: String? = null
    private var seen: Set<String> = emptySet()
    private var initialized = false
    private var historyLimit = 40
    private var soundEnabled = prefs.getBoolean("sound", false)
    private val refresh = object : Runnable {
        override fun run() { if (foreground && !closed) { refreshData(); ui.postDelayed(this, 30000L) } }
    }
    init {
        orientation = VERTICAL
        layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT)
        val hero = card()
        val heroHeading = LinearLayout(context).apply { orientation = VERTICAL; setPadding(d(14),d(13),d(14),d(10)) }
        heroHeading.addView(label("MY FARM", 12f, GREEN, true))
        heroHeading.addView(condition)
        heroHeading.addView(greeting)
        hero.addView(heroHeading)
        hero.addView(scene, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        hero.addView(label("Game weather follows saved activity · lighting follows your local schedule clock", 10.5f, MUTED).apply { setPadding(d(12),d(9),d(12),d(11)) })
        addView(hero)
        val periods = LinearLayout(context).apply { orientation = HORIZONTAL; background = background(SURFACE,20); setPadding(d(4),d(4),d(4),d(4)) }
        FarmModel.Period.values().forEach { choice ->
            val button = label(choice.name.lowercase().replaceFirstChar { it.uppercase() }, 13f, TEXT, true).apply {
                gravity = Gravity.CENTER; minHeight = d(46); isClickable=true; isFocusable=true
                setOnClickListener { period=choice; historyLimit=40; saveSelection(); renderPeriod() }
            }
            periodButtons[choice]=button; periods.addView(button,LayoutParams(0,LayoutParams.WRAP_CONTENT,1f))
        }
        addView(periods, gapParams())
        val dates=LinearLayout(context).apply { orientation=HORIZONTAL;gravity=Gravity.CENTER_VERTICAL }
        dates.addView(button("‹","Previous period") { shift(-1) },LayoutParams(d(48),d(48)))
        dateLabel.gravity=Gravity.CENTER
        dates.addView(dateLabel,LayoutParams(0,LayoutParams.WRAP_CONTENT,1f))
        dates.addView(button("›","Next period") { shift(1) },LayoutParams(d(48),d(48)))
        dates.addView(button("Today","Return to current period") { followToday=true; historyLimit=40; anchor=db.authoritativeNowMs();saveSelection();renderPeriod() },LayoutParams(d(64),d(48)))
        addView(dates,gapParams())
        addView(statistics)
        val growth=card().apply { setPadding(d(14),d(14),d(14),d(14)) }
        growth.addView(label("FARM PROGRESS",13f,TEXT,true));growth.addView(progress)
        growth.addView(label("Rewarded proof → growth & care\nConfirmed deduction → gathering storm\nNegative score → sustained weather · earned points → gradual recovery\nMissed required slot → drier soil",12f,MUTED).apply {setPadding(0,d(9),0,0)})
        addView(growth)
        val txCard=card().apply { setPadding(d(14),d(14),d(14),d(14)) }
        val titleRow=LinearLayout(context).apply { orientation=HORIZONTAL;gravity=Gravity.CENTER_VERTICAL }
        titleRow.addView(label("POINT HISTORY",13f,TEXT,true),LayoutParams(0,LayoutParams.WRAP_CONTENT,1f))
        titleRow.addView(button("All records →","Open full points history",openHistory))
        txCard.addView(titleRow);txCard.addView(summary);txCard.addView(history);addView(txCard)
        val syncCard=card().apply {setPadding(d(14),d(14),d(14),d(14))}
        syncCard.addView(label("DEVICE SYNCHRONIZATION",13f,TEXT,true));syncCard.addView(sync)
        syncCard.addView(button("Manage devices →","Open device center",openDevices))
        addView(syncCard)
        val controls=card().apply {setPadding(d(14),d(10),d(14),d(10))}
        val toggle=Switch(context).apply {
            text="Farm sound effects";textSize=13f;setTextColor(Color.parseColor(TEXT));minHeight=d(48);isChecked=soundEnabled
            setOnCheckedChangeListener { _,enabled -> soundEnabled=enabled;prefs.edit().putBoolean("sound",enabled).apply();if(!enabled)sound.stop() }
        }
        controls.addView(toggle)
        controls.addView(label("Off by default · respects silent mode and audio focus. Visuals work without sound.",11f,MUTED))
        addView(controls)
        addView(label("Small steps today, a greener tomorrow.",13f,GREEN,true).apply {gravity=Gravity.CENTER;setPadding(d(8),d(12),d(8),d(14))})
        refreshData()
    }
    fun setActive(value: Boolean) {
        foreground=value&&!closed
        scene.setActive(foreground)
        ui.removeCallbacks(refresh)
        if(foreground)ui.post(refresh) else sound.stop()
    }
    fun close() { if(closed)return;setActive(false);closed=true;ui.removeCallbacksAndMessages(null);worker.shutdownNow();sound.close() }
    override fun onDetachedFromWindow() { setActive(false);super.onDetachedFromWindow() }
    fun refreshData() {
        if(closed||inFlight)return
        inFlight=true
        worker.execute {
            try {
                val snapshot=repository.load(devMode)
                val state=FarmModel.project(snapshot)
                val environment=FarmWeatherModel.environment(snapshot,state)
                ui.post {
                    inFlight=false
                    if(closed)return@post
                    if(data?.let {snapshot.now<it.now}==true)return@post
                    data=snapshot;projection=state
                    val scope="${snapshot.account}:${if(devMode)"dev" else "prod"}"
                    if(scoped!=scope) {
                        scoped=scope;seen=prefs.getStringSet("seen:$scope",emptySet())?.toSet().orEmpty()
                        initialized=prefs.getBoolean("initialized:$scope",false)
                    }
                    val scoreEvents=FarmWeatherModel.changes(snapshot)
                    val allEffects=(scoreEvents+state.effects).distinctBy {it.id}
                    val fresh=visualSession.fresh(scope,allEffects,seen,initialized,snapshot.now)
                    val nextSeen=seen+allEffects.map {it.id}
                    if(nextSeen!=seen || !initialized)prefs.edit().putStringSet("seen:$scope",nextSeen).putBoolean("initialized:$scope",true).apply()
                    seen=nextSeen
                    initialized=true
                    // Fresh events observed while backgrounded are hydrated silently.
                    val oneShot=if(foreground) fresh.lastOrNull {it.weather==FarmModel.Weather.STORM} ?: fresh.lastOrNull() else null
                    val newDeltas=if(foreground)scoreEvents.filter {e->fresh.any {it.id==e.id}} else emptyList()
                    if(newDeltas.isNotEmpty() && newDeltas.none {it.amount<0})sound.stop() // Recovery cancels pending thunder.
                    scene.bind(snapshot,state,environment,scope,newDeltas,oneShot)
                    if(followToday)anchor=snapshot.now
                    updateCondition(oneShot)
                    renderPeriod()
                }
            } catch(error:Exception) {
                ui.post {inFlight=false;if(!closed){sync.text=if(data==null)"Ledger could not be read. No statistics or farm changes were inferred. Retry from Home." else "Refresh failed. Showing the last loaded ledger; synchronization is not confirmed.";condition.text="Farm refresh unavailable"}}
            }
        }
    }
    private fun updateCondition(effect: FarmModel.Effect?) {
        val snapshot=data?:return;val state=projection?:return
        val phase=FarmModel.lighting(snapshot.now,snapshot.timezone).phase
        val message=when(phase) {FarmModel.Light.SUNRISE->"A new day, a fresh start!";FarmModel.Light.MORNING->"Good morning · small steps, steady growth.";FarmModel.Light.AFTERNOON->"Keep going · your saved progress matters.";FarmModel.Light.SUNSET->"Time to wind down and reflect.";FarmModel.Light.NIGHT->"The farm rests. Tomorrow is another day."}
        greeting.text=message
        condition.text=effect?.message ?: when {
            snapshot.balance<=FarmWeatherModel.Policy.DISASTER_SCORE -> "Severe storm · score ${snapshot.balance} · farm can recover"
            snapshot.balance<0 -> "Cloudy farm · score ${snapshot.balance} · each earned point helps"
            state.condition==FarmModel.Weather.DROUGHT -> "Dry soil · a required slot was missed"
            snapshot.balance<=1 -> "Clouds clearing gently · score ${snapshot.balance}"
            else -> "Calmer skies · earned progress restores the farm"
        }
        val average=state.plots.map {it.growth}.average();val level=1+state.earnedGrowth/40
        progress.text="Level $level · ${(average*100).toInt()}% visual growth\n${state.plots.count {it.growth>.65f}} / 6 mature plots · no extra score points"
        sync.text=snapshot.sync
    }
    private fun shift(amount:Int) { val s=data?:return;followToday=false;historyLimit=40;anchor=FarmModel.move(period,anchor,amount,s.timezone);saveSelection();renderPeriod() }
    private fun saveSelection() {prefs.edit().putString("period",period.name).putLong("anchor",anchor).putBoolean("follow_today",followToday).apply()}
    private fun renderPeriod() {
        val snapshot=data?:return
        val range=FarmModel.range(period,anchor,snapshot.timezone)
        dateLabel.text=range.label
        periodButtons.forEach {(key,view)->view.background=background(if(key==period)GREEN else SURFACE,20);view.setTextColor(Color.parseColor(if(key==period)"#061D1C" else MUTED));view.isSelected=key==period}
        val totals=FarmModel.totals(snapshot,range)
        statistics.removeAllViews()
        val wide=resources.configuration.screenWidthDp>=600 && resources.configuration.fontScale<1.4f
        val cards=listOf(Triple("POINTS EARNED", "+${totals.earned}", GREEN),Triple("POINTS DEDUCTED", "−${totals.deducted}",RED),Triple("NET CHANGE", signed(totals.net), CYAN),Triple("CURRENT BALANCE", "${snapshot.balance} / 100",GOLD))
        val perRow=if(wide)4 else if(resources.configuration.fontScale>=1.4f)1 else 2
        cards.chunked(perRow).forEach { chunk ->
            val row=LinearLayout(context).apply {orientation=HORIZONTAL}
            chunk.forEach { (title,value,accent) -> val box=LinearLayout(context).apply {orientation=VERTICAL;background=background(SURFACE,16);setPadding(d(12),d(13),d(10),d(13))}
                box.addView(label(title,10.5f,MUTED,true));box.addView(label(value,24f,accent,true))
                if(title=="CURRENT BALANCE")box.addView(label("Current day · verified ledger",10f,MUTED))
                row.addView(box,LayoutParams(0,LayoutParams.WRAP_CONTENT,1f).apply {setMargins(d(3),d(3),d(3),d(4))})
            };statistics.addView(row)
        }
        summary.text="${totals.rewards} rewarded · ${totals.partial} partial · ${totals.penalties} deductions\n${totals.missed} missed · ${totals.excused} excused · ${totals.pending} pending\nAPK net ${signed(totals.android)} · EXE net ${signed(totals.windows)}\n${if(totals.resets!=0)"Balance resets ${signed(totals.resets)} · " else ""}Audited adjustments ${signed(totals.adjustments)} included in totals.\nFarm shows current saved state; history below follows this period."
        history.removeAllViews()
        val rows=FarmModel.unique(snapshot.events).filter {it.time>=range.start&&it.time<range.end}.sortedByDescending {it.time}
        if(rows.isEmpty())history.addView(label("No transactions in this period. Complete a task and wait for its confirmed reward.",13f,MUTED).apply {setPadding(0,d(18),0,d(14))})
        rows.take(historyLimit).forEach {tx->history.addView(transactionRow(tx,snapshot.timezone))}
        if(rows.size>historyLimit)history.addView(button("Show more · ${rows.size-historyLimit} remaining","Show more transactions in this period") {historyLimit+=40;renderPeriod()})
    }
    private fun transactionRow(tx:FarmModel.Transaction,zone:String): View {
        val row=LinearLayout(context).apply {orientation=HORIZONTAL;gravity=Gravity.CENTER_VERTICAL;setPadding(d(10),d(12),d(10),d(12));background=background("#10212C",12);isClickable=true;isFocusable=true;layoutParams=LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.WRAP_CONTENT).apply {topMargin=d(7)}}
        val text=LinearLayout(context).apply {orientation=VERTICAL}
        text.addView(label(tx.title,13f,TEXT,true));text.addView(label("${ScheduleClock.display(tx.time,zone,"d MMM · h:mm a")} · ${tx.origin}",10.5f,MUTED));text.addView(label(tx.status,10.5f,if(tx.confirmed)MUTED else GOLD))
        row.addView(text,LayoutParams(0,LayoutParams.WRAP_CONTENT,1f).apply {rightMargin=d(7)})
        row.addView(label(if(tx.confirmed)signed(tx.points) else "Pending",16f,if(!tx.confirmed)GOLD else if(tx.points<0)RED else GREEN,true))
        row.setOnClickListener {AlertDialog.Builder(context).setTitle(tx.title).setMessage("${tx.status}\n\n${ScheduleClock.display(tx.time,zone,"d MMM yyyy · h:mm:ss a")} ($zone)\n${tx.origin}\n${signed(tx.points)} points${if(!tx.confirmed)" (not included in totals)" else ""}\n\n${tx.reason}\n\nType: ${tx.type}\nTransaction: ${tx.id}\nReference: ${tx.reference.ifBlank {"Unavailable"}}${if(tx.partial)"\nPartial award" else ""}").setPositiveButton("Done",null).show()}
        return row
    }
    private fun label(text:String,size:Float,hex:String,bold:Boolean=false)=TextView(context).apply {this.text=text;textSize=size;setTextColor(Color.parseColor(hex));includeFontPadding=false;if(bold)setTypeface(typeface,Typeface.BOLD);setLineSpacing(d(3).toFloat(),1f)}
    private fun card()=LinearLayout(context).apply {orientation=VERTICAL;background=background(SURFACE,20);layoutParams=gapParams()}
    private fun gapParams()=LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.WRAP_CONTENT).apply {bottomMargin=d(12)}
    private fun button(text:String,description:String,click:()->Unit)=label(text,12f,CYAN,true).apply {minHeight=d(48);gravity=Gravity.CENTER;setPadding(d(7),d(7),d(7),d(7));contentDescription=description;isClickable=true;isFocusable=true;setOnClickListener {click()}}
    private fun background(hex:String,radius:Int)=GradientDrawable().apply {setColor(Color.parseColor(hex));cornerRadius=d(radius).toFloat();setStroke(d(1),Color.parseColor("#21424E"))}
    private fun d(value:Int)=(value*resources.displayMetrics.density+.5f).toInt()
    private fun signed(value:Int)=if(value>0)"+$value" else value.toString()
    companion object {private val visualSession=FarmModel.VisualSession();private const val SURFACE="#0C1A25";private const val TEXT="#EDF7F8";private const val MUTED="#99B2C7";private const val GREEN="#00DDAA";private const val CYAN="#37D8F1";private const val RED="#FF6B70";private const val GOLD="#F2C86B"}
}
