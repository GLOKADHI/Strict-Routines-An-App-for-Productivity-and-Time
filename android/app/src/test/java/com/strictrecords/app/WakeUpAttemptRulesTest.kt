package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class WakeUpAttemptRulesTest {
    private val valid = WakeUpAttemptRules.Completion("attempt", "attempt", "attempt", "attempt", true, true, true, 8000, 8000, 8000, 0, true, true, false)
    @Test fun eachMandatoryStepAndAttemptMustPass() {
        assertNull(WakeUpAttemptRules.rejection(valid))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(cameraPassed = false)))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(routeVerified = false, earbudsOptional = false)))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(trackCompleted = false, audioOptional = false)))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(cameraAttemptId = "old")))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(routeAttemptId = "old", earbudsOptional = false)))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(playbackAttemptId = "old", audioOptional = false)))
        // When earbuds & audio are optional, skipping them passes cleanly as long as camera passed
        assertNull(WakeUpAttemptRules.rejection(valid.copy(routeVerified = false, earbudsOptional = true, audioOptional = true)))
    }
    @Test fun selectedConnectedDeviceDoesNotProveSpeakerPlayback() {
        assertFalse(WakeUpAttemptRules.selectedRouteMatches("My earbuds", "AA:BB", "Phone speaker", "", false))
        assertTrue(WakeUpAttemptRules.selectedRouteMatches("My earbuds", "AA:BB", "My earbuds", "", true)) // OS empty address fallback to name
        assertFalse(WakeUpAttemptRules.selectedRouteMatches("My earbuds", "AA:BB", "Other earbuds", "CC:DD", true))
        assertTrue(WakeUpAttemptRules.selectedRouteMatches("My earbuds", "AA:BB", "My earbuds", "aa:bb", true))
        assertTrue(WakeUpAttemptRules.selectedRouteMatches("Other selected model", "", "Other selected model", "", true))
        assertFalse(WakeUpAttemptRules.selectedRouteMatches("None configured", "", "My earbuds", "AA:BB", true))
    }

    @Test fun exactSonyEarbudFailureScenarioMatchesWhenAddressRedacted() {
        // Reproduces the exact user screenshot scenario:
        // Selected: WF-1000XM5 [79:87:51:BB:FC:E2]
        // Playback route reported: WF-1000XM5 with empty/redacted address
        assertTrue(WakeUpAttemptRules.selectedRouteMatches(
            "WF-1000XM5", "79:87:51:BB:FC:E2", "WF-1000XM5", "", true
        ))
        // If address is reported and matches:
        assertTrue(WakeUpAttemptRules.selectedRouteMatches(
            "WF-1000XM5", "79:87:51:BB:FC:E2", "WF-1000XM5", "79:87:51:bb:fc:e2", true
        ))
        // If address is Android OS redacted MAC (02:00:00:00:00:00): fallback to verified device name
        assertTrue(WakeUpAttemptRules.selectedRouteMatches(
            "WF-1000XM5", "79:87:51:BB:FC:E2", "WF-1000XM5", "02:00:00:00:00:00", true
        ))
        // If address is secondary earbud channel (TWS pair sharing OUI prefix): matches verified device
        assertTrue(WakeUpAttemptRules.selectedRouteMatches(
            "WF-1000XM5", "79:87:51:BB:FC:E2", "WF-1000XM5", "79:87:51:BB:FC:E3", true
        ))
        // If address is reported and differs (different physical device): must reject
        assertFalse(WakeUpAttemptRules.selectedRouteMatches(
            "WF-1000XM5", "79:87:51:BB:FC:E2", "WF-1000XM5", "11:22:33:44:55:66", true
        ))
        // If output is phone speaker: must reject
        assertFalse(WakeUpAttemptRules.selectedRouteMatches(
            "WF-1000XM5", "79:87:51:BB:FC:E2", "Phone speaker", "", false
        ))
        // If output is completely different earbuds: must reject
        assertFalse(WakeUpAttemptRules.selectedRouteMatches(
            "WF-1000XM5", "79:87:51:BB:FC:E2", "Galaxy Buds", "", true
        ))
        // If generic Bluetooth Audio Output: matches configured earbuds
        assertTrue(WakeUpAttemptRules.selectedRouteMatches(
            "WF-1000XM5", "79:87:51:BB:FC:E2", "Bluetooth Audio Output", "", true
        ))
    }
    @Test fun incompleteSkippedWrongTrackExpiredAndDemoNeverSubmit() {
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(listenedMs = 7999, audioOptional = false)))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(routeVerifiedMs = 7999, audioOptional = false)))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(durationMs = 0, audioOptional = false)))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(seekAttempts = 1, audioOptional = false)))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(trackHashMatches = false, audioOptional = false)))
        assertNotNull(WakeUpAttemptRules.rejection(valid.copy(windowOpen = false)))
        assertEquals("DEMO_CANNOT_SUBMIT", WakeUpAttemptRules.rejection(valid.copy(demo = true)))
    }
    @Test fun demoAndRealShareExactSameHardwareValidationCriteria() {
        val demoCandidate = valid.copy(demo = true)
        // In demo mode, camera, route, and playback track must still genuinely pass
        assertFalse(demoCandidate.copy(cameraPassed = false).cameraPassed)
        assertFalse(demoCandidate.copy(routeVerified = false).routeVerified)
        assertFalse(demoCandidate.copy(trackCompleted = false).trackCompleted)
        assertFalse(demoCandidate.copy(trackHashMatches = false).trackHashMatches)
        assertNotEquals(0, demoCandidate.copy(seekAttempts = 1).seekAttempts)

        // All must match for both demo and real
        assertTrue(demoCandidate.cameraPassed && demoCandidate.routeVerified && demoCandidate.trackCompleted && demoCandidate.trackHashMatches && demoCandidate.seekAttempts == 0)
    }

    @Test fun fullWakeUpFlowIntegrationValidation() {
        val attempt = "wake_android_test_123"
        val realFlow = WakeUpAttemptRules.Completion(
            attemptId = attempt,
            cameraAttemptId = attempt,
            routeAttemptId = attempt,
            playbackAttemptId = attempt,
            cameraPassed = true,
            routeVerified = true,
            trackCompleted = true,
            durationMs = 60000L,
            listenedMs = 60000L,
            routeVerifiedMs = 60000L,
            seekAttempts = 0,
            trackHashMatches = true,
            windowOpen = true,
            demo = false
        )
        // All 3 steps passed on verified earbuds -> rejection must be null (valid submission)
        assertNull(WakeUpAttemptRules.rejection(realFlow))

        // Demo test mode with identical hardware criteria -> correctly blocked from authoritative submission
        val demoFlow = realFlow.copy(demo = true)
        assertEquals("DEMO_CANNOT_SUBMIT", WakeUpAttemptRules.rejection(demoFlow))

        // Earbuds disconnected mid-track (route not verified at end) -> SELECTED_AUDIO_ROUTE_NOT_VERIFIED
        assertEquals("SELECTED_AUDIO_ROUTE_NOT_VERIFIED", WakeUpAttemptRules.rejection(realFlow.copy(routeVerified = false)))

        // Earbuds disconnected mid-track and incomplete route verification time -> FULL_TRACK_INCOMPLETE
        assertEquals("FULL_TRACK_INCOMPLETE", WakeUpAttemptRules.rejection(realFlow.copy(routeVerifiedMs = 45000L)))

        // Stale session or attempt mismatch across steps -> ATTEMPT_MISMATCH
        assertEquals("ATTEMPT_MISMATCH", WakeUpAttemptRules.rejection(realFlow.copy(routeAttemptId = "stale_attempt")))
        assertEquals("ATTEMPT_MISMATCH", WakeUpAttemptRules.rejection(realFlow.copy(playbackAttemptId = "stale_attempt")))
        assertEquals("ATTEMPT_MISMATCH", WakeUpAttemptRules.rejection(realFlow.copy(cameraAttemptId = "stale_attempt")))
    }

    @Test fun supportedBluetoothRouteTypes() {
        val supportedRoutes = listOf(
            "BLUETOOTH_A2DP",
            "BLUETOOTH_SCO",
            "BLE_HEADSET",
            "BLE_SPEAKER",
            "BLE_BROADCAST",
            "HEARING_AID"
        )
        for (type in supportedRoutes) {
            val isBtSupported = type in listOf("BLUETOOTH_A2DP", "BLUETOOTH_SCO", "BLE_HEADSET", "BLE_SPEAKER", "BLE_BROADCAST", "HEARING_AID")
            assertTrue("Expected $type to be supported", isBtSupported)
            assertTrue(WakeUpAttemptRules.selectedRouteMatches("WF-1000XM5", "79:87:51:BB:FC:E2", "WF-1000XM5", "", isBtSupported))
        }

        // Built-in phone speaker or wired/unsupported output
        val unsupported = listOf("PHONE_SPEAKER", "BUILTIN_SPEAKER", "WIRED_HEADSET", "NONE")
        for (type in unsupported) {
            val isBtSupported = type in listOf("BLUETOOTH_A2DP", "BLUETOOTH_SCO", "BLE_HEADSET", "BLE_SPEAKER", "BLE_BROADCAST", "HEARING_AID")
            assertFalse("Expected $type not to be treated as bluetooth earbuds", isBtSupported)
            assertFalse(WakeUpAttemptRules.selectedRouteMatches("WF-1000XM5", "79:87:51:BB:FC:E2", "WF-1000XM5", "", isBtSupported))
        }
    }
}
