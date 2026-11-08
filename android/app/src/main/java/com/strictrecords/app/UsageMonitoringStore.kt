package com.strictrecords.app

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONObject

/** Observation checkpoint and blocked evidence only: this is NOT a point ledger.
 * Cursor and evidence commit together; replay cannot duplicate minute evidence. */
class UsageMonitoringStore(context: Context) : SQLiteOpenHelper(context, "restricted_usage_evidence.db", null, 1) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE checkpoint (owner TEXT PRIMARY KEY, state_json TEXT NOT NULL)")
        db.execSQL("""CREATE TABLE minute_evidence (
            idempotency_key TEXT PRIMARY KEY, account_id TEXT NOT NULL, device_id TEXT NOT NULL,
            package_name TEXT NOT NULL, started_at_ms INTEGER NOT NULL, ended_at_ms INTEGER NOT NULL,
            scoring_date TEXT NOT NULL, timezone TEXT NOT NULL, complete_minutes INTEGER NOT NULL,
            source TEXT NOT NULL, evidence_json TEXT NOT NULL,
            ledger_status TEXT NOT NULL DEFAULT 'BLOCKED_UNSUPPORTED_LEDGER', committed_points INTEGER NOT NULL DEFAULT 0 CHECK(committed_points=0))""")
    }
    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun load(owner: String): JSONObject = readableDatabase.rawQuery(
        "SELECT state_json FROM checkpoint WHERE owner=?", arrayOf(owner)).use {
        if (it.moveToFirst()) runCatching { JSONObject(it.getString(0)) }.getOrDefault(JSONObject()) else JSONObject()
    }

    fun save(owner: String, state: JSONObject, evidence: List<ContentValues>) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            evidence.forEach { db.insertWithOnConflict("minute_evidence", null, it, SQLiteDatabase.CONFLICT_IGNORE) }
            db.insertWithOnConflict("checkpoint", null, ContentValues().apply {
                put("owner", owner); put("state_json", state.toString())
            }, SQLiteDatabase.CONFLICT_REPLACE)
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
    }
}
