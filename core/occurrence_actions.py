"""Atomic negative transactions for one identified scheduled occurrence."""
import hashlib
import json


def skip_gym(engine, date, device_id, notes, timestamp, is_dev_mode=False, activity_id=None, account_id='user_lok'):
    db = engine.db
    if activity_id:
        with db._connect() as check:
            owned = check.execute("SELECT 1 FROM scheduled_activities WHERE activity_id=? AND account_id=? AND date=? AND type='GYM'", (activity_id, account_id, date)).fetchone()
        if not owned: return {'accepted': False, 'code': 'OCCURRENCE_NOT_FOUND', 'points': 0}
    if not db.get_daily_record(date, user_id=account_id, is_dev_mode=is_dev_mode):
        db.ensure_daily_record(date, user_id=account_id, is_dev_mode=is_dev_mode)
    settings = db.get_settings(account_id)
    dev = int(is_dev_mode)
    with db._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if activity_id:
            activity = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=? AND account_id=? AND date=? AND type='GYM'", (activity_id, account_id, date)).fetchone()
            if not activity:
                return {'accepted': False, 'code': 'OCCURRENCE_NOT_FOUND', 'points': 0}
        else:
            activity = conn.execute("SELECT * FROM scheduled_activities WHERE account_id=? AND date=? AND type='GYM' AND is_enabled=1 ORDER BY scheduled_time LIMIT 1", (account_id, date)).fetchone()
        occurrence = activity['activity_id'] if activity else f'task_{date}_{dev}_GYM'
        old = conn.execute("SELECT * FROM score_events WHERE date=? AND is_dev_mode=? AND event_type='GYM_SKIPPED' AND related_task_id=?", (date, dev, occurrence)).fetchone()
        # Preserve a prior day-only transaction for the default legacy occurrence.
        if not old and not activity_id:
            old = conn.execute("SELECT * FROM score_events WHERE date=? AND is_dev_mode=? AND event_type='GYM_SKIPPED' AND related_task_id=?", (date, dev, f'task_{date}_{dev}_GYM')).fetchone()
        if old:
            result = {'accepted': True, 'deduplicated': True, 'status': 'SKIPPED', 'activity_id': occurrence, 'points': old['points'], 'score_event_id': old['event_id']}
        else:
            if activity and (not activity['is_enabled'] or activity['status'] in {'VERIFIED','COMPLETED','SUBMITTED','VALIDATING','AI_PROVISIONAL','CANCELLED','EXCUSED','MISSED','CLOSED'}):
                return {'accepted': False, 'code': 'OCCURRENCE_FINAL', 'points': 0, 'activity_id': occurrence}
            penalty = int(settings.gym_missed_penalty)
            event_id = 'se_occ_' + hashlib.sha256(f'{account_id}:{occurrence}:{dev}:GYM_SKIPPED'.encode()).hexdigest()[:32]
            audit = {'activity_id': occurrence, 'account_id': account_id, 'action': 'SKIPPED', 'configured_penalty': penalty, 'source_device_id': device_id}
            conn.execute("INSERT INTO score_events(event_id,date,is_dev_mode,timestamp,event_type,device_id,description,points,rule_version,related_task_id,audit_json) VALUES (?,?,?,?,'GYM_SKIPPED',?,'Gym occurrence skipped',?,?,?,?)", (event_id,date,dev,timestamp,device_id,penalty,db.get_current_rule_version(),occurrence,json.dumps(audit)))
            result = {'accepted': True, 'deduplicated': False, 'status': 'SKIPPED', 'activity_id': occurrence, 'points': penalty, 'score_event_id': event_id}
        if activity:
            conn.execute("UPDATE scheduled_activities SET status='SKIPPED',attendance_status='SKIPPED',points=0,closed_at=COALESCE(closed_at,?) WHERE activity_id=?", (timestamp, occurrence))
        conn.execute("UPDATE gym_sessions SET status='SKIPPED',notes=?,device_id=? WHERE date=? AND is_dev_mode=?", (notes,device_id,date,dev))
        conn.execute("UPDATE daily_records SET gym_status='SKIPPED' WHERE date=? AND is_dev_mode=?", (date,dev))
        conn.execute("UPDATE scheduled_tasks SET state='SKIPPED',points_awarded=? WHERE date=? AND is_dev_mode=? AND task_code='GYM'", (result['points'],date,dev))
    # Recompute even on retry: recover a timeout/crash after the atomic commit.
    result['score'] = engine.recalculate_daily_score(date, user_id=account_id, is_dev_mode=is_dev_mode).get('final_score', 0)
    return result


