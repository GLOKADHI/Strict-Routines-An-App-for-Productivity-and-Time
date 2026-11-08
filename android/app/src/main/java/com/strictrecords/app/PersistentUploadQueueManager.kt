package com.strictrecords.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import androidx.core.app.NotificationCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.OutputStreamWriter
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min

/**
 * Part B (Sections 7–12): Durable Local Upload Queue, Resumable Chunked Upload Engine,
 * and Server-Side AI Validation Reconciliation Manager.
 *
 * Continues reliably when the APK is minimized, moved to the background, or the phone
 * screen is locked.
 */
object PersistentUploadQueueManager {
    private const val TAG = "PersistentUploadQueue"
    const val TABLE_NAME = "persistent_upload_queue"
    const val CHANNEL_ID = "strictrecords_proof_upload_channel"
    const val NOTIFICATION_ID_BASE = 95200
    const val JOB_ID_EXPEDITED_UPLOAD = 94102
    const val CHUNK_SIZE_BYTES = 256 * 1024 // 256 KB resumable chunks

    // Section 7: 13 Authoritative Upload & Validation States
    object States {
        const val CREATED = "CREATED"
        const val QUEUED = "QUEUED"
        const val UPLOADING = "UPLOADING"
        const val UPLOAD_PAUSED = "UPLOAD_PAUSED"
        const val UPLOAD_RETRY_PENDING = "UPLOAD_RETRY_PENDING"
        const val UPLOADED = "UPLOADED"
        const val SERVER_PROCESSING = "SERVER_PROCESSING"
        const val AI_VALIDATION_PENDING = "AI_VALIDATION_PENDING"
        const val AI_VALIDATING = "AI_VALIDATING"
        const val APPROVED = "APPROVED"
        const val REJECTED = "REJECTED"
        const val MANUAL_REVIEW = "MANUAL_REVIEW"
        const val COMPLETED = "COMPLETED"

        val TERMINAL_STATES = setOf(APPROVED, REJECTED, MANUAL_REVIEW, COMPLETED)
        val NEEDS_UPLOAD_STATES = setOf(CREATED, QUEUED, UPLOADING, UPLOAD_PAUSED, UPLOAD_RETRY_PENDING)
        val NEEDS_RECONCILE_STATES = setOf(UPLOADED, SERVER_PROCESSING, AI_VALIDATION_PENDING, AI_VALIDATING)
    }

    data class QueuedUploadRecord(
        val submissionId: String,
        val sessionId: String,
        val activityId: String,
        val taskType: String,
        val date: String,
        val category: String,
        val description: String,
        val evidenceFileReference: String,
        val fileName: String,
        val fileSize: Long,
        val mimeType: String,
        val sha256Checksum: String,
        val createdAt: String,
        val uploadOffset: Long,
        val retryCount: Int,
        val lastUploadError: String,
        val state: String,
        val serverAcknowledgement: String,
        val aiValidationStatus: String,
        val pointsTransactionStatus: String,
        val pointsAwarded: Int = 0
    ) {
        val progressPercent: Int
            get() = when (state) {
                States.CREATED, States.QUEUED -> 10
                States.UPLOADING, States.UPLOAD_PAUSED, States.UPLOAD_RETRY_PENDING -> {
                    if (fileSize > 0L) (15 + ((uploadOffset.toDouble() / fileSize.toDouble()) * 50.0).toInt()).coerceIn(15, 65)
                    else 25
                }
                States.UPLOADED, States.SERVER_PROCESSING -> 70
                States.AI_VALIDATION_PENDING, States.AI_VALIDATING -> 85
                States.APPROVED, States.REJECTED, States.MANUAL_REVIEW, States.COMPLETED -> 100
                else -> 10
            }

        fun toJson(): JSONObject = JSONObject().apply {
            put("submission_id", submissionId)
            put("session_id", sessionId)
            put("activity_id", activityId)
            put("task_type", taskType)
            put("date", date)
            put("category", category)
            put("description", description)
            put("evidence_file_reference", evidenceFileReference)
            put("file_name", fileName)
            put("file_size", fileSize)
            put("mime_type", mimeType)
            put("sha256_checksum", sha256Checksum)
            put("created_at", createdAt)
            put("upload_offset", uploadOffset)
            put("retry_count", retryCount)
            put("last_upload_error", lastUploadError)
            put("state", state)
            put("server_acknowledgement", serverAcknowledgement)
            put("ai_validation_status", aiValidationStatus)
            put("points_transaction_status", pointsTransactionStatus)
            put("points_awarded", pointsAwarded)
            put("progress_percent", progressPercent)
        }
    }

