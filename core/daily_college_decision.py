"""Synchronize the existing college/Bath plan without coupling it to Gym attendance."""
from datetime import date as calendar_date, datetime, timedelta
import hashlib
import json
import logging
import sqlite3
from zoneinfo import ZoneInfo

from core.gym_daily_decision import GymDecisionError,planning_connection
from core.time_window_engine import TimeWindowEngine, now_ist
from core.product_policy import BATH

logger = logging.getLogger("DailyCollegeDecision")
ATTENDANCE_POLICY = 'BATH_ACTIVITY_WINDOW_OVERLAP_V2'
MUTABLE_STATUSES = ('SCHEDULED', 'UPCOMING', 'NOTIFIED', 'OPEN', 'EXCUSED')


class DailyCollegeDecision:
    def __init__(self,db):self.db=db

    @staticmethod
    def ensure_schema(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS daily_college_decisions(
            account_id TEXT NOT NULL,date TEXT NOT NULL,attends_college INTEGER NOT NULL,revision INTEGER NOT NULL,
            bath_plans_json TEXT NOT NULL,timezone TEXT NOT NULL,updated_at TEXT NOT NULL,
            actor TEXT NOT NULL,device_id TEXT NOT NULL,PRIMARY KEY(account_id,date))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS college_decision_requests(account_id TEXT NOT NULL,request_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,result_json TEXT NOT NULL,PRIMARY KEY(account_id,request_id))''')

    @staticmethod
    def read(conn,account,date):
        row=conn.execute('SELECT * FROM daily_college_decisions WHERE account_id=? AND date=?',(account,date)).fetchone()
        if not row:
            from core.gym_daily_decision import DailyGymDecision
            zone=DailyGymDecision.read(conn,account,date)['timezone']
            return dict(account_id=account,date=date,timezone=zone,attends_college=None,revision=0,bath_plans=[],sync_status='DECISION_REQUIRED',policy=BATH)
        result=dict(row)
        result['attends_college']=bool(result['attends_college'])
        result['bath_plans']=json.loads(result.pop('bath_plans_json'))
        result['sync_status']='CONFIRMED'
        result['policy']=BATH
        return result

    def get(self,account,date):
        calendar_date.fromisoformat(date)
        with self.db._connect() as conn:
            self.ensure_schema(conn)
            return self.read(conn,account,date)

    def save(self,account,date,attends,expected_revision,request_id,*,bath_plans=None,timezone='Asia/Kolkata',actor,device_id,current=None,connection=None):
        if type(attends) is not bool or type(expected_revision) is not int or expected_revision<0:
            raise GymDecisionError('INVALID_DECISION','Choose Yes or No and provide the saved college-plan revision')
        try:
            day=calendar_date.fromisoformat(date)
            zone=ZoneInfo(timezone)
        except (ValueError,TypeError,KeyError):raise GymDecisionError('INVALID_DATE','Select a valid dated college plan')
        current=(current or now_ist()).astimezone(zone)
        if day<current.date():raise GymDecisionError('HISTORICAL_DATE','Past college plans and proof history cannot be rewritten')
        if not isinstance(request_id,str) or not 8<=len(request_id)<=128:raise GymDecisionError('REQUEST_ID_REQUIRED','A stable planning request ID is required')
        plans=bath_plans if bath_plans is not None else BATH['college'] if attends else []
        if not isinstance(plans,list) or (attends and not 1<=len(plans)<=2) or (not attends and not 0<=len(plans)<=2):
            raise GymDecisionError('INVALID_BATH_PLAN','College days support 1 or 2 Bath plans; non-college days support up to 2 explicitly configured plans')
        normalized=[]
        parsed_intervals=[]
        for occurrence,plan in enumerate(plans,1):
            if not isinstance(plan,dict):raise GymDecisionError('INVALID_BATH_PLAN','Invalid Bath plan')
            values={name:TimeWindowEngine.parse_time_24h(plan.get(name)) for name in ('planned_time','window_start','window_end')}
            if any(not value[0] for value in values.values()):raise GymDecisionError('INVALID_BATH_PLAN','Choose valid Bath times')
            duration=plan.get('activity_duration_minutes',30 if attends and occurrence == 1 else BATH['reservation_minutes'])
            if type(duration) is not int or not 1 <= duration <= 180:
                raise GymDecisionError('INVALID_BATH_PLAN','Bath duration must be a whole number of minutes between 1 and 180')
            if attends and occurrence == 1:
                if duration != 30 or values['planned_time'][1] != '07:30' or values['window_end'][1] != '08:30' or values['window_start'][1] not in ('07:30', '08:00'):
                    raise GymDecisionError('INVALID_BATH_PLAN', 'College-day Bath Session 1 is fixed at 07:30–08:00 with proof window 08:00–08:30')
                act_start_min = values['planned_time'][2]
                act_end_min = act_start_min + 30
            else:
                dur = duration
                if not values['window_start'][2]<=values['planned_time'][2] or values['planned_time'][2]+dur>values['window_end'][2]:
                    raise GymDecisionError('INVALID_BATH_PLAN','The planned Bath time must be inside its submission window')
                act_start_min = values['planned_time'][2]
                act_end_min = act_start_min + dur
            for prev_start, prev_end in parsed_intervals:
                if max(prev_start, act_start_min) < min(prev_end, act_end_min):
                    raise GymDecisionError('INVALID_BATH_PLAN', 'Bath Session 1 and Bath Session 2 activity windows must not overlap')
            parsed_intervals.append((act_start_min, act_end_min))
            normalized.append(dict(occurrence=occurrence,activity_duration_minutes=duration,
                                   **{name:value[1] for name,value in values.items()}))
        request_hash=hashlib.sha256(json.dumps([account,date,attends,expected_revision,normalized,timezone],sort_keys=True).encode()).hexdigest()
        with planning_connection(self.db,connection) as conn:
            self.ensure_schema(conn)
            if connection is None:conn.execute('BEGIN IMMEDIATE')
            prior=conn.execute('SELECT * FROM college_decision_requests WHERE account_id=? AND request_id=?',(account,request_id)).fetchone()
            if prior:
                if prior['request_sha256']!=request_hash:raise GymDecisionError('REQUEST_CONFLICT','Request ID already used for a different college plan',409)
                return json.loads(prior['result_json'])
            old=self.read(conn,account,date)
            if old.get('timezone')!=timezone:raise GymDecisionError('TIMEZONE_CONFLICT','Refresh the saved schedule timezone before changing this date',409)
            if old['revision']!=expected_revision:raise GymDecisionError('REVISION_CONFLICT','A newer college plan exists. Refresh before changing it.',409)
            conn.execute('''INSERT INTO daily_college_decisions(account_id,date,attends_college,revision,bath_plans_json,timezone,updated_at,actor,device_id)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,date) DO UPDATE SET attends_college=excluded.attends_college,revision=excluded.revision,
                bath_plans_json=excluded.bath_plans_json,updated_at=excluded.updated_at,actor=excluded.actor,device_id=excluded.device_id''',
                (account,date,int(attends),expected_revision+1,json.dumps(normalized),timezone,current.isoformat(),actor,device_id))
            # Preserve submitted/finalized occurrences. Retire only unstarted rows;
            # their old schedule and evidence remain available in the same tables.
            conn.execute("UPDATE scheduled_activities SET status='CANCELLED',is_enabled=0 WHERE account_id=? AND date=? AND type='BATH' AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN')",(account,date))
            record=self.read(conn,account,date)
            for item in self.activities(record):
                old_activity=conn.execute('SELECT status FROM scheduled_activities WHERE activity_id=?',(item['activity_id'],)).fetchone()
                if old_activity and old_activity['status'] not in ('CANCELLED','SCHEDULED','UPCOMING','NOTIFIED','OPEN'):continue
                conn.execute('''INSERT INTO scheduled_activities(activity_id,account_id,type,title,date,scheduled_time,window_start,window_end,
                    notification_time,closing_warning_time,window_duration_minutes,status,points,is_enabled,proof_requirements,validation_rule,details_json)
                    VALUES(?,?,'BATH',?,?,?,?,?,?,?,?,'SCHEDULED',?,1,?,?,?)
                    ON CONFLICT(activity_id) DO UPDATE SET status='SCHEDULED',is_enabled=1,title=excluded.title,scheduled_time=excluded.scheduled_time,
                    window_start=excluded.window_start,window_end=excluded.window_end,notification_time=excluded.notification_time,
                    closing_warning_time=excluded.closing_warning_time,window_duration_minutes=excluded.window_duration_minutes,
                    points=excluded.points,proof_requirements=excluded.proof_requirements,validation_rule=excluded.validation_rule,
                    details_json=excluded.details_json''',
                    (item['activity_id'],account,item['title'],date,item['scheduled_time'],item['window_start'],item['window_end'],item['notification_time'],item['closing_warning_time'],item['window_duration_minutes'],item['points'],item['proof_requirements'],item['validation_rule'],item['details_json']))
            existing_hourly=conn.execute("SELECT count(*) FROM scheduled_activities WHERE account_id=? AND date=? AND type='HOURLY'",(account,date)).fetchone()[0]
            if existing_hourly>0:
                if attends:
                    hourly_items=TimeWindowEngine.generate_college_day_hourly_activities(date,account_id=account,timezone_name=timezone)
                else:
                    sched_row=conn.execute("SELECT wake_up_time FROM routine_schedules WHERE account_id=? AND schedule_date=? AND status IN ('SCHEDULED','LOCKED') ORDER BY schedule_version DESC LIMIT 1",(account,date)).fetchone()
                    wake_target=(sched_row['wake_up_time'] if sched_row and sched_row['wake_up_time'] else '09:00')
                    hourly_items=[i for i in TimeWindowEngine.generate_daily_schedule(date,account_id=account,wakeup_time=wake_target,timezone_name=timezone) if i['type']=='HOURLY']
                TimeWindowEngine._retire_changed_hourly_plan(conn,hourly_items,date,account,current)
                for h_item in hourly_items:
                    conn.execute('''INSERT OR IGNORE INTO scheduled_activities(activity_id,account_id,type,title,date,scheduled_time,
                        window_start,window_end,window_duration_minutes,notification_time,closing_warning_time,status,submitted_at,
                        points,penalty_points,proof_requirements,validation_rule,is_enabled,required_by_admin,schedule_id,schedule_version,details_json)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (h_item['activity_id'],h_item['account_id'],h_item['type'],h_item.get('title',''),h_item['date'],
                         h_item['scheduled_time'],h_item['window_start'],h_item['window_end'],h_item.get('window_duration_minutes',20),
                         h_item['notification_time'],h_item.get('closing_warning_time',''),h_item['status'],h_item['submitted_at'],
                         h_item['points'],h_item.get('penalty_points',0),h_item.get('proof_requirements','ACTIVITY_LOG_AND_PHOTO'),
                         h_item['validation_rule'],h_item.get('is_enabled',1),h_item.get('required_by_admin',1),
                         h_item.get('schedule_id',''),h_item.get('schedule_version',1),h_item['details_json']))
            self.reconcile_bath_reservations(conn,account,date,current)
            result=dict(success=True,college_decision=record)
            conn.execute('INSERT INTO college_decision_requests VALUES(?,?,?,?)',(account,request_id,request_hash,json.dumps(result)))
            return result

    @staticmethod
    def reserved_hourly_target(planned):
        """End of the actual activity's containing hour, never the preceding slot."""
        hour,_=map(int,planned.split(':'))
        return f'{(hour+1)%24:02d}:00'

    @staticmethod
    def _has_evidence(row,detail):
        return bool(row['submitted_at'] or row['submission_id'] or
                    row['evidence_ids_json'] not in (None,'','[]') or
                    detail.get('submission_id') or detail.get('evidence_id') or detail.get('evidence_ids'))

    @staticmethod
    def _dismiss_notifications(conn,activity_id):
        # Retain deduplication history; only retire an obsolete visible reminder.
        conn.execute("UPDATE notification_center SET is_dismissed=1 WHERE activity_id=? AND is_dismissed=0",(activity_id,))

    @staticmethod
    def reconcile_bath_reservations(conn,account,date,current):
        record=DailyCollegeDecision.read(conn,account,date)
        current=current.astimezone(ZoneInfo(record.get('timezone','Asia/Kolkata')))
        if record['attends_college'] is None or calendar_date.fromisoformat(date)<current.date():return

        # Optimization & contention guard: Check if reconciliation work is actually required
        has_bath_plans = bool(record.get('bath_plans'))
        hourly_rows = conn.execute("SELECT * FROM scheduled_activities WHERE account_id=? AND date=? AND type='HOURLY' AND is_enabled=1",(account,date)).fetchall()
        has_suppressed = any(
            (json.loads(r['details_json'] or '{}').get('suppressed_by_bath') or 'bath' in (json.loads(r['details_json'] or '{}').get('suppression_reason') or '').lower())
            for r in hourly_rows
        )
        has_disabled_bath_notif = conn.execute(
            "SELECT 1 FROM notification_center nc JOIN scheduled_activities sa ON nc.activity_id=sa.activity_id "
            "WHERE sa.account_id=? AND sa.date=? AND sa.type='BATH' AND sa.is_enabled=0 AND nc.is_dismissed=0 LIMIT 1",
            (account,date)
        ).fetchone()

        if not has_bath_plans and not has_suppressed and not has_disabled_bath_notif:
            return

        savepoint_active = False
        try:
            conn.execute('SAVEPOINT bath_interval_reconcile')
            savepoint_active = True
            baths=[]
            for item in DailyCollegeDecision.activities(record):
                persisted=conn.execute('SELECT * FROM scheduled_activities WHERE activity_id=?',(item['activity_id'],)).fetchone()
                if persisted:
                    old_detail=json.loads(persisted['details_json'] or '{}')
                    if not persisted['is_enabled'] or persisted['status']=='CANCELLED':continue
                    if persisted['status'] in MUTABLE_STATUSES and not DailyCollegeDecision._has_evidence(persisted,old_detail):
                        # Historical proof records never acquire a new deadline.
                        if current<datetime.fromisoformat(persisted['window_end']):
                            changed=any(persisted[key]!=item[key] for key in ('window_start','window_end','notification_time'))
                            conn.execute('''UPDATE scheduled_activities SET window_start=?,window_end=?,notification_time=?,
                                closing_warning_time=?,window_duration_minutes=?,details_json=? WHERE activity_id=?''',
                                (item['window_start'],item['window_end'],item['notification_time'],item['closing_warning_time'],
                                 item['window_duration_minutes'],item['details_json'],item['activity_id']))
                            if changed:DailyCollegeDecision._dismiss_notifications(conn,item['activity_id'])
                    else:
                        # A submitted/finalized bath retains its actual saved interval.
                        if old_detail.get('reserved_start') and old_detail.get('reserved_end'):
                            details=json.loads(item['details_json'])
                            details.update(reserved_start=old_detail['reserved_start'],reserved_end=old_detail['reserved_end'])
                            item=dict(item,details_json=json.dumps(details))
                details=json.loads(item['details_json'])
                baths.append((item,details,datetime.fromisoformat(details['reserved_start']),datetime.fromisoformat(details['reserved_end'])))
            for row in hourly_rows:
                detail=json.loads(row['details_json'] or '{}')
                if (row['status'] not in MUTABLE_STATUSES and row['status'] != 'MISSED') or DailyCollegeDecision._has_evidence(row,detail):continue
                start,end=map(datetime.fromisoformat,(row['window_start'],row['window_end']))
                if end<=start:continue
                matches=[(item,details,act_start,act_end) for item,details,act_start,act_end in baths if start<act_end and act_start<end]
                was_suppressed = bool(detail.get('suppressed_by_bath') or 'bath' in (detail.get('suppression_reason') or '').lower() or detail.get('bath_activity_id'))
                if matches:
                    if row['status']=='EXCUSED' and not was_suppressed:continue
                    item,bath_detail,act_start,act_end=matches[0]
                    detail.setdefault('bath_original_points',row['points'])
                    detail.update(suppressed_by_bath=True,
                        suppression_reason='This proof window overlaps your scheduled Bath activity. No submission required.',
                        reconciliation_status='EXCUSED_BY_BATH',
                        attendance_status='EXCUSED_BY_BATH',
                        bath_activity_id=item['activity_id'],bath_activity_ids=[m[0]['activity_id'] for m in matches],
                        bath_reservation_id=bath_detail['bath_reservation_id'],bath_hourly_activity_id=row['activity_id'],
                        bath_slot_target_ms=bath_detail.get('held_hourly_target_ms', 0),bath_slot_display=bath_detail.get('held_hourly_slot_display', ''),
                        bath_activity_start=act_start.isoformat(),bath_activity_end=act_end.isoformat(),
                        bath_activity_interval=bath_detail.get('activity_interval', f"{act_start.strftime('%I:%M %p').lstrip('0')}–{act_end.strftime('%I:%M %p').lstrip('0')}"),
                        bath_overlap_kind='FULL' if act_start<=start and end<=act_end else 'PARTIAL',
                        bath_plan_revision=record['revision'],attendance_reservation_policy=ATTENDANCE_POLICY)
                    conn.execute("UPDATE scheduled_activities SET status='EXCUSED',attendance_status='EXCUSED',points=0,details_json=? WHERE activity_id=?",(json.dumps(detail),row['activity_id']))
                    DailyCollegeDecision._dismiss_notifications(conn,row['activity_id'])
                elif was_suppressed:
                    # Restore only live/future unsubmitted rows proven suppressed
                    # by a previous bath. No ledger, evidence or penalties change.
                    original_points=detail.pop('bath_original_points',8)
                    detail.pop('suppressed_by_bath',None);detail.pop('suppression_reason',None)
                    detail.pop('reconciliation_status',None);detail.pop('attendance_status',None)
                    for key in ('bath_activity_id','bath_activity_ids','bath_reservation_id','bath_hourly_activity_id',
                                'bath_slot_target_ms','bath_slot_display','bath_activity_start','bath_activity_end',
                                'bath_activity_interval',
                                'bath_overlap_kind','bath_plan_revision','attendance_reservation_policy'):
                        detail.pop(key,None)
                    if current < end:
                        notification=datetime.fromisoformat(row['notification_time'])
                        status='OPEN' if current>=start else 'NOTIFIED' if current>=notification else 'SCHEDULED'
                    conn.execute('UPDATE scheduled_activities SET status=?,attendance_status=?,points=?,details_json=? WHERE activity_id=?',
                        (status,'OPEN' if status=='OPEN' else 'UPCOMING',original_points,json.dumps(detail),row['activity_id']))
            if has_disabled_bath_notif:
                conn.execute("UPDATE notification_center SET is_dismissed=1 WHERE activity_id IN (SELECT activity_id FROM scheduled_activities WHERE account_id=? AND date=? AND type='BATH' AND is_enabled=0)",(account,date))
            conn.execute('RELEASE bath_interval_reconcile')
            savepoint_active = False
        except sqlite3.OperationalError as oe:
            if savepoint_active:
                try:
                    conn.execute('ROLLBACK TO bath_interval_reconcile')
                    conn.execute('RELEASE bath_interval_reconcile')
                except Exception:
                    pass
            logger.warning("reconcile_bath_reservations deferred due to database lock: %s", oe)
        except Exception:
            if savepoint_active:
                try:
                    conn.execute('ROLLBACK TO bath_interval_reconcile')
                    conn.execute('RELEASE bath_interval_reconcile')
                except Exception:
                    pass
            raise

    @staticmethod
    def activities(record):
        items=[]
        day=record['date'];zone=ZoneInfo(record.get('timezone','Asia/Kolkata'))
        for plan in record.get('bath_plans',[]):
            at=lambda value:datetime.fromisoformat(day+'T'+value+':00').replace(tzinfo=zone)
            planned=at(plan['planned_time'])
            occurrence=plan['occurrence'];activity_id=f"ACT-{day.replace('-','')}-{plan['planned_time'].replace(':','')}-BATH-{occurrence}"
            duration=int(plan.get('activity_duration_minutes',30 if record.get('attends_college') and occurrence==1 else BATH.get('reservation_minutes', 30)))
            res_start=planned;res_end=planned+timedelta(minutes=duration)
            slot_start=planned.replace(minute=0,second=0,microsecond=0)
            slot_end=slot_start+timedelta(hours=1)
            held_id=f"ACT-{slot_end.strftime('%Y%m%d-%H%M')}-HOURLY"
            clock=lambda value:value.strftime('%I:%M %p').lstrip('0')
            interval_str=f"{clock(res_start)}–{clock(res_end)}"
            details=dict(plan,is_college=record['attends_college'],reserved_start=res_start.isoformat(),
                         reserved_end=res_end.isoformat(),college_plan_revision=record['revision'],
                         activity_duration_minutes=duration,
                         activity_interval=interval_str,
                         proof_window_start=res_start.isoformat(),proof_window_end=res_end.isoformat(),
                         held_hourly_act_id=held_id,held_hourly_target_ms=int(slot_end.timestamp()*1000),
                         held_hourly_slot=f"{slot_start.strftime('%H:%M')}-{slot_end.strftime('%H:%M')}",
                         held_hourly_slot_display=f'{clock(slot_start)}–{clock(slot_end)}',
                         bath_reservation_id=f'bath-reservation:{activity_id}:{held_id}',attendance_reservation_policy=ATTENDANCE_POLICY)
            # Bath is a scheduled exempt activity: interval is res_start to res_end, no proof required, 0 points.
            items.append(dict(activity_id=activity_id,account_id=record['account_id'],type='BATH',title=f'Bath – {interval_str}',date=day,
                scheduled_time=planned.isoformat(),window_start=res_start.isoformat(),window_end=res_end.isoformat(),notification_time=(res_start-timedelta(minutes=1)).isoformat(),
                closing_warning_time=(res_end-timedelta(minutes=1)).isoformat(),window_duration_minutes=duration,
                points=0,penalty_points=0,status='SCHEDULED',submitted_at=None,is_enabled=1,
                proof_requirements='NONE',validation_rule='EXEMPT_ACTIVITY',details_json=json.dumps(details)))
        return items

    @staticmethod
    def evaluate_late_wakeup(now_dt, attends_college, bath1_activity, bath2_activity=None):
        details1 = json.loads(bath1_activity.get('details_json') or '{}')
        act_start = datetime.fromisoformat(details1.get('reserved_start', bath1_activity['scheduled_time']))
        act_end = datetime.fromisoformat(details1.get('reserved_end', bath1_activity['window_start']))
        proof_start = datetime.fromisoformat(bath1_activity['window_start'])
        proof_end = datetime.fromisoformat(bath1_activity['window_end'])

        if now_dt < act_start:
            state = 'BEFORE_ACTIVITY'
        elif now_dt < act_end:
            state = 'IN_ACTIVITY'
        elif now_dt < proof_end:
            state = 'PROOF_WINDOW_OPEN'
        else:
            state = 'EXPIRED_MISSED'

        bath2_feasible = True
        if bath2_activity is not None:
            details2 = json.loads(bath2_activity.get('details_json') or '{}')
            act2_start = datetime.fromisoformat(details2.get('reserved_start', bath2_activity['scheduled_time']))
            proof2_end = datetime.fromisoformat(bath2_activity['window_end'])
            if act2_start < proof_end or now_dt >= proof2_end:
                bath2_feasible = False

        remaining_min = max(0, int((proof_end - now_dt).total_seconds() // 60)) if state == 'PROOF_WINDOW_OPEN' else 0
        return dict(
            state=state,
            attends_college=bool(attends_college),
            activity_start=act_start.isoformat(),
            activity_end=act_end.isoformat(),
            proof_window_start=proof_start.isoformat(),
            proof_window_end=proof_end.isoformat(),
            proof_remaining_minutes=remaining_min,
            bath2_feasible=bath2_feasible,
        )

    def cancel_bath(self, account, date, activity_id=None):
        with self.db._connect() as conn:
            if activity_id:
                conn.execute("UPDATE scheduled_activities SET status='CANCELLED',is_enabled=0 WHERE account_id=? AND date=? AND activity_id=? AND type='BATH' AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN')", (account, date, activity_id))
            else:
                conn.execute("UPDATE scheduled_activities SET status='CANCELLED',is_enabled=0 WHERE account_id=? AND date=? AND type='BATH' AND status IN ('SCHEDULED','UPCOMING','NOTIFIED','OPEN')", (account, date))
            self.reconcile_bath_reservations(conn, account, date, now_ist())
            return {"success": True, "message": "Bath activity cancelled and overlapping windows restored"}
