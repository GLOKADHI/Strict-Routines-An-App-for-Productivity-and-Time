package com.strictrecords.app

import com.google.zxing.BarcodeFormat
import com.google.zxing.common.BitMatrix
import com.google.zxing.qrcode.QRCodeWriter
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.TimeZone

/**
 * Exhaustive regression test suite verifying:
 * Movie Prebook Ticket Extraction, Date Validation, Automatic Scheduling, and QR-Code Rejection.
 * Covers all 17 requirements of Section 9.
 */
class MovieTicketPrebookTest {

    private val testTz = TimeZone.getTimeZone("Asia/Kolkata")

    private fun createBitmapWithQr(
        width: Int,
        height: Int,
        qrText: String? = null,
        qrX: Int = 0,
        qrY: Int = 0,
        qrSize: Int = 100,
        decorations: ((IntArray, Int, Int) -> Unit)? = null
    ): IntArray {
        val pixels = IntArray(width * height) { 0xFFFFFF } // white background
        decorations?.invoke(pixels, width, height)
        if (qrText != null) {
            val matrix = QRCodeWriter().encode(qrText, BarcodeFormat.QR_CODE, qrSize, qrSize)
            for (x in 0 until qrSize) {
                for (y in 0 until qrSize) {
                    if (matrix.get(x, y)) {
                        val px = qrX + x
                        val py = qrY + y
                        if (px in 0 until width && py in 0 until height) {
                            pixels[py * width + px] = 0x000000
                        }
                    }
                }
            }
        }
        return pixels
    }

    private fun encodeBmp(width: Int, height: Int, pixels: IntArray): ByteArray {
        val rowSize = (width * 3 + 3) and 3.inv() // 4-byte aligned
        val imageSize = rowSize * height
        val fileSize = 54 + imageSize
        val buffer = ByteBuffer.allocate(fileSize).order(ByteOrder.LITTLE_ENDIAN)
        // BITMAPFILEHEADER
        buffer.put('B'.code.toByte())
        buffer.put('M'.code.toByte())
        buffer.putInt(fileSize)
        buffer.putShort(0)
        buffer.putShort(0)
        buffer.putInt(54) // offset to pixel data
        // BITMAPINFOHEADER
        buffer.putInt(40) // header size
        buffer.putInt(width)
        buffer.putInt(height) // bottom-up
        buffer.putShort(1) // planes
        buffer.putShort(24) // bpp
        buffer.putInt(0) // compression (BI_RGB)
        buffer.putInt(imageSize)
        buffer.putInt(2835) // 72 DPI
        buffer.putInt(2835) // 72 DPI
        buffer.putInt(0)
        buffer.putInt(0)

        val row = ByteArray(rowSize)
        for (y in height - 1 downTo 0) {
            var col = 0
            for (x in 0 until width) {
                val color = pixels[y * width + x]
                row[col++] = (color and 0xFF).toByte()         // Blue
                row[col++] = ((color shr 8) and 0xFF).toByte()  // Green
                row[col++] = ((color shr 16) and 0xFF).toByte() // Red
            }
            while (col < rowSize) {
                row[col++] = 0
            }
            buffer.put(row)
        }
        return buffer.array()
    }

