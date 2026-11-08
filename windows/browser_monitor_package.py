"""Prepare a user-confirmed Chromium profile. Never installs or changes a browser.

Run from this source distribution or the packaged companion. Each enrollment binds
one explicitly named browser profile. Tokens are generated locally, not shipped.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import uuid


def extension_id(manifest):
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    return ''.join(chr(97 + int(c, 16)) for c in digest)


def refresh_package(package, config, source=None):
    """Refresh a previously prepared pair without replacing enrollment or browser storage."""
    root = Path(source or getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    package = Path(package)
    for original,identity_key in [('browser_extension','target_id'),('browser_extension_monitor','helper_id')]:
        manifest=json.loads((root/original/'manifest.json').read_text(encoding='utf-8'))
        if extension_id(manifest) != config[identity_key]:
            raise ValueError('Extension identity changed; existing enrollment was preserved and setup refresh was refused.')
    for original, destination in [('browser_extension','StrictRoutines'),('browser_extension_monitor','StatusHelper')]:
        target = package/destination
        target.mkdir(parents=True,exist_ok=True)
        for path in (root/original).rglob('*'):
            if not path.is_file() or path.name == 'setup-config.js':
                continue
            raw = path.read_bytes()
            if path.name == 'manifest.json':
                manifest = json.loads(raw)
                name = config.get('browser_name',config.get('browser_key','chrome').title())
                manifest['name'] = ('StrictRoutines — ' if destination == 'StrictRoutines' else 'StrictRoutines Status Helper — ') + name
                raw = (json.dumps(manifest,indent=2)+'\n').encode('utf-8')
            relative=path.relative_to(root/original)
            (target/relative).parent.mkdir(parents=True,exist_ok=True)
            temp = target/(str(relative)+'.new')
            temp.write_bytes(raw)
            temp.replace(target/relative)
    key = config.get('browser_key','chrome')
    (package/'StrictRoutines/setup-config.js').write_text('globalThis.STRICTROUTINES_SETUP = ' +
        json.dumps(dict(browser_key=key,installation_id=config['installation_id'],monitor=config))+';\n',encoding='utf-8')
    (package/'StatusHelper/setup-config.js').write_text('globalThis.STRICTROUTINES_MONITOR = '+json.dumps(config)+';\n',encoding='utf-8')


def prepare(output, storage, profile_label, source=None, update_existing=False, browser_key='chrome', browser_id=''):
    from windows.browser_setup import BROWSERS, SUPPORTED
    if browser_key not in SUPPORTED:
        raise ValueError('This browser does not support the bundled Chromium extensions.')
    browser_name, _, manager_url = BROWSERS[browser_key]
    version_url = manager_url.replace('extensions/', 'version')
    root = Path(source or getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    output, storage = Path(output).resolve(), Path(storage).resolve()
    if not profile_label.strip() or len(profile_label) > 120:
        raise ValueError(f'Name the {browser_name} profile you will install into ({version_url} shows Profile Path).')
    if output.exists():
        raise ValueError('Choose a new output folder; an existing enrollment is never overwritten.')
    existing = storage / 'enrollment.json'
    if existing.exists() and not update_existing:
        raise ValueError('A profile is already enrolled. Use --update-existing with the same profile label, or follow recovery instructions.')
    target = json.loads((root / 'browser_extension/manifest.json').read_text(encoding='utf-8'))
    helper = json.loads((root / 'browser_extension_monitor/manifest.json').read_text(encoding='utf-8'))
    if update_existing:
        config = json.loads(existing.read_text(encoding='utf-8'))
        if (config.get('browser_key','chrome') != browser_key or config['profile_label'] != profile_label.strip() or config['target_id'] != extension_id(target)
                or config['helper_id'] != extension_id(helper)):
            raise ValueError('Existing profile label/extension identity differs; update refused.')
    else:
        config = dict(installation_id=uuid.uuid4().hex[:20], profile_label=profile_label.strip(),
                      token=secrets.token_urlsafe(32), target_id=extension_id(target), helper_id=extension_id(helper),
                      browser_key=browser_key, browser_id=browser_id, browser_name=browser_name)
    output.mkdir(parents=True)
    shutil.copytree(root / 'browser_extension', output / 'StrictRoutines')
    shutil.copytree(root / 'browser_extension_monitor', output / 'StatusHelper')
    refresh_package(output,config,root)
    (output / 'StrictRoutines/setup-config.js').write_text('globalThis.STRICTROUTINES_SETUP = ' +
        json.dumps(dict(browser_key=browser_key, installation_id=config['installation_id'], monitor=config)) + ';\n', encoding='utf-8')
    (output / 'StatusHelper/setup-config.js').write_text('globalThis.STRICTROUTINES_MONITOR = ' + json.dumps(config) + ';\n', encoding='utf-8')
    (output / 'INSTALL.txt').write_text(
        f'Chosen {browser_name} profile: {profile_label}\nTarget ID: {config["target_id"]}\nHelper ID: {config["helper_id"]}\n'
        f'Start the updated StrictRoutines Windows companion. In the chosen browser profile, use {version_url} to confirm its Profile Path.\n'
        f'Open {manager_url}, enable Developer mode, Load unpacked StrictRoutines, then StatusHelper from this folder.\n'
        'Approve local-network access if the browser requests it. Open StatusHelper and verify ENABLED before relying on monitoring.\n'
        'Management permission checks only the target ID. The helper cannot observe itself when disabled.\n'
        'No Native Messaging host or Windows registry change is required. No policy/force-install or new startup process is added.\n'
        'The chrome_extension_penalty_enabled rule controls new requests. The updated Android authority validates and commits this event type automatically.\n'
        f'Remove both extensions normally in {manager_url}. Stop the companion normally, then archive enrollment.json and extension_monitor_state.json\n'
        'to reset pairing. Preserve ledger data. Do not reuse this enrollment in another browser profile.\n'
        'Keep this folder private: its locally generated token authorizes only monitoring observations.\n', encoding='utf-8')
    storage.mkdir(parents=True, exist_ok=True)
    if not update_existing:
        # Exclusive creation also protects against double-clicked concurrent setup.
        with existing.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(config, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
    return {k: v for k, v in config.items() if k != 'token'}


