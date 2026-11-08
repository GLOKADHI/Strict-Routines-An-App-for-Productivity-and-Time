package com.strictrecords.app

import android.content.ContentResolver
import android.content.Context
import android.media.MediaMetadataRetriever
import android.media.MediaPlayer
import android.net.Uri
import android.os.SystemClock
import android.provider.OpenableColumns
import android.util.Log
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Manages Wake-up Music selection, validation, local persistence, and monthly change lock.
 *
 * Rules:
 * - Selected music is imported into app-private storage (`wake_up_music/`).
 * - Validates readability, supported audio decoding, and valid duration.
 * - Monthly lock: Committing a track locks changes until the 1st day of the next calendar month (00:00:00 local time).
 * - Conservative clock check: Backward clock changes do not unlock the track.
 * - Retains previous track and lock if a new import fails.
 */
object WakeUpMusicManager {

    private const val TAG = "WakeUpMusic"
    private const val MUSIC_DIR_NAME = "wake_up_music"
    private const val CURRENT_TRACK_FILENAME = "current_wake_up_track.audio"
    private const val MAX_FILE_SIZE_BYTES = 80L * 1024L * 1024L // 80 MB cap
    private const val MIN_TRACK_DURATION_MS = 3_000L // 3 seconds minimum

    // Configuration keys in system_config
    const val KEY_MUSIC_TITLE = "wakeup_music_title"
    const val KEY_MUSIC_ORIGINAL_FILENAME = "wakeup_music_orig_filename"
    const val KEY_MUSIC_FILE_PATH = "wakeup_music_file_path"
    const val KEY_MUSIC_DURATION_MS = "wakeup_music_duration_ms"
    const val KEY_MUSIC_FORMAT = "wakeup_music_format"
    const val KEY_MUSIC_SHA256 = "wakeup_music_sha256"
    const val KEY_MUSIC_FILE_SIZE = "wakeup_music_file_size"
    const val KEY_MUSIC_VALIDATED = "wakeup_music_validated"
    const val KEY_MUSIC_SELECTED_AT = "wakeup_music_selected_at"
    const val KEY_MUSIC_LOCK_UNTIL_MS = "wakeup_music_lock_until_ms"
    const val KEY_MUSIC_LOCK_UNTIL_DATE = "wakeup_music_lock_until_date"

    data class WakeUpTrackInfo(
        val title: String,
        val originalFilename: String,
        val filePath: String,
        val durationMs: Long,
        val format: String,
        val sha256: String,
        val fileSizeBytes: Long,
        val selectedAt: String,
        val lockUntilMs: Long,
        val lockUntilDate: String,
        val isValidated: Boolean
    ) {
        val fileExists: Boolean get() = File(filePath).isFile
    }

    sealed class ImportResult {
        data class Success(val trackInfo: WakeUpTrackInfo) : ImportResult()
        data class Failure(val error: String) : ImportResult()
    }

    private val localTimeZone: TimeZone
        get() = TimeZone.getTimeZone("Asia/Kolkata")

    /**
     * Retrieves the currently configured and validated wake-up track.
     */
    fun getCurrentTrack(context: Context): WakeUpTrackInfo? {
        val db = StrictRecordsLocalDb.getInstance(context)
        val path = db.getConfigValue(KEY_MUSIC_FILE_PATH, "")
        if (path.isBlank()) return null
        val file = File(path)
        if (!file.isFile || file.length() <= 0L) return null

        val validated = db.getConfigValue(KEY_MUSIC_VALIDATED, "0") == "1"
        if (!validated) return null

        return WakeUpTrackInfo(
            title = db.getConfigValue(KEY_MUSIC_TITLE, "Wake-up Music").ifBlank { "Wake-up Music" },
            originalFilename = db.getConfigValue(KEY_MUSIC_ORIGINAL_FILENAME, file.name),
            filePath = path,
            durationMs = db.getConfigValue(KEY_MUSIC_DURATION_MS, "0").toLongOrNull() ?: 0L,
            format = db.getConfigValue(KEY_MUSIC_FORMAT, "Audio"),
            sha256 = db.getConfigValue(KEY_MUSIC_SHA256, ""),
            fileSizeBytes = db.getConfigValue(KEY_MUSIC_FILE_SIZE, "0").toLongOrNull() ?: file.length(),
            selectedAt = db.getConfigValue(KEY_MUSIC_SELECTED_AT, ""),
            lockUntilMs = db.getConfigValue(KEY_MUSIC_LOCK_UNTIL_MS, "0").toLongOrNull() ?: 0L,
            lockUntilDate = db.getConfigValue(KEY_MUSIC_LOCK_UNTIL_DATE, ""),
            isValidated = true
        )
    }

