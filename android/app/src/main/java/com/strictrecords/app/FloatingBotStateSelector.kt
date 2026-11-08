package com.strictrecords.app

import android.content.Context
import java.util.Locale

/**
 * Central state selector for the animated floating discipline bot overlay.
 *
 * Implements strict message priority:
 * 1. Accessibility service disabled beyond the five-minute grace period:
 *    "Please turn on the accessibility"
 * 2. Entertainment access denied because of insufficient points:
 *    "No Entertainment apps unless sufficient points"
 */
object FloatingBotStateSelector {

    const val MSG_INSUFFICIENT_POINTS = "No Entertainment apps unless sufficient points"
    const val MSG_ACCESSIBILITY_DISABLED = "Please turn on the accessibility"

    enum class MessageState(val priority: Int, val defaultMessage: String) {
        ACCESSIBILITY_DISABLED(1, MSG_ACCESSIBILITY_DISABLED),
        INSUFFICIENT_POINTS(2, MSG_INSUFFICIENT_POINTS),
        NONE(999, "")
    }

    data class Decision(
        val shouldShow: Boolean,
        val state: MessageState,
        val message: String,
        val foregroundPackage: String,
        val targetAppId: String,
        val targetAppName: String,
        val currentScore: Int,
        val requiredScore: Int,
        val isEntertainmentApp: Boolean,
        val isAccessibilityInterrupted: Boolean,
        val isGracePeriodActive: Boolean,
        val isGracePeriodExpired: Boolean,
        val graceRemainingMs: Long,
        val reason: String
    ) {
        val requiresAccessibilityAction: Boolean
            get() = state == MessageState.ACCESSIBILITY_DISABLED
    }

    val SYSTEM_CRITICAL_PACKAGES = setOf(
        "android",
        "com.android.systemui",
        "com.android.phone",
        "com.google.android.dialer",
        "com.samsung.android.dialer",
        "com.android.emergency",
        "com.google.android.emergency",
        "com.android.packageinstaller",
        "com.google.android.packageinstaller",
        "com.android.permissioncontroller",
        "com.google.android.permissioncontroller"
    )

