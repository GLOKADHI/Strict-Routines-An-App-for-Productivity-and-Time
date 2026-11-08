"""Resolve legacy score-event ownership before exposing an account ledger."""
import json


def filter_account_score_events(conn, events, account_id):
    if account_id is None:
        return events
    activities = {row['activity_id']: row['account_id'] for row in conn.execute(
        'SELECT activity_id,account_id FROM scheduled_activities')}
    proofs = {row['evidence_id']: row['user_id'] for row in conn.execute(
        'SELECT evidence_id,user_id FROM proof_reviews')}
    devices = {row['device_id']: row['user_id'] for row in conn.execute(
        'SELECT device_id,user_id FROM devices')}
    selected = []
    for original in events:
        event = dict(original)
        try:
            audit = json.loads(event.get('audit_json') or '{}')
            audit = audit if isinstance(audit, dict) else {}
        except (TypeError, ValueError):
            audit = {}
        owner = audit.get('account_id') or audit.get('user_id')
        task = event.get('related_task_id') or audit.get('activity_id') or audit.get('scheduled_activity_id')
        owner = owner or activities.get(task) or proofs.get(event.get('evidence_id')) or devices.get(event.get('device_id'))
        # Events with no attributable owner remain in the global historical ledger.
        if owner == account_id:
            selected.append(original)
    return selected