    /**
     * Checks if track selection is locked for the current calendar month.
     * Enforces conservative clock handling:
     * - If nowMs < selectedAtMs (clock rolled backward), lock remains active.
     * - If nowMs < lockUntilMs, lock remains active.
     */
    fun isSelectionLocked(context: Context, nowMs: Long = System.currentTimeMillis()): Boolean {
        val db = StrictRecordsLocalDb.getInstance(context)
        val lockUntilMs = db.getConfigValue(KEY_MUSIC_LOCK_UNTIL_MS, "0").toLongOrNull() ?: 0L
        if (lockUntilMs <= 0L) return false

        // Check selectedAt for clock manipulation defense
        val selectedAtIso = db.getConfigValue(KEY_MUSIC_SELECTED_AT, "")
        if (selectedAtIso.isNotBlank()) {
            val selectedAtMs = parseIsoToMs(selectedAtIso)
            if (selectedAtMs > 0L && nowMs < selectedAtMs) {
                // Device clock was moved backwards relative to when the track was selected
                return true
            }
        }

        return nowMs < lockUntilMs
    }

    /**
     * Calculates the lock-until timestamp (epoch ms) representing 00:00:00 on the 1st day of the next calendar month.
     * Example: 16 October 2026 -> 1 November 2026 00:00:00.
     */
    fun calculateNextMonthLockUntilMs(nowMs: Long, tz: TimeZone = localTimeZone): Long {
        val cal = Calendar.getInstance(tz)
        cal.timeInMillis = nowMs
        cal.add(Calendar.MONTH, 1)
        cal.set(Calendar.DAY_OF_MONTH, 1)
        cal.set(Calendar.HOUR_OF_DAY, 0)
        cal.set(Calendar.MINUTE, 0)
        cal.set(Calendar.SECOND, 0)
        cal.set(Calendar.MILLISECOND, 0)
        return cal.timeInMillis
    }

    /**
     * Calculates the display date string for lock-until (e.g. "1 November").
     */
    fun calculateNextMonthLockDateString(nowMs: Long, tz: TimeZone = localTimeZone): String {
        val lockMs = calculateNextMonthLockUntilMs(nowMs, tz)
        val cal = Calendar.getInstance(tz).apply { timeInMillis = lockMs }
        val day = cal.get(Calendar.DAY_OF_MONTH)
        val monthName = cal.getDisplayName(Calendar.MONTH, Calendar.LONG, Locale.ENGLISH) ?: ""
        return "$day $monthName"
    }

    /**
     * Human-readable formatted string for remaining lock status.
     * Example: "Track changes available from 1 November"
     */
    fun getLockStatusMessage(context: Context, nowMs: Long = System.currentTimeMillis()): String {
        val track = getCurrentTrack(context)
        if (track == null) return "No track selected. Changes unlocked."
        if (!isSelectionLocked(context, nowMs)) {
            return "Track change available for this month."
        }
        val lockDateStr = track.lockUntilDate.ifBlank {
            calculateNextMonthLockDateString(nowMs)
        }
        return "Track changes locked until $lockDateStr."
    }

