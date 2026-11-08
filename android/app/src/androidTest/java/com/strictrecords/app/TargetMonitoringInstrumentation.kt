package com.strictrecords.app

import android.app.Activity
import android.app.Instrumentation
import android.app.UiAutomation
import android.os.Bundle
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import android.os.ParcelFileDescriptor

/**
 * Device regression runner: launches explicitly supplied apps and reads monitoring
 * metadata. It never grants access, changes selection/consent, writes scores,
 * interacts with payment UI, reads accessibility text, or dispatches music controls.
 */
class TargetMonitoringInstrumentation : Instrumentation() {
    private var arguments = Bundle()
    private val packagePattern = Regex("[A-Za-z][A-Za-z0-9_]*(?:\\.[A-Za-z][A-Za-z0-9_]*)+")
    private val componentPattern = Regex("([A-Za-z][A-Za-z0-9_]*(?:\\.[A-Za-z][A-Za-z0-9_]*)+)/(\\.?[A-Za-z_][A-Za-z0-9_.$]*)")
    private lateinit var automation: UiAutomation
    private val failures = JSONArray()
    private val limitations = JSONArray()
    private val samples = JSONArray()
    private val launches = JSONArray()
    private var targetMonitorSamples = 0
    private var targetOverlaySamples = 0
    private var paymentSamples = 0
    private var interruptedByConsent = false
    private var accessibilityObserved = false

    override fun onCreate(arguments: Bundle?) {
        this.arguments = arguments ?: Bundle()
        super.onCreate(arguments)
        start()
    }

