"""Single YouTube Reward authority. Reward state never writes points tables.

The enrolled Android ledger authority attests persisted acceptance and completed
Taufa/category approval, not a browser or a self-reported client score. One
entitlement per account/hourly occurrence is the existing frequency policy.
"""
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
from .demo_access import _now

DURATION_MS = 20 * 60 * 1000
EARLY_MS = 10 * 60 * 1000
SCHEMA = '''
CREATE TABLE IF NOT EXISTS youtube_reward_submissions(
 account_id TEXT NOT NULL,submission_id TEXT NOT NULL,activity_id TEXT NOT NULL,
 accepted_at_ms BIGINT NOT NULL,deadline_ms BIGINT NOT NULL,task_type TEXT NOT NULL,
 validation_status TEXT NOT NULL,PRIMARY KEY(account_id,submission_id));
CREATE TABLE IF NOT EXISTS youtube_reward_entitlements(
 account_id TEXT NOT NULL,reward_id TEXT NOT NULL,activity_id TEXT NOT NULL,
 submission_id TEXT NOT NULL,validated_score INTEGER NOT NULL,state TEXT NOT NULL,
 issued_at_ms BIGINT NOT NULL,activation_ms BIGINT,expiry_ms BIGINT,
 activated_by TEXT,termination_result TEXT,
 PRIMARY KEY(account_id,reward_id),UNIQUE(account_id,activity_id),UNIQUE(account_id,submission_id));
CREATE TABLE IF NOT EXISTS youtube_strict_study_holds(
 account_id TEXT NOT NULL,session_id TEXT NOT NULL,activity_id TEXT NOT NULL,
 owner_device TEXT NOT NULL,state TEXT NOT NULL,submission_id TEXT,
 PRIMARY KEY(account_id,session_id,activity_id));
'''
FINAL = {'VALID', 'NEEDS_REVIEW', 'INSUFFICIENT_EVIDENCE', 'INVALID', 'PROCESSING_ERROR', 'PENDING'}


def timestamp(value):
    if not isinstance(value, str) or not value.strip(): raise ValueError('Persisted timestamp required')
    dt = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo('Asia/Kolkata'))
    return int(dt.timestamp() * 1000)


