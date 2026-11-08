"""Narrow validation for Android-authorized extension interruption transactions."""
import math
import re

POLICY_VERSION = 'extension-disabled-v1'
BROWSERS = {'chrome', 'edge', 'brave', 'comet', 'vivaldi', 'opera', 'arc'}


def valid_proposal(proposal, now):
    try:
        evidence = proposal['evidence']
        episode, minute = evidence['episode_id'], evidence['minute_index']
        if (proposal['event_type'] != 'BROWSER_EXTENSION_DISABLED' or proposal['originating_device'] != 'windows_companion'
                or proposal['applicable_rule'] != 'CHROME_EXTENSION_ENFORCEMENT_RULE'
                or type(proposal['proposed_points']) is not int or proposal['proposed_points'] != -1
                or evidence.get('automatic_rule_version') != POLICY_VERSION
                or evidence.get('source') != 'CHROME_MANAGEMENT_API'
                or type(minute) is not int or minute < 1 or not re.fullmatch(r'ext_ep_[a-f0-9]{32}', episode)
                or proposal['proposal_id'] != f'tx_ext_{episode}_m{minute}'):
            return False
        grace, detected, deadline = evidence['grace_policy_minutes'], evidence['disabled_detected_at'], evidence['grace_deadline']
        if type(grace) is not int or grace not in (5, 7, 10):
            return False
        if any(type(n) not in (int,float) or not math.isfinite(n) or n <= 0 for n in (detected,deadline)) or abs(deadline-detected-grace*60) > .001:
            return False
        binding = evidence['installation']
        if binding['browser_key'] not in BROWSERS or not re.fullmatch('[a-f0-9]{20}',binding['installation_id']):
            return False
        if binding['target_id'] != 'iincmekgilffpjmmmcgloliadppioajc' or binding['helper_id'] != 'fnakpmohnkhnlhodhilbhecngkjgjkgn':
            return False
        import uuid
        uuid.UUID(binding['helper_profile']); uuid.UUID(binding['target_profile'])
        segments = evidence['verified_segments']
        if not isinstance(segments,list) or not 1 <= len(segments) <= 120:
            return False
        total, previous = 0., deadline
        for a,b in segments:
            if any(type(n) not in (int,float) or not math.isfinite(n) for n in (a,b)) or a < previous-.001 or b <= a or b > now+5:
                return False
            total += b-a
            previous = b
        return (abs(total-60) < .001 and evidence['interval_start'] == segments[0][0]
                and evidence['interval_end'] == segments[-1][1]
                and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}',proposal['date'])))
    except (KeyError,ValueError,TypeError,OverflowError):
        return False


def reconcile_authoritative_event(conn, event):
    """Called only inside a validated Android ledger snapshot transaction."""
    audit = event.get('audit') or {}
    if audit.get('approval_source') != 'BROWSER_EXTENSION_ENFORCEMENT':
        return
    proposal_id = audit.get('proposal_id','')
    row = conn.execute('SELECT * FROM deduction_proposals WHERE proposal_id=?',(proposal_id,)).fetchone()
    if not row or row['status'] == 'REJECTED':
        raise ValueError('Automatic extension deduction requires its retained pending proposal')
    import json
    from datetime import datetime
    proposal = dict(row,evidence=json.loads(row['evidence_json']))
    if (not valid_proposal(proposal,datetime.now().timestamp()) or event['points'] != -1
            or event['date'] != proposal['date'] or event['event_type'] != 'BROWSER_EXTENSION_DISABLED'
            or event['event_id'] != 'se_deduct_'+proposal_id or audit.get('rule_version') != POLICY_VERSION
            or audit.get('evidence') != proposal['evidence']):
        raise ValueError('Automatic extension deduction evidence does not match the retained proposal')
    conn.execute('''UPDATE deduction_proposals SET status='APPROVED',approved_by=?,approved_at=?,
        approval_source='BROWSER_EXTENSION_ENFORCEMENT',finalized_event_id=?,updated_at=? WHERE proposal_id=?''',
        ('Android extension rule',audit['approved_at'],event['event_id'],event['timestamp'],proposal_id))
