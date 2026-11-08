package com.strictrecords.app

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.TimeZone

class MovieBookingRulesTest {

    private val testTz = TimeZone.getTimeZone("Asia/Kolkata")

    // PART A: Category choices
    @Test
    fun testHourlyCategoryChoicesStrictlyFour() {
        val expected = listOf("COLLEGE", "STUDY", "EDITING", "VIDEO SHOOT")
        assertEquals(expected, ProofSubmissionRules.HOURLY_ACTIVE_CATEGORIES)
        assertFalse(ProofSubmissionRules.HOURLY_ACTIVE_CATEGORIES.contains("CODING"))
        assertFalse(ProofSubmissionRules.HOURLY_ACTIVE_CATEGORIES.contains("PROJECT"))
    }

    @Test
    fun testHourlyHistoricalCategoriesPreserved() {
        assertTrue(ProofSubmissionRules.HISTORICAL_HOURLY_CATEGORIES.contains("CODING"))
        assertTrue(ProofSubmissionRules.HISTORICAL_HOURLY_CATEGORIES.contains("PROJECT"))

        // Active categories are valid
        for (cat in ProofSubmissionRules.HOURLY_ACTIVE_CATEGORIES) {
            assertTrue(cat in ProofSubmissionRules.ALL_KNOWN_HOURLY_CATEGORIES)
        }

        // Historical categories also present in all known categories
        assertTrue("CODING" in ProofSubmissionRules.ALL_KNOWN_HOURLY_CATEGORIES)
        assertTrue("PROJECT" in ProofSubmissionRules.ALL_KNOWN_HOURLY_CATEGORIES)

        // Unrelated category not present
        assertFalse("GAMING" in ProofSubmissionRules.ALL_KNOWN_HOURLY_CATEGORIES)
    }

    // PART B: OCR extraction without QR code
    @Test
    fun testMovieTicketOcrParsingWithoutQrCode() {
        val ocrSample = """
            BOOKMYSHOW TICKET
            PVR Cinemas: Forum Mall, Koramangala
            Movie: Dune: Part Two (U/A)
            Date: Sun, 18 Oct 2026
            Time: 05:00 PM - 08:00 PM
            Screen: AUDI 04
            Seats: F12, F13
            Booking ID: WXYZ78910
            Scan QR code at entry: [QR_CODE_DATA_BINARY_OMITTED]
        """.trimIndent()

        val parsed = MovieBookingRules.parseMovieTicketOcr(ocrSample, "2026-10-18", testTz)

        // Verifies QR code was NOT required or used for validation
        assertTrue("QR code should be ignored for validation", parsed.qrCodeIgnoredForValidation)
        assertTrue(parsed.durationEstablishedReliably)
        assertEquals("Dune: Part Two", parsed.movieTitle)
        assertEquals("PVR Cinemas: Forum Mall, Koramangala", parsed.cinemaName)
        assertEquals("2026-10-18", parsed.showDate)
        assertEquals("17:00", parsed.startTime)
        assertEquals("20:00", parsed.endTime)
        assertEquals(180, parsed.durationMinutes)
        assertTrue("Screen should identify AUDI", parsed.screen.startsWith("AUDI"))
        assertEquals("F12, F13", parsed.seat)
        assertEquals("WXYZ78910", parsed.bookingRef)
    }

