package com.strictrecords.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import org.json.JSONObject

/**
 * Exported BroadcastReceiver that allows instant trigger of Strict Session preparation
 * and prerequisite checks via ADB or inter-process communication.
 */
class StrictSessionCommandReceiver : BroadcastReceiver() {
    companion object {
        private const val TAG = "StrictSessionReceiver"
        const val ACTION_PREPARE_STRICT_SESSION = "com.strictrecords.app.ACTION_PREPARE_STRICT_SESSION"
    }

    override fun onReceive(context: Context?, intent: Intent?) {
        if (context == null || intent == null) return
        val action = intent.action ?: return
        if (action == ACTION_PREPARE_STRICT_SESSION) {
            val prepId = intent.getStringExtra("preparation_id").orEmpty()
            Log.i(TAG, "Received ACTION_PREPARE_STRICT_SESSION (prepId=$prepId)")

            val active = StrictEnforcementService.activeInstance
            if (active != null) {
                val payload = JSONObject().apply {
                    if (prepId.isNotBlank()) put("preparation_id", prepId)
                }
                Thread({
                    active.executeSessionCommand("PREPARE_STRICT_SESSION_CHECK", payload)
                }, "StrictSession-BroadcastAck").start()
            } else {
                // Start enforcement service if not running
                StrictEnforcementService.start(context)
            }
        }
    }
}
