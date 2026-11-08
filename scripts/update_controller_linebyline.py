import re

ctrl_path = 'android/app/src/main/java/com/strictrecords/app/WakeUpVerificationController.kt'
with open(ctrl_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip_earbuds_added = False
finalize_btn_added = False

for i, line in enumerate(lines):
    # Step 2 header definition
    if 'val step2Header = TextView(activity).apply' in line:
        new_lines.append(line)
        continue
    if 'text = if (step2Done) "STEP 2: BLUETOOTH EARBUDS ROUTE' in line:
        new_lines.append('            text = if (step2Done) "STEP 2: BLUETOOTH EARBUDS ROUTE (OPTIONAL) — \\u2705 VERIFIED" else "STEP 2: BLUETOOTH EARBUDS ROUTE (OPTIONAL)"\n')
        continue
    if 'val verifyEarbudsBtn = Button(activity).apply' in line:
        new_lines.append(line)
        continue
    if 'text = if (step2Done) "RE-VERIFY ROUTE" else "VERIFY ROUTE"' in line:
        new_lines.append('            text = if (step2Done) "RE-VERIFY ROUTE" else "Connect Earbuds — Optional"\n')
        continue
    if 'step2Card.addView(step2BtnRow)' in line:
        new_lines.append(line)
        new_lines.append('''        val skipEarbudsBtn = Button(activity).apply {
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
        step2Card.addView(skipEarbudsBtn)
''')
        skip_earbuds_added = True
        continue
    if 'text = if (step3Done) "STEP 3: MOTIVATION TRACK' in line:
        new_lines.append('            text = if (step3Done) "STEP 3: MOTIVATION TRACK (OPTIONAL) — \\u2705 VERIFIED" else "STEP 3: MOTIVATION TRACK (OPTIONAL)"\n')
        continue
    if 'else -> "VERIFY EARBUDS ROUTE & PLAY FULL TRACK"' in line:
        new_lines.append('                else -> "Play Wake-Up Audio — Optional"\n')
        continue
    if 'step3Card.addView(step3PauseBtn)' in line:
        new_lines.append(line)
        new_lines.append('''        val skipAudioBtn = Button(activity).apply {
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
        step3Card.addView(skipAudioBtn)
''')
        continue
    if 'val closeBtn = Button(activity).apply' in line and not finalize_btn_added:
        new_lines.append('''        val finalizeWakeUpBtn = Button(activity).apply {
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

''')
        finalize_btn_added = True
        new_lines.append(line)
        continue

    # In refreshAllStepStates:
    if i > 580 and 'step2Header.text = if (step2Done)' in line:
        new_lines.append('            step2Header.text = if (step2Done) "STEP 2: BLUETOOTH EARBUDS ROUTE (OPTIONAL) — \\u2705 VERIFIED" else "STEP 2: BLUETOOTH EARBUDS ROUTE (OPTIONAL)"\n')
        continue
    if i > 580 and 'step3Header.text = if (step3Done)' in line:
        new_lines.append('            step3Header.text = if (step3Done) "STEP 3: MOTIVATION TRACK (OPTIONAL) — \\u2705 VERIFIED" else "STEP 3: MOTIVATION TRACK (OPTIONAL)"\n')
        continue
    if i > 580 and 'step3PlayBtn.text = when {' in line:
        new_lines.append('''            finalizeWakeUpBtn.isEnabled = step1Done
            finalizeWakeUpBtn.background = GradientDrawable().apply {
                setColor(if (step1Done) Color.parseColor("#238636") else Color.parseColor("#21262D"))
                cornerRadius = dp(8).toFloat()
            }
''')
        new_lines.append(line)
        continue

    new_lines.append(line)

assert skip_earbuds_added, "skip_earbuds_added was False"
assert finalize_btn_added, "finalize_btn_added was False"

with open(ctrl_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print("Updated WakeUpVerificationController.kt successfully")