    /**
     * Determines whether the floating bot overlay should be displayed,
     * and which exact message state has priority.
     */
    fun evaluate(
        foregroundPackage: String?,
        currentScore: Int,
        accessibilityStatus: AccessibilityGraceManager.AccessibilityStatus?,
        context: Context? = null,
        isFinancialPauseActive: Boolean = false,
        isEducationalUnlockActive: Boolean = false,
        isGymModeActive: Boolean = false,
        appLocksMap: Map<String, Boolean>? = null,
        confirmedAccessibilityWarning: Boolean? = null,
        isYouTubeRewardActive: Boolean? = null
    ): Decision {
        val pkg = foregroundPackage?.trim().orEmpty()

        if (pkg.isBlank()) {
            return buildHiddenDecision(
                pkg = "",
                currentScore = currentScore,
                reason = "No foreground package specified"
            )
        }

        // 1. Never show over system critical, emergency, or dialer packages
        if (pkg in SYSTEM_CRITICAL_PACKAGES || pkg.startsWith("com.android.settings") && pkg != "com.android.settings") {
            return buildHiddenDecision(
                pkg = pkg,
                currentScore = currentScore,
                reason = "System critical / emergency package excluded"
            )
        }

        // 2. Never show during financial safe pause or over banking / UPI / financial apps
        val isFinancialProtected = if (context != null) {
            FinancialSafePause.isProtectedFinancialPackage(context, pkg) ||
                FinancialSafePause.isPauseActive(context)
        } else {
            pkg in TargetAppMonitoringPolicy.DEFAULT_FINANCIAL_PACKAGES
        }
        if (isFinancialPauseActive || isFinancialProtected) {
            return buildHiddenDecision(
                pkg = pkg,
                currentScore = currentScore,
                reason = "Financial / banking safety protection active"
            )
        }

        // 3. Check if the package is a monitored entertainment application
        val targetApp = TargetAppMonitoringPolicy.targetForPackage(pkg)
        if (targetApp == null) {
            return buildHiddenDecision(
                pkg = pkg,
                currentScore = currentScore,
                reason = "Not a monitored entertainment application"
            )
        }

        val targetAppId = targetApp.id
        val targetAppName = targetApp.displayName
        val isMusic = targetApp.isMusic
        val targetThreshold = if (isMusic) 60 else 80
        val accessScore = DemoAccessManager.effectivePoints(context, currentScore)

        // 4. Accessibility grace-period analysis
        val isInterrupted = accessibilityStatus?.isInterrupted == true
        val remainingGraceMs = accessibilityStatus?.remainingGraceMs ?: 0L
        val isGraceActive = isInterrupted && remainingGraceMs > 0L
        val isGraceExpired = confirmedAccessibilityWarning ?: (isInterrupted && remainingGraceMs <= 0L &&
            accessibilityStatus?.missingAccessType in setOf(AccessibilityGraceManager.MISSING_TYPE_ACCESSIBILITY,
                AccessibilityGraceManager.MISSING_TYPE_BOTH) &&
            accessibilityStatus?.stateCode == AccessibilityGraceManager.STATE_PENALTY_ELIGIBLE)

        // PRIORITY 1: Accessibility service disabled beyond 5-minute grace period
        if (isGraceExpired) {
            return Decision(
                shouldShow = true,
                state = MessageState.ACCESSIBILITY_DISABLED,
                message = MSG_ACCESSIBILITY_DISABLED,
                foregroundPackage = pkg,
                targetAppId = targetAppId,
                targetAppName = targetAppName,
                currentScore = currentScore,
                requiredScore = targetThreshold,
                isEntertainmentApp = true,
                isAccessibilityInterrupted = true,
                isGracePeriodActive = false,
                isGracePeriodExpired = true,
                graceRemainingMs = 0L,
                reason = "Accessibility service disabled beyond 5-minute grace period"
            )
        }

        // 5. Evaluate points restriction
        val isGymAllowed = isMusic && isGymModeActive
        val hasRemoteLock = appLocksMap?.containsKey(targetAppId) == true
        val requestedLock = if (hasRemoteLock) appLocksMap?.get(targetAppId) == true else false

        val youtubeReward = targetAppId == "youtube" && (isYouTubeRewardActive ?: YouTubeRewardManager.isYouTubeActive(context))
        val isRestrictedByScore = if (youtubeReward) {
            if (context != null) StrictRecordsLocalDb.getInstance(context).rewardIndependentRestriction(targetAppId, requestedLock) else requestedLock
        } else if (isGymAllowed) {
            false
        } else if (hasRemoteLock) {
            requestedLock || accessScore < targetThreshold
        } else {
            accessScore < targetThreshold
        }

        // PRIORITY 2: Insufficient points to access entertainment app
        if (isRestrictedByScore) {
            return Decision(
                shouldShow = true,
                state = MessageState.INSUFFICIENT_POINTS,
                message = MSG_INSUFFICIENT_POINTS,
                foregroundPackage = pkg,
                targetAppId = targetAppId,
                targetAppName = targetAppName,
                currentScore = currentScore,
                requiredScore = targetThreshold,
                isEntertainmentApp = true,
                isAccessibilityInterrupted = isInterrupted,
                isGracePeriodActive = isGraceActive,
                isGracePeriodExpired = false,
                graceRemainingMs = remainingGraceMs,
                reason = "Insufficient access points ($accessScore/$targetThreshold) for $targetAppName; actual points: $currentScore"
            )
        }

        // 6. Access permitted (sufficient points and accessibility valid or in grace)
        return buildHiddenDecision(
            pkg = pkg,
            currentScore = currentScore,
            targetAppId = targetAppId,
            targetAppName = targetAppName,
            requiredScore = targetThreshold,
            isEntertainment = true,
            isInterrupted = isInterrupted,
            isGraceActive = isGraceActive,
            remainingGraceMs = remainingGraceMs,
            reason = "Access permitted: effective access points $accessScore >= $targetThreshold; actual points: $currentScore"
        )
    }

    private fun buildHiddenDecision(
        pkg: String,
        currentScore: Int,
        targetAppId: String = "",
        targetAppName: String = "",
        requiredScore: Int = 0,
        isEntertainment: Boolean = false,
        isInterrupted: Boolean = false,
        isGraceActive: Boolean = false,
        remainingGraceMs: Long = 0L,
        reason: String
    ): Decision {
        return Decision(
            shouldShow = false,
            state = MessageState.NONE,
            message = "",
            foregroundPackage = pkg,
            targetAppId = targetAppId,
            targetAppName = targetAppName,
            currentScore = currentScore,
            requiredScore = requiredScore,
            isEntertainmentApp = isEntertainment,
            isAccessibilityInterrupted = isInterrupted,
            isGracePeriodActive = isGraceActive,
            isGracePeriodExpired = false,
            graceRemainingMs = remainingGraceMs,
            reason = reason
        )
    }
}