    override fun onStart() {
        val report = JSONObject().apply {
            put("runner", "TargetMonitoringInstrumentation")
            put("read_only", true)
            put("started_at_ms", System.currentTimeMillis())
            put("target_dwell_ms", 250)
            put("payment_sample_offsets_ms", JSONArray(listOf(0, 150, 350)))
            put("target_rapid_sample_offsets_ms", JSONArray(listOf(0, 150)))
        }
        try {
            val configuration = configurationSnapshot()
            report.put("configuration", configuration)
            if (!configuration.optBoolean("can_read_usage", false)) {
                report.put("status", "NEEDS_USER_CONSENT")
                report.put("mode", "DISABLED_NO_MONITORING_CONSENT_OR_ACCESS")
                report.put("active_cases_skipped", true)
                report.put("skip_reason", configuration.optString("monitoring_status"))
                complete(report, passed = true)
                return
            }
            if (!configuration.optBoolean("screen_available", false)) {
                report.put("status", "NEEDS_UNLOCKED_SCREEN")
                report.put("active_cases_skipped", true)
                report.put("skip_reason", "The screen is locked or inactive. No app launch was attempted.")
                complete(report, passed = true)
                return
            }

            val paymentPackages = parsePaymentPackages(arguments.getString("payment_packages").orEmpty())
            if (paymentPackages.isEmpty()) {
                report.put("status", "NEEDS_PAYMENT_PACKAGE_ARGUMENT")
                report.put("active_cases_skipped", true)
                report.put("skip_reason", "Provide explicit payment_packages CSV. No payment app was guessed or launched.")
                complete(report, passed = true)
                return
            }
            val requestedCycles = arguments.getString("cycles")?.toIntOrNull() ?: 5
            require(requestedCycles in 1..5) { "cycles must be between 1 and 5" }
            report.put("cycles_per_target_payment_pair", requestedCycles)
            report.put("payment_packages", JSONArray(paymentPackages))

            // Preserve the genuine accessibility service during instrumentation.
            automation = getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES)
            report.put("accessibility_services_preserved", true)
            val accessibilityConnected = onMain { StrictAccessibilityService.activeInstance != null }
            accessibilityObserved = accessibilityConnected
            report.put("mode", if (accessibilityConnected) "MONITORING_AND_LIVE_WINDOW_ENFORCEMENT" else "MONITORING_ONLY_NO_ACCESSIBILITY")
            if (!accessibilityConnected) limitations.put("Accessibility is unavailable. Usage-event target detection is exercised; intrusive target overlay enforcement is intentionally unavailable.")

            val selectedPackages = onMain {
                TargetAppMonitoringPolicy.installedTargets(targetContext, forceRefresh = true)
                    .filter { it.selected && it.available }.flatMap { it.installedPackages }.distinct().sorted()
            }
            report.put("selected_installed_packages", JSONArray(selectedPackages))
            validateUsageScope("before_switching")

            for (target in selectedPackages) {
                if (!stillConsented()) break
                // A separate baseline supplies all three requested target timestamps.
                // Rapid cycles keep the target dwell at 250ms before switching away.
                if (!launch(target, "target_baseline", -1)) continue
                sampleAtOffsets(target, "target_baseline", -1, listOf(0L, 150L, 350L))
                for (payment in paymentPackages) {
                    for (cycle in 0 until requestedCycles) {
                        if (!stillConsented()) break
                        if (!launch(target, "target", cycle)) break
                        val targetStarted = SystemClock.elapsedRealtime()
                        sampleAtOffsets(target, "target", cycle, listOf(0L, 150L), targetStarted)
                        sleepUntil(targetStarted + 250L)
                        if (!launch(payment, "payment", cycle)) break
                        sampleAtOffsets(payment, "payment", cycle, listOf(0L, 150L, 350L))
                    }
                    if (interruptedByConsent) break
                }
                if (interruptedByConsent) break
            }
            if (!interruptedByConsent) validateUsageScope("after_switching")
            if (targetMonitorSamples == 0) limitations.put("No positive target foreground detection was observed; selected-app enforcement cannot be claimed as device-verified.")
            if (targetOverlaySamples == 0) limitations.put("No target overlay was observed. Scores, permissions, service readiness or unknown foreground can legitimately prevent overlay enforcement; these settings were not changed.")
            if (paymentSamples == 0) limitations.put("No payment foreground sample was obtained; payment switching is unverified.")
            report.put("active_cases_skipped", interruptedByConsent)
            report.put("status", when {
                failures.length() > 0 -> "FAILED"
                interruptedByConsent -> "NEEDS_USER_CONSENT"
                targetMonitorSamples == 0 || paymentSamples == 0 -> "INCOMPLETE_VERIFICATION"
                else -> "PASSED"
            })
            complete(report, passed = failures.length() == 0)
        } catch (error: Throwable) {
            report.put("status", "FAILED")
            // Do not return bank/UI text, raw shell dumps or exception stack contents.
            failures.put(JSONObject().put("kind", "RUNNER_ERROR").put("error_type", error.javaClass.simpleName)
                .put("reason", if (error is IllegalArgumentException) "Invalid package or cycle argument." else "A device operation could not be completed."))
            complete(report, passed = false)
        }
    }

    private fun parsePaymentPackages(csv: String): List<String> {
        val result = csv.split(',').map { it.trim() }.filter { it.isNotEmpty() }.distinct()
        require(result.size <= 4) { "At most four explicitly provided payment packages are supported" }
        for (packageName in result) {
            require(packageName.length <= 180 && packagePattern.matches(packageName)) { "Invalid package argument" }
            require(!TargetAppMonitoringPolicy.isTargetPackage(packageName)) { "Payment argument must not be a monitored target" }
            require(packageName != targetContext.packageName) { "Payment argument must not be StrictRecords" }
        }
        return result
    }

    private fun configurationSnapshot(): JSONObject = onMain {
        JSONObject().apply {
            put("explicit_usage_consent", TargetAppMonitoringPolicy.hasUsageConsent(targetContext))
            put("usage_access_granted", HardwareDeviceVerifier.hasUsageStatsPermission(targetContext))
            put("selected_target_ids", JSONArray(TargetAppMonitoringPolicy.selectedTargetIds(targetContext).sorted()))
            put("can_read_usage", TargetAppMonitoringPolicy.canReadUsage(targetContext))
            put("monitoring_status", TargetAppMonitoringPolicy.monitoringStatus(targetContext))
            put("accessibility_connected", StrictAccessibilityService.activeInstance != null)
            put("screen_available", HardwareDeviceVerifier.isMonitoringScreenAvailable(targetContext))
        }
    }

    private fun stillConsented(): Boolean {
        val allowed = onMain { TargetAppMonitoringPolicy.canReadUsage(targetContext) }
        if (!allowed && !interruptedByConsent) {
            interruptedByConsent = true
            limitations.put("Consent, selection, installation or Usage Access changed during the run. Remaining active cases were skipped.")
        }
        return allowed
    }

    private fun launch(packageName: String, phase: String, cycle: Int): Boolean {
        require(packagePattern.matches(packageName))
        // No action/data flags that start playback, payments or private screens.
        val command = "am start -W -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p $packageName"
        var ok = false
        var actualComponent: String? = null
        readShellLines(command) { line ->
            if (line.trim() == "Status: ok") ok = true
            if (line.trimStart().startsWith("Activity:")) actualComponent = componentPattern.find(line)?.value
        }
        launches.put(JSONObject().apply {
            put("phase", phase); put("cycle", cycle); put("requested_package", packageName)
            put("status_ok", ok); put("started_component", actualComponent ?: JSONObject.NULL)
        })
        if (!ok) {
            limitations.put("Launcher start was unavailable for $packageName. Its cases were not verified.")
            return false
        }
        val actualPackage = actualComponent?.substringBefore('/')
        if (actualPackage != null && actualPackage != packageName) {
            failures.put(JSONObject().put("kind", "UNEXPECTED_LAUNCH_REDIRECT").put("phase", phase)
                .put("expected_package", packageName).put("actual_package", actualPackage))
            return false
        }
        return true
    }

    private fun sampleAtOffsets(expectedPackage: String, phase: String, cycle: Int, offsets: List<Long>, start: Long = SystemClock.elapsedRealtime()) {
        for (offset in offsets) {
            sleepUntil(start + offset)
            if (!stillConsented()) return
            val result = onMain {
                val sampledAt = SystemClock.elapsedRealtime()
                val observation = HardwareDeviceVerifier.detectForegroundObservation(targetContext)
                JSONObject().apply {
                    put("phase", phase); put("cycle", cycle); put("expected_package", expectedPackage)
                    put("requested_offset_ms", offset); put("actual_offset_ms", sampledAt - start)
                    put("observation_status", observation.status)
                    put("observed_target_package", observation.packageName ?: JSONObject.NULL)
                    put("overlay_showing", StrictBotOverlayController.isRestrictionOverlayShowing())
                    put("accessibility_connected", StrictAccessibilityService.activeInstance != null)
                    put("screen_available", HardwareDeviceVerifier.isMonitoringScreenAvailable(targetContext))
                }
            }
            if (result.optBoolean("accessibility_connected")) accessibilityObserved = true
            if (phase == "payment") {
                paymentSamples++
                if (result.optString("observation_status") == TargetAppMonitoringPolicy.MONITOR)
                    failures.put(failure("PAYMENT_MONITORED", expectedPackage, cycle, offset, result))
                if (result.optBoolean("overlay_showing"))
                    failures.put(failure("STRICTBOT_OVER_PAYMENT", expectedPackage, cycle, offset, result))
                if (offset == offsets.last()) {
                    val resumedPackage = resumedPackageMetadata()
                    result.put("resumed_package_metadata", resumedPackage ?: JSONObject.NULL)
                    if (resumedPackage != null && resumedPackage != expectedPackage)
                        failures.put(JSONObject().put("kind", "UNEXPECTED_PAYMENT_REDIRECT").put("expected_package", expectedPackage)
                            .put("actual_package", resumedPackage).put("cycle", cycle).put("offset_ms", offset))
                    if (resumedPackage == null) limitations.put("Resumed-activity metadata unavailable for $expectedPackage at cycle $cycle; redirect check is incomplete.")
                }
            } else {
                if (result.optString("observation_status") == TargetAppMonitoringPolicy.MONITOR && result.optString("observed_target_package") == expectedPackage) targetMonitorSamples++
                if (result.optBoolean("overlay_showing")) targetOverlaySamples++
            }
            samples.put(result)
        }
    }

    private fun failure(kind: String, expectedPackage: String, cycle: Int, offset: Long, result: JSONObject) = JSONObject().apply {
        put("kind", kind); put("expected_package", expectedPackage); put("cycle", cycle); put("offset_ms", offset)
        put("observed_target_package", result.opt("observed_target_package")); put("overlay_showing", result.optBoolean("overlay_showing"))
    }

    private fun validateUsageScope(phase: String) {
        val summaryPackages = onMain { HardwareDeviceVerifier.collectLastHourUsageSummary(targetContext).second }
        val selectedPackages = onMain {
            TargetAppMonitoringPolicy.installedTargets(targetContext).filter { it.selected }.flatMap { it.installedPackages }.toSet()
        }
        for (packageName in summaryPackages) {
            if (packageName !in selectedPackages || !TargetAppMonitoringPolicy.isTargetPackage(packageName))
                failures.put(JSONObject().put("kind", "NON_SELECTED_USAGE_RETAINED").put("phase", phase).put("unexpected_package", packageName))
        }
        samples.put(JSONObject().put("phase", phase).put("selected_usage_packages", JSONArray(summaryPackages)))
    }

    /** Only resumed activity component metadata is retained. No view hierarchy or text is read. */
    private fun resumedPackageMetadata(): String? {
        var top: String? = null
        var resumed: String? = null
        readShellLines("dumpsys activity activities") { line ->
            if (line.contains("topResumedActivity") || line.contains("mResumedActivity")) {
                val packageName = componentPattern.find(line)?.groupValues?.get(1)
                if (line.contains("topResumedActivity") && packageName != null) top = packageName
                else if (resumed == null) resumed = packageName
            }
        }
        return top ?: resumed
    }

    private fun readShellLines(command: String, consume: (String) -> Unit) {
        val descriptor = automation.executeShellCommand(command)
        ParcelFileDescriptor.AutoCloseInputStream(descriptor).bufferedReader().use { reader ->
            var chars = 0
            while (chars < 512 * 1024) {
                val line = reader.readLine() ?: break
                chars += line.length
                consume(line)
            }
        }
    }

    private fun sleepUntil(deadline: Long) {
        val remaining = deadline - SystemClock.elapsedRealtime()
        if (remaining > 0L) SystemClock.sleep(remaining)
    }

    private fun <T> onMain(read: () -> T): T {
        var result: T? = null
        var failure: Throwable? = null
        runOnMainSync { try { result = read() } catch (error: Throwable) { failure = error } }
        failure?.let { throw it }
        @Suppress("UNCHECKED_CAST")
        return result as T
    }

    private fun complete(report: JSONObject, passed: Boolean) {
        report.put("target_monitor_samples", targetMonitorSamples)
        report.put("target_overlay_samples", targetOverlaySamples)
        report.put("payment_samples", paymentSamples)
        if (samples.length() > 0) report.put("mode", if (accessibilityObserved) "MONITORING_AND_LIVE_WINDOW_ENFORCEMENT" else "MONITORING_ONLY_NO_ACCESSIBILITY")
        report.put("launches", launches)
        report.put("samples", samples)
        report.put("failures", failures)
        report.put("limitations", limitations)
        report.put("finished_at_ms", System.currentTimeMillis())
        finish(if (passed) Activity.RESULT_OK else Activity.RESULT_CANCELED, Bundle().apply {
            putString("target_monitoring_report", report.toString())
            putString("stream", report.toString(2))
        })
    }
}
