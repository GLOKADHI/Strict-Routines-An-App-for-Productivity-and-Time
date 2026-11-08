package com.strictrecords.app

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONObject

/** Only widget preferences and display cache live here. No scores/schedules are written. */
class StrictWidgetStore internal constructor(private val prefs: SharedPreferences) {
    constructor(context: Context) : this(context.getSharedPreferences("strict_home_widgets_v1", Context.MODE_PRIVATE))
    fun load(id: Int, fallback: StrictWidgetKind): StrictWidgetOptions = decode(prefs.getString("instance_$id", null), fallback).forProvider(fallback)
    fun save(id: Int, options: StrictWidgetOptions) { prefs.edit().putString("instance_$id", encode(options)).apply() }
    fun remove(id: Int) { prefs.edit().remove("instance_$id").apply() }
    fun reset() { val edit = prefs.edit(); prefs.all.keys.filter { it.startsWith("instance_") }.forEach { edit.remove(it) }; edit.apply() }
    fun move(oldId: Int, newId: Int, fallback: StrictWidgetKind) { save(newId, load(oldId, fallback)); remove(oldId) }
    fun cached(): JSONObject? = runCatching { JSONObject(prefs.getString("display_cache", "") ?: "") }.getOrNull()
    fun cache(value: JSONObject) { prefs.edit().putString("display_cache", value.toString()).apply() }
    companion object {
        fun encode(o: StrictWidgetOptions) = JSONObject().put("kind", o.kind.name).put("categories", o.categories)
            .put("details", o.details).put("progress", o.progress).put("compact", o.compact)
            .put("light", o.light).put("battery", o.battery).toString()
        fun decode(value: String?, fallback: StrictWidgetKind): StrictWidgetOptions = runCatching {
            val j = JSONObject(value ?: "{}")
            StrictWidgetOptions(StrictWidgetKind.parse(j.optString("kind"), fallback), j.optBoolean("categories", true),
                j.optBoolean("details", true), j.optBoolean("progress", true), j.optBoolean("compact"),
                j.optBoolean("light"), j.optBoolean("battery"))
        }.getOrDefault(StrictWidgetOptions(fallback))
    }
}
