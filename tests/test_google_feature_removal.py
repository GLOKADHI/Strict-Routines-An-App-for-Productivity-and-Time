"""Removed sign-in endpoints must remain inactive, including old client callbacks."""
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from core.server import StrictRecordsRequestHandler
from core.ui_dashboard import DASHBOARD_HTML

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def local_server():
    class Handler(StrictRecordsRequestHandler):
        pass
    Handler.db=MagicMock()
    Handler.google_auth=MagicMock()
    Handler.cloud_sync_mgr=MagicMock()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    session=requests.Session();session.trust_env=False
    try:yield f'http://127.0.0.1:{server.server_port}',session,Handler
    finally:session.close();server.shutdown();server.server_close();worker.join(timeout=3)

@pytest.mark.parametrize('path',['/api/google/auth/start','/api/google/drive/grant','/api/google/sync/trigger',
    '/api/google/account/device_connect','/api/google/auth/configure','/api/releases/stage_windows'])
def test_old_actions_cannot_authorize_or_upload(local_server,path):
    base,client,handler=local_server
    response=client.post(base+path,json={'confirmed':True},timeout=5)
    assert response.status_code==410
    assert response.json()['code']=='FEATURE_REMOVED'
    assert not handler.google_auth.mock_calls and not handler.cloud_sync_mgr.mock_calls
    assert not handler.db.mock_calls

@pytest.mark.parametrize('path',['/api/google/auth/status','/api/google/account/identity','/api/google/drive/folder',
    '/callback?code=fixture&state=fixture','/oauth2callback?code=fixture','/api/releases/check_windows'])
def test_old_status_and_callback_routes_are_retired(local_server,path):
    base,client,handler=local_server
    response=client.get(base+path,timeout=5)
    assert response.status_code==410
    assert not handler.google_auth.mock_calls and not handler.cloud_sync_mgr.mock_calls

def test_no_account_controls_or_polling_in_dashboard():
    for value in ['/api/google/','settingsGoogleDriveCard','topbarGooglePill','triggerGoogleSync','loadGoogleIdentity','Sign in with Google']:
        assert value not in DASHBOARD_HTML
    assert 'registeredDevicesList' in DASHBOARD_HTML and '/api/device/list' in DASHBOARD_HTML

def test_android_has_no_oauth_sources_sdk_or_callback():
    app=ROOT/'android/app'
    source=(app/'src/main/java/com/strictrecords/app/MainActivity.kt').read_text(encoding='utf-8')
    for value in ['GoogleAuthManager','GoogleDriveClient','CloudSyncEngine','renderGoogleCloudStorageCard','startGoogleConnectFlow']:
        assert value not in source
    gradle=(app/'build.gradle').read_text()
    for value in ['androidx.credentials','identity.googleid','play-services-auth']:
        assert value not in gradle
    assert 'android:scheme="com.strictrecords.app"' not in (app/'src/main/AndroidManifest.xml').read_text()
    assert 'com.google.mlkit:face-detection' in gradle
    updater=(app/'src/main/java/com/strictrecords/app/AppUpdateManager.kt').read_text()
    assert '/api/releases/latest?platform=ANDROID' in updater and 'GoogleAuthManager' not in updater

def test_windows_starts_no_google_worker_and_excludes_runtime():
    source=(ROOT/'core/server.py').read_text(encoding='utf-8')
    assert 'from core.google_auth' not in source and 'cs_mgr.start()' not in source
    spec=(ROOT/'StrictRecordsApp.spec').read_text()
    for name in ['core.google_auth','core.google_drive','core.cloud_sync_manager','windows.google_connection_check']:
        assert name in spec