    private val worker = Executors.newSingleThreadExecutor { r ->
        Thread(r, "StrictRecords-DurableUploadWorker").apply { isDaemon = true }
    }
    private val mainHandler = Handler(Looper.getMainLooper())
    private val processingActive = AtomicBoolean(false)
    private val listeners = CopyOnWriteArrayList<(QueuedUploadRecord) -> Unit>()

    fun addListener(listener: (QueuedUploadRecord) -> Unit) {
        if (!listeners.contains(listener)) {
            listeners.add(listener)
        }
    }

    fun removeListener(listener: (QueuedUploadRecord) -> Unit) {
        listeners.remove(listener)
    }

    private fun notifyListeners(record: QueuedUploadRecord) {
        mainHandler.post {
            for (l in listeners) {
                runCatching { l(record) }
            }
        }
    }

    @Synchronized
    fun ensureTable(db: StrictRecordsLocalDb) {
        db.writableDatabase.execSQL(
            """
            CREATE TABLE IF NOT EXISTS $TABLE_NAME (
                submission_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL DEFAULT '',
                activity_id TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT 'HOURLY',
                date TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'STUDY',
                description TEXT NOT NULL DEFAULT '',
                evidence_file_reference TEXT NOT NULL,
                file_name TEXT NOT NULL DEFAULT 'evidence.jpg',
                file_size INTEGER NOT NULL DEFAULT 0,
                mime_type TEXT NOT NULL DEFAULT 'image/jpeg',
                sha256_checksum TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                upload_offset INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_upload_error TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'CREATED',
                server_acknowledgement TEXT NOT NULL DEFAULT '',
                ai_validation_status TEXT NOT NULL DEFAULT 'PENDING',
                points_transaction_status TEXT NOT NULL DEFAULT 'PENDING',
                points_awarded INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """.trimIndent()
        )
    }

