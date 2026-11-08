package com.strictrecords.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import java.util.concurrent.atomic.AtomicBoolean

/** Runs in the existing visible foreground service, including while the screen is off. */
class BluetoothAutoSyncCoordinator(private val context: Context, private val db: StrictRecordsLocalDb) {
    private val policy = BluetoothAutoSyncPolicy()
    private val stopped = AtomicBoolean(false)
    private val attempting = AtomicBoolean(false)

    fun tick() {
        if (stopped.get() || db.getConfigValue("bluetooth_auto_enabled", "false") != "true" ||
            !policy.isDue(SystemClock.elapsedRealtime()) || BluetoothTransferClient.isBusy()) return
        if (Build.VERSION.SDK_INT >= 31 && context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
            db.setConfigValue("bluetooth_auto_status", "Nearby devices permission required; open Devices to enable it")
            return
        }
        val registration = db.getDeviceRegistration()
        if (registration?.optString("device_token").isNullOrBlank() ||
            registration?.optString("device_id") != db.getConfigValue("bluetooth_auto_device_id", "") ||
            registration?.optString("account_id") != db.getConfigValue("bluetooth_auto_account_id", "")) {
            db.setConfigValue("bluetooth_auto_status", "Register this phone with your PC using QR / code first")
            return
        }
        if (!attempting.compareAndSet(false, true)) return
        db.setConfigValue("bluetooth_auto_status", "Connecting to remembered PC…")
        BluetoothTransferClient.send(context, db, "", db.getConfigValue("bluetooth_auto_pc_address", ""), {},
            { success, message, _ ->
                if (!stopped.get()) {
                    db.setConfigValue("bluetooth_auto_status", message)
                    if (success) policy.succeeded(SystemClock.elapsedRealtime()) else policy.failed(SystemClock.elapsedRealtime())
                }
                attempting.set(false)
            }, automatic = true)
    }

    fun retryNow() { policy.retryNow() }
    fun stop() { stopped.set(true); BluetoothTransferClient.cancelAutomatic() }
}
