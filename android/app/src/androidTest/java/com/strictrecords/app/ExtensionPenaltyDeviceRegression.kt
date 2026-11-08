package com.strictrecords.app

import android.app.Activity
import android.app.Instrumentation
import android.content.Context
import android.content.ContextWrapper
import android.database.DatabaseErrorHandler
import android.database.sqlite.SQLiteDatabase
import android.os.Bundle
import org.json.JSONObject
import java.io.File

/** Uses only an isolated emulator DB and a signed batch from the real Chrome integration run. */
object ExtensionPenaltyDeviceRegression {
    fun run(instrumentation: Instrumentation) {
        val target=instrumentation.targetContext
        val root=File(target.cacheDir,"extension-penalty-${System.currentTimeMillis()}").apply { mkdirs() }
        val isolated=object: ContextWrapper(target) {
            override fun getApplicationContext(): Context=this
            override fun getFilesDir(): File=File(root,"files").apply { mkdirs() }
            override fun getDatabasePath(name: String): File=File(root,name)
            override fun openOrCreateDatabase(name:String,mode:Int,factory:SQLiteDatabase.CursorFactory?):SQLiteDatabase=SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name),factory)
            override fun openOrCreateDatabase(name:String,mode:Int,factory:SQLiteDatabase.CursorFactory?,errorHandler:DatabaseErrorHandler?):SQLiteDatabase=SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name).path,factory,errorHandler)
        }
        val report=JSONObject()
        try {
            val input=JSONObject(File(target.filesDir,"extension-penalty-input.json").readText())
            val envelope=input.getJSONObject("envelope")
            val registration=input.getJSONObject("registration")
            val payload=JSONObject(envelope.getString("payload_json"))
            val first=payload.getJSONArray("proposals").getJSONObject(0)
            val date=first.getString("date")
            var db=StrictRecordsLocalDb(isolated)
            db.ensureDay(date,false)
            val before=db.getScore(date,false)
            db.writableDatabase.execSQL("CREATE TRIGGER extension_failure BEFORE INSERT ON score_events WHEN NEW.event_type='BROWSER_EXTENSION_DISABLED' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
            check(runCatching { db.applyAutomaticBrowserExtensionBatch(envelope,registration) }.isFailure)
            check(db.getDeductionProposal(first.getString("proposal_id"))==null)
            check(db.getScore(date,false)==before)
            db.writableDatabase.execSQL("DROP TRIGGER extension_failure")
            val expected=payload.getJSONArray("proposals").length()
            check(db.applyAutomaticBrowserExtensionBatch(envelope,registration)==expected)
            check(db.getScore(date,false)==before-expected)
            check(db.applyAutomaticBrowserExtensionBatch(envelope,registration)==0)
            db.saveOrUpdateDeductionProposal(first)
            check(db.getDeductionProposal(first.getString("proposal_id"))!!.getString("status")=="APPROVED")
            db.close();db=StrictRecordsLocalDb(isolated)
            check(db.applyAutomaticBrowserExtensionBatch(envelope,registration)==0)
            check(db.getScore(date,false)==before-expected)
            val snapshot=db.buildAuthoritativeLedgerSnapshot(date,false)
            File(target.filesDir,"extension-penalty-snapshot.json").writeText(snapshot.toString())
            report.put("status","PASSED").put("deductions",expected).put("balance_before",before).put("balance_after",db.getScore(date,false))
                .put("rollback_verified",true).put("retry_restart_dedup_verified",true).put("terminal_decision_preserved",true)
            db.close()
            File(target.filesDir,"extension-penalty-report.json").writeText(report.toString(2))
            instrumentation.finish(Activity.RESULT_OK,Bundle().apply { putString("report",report.toString()) })
        } catch(error:Throwable) {
            report.put("status","FAILED").put("error",error.stackTraceToString())
            File(target.filesDir,"extension-penalty-report.json").writeText(report.toString(2))
            instrumentation.finish(Activity.RESULT_CANCELED,Bundle().apply { putString("report",report.toString()) })
        }
    }
}
