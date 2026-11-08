"""Account-wide demo eligibility. No points, ledger, review or sync-event writes."""
import hashlib
import time

DURATION_MS = 30_000
COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000
ACCESS_POINTS = 81
SCHEMA = '''CREATE TABLE IF NOT EXISTS demo_access_state(
    account_id TEXT PRIMARY KEY,
    last_successful_activation_ms BIGINT NOT NULL,
    expires_at_ms BIGINT NOT NULL,
    status TEXT NOT NULL
)'''


class DemoUnavailable(ValueError):
    def __init__(self, state):
        self.state = state


def _now(store, tx):
    # Production time belongs to the trusted database, never a client timestamp.
    if store.sqlite:
        return int(time.time() * 1000)
    return int(tx.execute('SELECT EXTRACT(EPOCH FROM clock_timestamp()) * 1000 AS now_ms').fetchone()['now_ms'])


def _view(account, row, now):
    last = row['last_successful_activation_ms'] if row else None
    expiry = row['expires_at_ms'] if row else None
    active = bool(row and row['status'] == 'ACTIVE' and last <= now < expiry)
    return dict(account_id=account, last_successful_activation_ms=last, expires_at_ms=expiry,
                status='ACTIVE' if active else 'EXPIRED' if row else 'AVAILABLE',
                server_now_ms=now, next_eligible_ms=last + COOLDOWN_MS if row else None,
                available=not row or now >= last + COOLDOWN_MS,
                remaining_ms=max(0, expiry - now) if active else 0,
                activation_id=hashlib.sha256(f'{account}:{last}'.encode()).hexdigest() if row else None,
                duration_ms=DURATION_MS, effective_access_points=ACCESS_POINTS if active else None)


def state(store, principal, *, activate=False):
    account = principal['account_id']
    with store.transaction() as tx:
        # Serializes first use and simultaneous APK/EXE requests without editing
        # the identity row, any points table, or any history/approval record.
        store.lock(tx, account)
        now = _now(store, tx)
        row = tx.execute('SELECT * FROM demo_access_state WHERE account_id=?', (account,)).fetchone()
        if row and (not isinstance(row['last_successful_activation_ms'], int)
                    or row['last_successful_activation_ms'] <= 0
                    or row['expires_at_ms'] - row['last_successful_activation_ms'] != DURATION_MS
                    or row['status'] not in {'ACTIVE', 'EXPIRED'}):
            raise ValueError('Demo cooldown state is uncertain; no additional activation allowed')
        if row and row['status'] == 'ACTIVE' and not row['last_successful_activation_ms'] <= now < row['expires_at_ms']:
            tx.execute("UPDATE demo_access_state SET status='EXPIRED' WHERE account_id=?", (account,))
            row = dict(row, status='EXPIRED')
        view = _view(account, row, now)
        if activate:
            if not view['available'] or view['status'] == 'ACTIVE':
                raise DemoUnavailable(view)
            tx.execute('''INSERT INTO demo_access_state(account_id,last_successful_activation_ms,expires_at_ms,status)
                VALUES(?,?,?,'ACTIVE') ON CONFLICT(account_id) DO UPDATE SET
                last_successful_activation_ms=excluded.last_successful_activation_ms,
                expires_at_ms=excluded.expires_at_ms,status=excluded.status''', (account, now, now + DURATION_MS))
            view = _view(account, dict(last_successful_activation_ms=now, expires_at_ms=now + DURATION_MS, status='ACTIVE'), now)
    # A successful receipt is returned only after the activation commit succeeded.
    return dict(success=True, demo_access=view)
