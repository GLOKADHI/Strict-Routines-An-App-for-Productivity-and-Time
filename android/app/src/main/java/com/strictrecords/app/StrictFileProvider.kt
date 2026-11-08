package com.strictrecords.app

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.File
import java.io.FileNotFoundException

/**
 * StrictFileProvider: High-security ContentProvider for APK update installation and camera evidence capture.
 * Provides secure content:// URIs for downloaded APK binaries and captured camera photos without external dependencies.
 */
class StrictFileProvider : ContentProvider() {

    companion object {
        const val AUTHORITY = "com.strictrecords.app.fileprovider"

        fun getUriForFile(context: Context, file: File): Uri {
            return Uri.parse("content://$AUTHORITY/${file.name}")
        }
    }

    override fun onCreate(): Boolean = true

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor? {
        val fileName = uri.lastPathSegment ?: throw FileNotFoundException("Invalid URI")
        val cacheCameraDir = File(context?.cacheDir, "camera").apply { mkdirs() }
        val cacheUpdatesDir = File(context?.cacheDir, "updates").apply { mkdirs() }
        val cameraFile = File(cacheCameraDir, fileName)
        val updateFile = File(cacheUpdatesDir, fileName)

        val file = when {
            cameraFile.exists() -> cameraFile
            updateFile.exists() -> updateFile
            mode.contains("w") || mode.contains("rw") -> cameraFile
            else -> cameraFile
        }

        val parsedMode = when (mode) {
            "w", "wt" -> ParcelFileDescriptor.MODE_WRITE_ONLY or ParcelFileDescriptor.MODE_CREATE or ParcelFileDescriptor.MODE_TRUNCATE
            "wa" -> ParcelFileDescriptor.MODE_WRITE_ONLY or ParcelFileDescriptor.MODE_CREATE or ParcelFileDescriptor.MODE_APPEND
            "rw" -> ParcelFileDescriptor.MODE_READ_WRITE or ParcelFileDescriptor.MODE_CREATE
            "rwt" -> ParcelFileDescriptor.MODE_READ_WRITE or ParcelFileDescriptor.MODE_CREATE or ParcelFileDescriptor.MODE_TRUNCATE
            else -> {
                if (!file.exists()) {
                    throw FileNotFoundException("File not found: ${file.absolutePath}")
                }
                ParcelFileDescriptor.MODE_READ_ONLY
            }
        }
        return ParcelFileDescriptor.open(file, parsedMode)
    }

    override fun getType(uri: Uri): String {
        val name = uri.lastPathSegment.orEmpty().lowercase()
        return when {
            name.endsWith(".apk") -> "application/vnd.android.package-archive"
            name.endsWith(".png") -> "image/png"
            name.endsWith(".webp") -> "image/webp"
            else -> "image/jpeg"
        }
    }

    override fun query(
        uri: Uri,
        projection: Array<out String>?,
        selection: String?,
        selectionArgs: Array<out String>?,
        sortOrder: String?
    ): Cursor {
        val fileName = uri.lastPathSegment ?: "strictrecords_file"
        val cacheCameraDir = File(context?.cacheDir, "camera")
        val cacheUpdatesDir = File(context?.cacheDir, "updates")
        val cameraFile = File(cacheCameraDir, fileName)
        val updateFile = File(cacheUpdatesDir, fileName)
        val file = if (cameraFile.exists()) cameraFile else updateFile

        val cols = projection ?: arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)
        val cursor = MatrixCursor(cols)
        val row = cursor.newRow()
        for (col in cols) {
            when (col) {
                OpenableColumns.DISPLAY_NAME -> row.add(file.name)
                OpenableColumns.SIZE -> row.add(if (file.exists()) file.length() else 0L)
                else -> row.add(null)
            }
        }
        return cursor
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null

    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int {
        val fileName = uri.lastPathSegment ?: return 0
        val cameraFile = File(File(context?.cacheDir, "camera"), fileName)
        val updateFile = File(File(context?.cacheDir, "updates"), fileName)
        var count = 0
        if (cameraFile.exists() && cameraFile.delete()) count++
        if (updateFile.exists() && updateFile.delete()) count++
        return count
    }

    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?): Int = 0
}
