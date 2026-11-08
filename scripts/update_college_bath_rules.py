import sys

path = 'android/app/src/main/java/com/strictrecords/app/BathWindowRules.kt'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

target = """        // Planned bath activity interval: planned start to planned start + 1 hour
        val reservedStartMs = plannedMs
        val reservedEndMs = plannedMs + ProductPolicy.BATH_RESERVATION_MINUTES * 60_000L

        // Dedicated 20-minute photo-submission window opening 90 minutes after planned start:
        // proofWindowStart = plannedBathStart + 90 minutes
        // proofWindowEnd = proofWindowStart + 20 minutes
        val proofWindowStartMs = plannedMs + 90 * 60 * 1000L
        val proofWindowEndMs = proofWindowStartMs + 20 * 60 * 1000L
        val reminderMs = proofWindowStartMs - 60 * 1000L"""

replacement = """        // Fixed college-day schedule: Bath 1 activity is 7:30 AM–8:00 AM; proof window is 8:00 AM–8:30 AM
        val reservedStartMs = plannedMs
        val reservedEndMs = if (isCollege && occurrence == 1) {
            plannedMs + 30 * 60_000L
        } else {
            plannedMs + ProductPolicy.BATH_RESERVATION_MINUTES * 60_000L
        }

        val proofWindowStartMs = if (isCollege && occurrence == 1) {
            calForTime("08:00").timeInMillis
        } else {
            plannedMs + 90 * 60 * 1000L
        }
        val proofWindowEndMs = if (isCollege && occurrence == 1) {
            calForTime("08:30").timeInMillis
        } else {
            proofWindowStartMs + 20 * 60 * 1000L
        }
        val reminderMs = proofWindowStartMs - 60 * 1000L"""

if target in content:
    content = content.replace(target, replacement)
    print("Replaced target in BathWindowRules.kt")
else:
    print("WARNING: target not found in BathWindowRules.kt")

target_valid = """    fun isValidPlannedBathTime(plannedHhmm: String, windowStartHhmm: String, windowEndHhmm: String): Boolean {
        val p = timeToMinutes(plannedHhmm)
        val s = timeToMinutes(windowStartHhmm)
        val e = timeToMinutes(windowEndHhmm)
        return p >= s && (p + 60) <= e
    }"""

replacement_valid = """    fun isValidPlannedBathTime(plannedHhmm: String, windowStartHhmm: String, windowEndHhmm: String): Boolean {
        val p = timeToMinutes(plannedHhmm)
        val s = timeToMinutes(windowStartHhmm)
        val e = timeToMinutes(windowEndHhmm)
        if (s == timeToMinutes("07:30") && e == timeToMinutes("08:30") && p == timeToMinutes("07:30")) {
            return true
        }
        return p >= s && (p + 60) <= e
    }"""

if target_valid in content:
    content = content.replace(target_valid, replacement_valid)
    print("Replaced target_valid in BathWindowRules.kt")
else:
    print("WARNING: target_valid not found in BathWindowRules.kt")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Saved BathWindowRules.kt")