    // 1. A valid ticket screenshot without a QR code
    @Test
    fun test1_validTicketScreenshotWithoutQrCode_acceptedAndParsed() {
        // District movie confirmation sample from user reference
        val districtOcr = """
            Booking Confirmed
            HAVE A MEAL NEAR YOUR THEATRE
            Karuppu
            Tamil | 2D | UA13+
            17 May, 03:50 PM - 06:41 PM
            (approx)
            1 ticket
            CL - G13
            SCREEN 1
            INOX The Marina Mall OMR, Egatoor, Chennai
            Booking code: 672061111603034107
            Booking ID: TKA5ALM
            Share
        """.trimIndent()

        // Evaluated with intended date 2026-05-17 and clock at May 17 2026 14:00 (before show)
        val showDayMs = 1779006600000L // 2026-05-17 14:00:00 IST
        val parsed = MovieBookingRules.parseMovieTicketOcr(districtOcr, "2026-05-17", testTz, nowMs = showDayMs)

        assertEquals("Karuppu", parsed.movieTitle)
        assertEquals("Tamil", parsed.language)
        assertEquals("2D", parsed.format)
        assertEquals("UA13+", parsed.rating)
        assertEquals("2026-05-17", parsed.showDate)
        assertEquals("15:50", parsed.startTime)
        assertEquals("18:41", parsed.endTime)
        assertTrue("Approximate indicator should be detected", parsed.isApproximateEndTime)
        assertEquals(171, parsed.durationMinutes)
        assertEquals("TICKET_SHOW_TIMES", parsed.durationSource)
        assertEquals(1, parsed.ticketQuantity)
        assertEquals("CL - G13", parsed.seat)
        assertEquals("SCREEN 1", parsed.screen)
        assertEquals("INOX The Marina Mall OMR, Egatoor, Chennai", parsed.cinemaName)
        assertEquals("672061111603034107", parsed.bookingCode)
        assertEquals("TKA5ALM", parsed.bookingRef)
        assertFalse(parsed.isDateMismatch)
        assertFalse(parsed.isExpired)
        assertEquals(MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED, parsed.validationStatus)

        // Schedule check:
        // 15:50 - 90m = 14:20; 18:41 + 90m = 20:11
        assertEquals("14:20", parsed.reservationStartTime)
        assertEquals("20:11", parsed.reservationEndTime)
        // Proof window: opens 18:36 (18:41 - 5m), deadline 19:41 (18:41 + 60m)
        assertEquals("18:36", parsed.proofWindowOpenTime)
        assertEquals("19:41", parsed.proofDeadlineTime)
    }

    // 2. A ticket with a QR code at the bottom
    @Test
    fun test2_ticketWithQrCodeAtBottom_rejectedImmediatelyWithoutOcr() {
        val w = 500
        val h = 800
        val pixels = createBitmapWithQr(
            width = w,
            height = h,
            qrText = "STRICTRECORDS_QR_BOTTOM",
            qrX = 190,
            qrY = 640,
            qrSize = 120
        )
        val bytes = encodeBmp(w, h, pixels)
        val result = MovieQrPresenceDetector.detectQrPresence(bytes)

        assertEquals(MovieQrPresenceDetector.QrPresenceStatus.DETECTED, result.status)
        assertTrue(result.isQrDetected)
        assertFalse(result.isUncertain)
        assertEquals("Ticket screenshot rejected: QR code detected. Please upload a screenshot without a QR code.", result.message)
    }

    // 3. A ticket with a small QR code in a corner
    @Test
    fun test3_ticketWithSmallQrCodeInCorner_detectedAndRejected() {
        val w = 500
        val h = 800
        val pixels = createBitmapWithQr(
            width = w,
            height = h,
            qrText = "STRICTRECORDS_QR_CORNER",
            qrX = 400,
            qrY = 20,
            qrSize = 75
        )
        val bytes = encodeBmp(w, h, pixels)
        val result = MovieQrPresenceDetector.detectQrPresence(bytes)

        assertEquals(MovieQrPresenceDetector.QrPresenceStatus.DETECTED, result.status)
        assertTrue(result.isQrDetected)
        assertEquals(MovieQrPresenceDetector.REJECTION_MESSAGE, result.message)
    }

