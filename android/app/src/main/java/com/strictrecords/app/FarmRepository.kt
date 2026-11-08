package com.strictrecords.app

import android.database.Cursor
import org.json.JSONObject
import java.util.Locale

/** Read adapter for the existing APK ledger. It never imports or awards records. */
class FarmRepository(private val db: StrictRecordsLocalDb) {
    private fun Cursor.text(key: String): String = getColumnIndex(key).let { if (it < 0 || isNull(it)) "" else getString(it).orEmpty() }
    private fun Cursor.number(key: String): Int = getColumnIndex(key).let { if (it < 0 || isNull(it)) 0 else getInt(it) }
    private fun json(raw: String) = runCatching { JSONObject(raw) }.getOrDefault(JSONObject())

    private data class Row(val id:String,val time:Long,val date:String,val type:String,val description:String,
                           val points:Int,val device:String,val audit:JSONObject)
    private var cachedScope=""
    private var cachedRevision=""
    private var cachedRows=emptyList<Row>()

    fun load(dev: Boolean): FarmModel.Snapshot {
        val store = db.readableDatabase
        val account = db.getDeviceRegistration()?.optString("account_id").orEmpty().ifBlank { db.getConfigValue("account_id", "user_lok").ifBlank { "user_lok" } }
        val now = db.authoritativeNowMs()
        val timezone = db.scheduleTimezone()
        val scoreDate = ScheduleClock.display(now,timezone,"yyyy-MM-dd")
        val events = mutableListOf<FarmModel.Transaction>()
        val slots = mutableListOf<FarmModel.Slot>()
        store.beginTransactionNonExclusive()
        try {
            val args=arrayOf(if(dev)"1" else "0",account)
            // Immutable ledger rows change by append/reversal. Probe a compact revision, not a full
            // ledger decode on every clock/foreground refresh. Proof eligibility is rechecked below.
            val revision=store.rawQuery("SELECT COUNT(*),COALESCE(MAX(rowid),0),COALESCE(SUM(points),0),COALESCE(SUM(LENGTH(audit_json)),0) FROM score_events WHERE is_dev_mode=? AND account_id=?",args).use {c->
                c.moveToFirst();(0..3).joinToString(":"){c.getLong(it).toString()}
            }
            val scope="$account:$dev"
            if(scope!=cachedScope || revision!=cachedRevision) {
                val rows=mutableListOf<Row>()
                // No LIMIT: selected-period totals must never silently omit older records.
                store.rawQuery("SELECT * FROM score_events WHERE is_dev_mode=? AND account_id=? ORDER BY timestamp,event_id",args).use {c->
                    while(c.moveToNext()) rows+=Row(c.text("event_id"),db.parseIsoToMs(c.text("timestamp")),c.text("date"),c.text("event_type"),c.text("description"),c.number("points"),c.text("device_id"),json(c.text("audit_json")))
                }
                cachedRows=rows;cachedScope=scope;cachedRevision=revision
            }
            cachedRows.forEach {row->
                val proof=row.audit.optString("evidence_id")
                events+=FarmLedgerAdapter.transaction(row.id,row.time,row.date,row.type,row.description,row.points,row.device,row.audit,
                    row.points>0 && proof.isNotBlank() && db.hasApprovedRetainedProof(proof))
            }
            val pending = mutableSetOf<String>()
            store.rawQuery("SELECT activity_id,submitted_at FROM proof_evaluations WHERE is_dev_mode=? AND status NOT IN ('REJECTED','APPROVED','VERIFIED','FAILED')", arrayOf(if (dev) "1" else "0")).use { c ->
                while (c.moveToNext()) if (c.text("submitted_at").isNotBlank()) pending += c.text("activity_id")
            }
            store.rawQuery("SELECT * FROM scheduled_activities WHERE account_id=? ORDER BY window_end,activity_id", arrayOf(account)).use { c ->
                while (c.moveToNext()) {
                    val details = json(c.text("details_json"))
                    if (details.has("is_dev_mode") && details.optBoolean("is_dev_mode") != dev) continue
                    // The existing shared schedule has no dev column: do not project its misses into demo mode.
                    if (dev && !details.optBoolean("is_dev_mode")) continue
                    val status = c.text("status").uppercase(Locale.US)
                    val end = db.parseIsoToMs(c.text("window_end"))
                    val submitted = c.text("submitted_at").takeIf { it.isNotBlank() }?.let { db.parseIsoToMs(it) } ?: 0L
                    val required = c.text("type") in setOf("WAKE_UP", "WAKEUP", "HOURLY", "BATH", "GYM") &&
                        !details.optBoolean("optional") && details.optBoolean("required", true) && c.text("validation_rule") != "OPTIONAL"
                    val timely = status !in setOf("REJECTED", "REJECTED_LATE") &&
                        submitted > 0 && submitted <= end && (c.text("activity_id") in pending || status in setOf("SUBMITTED", "VALIDATING", "PENDING_SYNC", "MISSED"))
                    slots += FarmModel.Slot(c.text("activity_id"), c.text("date"), end, status, required, timely && status !in setOf("VERIFIED", "COMPLETED"))
                }
            }
            val balance = db.getScore(scoreDate, dev) // Existing authority, using the snapshot's same midnight boundary.
            val acknowledged = db.getConfigValue("pc_ledger_ack_time", "")
            val hasWindows = events.any { it.origin == "Windows EXE" && it.confirmed }
            val sync = (if (hasWindows) "Includes confirmed Windows-origin records saved in the Android ledger."
                else if (acknowledged.isNotBlank()) "PC acknowledged Android ledger at $acknowledged. Windows-origin rewards are not available."
                else "Local Android ledger · PC synchronization is not confirmed. Windows-origin rewards are unavailable.") +
                if (events.any { it.time <= 0 }) " Some original timestamps are unavailable and cannot be assigned to a period." else ""
            store.setTransactionSuccessful()
            return FarmModel.Snapshot(account, events, slots, balance, now, timezone, sync, complete = events.none { it.time <= 0 })
        } finally { store.endTransaction() }
    }
}
