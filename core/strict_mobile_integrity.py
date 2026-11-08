"""Technical participant state; screen-off/unknown background never incurs penalties."""
import hmac
import hashlib
import json
import time
from core.strict_participants import COMPANION_ROLES

SCHEMA='''CREATE TABLE IF NOT EXISTS strict_mobile_integrity(
 preparation_id TEXT NOT NULL,role TEXT NOT NULL,session_id TEXT NOT NULL DEFAULT '',
 sequence INTEGER NOT NULL,state TEXT NOT NULL,confirmed INTEGER NOT NULL DEFAULT 0,
 client_kind TEXT NOT NULL,updated REAL NOT NULL,lease_until REAL NOT NULL,reason TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(preparation_id,role));'''
ALLOWED={'FOREGROUND','SCREEN_OFF_ALLOWED','SCREEN_LOCKED','BACKGROUND_REASON_UNKNOWN',
         'APP_SWITCH_CONFIRMED','RECONNECTING','SESSION_CLOSED_PENDING'}


def normalize(report,client_kind='BROWSER'):
    value=report.get('integrity') if isinstance(report,dict) else None
    value=value if isinstance(value,dict) else {}
    state=str(value.get('state','FOREGROUND' if report.get('visible') is True else 'BACKGROUND_REASON_UNKNOWN'))
    if state not in ALLOWED:state='BACKGROUND_REASON_UNKNOWN'
    # Only the iOS browser timer participates. It cannot distinguish locking
    # from app switching, regardless of any client-supplied native claim.
    if state not in {'FOREGROUND','RECONNECTING'}:state='BACKGROUND_REASON_UNKNOWN'
    confirmed=False
    sequence=value.get('sequence',0)
    if not isinstance(sequence,int) or isinstance(sequence,bool) or not 0<=sequence<2**53:raise ValueError('Invalid integrity sequence')
    reason=str(value.get('reason',''))
    if reason not in {'PAGE_HIDDEN','PAGE_EXIT_OBSERVED','NATIVE_BACKGROUND','NATIVE_SCREEN_OFF','USER_INITIATED_LEAVE','RESUMED','PROCESS_REOPENED'}:reason=''
    return dict(state=state,sequence=sequence,confirmed=confirmed,session_id=str(value.get('session_id',''))[:128],client_kind=client_kind,reason=reason)


class MobileSessionIntegrity:
    def __init__(self,db,clock=time.time):self.db,self.clock=db,clock

    def observe(self,pid,role,credential,report):
        if role != 'IOS':raise PermissionError('This Strict Session companion is no longer supported.')
        with self.db._connect() as c:
            c.execute(SCHEMA)
            if 'reason' not in {r['name'] for r in c.execute('PRAGMA table_info(strict_mobile_integrity)')}:
                c.execute("ALTER TABLE strict_mobile_integrity ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
            participant=c.execute('SELECT * FROM strict_join_participants WHERE preparation_id=? AND role=?',(pid,role)).fetchone()
            if not participant or not hmac.compare_digest(participant['credential_hash'],hashlib.sha256(str(credential).encode()).hexdigest()):
                raise PermissionError('Invalid participant integrity credential')
            prep=c.execute("SELECT * FROM strict_preparations WHERE preparation_id=? AND status IN ('PENDING','ACTIVE')",(pid,)).fetchone()
            if not prep:raise ValueError('Participant preparation is no longer active')
            kind=participant['client_kind'] if 'client_kind' in participant.keys() else 'BROWSER'
            observation=normalize(report,kind)
            old=c.execute('SELECT * FROM strict_mobile_integrity WHERE preparation_id=? AND role=?',(pid,role)).fetchone()
            if 'integrity' not in report:
                observation['sequence']=(old['sequence']+1) if old else 0
            if old and observation['sequence']<old['sequence']:raise ValueError('Stale participant integrity observation')
            if old and observation['sequence']==old['sequence']:
                return dict(old)  # Retry is idempotent; it cannot clear an interruption.
            session=observation['session_id']
            if session:
                study=c.execute('SELECT * FROM strict_study_sessions WHERE session_id=? AND account_id=?',(session,prep['account_id'])).fetchone()
                if not study or study['state'] in {'FAILED','ABANDONED','SESSION_FINALIZED','REWARD_ACTIVE'}:
                    raise ValueError('Integrity event belongs to a different or finished session')
            if observation['confirmed'] and (not session or prep['status']!='ACTIVE'):
                observation.update(state='BACKGROUND_REASON_UNKNOWN',confirmed=False)
            # Unknown interruptions cannot erase a confirmed pause. A fresh
            # foreground observation from the same native client revalidates it.
            confirmed=bool(observation['confirmed'] or (old and old['confirmed'] and observation['state']!='FOREGROUND'))
            now=self.clock()
            c.execute('''INSERT INTO strict_mobile_integrity VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(preparation_id,role) DO UPDATE SET session_id=excluded.session_id,sequence=excluded.sequence,
                state=excluded.state,confirmed=excluded.confirmed,client_kind=excluded.client_kind,updated=excluded.updated,lease_until=excluded.lease_until,reason=excluded.reason''',
                (pid,role,session or (old['session_id'] if old else ''),observation['sequence'],observation['state'],int(confirmed),kind,now,now+43200,observation['reason']))
            return dict(c.execute('SELECT * FROM strict_mobile_integrity WHERE preparation_id=? AND role=?',(pid,role)).fetchone())

    def states(self,pid):
        with self.db._connect() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='strict_mobile_integrity'").fetchone():return {}
            return {r['role']:dict(r) for r in c.execute('SELECT * FROM strict_mobile_integrity WHERE preparation_id=?',(pid,)) if r['role'] == 'IOS'}


def session_interrupted(db,account,session):
    with db._connect() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='strict_mobile_integrity'").fetchone():return False
        return bool(c.execute('''SELECT 1 FROM strict_mobile_integrity i JOIN strict_preparations p USING(preparation_id)
            WHERE p.account_id=? AND i.session_id=? AND i.role='IOS' AND i.confirmed=1 AND p.status='ACTIVE' LIMIT 1''',(account,session)).fetchone())
