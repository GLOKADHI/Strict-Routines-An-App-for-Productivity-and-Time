package com.strictrecords.app

object DiagnosticLayoutRules {
    // A phone or large font uses two full-width lines instead of competing columns.
    fun stacked(contentWidthDp: Float, fontScale: Float): Boolean = contentWidthDp < 520f * fontScale.coerceAtLeast(1f)
    fun itemStacked(contentWidthDp: Float, fontScale: Float, statusTextLength: Int = 0): Boolean =
        stacked(contentWidthDp, fontScale) || statusTextLength > 24
    fun labelWidth(contentWidthDp: Float, fontScale: Float): Float =
        if (stacked(contentWidthDp, fontScale)) contentWidthDp else contentWidthDp * .6f
    fun statusWidth(contentWidthDp: Float, fontScale: Float): Float =
        if (stacked(contentWidthDp, fontScale)) contentWidthDp else contentWidthDp * .4f
}