def identity(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 200: raise ValueError('Invalid reward identity')
    return value


def reconcile(store, principal, submissions):
    if principal['role'] != 'AUTHORITY': raise PermissionError('Only the enrolled evidence/ledger authority can attest validation')
    if not isinstance(submissions, list) or len(submissions) > 100: raise ValueError('Invalid submission batch')
    account = principal['account_id']
    with store.transaction() as tx:
        owner = store.lock(tx, account)
        if owner['authority_device'] != principal['device_id']: raise PermissionError('Different authority device')
        now = _now(store, tx)
        for item in submissions:
            sub = identity(item['submission_id']); activity = identity(item['activity_id'])
            accepted = timestamp(item['accepted_at']); deadline = timestamp(item['submission_deadline'])
            kind = item.get('task_type'); status = item.get('validation_status')
            if kind != 'HOURLY' or status not in FINAL or accepted > now: raise ValueError('Invalid evidence attestation')
            old = tx.execute('SELECT * FROM youtube_reward_submissions WHERE account_id=? AND submission_id=?', (account, sub)).fetchone()
            if old and (old['activity_id'], old['accepted_at_ms'], old['deadline_ms'], old['task_type']) != (activity, accepted, deadline, kind):
                raise ValueError('Acceptance and actual submission deadline are immutable')
            tx.execute('''INSERT INTO youtube_reward_submissions VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(account_id,submission_id) DO UPDATE SET validation_status=excluded.validation_status''',
                (account, sub, activity, accepted, deadline, kind, status))
            # VALID is meaningful only after the existing approval policy committed.
            valid = (status == 'VALID' and item.get('approved') is True
                     and item.get('validator') == 'TAUFA_AI'
                     and all(item.get(k) is True for k in ('category_match', 'description_match', 'evidence_match'))
                     and item.get('duplicate_detected') is False
                     and type(item.get('validated_score')) is int and 6 <= item['validated_score'] <= 8)
            if valid and deadline - accepted >= EARLY_MS:
                reward = 'yt_' + hashlib.sha256((account + '\0' + activity).encode()).hexdigest()[:40]
                tx.execute('''INSERT INTO youtube_reward_entitlements
                    (account_id,reward_id,activity_id,submission_id,validated_score,state,issued_at_ms)
                    VALUES(?,?,?,?,?,'AVAILABLE',?) ON CONFLICT DO NOTHING''',
                    (account, reward, activity, sub, item['validated_score'], now))
    return state(store, principal)


def _expire(tx, account, now):
    tx.execute("UPDATE youtube_reward_entitlements SET state='EXPIRED',termination_result='DEADLINE_EXPIRED' WHERE account_id=? AND state='ACTIVE' AND (expiry_ms<=? OR activation_ms>?)", (account, now, now))


def _reward(row, now):
    r = dict(row)
    return dict(reward_id=r['reward_id'], account_id=r['account_id'], hourly_occurrence_id=r['activity_id'],
                submission_id=r['submission_id'], validated_score=r['validated_score'], state=r['state'],
                allowed_seconds=1200, activation_ms=r['activation_ms'], expiry_ms=r['expiry_ms'],
                active_client_id=r['activated_by'], termination_result=r['termination_result'],
                remaining_ms=max(0, r['expiry_ms'] - now) if r['state'] == 'ACTIVE' else 0)


def _view(tx, account, now):
    rows = [_reward(r, now) for r in tx.execute('SELECT * FROM youtube_reward_entitlements WHERE account_id=? ORDER BY issued_at_ms DESC,reward_id', (account,))]
    return dict(success=True, account_id=account, server_now_ms=now, duration_ms=DURATION_MS,
                active_session=next((r for r in rows if r['state'] == 'ACTIVE'), None),
                available_rewards=[r for r in rows if r['state'] == 'AVAILABLE'],
                reward_history=[r for r in rows if r['state'] in {'EXPIRED', 'CONSUMED'}][:100])


def state(store, principal):
    with store.transaction() as tx:
        account = principal['account_id']; store.lock(tx, account); now = _now(store, tx)
        _expire(tx, account, now)
        result = _view(tx, account, now)
    return result


def activate(store, principal, reward_id, *, stop=False):
    account = principal['account_id']; identity(reward_id)
    with store.transaction() as tx:
        store.lock(tx, account); now = _now(store, tx); _expire(tx, account, now)
        row = tx.execute('SELECT * FROM youtube_reward_entitlements WHERE account_id=? AND reward_id=?', (account, reward_id)).fetchone()
        if not row: raise ValueError('Reward not found for authenticated account')
        if not stop:
            holds = tx.execute('SELECT * FROM youtube_strict_study_holds WHERE account_id=? AND (activity_id=? OR state=?)',
                               (account, row['activity_id'], 'PENDING')).fetchall()
            if any(h['state'] != 'FINALIZED' or h['submission_id'] != row['submission_id'] for h in holds):
                raise ValueError('Strict Session requires verified Android proof and manual Windows completion')
        if stop:
            if row['state'] == 'ACTIVE':
                if row['activated_by'] != principal['device_id']:
                    raise ValueError('Only the claiming device can end this active reward')
                tx.execute("UPDATE youtube_reward_entitlements SET state='CONSUMED',termination_result='USER_COMPLETED' WHERE account_id=? AND reward_id=?", (account, reward_id))
        elif row['state'] == 'AVAILABLE':
            if tx.execute("SELECT 1 FROM youtube_reward_entitlements WHERE account_id=? AND state='ACTIVE'", (account,)).fetchone():
                raise ValueError('Another YouTube reward is already active')
            tx.execute("UPDATE youtube_reward_entitlements SET state='ACTIVE',activation_ms=?,expiry_ms=?,activated_by=? WHERE account_id=? AND reward_id=?", (now, now + DURATION_MS, principal['device_id'], account, reward_id))
        elif row['state'] == 'ACTIVE' and row['activated_by'] != principal['device_id']:
            raise ValueError('This reward is already active on another authenticated device')
        elif row['state'] != 'ACTIVE': raise ValueError('Reward already consumed or expired')
        # Retried claims return the same account-wide activation, never a new timer.
        result = _view(tx, account, now)
    return result


def strict_study(store, principal, action, body):
    """Authenticated Windows session hold; never changes points or eligibility."""
    if principal['role'] not in {'REPLICA', 'PROPOSER'}:
        raise PermissionError('Only an enrolled Windows controller can finalize a Strict Session')
    account, device = principal['account_id'], principal['device_id']
    session, activity = identity(body['session_id']), identity(body['activity_id'])
    if action not in {'begin', 'finalize', 'abort'}:
        raise ValueError('Unknown study action')
    with store.transaction() as tx:
        store.lock(tx, account)
        rows = tx.execute('SELECT * FROM youtube_strict_study_holds WHERE account_id=? AND session_id=?', (account, session)).fetchall()
        if any(r['owner_device'] != device for r in rows):
            raise PermissionError('Different Windows controller')
        if action == 'begin':
            existing = next((r for r in rows if r['activity_id'] == activity), None)
            if existing and existing['state'] == 'FINALIZED':
                return dict(success=True, account_id=account, session_id=session)
            if any(r['state'] != 'PENDING' for r in rows):
                raise ValueError('Final Strict Session cannot be reopened')
            if tx.execute("SELECT 1 FROM youtube_strict_study_holds WHERE account_id=? AND session_id<>? AND state='PENDING'", (account, session)).fetchone():
                raise ValueError('Another Strict Session is still pending')
            tx.execute("INSERT INTO youtube_strict_study_holds VALUES(?,?,?,?,'PENDING',NULL) ON CONFLICT DO NOTHING", (account, session, activity, device))
        elif action == 'abort':
            tx.execute("UPDATE youtube_strict_study_holds SET state='ABANDONED' WHERE account_id=? AND session_id=? AND state='PENDING'", (account, session))
        else:
            sub = identity(body['submission_id'])
            row = next((r for r in rows if r['activity_id'] == activity), None)
            if not row or row['state'] == 'ABANDONED':
                raise ValueError('No matching pending Strict Session')
            if row['state'] == 'FINALIZED' and row['submission_id'] != sub:
                raise ValueError('Completion receipt is immutable')
            # Android's existing authority still determines VALID and reward
            # eligibility. This receipt alone cannot create an entitlement.
            tx.execute("UPDATE youtube_strict_study_holds SET state='ABANDONED' WHERE account_id=? AND session_id=? AND activity_id<>? AND state='PENDING'", (account, session, activity))
            tx.execute("UPDATE youtube_strict_study_holds SET state='FINALIZED',submission_id=? WHERE account_id=? AND session_id=? AND activity_id=?", (sub, account, session, activity))
    return dict(success=True, account_id=account, session_id=session)