    // PART B: Attendance lock 90 min calculation & Required Example
    @Test
    fun testAttendanceLockCalculationAndRequiredFiveSlots() {
        // Required example: Show from 5:00 PM (17:00) to 8:00 PM (20:00) on 2026-10-18
        val lock = MovieBookingRules.calculateAttendanceLock("2026-10-18", "17:00", "20:00", testTz)

        // Show start = 17:00 (5:00 PM), Show end = 20:00 (8:00 PM)
        // Attendance lock start = 5:00 PM - 1h 30m = 3:30 PM (15:30)
        // Attendance lock end = 8:00 PM + 1h 30m = 9:30 PM (21:30)
        assertEquals("15:30", lock.lockStartTime)
        assertEquals("21:30", lock.lockEndTime)
        assertEquals("2026-10-18", lock.showDate)

        // StrictRecords hourly slots for the day
        val hourlySlots = HourlyWindowRules.slots("2026-10-18", "07:00", "Asia/Kolkata")

        val excusedSlots = mutableListOf<String>()
        val unexcusedSlots = mutableListOf<String>()

        for (slot in hourlySlots) {
            val covered = MovieBookingRules.isHourlySlotCoveredByMovie(slot.targetMs, lock.lockStartMs, lock.lockEndMs)
            if (covered) {
                excusedSlots.add(slot.hourSlot)
            } else {
                unexcusedSlots.add(slot.hourSlot)
            }
        }

        // Exactly the 5 required hourly slots must be excused:
        // 16:00-17:00 (4:00-5:00 PM)
        // 17:00-18:00 (5:00-6:00 PM)
        // 18:00-19:00 (6:00-7:00 PM)
        // 19:00-20:00 (7:00-8:00 PM)
        // 20:00-21:00 (8:00-9:00 PM)
        val expectedExcused = listOf(
            "16:00-17:00",
            "17:00-18:00",
            "18:00-19:00",
            "19:00-20:00",
            "20:00-21:00"
        )
        assertEquals(5, excusedSlots.size)
        assertEquals(expectedExcused, excusedSlots)

        // 15:00-16:00 (3:00-4:00 PM) must NOT be excused
        assertTrue("3:00-4:00 PM slot must not be excused", unexcusedSlots.contains("15:00-16:00"))

        // 21:00-22:00 (9:00-10:00 PM) must NOT be excused
        assertTrue("9:00-10:00 PM slot must not be excused", unexcusedSlots.contains("21:00-22:00"))
    }

    // PART B: Midnight-crossing show handling
    @Test
    fun testMidnightCrossingShowLockCalculation() {
        // Show from 11:30 PM (23:30) to 2:00 AM (02:00 next day)
        val lock = MovieBookingRules.calculateAttendanceLock("2026-10-18", "23:30", "02:00", testTz)

        // Lock start = 23:30 - 1h 30m = 22:00 (10:00 PM)
        // Lock end = 02:00 next day + 1h 30m = 03:30 AM next day
        assertEquals("22:00", lock.lockStartTime)
        assertEquals("03:30", lock.lockEndTime)
        assertTrue("Lock end must be after lock start", lock.lockEndMs > lock.lockStartMs)
        assertEquals(5.5 * 3600_000L, (lock.lockEndMs - lock.lockStartMs).toDouble(), 0.0)
    }

    // PART B: Ambiguous duration leads to Needs Review
    @Test
    fun testAmbiguousDurationRetainsNeedsReview() {
        val ocrSampleNoDuration = """
            Cinema: Cinepolis
            Film: An Unknown Indie Short
            Date: 2026-10-18
            Starts: 14:00
            Screen 1 Seat A1
        """.trimIndent()

        val parsed = MovieBookingRules.parseMovieTicketOcr(ocrSampleNoDuration, "2026-10-18", testTz)

        assertFalse("Duration must not be reliably established when missing and unknown", parsed.durationEstablishedReliably)
        assertNull(parsed.endTime)
        assertTrue(parsed.validationMessage.contains("could not be reliably determined", ignoreCase = true))
    }

    // PART B: Known film metadata fallback
    @Test
    fun testKnownFilmMetadataRuntimeResolution() {
        val ocrSampleKnownFilm = """
            INOX Megaplex
            Movie: Oppenheimer
            Show: 10:00 AM
            Date: 2026-10-18
            Seat: G5
        """.trimIndent()

        val parsed = MovieBookingRules.parseMovieTicketOcr(ocrSampleKnownFilm, "2026-10-18", testTz)

        assertTrue(parsed.durationEstablishedReliably)
        assertEquals("Oppenheimer", parsed.movieTitle)
        assertEquals("10:00", parsed.startTime)
        // Oppenheimer runtime is 180 min -> end time is 13:00
        assertEquals(180, parsed.durationMinutes)
        assertEquals("13:00", parsed.endTime)
    }

