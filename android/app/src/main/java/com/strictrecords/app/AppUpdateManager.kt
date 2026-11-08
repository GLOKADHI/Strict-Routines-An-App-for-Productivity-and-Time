package com.strictrecords.app

import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.Signature
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors

/**
 * Central In-App APK Updater for StrictRecords Android.
 * Features:
 * - Multi-layer verification (Size, SHA-256, Package Identity, Cryptographic Signatures).
 * - System notification with deduplication.
 * - Granular lifecycle states synchronized to PC dashboard.
 * - Seamless handoff via StrictFileProvider.
 */
class AppUpdateManager private constructor(private val appContext: Context) {

    enum class UpdateState {
        IDLE,
        CHECKING,
        UP_TO_DATE,
        UPDATE_AVAILABLE,
        PREPARING,
        DOWNLOADING,
        VERIFYING,
        DOWNLOAD_VERIFIED,
        INSTALLATION_PENDING,
        INSTALLED,
        FAILED
    }

    data class ReleaseInfo(
        val releaseId: String,
        val platform: String,
        val versionName: String,
        val versionCode: Int,
        val minSupportedVersion: Int,
        val downloadUrl: String,
        val fileSizeBytes: Long,
        val sha256Checksum: String,
        val releaseNotes: String,
        val isMandatory: Boolean,
        val channel: String
    )

    interface UpdateListener {
        fun onUpdateStateChanged(
            state: UpdateState,
            release: ReleaseInfo?,
            progress: Int,
            errorMessage: String?
        )
    }

    companion object {
        const val NOTIFICATION_CHANNEL_ID = "strictrecords_updates"
        const val NOTIFICATION_ID = 2099
        const val UPDATE_ACTION_VIEW = "com.strictrecords.app.ACTION_VIEW_UPDATE"

        @Volatile
        private var instance: AppUpdateManager? = null

        fun getInstance(context: Context): AppUpdateManager {
            return instance ?: synchronized(this) {
                instance ?: AppUpdateManager(context.applicationContext).also { instance = it }
            }
        }
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private val listeners = CopyOnWriteArrayList<UpdateListener>()
    private var lastAutomaticCheckElapsed = 0L

    @Volatile
    var currentState: UpdateState = UpdateState.IDLE
        private set

    var latestRelease: ReleaseInfo? = null
        private set

    var currentProgress: Int = 0
        private set

    var lastErrorMessage: String? = null
        private set

    init {
        createNotificationChannel()
    }

    fun addListener(listener: UpdateListener) {
        if (!listeners.contains(listener)) {
            listeners.add(listener)
            listener.onUpdateStateChanged(currentState, latestRelease, currentProgress, lastErrorMessage)
        }
    }

    fun removeListener(listener: UpdateListener) {
        listeners.remove(listener)
    }

    private fun setState(state: UpdateState, progress: Int = 0, error: String? = null) {
        currentState = state
        currentProgress = progress
        lastErrorMessage = error
        mainHandler.post {
            for (listener in listeners) {
                listener.onUpdateStateChanged(state, latestRelease, progress, error)
            }
        }
    }

    fun getInstalledVersionCode(): Int {
        return try {
            val pInfo = appContext.packageManager.getPackageInfo(appContext.packageName, 0)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                pInfo.longVersionCode.toInt()
            } else {
                @Suppress("DEPRECATION")
                pInfo.versionCode
            }
        } catch (_: Exception) {
            1
        }
    }

    fun getInstalledVersionName(): String {
        return try {
            appContext.packageManager.getPackageInfo(appContext.packageName, 0).versionName ?: "1.0.0"
        } catch (_: Exception) {
            "1.0.0"
        }
    }

    fun getBaseServerUrl(): String {
        val db = StrictRecordsLocalDb.getInstance(appContext)
        val reg = db.getDeviceRegistration()
        val savedEp = reg?.optString("server_endpoint", "")?.trimEnd('/') ?: ""
        if (savedEp.isNotEmpty() && !savedEp.contains("127.0.0.1") && !savedEp.contains("localhost")) {
            return savedEp
        }
        val discovered = db.discoverServerEndpoint(appContext, scanLan = false).trimEnd('/')
        if (discovered.isNotEmpty() && !discovered.contains("127.0.0.1") && !discovered.contains("localhost")) {
            return discovered
        }
        return if (savedEp.isNotEmpty()) savedEp else "http://192.168.29.9:8765"
    }