    /**
     * Copies evidence bytes into app-private durable storage (`filesDir/durable_proofs/`)
     * and inserts a durable queue entry in SQLite before starting background upload.
     */
    @Synchronized
    fun enqueueSubmission(
        context: Context,
        db: StrictRecordsLocalDb? = null,
        submissionId: String,
        activityId: String,
        taskType: String,
        date: String,
        category: String,
        description: String,
        fileBytes: ByteArray,
        fileName: String,
        existingFilePath: String? = null
    ): QueuedUploadRecord {
        val app = context.applicationContext
        val db = StrictRecordsLocalDb.getInstance(app)
        ensureTable(db)

        val durableDir = File(app.filesDir, "durable_proofs").apply { mkdirs() }
        val ext = fileName.substringAfterLast('.', "jpg").lowercase(Locale.US)
        val durableFile = File(durableDir, "$submissionId.$ext")

        if (fileBytes.isNotEmpty()) {
            val tmpFile = File(durableDir, "$submissionId.$ext.tmp")
            tmpFile.writeBytes(fileBytes)
            if (!tmpFile.renameTo(durableFile)) {
                durableFile.writeBytes(fileBytes)
                tmpFile.delete()
            }
        } else if (!existingFilePath.isNullOrBlank()) {
            val src = File(existingFilePath)
            if (src.exists() && src.length() > 0L && src.absolutePath != durableFile.absolutePath) {
                src.copyTo(durableFile, overwrite = true)
            }
        }

        val actualBytes = if (durableFile.exists()) durableFile.readBytes() else fileBytes
        val sha256 = if (actualBytes.isNotEmpty()) DisciplineScoringRules.computeSha256Hex(actualBytes) else ""
        val mimeType = when (ext) {
            "png" -> "image/png"
            "webp" -> "image/webp"
            "gif" -> "image/gif"
            else -> "image/jpeg"
        }
        val sessionId = AndroidStrictMonitoringService.currentSessionId.ifBlank { "daily_${date}" }
        val nowIso = db.nowIso()

        val cv = ContentValues().apply {
            put("submission_id", submissionId)
            put("session_id", sessionId)
            put("activity_id", activityId)
            put("task_type", taskType.uppercase(Locale.US))
            put("date", date)
            put("category", category.uppercase(Locale.US))
            put("description", description)
            put("evidence_file_reference", durableFile.absolutePath)
            put("file_name", fileName.ifBlank { "evidence.jpg" })
            put("file_size", actualBytes.size.toLong())
            put("mime_type", mimeType)
            put("sha256_checksum", sha256)
            put("created_at", nowIso)
            put("upload_offset", 0L)
            put("retry_count", 0)
            put("last_upload_error", "")
            put("state", States.QUEUED)
            put("server_acknowledgement", "")
            put("ai_validation_status", "PENDING")
            put("points_transaction_status", "PENDING")
            put("points_awarded", 0)
            put("updated_at", nowIso)
        }
        db.writableDatabase.insertWithOnConflict(
            TABLE_NAME,
            null,
            cv,
            android.database.sqlite.SQLiteDatabase.CONFLICT_REPLACE
        )

        val record = getSubmissionRecord(app, submissionId) ?: QueuedUploadRecord(
            submissionId = submissionId,
            sessionId = sessionId,
            activityId = activityId,
            taskType = taskType.uppercase(Locale.US),
            date = date,
            category = category.uppercase(Locale.US),
            description = description,
            evidenceFileReference = durableFile.absolutePath,
            fileName = fileName,
            fileSize = actualBytes.size.toLong(),
            mimeType = mimeType,
            sha256Checksum = sha256,
            createdAt = nowIso,
            uploadOffset = 0L,
            retryCount = 0,
            lastUploadError = "",
            state = States.QUEUED,
            serverAcknowledgement = "",
            aiValidationStatus = "PENDING",
            pointsTransactionStatus = "PENDING",
            pointsAwarded = 0
        )

        notifyListeners(record)
        postUploadStatusNotification(
            app,
            submissionId,
            "Proof Queued for Upload",
            "Evidence saved locally (${actualBytes.size / 1024} KB). Preparing background upload..."
        )
        scheduleExpeditedUploadJob(app)
        triggerQueueProcessingAsync(app)
        return record
    }

    fun getSubmissionRecord(context: Context, submissionId: String): QueuedUploadRecord? {
        val db = StrictRecordsLocalDb.getInstance(context.applicationContext)
        ensureTable(db)
        return db.readableDatabase.rawQuery(
            "SELECT * FROM $TABLE_NAME WHERE submission_id = ?",
            arrayOf(submissionId)
        ).use { cursor ->
            if (cursor.moveToFirst()) readCursorRow(cursor) else null
        }
    }

    fun getPendingRecords(context: Context): List<QueuedUploadRecord> {
        val db = StrictRecordsLocalDb.getInstance(context.applicationContext)
        ensureTable(db)
        val list = mutableListOf<QueuedUploadRecord>()
        db.readableDatabase.rawQuery(
            "SELECT * FROM $TABLE_NAME WHERE state NOT IN ('APPROVED', 'REJECTED', 'MANUAL_REVIEW', 'COMPLETED') ORDER BY created_at ASC",
            null
        ).use { cursor ->
            while (cursor.moveToNext()) {
                list.add(readCursorRow(cursor))
            }
        }
        return list
    }

