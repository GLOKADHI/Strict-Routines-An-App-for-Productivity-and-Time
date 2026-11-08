package com.strictrecords.app

import org.json.JSONObject
import java.util.Locale

/** Testable adapter for a real score_events row, using the same eligibility rule as getScore(). */
object FarmLedgerAdapter {
    fun transaction(id:String,time:Long,date:String,type:String,description:String,points:Int,deviceId:String,
                    audit:JSONObject,retainedProof:Boolean):FarmModel.Transaction {
        val evidence=audit.optString("evidence_id")
        val eligible=points==0 || DisciplineScoringRules.calculateVerifiedDailyScore(listOf(
            DisciplineScoringRules.ScorePointEntry(type,points,evidence,retainedProof)),allowOverflow=true)==points
        val approval=audit.optString("approval_source")
        val authorized=approval in setOf("ANDROID_HUMAN_CONFIRMATION","AUTHENTICATED_HUMAN_ADMIN","ANDROID_ACCESSIBILITY_ENFORCEMENT") && audit.optString("approved_by",audit.optString("Admin")).isNotBlank()
        val fake=type in setOf("FAKE_PROOF_PENALTY","CONFIRMED_FAKE_PROOF","FRAUD_PENALTY") ||
            (type=="PROOF_REJECTED" && audit.optString("decision") in setOf("CONFIRMED_FAKE","FAKE_PROOF_CONFIRMED"))
        val monitoring=type in setOf("RESTRICTED_APP_PENALTY","ENTERTAINMENT_VIOLATION","ACCESSIBILITY_PENALTY")
        val originText=listOf(deviceId,audit.optString("originating_device"),audit.optString("origin_platform"),audit.optString("Device")).joinToString(" ").uppercase(Locale.US)
        val origin=when {
            "WINDOWS" in originText || audit.optString("origin_platform").uppercase(Locale.US)=="EXE" -> "Windows EXE"
            "ANDROID" in originText -> "Android APK"
            "SERVER" in originText || "SYSTEM" in originText -> "Authoritative server"
            "ADMIN" in originText -> "Admin console"
            else -> "Origin unavailable"
        }
        val adjustment=type in setOf("REVERSAL","SCORE_CORRECTION","ADMIN_SCORE_ADJUSTMENT","INITIAL_RESET","DAILY_RESET","RECONCILIATION_COMPENSATION")
        val partial=(type=="MI_FITNESS_SLEEP_BONUS" && audit.optInt("LateHourBands",0)>0) || audit.optBoolean("partial_award",false) || audit.optString("award_status") in setOf("PARTIAL","PARTIALLY_AWARDED") || audit.optJSONObject("taufa_ai")?.optBoolean("is_partial_points",false)==true
        val category=audit.optString("activity_category").ifBlank {audit.optString("category").ifBlank {audit.optString("task_category").ifBlank {audit.optString("task_code").ifBlank {type}}}}
        val title=audit.optString("task_title").ifBlank {audit.optString("activity_title")}.ifBlank {
            if(type=="PROOF_APPROVED" && category!=type) "${LedgerSyncRules.transactionTitle(category)} proof" else LedgerSyncRules.transactionTitle(type)
        }
        return FarmModel.Transaction(id,time,date,type,title,points,eligible,origin,category,
            audit.optString("submission_id").ifBlank {audit.optString("SubmissionId").ifBlank {audit.optString("activity_id").ifBlank {evidence}}},
            audit.optString("reason").ifBlank {audit.optString("Reason").ifBlank {description}},partial,
            points>0 && eligible && !adjustment && (retainedProof || type in setOf("MI_FITNESS_SLEEP_BONUS","POST_EXAM_CLASS_BONUS")),
            points<0 && eligible && authorized && (fake || monitoring),
            audit.optString("reverses_event_id").ifBlank {audit.optString("reversal_of").ifBlank {audit.optString("original_event_id")}},
            if(!eligible)"Pending verification" else if(type=="REVERSAL")"Committed reversal" else if(partial)"Partial reward confirmed" else "Confirmed")
    }
}
