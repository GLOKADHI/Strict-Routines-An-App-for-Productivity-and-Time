package com.strictrecords.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DisciplinePermissionManagerTest {

    @Test
    fun testAllCapabilitiesHaveDisciplineRationalesAndImpact() {
        for (cap in PermissionCapability.values()) {
            assertNotNull(cap.icon)
            assertTrue("Icon must not be empty for $cap", cap.icon.isNotBlank())
            assertNotNull(cap.displayName)
            assertTrue("Display name must not be empty for $cap", cap.displayName.isNotBlank())
            assertNotNull(cap.disciplineReason)
            assertTrue("Discipline reason must not be empty for $cap", cap.disciplineReason.isNotBlank())
            assertTrue("Discipline reason must explain why for $cap", cap.disciplineReason.length > 20)
            assertNotNull(cap.impactIfDenied)
            assertTrue("Impact if denied must not be empty for $cap", cap.impactIfDenied.isNotBlank())
        }
    }

    @Test
    fun testCapabilityEnumCompleteness() {
        val caps = PermissionCapability.values().map { it.name }
        assertEquals(PermissionCapability.values().size, caps.size)
        assertTrue(caps.contains("CAMERA"))
        assertTrue(caps.contains("BLUETOOTH"))
        assertTrue(caps.contains("USAGE_STATS"))
        assertTrue(caps.contains("OVERLAY"))
        assertTrue(caps.contains("ACCESSIBILITY"))
        assertTrue(caps.contains("NOTIFICATIONS"))
        assertTrue(caps.contains("EXACT_ALARM"))
        assertTrue(caps.contains("BATTERY_OPTIMIZATION"))
    }

    @Test
    fun testRequestCodesUnique() {
        val codes = setOf(
            DisciplinePermissionManager.REQ_PERM_CAMERA,
            DisciplinePermissionManager.REQ_PERM_BLUETOOTH,
            DisciplinePermissionManager.REQ_PERM_NOTIFICATIONS,
            DisciplinePermissionManager.REQ_ENABLE_BLUETOOTH
        )
        assertEquals(4, codes.size)
    }

    @Test
    fun testTotalCountMatchesEnumSize() {
        assertEquals(PermissionCapability.values().size, DisciplinePermissionManager.getTotalCount())
    }
}
