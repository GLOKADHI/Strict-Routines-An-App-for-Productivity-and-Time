import copy
import hashlib
import hmac
import json
import socket
import struct
import time
from unittest.mock import Mock

import pytest

from core import bluetooth_protocol as wire
from core.android_ledger import checkpoint
from core.bluetooth_transfer import BluetoothTransferManager
from core.database import StrictRecordsDB
from tests.bluetooth_fakes import FakeBackend, PEER

DATE = '2026-10-18'
DEVICE = 'android-software-fixture'


def package(events=None, revision=1):
    events = events if events is not None else [{'event_id': 'sleep-4', 'date': DATE, 'is_dev_mode': False,
        'event_type': 'MI_FITNESS_SLEEP_BONUS', 'timestamp': DATE+'T09:15:00+05:30',
        'description': 'Verified sleep bonus', 'points': 4, 'eligible': True, 'audit': {'evidence_id':'sleep-proof'}}]
    raw_points=sum(e['points'] for e in events if e['eligible'])
    ledger={'schema':1,'account_id':'user_lok','device_id':DEVICE,'date':DATE,'timezone':'Asia/Kolkata',
            'revision':revision,'event_count':len(events),'is_dev_mode':False,'score_events':events,
            'confirmed_score':min(100,raw_points),'raw_verified_points':raw_points}
    raw=json.dumps(ledger,ensure_ascii=False,separators=(',',':'))
    return {'transfer_version':2,'account_id':'user_lok','device_id':DEVICE,'date':DATE,
            'ledger_snapshot':{'payload_json':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()},
            'proof_reviews':[],'scheduled_activities':[]}


@pytest.fixture
def transfer(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'transfer.sqlite'))
    manager=BluetoothTransferManager(db, backend_factory=FakeBackend)
    yield db,manager
    manager.cancel_session()


def begin(manager):
    session=manager.start_session(peer_address=PEER)
    assert session['success']
    return session


def do_import(manager, payload, code=None, **kwargs):
    session=begin(manager)
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    return manager.import_payload(code or session['session_code'], payload,
        hashlib.sha256(raw.encode()).hexdigest(),DEVICE,raw,peer_address=PEER,
        session_id=session['session_id'],declared_count=wire.package_count(payload),**kwargs)


def test_peer_selection_is_required_without_opening_receiver(transfer):
    _,m=transfer
    assert not m.start_session()['success']
    assert not m.start_session(peer_address='AA:BB:CC:DD:EE:99')['success']
    assert m._backend is None


def test_disabled_receiver_fails_truthfully(transfer):
    _,m=transfer
    m.backend_factory=type('Disabled',(FakeBackend,),{'capability':staticmethod(lambda:(False,'Bluetooth disabled'))})
    assert m.start_session(peer_address=PEER)['code']=='BLUETOOTH_UNAVAILABLE'
    assert not m.get_status()['active_session']


def test_failed_advertisement_does_not_claim_active_session(transfer):
    _,m=transfer
    def fail(self): raise OSError(10013,'Advertisement denied')
    m.backend_factory=type('Denied',(FakeBackend,),{'listen':fail})
    result=m.start_session(peer_address=PEER)
    assert result['code']=='RECEIVER_UNAVAILABLE'
    assert not m.get_status()['active_session']


def test_small_package_and_duplicate_are_truthful(transfer):
    db,m=transfer
    first=do_import(m,package())
    assert first['success'] and first['confirmed_score']==4
    assert first['transferred_count']==1 and first['already_present_count']==0
    second=do_import(m,package())
    assert second['success'] and second['transferred_count']==0 and second['already_present_count']==1
    assert checkpoint(db,DATE)['confirmed_balance']==4


def metadata(payload):
    payload['proof_reviews']=[{'evidence_id':'pending-proof','date':DATE,'task_code':'HOURLY','status':'PENDING',
                               'points_awarded':0,'submitted_at':DATE+'T10:01:00+05:30'}]
    payload['scheduled_activities']=[{'activity_id':'hourly-10','date':DATE,'type':'HOURLY','title':'Hourly checkpoint',
        'scheduled_time':DATE+'T10:00:00+05:30','window_start':DATE+'T09:50:00+05:30',
        'window_end':DATE+'T10:10:00+05:30','notification_time':DATE+'T09:45:00+05:30',
        'schedule_id':'schedule-one','schedule_version':1,'status':'SUBMITTED'}]
    return payload


