package com.strictrecords.app

import org.junit.Assert.*
import org.junit.Test

class DiagnosticLayoutRulesTest {
    @Test fun phoneAndLargeFontGiveBothTextsFullUsableWidth() {
        for (screen in listOf(320f, 360f, 412f, 480f)) for (scale in listOf(1f, 1.3f, 2f)) {
            val available = screen - 64f
            assertTrue(DiagnosticLayoutRules.stacked(available, scale))
            assertEquals(available, DiagnosticLayoutRules.labelWidth(available, scale), 0f)
            assertEquals(available, DiagnosticLayoutRules.statusWidth(available, scale), 0f)
        }
    }
    @Test fun tabletColumnsHaveBoundedWidthsAndScaleToStackedRows() {
        assertFalse(DiagnosticLayoutRules.stacked(736f, 1f))
        assertTrue(DiagnosticLayoutRules.labelWidth(736f, 1f) > 400f)
        assertTrue(DiagnosticLayoutRules.statusWidth(736f, 1f) > 280f)
        assertTrue(DiagnosticLayoutRules.stacked(736f, 2f))
    }
}
