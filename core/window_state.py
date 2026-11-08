"""Wall-clock window state stays separate from submission eligibility and credit."""
WAKEUP_WINDOW_START = "07:00"
WAKEUP_WINDOW_END = "09:30"
PRESERVED = {"SUBMITTED", "VALIDATING", "AI_PROVISIONAL", "VERIFIED", "COMPLETED", "REJECTED", "REJECTED_LATE", "MISSED", "CLOSED", "EXPIRED", "PENDING_SYNC", "SKIPPED", "EXCUSED", "CANCELLED"}

def window_state(saved, now, opening, closing, eligible=True):
    if saved in PRESERVED:
        return saved
    if closing <= opening:
        return "INVALID_WINDOW"
    if now >= closing:
        return "MISSED" if eligible else "EXPIRED"
    return "OPEN" if now >= opening else "UPCOMING"