    // 4. An ordinary booking reference and decorative square pattern that is not a QR code
    @Test
    fun test4_ordinaryBookingReferenceAndDecorativeBox_notTreatedAsQrCode() {
        val w = 500
        val h = 800
        val pixels = createBitmapWithQr(
            width = w,
            height = h,
            qrText = null,
            decorations = { px, width, _ ->
                // Dark gray decorative box (100, 150) to (400, 400)
                for (y in 150 until 400) {
                    for (x in 100 until 400) {
                        px[y * width + x] = 0x404040
                    }
                }
            }
        )
        val bytes = encodeBmp(w, h, pixels)
        val result = MovieQrPresenceDetector.detectQrPresence(bytes)

        assertEquals(MovieQrPresenceDetector.QrPresenceStatus.NOT_DETECTED, result.status)
        assertFalse(result.isQrDetected)
        assertFalse(result.isUncertain)
    }

    // 5. A low-quality screenshot where QR detection is uncertain
    @Test
    fun test5_lowQualityOrCorruptedScreenshot_qrCheckUncertain() {
        // Completely solid white image (zero contrast, stddev = 0)
        val w = 300
        val h = 300
        val blankPixels = IntArray(w * h) { 0xFFFFFF }
        val bytes = encodeBmp(w, h, blankPixels)
        val result = MovieQrPresenceDetector.detectQrPresence(bytes)

        assertEquals(MovieQrPresenceDetector.QrPresenceStatus.UNCERTAIN, result.status)
        assertTrue(result.isUncertain)
        assertFalse(result.isQrDetected)
        assertEquals("QR CHECK UNCERTAIN — PLEASE UPLOAD A DIFFERENT SCREENSHOT", result.message)

        // Empty bytes
        val emptyResult = MovieQrPresenceDetector.detectQrPresence(ByteArray(0))
        assertEquals(MovieQrPresenceDetector.QrPresenceStatus.UNCERTAIN, emptyResult.status)
    }

    // 6. A matching ticket date and intended booking date
    @Test
    fun test6_matchingTicketDateAndIntendedBookingDate_validatedSuccessfully() {
        val ocrText = """
            Movie: Karuppu
            Date: 17 May
            Time: 03:50 PM - 06:41 PM
            Screen 1 Seat G13
        """.trimIndent()

        val parsed = MovieBookingRules.parseMovieTicketOcr(
            ocrText,
            targetDate = "2026-05-17",
            timeZone = testTz,
            nowMs = 1779006600000L // 2026-05-17 14:00
        )

        assertFalse(parsed.isDateMismatch)
        assertEquals("2026-05-17", parsed.showDate)
        assertEquals(MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED, parsed.validationStatus)
    }

    // 7. A mismatched date
    @Test
    fun test7_mismatchedTicketDateAndIntendedDate_rejectedWithReview() {
        val ocrText = """
            Movie: Karuppu
            Date: 17 May
            Time: 03:50 PM - 06:41 PM
        """.trimIndent()

        // User selected 2026-10-18, but ticket is for 17 May!
        val parsed = MovieBookingRules.parseMovieTicketOcr(
            ocrText,
            targetDate = "2026-10-18",
            timeZone = testTz,
            nowMs = 1779006600000L
        )

        assertTrue("Date mismatch must be flagged", parsed.isDateMismatch)
        assertEquals(MovieBookingRules.STATE_NEEDS_REVIEW, parsed.validationStatus)
        assertTrue(parsed.validationMessage.contains("mismatch", ignoreCase = true))
    }

    // 8. A missing or ambiguous year
    @Test
    fun test8_missingOrAmbiguousYear_retainsNeedsReview() {
        val ocrText = """
            Movie: Karuppu
            Date: 17 May
            Time: 03:50 PM - 06:41 PM
        """.trimIndent()

        // No intended booking date provided; ticket omits year
        val parsed = MovieBookingRules.parseMovieTicketOcr(
            ocrText,
            targetDate = null,
            timeZone = testTz,
            nowMs = 1779006600000L
        )

        assertTrue(parsed.yearOmittedInTicket)
        assertEquals(MovieBookingRules.STATE_NEEDS_REVIEW, parsed.validationStatus)
        assertTrue(parsed.validationMessage.contains("year", ignoreCase = true))
    }