    /**
     * Check central server for latest APK release.
     */
    @Synchronized fun checkForUpdates(
        forceUserInitiated: Boolean = false,
        onComplete: ((Boolean, ReleaseInfo?, String?) -> Unit)? = null
    ) {
        if (currentState in setOf(UpdateState.DOWNLOADING, UpdateState.CHECKING, UpdateState.PREPARING, UpdateState.VERIFYING)) {
            mainHandler.post { onComplete?.invoke(false, latestRelease, "Already in progress") }
            return
        }
        val elapsed = SystemClock.elapsedRealtime()
        if (!forceUserInitiated && lastAutomaticCheckElapsed > 0L &&
            elapsed - lastAutomaticCheckElapsed in 0L until MonitoringWorkPolicy.UPDATE_CHECK_INTERVAL_MS) {
            mainHandler.post { onComplete?.invoke(currentState != UpdateState.FAILED, latestRelease, lastErrorMessage) }
            return
        }
        lastAutomaticCheckElapsed = elapsed

        setState(UpdateState.CHECKING)

        executor.execute {
            var connection: HttpURLConnection? = null
            try {
                var release: ReleaseInfo? = null

                // Check the paired local Windows server.
                if (release == null) {
                    val baseUrl = getBaseServerUrl()
                    val url = URL("$baseUrl/api/releases/latest?platform=ANDROID&channel=PRODUCTION")
                    val conn = (url.openConnection() as HttpURLConnection).apply {
                        requestMethod = "GET"
                        connectTimeout = 4000
                        readTimeout = 4000
                        setRequestProperty("Accept", "application/json")
                    }
                    connection = conn

                    val code = conn.responseCode
                    if (code in 200..299) {
                        val resp = conn.inputStream.bufferedReader().use { it.readText() }
                        val j = JSONObject(resp)
                        val relObj = j.optJSONObject("release")

                        if (relObj != null) {
                            var dlUrl = relObj.optString("downloadUrl", relObj.optString("download_url", ""))
                            if (!dlUrl.startsWith("http://") && !dlUrl.startsWith("https://")) {
                                dlUrl = if (dlUrl.startsWith("/")) "$baseUrl$dlUrl" else "$baseUrl/$dlUrl"
                            }

                            release = ReleaseInfo(
                                releaseId = relObj.optString("release_id", relObj.optString("releaseId", "rel_android_latest")),
                                platform = relObj.optString("platform", "ANDROID"),
                                versionName = relObj.optString("versionName", relObj.optString("version_name", "1.0.0")),
                                versionCode = relObj.optInt("versionCode", relObj.optInt("version_code", 1)),
                                minSupportedVersion = relObj.optInt("minimumSupportedVersion", relObj.optInt("min_supported_version", 1)),
                                downloadUrl = dlUrl,
                                fileSizeBytes = relObj.optLong("fileSizeBytes", relObj.optLong("file_size_bytes", 0L)),
                                sha256Checksum = relObj.optString("sha256", relObj.optString("sha256_checksum", "")).lowercase(),
                                releaseNotes = relObj.optString("releaseNotes", relObj.optString("release_notes", "")),
                                isMandatory = relObj.optBoolean("mandatory", relObj.optBoolean("is_mandatory", false)),
                                channel = relObj.optString("channel", "PRODUCTION")
                            )
                        }
                    }
                }

                if (release == null) {
                    setState(UpdateState.UP_TO_DATE)
                    mainHandler.post { onComplete?.invoke(true, null, "No published release") }
                    return@execute
                }

                latestRelease = release
                val installedCode = getInstalledVersionCode()

                if (release.versionCode > installedCode) {
                    setState(UpdateState.UPDATE_AVAILABLE)
                    showUpdateNotification(release)
                    postDeviceStatus(release.releaseId, "UPDATE_AVAILABLE", 0, null)
                    mainHandler.post { onComplete?.invoke(true, release, null) }
                } else {
                    setState(UpdateState.UP_TO_DATE)
                    cancelUpdateNotification()
                    postDeviceStatus(release.releaseId, "UP_TO_DATE", 100, null)
                    mainHandler.post { onComplete?.invoke(true, release, null) }
                }

            } catch (e: Exception) {
                val err = "Update check failed: ${e.message}"
                setState(UpdateState.FAILED, error = err)
                mainHandler.post { onComplete?.invoke(false, null, err) }
            } finally {
                connection?.disconnect()
            }
        }
    }

