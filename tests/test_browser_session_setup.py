"""Browser-specific setup, profile routing and native Strict Session integration."""
import io
import json
from pathlib import Path
from unittest.mock import patch
import uuid

import pytest
from windows.browser_setup import BROWSERS, SUPPORTED, detect_browsers
from windows.browser_monitor_registry import BrowserMonitorRegistry
from core.server import StrictRecordsRequestHandler


@pytest.fixture
def fixture(tmp_path):
    browsers=[]
    for key,(name,executable,_) in BROWSERS.items():
        path=tmp_path/name/executable
        path.parent.mkdir();path.write_bytes(b'not executable; software fixture')
        browsers.append((name,path))
    detected=detect_browsers(browsers)
    calls=[]
    registry=BrowserMonitorRegistry(storage=tmp_path/'monitor',detector=lambda:detected,launcher=calls.append)
    return registry,detected,calls


@pytest.mark.parametrize('key',sorted(SUPPORTED))
def test_each_browser_has_separate_complete_configured_folders(fixture,key):
    registry,detected,calls=fixture
    browser=next(b for b in detected if b['key']==key)
    result=registry.prepare_browser(browser['id'],'Work profile')
    assert result['success'] and not calls
    for folder in ('target_folder','helper_folder'):
        manifest=json.loads((Path(result[folder])/'manifest.json').read_text())
        assert manifest['manifest_version']==3
        assert BROWSERS[key][0] in manifest['name']
        assert (Path(result[folder])/manifest['background']['service_worker']).is_file()
    config=(Path(result['target_folder'])/'setup-config.js').read_text()
    assert f'"browser_key": "{key}"' in config
    identity=result['installation_id']
    enrollment=registry.enrollments[identity]
    assert enrollment['browser_id']==browser['id'] and enrollment['browser_key']==key
    assert enrollment['token'] not in json.dumps(registry.get_ui_status())
    registry.open_manager(browser['id'])
    assert calls[-1]==[browser['executable'],BROWSERS[key][2]]
    registry.open_folder(identity,'helper')
    assert calls[-1][1]==result['helper_folder']


def test_update_and_another_profile_never_replace_enrollment_or_timer(fixture):
    registry,detected,_=fixture
    browser=next(b for b in detected if b['key']=='edge')
    first=registry.prepare_browser(browser['id'],'Default')
    monitor=registry.monitors[first['installation_id']]
    enrollment=monitor.enrollment_file.read_bytes()
    monitor.recovery_hold=True;monitor._save_state()
    state=Path(monitor.state_file).read_bytes()
    second=registry.prepare_browser(browser['id'],'Default')
    assert second['installation_id']==first['installation_id']
    assert monitor.enrollment_file.read_bytes()==enrollment
    assert Path(monitor.state_file).read_bytes()==state
    other=registry.prepare_browser(browser['id'],'Personal')
    assert other['installation_id']!=first['installation_id']
    assert len(registry.monitors)==2


def test_status_lists_catalogue_and_unsupported_firefox_without_install_claims(fixture):
    registry,detected,_=fixture
    rows=registry.get_ui_status()['browsers']
    assert {r['key'] for r in rows}==set(BROWSERS)
    firefox=next(r for r in rows if r['key']=='firefox')
    assert firefox['installed'] and not firefox['supported']
    with pytest.raises(ValueError):registry.prepare_browser(firefox['id'],'Default')
    registry.detector=lambda:[]
    assert all(not row['installed'] for row in registry.get_ui_status()['browsers'])


