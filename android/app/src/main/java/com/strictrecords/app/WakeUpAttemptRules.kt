package com.strictrecords.app

object WakeUpAttemptRules {
    fun selectedRouteMatches(selectedName: String, selectedAddress: String, routeName: String, routeAddress: String, bluetooth: Boolean): Boolean {
        if (!bluetooth || selectedName.isBlank() || selectedName.equals("None configured", true)) return false
        if (selectedName.equals("Any Bluetooth Audio Output", true)) return bluetooth

        val cleanSelName = selectedName.trim()
        val cleanRouteName = routeName.trim()
        val cleanSelAddr = selectedAddress.trim().replace("-", ":")
        val cleanRouteAddr = routeAddress.trim().replace("-", ":")

        val nameMatches = cleanSelName.equals(cleanRouteName, ignoreCase = true) ||
            cleanRouteName.equals("Bluetooth Audio Output", ignoreCase = true) ||
            cleanRouteName.equals("Bluetooth Audio Device", ignoreCase = true) ||
            cleanRouteName.equals("Bluetooth Audio", ignoreCase = true) ||
            cleanRouteName.equals("Bluetooth", ignoreCase = true) ||
            cleanRouteName.equals("Wireless Earbuds", ignoreCase = true) ||
            cleanRouteName.equals("Wireless Headphones", ignoreCase = true) ||
            cleanRouteName.equals("Headphones", ignoreCase = true) ||
            cleanRouteName.equals("Headset", ignoreCase = true) ||
            (cleanRouteName.isNotBlank() && (cleanRouteName.contains(cleanSelName, ignoreCase = true) || cleanSelName.contains(cleanRouteName, ignoreCase = true)))

        val isRedactedSel = cleanSelAddr.equals("02:00:00:00:00:00", ignoreCase = true) ||
            cleanSelAddr.equals("00:00:00:00:00:00", ignoreCase = true)
        val isRedactedRoute = cleanRouteAddr.equals("02:00:00:00:00:00", ignoreCase = true) ||
            cleanRouteAddr.equals("00:00:00:00:00:00", ignoreCase = true)

        val hasRealSelAddr = cleanSelAddr.isNotBlank() && !isRedactedSel
        val hasRealRouteAddr = cleanRouteAddr.isNotBlank() && !isRedactedRoute

        if (hasRealSelAddr && hasRealRouteAddr) {
            if (cleanSelAddr.equals(cleanRouteAddr, ignoreCase = true)) return true
            // Support true-wireless earbud pairs where left/right channels share OUI prefix
            if (cleanSelAddr.contains(":") && cleanRouteAddr.contains(":") &&
                cleanSelAddr.substringBeforeLast(":").equals(cleanRouteAddr.substringBeforeLast(":"), ignoreCase = true) &&
                nameMatches
            ) {
                return true
            }
            return false
        }

        return nameMatches
    }

    data class Completion(
        val attemptId: String, val cameraAttemptId: String, val routeAttemptId: String, val playbackAttemptId: String,
        val cameraPassed: Boolean, val routeVerified: Boolean, val trackCompleted: Boolean,
        val durationMs: Long, val listenedMs: Long, val routeVerifiedMs: Long, val seekAttempts: Int,
        val trackHashMatches: Boolean, val windowOpen: Boolean, val demo: Boolean,
        val earbudsOptional: Boolean = false, val audioOptional: Boolean = false
    )
    fun rejection(c: Completion): String? = when {
        c.demo -> "DEMO_CANNOT_SUBMIT"
        !c.windowOpen -> "WAKEUP_WINDOW_CLOSED"
        c.attemptId.isBlank() || c.cameraAttemptId != c.attemptId -> "ATTEMPT_MISMATCH"
        !c.earbudsOptional && c.routeAttemptId != c.attemptId -> "ATTEMPT_MISMATCH"
        !c.audioOptional && c.playbackAttemptId != c.attemptId -> "ATTEMPT_MISMATCH"
        !c.cameraPassed -> "CAMERA_PROOF_INCOMPLETE"
        !c.earbudsOptional && !c.routeVerified -> "SELECTED_AUDIO_ROUTE_NOT_VERIFIED"
        !c.audioOptional && (!c.trackCompleted || c.durationMs <= 0 || c.listenedMs < c.durationMs || c.routeVerifiedMs < c.durationMs) -> "FULL_TRACK_INCOMPLETE"
        !c.audioOptional && c.seekAttempts != 0 -> "PLAYBACK_SEEK_DETECTED"
        !c.audioOptional && !c.trackHashMatches -> "CONFIGURED_TRACK_MISMATCH"
        else -> null
    }
}