    // PART B: Conflict detection
    @Test
    fun testConflictDetectionWithTravelGymBathAndOtherBookings() {
        val lock = MovieBookingRules.calculateAttendanceLock("2026-10-18", "17:00", "20:00", testTz)

        // 1. Conflict with travel: 18:00 to 19:30
        val travelStart = lock.movieStartMs + 3600_000L
        val travelEnd = travelStart + 5400_000L
        val conflictTravel = MovieBookingRules.checkScheduleConflicts(
            lock.lockStartMs, lock.lockEndMs,
            existingTravelIntervals = listOf(Pair(travelStart, travelEnd)),
            existingMovieIntervals = emptyList()
        )
        assertTrue(conflictTravel.hasConflict)
        assertTrue(conflictTravel.conflictReason.contains("travel", ignoreCase = true))

        // 2. Conflict with Gym: 16:00 to 17:30
        val gymStart = lock.lockStartMs + 1800_000L
        val gymEnd = gymStart + 3600_000L
        val conflictGym = MovieBookingRules.checkScheduleConflicts(
            lock.lockStartMs, lock.lockEndMs,
            existingTravelIntervals = emptyList(),
            existingMovieIntervals = emptyList(),
            gymInterval = Pair(gymStart, gymEnd)
        )
        assertTrue(conflictGym.hasConflict)
        assertTrue(conflictGym.conflictReason.contains("Gym", ignoreCase = true))

        // 3. Conflict with existing Movie booking
        val conflictMovie = MovieBookingRules.checkScheduleConflicts(
            lock.lockStartMs, lock.lockEndMs,
            existingTravelIntervals = emptyList(),
            existingMovieIntervals = listOf(Pair(lock.lockStartMs + 1000L, lock.lockEndMs - 1000L))
        )
        assertTrue(conflictMovie.hasConflict)
        assertTrue(conflictMovie.conflictReason.contains("movie reservation", ignoreCase = true))

        // 4. Non-conflicting interval (earlier in morning)
        val morningStart = lock.lockStartMs - 20000_000L
        val morningEnd = morningStart + 3600_000L
        val noConflict = MovieBookingRules.checkScheduleConflicts(
            lock.lockStartMs, lock.lockEndMs,
            existingTravelIntervals = listOf(Pair(morningStart, morningEnd)),
            existingMovieIntervals = emptyList()
        )
        assertFalse(noConflict.hasConflict)
        assertTrue(noConflict.conflictReason.isEmpty())
    }

    // PART B: State progression
    @Test
    fun testAuthoritativeStateResolution() {
        val lockStartMs = 100_000_000L
        val lockEndMs = 120_000_000L

        // Before lock window
        val stateBefore = MovieBookingRules.resolveAuthoritativeState(
            nowMs = 90_000_000L,
            lockStartMs = lockStartMs,
            lockEndMs = lockEndMs,
            currentStatus = MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED
        )
        assertEquals(MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED, stateBefore)

        // During lock window
        val stateDuring = MovieBookingRules.resolveAuthoritativeState(
            nowMs = 110_000_000L,
            lockStartMs = lockStartMs,
            lockEndMs = lockEndMs,
            currentStatus = MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED
        )
        assertEquals(MovieBookingRules.STATE_ATTENDANCE_HOLD_ACTIVE, stateDuring)

        // After lock window
        val stateAfter = MovieBookingRules.resolveAuthoritativeState(
            nowMs = 130_000_000L,
            lockStartMs = lockStartMs,
            lockEndMs = lockEndMs,
            currentStatus = MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED
        )
        assertEquals(MovieBookingRules.STATE_RESERVATION_COMPLETED, stateAfter)

        // Needs Review remains Needs Review regardless of clock
        val stateReview = MovieBookingRules.resolveAuthoritativeState(
            nowMs = 110_000_000L,
            lockStartMs = 0L,
            lockEndMs = 0L,
            currentStatus = MovieBookingRules.STATE_NEEDS_REVIEW
        )
        assertEquals(MovieBookingRules.STATE_NEEDS_REVIEW, stateReview)
    }
}
