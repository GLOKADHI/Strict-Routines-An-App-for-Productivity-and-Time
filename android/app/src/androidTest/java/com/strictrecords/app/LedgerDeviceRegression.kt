package com.strictrecords.app

import android.app.Activity
import android.app.Instrumentation
import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import android.database.DatabaseErrorHandler
import android.database.sqlite.SQLiteDatabase
import android.os.Bundle
import org.json.JSONObject
import java.io.File

/** Actual Android SQLite checks in a separate database, files directory and preference namespace. */
object LedgerDeviceRegression {
    fun run(instrumentation: Instrumentation) {
        val target=instrumentation.targetContext
        val folder=File(target.filesDir,"ledger-regression-${System.currentTimeMillis()}").apply { mkdirs() }
        val isolated=object: ContextWrapper(target) {
            override fun getApplicationContext(): Context = this
            override fun getFilesDir(): File = File(folder,"files").apply { mkdirs() }
            override fun getDatabasePath(name: String): File = File(folder,name)
            override fun getSharedPreferences(name: String,mode: Int): SharedPreferences = target.getSharedPreferences("ledger-fixture-$name",mode)
            override fun openOrCreateDatabase(name: String,mode: Int,factory: SQLiteDatabase.CursorFactory?): SQLiteDatabase = SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name),factory)
            override fun openOrCreateDatabase(name: String,mode: Int,factory: SQLiteDatabase.CursorFactory?,handler: DatabaseErrorHandler?): SQLiteDatabase = SQLiteDatabase.openOrCreateDatabase(getDatabasePath(name).path,factory,handler)
        }
        var count=0
        val report=JSONObject().put("isolated_database",true).put("physical_phone_used",false)
        fun verify(value: Boolean,message: String) { check(value) { message };count++ }
        try {
            val db=StrictRecordsLocalDb(isolated);val date="2026-10-17"
            val gymBytes=instrumentation.context.assets.open("audit-gym-workout.jpg").use { it.readBytes() }
            val observed=db.analyzeAndRecordEvidence(
                "audit-ocr-only", "audit-submission", "audit-occurrence", "2026-10-13",
                category="GYM", bytes=gymBytes, fileName="gym-fixture.jpg")
            verify(observed.optInt("points_awarded")==0,"OCR/novelty awarded points before task validation")
            verify(observed.optString("ocr_status")=="OCR_COMPLETED","Real ML Kit OCR did not complete")
            val gym=MiFitnessGymWorkoutValidator.validate(
                observed.optString("ocr_text"), defaultYear=2026, claimedDate="2026-10-13",
                sourceImageId="audit-ocr-only", sourceIntegrityHash=observed.optString("sha256_hash"))
            verify(gym.workoutDate=="2026-10-13","Real OCR workout date mismatch")
            verify(gym.durationSeconds==3841,"Real OCR duration mismatch")
            verify(gym.workoutCalories==348 && gym.totalCalories==444,"Real OCR calorie fields mixed")
            report.put("real_gym_ocr",gym.toJson())
            val proofFile=File(isolated.filesDir,"sleep-fixture.jpg").apply { writeBytes(byteArrayOf(1,2,3)) }
            verify(db.saveInitialSleepSubmission("sleep-original",date,false,"fixture-hash",proofFile.path,1792197000000L,"sleep-eval"),"Sleep acceptance was not committed")
            verify(db.getProofEvaluationByActivity("act_sleep_$date",date,false)?.optString("submission_id")=="sleep-original","Sleep queue was not committed with its submission")
            verify(!db.saveInitialSleepSubmission("sleep-second",date,false,"replacement",proofFile.path,1792197001000L,"sleep-eval-2"),"Accepted sleep screenshot was replaced")
            verify(db.getSleepSubmission(date,false)?.optString("submission_id")=="sleep-original","Original sleep submission lost")
            verify(db.saveInitialSleepSubmission("sleep-demo",date,true,"demo-hash",proofFile.path,1792197000000L,"sleep-eval-demo"),"Demo namespace collided with real sleep")
            verify(db.getProofEvaluationByActivity("act_sleep_$date",date,true)?.optString("submission_id")=="sleep-demo","Demo proof query leaked real evaluation")
            // Deliberately unavailable evaluation table proves both acceptance writes roll back.
            db.writableDatabase.execSQL("ALTER TABLE proof_evaluations RENAME TO proof_evaluations_fixture_hidden")
            verify(!db.saveInitialSleepSubmission("sleep-fail","2026-10-19",false,"bad",proofFile.path,1792197000000L,"sleep-fail-eval"),"Missing queue still returned acceptance")
            verify(db.getSleepSubmission("2026-10-19",false)==null,"Partial sleep acceptance survived rollback")
            db.writableDatabase.execSQL("ALTER TABLE proof_evaluations_fixture_hidden RENAME TO proof_evaluations")
            db.ensureDay(date,false)
            verify(db.getScoreEvents(date,false).let { e -> (0 until e.length()).none { e.getJSONObject(it).optString("event_type")=="INITIAL_RESET" } },"Opening a day performed an initial reset")
            db.writableDatabase.execSQL("INSERT INTO score_events(event_id,date,is_dev_mode,timestamp,event_type,description,points,audit_json) VALUES(?,?,0,?,'INITIAL_RESET','User-requested initial points reset',0,?)",arrayOf("initial-fixture",date,"$date 09:00:00",JSONObject().put("action","INITIAL_POINTS_RESET").put("reason","User-requested reset").toString()))
            db.addScoreEvent(date,false,"MI_FITNESS_SLEEP_BONUS","Mi Fitness Sleep Bonus: 4 pts (Partial - Onset 12:33 AM, Wake 9:24 AM)",4,JSONObject().put("evidence_id","sleep-proof"))
            db.setConfigValue("server_shared_score","15");db.setConfigValue("server_shared_score_date",date);db.setConfigValue("server_verified_points","15")
            verify(db.getScore(date,false)==4,"Windows cache replaced local score")
            val state=db.getDayState(date,false)
            verify(state.getInt("score")==4&&state.getInt("verified_points")==4,"Home, Tasks and Rewards projection disagrees")
            verify(db.getUnifiedTransactionHistory(date).getInt("current_confirmed_balance")==4,"History differs")
            val snapshot=db.buildAuthoritativeLedgerSnapshot(date);val first=JSONObject(snapshot.getString("payload_json"))
            verify(first.getInt("confirmed_score")==4&&first.getInt("raw_verified_points")==4,"Snapshot differs from persisted ledger")
            verify(snapshot.getString("sha256")==db.buildAuthoritativeLedgerSnapshot(date).getString("sha256"),"Retry changed checkpoint")
            verify(first.getJSONArray("score_events").length()==db.getScoreEvents(date,false).length(),"Snapshot omitted records")
            verify(DisciplineScoringRules.evaluateRestrictionDecision(4,"com.google.android.youtube").isRestricted,"YouTube threshold changed")
            verify(DisciplineScoringRules.evaluateRestrictionDecision(4,"com.spotify.music").isRestricted,"Music threshold changed")
            File(target.filesDir,"points-ledger-android-snapshot.json").writeText(snapshot.toString())
            db.addScoreEvent(date,false,"ENTERTAINMENT_VIOLATION","Proposed violation",-7,JSONObject())
            verify(db.getScore(date,false)==4,"Pending deduction changed score")
            db.addScoreEvent(date,false,"ENTERTAINMENT_VIOLATION","Human-confirmed violation",-7,JSONObject().put("approval_source","ANDROID_HUMAN_CONFIRMATION").put("approved_by","test-human"))
            verify(db.getScore(date,false)==-3,"Confirmed deduction missing")
            File(target.filesDir,"points-ledger-android-negative.json").writeText(db.buildAuthoritativeLedgerSnapshot(date).toString())
            verify(!db.executeInitialResetIfNeeded(date,false).optBoolean("reset_applied")&&db.getScore(date,false)==-3,"Reset boundary applied twice")
            db.ensureDay("2026-10-18",false)
            verify(db.getScore("2026-10-18",false)==0,"Daily reset carried previous score")
            verify(db.getScore(date,false)==-3,"Daily reset destroyed history")
            verify(db.getScoreEvents(date,false).length()>=4,"History lost")
            report.put("checks_passed",count).put("initial_confirmed_score",4).put("human_confirmed_score",-3).put("stale_windows_cache",15).put("daily_reset_score",0).put("snapshot",snapshot)
            File(target.filesDir,"points-ledger-android-report.json").writeText(report.toString(2));db.close()
            instrumentation.finish(Activity.RESULT_OK,Bundle().apply { putString("ledger_report",report.toString()) })
        } catch(error: Throwable) {
            report.put("checks_passed",count).put("error",error.stackTraceToString())
            File(target.filesDir,"points-ledger-android-report.json").writeText(report.toString(2))
            instrumentation.finish(Activity.RESULT_CANCELED,Bundle().apply { putString("error",error.stackTraceToString()) })
        }
    }
}
