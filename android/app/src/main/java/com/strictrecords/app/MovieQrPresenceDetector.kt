package com.strictrecords.app

import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.LuminanceSource
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.GlobalHistogramBinarizer
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.multi.qrcode.detector.MultiDetector
import com.google.zxing.qrcode.detector.Detector
import java.io.ByteArrayInputStream
import java.io.File
import java.util.EnumMap
import kotlin.math.sqrt

/**
 * Local QR-code pattern detector for Movie Pre-booking screenshots.
 *
 * Strict Architectural Rules:
 * 1. Detects presence of QR finder patterns without decoding the QR payload.
 * 2. If a QR code is detected anywhere in the screenshot, immediately rejects the image.
 * 3. Never continues OCR, validation, scheduling, or booking creation for rejected images.
 * 4. Deletes any app-owned temporary artifacts created during the attempt (never touches gallery originals).
 * 5. Returns UNCERTAIN for corrupted, zero-contrast, or unreadable images.
 */
object MovieQrPresenceDetector {

    const val REJECTION_MESSAGE = "Ticket screenshot rejected: QR code detected. Please upload a screenshot without a QR code."
    const val UNCERTAIN_MESSAGE = "QR CHECK UNCERTAIN — PLEASE UPLOAD A DIFFERENT SCREENSHOT"

    enum class QrPresenceStatus {
        NOT_DETECTED,
        DETECTED,
        UNCERTAIN
    }

    data class QrDetectionResult(
        val status: QrPresenceStatus,
        val isQrDetected: Boolean,
        val isUncertain: Boolean,
        val message: String,
        val method: String = "ZXING_FINDER_PATTERN_MULTI_STAGE"
    )

    data class DecodedRgb(val width: Int, val height: Int, val pixels: IntArray)

    /**
     * Decodes an image into raw RGB pixel array.
     * Compatible with both Android runtime (via BitmapFactory/reflection) and JVM unit tests (via ImageIO).
     */
    fun decodeRgbPixels(bytes: ByteArray): DecodedRgb? {
        if (bytes.isEmpty()) return null

        // 1. Try Java ImageIO first (available in JVM unit tests & standard Java environments)
        try {
            val iioClass = Class.forName("javax.imageio.ImageIO")
            val readMethod = iioClass.getMethod("read", java.io.InputStream::class.java)
            val imgObj = readMethod.invoke(null, ByteArrayInputStream(bytes))
            if (imgObj != null) {
                val getW = imgObj.javaClass.getMethod("getWidth")
                val getH = imgObj.javaClass.getMethod("getHeight")
                val getRgb = imgObj.javaClass.getMethod(
                    "getRGB",
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType,
                    IntArray::class.java,
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType
                )
                val w = (getW.invoke(imgObj) as Number).toInt()
                val h = (getH.invoke(imgObj) as Number).toInt()
                if (w > 0 && h > 0) {
                    val rgb = IntArray(w * h)
                    getRgb.invoke(imgObj, 0, 0, w, h, rgb, 0, w)
                    return DecodedRgb(w, h, rgb)
                }
            }
        } catch (_: Throwable) {}

        // 2. Try Android BitmapFactory (available on Android runtime)
        try {
            val bmpFactoryClass = Class.forName("android.graphics.BitmapFactory")
            val decodeByteArrayMethod = bmpFactoryClass.getMethod(
                "decodeByteArray",
                ByteArray::class.java,
                Int::class.javaPrimitiveType,
                Int::class.javaPrimitiveType
            )
            val bmpObj = decodeByteArrayMethod.invoke(null, bytes, 0, bytes.size)
            if (bmpObj != null) {
                val getW = bmpObj.javaClass.getMethod("getWidth")
                val getH = bmpObj.javaClass.getMethod("getHeight")
                val getPixels = bmpObj.javaClass.getMethod(
                    "getPixels",
                    IntArray::class.java,
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType,
                    Int::class.javaPrimitiveType
                )
                val w = (getW.invoke(bmpObj) as Number).toInt()
                val h = (getH.invoke(bmpObj) as Number).toInt()
                if (w > 0 && h > 0) {
                    val pixels = IntArray(w * h)
                    getPixels.invoke(bmpObj, pixels, 0, w, 0, 0, w, h)
                    val recycleMethod = runCatching { bmpObj.javaClass.getMethod("recycle") }.getOrNull()
                    recycleMethod?.invoke(bmpObj)
                    return DecodedRgb(w, h, pixels)
                }
            }
        } catch (_: Throwable) {}

        // 3. Fallback: Parse uncompressed BMP header & pixels directly (pure Kotlin, no external deps)
        try {
            if (bytes.size >= 54 && bytes[0] == 'B'.code.toByte() && bytes[1] == 'M'.code.toByte()) {
                val dataOffset = (bytes[10].toInt() and 0xFF) or
                        ((bytes[11].toInt() and 0xFF) shl 8) or
                        ((bytes[12].toInt() and 0xFF) shl 16) or
                        ((bytes[13].toInt() and 0xFF) shl 24)
                val w = (bytes[18].toInt() and 0xFF) or
                        ((bytes[19].toInt() and 0xFF) shl 8) or
                        ((bytes[20].toInt() and 0xFF) shl 16) or
                        ((bytes[21].toInt() and 0xFF) shl 24)
                val rawH = (bytes[22].toInt() and 0xFF) or
                        ((bytes[23].toInt() and 0xFF) shl 8) or
                        ((bytes[24].toInt() and 0xFF) shl 16) or
                        ((bytes[25].toInt() and 0xFF) shl 24)
                val bpp = (bytes[28].toInt() and 0xFF) or ((bytes[29].toInt() and 0xFF) shl 8)
                val compression = (bytes[30].toInt() and 0xFF) or
                        ((bytes[31].toInt() and 0xFF) shl 8) or
                        ((bytes[32].toInt() and 0xFF) shl 16) or
                        ((bytes[33].toInt() and 0xFF) shl 24)
                val h = kotlin.math.abs(rawH)
                val isBottomUp = rawH > 0
                if (w > 0 && h > 0 && compression == 0 && (bpp == 24 || bpp == 32) && dataOffset < bytes.size) {
                    val bytesPerPixel = bpp / 8
                    val rowStride = (w * bytesPerPixel + 3) and 3.inv()
                    val pixels = IntArray(w * h)
                    for (y in 0 until h) {
                        val targetY = if (isBottomUp) (h - 1 - y) else y
                        val rowStart = dataOffset + y * rowStride
                        for (x in 0 until w) {
                            val pxOffset = rowStart + x * bytesPerPixel
                            if (pxOffset + 2 < bytes.size) {
                                val b = bytes[pxOffset].toInt() and 0xFF
                                val g = bytes[pxOffset + 1].toInt() and 0xFF
                                val r = bytes[pxOffset + 2].toInt() and 0xFF
                                pixels[targetY * w + x] = (r shl 16) or (g shl 8) or b
                            }
                        }
                    }
                    return DecodedRgb(w, h, pixels)
                }
            }
        } catch (_: Throwable) {}

        return null
    }

