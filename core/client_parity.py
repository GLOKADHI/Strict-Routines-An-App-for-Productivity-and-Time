"""Shared Windows/extension projections and commands over the existing engines."""
import calendar
import contextlib
import hashlib
import json
import logging
from datetime import date as Date, timedelta

from core.activity_report import build_truthful_daily_summary
from core.android_ledger import checkpoint
from core.daily_college_decision import DailyCollegeDecision
from core.gym_daily_decision import DailyGymDecision
from core.gym_mode_engine import GymModeEngine
from core.time_window_engine import TimeWindowEngine, now_ist
from core.product_policy import GYM


class _TransactionConnection:
    def __init__(self, connection):
        self.connection = connection
    def __getattr__(self, name):
        return getattr(self.connection, name)
    def commit(self):
        pass  # Only the outer routine transaction commits.
    def rollback(self):
        raise RuntimeError('Nested routine operation requested rollback')


def _transaction_db(db, conn):
    from core.database import StrictRecordsDB
    class BoundDB(StrictRecordsDB):
        @contextlib.contextmanager
        def _connect(self):
            yield _TransactionConnection(conn)
    bound = object.__new__(BoundDB)
    bound.__dict__.update(db.__dict__)
    return bound


class ClientParityService:
    def __init__(self, db, engine):
        self.db, self.engine = db, engine
        from core.client_account import ensure_configuration
        ensure_configuration(db)

    def state(self, account, day=None):
        day = day or now_ist().date().isoformat()
        Date.fromisoformat(day)
        receipt = checkpoint(self.db, day, account)
        with self.db._connect() as conn:
            DailyCollegeDecision.reconcile_bath_reservations(conn,account,day,now_ist())
            self.db.reconcile_exam_attendance_exemptions(day)
            activities = [dict(row) for row in conn.execute('''SELECT activity_id,type,title,date,
                scheduled_time,window_start,window_end,status,attendance_status,validation_status,
                submission_id,submitted_at,points,schedule_version,details_json FROM scheduled_activities
                WHERE account_id=? AND date=? AND is_enabled=1 ORDER BY scheduled_time''', (account, day))]
            marker = self.db.get_config_value(f'daily_entry_confirmed:{account}:{day}')
        return dict(success=True, schema_version=1, account_id=account, date=day,
            policy={'gym_supported_minutes':GYM['supported_workout_minutes'],'gym_default_minutes':GYM['default_workout_minutes']},
            schedule=self.db.get_confirmed_routine_schedule(day,account) or self.db.get_draft_routine_schedule(day,account),
            timezone='Asia/Kolkata', ledger={key: receipt.get(key) for key in
                ('confirmed_balance','ledger_revision','revision','received_at','fresh','sync_status')} if receipt else None,
            ledger_state='ANDROID_AUTHORITY' if receipt else 'AWAITING_AUTHORITY',
            activities=activities, college=DailyCollegeDecision(self.db).get(account, day),
            gym=DailyGymDecision(self.db).get(account, day),
            gym_mode=GymModeEngine(self.db).state(account),
            entry_confirmed=bool(marker),
            summary=build_truthful_daily_summary(self.db, day, activities=activities,
                ledger=self.db.get_point_ledger_summary(date=day,account_id=account),account_id=account),
            capabilities={'android_accessibility':False,'native_android_camera':False,
                          'native_android_blocking':False,'browser_website_enforcement':True})

    def month(self, account, month):
        first = Date.fromisoformat(month + '-01')
        end = first + timedelta(days=calendar.monthrange(first.year, first.month)[1])
        with self.db._connect() as conn:
            rows = [dict(row) for row in conn.execute('''SELECT activity_id,date,type,title,status,
                scheduled_time,window_start,window_end,attendance_status,validation_status,
                submission_id,schedule_version FROM scheduled_activities
                WHERE account_id=? AND date>=? AND date<? AND is_enabled=1
                ORDER BY date,scheduled_time LIMIT 2000''', (account, first.isoformat(), end.isoformat()))]
        return dict(success=True, schema_version=1, month=month, account_id=account, activities=rows,
                    source='PERSISTED_SCHEDULED_ACTIVITIES')

    def confirm_entry(self, principal, body):
        account, day = principal.account_id, str(body.get('date') or '')
        Date.fromisoformat(day)
        request = body.get('request_id')
        if not isinstance(request, str) or not 8 <= len(request) <= 100:
            raise ValueError('A stable daily-setup request ID is required.')
        if type(body.get('attends_college')) is not bool or type(body.get('gym_planned')) is not bool:
            raise ValueError('Choose Yes or No for both College and Gym.')
        gym = body['gym_planned']
        if gym and (not body.get('gym_time') or type(body.get('gym_duration_minutes')) is not int):
            raise ValueError('Choose the Gym time and expected duration.')
        payload = dict(schedule_date=day, wake_up_time=body.get('wake_up_time'),
            gym_enabled=gym, gym_time=body.get('gym_time') if gym else '18:30',
            gym_duration_minutes=body.get('gym_duration_minutes') if gym else GYM['default_workout_minutes'],
            timezone='Asia/Kolkata')
        if not body.get('wake_up_time'):
            raise ValueError('Confirm the morning Wake-Up target.')
        digest = hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        # One transaction covers the two existing choice services, schedule,
        # materialized activities, queue and confirmation marker. Failed conflict
        # validation cannot leave half a routine saved.
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''CREATE TABLE IF NOT EXISTS client_routine_requests(
                account_id TEXT NOT NULL,request_id TEXT NOT NULL,sha256 TEXT NOT NULL,
                PRIMARY KEY(account_id,request_id))''')
            prior=conn.execute('SELECT sha256 FROM client_routine_requests WHERE account_id=? AND request_id=?',
                               (account,request)).fetchone()
            if prior and prior[0] != digest:
                raise ValueError('This request ID was already used with different routine choices.')
            if not prior:
                bound=_transaction_db(self.db,conn)
                existing=bound.get_confirmed_routine_schedule(day,account)
                if existing:
                    raise ValueError('This schedule is already confirmed. Use the existing authorized schedule-change flow.')
                DailyCollegeDecision(bound).save(account, day, body['attends_college'],
                    body.get('college_revision'), request+'-college',bath_plans=body.get('bath_plans'),
                    actor=principal.actor_id,device_id=principal.device_id,connection=conn)
                DailyGymDecision(bound).save(account,day,gym,body.get('gym_revision'),request+'-gym',
                    actor=principal.actor_id,device_id=principal.device_id,connection=conn)
                engine=TimeWindowEngine(bound)
                result=engine.confirm_next_day_schedule(payload,account_id=account,
                    actor=principal.actor_id,device_id=principal.device_id)
                if not result.get('success'):
                    raise ValueError(result.get('error') or 'Routine validation failed.')
                conn.execute('INSERT INTO client_routine_requests VALUES(?,?,?)',(account,request,digest))
                conn.execute('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING',
                    (f'daily_entry_confirmed:{account}:{day}',json.dumps({'request_id':request,'at':now_ist().isoformat()})))
        return self.state(account, day)


def dispatch(handler, method, path, query, body=None):
    if not path.startswith('/api/client/'):
        return False
    from core.gym_daily_decision import GymDecisionError
    from core.strictbot_assistant import AssistantError
    body = body or {}
    try:
        if handler.headers.get('Origin','').startswith('chrome-extension://') and not handler.headers.get('X-Device-Token'):
            raise PermissionError('Pair this browser profile using the PC Device Center code.')
        principal = handler._gym_principal(body)
        requested = body.get('account_id') or query.get('account_id', [principal.account_id])[0]
        if requested != principal.account_id:
            raise PermissionError('Account scope does not match this authenticated client.')
        service = ClientParityService(handler.db, handler.engine)
        if path.startswith('/api/client/account'):
            from core.client_account import ClientAccountConnection
            if not getattr(handler.server, 'client_account_connection', None):
                handler.server.client_account_connection = ClientAccountConnection(handler.db)
            account = handler.server.client_account_connection
            if method == 'GET':
                result = account.status(principal.account_id)
                if handler.headers.get('X-Device-Id'):
                    result.pop('queue',None)
                    result.pop('error',None)
            else:
                if handler.headers.get('Origin', '').startswith('chrome-extension://') or handler.headers.get('X-Device-Id') or not handler._is_local_request():
                    raise PermissionError('Manage Windows Google sign-in inside the Windows app. The extension has its own OAuth authorization.')
                result = account.action(principal.account_id, path.rsplit('/',1)[-1], body)
        elif path == '/api/client/state' and method == 'GET':
            result = service.state(principal.account_id, query.get('date', [None])[0])
        elif path == '/api/client/calendar' and method == 'GET':
            result = service.month(principal.account_id, query.get('month', [now_ist().strftime('%Y-%m')])[0])
        elif path == '/api/client/routine' and method == 'POST':
            result = service.confirm_entry(principal, body)
        elif path == '/api/client/demo' and method in ('GET','POST'):
            from core.paired_demo_service import LocalAccountDemoStore
            from cloud_points.demo_access import state
            result = state(LocalAccountDemoStore(handler.db.db_path, principal.account_id),
                           {'account_id':principal.account_id}, activate=method=='POST')
        else:
            handler._send_json({'success':False,'error':'Unknown client action'}, status=404)
            return True
        handler._send_json(result)
    except ConnectionError:
        return True  # The client closed its page; do not write a second response.
    except (GymDecisionError, AssistantError) as error:
        handler._send_json({'success':False,'code':error.code,'error':str(error)}, status=getattr(error,'status',400))
    except PermissionError as error:
        handler._send_json({'success':False,'error':str(error)}, status=403)
    except (ValueError, TypeError, KeyError) as error:
        from cloud_points.demo_access import DemoUnavailable
        if isinstance(error, DemoUnavailable):
            handler._send_json({'success':False,'code':'DEMO_COOLDOWN','demo_access':error.state}, status=409)
        else:
            handler._send_json({'success':False,'error':str(error)}, status=400)
    except Exception:
        logging.exception('Shared client operation failed')
        handler._send_json({'success':False,'code':'SERVICE_UNAVAILABLE',
            'error':'The operation did not complete. Retain the same request ID and retry.'},status=503)
    return True
