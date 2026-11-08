package com.strictrecords.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Matrix
import android.net.Uri
import android.os.Looper
import android.os.SystemClock
import android.util.AtomicFile
import androidx.exifinterface.media.ExifInterface
import com.google.android.gms.tasks.Task
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.common.MlKitException
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.label.ImageLabeling
import com.google.mlkit.vision.label.defaults.ImageLabelerOptions
import com.google.mlkit.vision.objects.ObjectDetection
import com.google.mlkit.vision.objects.defaults.ObjectDetectorOptions
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.InputStream
import java.security.MessageDigest
import java.util.Locale
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/** Pure size/provenance rules shared by diagnostics, production analysis and JVM tests. */
object EvidenceAnalysisRules {
    const val MAX_IMAGES = 5
    const val MAX_IMAGE_BYTES = 25L * 1024 * 1024
    const val MAX_BATCH_BYTES = 125L * 1024 * 1024
    const val MAX_INPUT_PIXELS = 100_000_000L
    const val MAX_ANALYSIS_SIDE = 2048
    const val MAX_ANALYSIS_PIXELS = 3_000_000L
    const val JSON_OVERHEAD_PER_IMAGE = 64L * 1024
    const val MAX_ENCODED_BATCH_BYTES = 174_762_680L + MAX_IMAGES * JSON_OVERHEAD_PER_IMAGE
    data class Budget(val passed: Boolean, val reason: String, val rawBytes: Long, val encodedBytes: Long)

    fun base64Size(bytes: Long): Long {
        require(bytes >= 0L && bytes <= Long.MAX_VALUE / 4 * 3 - 3) { "Invalid byte count" }
        return ((bytes + 2L) / 3L) * 4L
    }

    fun validateBatchBudget(sizes: List<Long>, encoded: Boolean = false, bodyLimit: Long = MAX_ENCODED_BATCH_BYTES): Budget {
        if (sizes.isEmpty() || sizes.size > MAX_IMAGES) return Budget(false, "Select between 1 and 5 images.", 0L, 0L)
        if (sizes.any { it <= 0L || it > MAX_IMAGE_BYTES }) return Budget(false, "Each image must be readable and no larger than 25 MiB.", 0L, 0L)
        val total = sizes.sum()
        val encodedTotal = sizes.sumOf { base64Size(it) + JSON_OVERHEAD_PER_IMAGE }
        return when {
            total > MAX_BATCH_BYTES -> Budget(false, "Combined image size exceeds 125 MiB.", total, encodedTotal)
            encoded && (bodyLimit <= 0L || encodedTotal > bodyLimit) -> Budget(false, "Encoded images and metadata exceed this transport's body limit.", total, encodedTotal)
            else -> Budget(true, "Within the image and transport budgets.", total, encodedTotal)
        }
    }

    fun decodeSampleSize(width: Int, height: Int, maxSide: Int = MAX_ANALYSIS_SIDE, maxPixels: Long = MAX_ANALYSIS_PIXELS): Int {
        require(width in 100..50_000 && height in 100..50_000 && width.toLong() * height <= MAX_INPUT_PIXELS) { "Image dimensions exceed the supported pixel budget." }
        require(maxSide >= 100 && maxPixels > 0L)
        var sample = 1
        while (max(width, height) / sample > maxSide || ceil(width.toDouble() / sample).toLong() * ceil(height.toDouble() / sample).toLong() > maxPixels) {
            sample *= 2
        }
        return sample
    }

    fun acceptsRealOcr(engine: String, status: String, expectedEngine: String): Boolean = engine == expectedEngine && status in setOf("OCR_COMPLETED", "NO_TEXT_DETECTED")

    fun imageTokens(text: String): List<String> = Regex("[\\p{L}\\p{N}][\\p{L}\\p{N}_-]{2,}")
        .findAll(text).map { it.value.lowercase(Locale.ROOT) }.distinct().take(2048).toList()
}

