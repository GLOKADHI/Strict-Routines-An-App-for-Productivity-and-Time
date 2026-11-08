import copy
import json
from pathlib import Path
import time
import uuid
from unittest.mock import patch

import pytest
from core.extension_penalty_rule import valid_proposal
from windows.extension_countdown_notification import notification_text
from test_chrome_interruption_evidence import rig


def ready(rig):
    monitor, clock, ledger, send, *_ = rig
    send(advance=0)
    for _ in range(12): send()
    return next(iter(ledger.rows.values()))


def test_countdown_uses_actual_immutable_deadline_and_persistent_host(rig):
    monitor, clock, ledger, send, *_ = rig
    send(advance=0)
    with patch('windows.extension_countdown_notification.ExtensionCountdownNotifications.get_instance') as factory:
        host = factory.return_value
        host.update.return_value = True
        monitor.show_or_update_notification = type(monitor).show_or_update_notification.__get__(monitor)
        clock.advance(1)
        monitor.show_or_update_notification(remaining_sec=999, deadline=0, penalty=0)
        model = host.update.call_args.args[1]()
        assert model['remaining_grace_sec'] == 299
        assert '04:59' in notification_text(model)
        monitor.set_grace_duration_minutes(10)
        assert monitor._remaining_grace() == 299
        send('ENABLED',1)
        assert host.remove.called
        assert monitor.notification_snapshot() is None


def test_uncertain_gap_consumes_no_penalty_but_does_not_move_grace_deadline(rig):
    monitor, clock, ledger, send, *_ = rig
    send(advance=0);send()
    deadline = monitor.grace_deadline
    clock.advance(600)
    monitor.check_state()
    assert not ledger.rows and monitor._remaining_grace() == 0
    send(advance=0);send();send()
    assert monitor.grace_deadline == deadline and len(ledger.rows) == 1
    row = next(iter(ledger.rows.values()))
    assert row['evidence']['interval_start'] > deadline


def test_only_valid_scoped_minute_can_be_automatic(rig):
    row = ready(rig)
    assert valid_proposal(row,rig[1].wall)
    for field,value in [('proposed_points',-3),('event_type','HOURLY_MISSED'),('proposal_id','arbitrary'),('originating_device','remote')]:
        changed=copy.deepcopy(row);changed[field]=value
        assert not valid_proposal(changed,rig[1].wall)
    for field,value in [('source','HEARTBEAT_MISSING'),('verified_segments',[[1,61]]),('grace_deadline',1),('automatic_rule_version','old')]:
        changed=copy.deepcopy(row);changed['evidence'][field]=value
        assert not valid_proposal(changed,rig[1].wall)


def test_pending_sync_never_claims_committed_points(rig):
    row=ready(rig)
    monitor=rig[0]
    assert monitor.get_ui_status()['sync_status']=='PENALTY SYNC PENDING'
    assert monitor.total_penalty==0 and monitor.pending_penalty==1


def test_signed_grant_uses_immutable_profile_identity_after_restoration(rig):
    from windows.browser_monitor_registry import BrowserMonitorRegistry
    row=ready(rig)
    monitor,clock,ledger,send,packet,config,storage=rig
    registry=BrowserMonitorRegistry(storage=storage,db=ledger,detector=lambda:[])
    registry.monitors[config['installation_id']]=monitor
    assert registry.owns_verified_proposal(row)
    send('ENABLED',1)
    assert registry.owns_verified_proposal(row)
    changed=copy.deepcopy(row);changed['evidence']['verified_segments'][0][0]-=1
    assert not registry.owns_verified_proposal(changed)


def test_notification_restore_uses_enrolled_browser_and_fixed_url(rig):
    monitor,_,_,_,_,_,storage=rig
    browser=dict(id='correct',key='chrome',supported=True,executable=r'C:\fixture\chrome.exe')
    with patch('windows.browser_setup.detect_browsers',return_value=[browser]),patch('subprocess.Popen') as popen:
        monitor.open_extension_manager()
        assert popen.call_args.args[0] == [browser['executable'],'chrome://extensions/']


def test_android_authority_receipt_commits_and_deduplicates_retained_proposal(rig,tmp_path):
    from core.database import StrictRecordsDB
    from core.android_ledger import reconcile
    from core.extension_penalty_rule import POLICY_VERSION
    import hashlib
    row=ready(rig)
    db=StrictRecordsDB(str(tmp_path/'replica.sqlite'))
    db.create_deduction_proposal(**{k:v for k,v in row.items() if k!='status'})
    event=dict(event_id='se_deduct_'+row['proposal_id'],date=row['date'],is_dev_mode=False,points=-1,
        eligible=True,event_type=row['event_type'],timestamp=row['timestamp'],description=row['reason'],
        audit=dict(proposal_id=row['proposal_id'],approval_source='BROWSER_EXTENSION_ENFORCEMENT',
            approved_by='Android extension rule',approved_at=row['timestamp'],rule_version=POLICY_VERSION,evidence=row['evidence']))
    payload=dict(schema=1,account_id='user_lok',device_id='android-fixture',date=row['date'],timezone='Asia/Kolkata',
        is_dev_mode=False,revision=1,event_count=1,score_events=[event],confirmed_score=-1,raw_verified_points=-1)
    raw=json.dumps(payload)
    envelope=dict(payload_json=raw,sha256=hashlib.sha256(raw.encode()).hexdigest())
    assert reconcile(db,envelope,'android-fixture')['accepted']
    assert reconcile(db,envelope,'android-fixture')['accepted']
    assert db.get_deduction_proposal(row['proposal_id'])['finalized_event_id']==event['event_id']
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM score_events WHERE event_id=?',(event['event_id'],)).fetchone()[0]==1


def test_policy_batch_is_signed_and_excludes_non_monitor_proposals(rig):
    from core.server import StrictRecordsRequestHandler
    from types import SimpleNamespace
    import hmac,hashlib
    row=ready(rig)
    replies=[]
    db=SimpleNamespace(get_active_configuration=lambda:{'rules':{'chrome_extension_penalty_enabled':True}},
                       get_deduction_proposals=lambda **_: [row])
    handler=object.__new__(StrictRecordsRequestHandler)
    handler.path='/api/device/extension_penalties'
    handler.headers={'X-Device-Id':'android-test','X-Device-Token':'fixture-token'}
    handler.db=db
    handler._get_authenticated_device_account=lambda *_:'user_lok'
    handler._send_json=lambda value,**kwargs:replies.append((value,kwargs))
    registry=SimpleNamespace(owns_verified_proposal=lambda _:True)
    with patch('windows.browser_extension_monitor.get_browser_extension_monitor',return_value=registry):
        handler.do_GET()
        envelope=replies[-1][0]
        assert hmac.compare_digest(envelope['signature'],hmac.new(b'fixture-token',envelope['payload_json'].encode(),hashlib.sha256).hexdigest())
        assert len(json.loads(envelope['payload_json'])['proposals'])==1
        registry.owns_verified_proposal=lambda _:False
        handler.do_GET()
        assert not json.loads(replies[-1][0]['payload_json'])['proposals']
