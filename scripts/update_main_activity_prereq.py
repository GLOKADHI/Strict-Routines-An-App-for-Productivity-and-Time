import sys

main_path = 'android/app/src/main/java/com/strictrecords/app/MainActivity.kt'
with open(main_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add showWakeUpPrerequisiteDialog before showActivityProofDialog
target_proof_dialog = '    private fun showActivityProofDialog(type: String) {'
prereq_dialog = '''    private fun showWakeUpPrerequisiteDialog() {
        if (isFinishing || isDestroyed) return
        AlertDialog.Builder(this)
            .setTitle("Complete Wake-Up Verification first.")
            .setMessage("Today's required wake-up verification is still pending. Complete it before proceeding with this activity.")
            .setPositiveButton("Open Wake-Up Verification") { _, _ ->
                showWakeUpVerificationDialog(isDevMode = false)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun showActivityProofDialog(type: String) {'''

assert target_proof_dialog in content, "target_proof_dialog not found"
content = content.replace(target_proof_dialog, prereq_dialog, 1)

# 2. Check prerequisite in showActivityProofDialog for BATH
target_bath_check = '''        if (type == "BATH") {
            val bathCount = db.getBathSubmissionCount(date)'''
replacement_bath_check = '''        if (type == "BATH") {
            if (!db.isWakeUpVerificationCompleted(date)) {
                showWakeUpPrerequisiteDialog()
                return
            }
            val bathCount = db.getBathSubmissionCount(date)'''

assert target_bath_check in content, "target_bath_check not found"
content = content.replace(target_bath_check, replacement_bath_check, 1)

# 3. Check prerequisite in showHourlySubmissionDialog
target_hourly_check = '''        if (act == null) {
            Toast.makeText(this, "No scheduled occurrence is available.", Toast.LENGTH_LONG).show()
            return
        }'''

replacement_hourly_check = '''        val isDependentTask = act == null || act.type in listOf("HOURLY", "BATH") || act.activityId.contains("HOURLY") || act.activityId.contains("BATH")
        if (isDependentTask && !db.isWakeUpVerificationCompleted(date)) {
            showWakeUpPrerequisiteDialog()
            return
        }
        if (act == null) {
            Toast.makeText(this, "No scheduled occurrence is available.", Toast.LENGTH_LONG).show()
            return
        }'''

assert target_hourly_check in content, "target_hourly_check not found"
content = content.replace(target_hourly_check, replacement_hourly_check, 1)

# 4. In onResume, check if hourly submission dialog is open while wake-up is pending
target_resume = 'screenResumed = true'
replacement_resume = '''screenResumed = true
        if (activeHourlyDialog?.isShowing == true && !db.isWakeUpVerificationCompleted(db.nowDate())) {
            activeHourlyDialog?.dismiss()
            activeHourlyDialog = null
            showWakeUpPrerequisiteDialog()
        }'''

assert target_resume in content, "target_resume not found"
content = content.replace(target_resume, replacement_resume, 1)

with open(main_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated MainActivity.kt with Wake-Up prerequisite dialog and checks")
