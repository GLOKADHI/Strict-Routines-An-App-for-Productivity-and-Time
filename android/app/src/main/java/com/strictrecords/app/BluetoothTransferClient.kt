package com.strictrecords.app

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothSocket
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** One temporary sender, one explicitly chosen paired PC; never scans other peers or uses HTTP. */
object BluetoothTransferClient {
    private val busy = AtomicBoolean(false)
    private val cancelled = AtomicBoolean(false)
    @Volatile private var currentSocket: BluetoothSocket? = null
    @Volatile private var automaticTransfer = false

    fun isBusy(): Boolean = busy.get()
    fun cancelAutomatic() { if (automaticTransfer && busy.get()) cancel() }

    fun cancel() {
        cancelled.set(true)
        runCatching { currentSocket?.close() }
    }

    /** Discard only the retry snapshot, never records in the application database. */
    fun clearPendingPackage(context: Context): Boolean {
        if (!busy.compareAndSet(false, true)) return false
        return try {
            val pending = File(context.filesDir, "bluetooth-transfer-pending-v2.json")
            !pending.exists() || pending.delete()
        } finally { busy.set(false) }
    }

    fun send(context: Context, db: StrictRecordsLocalDb, code: String, address: String,
             progress: (String) -> Unit, complete: (Boolean, String, JSONObject?) -> Unit, automatic: Boolean = false) {
        val main = Handler(Looper.getMainLooper())
        fun status(text: String) { main.post { progress(text) } }
        if (!busy.compareAndSet(false, true)) {
            main.post { complete(false, "A Bluetooth transfer is already running", null) }; return
        }
        cancelled.set(false)
        automaticTransfer = automatic
        Thread({
            val watchdog = Executors.newSingleThreadScheduledExecutor()
            var success = false; var message = "Transfer did not complete"; var result: JSONObject? = null
            try {
                require(automatic || Regex("[0-9]{6}").matches(code)) { "Enter the current six-digit code from the selected PC" }
                if (Build.VERSION.SDK_INT >= 31 && context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED)
                    throw SecurityException("Nearby devices permission is required for paired Bluetooth transfer")
                val adapter = BluetoothAdapter.getDefaultAdapter() ?: error("This device has no Bluetooth adapter")
                check(adapter.isEnabled) { "Turn on Bluetooth, then retry" }
                val device = adapter.bondedDevices.firstOrNull { it.address.equals(address, true) }
                    ?: error("Pair and explicitly select the intended Windows PC")
                status("Device selected: ${device.name ?: device.address}. Preparing the original package…")
                val outbox = File(context.filesDir, if (automatic) "bluetooth-auto-pending-v1.json" else "bluetooth-transfer-pending-v2.json")
                require(!outbox.exists() || outbox.length() in 1..BluetoothTransferProtocol.MAX_BYTES.toLong()) { "Saved transfer package is invalid; choose Use latest data" }
                val freshPayload = db.buildBluetoothTransferPayload()
                if (automatic) freshPayload.put("planning_requests",GymPolicyClient.pendingBluetoothRequests(db))
                val fingerprint = BluetoothTransferProtocol.contentFingerprint(freshPayload)
                if (automatic && !outbox.exists() && fingerprint == db.getConfigValue("bluetooth_auto_last_fingerprint", "") &&
                    System.currentTimeMillis() - db.getConfigValue("bluetooth_auto_last_commit_ms", "0").toLongOrNull().let { it ?: 0L } in 0L until 300_000L) {
                    success = true
                    message = "Up to date · checking for changes automatically"
                    return@Thread
                }
                val freshSnap = freshPayload.optJSONObject("ledger_snapshot")
                val freshInner = if (freshSnap != null) JSONObject(freshSnap.optString("payload_json", "{}")) else JSONObject()
                val freshRev = freshInner.optLong("revision", 0L)
                val freshEventCount = freshInner.optInt("event_count", 0)

                val payload = if (outbox.isFile) {
                    try {
                        val saved = JSONObject(outbox.readText())
                        val savedDate = saved.optString("date")
                        val savedSnap = saved.optJSONObject("ledger_snapshot")
                        val savedInner = if (savedSnap != null) JSONObject(savedSnap.optString("payload_json", "{}")) else JSONObject()
                        val savedRev = savedInner.optLong("revision", 0L)
                        val savedEventCount = savedInner.optInt("event_count", 0)
                        if (savedDate != freshPayload.optString("date") || freshRev > savedRev || freshEventCount > savedEventCount ||
                            (automatic && BluetoothTransferProtocol.contentFingerprint(saved) != fingerprint)) {
                            outbox.delete()
                            freshPayload
                        } else {
                            saved
                        }
                    } catch (_: Exception) {
                        outbox.delete()
                        freshPayload
                    }
                } else {
                    freshPayload
                }

                if (!outbox.exists()) {
                    val temporary = File(context.filesDir, outbox.name + ".tmp")
                    temporary.writeText(payload.toString())
                    check(temporary.renameTo(outbox)) { "Could not retain the transfer package" }
                }

                check(payload.optString("device_id") == db.getStableDeviceId(context) ||
                    payload.optString("device_id") == db.getDeviceRegistration()?.optString("device_id")) { "Saved package belongs to a different device; review it before retrying" }
                check(payload.optString("account_id") == db.getConfigValue("account_id", "user_lok").ifBlank { "user_lok" }) { "Saved package belongs to a different account; choose Use latest data" }
                val originalRaw = outbox.readText()
                val bytes = originalRaw.toByteArray(Charsets.UTF_8)
                require(bytes.size in 1..BluetoothTransferProtocol.MAX_BYTES) { "Package exceeds the 16 MiB transfer limit" }
                val count = BluetoothTransferProtocol.recordCount(payload)
                require(count <= 10000) { "Package exceeds the configured record limit" }
                val hash = LedgerSyncRules.sha256(originalRaw)
                val registration = db.getDeviceRegistration()
                val authenticationKey = if (automatic) {
                    check(db.getConfigValue("bluetooth_auto_enabled", "false") == "true" &&
                        db.getConfigValue("bluetooth_auto_pc_address", "").equals(address, true)) { "Automatic sync was disabled or its PC changed" }
                    val token = registration?.optString("device_token").orEmpty()
                    require(token.isNotBlank() && registration?.optString("device_id") == payload.getString("device_id")) { "Register this phone with your PC first" }
                    BluetoothTransferProtocol.automaticKey(token, payload.getString("device_id"), payload.getString("account_id"))
                } else code

                val sppUuid = UUID.fromString(if (automatic) BluetoothTransferProtocol.AUTO_UUID else "00001101-0000-1000-8000-00805F9B34FB")
                var socket: BluetoothSocket? = null
                var lastConnectError: Exception? = null

                // Covers connection establishment too; never leave a stuck background connect.
                val deadline = watchdog.schedule({ cancelled.set(true); runCatching { currentSocket?.close() } },
                    if (automatic) 90L else 180L, TimeUnit.SECONDS)
                for (attempt in 1..(if (automatic) 1 else 3)) {
                    check(!cancelled.get()) { "Transfer cancelled; original package retained for retry" }
                    try {
                        val s = when (attempt) {
                            1 -> {
                                status("Connecting to ${device.name ?: device.address}…")
                                device.createRfcommSocketToServiceRecord(sppUuid)
                            }
                            2 -> {
                                status("Retrying connection to ${device.name ?: device.address} (insecure RFCOMM)…")
                                device.createInsecureRfcommSocketToServiceRecord(sppUuid)
                            }
                            else -> {
                                status("Retrying RFCOMM connection to ${device.name ?: device.address}…")
                                try {
                                    val m = device.javaClass.getMethod("createRfcommSocket", Int::class.javaPrimitiveType)
                                    m.invoke(device, 1) as BluetoothSocket
                                } catch (_: Exception) {
                                    device.createInsecureRfcommSocketToServiceRecord(sppUuid)
                                }
                            }
                        }
                        currentSocket = s
                        val connectTimeout = watchdog.schedule({ runCatching { s.close() } }, 15, TimeUnit.SECONDS)
                        try { s.connect() } finally { connectTimeout.cancel(false) }
                        socket = s
                        break
                    } catch (e: Exception) {
                        lastConnectError = e
                        runCatching { currentSocket?.close() }
                        currentSocket = null
                        if (!automatic && attempt < 3 && !cancelled.get()) {
                            android.os.SystemClock.sleep(800)
                        }
                    }
                }
                if (socket == null) {
                    throw lastConnectError ?: java.io.IOException("Could not connect to Bluetooth receiver on ${device.name ?: device.address}")
                }
                currentSocket = socket
                check(!cancelled.get()) { "Transfer cancelled; original package retained for retry" }
                var timeout = watchdog.schedule({ runCatching { socket.close() } }, 25, TimeUnit.SECONDS)
                fun renewTimeout() {
                    timeout.cancel(false)
                    timeout = watchdog.schedule({ runCatching { socket.close() } }, 30, TimeUnit.SECONDS)
                    check(!cancelled.get()) { "Transfer cancelled; the original package is retained for retry" }
                }
                renewTimeout()
                status("Connected. Authenticating the temporary session…")
                val input = DataInputStream(socket.inputStream); val output = DataOutputStream(socket.outputStream)
                val hello = BluetoothTransferProtocol.readFrame(input)
                require(hello.optString("protocol") == BluetoothTransferProtocol.NAME && hello.optInt("version") == 2) { "Update both applications: receiver protocol is incompatible" }
                if (automatic) require(hello.optString("auth_mode") == BluetoothTransferProtocol.AUTO_MODE) { "Enable automatic Bluetooth sync on the updated PC app" }
                val session = hello.getString("session_id")
                val text = BluetoothTransferProtocol.authText(session, hello.getString("challenge"), hash,
                    payload.getString("device_id"), payload.getString("account_id"), bytes.size, count)
                val authentication = JSONObject().put("protocol", BluetoothTransferProtocol.NAME).put("version", 2)
                    .put("session_id", session).put("package_id", hash).put("device_id", payload.getString("device_id"))
                    .put("account_id", payload.getString("account_id")).put("payload_length", bytes.size).put("record_count", count)
                    .put("authenticator", BluetoothTransferProtocol.authenticate(authenticationKey, text))
                if (automatic) authentication.put("auth_mode", BluetoothTransferProtocol.AUTO_MODE).put("credential_tag",
                    LedgerSyncRules.sha256(LedgerSyncRules.sha256(registration!!.getString("device_token"))))
                BluetoothTransferProtocol.writeFrame(output, authentication)
                val accepted = BluetoothTransferProtocol.readFrame(input)
                require(accepted.optString("state") == "AUTHENTICATED" && accepted.optString("session_id") == session && accepted.optString("package_id") == hash) { "Session authentication failed; confirm the code and selected PC" }
                if (automatic) require(java.security.MessageDigest.isEqual(
                    BluetoothTransferProtocol.authenticate(authenticationKey, "RECEIVER\n" + text).toByteArray(Charsets.US_ASCII),
                    accepted.optString("receiver_mac").toByteArray(Charsets.US_ASCII))) { "PC registration could not be verified; no data sent" }
                status("Authenticated. Sending $count records…")
                var offset = 0
                var lastProgress = 0L
                while (offset < bytes.size) {
                    renewTimeout()
                    val amount = minOf(16384, bytes.size - offset)
                    output.write(bytes, offset, amount); output.flush(); offset += amount
                    val now = android.os.SystemClock.elapsedRealtime()
                    if (offset == bytes.size || now - lastProgress >= 150) {
                        status("Transferring: $offset / ${bytes.size} bytes. Waiting for committed receipt after upload.")
                        lastProgress = now
                    }
                }
                renewTimeout()
                val receipt = BluetoothTransferProtocol.readFrame(input)
                check(receipt.optBoolean("success")) { receipt.optString("error", "Receiver could not reconcile this package; no success recorded") }
                check(BluetoothTransferProtocol.acceptsReceipt(receipt, payload, authenticationKey, session, hash)) { "Receipt validation failed; retry the retained original package" }
                if (automatic) check(BluetoothTransferProtocol.acceptsAutomaticReceipt(receipt,payload,authenticationKey,session,hash)) { "Automatic receipt validation failed; original package retained" }
                val localStore=db.writableDatabase
                localStore.beginTransaction()
                try {
                if (automatic) GymPolicyClient.acceptBluetoothPlanning(db,receipt)
                db.setConfigValue("last_bluetooth_transfer", receipt.getString("synced_at"))
                db.setConfigValue("last_bluetooth_transfer_receipt", receipt.toString())
                val scoringDate = receipt.optString("scoring_date").ifBlank { db.nowDate() }
                val ledgerReceipt = receipt.optJSONObject("ledger_receipt")
                if (ledgerReceipt != null) {
                    db.setConfigValue("pc_ledger_ack_$scoringDate", ledgerReceipt.toString())
                    db.setConfigValue("pc_ledger_ack_time", receipt.optString("synced_at", db.nowIso()))
                }
                db.setConfigValue("last_sync_time", db.nowTime())
                db.writableDatabase.execSQL(
                    "UPDATE daily_state SET sync_status='SYNCED ✅' WHERE date=? AND is_dev_mode=0",
                    arrayOf<Any>(scoringDate)
                )
                localStore.setTransactionSuccessful()
                } finally { localStore.endTransaction() }
                outbox.delete()
                if (automatic) {
                    val currentFingerprint=JSONObject(payload.toString()).put("planning_requests",GymPolicyClient.pendingBluetoothRequests(db))
                    db.setConfigValue("bluetooth_auto_last_fingerprint", BluetoothTransferProtocol.contentFingerprint(currentFingerprint))
                    db.setConfigValue("bluetooth_auto_last_commit_ms", System.currentTimeMillis().toString())
                }
                success = true; result = receipt
                message = "Transfer committed: ${receipt.optInt("transferred_count")} new, ${receipt.optInt("already_present_count")} already present. Confirmed balance: ${receipt.optInt("confirmed_score")} points."
                if (receipt.optInt("pending_metadata_count") > 0) message += " ${receipt.optInt("pending_metadata_count")} records retained pending reconciliation."
                timeout.cancel(false); deadline.cancel(false)
            } catch (error: Exception) {
                message = if (cancelled.get()) "Transfer cancelled; original package retained for retry" else error.message ?: "Bluetooth connection failed; retry the original package"
            } finally {
                runCatching { currentSocket?.close() }; currentSocket = null
                watchdog.shutdownNow(); automaticTransfer = false; busy.set(false)
                main.post { complete(success, message, result) }
            }
        }, "StrictRecords-BluetoothTransfer").start()
    }
}