def test_multiple_records_metadata_and_durable_restart_receipt(transfer):
    db,m=transfer
    result=do_import(m,metadata(package()))
    assert result['success'] and result['total_count']==3 and result['transferred_count']==3
    m.cancel_session()
    restored=BluetoothTransferManager(db,backend_factory=FakeBackend)
    assert restored.get_status()['last_receipt']['package_id']==result['package_id']
    assert do_import(restored,metadata(package()))['already_present_count']==3
    restored.cancel_session()


def test_metadata_failure_rolls_back_ledger_and_receipt(transfer):
    db,m=transfer
    payload=package();payload['proof_reviews']=[{'evidence_id':'missing-date'}]
    result=do_import(m,payload)
    assert not result['success'] and result['transferred_count']==0
    with db._connect() as c:
        assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='system_config'").fetchone():
            assert c.execute("SELECT count(*) FROM system_config WHERE key='bluetooth_last_receipt_v2'").fetchone()[0]==0


@pytest.mark.parametrize('missing_date', [None, ''])
@pytest.mark.parametrize('submitted_at', [DATE+'T10:01:00+05:30', DATE+'T04:31:00Z', DATE+'T10:01:00'])
def test_legacy_android_review_date_is_recovered_from_same_reporting_day(transfer, missing_date, submitted_at):
    db, manager = transfer
    payload = metadata(package())
    review = payload['proof_reviews'][0]
    review['submitted_at'] = submitted_at
    if missing_date is None:
        review.pop('date')
    else:
        review['date'] = missing_date
    result = do_import(manager, payload)
    assert result['success'] and result['transferred_count'] == 3
    with db._connect() as conn:
        stored = conn.execute("SELECT date,user_id,status,points_awarded FROM proof_reviews WHERE evidence_id='pending-proof'").fetchone()
    assert tuple(stored) == (DATE, 'user_lok', 'PENDING', 0)
    assert do_import(manager, payload)['already_present_count'] == 3


@pytest.mark.parametrize('changes', [
    {'date': '', 'submitted_at': '2026-10-17T10:00:00+05:30'},
    {'date': '', 'submitted_at': '2026-10-18T20:00:00Z'},  # Next reporting day in India.
    {'date': '2026-10-17'},
    {'date': '', 'submitted_at': 'not-a-timestamp'},
    {'date': '', 'submitted_at': None},
    {'date': '', 'submitted_at': DATE},
    {'date': '', 'user_id': 'different-account'},
])
def test_review_date_compatibility_does_not_relax_scope_or_atomicity(transfer, changes):
    db, manager = transfer
    payload = metadata(package())
    payload['proof_reviews'][0].update(changes)
    result = do_import(manager, payload)
    assert not result['success'] and result['transferred_count'] == 0
    with db._connect() as conn:
        assert conn.execute('SELECT count(*) FROM score_events').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM proof_reviews').fetchone()[0] == 0


def test_review_claim_cannot_turn_pending_into_approval(transfer):
    db,m=transfer
    assert do_import(m,metadata(package()))['success']
    payload=metadata(package());payload['proof_reviews'][0].update(status='APPROVED',reviewed_by='claimed-admin',points_awarded=8)
    result=do_import(m,payload)
    assert not result['success'] and result['conflict_count']==1
    with db._connect() as c:
        assert c.execute("SELECT status FROM proof_reviews WHERE evidence_id='pending-proof'").fetchone()[0]=='PENDING'


def test_negative_human_claim_is_not_received_as_approval(transfer):
    db,m=transfer
    assert do_import(m,package())['success']
    ev=json.loads(package()['ledger_snapshot']['payload_json'])['score_events']
    ev.append(dict(ev[0],event_id='forged-loss',event_type='PENALTY',points=-20,
        audit={'approval_source':'ANDROID_HUMAN_CONFIRMATION','approved_by':'Claimed human'}))
    result=do_import(m,package(ev,revision=2))
    assert not result['success'] and result['conflict_count']==1
    assert checkpoint(db,DATE)['confirmed_balance']==4
    with db._connect() as c:
        assert not c.execute("SELECT 1 FROM score_events WHERE event_id='forged-loss'").fetchone()


def test_stale_checkpoint_and_conflicting_identity_do_not_overwrite(transfer):
    db,m=transfer
    assert do_import(m,package(revision=2))['success']
    assert not do_import(m,package(revision=1))['success']
    modified=package(revision=3); raw=json.loads(modified['ledger_snapshot']['payload_json']);raw['score_events'][0]['description']='Changed original reason'
    encoded=json.dumps(raw);modified['ledger_snapshot']={'payload_json':encoded,'sha256':hashlib.sha256(encoded.encode()).hexdigest()}
    assert not do_import(m,modified)['success']
    assert checkpoint(db,DATE)['confirmed_balance']==4


