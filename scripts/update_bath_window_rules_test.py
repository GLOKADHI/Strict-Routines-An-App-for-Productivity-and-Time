import sys

path = 'android/app/src/test/java/com/strictrecords/app/BathWindowRulesTest.kt'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target1 = '''        assertEquals("07:00", bath1.windowStartHhmm)
        assertEquals("10:00", bath1.windowEndHhmm)
        assertEquals("07:30", bath1.plannedHhmm)
        assertEquals("07:00 AM–10:00 AM", bath1.windowDisplay)
        assertEquals("Bath Protocol (#1)", bath1.title)
        assertEquals("ACT-20261015-0730-BATH-1", bath1.actId)

        val expectedBath1StartMs = timeMs(date, "07:00")
        val expectedBath1EndMs = timeMs(date, "10:00")
        assertEquals(expectedBath1StartMs, bath1.windowStartMs)
        assertEquals(expectedBath1EndMs, bath1.windowEndMs)
        assertEquals(3 * 3600 * 1000L, bath1.windowEndMs - bath1.windowStartMs)'''

replacement1 = '''        assertEquals("07:30", bath1.windowStartHhmm)
        assertEquals("08:30", bath1.windowEndHhmm)
        assertEquals("07:30", bath1.plannedHhmm)
        assertEquals("Bath Protocol (#1)", bath1.title)
        assertEquals("ACT-20261015-0730-BATH-1", bath1.actId)

        val expectedBath1StartMs = timeMs(date, "07:30")
        val expectedBath1EndMs = timeMs(date, "08:30")
        assertEquals(expectedBath1StartMs, bath1.windowStartMs)
        assertEquals(expectedBath1EndMs, bath1.windowEndMs)
        assertEquals(timeMs(date, "07:30"), bath1.reservedStartMs)
        assertEquals(timeMs(date, "08:00"), bath1.reservedEndMs)
        assertEquals(timeMs(date, "08:00"), bath1.proofWindowStartMs)
        assertEquals(timeMs(date, "08:30"), bath1.proofWindowEndMs)'''

assert target1 in content, "target1 not found in BathWindowRulesTest.kt"
content = content.replace(target1, replacement1)

target2 = '''        // Bath Protocol #1: 7:00 AM–10:00 AM
        val bath1 = BathWindowRules.calculateBathWindow(1, true, date, "07:30", timezone = "Asia/Kolkata")
        val b1Start = bath1.windowStartMs
        val b1End = bath1.windowEndMs

        // Start-inclusive and end-exclusive check:
        // Before 7:00 AM: ineligible
        assertFalse(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "06:59"), b1Start, b1End))
        // Exactly at 7:00:00 AM: eligible (start-inclusive)
        assertTrue(BathWindowRules.isSubmissionWithinWindow(b1Start, b1Start, b1End))
        // At 8:30 AM: eligible
        assertTrue(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "08:30"), b1Start, b1End))
        // At 9:59:59 AM: eligible
        assertTrue(BathWindowRules.isSubmissionWithinWindow(b1End - 1000L, b1Start, b1End))
        // Exactly at 10:00:00 AM: ineligible (end-exclusive)
        assertFalse(BathWindowRules.isSubmissionWithinWindow(b1End, b1Start, b1End))
        // After 10:00 AM: ineligible
        assertFalse(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "10:05"), b1Start, b1End))'''

replacement2 = '''        // Bath Protocol #1: 7:30 AM–8:30 AM (proof window 8:00–8:30 AM)
        val bath1 = BathWindowRules.calculateBathWindow(1, true, date, "07:30", timezone = "Asia/Kolkata")
        val b1Start = bath1.windowStartMs
        val b1End = bath1.windowEndMs

        // Start-inclusive and end-exclusive check:
        // Before 7:30 AM: ineligible
        assertFalse(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "07:29"), b1Start, b1End))
        // Exactly at 7:30:00 AM: eligible (start-inclusive)
        assertTrue(BathWindowRules.isSubmissionWithinWindow(b1Start, b1Start, b1End))
        // At 8:00 AM: eligible
        assertTrue(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "08:00"), b1Start, b1End))
        // At 8:29:59 AM: eligible
        assertTrue(BathWindowRules.isSubmissionWithinWindow(b1End - 1000L, b1Start, b1End))
        // Exactly at 8:30:00 AM: ineligible (end-exclusive)
        assertFalse(BathWindowRules.isSubmissionWithinWindow(b1End, b1Start, b1End))
        // After 8:30 AM: ineligible
        assertFalse(BathWindowRules.isSubmissionWithinWindow(timeMs(date, "08:35"), b1Start, b1End))'''

assert target2 in content, "target2 not found in BathWindowRulesTest.kt"
content = content.replace(target2, replacement2)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated BathWindowRulesTest.kt successfully")
