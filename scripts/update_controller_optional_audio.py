import sys

ctrl_path = 'android/app/src/main/java/com/strictrecords/app/WakeUpVerificationController.kt'
with open(ctrl_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update Step 2 Card headers & text
target_step2_header = 'text = if (step2Done) "STEP 2: BLUETOOTH EARBUDS ROUTE — ✅ VERIFIED" else "STEP 2: BLUETOOTH EARBUDS ROUTE — ⏳ PENDING"'
replacement_step2_header = 'text = if (step2Done) "STEP 2: BLUETOOTH EARBUDS ROUTE (OPTIONAL) — ✅ VERIFIED" else "STEP 2: BLUETOOTH EARBUDS ROUTE (OPTIONAL)"'
assert target_step2_header in content, "target_step2_header not found"
content = content.replace(target_step2_header, replacement_step2_header)

target_verify_btn = 'text = if (step2Done) "RE-VERIFY ROUTE" else "VERIFY ROUTE"'
replacement_verify_btn = 'text = if (step2Done) "RE-VERIFY ROUTE" else "Connect Earbuds — Optional"'
assert target_verify_btn in content, "target_verify_btn not found"
content = content.replace(target_verify_btn, replacement_verify_btn)

# Add Skip Earbuds button after step2BtnRow
target_step2_card_add = '''        step2Card.addView(step2Header)
        step2Card.addView(step2Desc)
        step2Card.addView(step2RouteStatus)
        step2Card.addView(step2BtnRow)
        root.addView(step2Card)'''

replacement_step2_card_add = '''        val skipEarbudsBtn = Button(activity).apply {
            text = "Skip Earbud Connection"
            setTextColor(Color.parseColor("#8B949E"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 10.5f)
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#161B22"))
                cornerRadius = dp(8).toFloat()
                setStroke(dp(1), Color.parseColor("#30363D"))
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(6) }
            setOnClickListener {
                db.setWakeConfig("wakeup_bt_skipped", today, isDevMode, "1")
                step2RouteStatus.text = "Earbuds route skipped (optional). Camera verification is sufficient."
                step2RouteStatus.setTextColor(Color.parseColor("#3FB950"))
            }
        }
        step2Card.addView(step2Header)
        step2Card.addView(step2Desc)
        step2Card.addView(step2RouteStatus)
        step2Card.addView(step2BtnRow)
        step2Card.addView(skipEarbudsBtn)
        root.addView(step2Card)'''

assert target_step2_card_add in content, "target_step2_card_add not found"
content = content.replace(target_step2_card_add, replacement_step2_card_add)

# 2. Update Step 3 Card headers & buttons
target_step3_header = 'text = if (step3Done) "STEP 3: MOTIVATION TRACK (≤ 60% VOL) — ✅ VERIFIED" else "STEP 3: MOTIVATION TRACK (≤ 60% VOL) — ⏳ PENDING"'
replacement_step3_header = 'text = if (step3Done) "STEP 3: MOTIVATION TRACK (OPTIONAL) — ✅ VERIFIED" else "STEP 3: MOTIVATION TRACK (OPTIONAL)"'
assert target_step3_header in content, "target_step3_header not found"
content = content.replace(target_step3_header, replacement_step3_header)

target_play_btn_text = 'else -> "VERIFY EARBUDS ROUTE & PLAY FULL TRACK"'
replacement_play_btn_text = 'else -> "Play Wake-Up Audio — Optional"'
assert target_play_btn_text in content, "target_play_btn_text not found"
content = content.replace(target_play_btn_text, replacement_play_btn_text)

# Add Skip Audio button and Finalize button before closeBtn
target_step3_card_add = '''        step3Card.addView(step3Header)
        step3Card.addView(step3Disclosure)
        step3Card.addView(step3StatusText)
        step3Card.addView(step3Progress)
        step3Card.addView(step3PlayBtn)
        step3Card.addView(step3PauseBtn)
        root.addView(step3Card)

        val closeBtn = Button(activity).apply {'''

replacement_step3_card_add = '''        val skipAudioBtn = Button(activity).apply {
            text = "Skip Audio"
            setTextColor(Color.parseColor("#8B949E"))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#161B22"))
                cornerRadius = dp(8).toFloat()
                setStroke(dp(1), Color.parseColor("#30363D"))
            }
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(6) }
            setOnClickListener {
                WakeUpPlaybackService.stop(activity)
                db.setWakeConfig("wakeup_track_skipped", today, isDevMode, "1")
                step3StatusText.text = "Motivation audio skipped (optional). You can complete verification."
                step3StatusText.setTextColor(Color.parseColor("#3FB950"))
                step3Progress.progress = 100
            }
        }
        step3Card.addView(step3Header)
        step3Card.addView(step3Disclosure)
        step3Card.addView(step3StatusText)
        step3Card.addView(step3Progress)
        step3Card.addView(step3PlayBtn)
        step3Card.addView(step3PauseBtn)
        step3Card.addView(skipAudioBtn)
        root.addView(step3Card)

        val finalizeWakeUpBtn = Button(activity).apply {
            text = if (isDevMode) "COMPLETE WAKE-UP DEMO TEST" else "COMPLETE WAKE-UP VERIFICATION"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            isEnabled = step1Done
            background = GradientDrawable().apply {
                setColor(if (step1Done) Color.parseColor("#238636") else Color.parseColor("#21262D"))
                cornerRadius = dp(8).toFloat()
            }
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply {
                topMargin = dp(14)
                bottomMargin = dp(8)
            }
            setOnClickListener {
                if (!step1Done) {
                    Toast.makeText(activity, "Complete Step 1 (Camera face verification) first.", Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                val (ok, msg) = db.finalizeWakeUpVerification(today, isDevMode)
                Toast.makeText(activity, msg, Toast.LENGTH_LONG).show()
                if (ok) {
                    onUpdated()
                    activeVerificationDialog?.dismiss()
                }
            }
        }
        root.addView(finalizeWakeUpBtn)

        val closeBtn = Button(activity).apply {'''

assert target_step3_card_add in content, "target_step3_card_add not found"
content = content.replace(target_step3_card_add, replacement_step3_card_add)

# In refreshAllStepStates, also update headers and finalize button state
target_refresh = 'step2Header.text = if (step2Done) "STEP 2: BLUETOOTH EARBUDS ROUTE — ✅ VERIFIED" else "STEP 2: BLUETOOTH EARBUDS ROUTE — ⏳ PENDING"'
replacement_refresh = 'step2Header.text = if (step2Done) "STEP 2: BLUETOOTH EARBUDS ROUTE (OPTIONAL) — ✅ VERIFIED" else "STEP 2: BLUETOOTH EARBUDS ROUTE (OPTIONAL)"'
assert target_refresh in content, "target_refresh not found"
content = content.replace(target_refresh, replacement_refresh)

target_refresh3 = 'step3Header.text = if (step3Done) "STEP 3: MOTIVATION TRACK (≤ 60% VOL) — ✅ VERIFIED" else "STEP 3: MOTIVATION TRACK (≤ 60% VOL) — ⏳ PENDING"'
replacement_refresh3 = 'step3Header.text = if (step3Done) "STEP 3: MOTIVATION TRACK (OPTIONAL) — ✅ VERIFIED" else "STEP 3: MOTIVATION TRACK (OPTIONAL)"'
assert target_refresh3 in content, "target_refresh3 not found"
content = content.replace(target_refresh3, replacement_refresh3)

target_refresh_end = 'step3PlayBtn.text = when {'
replacement_refresh_end = '''finalizeWakeUpBtn.isEnabled = step1Done
            finalizeWakeUpBtn.background = GradientDrawable().apply {
                setColor(if (step1Done) Color.parseColor("#238636") else Color.parseColor("#21262D"))
                cornerRadius = dp(8).toFloat()
            }
            step3PlayBtn.text = when {'''
assert target_refresh_end in content, "target_refresh_end not found"
content = content.replace(target_refresh_end, replacement_refresh_end, 1)

with open(ctrl_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated WakeUpVerificationController.kt with optional audio and earbuds")