@pytest.mark.parametrize('change',[lambda p:p.update(transfer_version=99),lambda p:p.update(account_id='other-account'),lambda p:p.update(device_id='other-device')])
def test_incompatible_or_wrong_identity_is_rejected(transfer,change):
    db,m=transfer;p=package();change(p)
    assert not do_import(m,p)['success']
    with db._connect() as c:assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0


def test_wrong_code_wrong_peer_and_corrupt_bytes(transfer):
    db,m=transfer;session=begin(m);payload=package();raw=json.dumps(payload);digest=hashlib.sha256(raw.encode()).hexdigest()
    wrong=m.import_payload('000000',payload,digest,DEVICE,raw,peer_address=PEER)
    assert not wrong['success'] and session['session_code'] not in json.dumps(wrong)
    assert not m.import_payload(session['session_code'],payload,digest,DEVICE,raw,peer_address='AA:BB:CC:DD:EE:99')['success']
    assert not m.import_payload(session['session_code'],payload,'0'*64,DEVICE,raw,peer_address=PEER)['success']


def connect_client(manager,payload,bad_auth=False,partial=False):
    client,server=socket.socketpair();client.settimeout(5)
    manager._backend.listener.connections.put((server,(PEER,1)))
    hello=wire.read_frame(client)
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
    digest=hashlib.sha256(raw).hexdigest();count=wire.package_count(payload)
    text=wire.auth_text(hello['session_id'],hello['challenge'],digest,DEVICE,'user_lok',len(raw),count)
    code=manager._active_session['session_code']
    wire.send_frame(client,dict(protocol=wire.PROTOCOL,version=2,session_id=hello['session_id'],package_id=digest,
        device_id=DEVICE,account_id='user_lok',payload_length=len(raw),record_count=count,
        authenticator='wrong' if bad_auth else wire.mac(code,text)))
    if bad_auth:return client
    assert wire.read_frame(client)['state']=='AUTHENTICATED'
    client.sendall(raw[:len(raw)//2] if partial else raw)
    return client


def test_actual_bytes_authentication_commit_and_cleanup_on_simulated_stream(transfer):
    db,m=transfer;session=begin(m)
    client=connect_client(m,metadata(package()))
    receipt=wire.read_frame(client);client.close()
    assert receipt['success'] and receipt['total_count']==3
    assert hmac.compare_digest(receipt['receipt_mac'],wire.mac(session['session_code'],wire.receipt_text(receipt)))
    for _ in range(100):
        if not m.get_status()['active_session']:break
        time.sleep(.01)
    assert not m.get_status()['active_session']
    assert checkpoint(db,DATE)['confirmed_balance']==4


def test_interruption_does_not_import_partial_package_and_retry_keeps_ids(transfer):
    db,m=transfer;begin(m)
    client=connect_client(m,package(),partial=True);client.close()
    time.sleep(.1)
    with db._connect() as c:assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0
    retry=connect_client(m,package());assert wire.read_frame(retry)['success'];retry.close()
    with db._connect() as c:assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==1


def test_bad_authentication_and_cancellation_release_only_transfer_resources(transfer):
    db,m=transfer;begin(m)
    unrelated=Mock()
    m.engine=unrelated
    client=connect_client(m,package(),bad_auth=True)
    assert client.recv(1)==b'';client.close()
    backend=m._backend
    m.cancel_session()
    assert backend.closed and not m.get_status()['active_session']
    assert unrelated.mock_calls==[]
    with db._connect() as c:assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0


def test_unsupported_frame_truncated_json_and_limits():
    left,right=socket.socketpair()
    try:
        right.sendall(struct.pack('!I',wire.MAX_FRAME_BYTES+1))
        with pytest.raises(ValueError):wire.read_frame(left)
    finally:left.close();right.close()
    with pytest.raises(ValueError):wire.strict_json('{"a":1,"a":2}')
    with pytest.raises(ValueError):wire.strict_json('{"a":NaN}')


def test_native_sockaddr_matches_installed_windows_sdk_packing():
    import ctypes
    from windows.bluetooth_rfcomm import SockaddrBth,address_string,normalize_address
    assert ctypes.sizeof(SockaddrBth)==30 and SockaddrBth.address.offset==2 and SockaddrBth.port.offset==26
    assert address_string(0xAABBCCDDEE01)==PEER
    assert normalize_address(PEER.lower())==PEER


def test_large_package_at_record_limit_and_one_over_limit(transfer):
    db,m=transfer
    base=json.loads(package()['ledger_snapshot']['payload_json'])['score_events'][0]
    events=[dict(base,event_id=f'large-{i}',points=0) for i in range(wire.MAX_RECORDS)]
    receipt=do_import(m,package(events))
    assert receipt['success'] and receipt['total_count']==10000 and receipt['transferred_count']==10000
    result=do_import(m,package(events+[dict(base,event_id='over-limit',points=0)],revision=2))
    assert not result['success'] and result['transferred_count']==0
    with db._connect() as c:assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==10000


def test_payload_byte_limit_is_checked_before_import(transfer):
    _,m=transfer
    value=package();value['oversized_field']='x'*wire.MAX_TRANSFER_BYTES
    result=do_import(m,value)
    assert not result['success'] and 'limit' in result['error']


def test_pending_loss_is_never_a_confirmed_deduction(transfer):
    db,m=transfer
    base=json.loads(package()['ledger_snapshot']['payload_json'])['score_events'][0]
    pending=dict(base,event_id='pending-loss',event_type='ENTERTAINMENT_VIOLATION',points=-20,eligible=False,audit={})
    result=do_import(m,package([pending]))
    assert result['success'] and result['confirmed_score']==0
    assert checkpoint(db,DATE)['eligible_points'].get('pending-loss',0)==0


def test_retained_human_approval_cannot_be_reused_for_another_deduction(transfer):
    db,m=transfer
    db.create_deduction_proposal(proposal_id='approved-proposal',date=DATE,timestamp=DATE+'T10:00:00+05:30',originating_device=DEVICE,event_type='ENTERTAINMENT_VIOLATION',proposed_points=-4,reason='Retained authorized approval')
    with db._connect() as c:
        c.execute("UPDATE deduction_proposals SET status='APPROVED',approved_by='human',approved_at=? WHERE proposal_id='approved-proposal'",(DATE+'T10:00:00+05:30',))
    base=json.loads(package()['ledger_snapshot']['payload_json'])['score_events'][0]
    loss=dict(base,event_id='approved-loss',event_type='ENTERTAINMENT_VIOLATION',points=-4,
        audit={'proposal_id':'approved-proposal','approved_by':'human','approval_source':'ANDROID_HUMAN_CONFIRMATION'})
    receipt=do_import(m,package([loss]));assert receipt['success'] and receipt['confirmed_score']==-4
    result=do_import(m,package([loss,dict(loss,event_id='reused-approval')],revision=2))
    assert not result['success'] and result['conflict_count']==1
    assert checkpoint(db,DATE)['confirmed_balance']==-4


def test_metadata_does_not_finalize_an_unrelated_task(transfer):
    db,m=transfer
    value=metadata(package());value['scheduled_activities'][0].update(status='VERIFIED',points=50)
    assert not do_import(m,value)['success']
    with db._connect() as c:
        assert c.execute('SELECT count(*) FROM scheduled_activities').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0


def test_ack_lost_retry_preserves_newer_local_task_state(transfer):
    db,m=transfer;value=metadata(package())
    assert do_import(m,value)['success']
    with db._connect() as c:c.execute("UPDATE scheduled_activities SET status='VERIFIED' WHERE activity_id='hourly-10'")
    result=do_import(m,value)
    assert result['success'] and result['transferred_count']==0 and result['already_present_count']==3
    with db._connect() as c:assert c.execute("SELECT status FROM scheduled_activities WHERE activity_id='hourly-10'").fetchone()[0]=='VERIFIED'


def test_expiry_closes_receiver_and_removes_code(transfer):
    from datetime import datetime,timedelta,timezone
    _,m=transfer;session=begin(m);backend=m._backend
    m._active_session['expires_at']=datetime.now(timezone.utc)-timedelta(seconds=1)
    assert not m.get_status()['active_session'] and backend.closed
    assert 'session_code' not in m.get_status()
    assert not m.import_payload(session['session_code'],package(),peer_address=PEER)['success']


def test_cancellation_during_metadata_import_rolls_back_everything(transfer,monkeypatch):
    db,m=transfer
    original=m._merge_metadata
    def cancel(conn,payload,manifest):
        counts=original(conn,payload,manifest);m._stop_listener.set();return counts
    monkeypatch.setattr(m,'_merge_metadata',cancel)
    assert not do_import(m,metadata(package()))['success']
    with db._connect() as c:
        assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM proof_reviews').fetchone()[0]==0


def test_task_update_cannot_inflate_its_committed_award(transfer):
    db,m=transfer
    base=json.loads(package()['ledger_snapshot']['payload_json'])['score_events'][0]
    events=[dict(base,related_task_id='hourly-10')]
    assert do_import(m,metadata(package(events)))['success']
    value=metadata(package(events,revision=2))
    value['scheduled_activities'][0].update(status='VERIFIED',points=999)
    result=do_import(m,value)
    assert not result['success'] and result['conflict_count']==1
    with db._connect() as c:
        row=c.execute("SELECT status,points FROM scheduled_activities WHERE activity_id='hourly-10'").fetchone()
        assert tuple(row)==('SUBMITTED',0)


def test_correction_preserves_original_parent_identity(transfer):
    db,m=transfer
    base=json.loads(package()['ledger_snapshot']['payload_json'])['score_events'][0]
    correction=dict(base,event_id='linked-correction',event_type='CORRECTION',points=0,parent_event_id=base['event_id'])
    result=do_import(m,package([base,correction]))
    assert result['success'] and result['total_count']==2
    with db._connect() as c:
        assert c.execute("SELECT parent_event_id FROM score_events WHERE event_id='linked-correction'").fetchone()[0]==base['event_id']
    assert do_import(m,package([base,correction]))['already_present_count']==2


def test_correction_with_missing_original_is_rejected_atomically(transfer):
    db,m=transfer
    base=json.loads(package()['ledger_snapshot']['payload_json'])['score_events'][0]
    invalid=dict(base,event_id='orphan-correction',points=0,parent_event_id='missing-original')
    assert not do_import(m,package([invalid]))['success']
    with db._connect() as c:assert c.execute('SELECT count(*) FROM score_events').fetchone()[0]==0


def test_native_advertisement_arguments_and_cleanup_without_using_radio(monkeypatch):
    import ctypes as C
    from windows import bluetooth_rfcomm as native
    calls=[]
    class Socket:
        def setsockopt(self,*args):calls.append(('option',args[:2]))
        def fileno(self):return 42
        def listen(self,backlog):calls.append(('listen',backlog))
        def settimeout(self,seconds):pass
        def close(self):calls.append(('closed',))
    class Api:
        def bind(self,handle,pointer,size):
            address=C.cast(pointer,C.POINTER(native.SockaddrBth)).contents
            assert size==30 and address.family==32 and address.port==0xFFFFFFFF
            return 0
        def getsockname(self,handle,pointer,size):
            C.cast(pointer,C.POINTER(native.SockaddrBth)).contents.port=7
            return 0
        def WSASetServiceW(self,pointer,operation,flags):
            query=C.cast(pointer,C.POINTER(native.QuerySet)).contents
            assert query.namespace==16 and query.address_count==1
            address=C.cast(query.addresses.contents.local.address,C.POINTER(native.SockaddrBth)).contents
            assert address.port==7
            calls.append(('service',operation));return 0
    backend=native.WindowsRfcomm.__new__(native.WindowsRfcomm)
    backend.ws=Api();backend.socket=None;backend._registration=None
    monkeypatch.setattr(native.socket,'socket',lambda *args:Socket())
    backend.listen();backend.close()
    assert ('option',(3,-2147483647)) in calls and ('option',(3,2)) in calls
    assert ('service',0) in calls and ('service',2) in calls and ('closed',) in calls


@pytest.mark.parametrize('client,origin,allowed',[
    ('127.0.0.1','',True),('127.0.0.1','http://127.0.0.1:8765',True),
    ('192.168.1.5','',False),('127.0.0.1','https://unrelated.example',False),
    ('127.0.0.1','http://localhost:bad',False),('127.0.0.1','null',False)])
def test_receiver_session_secrets_are_local_only(client,origin,allowed):
    from types import SimpleNamespace
    from core.server import StrictRecordsRequestHandler
    handler=SimpleNamespace(client_address=(client,1),headers={'Origin':origin},server=SimpleNamespace(server_address=('0.0.0.0',8765)),_send_json=Mock())
    assert StrictRecordsRequestHandler._authorize_local_bluetooth_control(handler) is allowed
    assert handler._send_json.called is (not allowed)


def test_bluetooth_transfer_carries_points_breakdown(transfer):
    db, m = transfer
    pkg = package()
    pkg['points_breakdown'] = {
        'wake_up': 10,
        'bath': 15,
        'hourly': 40,
        'gym': 20,
        'focus': 10,
        'penalties': -5,
        'confirmed_score': 90
    }
    receipt = do_import(m, pkg)
    assert receipt['success']
    assert 'points_breakdown' in receipt
    assert receipt['points_breakdown']['wake_up'] == 10
    assert receipt['points_breakdown']['gym'] == 20
    status = m.get_status()
    assert status['last_receipt']['points_breakdown']['hourly'] == 40