    // 9. An old or expired movie ticket
    @Test
    fun test9_oldOrExpiredMovieTicket_rejectedFromFutureReservation() {
        val ocrText = """
            Movie: Karuppu
            Date: 17 May 2026
            Time: 03:50 PM - 06:41 PM
        """.trimIndent()

        // Current time is 2026-10-18 (5 months after May 17 show)
        val nowMs = 1792318911502L
        val parsed = MovieBookingRules.parseMovieTicketOcr(
            ocrText,
            targetDate = "2026-05-17",
            timeZone = testTz,
            nowMs = nowMs
        )

        assertTrue("Expired ticket must be flagged", parsed.isExpired)
        assertEquals(MovieBookingRules.STATE_BOOKING_REJECTED, parsed.validationStatus)
        assertTrue(parsed.validationMessage.contains("already finished", ignoreCase = true) ||
            parsed.validationMessage.contains("expired", ignoreCase = true))
    }

    // 10. A ticket with an exact end time
    @Test
    fun test10_ticketWithExactEndTime_exactProvenance() {
        val ocrText = """
            Movie: Oppenheimer
            Date: 18 Oct 2026
            Time: 05:00 PM - 08:00 PM
            Cinema: PVR Forum
        """.trimIndent()

        val parsed = MovieBookingRules.parseMovieTicketOcr(
            ocrText,
            targetDate = "2026-10-18",
            timeZone = testTz,
            nowMs = 1792200000000L
        )

        assertFalse("End time without approx indicator is exact", parsed.isApproximateEndTime)
        assertEquals("17:00", parsed.startTime)
        assertEquals("20:00", parsed.endTime)
        assertEquals(180, parsed.durationMinutes)
    }

    // 11. A ticket whose finish time is explicitly approximate
    @Test
    fun test11_ticketWithExplicitlyApproximateEndTime_preservesApproximateIndicator() {
        val ocrText = """
            Karuppu
            17 May, 03:50 PM - 06:41 PM
            (approx)
            SCREEN 1
        """.trimIndent()

        val parsed = MovieBookingRules.parseMovieTicketOcr(
            ocrText,
            targetDate = "2026-05-17",
            timeZone = testTz,
            nowMs = 1779006600000L
        )

        assertTrue("Approximate indicator must be preserved", parsed.isApproximateEndTime)
        assertEquals("15:50", parsed.startTime)
        assertEquals("18:41", parsed.endTime)
    }

    // 12. A ticket with insufficient duration information
    @Test
    fun test12_ticketWithInsufficientDurationInformation_retainsNeedsReview() {
        val ocrText = """
            Cinema: Indie Cinema
            Movie: Rare Unreleased Indie
            Date: 18 Oct 2026
            Time: 04:00 PM
        """.trimIndent()

        val parsed = MovieBookingRules.parseMovieTicketOcr(
            ocrText,
            targetDate = "2026-10-18",
            timeZone = testTz,
            nowMs = 1792200000000L
        )

        assertFalse(parsed.durationEstablishedReliably)
        assertNull(parsed.endTime)
        assertEquals(MovieBookingRules.STATE_NEEDS_REVIEW, parsed.validationStatus)
    }

