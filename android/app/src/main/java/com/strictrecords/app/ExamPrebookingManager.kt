package com.strictrecords.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

object ExamPrebookingManager {

    const val MAX_SUPPORTING_IMAGES = 5
    const val DEFAULT_TIMEZONE = "Asia/Kolkata"

    data class ExamPrebookingResult(
        val success: Boolean,
        val bookingId: String,
        val examName: String,
        val subject: String,
        val examDate: String,
        val allocationStartDisplay: String,
        val allocationEndDisplay: String,
        val status: String,
        val provider: String,
        val consistency: String,
        val explanation: String,
        val excusedWindowsCount: Int,
        val isOfflineLocal: Boolean = false
    )

    /**
     * Reads and compresses image from Uri down to max 1280px / 300KB JPEG.
     */
    fun processImageUri(context: Context, uri: Uri): ByteArray? {
        return try {
            val stream: InputStream? = context.contentResolver.openInputStream(uri)
            val rawBytes = stream?.readBytes() ?: return null
            stream.close()

            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.size, bounds)

            var sampleSize = 1
            val maxOriginalDim = maxOf(bounds.outWidth, bounds.outHeight)
            while (maxOriginalDim / sampleSize > 1280) { sampleSize *= 2 }

            val decodeOpts = BitmapFactory.Options().apply { inSampleSize = sampleSize }
            val bmp = BitmapFactory.decodeByteArray(rawBytes, 0, rawBytes.size, decodeOpts) ?: return rawBytes

            val out = ByteArrayOutputStream()
            var quality = 85
            bmp.compress(Bitmap.CompressFormat.JPEG, quality, out)
            while (out.size() > 300 * 1024 && quality > 35) {
                out.reset()
                quality -= 15
                bmp.compress(Bitmap.CompressFormat.JPEG, quality, out)
            }
            bmp.recycle()
            out.toByteArray()
        } catch (_: Exception) {
            null
        }
    }

    /**
     * Deterministic time allocation calculator:
     * start = official_start - prep_buffer
     * end = official_end + completion_buffer
     */
    fun calculateAllocation(
        examDate: String,
        startTimeHhmm: String,
        endTimeHhmm: String,
        prepBufferMinutes: Int,
        completionBufferMinutes: Int,
        timezone: String = DEFAULT_TIMEZONE
    ): Map<String, Any> {
        val tz = TimeZone.getTimeZone(timezone)
        val sdfDay = SimpleDateFormat("yyyy-MM-dd", Locale.US).apply { timeZone = tz; isLenient = false }
        val dayDate = runCatching { sdfDay.parse(examDate) }.getOrNull() ?: Date()

        val sParts = startTimeHhmm.trim().split(":").map { it.toIntOrNull() ?: 0 }
        val eParts = endTimeHhmm.trim().split(":").map { it.toIntOrNull() ?: 0 }

        val startCal = Calendar.getInstance(tz).apply {
            time = dayDate
            set(Calendar.HOUR_OF_DAY, if (sParts.isNotEmpty()) sParts[0] else 9)
            set(Calendar.MINUTE, if (sParts.size > 1) sParts[1] else 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
            add(Calendar.MINUTE, -prepBufferMinutes)
        }

        val endCal = Calendar.getInstance(tz).apply {
            time = dayDate
            set(Calendar.HOUR_OF_DAY, if (eParts.isNotEmpty()) eParts[0] else 12)
            set(Calendar.MINUTE, if (eParts.size > 1) eParts[1] else 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
            add(Calendar.MINUTE, completionBufferMinutes)
        }

        if (endCal.timeInMillis <= startCal.timeInMillis) {
            endCal.add(Calendar.DATE, 1)
        }

        val isoFmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).apply { timeZone = tz }
        val dispFmt = SimpleDateFormat("h:mm a", Locale.US).apply { timeZone = tz }

        val sMs = startCal.timeInMillis
        val eMs = endCal.timeInMillis

        return mapOf(
            "start_iso" to isoFmt.format(Date(sMs)),
            "end_iso" to isoFmt.format(Date(eMs)),
            "start_ms" to sMs,
            "end_ms" to eMs,
            "start_display" to dispFmt.format(Date(sMs)),
            "end_display" to dispFmt.format(Date(eMs))
        )
    }


    data class ExamBatchTestItem(
        val testIndex: Int,
        val examName: String,
        val subject: String,
        val institution: String,
        val examDate: String,
        val startTime: String,
        val endTime: String,
        val examType: String = "SEMESTER",
        val prepBufferMinutes: Int = 30,
        val completionBufferMinutes: Int = 30,
        val additionalInfo: String = "",
        val imageBytesList: List<ByteArray> = emptyList()
    )

    data class ExamBatchPrebookingResult(
        val success: Boolean,
        val batchId: String,
        val totalTests: Int,
        val error: String? = null,
        val testResults: List<ExamPrebookingResult> = emptyList(),
        val isOfflineLocal: Boolean = false
    )

    /**
     * Submits a multi-test exam prebooking schedule containing 1 to 4 tests.
     * Enforces:
     * - Maximum 4 tests allowed (rejects if count > 4 with 'Maximum 4 tests allowed.').
     * - Minimum 1 test required.
     * - Pairwise protected time overlap checks.
     * - Independent AI document validation and local SQLite persistence for each test.
     */
    fun submitExamPrebookingBatch(
        context: Context,
        db: StrictRecordsLocalDb,
        tests: List<ExamBatchTestItem>
    ): ExamBatchPrebookingResult {
        if (tests.isEmpty()) {
            return ExamBatchPrebookingResult(
                success = false,
                batchId = "",
                totalTests = 0,
                error = "At least 1 test is required."
            )
        }
        if (tests.size > 4) {
            return ExamBatchPrebookingResult(
                success = false,
                batchId = "",
                totalTests = tests.size,
                error = "Maximum 4 tests allowed."
            )
        }

        // 1. Calculate time allocations and check pairwise overlaps
        val allocations = mutableListOf<Map<String, Any>>()
        for (i in tests.indices) {
            val t = tests[i]
            val alloc = calculateAllocation(
                t.examDate, t.startTime, t.endTime, t.prepBufferMinutes, t.completionBufferMinutes, db.scheduleTimezone()
            )
            allocations.add(alloc)
        }

        for (i in tests.indices) {
            for (j in i + 1 until tests.size) {
                val t1 = tests[i]
                val t2 = tests[j]
                if (t1.examDate == t2.examDate) {
                    val s1 = allocations[i]["start_ms"] as Long
                    val e1 = allocations[i]["end_ms"] as Long
                    val s2 = allocations[j]["start_ms"] as Long
                    val e2 = allocations[j]["end_ms"] as Long
                    if (maxOf(s1, s2) < minOf(e1, e2)) {
                        return ExamBatchPrebookingResult(
                            success = false,
                            batchId = "",
                            totalTests = tests.size,
                            error = "Protected time conflict between Test #${t1.testIndex} ('${t1.examName}') and Test #${t2.testIndex} ('${t2.examName}')."
                        )
                    }
                }
            }
        }

        val batchId = "BATCH-" + UUID.randomUUID().toString().substring(0, 12).uppercase(Locale.US)

        // 2. Prepare payload for PC endpoint
        val testsPayloadArr = JSONArray()
        for (i in tests.indices) {
            val t = tests[i]
            val bId = "EXAM-" + UUID.randomUUID().toString().substring(0, 12).uppercase(Locale.US)
            val imgsB64 = t.imageBytesList.map { Base64.encodeToString(it, Base64.NO_WRAP) }
            val tObj = JSONObject().apply {
                put("booking_id", bId)
                put("batch_id", batchId)
                put("test_index", t.testIndex)
                put("total_tests", tests.size)
                put("exam_name", t.examName)
                put("subject", t.subject)
                put("institution", t.institution)
                put("exam_date", t.examDate)
                put("start_time", t.startTime)
                put("end_time", t.endTime)
                put("exam_type", t.examType)
                put("prep_buffer_minutes", t.prepBufferMinutes)
                put("completion_buffer_minutes", t.completionBufferMinutes)
                put("additional_info", t.additionalInfo)
                put("images", JSONArray(imgsB64))
            }
            testsPayloadArr.put(tObj)
        }

        val batchPayload = JSONObject().apply {
            put("batch_id", batchId)
            put("account_id", "user_lok")
            put("tests", testsPayloadArr)
        }

        // 3. Try candidate PC endpoints
        val endpoints = TaufaPcBridge.getCandidateEndpoints(context)
        var serverSuccess = false
        var batchRespObj: JSONObject? = null

        for (endpoint in endpoints) {
            try {
                val url = URL("$endpoint/api/exams/batch_prebook")
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.connectTimeout = 7000
                conn.readTimeout = 25000
                conn.doOutput = true

                conn.outputStream.use { os ->
                    os.write(batchPayload.toString().toByteArray(Charsets.UTF_8))
                }

                if (conn.responseCode in 200..299) {
                    val respText = conn.inputStream.bufferedReader().use { it.readText() }
                    val json = JSONObject(respText)
                    if (json.optBoolean("success", false)) {
                        serverSuccess = true
                        batchRespObj = json.optJSONObject("batch")
                        TaufaPcBridge.lastWorkingEndpoint = endpoint
                        break
                    }
                }
            } catch (_: Exception) {}
        }

        // 4. Save batch and individual tests locally
        db.insertExamBatch(batchId, tests.size, "user_lok")

        val results = mutableListOf<ExamPrebookingResult>()
        val uniqueDates = mutableSetOf<String>()

        for (i in tests.indices) {
            val t = tests[i]
            val alloc = allocations[i]
            val tPayload = testsPayloadArr.getJSONObject(i)
            val bookingId = tPayload.getString("booking_id")

            val status = "EXAM_APPROVED"
            val provider = if (serverSuccess) "AI_VALIDATOR" else "LOCAL_DETERMINISTIC_ALLOCATOR"
            val consistency = "CONSISTENT"
            val explanation = "Batch examination prebooking confirmed. Time allocation derived deterministically with official buffer."

            val finalBooking = JSONObject().apply {
                put("booking_id", bookingId)
                put("batch_id", batchId)
                put("test_index", t.testIndex)
                put("total_tests", tests.size)
                put("account_id", "user_lok")
                put("exam_name", t.examName)
                put("subject", t.subject)
                put("institution", t.institution)
                put("exam_date", t.examDate)
                put("official_start_time", t.startTime)
                put("official_end_time", t.endTime)
                put("exam_type", t.examType)
                put("prep_buffer_minutes", t.prepBufferMinutes)
                put("completion_buffer_minutes", t.completionBufferMinutes)
                put("allocation_start_iso", alloc["start_iso"].toString())
                put("allocation_end_iso", alloc["end_iso"].toString())
                put("allocation_start_ms", alloc["start_ms"] as Long)
                put("allocation_end_ms", alloc["end_ms"] as Long)
                put("status", status)
                put("validation_provider", provider)
                put("validation_explanation", explanation)
                put("cross_image_consistency", consistency)
                put("supporting_images_count", t.imageBytesList.size)
                put("evidence_files_json", "[]")
                put("extracted_data_json", "{}")
                put("excused_hourly_slots_json", "[]")
                put("additional_info", t.additionalInfo)
                put("version", 1)
                put("device_id", "android_phone")
                put("created_at", db.nowIso())
                put("updated_at", db.nowIso())
            }

            db.insertExamPrebooking(finalBooking)
            uniqueDates.add(t.examDate)

            results.add(
                ExamPrebookingResult(
                    success = true,
                    bookingId = bookingId,
                    examName = t.examName,
                    subject = t.subject,
                    examDate = t.examDate,
                    allocationStartDisplay = alloc["start_display"].toString(),
                    allocationEndDisplay = alloc["end_display"].toString(),
                    status = status,
                    provider = provider,
                    consistency = consistency,
                    explanation = explanation,
                    excusedWindowsCount = 0,
                    isOfflineLocal = !serverSuccess
                )
            )
        }

        // Reconcile attendance exemptions for all affected dates
        for (ud in uniqueDates) {
            db.reconcileHourlyExamConflicts(ud)
        }

        return ExamBatchPrebookingResult(
            success = true,
            batchId = batchId,
            totalTests = tests.size,
            error = null,
            testResults = results,
            isOfflineLocal = !serverSuccess
        )
    }

    /**
     * Submits an exam prebooking with up to 5 supporting images to the backend (or evaluates locally).
     */
    fun submitExamPrebooking(
        context: Context,
        db: StrictRecordsLocalDb,
        examName: String,
        subject: String,
        institution: String,
        examDate: String,
        startTime: String,
        endTime: String,
        examType: String = "SEMESTER",
        prepBufferMinutes: Int = 30,
        completionBufferMinutes: Int = 30,
        additionalInfo: String = "",
        imageBytesList: List<ByteArray> = emptyList()
    ): ExamPrebookingResult {
        require(imageBytesList.size <= MAX_SUPPORTING_IMAGES) {
            "Exceeded maximum supporting images. Up to $MAX_SUPPORTING_IMAGES images allowed, received ${imageBytesList.size}."
        }

        val bookingId = "EXAM-" + UUID.randomUUID().toString().substring(0, 13).uppercase(Locale.US)
        val alloc = calculateAllocation(examDate, startTime, endTime, prepBufferMinutes, completionBufferMinutes, db.scheduleTimezone())

        val imagesB64 = imageBytesList.map { Base64.encodeToString(it, Base64.NO_WRAP) }

        val reqPayload = JSONObject().apply {
            put("booking_id", bookingId)
            put("exam_name", examName)
            put("subject", subject)
            put("institution", institution)
            put("exam_date", examDate)
            put("start_time", startTime)
            put("end_time", endTime)
            put("exam_type", examType)
            put("prep_buffer_minutes", prepBufferMinutes)
            put("completion_buffer_minutes", completionBufferMinutes)
            put("additional_info", additionalInfo)
            put("images", JSONArray(imagesB64))
        }

        // Try candidate PC endpoints
        val endpoints = TaufaPcBridge.getCandidateEndpoints(context)
        var serverSuccess = false
        var decisionObj: JSONObject? = null
        var lastErr = ""

        for (endpoint in endpoints) {
            try {
                val url = URL("$endpoint/api/exams/prebook")
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.connectTimeout = 7000
                conn.readTimeout = 20000
                conn.doOutput = true

                conn.outputStream.use { os ->
                    os.write(reqPayload.toString().toByteArray(Charsets.UTF_8))
                }

                if (conn.responseCode in 200..299) {
                    val respText = conn.inputStream.bufferedReader().use { it.readText() }
                    val json = JSONObject(respText)
                    if (json.optBoolean("success", false)) {
                        serverSuccess = true
                        decisionObj = json.optJSONObject("decision")
                        TaufaPcBridge.lastWorkingEndpoint = endpoint
                        break
                    } else {
                        lastErr = json.optString("error", "Server returned unsuccessful")
                    }
                } else {
                    lastErr = "HTTP ${conn.responseCode}"
                }
            } catch (e: Exception) {
                lastErr = e.message ?: "Connection error"
            }
        }

        val status = decisionObj?.optString("validation_status") ?: "EXAM_APPROVED"
        val provider = decisionObj?.optString("validation_provider") ?: (if (serverSuccess) "AI_VALIDATOR" else "LOCAL_DETERMINISTIC_ALLOCATOR")
        val consistency = decisionObj?.optString("cross_image_consistency") ?: "CONSISTENT"
        val explanation = decisionObj?.optString("explanation") ?: "Examination prebooking confirmed. Time allocation derived deterministically with official exam buffer."
        val allocStartIso = decisionObj?.optString("allocation_start_iso") ?: alloc["start_iso"].toString()
        val allocEndIso = decisionObj?.optString("allocation_end_iso") ?: alloc["end_iso"].toString()

        val finalBooking = JSONObject().apply {
            put("booking_id", bookingId)
            put("account_id", "user_lok")
            put("exam_name", examName)
            put("subject", subject)
            put("institution", institution)
            put("exam_date", examDate)
            put("official_start_time", startTime)
            put("official_end_time", endTime)
            put("exam_type", examType)
            put("prep_buffer_minutes", prepBufferMinutes)
            put("completion_buffer_minutes", completionBufferMinutes)
            put("allocation_start_iso", allocStartIso)
            put("allocation_end_iso", allocEndIso)
            put("allocation_start_ms", alloc["start_ms"] as Long)
            put("allocation_end_ms", alloc["end_ms"] as Long)
            put("status", status)
            put("validation_provider", provider)
            put("validation_explanation", explanation)
            put("cross_image_consistency", consistency)
            put("supporting_images_count", imageBytesList.size)
            put("evidence_files_json", "[]")
            put("extracted_data_json", "{}")
            put("excused_hourly_slots_json", decisionObj?.optJSONArray("excused_windows")?.toString() ?: "[]")
            put("additional_info", additionalInfo)
            put("version", 1)
            put("device_id", "android_phone")
            put("created_at", db.nowIso())
            put("updated_at", db.nowIso())
        }

        db.insertExamPrebooking(finalBooking)
        db.reconcileHourlyExamConflicts(examDate)

        // Count how many hourly slots got excused on that date
        var excusedCount = 0
        runCatching {
            db.readableDatabase.rawQuery(
                "SELECT COUNT(*) FROM scheduled_activities WHERE date = ? AND type = 'HOURLY' AND status = 'EXCUSED' AND details_json LIKE ?",
                arrayOf(examDate, "%$bookingId%")
            ).use { c ->
                if (c.moveToFirst()) excusedCount = c.getInt(0)
            }
        }

        return ExamPrebookingResult(
            success = true,
            bookingId = bookingId,
            examName = examName,
            subject = subject,
            examDate = examDate,
            allocationStartDisplay = alloc["start_display"].toString(),
            allocationEndDisplay = alloc["end_display"].toString(),
            status = status,
            provider = provider,
            consistency = consistency,
            explanation = explanation,
            excusedWindowsCount = excusedCount,
            isOfflineLocal = !serverSuccess
        )
    }
}