    private fun readCursorRow(cursor: android.database.Cursor): QueuedUploadRecord {
        fun str(col: String, def: String = ""): String {
            val idx = cursor.getColumnIndex(col)
            return if (idx >= 0 && !cursor.isNull(idx)) cursor.getString(idx) ?: def else def
        }
        fun lng(col: String, def: Long = 0L): Long {
            val idx = cursor.getColumnIndex(col)
            return if (idx >= 0 && !cursor.isNull(idx)) cursor.getLong(idx) else def
        }
        fun intVal(col: String, def: Int = 0): Int {
            val idx = cursor.getColumnIndex(col)
            return if (idx >= 0 && !cursor.isNull(idx)) cursor.getInt(idx) else def
        }
        return QueuedUploadRecord(
            submissionId = str("submission_id"),
            sessionId = str("session_id"),
            activityId = str("activity_id"),
            taskType = str("task_type", "HOURLY"),
            date = str("date"),
            category = str("category", "STUDY"),
            description = str("description"),
            evidenceFileReference = str("evidence_file_reference"),
            fileName = str("file_name", "evidence.jpg"),
            fileSize = lng("file_size", 0L),
            mimeType = str("mime_type", "image/jpeg"),
            sha256Checksum = str("sha256_checksum"),
            createdAt = str("created_at"),
            uploadOffset = lng("upload_offset", 0L),
            retryCount = intVal("retry_count", 0),
            lastUploadError = str("last_upload_error"),
            state = str("state", States.QUEUED),
            serverAcknowledgement = str("server_acknowledgement"),
            aiValidationStatus = str("ai_validation_status", "PENDING"),
            pointsTransactionStatus = str("points_transaction_status", "PENDING"),
            pointsAwarded = intVal("points_awarded", 0)
        )
    }

    @Synchronized
    private fun updateRecordFields(
        context: Context,
        submissionId: String,
        state: String? = null,
        uploadOffset: Long? = null,
        retryCount: Int? = null,
        lastError: String? = null,
        serverAck: String? = null,
        aiValidationStatus: String? = null,
        pointsTxStatus: String? = null,
        pointsAwarded: Int? = null
    ): QueuedUploadRecord? {
        val db = StrictRecordsLocalDb.getInstance(context.applicationContext)
        ensureTable(db)
        val cv = ContentValues().apply {
            state?.let { put("state", it) }
            uploadOffset?.let { put("upload_offset", it) }
            retryCount?.let { put("retry_count", it) }
            lastError?.let { put("last_upload_error", it) }
            serverAck?.let { put("server_acknowledgement", it) }
            aiValidationStatus?.let { put("ai_validation_status", it) }
            pointsTxStatus?.let { put("points_transaction_status", it) }
            pointsAwarded?.let { put("points_awarded", it) }
            put("updated_at", db.nowIso())
        }
        db.writableDatabase.update(TABLE_NAME, cv, "submission_id = ?", arrayOf(submissionId))
        val updated = getSubmissionRecord(context, submissionId)
        if (updated != null) {
            notifyListeners(updated)
        }
        return updated
    }