def miss_bath(engine, date, device_id, timestamp, is_dev_mode=False, activity_id=None, account_id='user_lok'):
    from core.time_window_engine import iso_to_dt
    db = engine.db
    settings = db.get_settings(account_id)
    dev = int(is_dev_mode)
    with db._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if activity_id:
            activity = conn.execute("SELECT * FROM scheduled_activities WHERE activity_id=? AND account_id=? AND date=? AND type='BATH'", (activity_id,account_id,date)).fetchone()
            if not activity: return {'accepted':False,'code':'OCCURRENCE_NOT_FOUND','points':0}
        else:
            activity = conn.execute("SELECT * FROM scheduled_activities WHERE account_id=? AND date=? AND type='BATH' AND is_enabled=1 ORDER BY scheduled_time LIMIT 1", (account_id,date)).fetchone()
        occurrence = activity['activity_id'] if activity else f'task_{date}_{dev}_BATH'
        old = conn.execute("SELECT * FROM score_events WHERE date=? AND is_dev_mode=? AND event_type='BATH_MISSED' AND related_task_id=?", (date,dev,occurrence)).fetchone()
        if not old and not activity_id:
            old = conn.execute("SELECT * FROM score_events WHERE date=? AND is_dev_mode=? AND event_type='BATH_MISSED' AND related_task_id=?", (date,dev,f'task_{date}_{dev}_BATH')).fetchone()
        if old:
            result={'accepted':True,'deduplicated':True,'activity_id':occurrence,'status':'MISSED','points':old['points'],'score_event_id':old['event_id']}
        else:
            if activity and (activity['status'] in {'VERIFIED','COMPLETED','SUBMITTED','VALIDATING','AI_PROVISIONAL','REJECTED','CANCELLED','EXCUSED'} or activity['submitted_at'] or not activity['is_enabled']):
                return {'accepted':False,'code':'OCCURRENCE_FINAL','points':0}
            closing = activity['window_end'] if activity else f'{date}T{settings.bath_deadline}:00+05:30'
            if iso_to_dt(timestamp) < iso_to_dt(closing): return {'accepted':False,'code':'WINDOW_NOT_CLOSED','points':0}
            penalty = 0  # Bath is an exempt scheduled activity; 0 penalty points
            identity = 'se_occ_' + hashlib.sha256(f'{account_id}:{occurrence}:{dev}:BATH_MISSED'.encode()).hexdigest()[:32]
            audit={'activity_id':occurrence,'account_id':account_id,'configured_penalty':penalty,'action':'MISSED','source_device_id':device_id}
            conn.execute("INSERT INTO score_events(event_id,date,is_dev_mode,timestamp,event_type,device_id,description,points,rule_version,related_task_id,audit_json) VALUES (?,?,?,?,'BATH_MISSED',?,'Bath occurrence missed',?,?,?,?)",(identity,date,dev,timestamp,device_id,penalty,db.get_current_rule_version(),occurrence,json.dumps(audit)))
            result={'accepted':True,'deduplicated':False,'activity_id':occurrence,'status':'MISSED','points':penalty,'score_event_id':identity}
        if activity: conn.execute("UPDATE scheduled_activities SET status='MISSED',attendance_status='ABSENT',points=0 WHERE activity_id=?",(occurrence,))
        conn.execute("UPDATE bath_records SET status='MISSED' WHERE date=? AND is_dev_mode=?",(date,dev))
        conn.execute("UPDATE daily_records SET bath_status='MISSED' WHERE date=? AND is_dev_mode=?",(date,dev))
        conn.execute("UPDATE scheduled_tasks SET state='MISSED',points_awarded=? WHERE date=? AND is_dev_mode=? AND task_code='BATH'",(result['points'],date,dev))
    result['score'] = engine.recalculate_daily_score(date,user_id=account_id,is_dev_mode=is_dev_mode).get('final_score',0)
    return result
