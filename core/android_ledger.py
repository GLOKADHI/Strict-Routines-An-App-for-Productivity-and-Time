"""Validated replica checkpoints for the existing Android-authoritative score_events ledger.

Transactions remain in score_events. system_config holds only transfer receipts and
projection metadata; it is never an independent source of point awards.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


PREFIX = 'android_ledger_v1:'


def reporting_date(value=None, now=None, timezone_name='Asia/Kolkata'):
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(timezone_name))
    if value is None or str(value).lower() == 'today':
        return local.date().isoformat()
    if str(value).lower() == 'all':
        return None
    if str(value).lower() == 'yesterday':
        return (local.date() - timedelta(days=1)).isoformat()
    return datetime.strptime(str(value), '%Y-%m-%d').date().isoformat()


def _key(account, date, dev=False):
    return f'{PREFIX}{account}:{date}:{int(dev)}'


def _read(conn, account, date, dev=False):
    try:
        row = conn.execute('SELECT value FROM system_config WHERE key=?', (_key(account, date, dev),)).fetchone()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def _authorized_negative(event):
    audit = event.get('audit') or {}
    if isinstance(audit, str):
        try:
            audit = json.loads(audit)
        except Exception:
            audit = {}
    if event['event_type'] == 'INITIAL_RESET' and audit.get('action') == 'INITIAL_POINTS_RESET':
        return bool(audit.get('reason'))
    if event['event_type'] in {'SNOOZE', 'HOURLY_WASTED', 'DAILY_RESET', 'RESTRICTED_APP_PENALTY', 'BROWSER_EXTENSION_PENALTY', 'REVERSAL'}:
        return True
    source = audit.get('approval_source')
    author = str(audit.get('approved_by') or audit.get('Admin') or audit.get('admin_id') or '').strip()
    return source in {'ANDROID_HUMAN_CONFIRMATION', 'AUTHENTICATED_HUMAN_ADMIN', 'ANDROID_ADMIN', 'ANDROID_ACCESSIBILITY_ENFORCEMENT', 'BROWSER_EXTENSION_ENFORCEMENT'} and bool(author) and author.lower() not in {'system', 'auto', 'unknown'}


def _financial_manifest(events):
    """Ignore transport-only descriptions, never financial/source/approval fields."""
    from cloud_points.protocol import sanitize_envelope
    payload={'schema':1,'account_id':'scope','device_id':'scope','date':'scope','timezone':'Asia/Kolkata',
             'revision':1,'event_count':len(events),'confirmed_score':0,'raw_verified_points':0,'score_events':events}
    raw=json.dumps(payload)
    clean=sanitize_envelope({'payload_json':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()})
    rows=json.loads(clean['payload_json'])['score_events']
    return json.dumps(sorted(rows,key=lambda row:row['event_id']),sort_keys=True)


def reconcile(db, envelope, device_id, account_id='user_lok', *, minimized_transport=False):
    """Called only behind existing device credentials or an authorized transfer session."""
    try:
        raw = envelope['payload_json']
        digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        if digest != envelope.get('sha256'):
            raise ValueError('Ledger payload checksum mismatch')
        payload = json.loads(raw)
        if payload.get('schema') != 1 or payload.get('account_id') != account_id or payload.get('device_id') != device_id:
            raise ValueError('Ledger schema, account or device mismatch')
        date = reporting_date(payload['date'])
        if date != payload['date']:
            raise ValueError('An explicit ledger reporting date is required')
        ZoneInfo(payload['timezone'])
        dev = bool(payload.get('is_dev_mode', False))
        revision = int(payload['revision'])
        if revision < 1:
            raise ValueError('Invalid ledger revision')
        events = payload['score_events']
        if not isinstance(events, list) or len(events) != payload['event_count']:
            raise ValueError('Incomplete ledger transaction manifest')
        if len(events) > 10000:
            raise ValueError('Ledger manifest exceeds transfer limit')
        ids = set()
        eligible = {}
        seen_award_activities = set()
        for event in events:
            event_id = event.get('event_id')
            if not isinstance(event_id, str) or not event_id or event_id in ids:
                raise ValueError('Missing or duplicate transaction identity')
            ids.add(event_id)
            if event.get('date') != date or bool(event.get('is_dev_mode', False)) != dev:
                raise ValueError('Transaction reporting scope mismatch')
            if not event.get('event_type') or not event.get('timestamp'):
                raise ValueError('Transaction metadata is incomplete')
            points = event['points']
            if type(points) is not int or type(event.get('eligible')) is not bool:
                raise ValueError('Invalid transaction amount or confirmation state')
            if points < 0 and event['eligible'] and not _authorized_negative(event):
                raise ValueError('A negative transaction lacks human authorization')
            if points > 0 and event.get('event_type') in {'PROOF_APPROVED', 'TASK_VERIFIED'}:
                audit_info = event.get('audit') or {}
                if isinstance(audit_info, str):
                    try:
                        audit_info = json.loads(audit_info)
                    except Exception:
                        audit_info = {}
                act_key = event.get('related_task_id') or audit_info.get('activity_id') or audit_info.get('submission_id')
                if act_key:
                    if act_key in seen_award_activities:
                        raise ValueError(f'Duplicate point award for activity/submission {act_key}')
                    seen_award_activities.add(act_key)
            eligible[event_id] = points if event['eligible'] else 0
        raw_score = sum(eligible.values())
        # Preserve the APK's established upper cap and negative-score behavior.
        balance = min(100, raw_score)
        if payload.get('confirmed_score') != balance or payload.get('raw_verified_points') != raw_score:
            raise ValueError('Confirmed balance disagrees with the transaction manifest')
        received = datetime.now(timezone.utc).isoformat()
        with db._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)')
            prior = _read(conn, account_id, date, dev)
            if prior and prior['device_id'] != device_id:
                raise ValueError('A different Android authority already owns this reporting period')
            aliases=list(prior.get('sha256_aliases',[])) if prior else []
            if prior and revision==prior['revision'] and digest!=prior['sha256'] and digest not in aliases:
                equivalent=(minimized_transport and _financial_manifest(events)==_financial_manifest(prior['transactions']))
                if equivalent:aliases=list(dict.fromkeys(aliases+[prior['sha256'],digest]))[-4:]
                else:raise ValueError('Stale or conflicting ledger checkpoint')
            if prior and revision < prior['revision']:
                raise ValueError('Stale or conflicting ledger checkpoint')
            inserted_count = 0
            for event in events:
                if (event.get('audit') or {}).get('approval_source') == 'BROWSER_EXTENSION_ENFORCEMENT' and event['event_type'] == 'BROWSER_EXTENSION_DISABLED':
                    from core.extension_penalty_rule import reconcile_authoritative_event
                    reconcile_authoritative_event(conn,event)
                existing = conn.execute('SELECT date,is_dev_mode,event_type,points FROM score_events WHERE event_id=?', (event['event_id'],)).fetchone()
                if existing and (existing['date'], bool(existing['is_dev_mode']), existing['event_type'], existing['points']) != (
                    date, dev, event['event_type'], event['points']):
                    raise ValueError('Transaction identity conflicts with a retained record')
                if not existing:
                    audit = dict(event.get('audit') or {})
                    audit['synced_from_android'] = True
                    audit['account_id'] = account_id
                    inserted_count += 1
                    conn.execute('''INSERT OR IGNORE INTO score_events(event_id,date,is_dev_mode,timestamp,event_type,
                        device_id,description,points,rule_version,evidence_id,related_task_id,audit_json)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (event['event_id'], date, int(dev), event['timestamp'],
                        event['event_type'], device_id, event.get('description') or event['event_type'].replace('_', ' ').capitalize(),
                        event['points'], int(event.get('rule_version') or 1), event.get('evidence_id') or None,
                        event.get('related_task_id') or None, json.dumps(audit)))
            receipt = {'accepted': True, 'authority': 'ANDROID', 'account_id': account_id, 'device_id': device_id,
                       'date': date, 'timezone': payload['timezone'], 'is_dev_mode': dev,
                       'revision': revision, 'sha256': digest, 'event_count': len(events),
                       'sha256_aliases': aliases if prior and revision==prior['revision'] else [],
                       'confirmed_balance': balance, 'raw_verified_points': raw_score, 'received_at': received,
                       'new_event_count': inserted_count,
                       'event_ids': sorted(ids), 'eligible_points': eligible,
                       'transactions': events}
            conn.execute('INSERT OR REPLACE INTO system_config(key,value,updated_at) VALUES(?,?,?)',
                         (_key(account_id, date, dev), json.dumps(receipt), received))
        return receipt
    except (KeyError, ValueError, TypeError, OverflowError) as error:
        with db._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)')
            failed = datetime.now(timezone.utc).isoformat()
            conn.execute('INSERT OR REPLACE INTO system_config(key,value,updated_at) VALUES(?,?,?)',
                         ('android_ledger_error:' + account_id, json.dumps({'at': failed, 'error': str(error)}), failed))
        return {'accepted': False, 'authority': 'ANDROID', 'error': str(error), 'code': 'LEDGER_RECONCILIATION_REQUIRED'}


def checkpoint(db, date, account_id='user_lok', dev=False, now=None):
    """An offline replica retains its last confirmed balance and never claims freshness."""
    with db._connect() as conn:
        receipt = _read(conn, account_id, date, dev)
        if not receipt:
            return None
        rows = {r['event_id']: dict(r) for r in conn.execute(
            'SELECT event_id,date,is_dev_mode,event_type,points FROM score_events WHERE date=? AND is_dev_mode=?', (date, int(dev)))}
        for event in receipt['transactions']:
            stored = rows.get(event['event_id'])
            if not stored or (stored['event_type'], stored['points']) != (event['event_type'], event['points']):
                return None
        error_row = conn.execute('SELECT value FROM system_config WHERE key=?', ('android_ledger_error:' + account_id,)).fetchone()
        failure = json.loads(error_row[0]) if error_row else {}
    moment = now or datetime.now(timezone.utc)
    age = (moment - datetime.fromisoformat(receipt['received_at'])).total_seconds()
    last_error = failure.get('error') if failure.get('at', '') >= receipt['received_at'] else None
    fresh = 0 <= age <= 120 and not last_error
    return dict(receipt, fresh=fresh, last_error=last_error,
                sync_status='RECONCILIATION REQUIRED' if last_error else 'SYNCED' if fresh else 'STALE — last confirmed Android ledger')


def projection(db, date, account_id='user_lok', dev=False):
    receipt = checkpoint(db, date, account_id, dev)
    if not receipt:
        return None
    return {'date': date, 'verified_points': receipt['confirmed_balance'],
            'raw_verified_points': receipt['raw_verified_points'], 'provisional_points': 0,
            'rejected_or_revoked_points': 0, 'unverified_points': 0, 'total_events': receipt['event_count'],
            'authority': 'ANDROID', 'ledger_revision': receipt['revision'], 'ledger_checkpoint': receipt['sha256'],
            'sync_status': receipt['sync_status'], 'last_sync_at': receipt['received_at']}


def reporting_timezone(db, account_id='user_lok'):
    with db._connect() as conn:
        try:
            row = conn.execute('SELECT value FROM system_config WHERE key LIKE ? ORDER BY updated_at DESC LIMIT 1',
                               (PREFIX + account_id + ':%',)).fetchone()
            saved = json.loads(row[0]) if row else {}
            return saved.get('timezone', 'Asia/Kolkata') if saved.get('account_id') == account_id else 'Asia/Kolkata'
        except Exception:
            return 'Asia/Kolkata'