    /**
     * Schedules an expedited Android JobScheduler job so the OS keeps the upload alive
     * even when the user minimizes the APK or locks the phone screen.
     */
    fun scheduleExpeditedUploadJob(context: Context) {
        val app = context.applicationContext
        try {
            val scheduler = app.getSystemService(Context.JOB_SCHEDULER_SERVICE) as? JobScheduler ?: return
            val component = ComponentName(app, ProofUploadJobService::class.java)
            val builder = JobInfo.Builder(JOB_ID_EXPEDITED_UPLOAD, component)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                try {
                    builder.setExpedited(true)
                } catch (_: Exception) {
                    builder.setOverrideDeadline(5_000L)
                }
            } else {
                builder.setOverrideDeadline(5_000L)
            }
            scheduler.schedule(builder.build())
        } catch (e: Exception) {
            Log.w(TAG, "Failed to schedule expedited upload job: ${e.message}")
        }
    }

    fun triggerQueueProcessingAsync(context: Context, onComplete: (() -> Unit)? = null) {
        val app = context.applicationContext
        worker.execute {
            try {
                processPendingQueueBlocking(app)
            } finally {
                onComplete?.let { cb -> mainHandler.post { cb() } }
            }
        }
    }

    /**
     * Processes all pending uploads and reconciles any server-side AI validations.
     */
    fun processPendingQueueBlocking(context: Context) {
        if (!processingActive.compareAndSet(false, true)) return
        val app = context.applicationContext
        try {
            val pending = getPendingRecords(app)
            if (pending.isEmpty()) return

            for (record in pending) {
                if (record.state in States.NEEDS_UPLOAD_STATES) {
                    executeResumableUploadForRecord(app, record)
                }
            }
            reconcileWithServerBlocking(app)
        } catch (e: Exception) {
            Log.w(TAG, "Queue processing warning: ${e.message}")
        } finally {
            processingActive.set(false)
        }
    }

    /**
     * Executes chunked/resumable upload (`/api/proof_upload/init` -> `/api/proof_upload/chunk`
     * -> `/api/proof_upload/complete`) with SHA-256 verification and byte-offset tracking.
     */
    private fun executeResumableUploadForRecord(context: Context, record: QueuedUploadRecord): Boolean {
        val file = File(record.evidenceFileReference)
        if (!file.exists() || file.length() <= 0L) {
            updateRecordFields(
                context,
                record.submissionId,
                state = States.UPLOAD_RETRY_PENDING,
                lastError = "Local evidence file missing at ${record.evidenceFileReference}"
            )
            return false
        }

        if (!HardwareDeviceVerifier.isNetworkOrWifiAvailable(context)) {
            updateRecordFields(
                context,
                record.submissionId,
                state = States.UPLOAD_PAUSED,
                lastError = "UPLOAD QUEUED – WAITING FOR BACKGROUND NETWORK ACCESS"
            )
            postUploadStatusNotification(
                context,
                record.submissionId,
                "UPLOAD QUEUED – WAITING FOR BACKGROUND NETWORK ACCESS",
                "Proof is safely stored on your phone and will upload automatically when network is available."
            )
            return false
        }

        val endpoints = TaufaPcBridge.getCandidateEndpoints(context)
        var workingEndpoint: String? = null
        var serverOffset = record.uploadOffset
        var lastErr = "PC server unreachable"

        // Step 1: Initialize or resume upload session (`/api/proof_upload/init`)
        for (ep in endpoints) {
            try {
                val initPayload = JSONObject().apply {
                    put("submission_id", record.submissionId)
                    put("session_id", record.sessionId)
                    put("activity_id", record.activityId)
                    put("task_type", record.taskType)
                    put("date", record.date)
                    put("category", record.category)
                    put("description", record.description)
                    put("filename", record.fileName)
                    put("mime_type", record.mimeType)
                    put("file_size", file.length())
                    put("sha256_checksum", record.sha256Checksum)
                }
                val resp = postJson("$ep/api/proof_upload/init", initPayload, timeoutMs = 8_000)
                if (resp != null && resp.optBoolean("success", false)) {
                    workingEndpoint = ep
                    TaufaPcBridge.lastWorkingEndpoint = ep
                    serverOffset = resp.optLong("upload_offset", 0L).coerceIn(0L, file.length())
                    val serverState = resp.optString("state", States.UPLOADING)
                    val ack = resp.optString("server_acknowledgement", "")
                    updateRecordFields(
                        context,
                        record.submissionId,
                        state = serverState,
                        uploadOffset = serverOffset,
                        serverAck = ack.ifBlank { null },
                        lastError = ""
                    )
                    if (serverState in States.NEEDS_RECONCILE_STATES || serverState in States.TERMINAL_STATES) {
                        return true
                    }
                    break
                }
            } catch (e: Exception) {
                lastErr = e.message ?: "Init connection failed"
            }
        }

        if (workingEndpoint == null) {
            val nextRetry = record.retryCount + 1
            updateRecordFields(
                context,
                record.submissionId,
                state = States.UPLOAD_RETRY_PENDING,
                retryCount = nextRetry,
                lastError = "Upload retry #$nextRetry pending: $lastErr"
            )
            return false
        }

        // Step 2: Stream chunks from `serverOffset` to `file.length()` (`/api/proof_upload/chunk`)
        val totalSize = file.length()
        try {
            RandomAccessFile(file, "r").use { raf ->
                var offset = serverOffset
                val buf = ByteArray(CHUNK_SIZE_BYTES)
                while (offset < totalSize) {
                    raf.seek(offset)
                    val toRead = min(CHUNK_SIZE_BYTES.toLong(), totalSize - offset).toInt()
                    val read = raf.read(buf, 0, toRead)
                    if (read <= 0) break
                    val chunkSlice = if (read == buf.size) buf else buf.copyOfRange(0, read)
                    val chunkB64 = Base64.encodeToString(chunkSlice, Base64.NO_WRAP)

                    val chunkPayload = JSONObject().apply {
                        put("submission_id", record.submissionId)
                        put("offset", offset)
                        put("chunk_base64", chunkB64)
                    }
                    val chunkResp = postJson("$workingEndpoint/api/proof_upload/chunk", chunkPayload, timeoutMs = 15_000)
                    if (chunkResp == null || !chunkResp.optBoolean("success", false)) {
                        val confirmedOffset = chunkResp?.optLong("upload_offset", offset) ?: offset
                        val errMsg = chunkResp?.optString("error", "Chunk upload interrupted") ?: "Chunk upload interrupted"
                        updateRecordFields(
                            context,
                            record.submissionId,
                            state = States.UPLOAD_RETRY_PENDING,
                            uploadOffset = confirmedOffset,
                            retryCount = record.retryCount + 1,
                            lastError = errMsg
                        )
                        return false
                    }
                    offset = chunkResp.optLong("upload_offset", offset + read)
                    updateRecordFields(
                        context,
                        record.submissionId,
                        state = States.UPLOADING,
                        uploadOffset = offset,
                        lastError = ""
                    )
                    val pct = ((offset.toDouble() / totalSize.toDouble()) * 100.0).toInt().coerceIn(1, 99)
                    postUploadStatusNotification(
                        context,
                        record.submissionId,
                        "Uploading Proof ($pct%)",
                        "Transferred ${offset / 1024} / ${totalSize / 1024} KB..."
                    )
                }
            }
        } catch (e: Exception) {
            updateRecordFields(
                context,
                record.submissionId,
                state = States.UPLOAD_RETRY_PENDING,
                retryCount = record.retryCount + 1,
                lastError = "Chunk transfer error: ${e.message}"
            )
            return false
        }

        // Step 3: Complete upload & verify SHA-256 on server (`/api/proof_upload/complete`)
        val completePayload = JSONObject().apply {
            put("submission_id", record.submissionId)
            put("sha256_checksum", record.sha256Checksum)
            put("async_validation", true)
        }
        val completeResp = postJson("$workingEndpoint/api/proof_upload/complete", completePayload, timeoutMs = 15_000)
        if (completeResp != null && completeResp.optBoolean("success", false)) {
            val ack = completeResp.optString("server_acknowledgement", "ack_uploaded")
            val nextState = completeResp.optString("state", States.AI_VALIDATION_PENDING)
            updateRecordFields(
                context,
                record.submissionId,
                state = nextState,
                uploadOffset = totalSize,
                serverAck = ack,
                aiValidationStatus = "AI_VALIDATION_PENDING",
                lastError = ""
            )
            postUploadStatusNotification(
                context,
                record.submissionId,
                "Proof Uploaded · Server Validating",
                "Full evidence verified on server (SHA-256 matched). AI validation continues on server."
            )
            return true
        } else {
            val err = completeResp?.optString("error", "Upload completion check failed") ?: "Upload completion check failed"
            updateRecordFields(
                context,
                record.submissionId,
                state = States.UPLOAD_RETRY_PENDING,
                retryCount = record.retryCount + 1,
                lastError = err
            )
            return false
        }
    }

    /**
     * Section 12: Automatic Reconciliation.
     * Queries `/api/proof_upload/reconcile` for all pending/uploaded submissions and syncs
     * server-side AI validation results and points transactions back into the APK database.
     */
    fun reconcileWithServerBlocking(context: Context): Int {
        val app = context.applicationContext
        val pending = getPendingRecords(app)
        if (pending.isEmpty()) return 0

        val endpoints = TaufaPcBridge.getCandidateEndpoints(app)
        val subIdsArray = JSONArray().apply {
            pending.forEach { put(it.submissionId) }
        }
        val reqPayload = JSONObject().apply {
            put("submission_ids", subIdsArray)
        }

        for (ep in endpoints) {
            val resp = postJson("$ep/api/proof_upload/reconcile", reqPayload, timeoutMs = 10_000) ?: continue
            if (!resp.optBoolean("success", false)) continue
            TaufaPcBridge.lastWorkingEndpoint = ep

            // Also update AI Health record if returned in reconcile response
            resp.optJSONObject("ai_health")?.let { healthJson ->
                val healthRec = AiServiceHealthManager.HealthRecord.fromJson(healthJson)
                if (healthRec.lastHealthCheckMs > 0L) {
                    AiServiceHealthManager.saveHealthRecord(app, healthRec)
                }
            }

            val submissionsArr = resp.optJSONArray("submissions") ?: return 0
            var reconciledCount = 0
            for (i in 0 until submissionsArr.length()) {
                val item = submissionsArr.optJSONObject(i) ?: continue
                val subId = item.optString("submission_id", "")
                if (subId.isBlank()) continue
                val serverState = item.optString("state", "")
                val serverOffset = item.optLong("upload_offset", 0L)
                val serverAck = item.optString("server_acknowledgement", "")
                val aiValStatus = item.optString("ai_validation_status", "PENDING")
                val pointsTx = item.optString("points_transaction_status", "PENDING")
                val pts = item.optInt("points_awarded", 0)
                val aiReason = item.optString("ai_reason", "")

                updateRecordFields(
                    context = app,
                    submissionId = subId,
                    state = serverState.ifBlank { null },
                    uploadOffset = serverOffset,
                    serverAck = serverAck.ifBlank { null },
                    aiValidationStatus = aiValStatus,
                    pointsTxStatus = pointsTx,
                    pointsAwarded = pts
                )

                if (serverState in States.TERMINAL_STATES) {
                    reconciledCount++
                    val title = when (serverState) {
                        States.APPROVED -> "Proof Approved (+$pts pts)"
                        States.REJECTED -> "Proof Rejected (0 pts)"
                        States.MANUAL_REVIEW -> "Proof Queued for Review"
                        else -> "Proof Processing Complete"
                    }
                    postUploadStatusNotification(
                        app,
                        subId,
                        title,
                        aiReason.ifBlank { "Server completed evidence validation." }
                    )
                }
            }
            return reconciledCount
        }
        return 0
    }

    private fun postJson(urlStr: String, payload: JSONObject, timeoutMs: Int): JSONObject? {
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL(urlStr).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = timeoutMs
                readTimeout = timeoutMs
                doInput = true
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=UTF-8")
            }
            OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use { writer ->
                writer.write(payload.toString())
                writer.flush()
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (text.isNotBlank()) JSONObject(text) else null
        } catch (_: Exception) {
            null
        } finally {
            runCatching { conn?.disconnect() }
        }
    }

    private fun postUploadStatusNotification(
        context: Context,
        submissionId: String,
        title: String,
        message: String
    ) {
        try {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(
                        CHANNEL_ID,
                        "StrictRoutines Proof Uploads",
                        NotificationManager.IMPORTANCE_LOW
                    ).apply {
                        description = "Background proof upload and AI validation progress"
                        setShowBadge(false)
                    }
                )
            }
            val notifId = NOTIFICATION_ID_BASE + (submissionId.hashCode() and 0x0fff)
            val intent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pi = PendingIntent.getActivity(
                context,
                notifId,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val notif = NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle(title)
                .setContentText(message)
                .setStyle(NotificationCompat.BigTextStyle().bigText(message))
                .setContentIntent(pi)
                .setOnlyAlertOnce(true)
                .setAutoCancel(true)
                .build()
            nm.notify(notifId, notif)
        } catch (_: Exception) {}
    }
}

/**
 * Expedited Android JobService that continues resumable proof uploads even when the
 * APK is minimized or the phone screen is locked (Part B, Section 8).
 */
class ProofUploadJobService : JobService() {
    override fun onStartJob(params: JobParameters?): Boolean {
        PersistentUploadQueueManager.triggerQueueProcessingAsync(applicationContext) {
            val stillPending = PersistentUploadQueueManager.getPendingRecords(applicationContext)
                .any { it.state in PersistentUploadQueueManager.States.NEEDS_UPLOAD_STATES }
            jobFinished(params, stillPending)
        }
        return true
    }

    override fun onStopJob(params: JobParameters?): Boolean = true
}