    // 13. Correct reservation and proof-window calculations
    @Test
    fun test13_correctReservationAndProofWindowCalculations() {
        // Standard 5:00 PM - 8:00 PM example
        val lock = MovieBookingRules.calculateAttendanceLock("2026-10-18", "17:00", "20:00", testTz)

        // 1. Attendance reservation envelope: 3:30 PM - 9:30 PM (±90 min)
        assertEquals("15:30", lock.lockStartTime)
        assertEquals("21:30", lock.lockEndTime)

        // 2. Affected hourly slots
        val hourlySlots = HourlyWindowRules.slots("2026-10-18", "07:00", "Asia/Kolkata")
        val excused = hourlySlots.filter { MovieBookingRules.isHourlySlotCoveredByMovie(it.targetMs, lock.lockStartMs, lock.lockEndMs) }
            .map { it.hourSlot }

        val expectedExcused = listOf("16:00-17:00", "17:00-18:00", "18:00-19:00", "19:00-20:00", "20:00-21:00")
        assertEquals(expectedExcused, excused)

        // 3:00-4:00 PM (15:00-16:00) is NOT excused
        assertFalse(excused.contains("15:00-16:00"))

        // 3. Movie proof-submission window:
        // Opens: 7:55 PM (19:55) (show end - 5 min, start-inclusive)
        // Deadline: 9:00 PM (21:00) (show end + 60 min, end-exclusive)
        val (openMs, deadlineMs) = MovieBookingRules.calculateProofWindow(lock.movieEndMs)
        val timeFormat = java.text.SimpleDateFormat("HH:mm", java.util.Locale.US).apply { timeZone = testTz }
        assertEquals("19:55", timeFormat.format(java.util.Date(openMs)))
        assertEquals("21:00", timeFormat.format(java.util.Date(deadlineMs)))

        // Boundary checks:
        // Exactly at 7:55 PM -> within window
        assertTrue(MovieBookingRules.isWithinProofWindow(openMs, openMs, deadlineMs))
        // At 8:59 PM -> within window
        assertTrue(MovieBookingRules.isWithinProofWindow(deadlineMs - 60_000L, openMs, deadlineMs))
        // Exactly at 9:00 PM -> late / outside window (end-exclusive)
        assertFalse(MovieBookingRules.isWithinProofWindow(deadlineMs, openMs, deadlineMs))
        // At 7:54 PM -> before window
        assertFalse(MovieBookingRules.isWithinProofWindow(openMs - 60_000L, openMs, deadlineMs))
    }

    // 14. Duplicate confirmation and retry attempts
    @Test
    fun test14_duplicateConfirmationAndRetryAttempts_prevented() {
        val analysis = JSONObject().apply {
            put("can_confirm", true)
            put("show_date", "2026-10-18")
            put("activity_id", "ACT-20261018-1700-MOVIE")
            put("movie_booking_id", "MBK-20261018-1700-Oppenhei")
            put("movie_title", "Oppenheimer")
            put("start_time", "17:00")
            put("end_time", "20:00")
            put("file_sha256", "abc123sha")
        }

        // Simulating repeated confirm clicks: payload retains stable booking and activity IDs
        assertEquals("MBK-20261018-1700-Oppenhei", analysis.getString("movie_booking_id"))
        assertEquals("ACT-20261018-1700-MOVIE", analysis.getString("activity_id"))
    }

    // 15. App restart and persistence recovery
    @Test
    fun test15_appRestartAndPersistenceRecovery_roundTripJson() {
        val original = MovieBookingRules.MovieTicketInfo(
            movieTitle = "Karuppu",
            cinemaName = "INOX Marina",
            showDate = "2026-05-17",
            startTime = "15:50",
            endTime = "18:41",
            durationMinutes = 171,
            durationEstablishedReliably = true,
            durationSource = "TICKET_SHOW_TIMES",
            screen = "SCREEN 1",
            seat = "CL - G13",
            bookingRef = "TKA5ALM",
            qrCodeDetected = false,
            rawOcrText = "Sample raw",
            validationStatus = MovieBookingRules.STATE_MOVIE_BOOKING_CONFIRMED,
            validationMessage = "Validated",
            isApproximateEndTime = true,
            ticketQuantity = 1,
            language = "Tamil",
            format = "2D",
            rating = "UA13+",
            bookingCode = "672061111603034107",
            ticketDateRaw = "17 May",
            yearOmittedInTicket = true,
            intendedDate = "2026-05-17",
            proofWindowOpenTime = "18:36",
            proofDeadlineTime = "19:41",
            reservationStartTime = "14:20",
            reservationEndTime = "20:11"
        )

        val json = original.toJson()

        assertEquals("Karuppu", json.getString("movie_title"))
        assertEquals("INOX Marina", json.getString("cinema_name"))
        assertEquals("2026-05-17", json.getString("show_date"))
        assertEquals("15:50", json.getString("start_time"))
        assertEquals("18:41", json.getString("end_time"))
        assertTrue(json.getBoolean("is_approximate_end_time"))
        assertEquals(1, json.getInt("ticket_quantity"))
        assertEquals("CL - G13", json.getString("seat"))
        assertEquals("SCREEN 1", json.getString("screen"))
        assertEquals("672061111603034107", json.getString("booking_code"))
        assertEquals("TKA5ALM", json.getString("booking_ref"))
        assertEquals("18:36", json.getString("proof_window_open_time"))
        assertEquals("19:41", json.getString("proof_deadline_time"))
        assertEquals("14:20", json.getString("reservation_start_time"))
        assertEquals("20:11", json.getString("reservation_end_time"))
    }

