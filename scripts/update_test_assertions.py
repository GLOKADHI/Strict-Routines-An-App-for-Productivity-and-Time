# 1. Update BathScheduleMorningPopupTest.kt
p1 = 'android/app/src/test/java/com/strictrecords/app/BathScheduleMorningPopupTest.kt'
with open(p1, 'r', encoding='utf-8') as f:
    c = f.read()

target1 = '''        // College Bath #1 at 07:30
        val c1 = BathWindowRules.calculateBathWindow(1, true, testDate, "07:30", timezone = zoneStr)
        assertEquals("7:30 AM", c1.plannedDisplay)
        assertEquals("7:30\\u20138:30 AM", c1.activityIntervalDisplay)
        assertEquals("9:00\\u20139:20 AM", c1.proofWindowDisplay)
        assertEquals("9:20 AM", c1.proofDeadlineDisplay)'''

rep1 = '''        // College Bath #1 at 07:30
        val c1 = BathWindowRules.calculateBathWindow(1, true, testDate, "07:30", timezone = zoneStr)
        assertEquals("7:30 AM", c1.plannedDisplay)
        assertEquals("7:30\\u20138:00 AM", c1.activityIntervalDisplay)
        assertEquals("8:00\\u20138:30 AM", c1.proofWindowDisplay)
        assertEquals("8:30 AM", c1.proofDeadlineDisplay)'''

if target1 in c:
    c = c.replace(target1, rep1)
    with open(p1, 'w', encoding='utf-8') as f:
        f.write(c)
    print("BathScheduleMorningPopupTest updated")
else:
    print("Target 1 not found in BathScheduleMorningPopupTest")

# 2. Update BathWindowRulesTest.kt
p2 = 'android/app/src/test/java/com/strictrecords/app/BathWindowRulesTest.kt'
with open(p2, 'r', encoding='utf-8') as f:
    c2 = f.read()

target2 = '''        assertEquals("07:00", bath1.windowStartHhmm)
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

rep2 = '''        assertEquals("07:30", bath1.windowStartHhmm)
        assertEquals("08:30", bath1.windowEndHhmm)
        assertEquals("07:30", bath1.plannedHhmm)
        assertEquals("07:30 AM–08:30 AM", bath1.windowDisplay)
        assertEquals("Bath Protocol (#1)", bath1.title)
        assertEquals("ACT-20261015-0730-BATH-1", bath1.actId)

        val expectedBath1StartMs = timeMs(date, "07:30")
        val expectedBath1EndMs = timeMs(date, "08:30")
        assertEquals(expectedBath1StartMs, bath1.windowStartMs)
        assertEquals(expectedBath1EndMs, bath1.windowEndMs)
        assertEquals(1 * 3600 * 1000L, bath1.windowEndMs - bath1.windowStartMs)'''

if target2 in c2:
    c2 = c2.replace(target2, rep2)
    print("BathWindowRulesTest test1 updated")
else:
    print("Target 2 not found in BathWindowRulesTest")

target3 = '''        // Before 7:00 AM: ineligible
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

rep3 = '''        // Before 7:30 AM: ineligible
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

if target3 in c2:
    c2 = c2.replace(target3, rep3)
    print("BathWindowRulesTest test7 updated")
else:
    print("Target 3 not found in BathWindowRulesTest")

with open(p2, 'w', encoding='utf-8') as f:
    f.write(c2)

# 3. Update TravelBookingRulesTest.kt
p3 = 'android/app/src/test/java/com/strictrecords/app/TravelBookingRulesTest.kt'
with open(p3, 'r', encoding='utf-8') as f:
    c3 = f.read()

c3 = c3.replace('assertEquals("07:00 AM–10:00 AM", bathWin.windowDisplay)', 'assertEquals("07:30 AM–08:30 AM", bathWin.windowDisplay)')
c3 = c3.replace('assertEquals("07:00 AM–10:00 AM", bathWin1.windowDisplay)', 'assertEquals("07:30 AM–08:30 AM", bathWin1.windowDisplay)')

with open(p3, 'w', encoding='utf-8') as f:
    f.write(c3)
print("TravelBookingRulesTest updated")
