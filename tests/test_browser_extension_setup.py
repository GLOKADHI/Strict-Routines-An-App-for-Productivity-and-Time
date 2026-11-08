import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from windows.browser_setup import BrowserExtensionSetup, detect_browsers, executable_from_command
from core.server import StrictRecordsRequestHandler


def fixture(tmp_path):
    candidates=[]
    for name,exe in [('Google Chrome','chrome.exe'),('Comet','comet.exe'),('Mozilla Firefox','firefox.exe')]:
        path=tmp_path/name/exe;path.parent.mkdir();path.write_bytes(b'fixture - not executable')
        candidates.append((name,path))
    candidates.append(candidates[0])
    candidates.append(('Stale browser',tmp_path/'removed.exe'))
    records=detect_browsers(candidates)
    launched=[]
    setup=BrowserExtensionSetup(storage=tmp_path/'persistent',detector=lambda:records,launcher=launched.append)
    return setup,records,launched


def test_discovery_deduplicates_executables_and_rejects_stale_entries(tmp_path):
    setup,rows,_=fixture(tmp_path)
    assert len(rows)==3
    assert {b['key'] for b in rows}=={'chrome','comet','firefox'}
    assert next(b for b in rows if b['key']=='firefox')['supported'] is False
    assert all(not b['connected'] for b in setup.status()['browsers'])


def test_windows_command_parser_ignores_registered_arguments():
    assert str(executable_from_command('"C:\\Apps\\My Browser\\chrome.exe" --flag "%1"'))=='C:\\Apps\\My Browser\\chrome.exe'
    assert str(executable_from_command('C:\\Apps\\Chrome\\chrome.exe --flag'))=='C:\\Apps\\Chrome\\chrome.exe'
    assert executable_from_command('powershell -Command arbitrary') is None


def test_one_action_prepares_separate_browser_copies_and_fixed_manager_urls(tmp_path):
    setup,rows,launched=fixture(tmp_path)
    result=setup.setup()
    assert result['success']
    assert len(launched)==2
    assert {args[1] for args in launched}=={'chrome://extensions/'}
    assert all(len(args)==2 for args in launched)
    for browser in result['browsers']:
        assert browser['connected'] is False
        if not browser['supported']: continue
        folder=Path(browser['folder'])
        assert folder.is_relative_to(tmp_path/'persistent')
        config=json.loads((folder/'setup.json').read_text())
        assert config['browser_key']==browser['key']
        assert config['installation_id']==browser['id']
        manifest=json.loads((folder/'manifest.json').read_text())
        assert (folder/manifest['action']['default_popup']).is_file()
        assert (folder/'setup-config.js').is_file()
        assert {'tabs','webNavigation','storage','alarms','nativeMessaging'} <= set(manifest['permissions'])
        assert browser['state']=='AWAITING_CONFIRMATION'
    assert not setup.setup()['success']  # double click does not launch twice


def test_status_requires_matching_recent_installation_heartbeat(tmp_path):
    setup,rows,_=fixture(tmp_path);setup.setup()
    browser=next(b for b in rows if b['key']=='comet')
    assert not setup.note_heartbeat(browser['id'],'chrome','1.2.0')
    assert not setup.note_heartbeat('../outside','comet','1.2.0')
    assert setup.note_heartbeat(browser['id'],'comet','1.2.0')
    statuses={b['key']:b for b in setup.status()['browsers']}
    assert statuses['comet']['connected'] and not statuses['chrome']['connected']
    setup.live[browser['id']]=(1,'1.2.0')
    assert not next(b for b in setup.status()['browsers'] if b['key']=='comet')['connected']


def test_arbitrary_paths_and_unsupported_browser_never_launch(tmp_path):
    setup,rows,launched=fixture(tmp_path)
    with pytest.raises(ValueError):setup.setup(['../../evil.exe'])
    firefox=next(b for b in rows if b['key']=='firefox')
    assert not setup.setup([firefox['id']])['success']
    assert not launched
    with pytest.raises(ValueError):setup.open_folder('../../')


@pytest.mark.parametrize('client,host,origin,allowed',[
    ('127.0.0.1','127.0.0.1:8765','http://127.0.0.1:8765',True),
    ('192.168.1.4','127.0.0.1:8765','http://127.0.0.1:8765',False),
    ('127.0.0.1','evil.example:8765','http://evil.example:8765',False),
    ('127.0.0.1','127.0.0.1:8765','https://evil.example',False),
    ('127.0.0.1','127.0.0.1:8765','null',False),
    ('127.0.0.1','127.0.0.1:8765','http://127.0.0.1:9999',False),
])
def test_setup_mutations_only_accept_local_dashboard(client,host,origin,allowed):
    h=object.__new__(StrictRecordsRequestHandler)
    h.client_address=(client,1234)
    h.headers={'Host':host,'Origin':origin,'X-StrictRoutines-Setup':'1'}
    h._send_json=lambda *args,**kwargs:None
    assert h._browser_setup_allowed(mutation=True) is allowed


def test_route_uses_detected_ids_and_rejects_invalid_payload(tmp_path):
    setup,rows,launched=fixture(tmp_path)
    h=object.__new__(StrictRecordsRequestHandler)
    h.client_address=('127.0.0.1',1234)
    raw=json.dumps({'browser_ids':[next(b for b in rows if b['key']=='comet')['id']]}).encode()
    h.headers={'Host':'127.0.0.1:8765','X-StrictRoutines-Setup':'1','Content-Length':str(len(raw))}
    h.rfile=io.BytesIO(raw)
    replies=[];h._send_json=lambda data,**kwargs:replies.append((data,kwargs))
    with patch('windows.browser_setup.get_browser_extension_setup',return_value=setup):
        h._handle_browser_setup('/api/browser_extensions/setup')
    assert replies[-1][0]['success'] and len(launched)==1
    assert launched[0][0].endswith('comet.exe')
