package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StrictBotNetworkPolicyTest {
    @Test fun credentialsAreLimitedToAPairedOrigin() {
        assertEquals("http://192.168.29.9:8765", StrictBotNetworkPolicy.normalizeEndpoint(" http://192.168.29.9:8765/ "))
        assertEquals("https://pc.example:8765", StrictBotNetworkPolicy.normalizeEndpoint("https://pc.example:8765"))
    }

    @Test fun physicalPhonesNeverUseLoopbackAsTheirPc() {
        for (origin in listOf("http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765", "http://0.0.0.0:8765"))
            assertNull(origin, StrictBotNetworkPolicy.normalizeEndpoint(origin))
    }

    @Test fun anOriginCannotContainEmbeddedCredentialsOrRoutingData() {
        for (origin in listOf("http://token@192.168.29.9:8765", "http://192.168.29.9:8765?redirect=https://elsewhere.example", "http://192.168.29.9:8765/#secret", "http://192.168.29.9:8765/api/device"))
            assertNull(origin, StrictBotNetworkPolicy.normalizeEndpoint(origin))
    }

    @Test fun malformedAndUnsupportedOriginsDoNotReceiveDeviceCredentials() {
        for (origin in listOf("192.168.29.9:8765", "file:///private", "ftp://192.168.29.9", "http://192.168.29.9:0", "http://192.168.29.9:65536", "http://", ""))
            assertNull(origin, StrictBotNetworkPolicy.normalizeEndpoint(origin))
    }

    @Test fun aRejectedDeviceSessionIsNotReportedAsAnOfflineSuccess() {
        assertTrue(StrictBotNetworkPolicy.failureMessage(403).contains("rejected"))
        assertTrue(StrictBotNetworkPolicy.failureMessage(403).contains("no change was confirmed"))
    }

    @Test fun missingBackendVersionHasAConcreteRecoveryInstruction() {
        assertTrue(StrictBotNetworkPolicy.failureMessage(404).contains("updated Windows app"))
    }
}