    /**
     * Inspects the given image bytes locally for the presence of a QR-code pattern.
     * Conservative multi-stage inspection:
     * - Full image multi-binarization (Hybrid, Global Histogram, Inverted).
     * - Quadrants and bottom/corner crops to catch small or corner QR codes.
     * - Does NOT decode payload.
     */
    fun detectQrPresence(imageBytes: ByteArray): QrDetectionResult {
        if (imageBytes.isEmpty()) {
            return QrDetectionResult(
                status = QrPresenceStatus.UNCERTAIN,
                isQrDetected = false,
                isUncertain = true,
                message = UNCERTAIN_MESSAGE
            )
        }

        val decoded = decodeRgbPixels(imageBytes)
        if (decoded == null || decoded.width < 10 || decoded.height < 10) {
            return QrDetectionResult(
                status = QrPresenceStatus.UNCERTAIN,
                isQrDetected = false,
                isUncertain = true,
                message = UNCERTAIN_MESSAGE
            )
        }

        return detectQrPresenceInRgb(decoded.width, decoded.height, decoded.pixels)
    }

    /**
     * Evaluates RGB pixels directly for QR pattern presence.
     */
    fun detectQrPresenceInRgb(width: Int, height: Int, pixels: IntArray): QrDetectionResult {
        if (width < 10 || height < 10 || pixels.isEmpty()) {
            return QrDetectionResult(
                status = QrPresenceStatus.UNCERTAIN,
                isQrDetected = false,
                isUncertain = true,
                message = UNCERTAIN_MESSAGE
            )
        }

        val baseSource = RGBLuminanceSource(width, height, pixels)
        val hints = EnumMap<DecodeHintType, Any>(DecodeHintType::class.java).apply {
            put(DecodeHintType.TRY_HARDER, java.lang.Boolean.TRUE)
        }

        // Stage 1: Full-image detection across multiple binarizations
        if (checkLuminanceSourceForQr(baseSource, hints)) {
            return QrDetectionResult(
                status = QrPresenceStatus.DETECTED,
                isQrDetected = true,
                isUncertain = false,
                message = REJECTION_MESSAGE,
                method = "FULL_IMAGE_DETECTOR"
            )
        }

        // Stage 2: Inverted luminance check (light-on-dark / inverted QR code)
        if (baseSource.isCropSupported) {
            val inverted = baseSource.invert()
            if (checkLuminanceSourceForQr(inverted, hints)) {
                return QrDetectionResult(
                    status = QrPresenceStatus.DETECTED,
                    isQrDetected = true,
                    isUncertain = false,
                    message = REJECTION_MESSAGE,
                    method = "INVERTED_IMAGE_DETECTOR"
                )
            }
        }

        // Stage 3: Sub-region crops for small corner and bottom-positioned QR codes
        if (width >= 60 && height >= 60 && baseSource.isCropSupported) {
            val subRegions = listOf(
                // 4 Quadrants
                intArrayOf(0, 0, width / 2, height / 2),
                intArrayOf(width / 2, 0, width / 2, height / 2),
                intArrayOf(0, height / 2, width / 2, height / 2),
                intArrayOf(width / 2, height / 2, width / 2, height / 2),
                // Bottom third / half where ticket QR codes commonly appear
                intArrayOf(0, height * 2 / 3, width, height / 3),
                intArrayOf(0, height / 2, width, height / 2),
                // Top corners
                intArrayOf(0, 0, width * 2 / 5, height * 2 / 5),
                intArrayOf(width * 3 / 5, 0, width * 2 / 5, height * 2 / 5)
            )

            for (crop in subRegions) {
                val cx = crop[0]
                val cy = crop[1]
                val cw = crop[2]
                val ch = crop[3]
                if (cw < 30 || ch < 30) continue
                try {
                    val subSource = baseSource.crop(cx, cy, cw, ch)
                    if (checkLuminanceSourceForQr(subSource, hints)) {
                        return QrDetectionResult(
                            status = QrPresenceStatus.DETECTED,
                            isQrDetected = true,
                            isUncertain = false,
                            message = REJECTION_MESSAGE,
                            method = "SUBREGION_CROP_DETECTOR"
                        )
                    }
                } catch (_: Throwable) {}
            }
        }

        // If no QR code pattern was detected, verify image contrast:
        // If standard deviation of luminance is near zero, contrast is insufficient (washed out or blank)
        val stdDev = calculateLuminanceStdDev(pixels, width)
        if (stdDev < 2.5) {
            return QrDetectionResult(
                status = QrPresenceStatus.UNCERTAIN,
                isQrDetected = false,
                isUncertain = true,
                message = UNCERTAIN_MESSAGE
            )
        }

        return QrDetectionResult(
            status = QrPresenceStatus.NOT_DETECTED,
            isQrDetected = false,
            isUncertain = false,
            message = "No QR code pattern detected."
        )
    }

