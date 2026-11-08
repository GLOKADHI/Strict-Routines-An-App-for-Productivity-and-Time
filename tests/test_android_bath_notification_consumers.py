"""Guard Android delivery/UI consumers against obsolete Bath assignments."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "android/app/src/main/java/com/strictrecords/app"


def test_bath_proof_alarms_use_current_window_and_shared_dedup_keys():
    alarm = (ROOT / "WakeUpAlarmReceiver.kt").read_text(encoding="utf-8")
    schedule = alarm.split("        fun scheduleHourlyWindows(", 1)[1].split("        private fun scheduleAtTime(", 1)[0]
    assert '"HOURLY", "WAKE_UP", "BATH", "GYM", "TRAVEL"' in schedule
    assert 'putExtra("WINDOW_START_MS", start)' in schedule
    assert 'putExtra("WINDOW_END_MS", end)' in schedule
    assert 'val key = db.notificationKey(act.activityId,suffix,act.windowStart,act.windowEnd,act.type)' in schedule
    receiver = alarm.split("        when (action)", 1)[0]
    assert 'AlarmScheduleRules.matchesCurrentWindow(' in receiver
    assert '"EXCUSED", "CANCELLED"' in receiver
    for event in ("OPENING", "OPENED", "WARN1M", "CLOSED"):
        assert f'.reminderMessage("{event}")' in alarm


def test_queued_server_hourly_notifications_obey_current_exemption():
    service = (ROOT / "StrictEnforcementService.kt").read_text(encoding="utf-8")
    consumer = service.split("    private fun postHourlyWindowNotification(", 1)[1].split("    private fun isConnectedToWifi(", 1)[0]
    assert consumer.index('currentDetails.optBoolean("suppressed_by_bath", false)') < consumer.index("nm.notify(")
    assert consumer.index("AlarmScheduleRules.matchesCurrentWindow(") < consumer.index("db.markNotificationSent(")


def test_tasks_keep_excused_points_separate_and_draft_preview_is_fresh():
    ui = (ROOT / "MainActivity.kt").read_text(encoding="utf-8")
    assert '"Excused — Scheduled Bath Activity"' in ui
    points = ui.split("                val pointsLabel = when {", 1)[1].split("                val isCardActiveOpen", 1)[0]
    assert points.index('state == "EXCUSED" -> "0 PTS"') < points.index('activity.configuredPoints > 0 -> "TARGET')
    assert 'val b1Rec = BathScheduleRecord.fromWindow(b1Win, db.scheduleTimezone())' in ui
    assert 'val b2Rec = BathScheduleRecord.fromWindow(b2Calculated, db.scheduleTimezone())' in ui
    assert 'heldHourlySlotDisplay} (Excused)' not in ui
    assert 'Mandatory photo proof · Taufa AI validation required' in ui
