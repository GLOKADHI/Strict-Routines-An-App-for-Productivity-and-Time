"""Static package/permission checks; these do not prove a Chrome installation."""
import json
from pathlib import Path
import re

import pytest
from prepare_chrome_monitor import extension_id

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('folder', ['browser_extension','browser_extension_monitor'])
def test_mv3_manifest_files_and_minimum_version(folder):
    directory = ROOT/folder
    manifest = json.loads((directory/'manifest.json').read_text())
    assert manifest['manifest_version'] == 3 and int(manifest['minimum_chrome_version']) >= 120
    files = [manifest['background']['service_worker'],manifest['action']['default_popup']]
    files += list(manifest.get('icons',{}).values())
    for row in manifest.get('content_scripts',[]):
        files += row['js']
    for row in manifest.get('web_accessible_resources',[]):
        files += row['resources']
    for name in files:
        assert (directory/name).is_file() and (directory/name).stat().st_size
    for page in directory.glob('*.html'):
        for script in re.findall(r'<script[^>]+src="([^"]+)"',page.read_text(encoding='utf-8')):
            assert (directory/script).is_file()
    assert not set(manifest['permissions']) & {'cookies','history','debugger','webRequest','nativeMessaging'}
    assert '<all_urls>' not in manifest.get('host_permissions',[])
    assert all(not p.startswith('*://') for p in manifest.get('host_permissions',[]))


def test_helper_identity_and_target_privileged_pages_are_private():
    target = json.loads((ROOT/'browser_extension/manifest.json').read_text())
    helper = json.loads((ROOT/'browser_extension_monitor/manifest.json').read_text())
    assert extension_id(target) == 'iincmekgilffpjmmmcgloliadppioajc'
    assert target['externally_connectable'] == {'ids':[extension_id(helper)]}
    assert target['permissions'] == ['alarms','storage']
    assert helper['permissions'] == ['management','storage','alarms']
    assert helper['host_permissions'] == ['http://127.0.0.1:8765/*']
    assert target['web_accessible_resources'][0]['resources'] == ['strictbot.png']


def test_disabled_monitor_has_no_preference_or_global_heartbeat_evidence():
    source = (ROOT/'windows/browser_extension_monitor.py').read_text()
    assert 'Secure Preferences' not in source and 'setup.live' not in source
    assert 'INSERT OR IGNORE INTO score_events' not in source
    assert 'create_deduction_proposal' in source and 'hmac.compare_digest' in source
    helper = (ROOT/'browser_extension_monitor/service_worker.js').read_text()
    assert 'management.get(config.target_id)' in helper
    for forbidden in ('management.getAll','chrome.tabs','chrome.history','chrome.cookies','setInterval','setTimeout'):
        assert forbidden not in helper


def test_alarm_recovery_and_no_shipped_monitor_credentials():
    worker = (ROOT/'browser_extension/service_worker.js').read_text()
    assert 'chrome.alarms.get(HEARTBEAT_ALARM)' in worker
    assert 'maintainConnection().catch' in worker
    assert (ROOT/'browser_extension_monitor/setup-config.js').read_text().rstrip().endswith('= null;')
    assert (ROOT/'browser_extension/setup-config.js').read_text().rstrip().endswith('= {};')