    private fun checkLuminanceSourceForQr(source: LuminanceSource, hints: Map<DecodeHintType, Any>): Boolean {
        // 1. Hybrid binarizer with standard single Detector
        try {
            val bmHybrid = BinaryBitmap(HybridBinarizer(source))
            val res = Detector(bmHybrid.blackMatrix).detect(hints)
            if (res != null && res.points != null && res.points.size >= 3) return true
        } catch (_: Throwable) {}

        // 2. Global histogram binarizer (for low-contrast or uniform backgrounds)
        try {
            val bmGlobal = BinaryBitmap(GlobalHistogramBinarizer(source))
            val res = Detector(bmGlobal.blackMatrix).detect(hints)
            if (res != null && res.points != null && res.points.size >= 3) return true
        } catch (_: Throwable) {}

        // 3. MultiDetector (handles multiple or candidate patterns)
        try {
            val bmHybrid = BinaryBitmap(HybridBinarizer(source))
            val multiRes = MultiDetector(bmHybrid.blackMatrix).detectMulti(hints)
            if (multiRes != null && multiRes.isNotEmpty()) return true
        } catch (_: Throwable) {}

        return false
    }

    /**
     * Calculates luminance standard deviation to guard against completely blank or washed-out images.
     */
    private fun calculateLuminanceStdDev(pixels: IntArray, width: Int): Double {
        if (pixels.isEmpty()) return 0.0
        var step = (pixels.size / 2048).coerceAtLeast(1)
        while (step > 1 && gcd(step, width) > 1) {
            step++
        }
        var sum = 0.0
        var count = 0
        var i = 0
        while (i < pixels.size) {
            val rgb = pixels[i]
            val r = (rgb shr 16) and 0xFF
            val g = (rgb shr 8) and 0xFF
            val b = rgb and 0xFF
            val lum = 0.299 * r + 0.587 * g + 0.114 * b
            sum += lum
            count++
            i += step
        }
        if (count == 0) return 0.0
        val mean = sum / count
        var sumSq = 0.0
        i = 0
        while (i < pixels.size) {
            val rgb = pixels[i]
            val r = (rgb shr 16) and 0xFF
            val g = (rgb shr 8) and 0xFF
            val b = rgb and 0xFF
            val lum = 0.299 * r + 0.587 * g + 0.114 * b
            val diff = lum - mean
            sumSq += diff * diff
            i += step
        }
        return sqrt(sumSq / count)
    }

    private fun gcd(a: Int, b: Int): Int {
        var x = a
        var y = b
        while (y != 0) {
            val t = y
            y = x % y
            x = t
        }
        return x
    }

    /**
     * Deletes app-owned temporary files cleanly without touching external Gallery originals.
     */
    fun safelyDeleteAppOwnedTempFile(tempFile: File?): Boolean {
        if (tempFile == null || !tempFile.exists()) return true
        return try {
            val deleted = tempFile.delete()
            if (!deleted && tempFile.exists()) {
                tempFile.deleteOnExit()
                false
            } else {
                true
            }
        } catch (_: Throwable) {
            false
        }
    }
}