    /**
     * Imports and validates an audio track from a chosen content Uri.
     * Thread-safe and atomic: writes to a temporary file first, validates decoding, and then commits.
     */
    @Synchronized
    fun importMusicFromUri(
        context: Context,
        uri: Uri,
        nowMs: Long = System.currentTimeMillis()
    ): ImportResult {
        val db = StrictRecordsLocalDb.getInstance(context)

        // 1. Enforce monthly lock
        if (isSelectionLocked(context, nowMs)) {
            val track = getCurrentTrack(context)
            val dateStr = track?.lockUntilDate ?: calculateNextMonthLockDateString(nowMs)
            return ImportResult.Failure("Track changes are locked until $dateStr.")
        }

        val contentResolver = context.contentResolver
        val musicDir = File(context.filesDir, MUSIC_DIR_NAME).apply { mkdirs() }
        val tempFile = File(musicDir, "temp_import_${SystemClock.elapsedRealtime()}.tmp")

        try {
            // 2. Query original filename and size from ContentResolver
            var origFilename = "audio_track_${System.currentTimeMillis()}"
            var estimatedSize = 0L
            runCatching {
                contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                    if (cursor.moveToFirst()) {
                        val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                        if (nameIndex != -1) {
                            val name = cursor.getString(nameIndex)
                            if (!name.isNullOrBlank()) origFilename = name
                        }
                        val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                        if (sizeIndex != -1) {
                            estimatedSize = cursor.getLong(sizeIndex)
                        }
                    }
                }
            }

            if (estimatedSize > MAX_FILE_SIZE_BYTES) {
                return ImportResult.Failure("File exceeds size limit of 80 MB.")
            }

            // 3. Stream copy into temporary file
            val inputStream = contentResolver.openInputStream(uri)
                ?: return ImportResult.Failure("Unable to open selected file. Check file access permissions.")

            inputStream.use { input ->
                FileOutputStream(tempFile).use { output ->
                    input.copyTo(output)
                }
            }

            if (!tempFile.isFile || tempFile.length() <= 0L) {
                tempFile.delete()
                return ImportResult.Failure("Imported file is empty.")
            }

            if (tempFile.length() > MAX_FILE_SIZE_BYTES) {
                tempFile.delete()
                return ImportResult.Failure("File exceeds size limit of 80 MB.")
            }

            // 4. Validate audio properties with MediaMetadataRetriever
            val retriever = MediaMetadataRetriever()
            var durationMs = 0L
            var mimeType = "audio/*"
            var title = origFilename
            try {
                retriever.setDataSource(tempFile.absolutePath)
                val durStr = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                durationMs = durStr?.toLongOrNull() ?: 0L
                val mime = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_MIMETYPE)
                if (!mime.isNullOrBlank()) mimeType = mime
                val metaTitle = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_TITLE)
                if (!metaTitle.isNullOrBlank()) title = metaTitle.trim()
            } catch (e: Exception) {
                Log.w(TAG, "MediaMetadataRetriever warning: ${e.message}")
            } finally {
                runCatching { retriever.release() }
            }

            if (durationMs < MIN_TRACK_DURATION_MS) {
                // If retriever failed to get duration, attempt test preparation with MediaPlayer
                val mpTest = MediaPlayer()
                try {
                    mpTest.setDataSource(tempFile.absolutePath)
                    mpTest.prepare()
                    durationMs = mpTest.duration.toLong()
                } catch (e: Exception) {
                    tempFile.delete()
                    return ImportResult.Failure("This audio format is not supported or cannot be decoded: ${e.message}")
                } finally {
                    runCatching { mpTest.release() }
                }
            }

            if (durationMs < MIN_TRACK_DURATION_MS) {
                tempFile.delete()
                return ImportResult.Failure("Track duration is too short (${durationMs}ms). Minimum duration is 3 seconds.")
            }

            // 5. Test decode with MediaPlayer to ensure playback readiness
            val testPlayer = MediaPlayer()
            try {
                testPlayer.setDataSource(tempFile.absolutePath)
                testPlayer.prepare()
                if (testPlayer.duration <= 0) {
                    tempFile.delete()
                    return ImportResult.Failure("Audio decoder reported invalid track duration.")
                }
            } catch (e: Exception) {
                tempFile.delete()
                return ImportResult.Failure("Audio playback verification failed: ${e.message}")
            } finally {
                runCatching { testPlayer.release() }
            }

            // 6. Compute SHA-256 Checksum
            val sha256 = computeFileSha256(tempFile)
            if (sha256.isBlank()) {
                tempFile.delete()
                return ImportResult.Failure("Failed to calculate audio integrity hash.")
            }

            // 7. Atomic Commit: Move temp file to current track destination
            val extension = if (origFilename.contains(".")) origFilename.substringAfterLast(".") else "audio"
            val targetFile = File(musicDir, "track_${sha256.take(12)}.$extension")
            if (targetFile.exists()) targetFile.delete()

            val renameOk = tempFile.renameTo(targetFile)
            val finalFile = if (renameOk) targetFile else {
                // Fallback copy if cross-device rename fails
                tempFile.copyTo(targetFile, overwrite = true)
                tempFile.delete()
                targetFile
            }

            // 8. Calculate monthly lock values
            val lockUntilMs = calculateNextMonthLockUntilMs(nowMs)
            val lockUntilDate = calculateNextMonthLockDateString(nowMs)
            val selectedAtIso = formatMsToIso(nowMs)

            // 9. Persist metadata
            db.setConfigValue(KEY_MUSIC_TITLE, title)
            db.setConfigValue(KEY_MUSIC_ORIGINAL_FILENAME, origFilename)
            db.setConfigValue(KEY_MUSIC_FILE_PATH, finalFile.absolutePath)
            db.setConfigValue(KEY_MUSIC_DURATION_MS, durationMs.toString())
            db.setConfigValue(KEY_MUSIC_FORMAT, mimeType)
            db.setConfigValue(KEY_MUSIC_SHA256, sha256)
            db.setConfigValue(KEY_MUSIC_FILE_SIZE, finalFile.length().toString())
            db.setConfigValue(KEY_MUSIC_VALIDATED, "1")
            db.setConfigValue(KEY_MUSIC_SELECTED_AT, selectedAtIso)
            db.setConfigValue(KEY_MUSIC_LOCK_UNTIL_MS, lockUntilMs.toString())
            db.setConfigValue(KEY_MUSIC_LOCK_UNTIL_DATE, lockUntilDate)

            val trackInfo = WakeUpTrackInfo(
                title = title,
                originalFilename = origFilename,
                filePath = finalFile.absolutePath,
                durationMs = durationMs,
                format = mimeType,
                sha256 = sha256,
                fileSizeBytes = finalFile.length(),
                selectedAt = selectedAtIso,
                lockUntilMs = lockUntilMs,
                lockUntilDate = lockUntilDate,
                isValidated = true
            )

            Log.i(TAG, "Wake-up track successfully committed: title='$title', duration=${durationMs}ms, locked until $lockUntilDate")
            return ImportResult.Success(trackInfo)

        } catch (e: Exception) {
            runCatching { tempFile.delete() }
            Log.e(TAG, "Error importing music: ${e.message}", e)
            return ImportResult.Failure("Failed to import music file: ${e.message}")
        }
    }

    /**
     * Formats duration in milliseconds to mm:ss format.
     */
    fun formatDurationMmSs(durationMs: Long): String {
        if (durationMs <= 0L) return "0:00"
        val totalSec = durationMs / 1000L
        val min = totalSec / 60L
        val sec = totalSec % 60L
        return String.format(Locale.US, "%d:%02d", min, sec)
    }

    private fun computeFileSha256(file: File): String {
        return try {
            val digest = MessageDigest.getInstance("SHA-256")
            FileInputStream(file).use { fis ->
                val buffer = ByteArray(16 * 1024)
                var bytesRead: Int
                while (fis.read(buffer).also { bytesRead = it } != -1) {
                    digest.update(buffer, 0, bytesRead)
                }
            }
            digest.digest().joinToString("") { "%02x".format(it) }
        } catch (_: Exception) {
            ""
        }
    }

    private fun formatMsToIso(ms: Long): String {
        val sdf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US)
        sdf.timeZone = localTimeZone
        return sdf.format(Date(ms))
    }

    private fun parseIsoToMs(iso: String): Long {
        return try {
            val sdf = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US)
            sdf.timeZone = localTimeZone
            sdf.parse(iso)?.time ?: 0L
        } catch (_: Exception) {
            try {
                val fallback = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US)
                fallback.timeZone = localTimeZone
                fallback.parse(iso)?.time ?: 0L
            } catch (_: Exception) {
                0L
            }
        }
    }
}
