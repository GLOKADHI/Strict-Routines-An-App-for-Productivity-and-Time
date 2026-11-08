"""Receipt freshness for the session-bound iPad; never guesses from its clock."""
import hmac
import json

GRACE_SECONDS = 60.0


def ipad_binding(db, preparation_id, account):
    with db._connect() as c:
        row = c.execute(
            '''SELECT j.credential_hash FROM strict_join_participants j
            JOIN strict_preparations p USING(preparation_id)
            WHERE p.preparation_id=? AND p.account_id=? AND j.role='IOS' ''',
            (preparation_id, account),
        ).fetchone()
        if not row:
            row = c.execute(
                '''SELECT credential_hash FROM strict_join_participants
                WHERE preparation_id=? AND role='IOS' ''',
                (preparation_id,),
            ).fetchone()
    if not row:
        raise ValueError('Join the iPhone/iPad timer before starting or resuming.')
    return row['credential_hash']


def ipad_connection(db, preparation_id, account, binding_hash, now):
    try:
        with db._connect() as c:
            row = c.execute(
                '''SELECT j.seen, j.report_json, j.credential_hash FROM strict_join_participants j
                JOIN strict_preparations p USING(preparation_id)
                WHERE p.preparation_id=? AND p.account_id=? AND j.role='IOS' ''',
                (preparation_id, account),
            ).fetchone()
            if not row:
                row = c.execute(
                    '''SELECT seen, report_json, credential_hash FROM strict_join_participants
                    WHERE preparation_id=? AND role='IOS' ''',
                    (preparation_id,),
                ).fetchone()
        if not row:
            return dict(connected=False, reason='Reconnect the same iPad timer; its session binding is missing or changed.')
        if binding_hash and not hmac.compare_digest(str(row['credential_hash']), str(binding_hash)):
            return dict(connected=False, reason='Reconnect the same iPad timer; credential binding changed.')
        report = json.loads(row['report_json']) if row['report_json'] else {}
        age = now - float(row['seen'])
        connected = bool(row['seen'] and 0 <= age < GRACE_SECONDS and report.get('timer_ready') is True and report.get('online') is True)
        return dict(
            connected=connected,
            age_seconds=max(0, age),
            reason='' if connected else 'iPad connection lost. Study time is paused until its timer reconnects.',
            screen_sharing=bool(connected and report.get('screen_sharing')),
            screen_preview=report.get('screen_preview') if connected else None,
        )
    except Exception:
        return dict(connected=False, reason='iPad connection cannot be verified. Reconnect its timer.')