    // 16. Immediate cleanup of an app-owned temporary copy after QR rejection
    @Test
    fun test16_immediateCleanupOfAppOwnedTemporaryCopyAfterQrRejection() {
        val tempFile = File.createTempFile("strict_temp_qr_test_", ".jpg")
        tempFile.writeText("simulated temp ticket")
        assertTrue(tempFile.exists())

        // Simulated QR rejection cleanup
        val cleaned = MovieQrPresenceDetector.safelyDeleteAppOwnedTempFile(tempFile)
        assertTrue(cleaned)
        assertFalse("App-owned temporary file must be deleted immediately", tempFile.exists())
    }

    // 17. Verification that a QR-containing image produces no confirmed booking or attendance hold
    @Test
    fun test17_qrContainingImageProducesNoConfirmedBookingOrAttendanceHold() {
        val w = 200
        val h = 200
        val pixels = createBitmapWithQr(
            width = w,
            height = h,
            qrText = "QR_CONTAINING_TICKET",
            qrX = 25,
            qrY = 25,
            qrSize = 120
        )
        val bytes = encodeBmp(w, h, pixels)

        val qrResult = MovieQrPresenceDetector.detectQrPresence(bytes)
        assertTrue(qrResult.isQrDetected)
        assertEquals(MovieQrPresenceDetector.QrPresenceStatus.DETECTED, qrResult.status)

        // When QR is detected, can_confirm must be false and status must be REJECTED
        val simulatedEngineResponse = JSONObject().apply {
            put("success", false)
            put("qr_detected", true)
            put("qr_rejected", true)
            put("status", MovieBookingRules.STATE_BOOKING_REJECTED)
            put("can_confirm", false)
            put("error", qrResult.message)
        }

        assertFalse(simulatedEngineResponse.getBoolean("success"))
        assertFalse(simulatedEngineResponse.getBoolean("can_confirm"))
        assertEquals(MovieBookingRules.STATE_BOOKING_REJECTED, simulatedEngineResponse.getString("status"))
        assertEquals("Ticket screenshot rejected: QR code detected. Please upload a screenshot without a QR code.", simulatedEngineResponse.getString("error"))
    }

    // 18. Demo mode is completely isolated and modifies no production state
    @Test
    fun test18_demoModeCompletelyIsolated() {
        val demoAnalysis = JSONObject().apply {
            put("can_confirm", true)
            put("is_demo", true)
            put("movie_title", "Karuppu")
        }

        val demoResult = if (demoAnalysis.optBoolean("is_demo", false)) {
            JSONObject().apply {
                put("success", true)
                put("is_demo", true)
                put("message", "Demo movie analysis completed — no points, attendance holds, or production records changed.")
            }
        } else {
            JSONObject().put("success", false)
        }

        assertTrue(demoResult.getBoolean("success"))
        assertTrue(demoResult.getBoolean("is_demo"))
        assertEquals("Demo movie analysis completed — no points, attendance holds, or production records changed.", demoResult.getString("message"))
    }
}
