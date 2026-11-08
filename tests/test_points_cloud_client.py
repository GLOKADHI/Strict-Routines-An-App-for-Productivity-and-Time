import json
import threading
from http.server import HTTPServer
from wsgiref.simple_server import WSGIRequestHandler,WSGIServer,make_server
from socketserver import ThreadingMixIn
from unittest.mock import MagicMock
import pytest
import requests
from core.android_ledger import reconcile
from core.database import StrictRecordsDB
from core.points_cloud_client import PointsCloudReplica
from cloud_points.protocol import sanitize_envelope
from cloud_points.service import Application
from test_cloud_points import envelope,principal,cloud

class ThreadedServer(ThreadingMixIn,WSGIServer):daemon_threads=True
class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):pass

def test_real_http_snapshot_delivery_and_local_transport_compatibility(cloud,tmp_path,monkeypatch):
    original=envelope();cloud.publish(principal(cloud),original)
    # This historical fixture must query its own reporting date, not today's
    # clock. Production continues selecting the actual current reporting date.
    monkeypatch.setattr('core.points_cloud_client.reporting_date',lambda:json.loads(original['payload_json'])['date'])
    db=StrictRecordsDB(str(tmp_path/'replica.db'))
    assert reconcile(db,original,'android')['accepted']
    monkeypatch.setenv('STRICTROUTINES_POINTS_TEST_HTTP','1')
    server=make_server('127.0.0.1',0,Application(cloud),server_class=ThreadedServer,handler_class=QuietHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    config={'endpoint':f'http://127.0.0.1:{server.server_port}','account_id':'user_lok','device_id':'windows','device_token':'token-windows'}
    client=requests.Session();client.trust_env=False
    replica=PointsCloudReplica(db,configuration=lambda:config,transport=client)
    try:
        replica.once();assert replica.status()['status']=='SYNCED'
        assert replica.status()['confirmed_balance']==4 and replica.status()['ledger_revision']==1
        # The unchanged full LAN envelope remains valid after a minimized cloud receipt.
        assert reconcile(db,original,'android')['accepted']
        replica.once();assert replica.status()['status']=='SYNCED'
        config['device_token']='expired-or-revoked';replica.once()
        assert replica.status()['status']=='AUTHENTICATION_REQUIRED'
        assert replica.status()['confirmed_balance']==4
    finally:server.shutdown();server.server_close();thread.join(timeout=3)
    config['device_token']='token-windows'
    replica.once()
    assert replica.status()['status']=='OFFLINE_OR_RECONCILIATION_REQUIRED'
    assert replica.status()['confirmed_balance']==4
    client.close()

def test_unconfigured_client_starts_no_thread_and_sends_nothing(tmp_path):
    transport=MagicMock();replica=PointsCloudReplica(StrictRecordsDB(str(tmp_path/'replica.db')),configuration=lambda:None,transport=transport)
    replica.start();replica.once();assert replica._thread is None
    assert replica.status()['status']=='NOT_CONFIGURED';assert not transport.mock_calls

def test_other_account_binding_never_sends_device_credentials(tmp_path):
    transport=MagicMock();replica=PointsCloudReplica(StrictRecordsDB(str(tmp_path/'replica.db')),configuration=lambda:{'account_id':'other'},transport=transport)
    replica.once();assert not transport.mock_calls
    assert replica.status()['status']=='OFFLINE_OR_RECONCILIATION_REQUIRED'

def test_minimized_transport_cannot_change_financial_history(tmp_path):
    db=StrictRecordsDB(str(tmp_path/'replica.db'));original=envelope()
    assert reconcile(db,original,'android')['accepted']
    modified=json.loads(original['payload_json']);modified['score_events'][0]['points']=8;modified['confirmed_score']=modified['raw_verified_points']=8
    import hashlib
    raw=json.dumps(modified);changed={'payload_json':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()}
    assert not reconcile(db,changed,'android',minimized_transport=True)['accepted']