def test_cross_browser_evidence_and_grace_settings_are_isolated(fixture):
    registry,detected,_=fixture
    registry._running=lambda _:True
    prepared=[registry.prepare_browser(next(b for b in detected if b['key']==key)['id'],'Default') for key in ('chrome','edge')]
    first,second=[r['installation_id'] for r in prepared]
    packets=[]
    for identity in (first,second):
        monitor=registry.monitors[identity];monitor.show_or_update_notification=lambda **_:None
        monitor._clock=lambda:1000;monitor._monotonic=lambda:100
        packet=dict(role='observer',installation_id=identity,state='ENABLED',operational=True,
                    helper_profile=str(uuid.uuid4()),target_profile=str(uuid.uuid4()),browser_session=str(uuid.uuid4()),sequence=1,observed_at_ms=1000000)
        enrollment=registry.enrollments[identity]
        packets.append(packet)
        assert registry.submit_observation(packet,'chrome-extension://'+enrollment['helper_id'],enrollment['token'])['success']
    wrong=registry.enrollments[second]
    assert not registry.submit_observation(dict(packets[0],sequence=2),'chrome-extension://'+wrong['helper_id'],wrong['token'])['success']
    registry.set_grace_duration_minutes(10,first)
    assert registry.get_grace_duration_minutes(first)==10 and registry.get_grace_duration_minutes(second)==5
    with pytest.raises(ValueError):registry.set_grace_duration_minutes(7)
    assert {p['state'] for p in registry.get_ui_status()['installations']}=={'ENABLED'}


def test_legacy_chrome_enrollment_is_preserved(tmp_path):
    from prepare_chrome_monitor import prepare
    storage=tmp_path/'legacy'
    public=prepare(tmp_path/'package',storage,'Old Chrome profile')
    before=(storage/'enrollment.json').read_bytes()
    registry=BrowserMonitorRegistry(storage=storage,detector=lambda:[])
    assert public['installation_id'] in registry.monitors
    assert (storage/'enrollment.json').read_bytes()==before


def test_invalid_paths_and_actions_never_launch(fixture):
    registry,_,calls=fixture
    with pytest.raises(ValueError):registry.open_manager('../../outside')
    with pytest.raises(ValueError):registry.open_folder('../../outside','helper')
    with pytest.raises(ValueError):registry.prepare_browser('../../outside','Default')
    assert not calls


def test_monitor_prepare_route_uses_detected_browser_and_never_changes_policy(fixture):
    registry,detected,calls=fixture
    browser=next(b for b in detected if b['key']=='brave')
    handler=object.__new__(StrictRecordsRequestHandler)
    handler.db=None;handler.client_address=('127.0.0.1',1234)
    payload=json.dumps(dict(browser_id=browser['id'],profile_label='Default')).encode()
    handler.headers={'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765','X-StrictRoutines-Setup':'1','Content-Length':str(len(payload))}
    handler.rfile=io.BytesIO(payload);responses=[]
    handler._send_json=lambda data,**kwargs:responses.append(data)
    with patch('windows.browser_extension_monitor.get_browser_extension_monitor',return_value=registry):
        handler._handle_browser_setup('/api/browser_extensions/monitor_prepare')
    assert responses[-1]['success'] and not calls


def test_ui_controls_are_in_strict_session_and_refresh_only_when_visible():
    from core.ui_dashboard import DASHBOARD_HTML
    section=DASHBOARD_HTML.split('id="page-strict-session"')[1].split('</section>')[0]
    assert 'strictBrowserMonitorPanel' in section and 'strictBrowserCards' in section
    assert 'refreshStrictBrowserConnections' in DASHBOARD_HTML
    assert "getElementById('page-strict-session')?.classList.contains('active')" in DASHBOARD_HTML
    assert "root.replaceChildren()" in DASHBOARD_HTML


def test_identity_change_does_not_overwrite_existing_prepared_pair(fixture,tmp_path):
    from windows.browser_monitor_package import refresh_package
    from shutil import copytree
    registry,detected,_=fixture
    browser=next(b for b in detected if b['key']=='edge')
    result=registry.prepare_browser(browser['id'],'Default')
    original=Path(result['target_folder'])/'setup-config.js'
    before=original.read_bytes()
    source=Path(__file__).resolve().parents[1]
    copytree(source/'browser_extension',tmp_path/'changed/browser_extension')
    copytree(source/'browser_extension_monitor',tmp_path/'changed/browser_extension_monitor')
    manifest=tmp_path/'changed/browser_extension/manifest.json'
    data=json.loads(manifest.read_text());data['key']=json.loads((tmp_path/'changed/browser_extension_monitor/manifest.json').read_text())['key']
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        refresh_package(original.parent.parent,registry.enrollments[result['installation_id']],tmp_path/'changed')
    assert original.read_bytes()==before