    /**
     * Download the latest APK with progress reporting and run multi-layer verification.
     */
    fun startDownloadAndInstall(release: ReleaseInfo, activity: Activity? = null) {
        if (currentState == UpdateState.DOWNLOADING || currentState == UpdateState.PREPARING) return

        setState(UpdateState.PREPARING, 0)
        showProgressNotification("Preparing update...", 0)
        postDeviceStatus(release.releaseId, "PREPARING", 0, null)

        executor.execute {
            try {
                val updatesDir = File(appContext.cacheDir, "updates")
                if (!updatesDir.exists()) updatesDir.mkdirs()

                val apkFile = File(updatesDir, "strictrecords_v${release.versionCode}.apk")

                // Check if already downloaded and verified
                if (apkFile.exists() && apkFile.length() > 0) {
                    setState(UpdateState.VERIFYING, 100)
                    showProgressNotification("Verifying package...", 100)
                    postDeviceStatus(release.releaseId, "VERIFYING", 100, null)

                    val (valid, _) = verifyApk(apkFile, release)
                    if (valid) {
                        setState(UpdateState.DOWNLOAD_VERIFIED, 100)
                        showProgressNotification("Verified ✓", 100)
                        postDeviceStatus(release.releaseId, "DOWNLOAD_VERIFIED", 100, null)

                        setState(UpdateState.INSTALLATION_PENDING, 100)
                        showProgressNotification("Ready to install", 100)
                        postDeviceStatus(release.releaseId, "INSTALLATION_PENDING", 100, null)

                        rememberPendingInstallation(release)
                        mainHandler.post { launchInstaller(activity ?: appContext, apkFile) }
                        return@execute
                    } else {
                        apkFile.delete()
                    }
                }

                setState(UpdateState.DOWNLOADING, 0)
                showProgressNotification("Downloading: 0%", 0)
                postDeviceStatus(release.releaseId, "DOWNLOADING", 0, null)

                // Download APK binary
                val url = URL(release.downloadUrl)
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 8000
                    readTimeout = 15000

                }

                val code = conn.responseCode
                if (code !in 200..299) {
                    val err = "Download failed with HTTP $code"
                    setState(UpdateState.FAILED, error = err)
                    showProgressNotification("Update failed: $err", 0)
                    postDeviceStatus(release.releaseId, "FAILED", 0, err)
                    return@execute
                }

                val totalBytes = if (release.fileSizeBytes > 0) release.fileSizeBytes else conn.contentLength.toLong()
                val tempFile = File(updatesDir, "strictrecords_v${release.versionCode}.tmp")
                if (tempFile.exists()) tempFile.delete()

                var downloadedBytes = 0L
                var lastProgressReport = 0

                conn.inputStream.use { input ->
                    FileOutputStream(tempFile).use { output ->
                        val buffer = ByteArray(8192)
                        var bytesRead: Int
                        while (input.read(buffer).also { bytesRead = it } != -1) {
                            output.write(buffer, 0, bytesRead)
                            downloadedBytes += bytesRead

                            if (totalBytes > 0) {
                                val progress = ((downloadedBytes * 100) / totalBytes).toInt()
                                if (progress != lastProgressReport && (progress % 5 == 0 || progress == 100)) {
                                    lastProgressReport = progress
                                    setState(UpdateState.DOWNLOADING, progress)
                                    showProgressNotification("Downloading: $progress%", progress)
                                    postDeviceStatus(release.releaseId, "DOWNLOADING", progress, null)
                                }
                            }
                        }
                    }
                }

                if (apkFile.exists()) apkFile.delete()
                if (!tempFile.renameTo(apkFile)) {
                    val err = "Failed to finalize downloaded APK"
                    setState(UpdateState.FAILED, error = err)
                    showProgressNotification("Update failed: $err", 0)
                    postDeviceStatus(release.releaseId, "FAILED", 0, err)
                    return@execute
                }

                // Multi-Layer Verification
                setState(UpdateState.VERIFYING, 100)
                showProgressNotification("Verifying package...", 100)
                postDeviceStatus(release.releaseId, "VERIFYING", 100, null)

                val (isVerified, verifyError) = verifyApk(apkFile, release)
                if (!isVerified) {
                    apkFile.delete()
                    val err = "Security verification failed: $verifyError"
                    setState(UpdateState.FAILED, error = err)
                    showProgressNotification("Verification failed", 0)
                    postDeviceStatus(release.releaseId, "FAILED", 0, err)
                    return@execute
                }

                setState(UpdateState.DOWNLOAD_VERIFIED, 100)
                showProgressNotification("Verified ✓", 100)
                postDeviceStatus(release.releaseId, "DOWNLOAD_VERIFIED", 100, null)

                setState(UpdateState.INSTALLATION_PENDING, 100)
                showProgressNotification("Ready to install", 100)
                postDeviceStatus(release.releaseId, "INSTALLATION_PENDING", 100, null)

                rememberPendingInstallation(release)
                mainHandler.post {
                    launchInstaller(activity ?: appContext, apkFile)
                }

            } catch (e: Exception) {
                val err = "Download error: ${e.message}"
                setState(UpdateState.FAILED, error = err)
                showProgressNotification("Update failed", 0)
                postDeviceStatus(release.releaseId, "FAILED", 0, err)
            }
        }
    }

    /**
     * Multi-Layer Cryptographic and Package Verification:
     * 1. File Size Verification
     * 2. SHA-256 Digest Verification
     * 3. Package Identity & Version Code Verification
     * 4. Cryptographic Signing Certificate Match
     */
    private fun verifyApk(apkFile: File, release: ReleaseInfo): Pair<Boolean, String?> {
        if (!apkFile.exists() || apkFile.length() == 0L) {
            return Pair(false, "APK file is missing or empty")
        }

        // 1. File size check
        if (release.fileSizeBytes > 0 && apkFile.length() != release.fileSizeBytes) {
            return Pair(false, "File size mismatch (expected: ${release.fileSizeBytes}, actual: ${apkFile.length()})")
        }

        // 2. SHA-256 check
        if (release.sha256Checksum.isNotEmpty()) {
            val actualSha256 = calculateSha256(apkFile)
            if (!actualSha256.equals(release.sha256Checksum, ignoreCase = true)) {
                return Pair(false, "SHA-256 checksum mismatch (expected: ${release.sha256Checksum}, actual: $actualSha256)")
            }
        }

        // 3. Package Identity & Version Code check
        val pm = appContext.packageManager
        @Suppress("DEPRECATION")
        val archiveInfo = pm.getPackageArchiveInfo(apkFile.absolutePath, PackageManager.GET_SIGNATURES)
            ?: return Pair(false, "Corrupted APK: Android PackageManager could not parse archive")

        if (archiveInfo.packageName != "com.strictrecords.app") {
            return Pair(false, "Package name mismatch (expected: com.strictrecords.app, actual: ${archiveInfo.packageName})")
        }

        @Suppress("DEPRECATION")
        val archiveVersionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            archiveInfo.longVersionCode.toInt()
        } else {
            archiveInfo.versionCode
        }

        if (archiveVersionCode != release.versionCode) {
            return Pair(false, "Version code in APK ($archiveVersionCode) does not match manifest (${release.versionCode})")
        }

        // 4. Signing certificate verification
        val installedSignatures = getInstalledSignatures()
        if (installedSignatures.isNotEmpty()) {
            @Suppress("DEPRECATION")
            val archiveSignatures = archiveInfo.signatures
            if (archiveSignatures == null || archiveSignatures.isEmpty()) {
                return Pair(false, "APK is not signed")
            }

            var signatureMatch = false
            for (instSig in installedSignatures) {
                for (archSig in archiveSignatures) {
                    if (instSig == archSig) {
                        signatureMatch = true
                        break
                    }
                }
                if (signatureMatch) break
            }

            if (!signatureMatch) {
                return Pair(false, "Signing certificate does not match the currently installed app certificate")
            }
        }

        return Pair(true, null)
    }

    private fun calculateSha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { fis ->
            val buffer = ByteArray(8192)
            var bytesRead: Int
            while (fis.read(buffer).also { bytesRead = it } != -1) {
                digest.update(buffer, 0, bytesRead)
            }
        }
        val bytes = digest.digest()
        val sb = StringBuilder()
        for (b in bytes) {
            sb.append(String.format("%02x", b))
        }
        return sb.toString()
    }

    private fun getInstalledSignatures(): List<Signature> {
        return try {
            val pm = appContext.packageManager
            @Suppress("DEPRECATION")
            val pInfo = pm.getPackageInfo(appContext.packageName, PackageManager.GET_SIGNATURES)
            @Suppress("DEPRECATION")
            pInfo.signatures?.toList() ?: emptyList()
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun rememberPendingInstallation(release: ReleaseInfo) {
        appContext.getSharedPreferences("apk_update_state", Context.MODE_PRIVATE)
            .edit()
            .putString("pending_release_id", release.releaseId)
            .putInt("pending_version_code", release.versionCode)
            .commit()
    }

    /** Called after Android replaces this package and starts the newly installed version. */
    fun reportInstalledAfterPackageReplacement(onComplete: (() -> Unit)? = null) {
        val prefs = appContext.getSharedPreferences("apk_update_state", Context.MODE_PRIVATE)
        val releaseId = prefs.getString("pending_release_id", null)?.takeIf { it.isNotBlank() }
        if (releaseId == null) {
            onComplete?.invoke()
            return
        }
        val expectedVersion = prefs.getInt("pending_version_code", 0)
        val installedVersion = getInstalledVersionCode()
        if (expectedVersion <= 0 || installedVersion < expectedVersion) {
            onComplete?.invoke()
            return
        }

        setState(UpdateState.INSTALLED, 100)
        showProgressNotification("Update installed successfully", 100)
        postDeviceStatus(releaseId, "INSTALLED", 100, null, onComplete)
        prefs.edit()
            .remove("pending_release_id")
            .remove("pending_version_code")
            .apply()
    }

    /**
     * Hand off APK to Android's native package installer using StrictFileProvider.
     */
    fun launchInstaller(contextOrActivity: Context, apkFile: File) {
        try {
            // Android 8.0+ Unknown App Sources Permission Check
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                if (!appContext.packageManager.canRequestPackageInstalls()) {
                    val manageIntent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES).apply {
                        data = Uri.parse("package:${appContext.packageName}")
                        if (contextOrActivity !is Activity) {
                            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        }
                    }
                    contextOrActivity.startActivity(manageIntent)
                    return
                }
            }

            val apkUri = StrictFileProvider.getUriForFile(appContext, apkFile)
            val installIntent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(apkUri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                if (contextOrActivity !is Activity) {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
            }
            contextOrActivity.startActivity(installIntent)

        } catch (e: Exception) {
            val err = "Installation handoff failed: ${e.message}"
            setState(UpdateState.FAILED, error = err)
            latestRelease?.let { postDeviceStatus(it.releaseId, "FAILED", 0, err) }
        }
    }

    /**
     * Synchronize device update lifecycle state to PC backend.
     */
    private fun postDeviceStatus(
        releaseId: String,
        status: String,
        progressPercent: Int,
        lastError: String?,
        onComplete: (() -> Unit)? = null
    ) {
        executor.execute {
            try {
                val db = StrictRecordsLocalDb.getInstance(appContext)
                val reg = db.getDeviceRegistration()
                val devId = reg?.optString("device_id", "")?.takeIf { it.isNotBlank() } ?: db.getStableDeviceId(appContext)
                val baseUrl = getBaseServerUrl()

                val url = URL("$baseUrl/api/device/update_status")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 3000
                    readTimeout = 3000
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                }

                val req = JSONObject().apply {
                    put("device_id", devId)
                    put("release_id", releaseId)
                    put("installed_version_code", getInstalledVersionCode())
                    put("status", status)
                    put("progress_percent", progressPercent)
                    if (lastError != null) put("last_error", lastError)
                }

                OutputStreamWriter(conn.outputStream).use { it.write(req.toString()) }
                conn.responseCode
            } catch (_: Exception) {
            } finally {
                onComplete?.let { mainHandler.post(it) }
            }
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "StrictRoutines Updates",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "Notifies when a new verified version of StrictRoutines is ready to install."
            }
            val nm = appContext.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
            nm?.createNotificationChannel(channel)
        }
    }

    @Suppress("DEPRECATION")
    private fun showUpdateNotification(release: ReleaseInfo) {
        try {
            val nm = appContext.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
                ?: return

            val tapIntent = Intent(appContext, MainActivity::class.java).apply {
                action = UPDATE_ACTION_VIEW
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
                putExtra("release_version_name", release.versionName)
                putExtra("release_version_code", release.versionCode)
            }

            val piFlags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }
            val contentPendingIntent = PendingIntent.getActivity(appContext, 0, tapIntent, piFlags)

            // Notification Action: [ Update Now ]
            val updateNowIntent = Intent(appContext, UpdateActionReceiver::class.java).apply {
                action = UpdateActionReceiver.ACTION_UPDATE_NOW
            }
            val updateNowPi = PendingIntent.getBroadcast(appContext, 101, updateNowIntent, piFlags)

            // Notification Action: [ Later ]
            val laterIntent = Intent(appContext, UpdateActionReceiver::class.java).apply {
                action = UpdateActionReceiver.ACTION_UPDATE_LATER
            }
            val laterPi = PendingIntent.getBroadcast(appContext, 102, laterIntent, piFlags)

            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                android.app.Notification.Builder(appContext, NOTIFICATION_CHANNEL_ID)
            } else {
                android.app.Notification.Builder(appContext)
            }

            val notifTitle = "StrictRoutines update available"
            val notifBody = "StrictRoutines ${release.versionName} is ready to install."
            val notesSummary = if (release.releaseNotes.isNotBlank()) {
                release.releaseNotes.lines().map { it.trim() }.filter { it.isNotEmpty() }.joinToString("\n")
            } else {
                "Improved hourly submissions, Android-PC synchronization, and stability."
            }
            val bigTextContent = "$notifBody\n$notesSummary"

            builder.setContentTitle(notifTitle)
                .setContentText(notifBody)
                .setStyle(android.app.Notification.BigTextStyle().bigText(bigTextContent))
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentIntent(contentPendingIntent)
                .setAutoCancel(true)

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val viewAction = android.app.Notification.Action.Builder(
                    null, "View Update", contentPendingIntent
                ).build()
                val updateAction = android.app.Notification.Action.Builder(
                    null, "Update Now", updateNowPi
                ).build()
                val laterAction = android.app.Notification.Action.Builder(
                    null, "Later", laterPi
                ).build()
                builder.addAction(viewAction)
                builder.addAction(updateAction)
                builder.addAction(laterAction)
            } else {
                builder.addAction(0, "View Update", contentPendingIntent)
                builder.addAction(0, "Update Now", updateNowPi)
                builder.addAction(0, "Later", laterPi)
            }

            nm.notify(NOTIFICATION_ID, builder.build())
        } catch (_: Exception) {}

    }

    fun showProgressNotification(message: String, progress: Int = 0) {
        try {
            val nm = appContext.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
                ?: return

            val tapIntent = Intent(appContext, MainActivity::class.java).apply {
                action = UPDATE_ACTION_VIEW
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            }
            val piFlags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }
            val contentPi = PendingIntent.getActivity(appContext, 0, tapIntent, piFlags)

            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                android.app.Notification.Builder(appContext, NOTIFICATION_CHANNEL_ID)
            } else {
                @Suppress("DEPRECATION")
                android.app.Notification.Builder(appContext)
            }

            builder.setContentTitle("STRICTRECORDS UPDATE")
                .setContentText(message)
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentIntent(contentPi)
                .setOngoing(currentState == UpdateState.DOWNLOADING || currentState == UpdateState.PREPARING || currentState == UpdateState.VERIFYING)
                .setAutoCancel(currentState == UpdateState.INSTALLATION_PENDING || currentState == UpdateState.INSTALLED || currentState == UpdateState.FAILED)

            if (currentState == UpdateState.DOWNLOADING) {
                builder.setProgress(100, progress, false)
            } else if (currentState == UpdateState.PREPARING || currentState == UpdateState.VERIFYING) {
                builder.setProgress(100, 0, true)
            }

            nm.notify(NOTIFICATION_ID, builder.build())
        } catch (_: Exception) {}
    }

    fun getStatusMessage(state: UpdateState, progress: Int = currentProgress, err: String? = lastErrorMessage): String {
        return when (state) {
            UpdateState.IDLE -> "Idle"
            UpdateState.CHECKING -> "Checking for updates..."
            UpdateState.UP_TO_DATE -> "Up to date ✓"
            UpdateState.UPDATE_AVAILABLE -> "Update available"
            UpdateState.PREPARING -> "Preparing update..."
            UpdateState.DOWNLOADING -> "Downloading: $progress%"
            UpdateState.VERIFYING -> "Verifying package..."
            UpdateState.DOWNLOAD_VERIFIED -> "Verified ✓"
            UpdateState.INSTALLATION_PENDING -> "Ready to install"
            UpdateState.INSTALLED -> "Installed"
            UpdateState.FAILED -> "Update failed: ${err ?: "Unknown error"}"
        }
    }

    fun cancelUpdateNotification() {
        try {
            val nm = appContext.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
            nm?.cancel(NOTIFICATION_ID)
        } catch (_: Exception) {}
    }
}
