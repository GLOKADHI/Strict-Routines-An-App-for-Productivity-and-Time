"""Source integration contracts; Android device execution requires a future authorized build."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'android/app/src/main/java/com/strictrecords/app'
ENGINE=(ROOT/'StrictRecordsLocalEngine.kt').read_text(encoding='utf-8')
UI=(ROOT/'MainActivity.kt').read_text(encoding='utf-8')


def section(start,end):return ENGINE.split(start,1)[1].split(end,1)[0]


def test_labels_and_screens_use_saved_occurrence():
    assert 'Bath session starts at' not in UI and 'Bath session in progress' not in UI
    assert 'Overlapping hourly attendance requirements are automatically excused' not in UI
    assert UI.count('db.getSavedBathSchedule(activity)')==3
    assert UI.count('db.getBathScheduleForDisplay(')==1
    assert UI.count('Assigned attendance slot')>=5


def test_reader_does_not_recalculate_or_delete_saved_bath_history():
    reader=section('    fun getDailyActivities(','    fun isWakeUpWindowOpen(')
    assert 'BathWindowRules.calculateBathWindow' not in reader
    assert 'DELETE FROM scheduled_activities' not in reader
    assert reader.index('reconcileHourlyBathConflicts(date)') < reader.index('val c = readableDatabase.rawQuery(')
    assert reader.index('act.status == "EXCUSED"') < reader.index('val pendingOffline =')


def test_exemption_keeps_evidence_score_and_finalized_records():
    resolver=section('    fun reconcileHourlyBathConflicts(','    private val isReconcilingGym')
    assert 'if (date < nowDate()) return' in resolver
    assert 'canExcuseHourly(currentStatus,c.getString(6),c.getInt(7))' in resolver
    assert 'it.overlapWithHourly(hStartMs,hEndMs)' in resolver
    for key in ('bath_activity_id','bath_reservation_id','bath_hourly_activity_id','bath_slot_target_ms'):
        assert 'details.put("'+key+'"' in resolver
    assert 'DELETE ' not in resolver
    for unexpected in ('addScoreEvent(', 'createDeductionProposal(', 'recordActivityVerification(', 'put("submitted_at"'):
        assert unexpected not in resolver
    assert 'store.setTransactionSuccessful()' in resolver and 'store.endTransaction()' in resolver


def test_submission_and_sync_obey_the_same_saved_exemption():
    submission=section('    fun evaluateSubmissionTiming(','    fun recordActivityVerification(')
    assert submission.index('reconcileHourlyBathConflicts(activityDate)') < submission.index('val c = readableDatabase.rawQuery(')
    assert 'details.optBoolean("suppressed_by_bath",false)' in submission
    merge=section('    private fun mergeAuthoritativeScheduleActivities(','    fun recordActivityJournalEvent(')
    assert 'BathScheduleRecord.preserveAssignmentOnSync' in merge
    assert merge.index('if (preserveBathAssignment) continue') < merge.index('db.insertWithOnConflict(')


def test_notifications_reuse_proof_record_not_activity_start():
    reminders=section('    fun getPendingNotifications(','    fun ')
    assert 'getSavedBathSchedule(act)' in reminders
    for event in ('OPENING','OPENED','WARN1M','CLOSED'):
        assert 'savedBath?.reminderMessage("'+event+'")' in reminders


def test_unchanged_authoritative_backend_has_identical_custom_mapping():
    from core.daily_college_decision import DailyCollegeDecision
    for planned,target in {'12:10':'13:00','12:20':'13:00','12:40':'13:00','14:10':'15:00','14:22':'15:00'}.items():
        assert DailyCollegeDecision.reserved_hourly_target(planned)==target


def test_backend_reserves_only_assigned_occurrence_without_points_or_evidence(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import uuid,json
    from core.database import StrictRecordsDB
    from core.daily_college_decision import DailyCollegeDecision
    db=StrictRecordsDB(str(tmp_path/'bath-authority.sqlite'))
    current=datetime(2026,10,21,8,0,tzinfo=ZoneInfo('Asia/Kolkata'))
    DailyCollegeDecision(db).save('user_lok','2026-10-21',False,0,str(uuid.uuid4()),
        bath_plans=[dict(occurrence=1,planned_time='14:10',window_start='13:00',window_end='17:30')],
        actor='fixture',device_id='fixture',current=current)
    with db._connect() as conn:
        for hour in (13,14,15,16):
            target=datetime(2026,10,21,hour,0,tzinfo=ZoneInfo('Asia/Kolkata'))
            from datetime import timedelta
            iso=target.isoformat()
            start=(target-timedelta(minutes=10)).isoformat()
            end=(target+timedelta(minutes=10)).isoformat()
            conn.execute('''INSERT INTO scheduled_activities(activity_id,account_id,type,date,title,scheduled_time,
                window_start,window_end,notification_time,status,details_json,points)
                VALUES(?,'user_lok','HOURLY','2026-10-21','Hourly check',?,?,?,?,'SCHEDULED','{}',0)''',
                (f'ACT-20261021-{hour:02d}00-HOURLY',iso,start,end,start))
        count=conn.execute('SELECT COUNT(*) FROM score_events').fetchone()[0]
        for _ in range(3):DailyCollegeDecision.reconcile_bath_reservations(conn,'user_lok','2026-10-21',current)
        rows=conn.execute("SELECT * FROM scheduled_activities WHERE type='HOURLY'").fetchall()
        assert [r['activity_id'] for r in rows if r['status']=='EXCUSED']==['ACT-20261021-1500-HOURLY']
        assert conn.execute('SELECT COUNT(*) FROM score_events').fetchone()[0]==count
        assert all(not r['submitted_at'] and not json.loads(r['details_json']).get('evidence_id') for r in rows)


def test_sync_distinguishes_planning_range_from_proof_window():
    client=(ROOT/'GymPolicyClient.kt').read_text(encoding='utf-8')
    queue=client.split('    fun queueCollegeChoice(',1)[1].split('    private fun acceptCollege(',1)[0]
    assert '.put("window_start",window.windowStartHhmm)' in queue
    assert '.put("window_end",window.windowEndHhmm)' in queue
    assert 'window.proofWindowStartIso' not in queue
    merge=section('    private fun mergeAuthoritativeScheduleActivities(','    fun recordActivityJournalEvent(')
    assert 'item = BathScheduleRecord.normalizeIncoming(item,scheduleTimezone(activityDate))' in merge
    assert 'localStatus in BathScheduleRecord.protectedStatuses' in merge
