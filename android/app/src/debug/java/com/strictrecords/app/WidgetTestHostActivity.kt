package com.strictrecords.app

import android.app.Activity
import android.os.Bundle
import android.widget.FrameLayout

/** Emulator-only test host; excluded from release builds and has no launcher entry. */
class WidgetTestHostActivity : Activity() {
    lateinit var frame: FrameLayout
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        frame=FrameLayout(this).apply { setBackgroundColor(android.graphics.Color.rgb(20,29,40)); setPadding(24,48,24,24) }
        setContentView(frame)
    }
}
