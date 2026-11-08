package com.strictrecords.app

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

object PointsCloudUi {
    fun card(activity:Activity,db:StrictRecordsLocalDb,refresh:()->Unit):View {
        val column=LinearLayout(activity).apply{orientation=LinearLayout.VERTICAL;setPadding(24,20,24,20)}
        column.addView(TextView(activity).apply{text="Cloud points";textSize=17f;setTextColor(Color.CYAN)})
        val endpoint=db.getConfigValue("points_cloud_origin","")
        column.addView(TextView(activity).apply{
            text=if(endpoint.isBlank()) "Not configured · Cloud backend required\nLocal records remain available. Taufa reconciliation unavailable."
                else "${db.getConfigValue("points_cloud_status","PENDING")} · Points ${db.getScore(db.nowDate(),false)}\nRevision ${db.getConfigValue("points_cloud_revision","—")} · Last sync ${db.getConfigValue("points_cloud_last_sync","Never")}\nPending changes ${db.getConfigValue("points_cloud_pending","0")}\nReconciliation: ${db.getConfigValue("points_cloud_reconciliation","Pending")}\nTaufa: ${db.getConfigValue("points_cloud_taufa","Awaiting session configuration")}" 
            setTextColor(Color.LTGRAY);textSize=12f;setPadding(0,12,0,12)
        })
        column.addView(Button(activity).apply{text=if(endpoint.isBlank())"Configure cloud endpoint" else "Cloud connection settings";setOnClickListener{
            val input=EditText(activity).apply{setText(endpoint);hint="https://your-points-service.example";inputType=android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI}
            AlertDialog.Builder(activity).setTitle("Cloud points service").setMessage("Use your deployed StrictRecords HTTPS origin. This sends point-event IDs, amounts and verification references using this device's existing account credential. It does not send proof images. No Google account is used. The service must already trust this registered device.")
                .setView(input).setPositiveButton("Save"){_,_->
                    val value=input.text.toString().trim().trimEnd('/')
                    if(value.isBlank() || PointsCloudSync.validOrigin(value)){
                        db.setConfigValue("points_cloud_origin",value);PointsCloudSync.schedule(activity);PointsCloudSync.request(activity){activity.runOnUiThread{refresh()}};refresh()
                    }else android.widget.Toast.makeText(activity,"Enter a valid HTTPS origin",android.widget.Toast.LENGTH_LONG).show()
                }.setNegativeButton("Cancel",null).show()
        }})
        if(endpoint.isNotBlank())column.addView(Button(activity).apply{text="Sync points now";setOnClickListener{PointsCloudSync.request(activity){activity.runOnUiThread{refresh()}}}})
        if(db.getConfigValue("points_cloud_pending","0").toIntOrNull()?.let{it>0}==true)column.addView(Button(activity).apply{
            text="Review pending source claims";setOnClickListener{
                val claims=try{org.json.JSONArray(db.getConfigValue("points_cloud_pending_proposals","[]"))}catch(_:Exception){org.json.JSONArray()}
                val message=(0 until claims.length()).take(100).joinToString("\n"){i->val row=claims.getJSONObject(i);"${row.optString("event_type")} · ${row.optInt("points")} points · ${row.optString("related_task_id",row.optString("event_id"))}"}
                AlertDialog.Builder(activity).setTitle("Awaiting existing verification").setMessage("These claims have not changed your points. Complete or review the corresponding task through the existing proof/admin flow. Cloud sync cannot approve a claim or invent an award.\n\n$message").setPositiveButton("Close",null).show()
            }
        })
        return column
    }
}
