package com.strictrecords.app

import android.app.Activity
import android.app.AlertDialog
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.widget.*
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean

/** Shared settings gallery and launcher configuration. Does not change any business settings. */
class StrictWidgetConfigurationActivity : Activity() {
    private var id = AppWidgetManager.INVALID_APPWIDGET_ID
    private lateinit var body: LinearLayout
    private lateinit var scroll: ScrollView
    private lateinit var selectedPreview: FrameLayout
    private lateinit var optionPanel: LinearLayout
    private var placedPanel: LinearLayout? = null
    private val galleryPreviews = linkedMapOf<StrictWidgetKind,FrameLayout>()
    private var loading = false
    private var saving = false
    private var snapshot = StrictWidgetSnapshot(org.json.JSONObject(),true)
    private var selected = StrictWidgetOptions(StrictWidgetKind.S1)
    private var selectedFallback = StrictWidgetKind.S1
    private val ink = Color.parseColor("#EDF6FF")
    private val cyan = Color.parseColor("#3DCDF7")
    private val muted = Color.parseColor("#ACC4DB")
    private fun dp(n: Int) = (resources.displayMetrics.density*n).toInt()
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        setResult(RESULT_CANCELED)
        id=intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID,AppWidgetManager.INVALID_APPWIDGET_ID)
        if(intent.action == AppWidgetManager.ACTION_APPWIDGET_CONFIGURE && id == AppWidgetManager.INVALID_APPWIDGET_ID) { finish(); return }
        if(id != AppWidgetManager.INVALID_APPWIDGET_ID) {
            val fallback=StrictWidgetUpdates.defaultKind(this,id) ?: run { finish(); return }
            selectedFallback=fallback
            selected=StrictWidgetStore(this).load(id,fallback)
        }
        if(state != null) {
            selected=StrictWidgetStore.decode(state.getString("draft"),selected.kind)
            if(id >= 0) selected=selected.forProvider(selectedFallback)
        }
        window.statusBarColor=Color.parseColor("#07111C")
        window.navigationBarColor=Color.parseColor("#07111C")
        body=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL; setPadding(dp(18),dp(18),dp(18),dp(32)); setBackgroundColor(Color.parseColor("#07111C")) }
        scroll=ScrollView(this).apply { isFillViewport=true; addView(body) }
        setContentView(scroll)
        render()
        val savedScroll=state?.getInt("scroll_y") ?: 0
        scroll.post { scroll.scrollTo(0,savedScroll) }
    }
    override fun onResume() {
        super.onResume()
        if(!::body.isInitialized) return
        refreshPlacedWidgets()
        if(loading) return
        loading=true
        StrictWidgetUpdates.worker.execute {
            val loaded=runCatching {
                StrictWidgetRepository.invalidate()
                StrictWidgetRepository.load(applicationContext)
            }.getOrNull()
            runOnUiThread {
                loading=false
                if(!isFinishing && !isDestroyed && loaded != null) {
                    snapshot=loaded
                    updatePreview(selectedPreview,selected)
                    galleryPreviews.forEach { (kind,holder) -> updatePreview(holder,StrictWidgetOptions(kind)) }
                }
            }
        }
    }
    override fun onSaveInstanceState(out: Bundle) {
        out.putString("draft",StrictWidgetStore.encode(selected))
        if(::scroll.isInitialized) out.putInt("scroll_y",scroll.scrollY)
        super.onSaveInstanceState(out)
    }
    private fun label(text: String, size: Float=14f, color: Int=ink) = TextView(this).apply {
        this.text=text; textSize=size; setTextColor(color); setPadding(0,dp(6),0,dp(6))
    }
    private fun button(text: String, action: () -> Unit) = Button(this).apply {
        this.text=text; isAllCaps=false; setTextColor(ink)
        background=GradientDrawable().apply { setColor(Color.parseColor("#08794F")); cornerRadius=dp(14).toFloat() }
        layoutParams=LinearLayout.LayoutParams(-1,-2).apply { topMargin=dp(10); bottomMargin=dp(6) }
        setPadding(dp(12),dp(10),dp(12),dp(10)); setOnClickListener { action() }
    }
    private fun preview(options: StrictWidgetOptions): FrameLayout {
        val holder=FrameLayout(this)
        updatePreview(holder,options)
        return holder
    }
    private fun updatePreview(holder: FrameLayout, options: StrictWidgetOptions) {
        val small=options.kind.name.startsWith("S")
        val large=options.kind.name.startsWith("L")
        val width=if(small) 180 else (resources.displayMetrics.widthPixels/resources.displayMetrics.density).toInt()-36
        val height=if(large) 400 else 220
        holder.layoutParams=LinearLayout.LayoutParams(if(small) dp(180) else -1,dp(height)).apply { topMargin=dp(8); bottomMargin=dp(12) }
        holder.removeAllViews()
        runCatching {
            val view=StrictWidgetRenderer.render(this,id,options,snapshot,width,height).apply(this,holder)
            // Previews do not execute the placed widget's navigation PendingIntent.
            view.findViewById<View>(R.id.sw_root)?.apply { setOnClickListener(null); isClickable=false }
            holder.addView(view)
        }
            .onFailure { holder.addView(label("Preview unavailable · Saved data will refresh on add")) }
    }
    private fun toggle(text: String, checked: Boolean, update: (Boolean) -> Unit) = CheckBox(this).apply {
        this.text=text; setTextColor(ink); isChecked=checked
        setOnCheckedChangeListener { _,value -> update(value); updatePreview(selectedPreview,selected); updateDependentOptions() }
    }
    private fun updateDependentOptions() {
        val categories=optionPanel.findViewWithTag<CheckBox>("widget_categories")
        categories?.apply { isEnabled=selected.details; alpha=if(isEnabled) 1f else .5f }
    }
    private fun renderOptions() {
        optionPanel.removeAllViews()
        val detailsLabel=when(selected.kind.domain) {
            "tasks" -> if(selected.kind.name.startsWith("S")) "Show submission window" else "Show task details / timeline"
            "points" -> "Show ledger and reward details"
            else -> "Show permission and connection details"
        }
        optionPanel.addView(toggle(detailsLabel,selected.details) { selected=selected.copy(details=it) })
        if(selected.kind in setOf(StrictWidgetKind.M2,StrictWidgetKind.L2)) {
            optionPanel.addView(toggle("Show category summaries",selected.categories) { selected=selected.copy(categories=it) }.apply { tag="widget_categories" })
        }
        if(selected.kind.domain == "points") optionPanel.addView(toggle("Show score progress",selected.progress) { selected=selected.copy(progress=it) })
        if(!selected.kind.name.startsWith("S")) optionPanel.addView(toggle("Compact presentation",selected.compact) { selected=selected.copy(compact=it) })
        optionPanel.addView(toggle("Light theme",selected.light) { selected=selected.copy(light=it) })
        if(selected.kind == StrictWidgetKind.L3) optionPanel.addView(toggle("Show battery percentage",selected.battery) { selected=selected.copy(battery=it) })
        updateDependentOptions()
    }
    private fun refreshPlacedWidgets() {
        val panel=placedPanel ?: return
        panel.removeAllViews()
        val placed=runCatching { StrictWidgetUpdates.instances(this) }.getOrDefault(emptyMap())
        if(placed.isEmpty()) panel.addView(label("No widgets placed yet",13f,muted))
        placed.forEach { (widgetId,fallback) ->
            val options=StrictWidgetStore(this).load(widgetId,fallback)
            panel.addView(button("#${widgetId} · ${options.kind.label}") {
                startActivity(Intent(this,StrictWidgetConfigurationActivity::class.java).putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID,widgetId))
            })
        }
    }
    private fun render() {
        body.removeAllViews()
        body.addView(label("◆ StrictRoutines",15f,cyan))
        body.addView(label(if(id < 0) "Home-screen widgets" else "Configure widget #$id",24f).apply { setTypeface(typeface,Typeface.BOLD) })
        body.addView(label("Minute-resolution summaries from saved app data. Background refresh may be delayed by Android. Tap any widget to open its screen.",13f,muted))
        val kinds=StrictWidgetKind.values().filter { id < 0 || it.cells == selectedFallback.cells }
        val selector=Spinner(this)
        selector.adapter=ArrayAdapter(this,android.R.layout.simple_spinner_dropdown_item,kinds.map { "${it.name} · ${it.label} (${it.cells})" })
        selector.setSelection(kinds.indexOf(selected.kind).coerceAtLeast(0))
        selector.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?) {}
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, itemId: Long) {
                if(selected.kind != kinds[position]) {
                    selected=selected.copy(kind=kinds[position])
                    updatePreview(selectedPreview,selected)
                    renderOptions()
                }
            }
        }
        body.addView(selector)
        selectedPreview=preview(selected).apply { tag="widget_selected_preview" }
        body.addView(selectedPreview)
        body.addView(label("Live preview · Your saved values",12f,muted))
        optionPanel=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL }
        body.addView(optionPanel)
        renderOptions()
        if(id >= 0) {
            body.addView(button("Save widget") {
                if(saving) return@button
                if(StrictWidgetUpdates.defaultKind(this,id)==null) { finish(); return@button }
                saving=true
                StrictWidgetStore(this).save(id,selected)
                StrictWidgetUpdates.worker.execute {
                    runCatching { StrictWidgetUpdates.refresh(applicationContext,intArrayOf(id)) }
                    runOnUiThread { if(!isDestroyed) { setResult(RESULT_OK,Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID,id)); finish() } }
                }
            })
            body.addView(button("Cancel") { finish() })
        } else {
            body.addView(button("Add to Home Screen") { pin() })
            body.addView(label("Manual add: touch and hold an empty area of your home screen → Widgets → StrictRoutines → choose a size → drag it onto your home screen. Resize with the launcher handles. Long-press → Configure where supported.",13f,muted))
            body.addView(label("Placed widgets",19f))
            placedPanel=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL; tag="widget_placed_instances" }
            body.addView(placedPanel)
            refreshPlacedWidgets()
            body.addView(button("Reset Widget Settings") {
                AlertDialog.Builder(this).setTitle("Reset widget preferences?")
                    .setMessage("Restores widget appearance and content defaults. Scores, tasks, evidence and other app settings are preserved.")
                    .setNegativeButton("Cancel",null).setPositiveButton("Reset") { _,_ ->
                        StrictWidgetStore(this).reset(); selected=StrictWidgetOptions(selected.kind)
                        StrictWidgetUpdates.request(this)
                        updatePreview(selectedPreview,selected); renderOptions(); refreshPlacedWidgets()
                    }.show()
            })
            body.addView(label("Gallery · Small / Medium / Large",19f))
            StrictWidgetKind.values().forEach { kind ->
                body.addView(label("${kind.name} · ${kind.label} · ${kind.cells}",16f,cyan))
                val holder=preview(StrictWidgetOptions(kind)).apply { tag="widget_gallery_${kind.name}" }
                galleryPreviews[kind]=holder
                body.addView(holder)
            }
        }
    }
    private fun pin() {
        runCatching { requestPin() }.onFailure {
            Toast.makeText(this,"Could not open the launcher request. Add StrictRoutines from your home screen's Widgets picker.",Toast.LENGTH_LONG).show()
        }
    }
    private fun requestPin() {
        val manager=AppWidgetManager.getInstance(this)
        if(Build.VERSION.SDK_INT < 26 || !manager.isRequestPinAppWidgetSupported) {
            Toast.makeText(this,"Use your home screen's Widgets picker; this launcher does not support pin requests.",Toast.LENGTH_LONG).show(); return
        }
        val token=UUID.randomUUID().toString()
        val callback=Intent(this,StrictWidgetPinReceiver::class.java).apply {
            data=Uri.parse("strictroutines://widget-pin/$token")
            putExtra("options",StrictWidgetStore.encode(selected))
        }
        // Mutable only so the launcher can fill in the new ID; explicit non-exported receiver.
        val flags=PendingIntent.FLAG_ONE_SHOT or (if(Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
        val pending=PendingIntent.getBroadcast(this,0,callback,flags)
        val component=ComponentName(this,StrictWidgetUpdates.providers.getValue(selected.kind))
        val accepted=manager.requestPinAppWidget(component,Bundle().apply {
            putParcelable(AppWidgetManager.EXTRA_APPWIDGET_PREVIEW,StrictWidgetRenderer.render(this@StrictWidgetConfigurationActivity,0,selected,snapshot))
        },pending)
        Toast.makeText(this,if(accepted) "Request sent. Confirm placement in your launcher." else "Launcher declined. Use the Widgets picker.",Toast.LENGTH_LONG).show()
    }
}

class StrictWidgetPinReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id=intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID,AppWidgetManager.INVALID_APPWIDGET_ID)
        val fallback=StrictWidgetUpdates.defaultKind(context,id) ?: return
        StrictWidgetStore(context).save(id,StrictWidgetStore.decode(intent.getStringExtra("options"),fallback).forProvider(fallback))
        val pending=goAsync()
        val released=AtomicBoolean(false)
        val handler=Handler(Looper.getMainLooper())
        val release=Runnable { if(released.compareAndSet(false,true)) pending.finish() }
        handler.postDelayed(release,8_000L)
        StrictWidgetUpdates.worker.execute {
            try { runCatching { StrictWidgetUpdates.refresh(context,intArrayOf(id)) } }
            finally { handler.removeCallbacks(release); release.run() }
        }
        Toast.makeText(context,"Widget placement confirmed by launcher",Toast.LENGTH_SHORT).show()
    }
}
