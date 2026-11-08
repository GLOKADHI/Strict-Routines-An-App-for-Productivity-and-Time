import sys

path = 'android/app/src/main/java/com/strictrecords/app/ProofEvaluationManager.kt'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target = '''        val timing = db.evaluateSubmissionTiming(actId, db.authoritativeNowMs(), recordLocally = false)'''

replacement = '''        // Prerequisite: Wake-Up Verification must be completed before Bath or Hourly attendance submissions
        val isDependentTask = actType in listOf("HOURLY", "BATH") || actId.contains("HOURLY") || actId.contains("BATH")
        if (isDependentTask && !db.isWakeUpVerificationCompleted(date)) {
            return ProofSubmissionRules.SubmissionReceipt(
                success = false,
                message = "Complete Wake-Up Verification first. Today's required wake-up verification is still pending. Complete it before proceeding with this activity.",
                errorCode = "WAKEUP_VERIFICATION_REQUIRED"
            )
        }

        val timing = db.evaluateSubmissionTiming(actId, db.authoritativeNowMs(), recordLocally = false)'''

assert target in content, "target not found in ProofEvaluationManager.kt"
content = content.replace(target, replacement, 1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated ProofEvaluationManager.kt with Wake-Up prerequisite check")