/** On-device advisory analysis. Attendance, liveness, scoring and approvals are never modified here. */
object EvidenceAnalysisEngine {
    const val PIPELINE_VERSION = "android-evidence-v1"
    const val OCR_ENGINE = "mlkit-latin-16.0.1-v1"
    const val VISION_ENGINE = "mlkit-image-labeling-17.0.9-v1"
    const val OBJECT_ENGINE = "mlkit-object-detection-17.0.2-v1"
    private const val TASK_TIMEOUT_MS = 8_000L
    private val laneLock = Any()
    private val activeTasks = mutableMapOf<String, Task<*>>()
    private val recognizer by lazy { TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS) }
    private val labeler by lazy { ImageLabeling.getClient(ImageLabelerOptions.Builder().setConfidenceThreshold(0.25f).build()) }
    private val objectDetector by lazy { ObjectDetection.getClient(ObjectDetectorOptions.Builder()
        .setDetectorMode(ObjectDetectorOptions.SINGLE_IMAGE_MODE).enableMultipleObjects().enableClassification().build()) }

    data class Attachment(val id: String, val bytes: ByteArray, val fileName: String = "evidence.jpg")

    private interface Source {
        val length: Long
        fun open(): InputStream
        fun bounds(options: BitmapFactory.Options)
        fun bitmap(options: BitmapFactory.Options): Bitmap?
    }
    private class BytesSource(private val bytes: ByteArray) : Source {
        override val length get() = bytes.size.toLong()
        override fun open() = ByteArrayInputStream(bytes)
        override fun bounds(options: BitmapFactory.Options) { BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options) }
        override fun bitmap(options: BitmapFactory.Options) = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)
    }
    private class FileSource(private val file: File) : Source {
        override val length get() = file.length()
        override fun open() = FileInputStream(file)
        override fun bounds(options: BitmapFactory.Options) { BitmapFactory.decodeFile(file.absolutePath, options) }
        override fun bitmap(options: BitmapFactory.Options) = BitmapFactory.decodeFile(file.absolutePath, options)
    }

    /** Keeps a timed-out SDK input alive only until that real task finishes. At most one task per stage. */
    private class BitmapLease(val bitmap: Bitmap) {
        private var readers = 0
        private var ownerFinished = false
        @Synchronized fun retain() { readers++ }
        @Synchronized fun release() { readers--; recycleIfFinished() }
        @Synchronized fun finish() { ownerFinished = true; recycleIfFinished() }
        private fun recycleIfFinished() { if (ownerFinished && readers == 0 && !bitmap.isRecycled) bitmap.recycle() }
    }

    fun validateBatchBudget(sizes: List<Long>, encoded: Boolean = false, bodyLimit: Long = EvidenceAnalysisRules.MAX_ENCODED_BATCH_BYTES) =
        EvidenceAnalysisRules.validateBatchBudget(sizes, encoded, bodyLimit)

    fun availability(): JSONObject = JSONObject().apply {
        put("ocr", "BUNDLED_ON_DEVICE_LATIN")
        put("vision", "BUNDLED_ON_DEVICE_GLOBAL_LABELS")
        put("object_detection", "OPTIONAL_UNBUNDLED_MODEL")
        put("pipeline_version", PIPELINE_VERSION)
        put("cloud_evidence_upload", false)
        put("scripts", JSONArray(listOf("Latin")))
    }

    fun analyze(context: Context, originalBytes: ByteArray, attachmentId: String, taskId: String, batchId: String = "",
        persistDerivatives: Boolean = false, useObjectDetection: Boolean = false, priorResult: JSONObject? = null,
        retryStages: Set<String> = emptySet()): JSONObject = analyzeSource(context, BytesSource(originalBytes), attachmentId, taskId,
        batchId, persistDerivatives, useObjectDetection, priorResult, retryStages)

    fun analyzeFile(context: Context, file: File, attachmentId: String, taskId: String, batchId: String = "",
        persistDerivatives: Boolean = false, useObjectDetection: Boolean = false, priorResult: JSONObject? = null,
        retryStages: Set<String> = emptySet()): JSONObject = analyzeSource(context, FileSource(file), attachmentId, taskId,
        batchId, persistDerivatives, useObjectDetection, priorResult, retryStages)

    /** Diagnostic copy is streamed, bounded and always removed; originals/production records are untouched. */
    fun analyzeUri(context: Context, uri: Uri, attachmentId: String, taskId: String, batchId: String = "",
        persistDerivatives: Boolean = false, useObjectDetection: Boolean = false, priorResult: JSONObject? = null,
        retryStages: Set<String> = emptySet()): JSONObject {
        if (Looper.myLooper() == Looper.getMainLooper()) return failed(attachmentId, taskId, batchId, "BACKGROUND_THREAD_REQUIRED", "Run image analysis on a background worker.")
        var scratch: File? = null
        return try {
            scratch = File.createTempFile("evidence-analysis-", ".img", context.cacheDir)
            context.contentResolver.openInputStream(uri)?.use { input ->
                scratch.outputStream().use { output ->
                    val buffer = ByteArray(8192)
                    var total = 0L
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        total += count
                        require(total <= EvidenceAnalysisRules.MAX_IMAGE_BYTES) { "Image exceeds 25 MiB." }
                        output.write(buffer, 0, count)
                    }
                }
            } ?: return failed(attachmentId, taskId, batchId, "IMAGE_UNREADABLE", "The selected image could not be opened.")
            analyzeFile(context, scratch, attachmentId, taskId, batchId, persistDerivatives, useObjectDetection, priorResult, retryStages)
        } catch (_: Exception) {
            failed(attachmentId, taskId, batchId, "IMAGE_UNREADABLE_OR_OVERSIZE", "The selected image is unreadable or exceeds the image budget.")
        } finally { scratch?.delete() }
    }

    fun analyzeBatch(context: Context, attachments: List<Attachment>, taskId: String, batchId: String,
        persistDerivatives: Boolean = false, useObjectDetection: Boolean = false): JSONObject {
        val budget = validateBatchBudget(attachments.map { it.bytes.size.toLong() })
        if (!budget.passed) return JSONObject().put("success", false).put("status", "INVALID_BATCH").put("error", budget.reason)
        val results = JSONArray()
        attachments.forEach { attachment -> results.put(analyze(context, attachment.bytes, attachment.id, taskId, batchId, persistDerivatives, useObjectDetection)) }
        return JSONObject().put("success", true).put("status", "BATCH_ANALYZED").put("batch_id", batchId)
            .put("task_id", taskId).put("sequential", true).put("original_total_bytes", budget.rawBytes).put("attachments", results)
    }

    private fun analyzeSource(context: Context, source: Source, attachmentId: String, taskId: String, batchId: String,
        persist: Boolean, objects: Boolean, prior: JSONObject?, retry: Set<String>): JSONObject {
        if (Looper.myLooper() == Looper.getMainLooper()) return failed(attachmentId, taskId, batchId, "BACKGROUND_THREAD_REQUIRED", "Run image analysis on a background worker.")
        val started = SystemClock.elapsedRealtime()
        if (source.length !in 1..EvidenceAnalysisRules.MAX_IMAGE_BYTES) return failed(attachmentId, taskId, batchId, "INVALID_IMAGE_SIZE", "An image must contain between 1 byte and 25 MiB.")
        var lease: BitmapLease? = null
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            source.bounds(bounds)
            require(bounds.outMimeType in setOf("image/jpeg", "image/png", "image/webp", "image/jpg")) { "Unsupported image format." }
            val sample = EvidenceAnalysisRules.decodeSampleSize(bounds.outWidth, bounds.outHeight)
            val originalSha = source.open().use(::hashStream)
            val orientation = runCatching { source.open().use { ExifInterface(it).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL) } }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)
            val decoded = source.bitmap(BitmapFactory.Options().apply { inSampleSize = sample; inPreferredConfig = Bitmap.Config.ARGB_8888 })
                ?: return failed(attachmentId, taskId, batchId, "IMAGE_DECODE_FAILED", "The image could not be decoded.")
            val bitmap = orient(decoded, orientation)
            lease = BitmapLease(bitmap)
            val result = base(attachmentId, taskId, batchId).apply {
                put("original", JSONObject().put("sha256", originalSha).put("bytes", source.length)
                    .put("width", bounds.outWidth).put("height", bounds.outHeight).put("mime_type", bounds.outMimeType)
                    .put("exif_orientation", orientation).put("oriented_width", if (orientation in 5..8) bounds.outHeight else bounds.outWidth)
                    .put("oriented_height", if (orientation in 5..8) bounds.outWidth else bounds.outHeight))
                put("analysis_image", JSONObject().put("width", bitmap.width).put("height", bitmap.height).put("decode_sample_size", sample)
                    .put("coordinate_space", "exif_normalized_analysis_pixels").put("pixel_budget", EvidenceAnalysisRules.MAX_ANALYSIS_PIXELS))
            }
            val reusable = prior?.takeIf { it.optString("pipeline_version") == PIPELINE_VERSION && it.optJSONObject("original")?.optString("sha256") == originalSha }
            fun previous(name: String, good: Set<String>): JSONObject? {
                val stage = reusable?.optJSONObject(name) ?: return null
                if (name in retry || stage.optString("status") !in good) return null
                return JSONObject(stage.toString()).put("reused", true)
            }
            val ocr = previous("ocr", setOf("OCR_COMPLETED", "NO_TEXT_DETECTED")) ?: runOcr(lease)
            result.put("ocr", ocr)
            val vision = previous("vision", setOf("COMPLETED", "NO_LABELS_DETECTED")) ?: runLabels(lease)
            result.put("vision", vision)
            result.put("object_detection", if (objects) previous("object_detection", setOf("COMPLETED", "NO_OBJECTS_DETECTED")) ?: runObjects(lease)
                else JSONObject().put("status", "NOT_REQUESTED").put("engine", OBJECT_ENGINE).put("boxes", JSONArray())
                    .put("reason", "Optional unbundled detector is disabled; global image labels have no fabricated object boxes."))
            result.put("measurements", measure(bitmap))
            result.put("texture", JSONObject().put("status", "UNCERTAIN").put("material", JSONObject.NULL)
                .put("reason", "Visible pixel edges and global labels cannot reliably establish garment fabric, material, wearer identity or task completion."))
            result.put("derivatives", derivatives(context, bitmap, originalSha, persist))
            val statuses = listOf(ocr.optString("status"), vision.optString("status"))
            result.put("success", true)
            result.put("status", if (statuses.any { it in setOf("FAILED", "TIMEOUT", "MODEL_UNAVAILABLE", "MODEL_BUSY") }) "PARTIAL" else "COMPLETED")
            result.put("elapsed_ms", SystemClock.elapsedRealtime() - started)
            result
        } catch (_: OutOfMemoryError) {
            failed(attachmentId, taskId, batchId, "MEMORY_LIMIT", "The image exceeded available decode memory; use a smaller readable image.")
        } catch (_: Exception) {
            failed(attachmentId, taskId, batchId, "INVALID_IMAGE", "The image is corrupt, unsupported or exceeds the pixel budget.")
        } finally { lease?.finish() }
    }

    private fun base(attachmentId: String, taskId: String, batchId: String) = JSONObject().apply {
        put("pipeline_version", PIPELINE_VERSION); put("attachment_id", attachmentId); put("task_id", taskId); put("batch_id", batchId)
        put("advisory_only", true); put("points_changed", false); put("liveness_evaluated", false)
        put("cloud_evidence_upload", false); put("processed_at", System.currentTimeMillis())
    }
    private fun failed(id: String, task: String, batch: String, code: String, message: String) = base(id, task, batch)
        .put("success", false).put("status", "FAILED").put("error_code", code).put("error", message)

    private fun runOcr(lease: BitmapLease): JSONObject = stage("ocr", OCR_ENGINE, lease, { recognizer.process(InputImage.fromBitmap(lease.bitmap, 0)) }) { text ->
        val blocks = JSONArray()
        text.textBlocks.take(200).forEach { block ->
            val lines = JSONArray()
            block.lines.take(200).forEach { line -> lines.put(JSONObject().put("text", line.text.take(2000)).put("box", box(line.boundingBox)).put("confidence", JSONObject.NULL)) }
            blocks.put(JSONObject().put("text", block.text.take(4000)).put("box", box(block.boundingBox)).put("lines", lines))
        }
        JSONObject().put("status", if (text.text.isBlank()) "NO_TEXT_DETECTED" else "OCR_COMPLETED")
            .put("text", text.text.take(32_000)).put("blocks", blocks).put("script", "Latin").put("confidence", JSONObject.NULL)
            .put("confidence_reason", "No calibrated whole-document confidence is supplied by this engine.")
    }

    private fun runLabels(lease: BitmapLease): JSONObject = stage("vision", VISION_ENGINE, lease, { labeler.process(InputImage.fromBitmap(lease.bitmap, 0)) }) { labels ->
        val values = JSONArray()
        labels.sortedByDescending { it.confidence }.take(30).forEach { label -> values.put(JSONObject().put("label", label.text)
            .put("confidence", label.confidence.toDouble()).put("index", label.index).put("scope", "GLOBAL_IMAGE")) }
        JSONObject().put("status", if (labels.isEmpty()) "NO_LABELS_DETECTED" else "COMPLETED").put("labels", values)
            .put("scope", "GLOBAL_IMAGE").put("boxes", JSONArray()).put("limitations", "Global labels are hypotheses; they do not localize objects or verify a task, action, person identity or garment material.")
    }

    private fun runObjects(lease: BitmapLease): JSONObject = stage("object_detection", OBJECT_ENGINE, lease,
        { objectDetector.process(InputImage.fromBitmap(lease.bitmap, 0)) }) { objects ->
        val values = JSONArray()
        objects.take(5).forEach { detected ->
            val labels = JSONArray()
            detected.labels.forEach { label -> labels.put(JSONObject().put("label", label.text).put("confidence", label.confidence.toDouble()).put("index", label.index)) }
            values.put(JSONObject().put("box", box(detected.boundingBox)).put("labels", labels).put("tracking_id", detected.trackingId ?: JSONObject.NULL))
        }
        JSONObject().put("status", if (objects.isEmpty()) "NO_OBJECTS_DETECTED" else "COMPLETED").put("boxes", values)
            .put("unbundled_model", true).put("limitations", "At most five actual detected boxes and coarse categories; first-use offline availability is not guaranteed.")
    }

    private fun <T> stage(name: String, engine: String, lease: BitmapLease, begin: () -> Task<T>, convert: (T) -> JSONObject): JSONObject {
        val start = SystemClock.elapsedRealtime()
        val task: Task<T>
        try {
            synchronized(laneLock) {
                if (activeTasks[name]?.isComplete == false) return JSONObject().put("status", "MODEL_BUSY").put("engine", engine)
                    .put("retryable", true).put("reason", "A previous real SDK task is still completing; no additional input was queued.")
                lease.retain()
                task = try { begin() } catch (error: Throwable) { lease.release(); throw error }
                activeTasks[name] = task
                task.addOnCompleteListener { lease.release() }
            }
            val value = Tasks.await(task, TASK_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            return convert(value).put("engine", engine).put("elapsed_ms", SystemClock.elapsedRealtime() - start).put("reused", false)
        } catch (error: Throwable) {
            val cause = error.cause ?: error
            val status = when {
                error is TimeoutException || cause is TimeoutException -> "TIMEOUT"
                cause is MlKitException && cause.errorCode == MlKitException.UNAVAILABLE -> "MODEL_UNAVAILABLE"
                name == "object_detection" -> "MODEL_UNAVAILABLE"
                else -> "FAILED"
            }
            return JSONObject().put("status", status).put("engine", engine).put("retryable", true)
                .put("elapsed_ms", SystemClock.elapsedRealtime() - start).put("error_type", cause.javaClass.simpleName)
                .put("reason", if (status == "MODEL_UNAVAILABLE") "The optional on-device model is unavailable. Other image stages remain usable." else "The on-device stage did not complete. Its result is not fabricated.")
        }
    }

    private fun box(rect: android.graphics.Rect?): Any = rect?.let { JSONObject().put("left", it.left).put("top", it.top).put("right", it.right).put("bottom", it.bottom) } ?: JSONObject.NULL

    private fun orient(source: Bitmap, orientation: Int): Bitmap {
        val matrix = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.setScale(-1f, 1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> matrix.setRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> matrix.setScale(1f, -1f)
            ExifInterface.ORIENTATION_TRANSPOSE -> { matrix.setRotate(90f); matrix.postScale(-1f, 1f) }
            ExifInterface.ORIENTATION_ROTATE_90 -> matrix.setRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> { matrix.setRotate(-90f); matrix.postScale(-1f, 1f) }
            ExifInterface.ORIENTATION_ROTATE_270 -> matrix.setRotate(-90f)
            else -> return source
        }
        return try { Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, true).also { if (it !== source) source.recycle() } }
        catch (error: Throwable) { source.recycle(); throw error }
    }

    private fun measure(bitmap: Bitmap): JSONObject {
        val scale = min(1.0, 128.0 / max(bitmap.width, bitmap.height))
        val small = Bitmap.createScaledBitmap(bitmap, max(1, (bitmap.width * scale).roundToInt()), max(1, (bitmap.height * scale).roundToInt()), true)
        try {
            val pixels = IntArray(small.width * small.height)
            small.getPixels(pixels, 0, small.width, 0, 0, small.width, small.height)
            val histogram = mutableMapOf<Int, Int>()
            var mean = 0.0; var squares = 0.0; var dark = 0; var bright = 0
            val luma = DoubleArray(pixels.size)
            pixels.forEachIndexed { index, pixel ->
                val red = Color.red(pixel); val green = Color.green(pixel); val blue = Color.blue(pixel)
                val value = 0.299 * red + 0.587 * green + 0.114 * blue
                luma[index] = value; mean += value; squares += value * value
                if (value < 10) dark++; if (value > 245) bright++
                val bucket = ((red / 32) shl 6) or ((green / 32) shl 3) or (blue / 32)
                histogram[bucket] = (histogram[bucket] ?: 0) + 1
            }
            val count = max(1, pixels.size)
            mean /= count
            val contrast = sqrt(max(0.0, squares / count - mean * mean))
            var edge = 0.0; var edgeCount = 0
            for (y in 1 until small.height - 1) for (x in 1 until small.width - 1) {
                val index = y * small.width + x
                val laplacian = 4 * luma[index] - luma[index - 1] - luma[index + 1] - luma[index - small.width] - luma[index + small.width]
                edge += laplacian * laplacian; edgeCount++
            }
            val sharpness = edge / max(1, edgeCount)
            val colors = JSONArray()
            histogram.entries.sortedByDescending { it.value }.take(5).forEach { (bucket, occurrences) ->
                val red = ((bucket shr 6) and 7) * 32 + 16
                val green = ((bucket shr 3) and 7) * 32 + 16
                val blue = (bucket and 7) * 32 + 16
                colors.put(JSONObject().put("hex", String.format(Locale.ROOT, "#%02X%02X%02X", red, green, blue))
                    .put("fraction", occurrences.toDouble() / count).put("scope", "ENTIRE_IMAGE_PIXEL_BUCKET"))
            }
            val warnings = JSONArray()
            if (mean < 45) warnings.put("Dark image: some text or objects may be unreadable.")
            if (mean > 230) warnings.put("Bright image: highlights may hide detail.")
            if (contrast < 12) warnings.put("Low measured contrast; readable detail is uncertain.")
            if (sharpness < 20) warnings.put("Low measured edge energy; blur or a plain surface may be present.")
            return JSONObject().put("source", "MEASURED_IMAGE_PIXELS").put("dominant_colors", colors)
                .put("quality", JSONObject().put("mean_luma", mean).put("contrast_stddev", contrast).put("laplacian_energy", sharpness)
                    .put("dark_pixel_fraction", dark.toDouble() / count).put("bright_pixel_fraction", bright.toDouble() / count)
                    .put("sample_width", small.width).put("sample_height", small.height).put("warnings", warnings))
        } finally { if (small !== bitmap) small.recycle() }
    }

    private fun derivatives(context: Context, bitmap: Bitmap, originalSha: String, persist: Boolean): JSONObject {
        val result = JSONObject().put("original_sha256", originalSha).put("original_preserved", true).put("persisted", persist)
        for ((name, maxSide, budget) in listOf(Triple("preview", 1280, 400 * 1024), Triple("analysis", 2048, 900 * 1024))) {
            val encoded = compact(bitmap, maxSide, budget)
            val sha = hashBytes(encoded)
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(encoded, 0, encoded.size, bounds)
            val metadata = JSONObject().put("sha256", sha).put("bytes", encoded.size).put("base64_bytes", EvidenceAnalysisRules.base64Size(encoded.size.toLong()))
                .put("mime_type", "image/jpeg").put("width", bounds.outWidth).put("height", bounds.outHeight).put("exif_normalized", true)
            if (persist) {
                val directory = File(context.filesDir, "proofs/evidence_analysis").apply { mkdirs() }
                val target = File(directory, "$sha.jpg")
                if (!target.isFile || target.inputStream().use(::hashStream) != sha) {
                    val atomic = AtomicFile(target)
                    val stream = atomic.startWrite()
                    try { stream.write(encoded); atomic.finishWrite(stream) } catch (error: Throwable) { atomic.failWrite(stream); throw error }
                }
                metadata.put("file_path", target.absolutePath)
            }
            result.put(name, metadata)
        }
        return result
    }

    /** A complete JPEG preview; encoded images are never truncated. Original bytes are unchanged. */
    fun createPreview(bytes: ByteArray, maxSide: Int = 1280, maxBytes: Int = 400 * 1024): ByteArray? = runCatching {
        val source = BytesSource(bytes)
        require(source.length in 1..EvidenceAnalysisRules.MAX_IMAGE_BYTES)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }; source.bounds(bounds)
        val sample = EvidenceAnalysisRules.decodeSampleSize(bounds.outWidth, bounds.outHeight, maxSide)
        val decoded = source.bitmap(BitmapFactory.Options().apply { inSampleSize = sample; inPreferredConfig = Bitmap.Config.ARGB_8888 }) ?: return null
        val orientation = runCatching { source.open().use { ExifInterface(it).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL) } }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)
        val bitmap = orient(decoded, orientation)
        try { compact(bitmap, maxSide, maxBytes) } finally { bitmap.recycle() }
    }.getOrNull()

    private fun compact(source: Bitmap, maxSide: Int, budget: Int): ByteArray {
        require(maxSide >= 100 && budget > 1024)
        val initialScale = min(1.0, maxSide.toDouble() / max(source.width, source.height))
        var current = if (initialScale < 1.0) Bitmap.createScaledBitmap(source, max(1, (source.width * initialScale).roundToInt()), max(1, (source.height * initialScale).roundToInt()), true) else source
        try {
            while (true) {
                for (quality in listOf(88, 76, 64, 52, 40)) {
                    val output = ByteArrayOutputStream()
                    require(current.compress(Bitmap.CompressFormat.JPEG, quality, output)) { "Preview encoding failed." }
                    if (output.size() <= budget) return output.toByteArray()
                }
                require(min(current.width, current.height) > 100) { "Readable derivative cannot fit the byte budget." }
                val smaller = Bitmap.createScaledBitmap(current, max(1, (current.width * 0.8).roundToInt()), max(1, (current.height * 0.8).roundToInt()), true)
                if (current !== source) current.recycle()
                current = smaller
            }
        } finally { if (current !== source && !current.isRecycled) current.recycle() }
    }

    private fun hashBytes(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    private fun hashStream(stream: InputStream): String {
        val digest = MessageDigest.getInstance("SHA-256"); val buffer = ByteArray(8192)
        while (true) { val count = stream.read(buffer); if (count < 0) break; digest.update(buffer, 0, count) }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
