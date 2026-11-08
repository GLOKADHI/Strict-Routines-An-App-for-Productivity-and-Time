"""Package verified local artifacts. Never open user databases or token stores."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
import zipfile

WORKSPACE = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get('STRICTROUTINES_RELEASE_SOURCE', str(WORKSPACE))).resolve()
REPORT = WORKSPACE / 'reports/windows-extension-parity-20261031'
RELEASE = WORKSPACE / 'releases/StrictRoutines-Client-Parity-20261031'
WINDOWS = WORKSPACE / 'releases/StrictRoutines-Windows-v2.5.0'
EXTENSION = ROOT / 'browser_extension'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def safe_files(folder):
    blocked = {'.enc', '.db', '.sqlite', '.sqlite3', '.apk', '.pem', '.key', '.pfx', '.p12', '.jks', '.keystore'}
    files = sorted(p for p in folder.rglob('*') if p.is_file())
    for path in files:
        # certifi's public CA trust bundle is required for TLS, not a private key.
        assert path.suffix.lower() not in blocked or path.name == 'cacert.pem', 'User-data/secret format in package: ' + str(path.relative_to(folder))
        assert not path.name.startswith(('client_secret_', 'google_client_config')), 'Private config in package'
        assert '__pycache__' not in path.parts, 'Cache included in package'
    return files


def archive(target, entries):
    entries = [(p, name.replace('\\', '/')) for p, name in entries]
    if target.is_file():
        with zipfile.ZipFile(target) as existing:
            reusable = set(existing.namelist()) == {name for _, name in entries}
            if reusable:
                for path, name in entries:
                    with existing.open(name) as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest() != digest(path):
                            reusable = False
                            break
            if reusable:
                return dict(path=str(target.relative_to(WORKSPACE)), bytes=target.stat().st_size, sha256=digest(target))
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path, name in entries:
            output.write(path, name)
    with zipfile.ZipFile(target) as output:
        assert output.testzip() is None, 'Archive integrity failure'
    return dict(path=str(target.relative_to(WORKSPACE)), bytes=target.stat().st_size, sha256=digest(target))


def suite(name):
    root = ET.parse(REPORT / name).getroot()
    rows = list(root) if root.tag == 'testsuites' else [root]
    return {key: sum(int(s.get(key, 0)) for s in rows) for key in ('tests', 'failures', 'errors', 'skipped')}


def main():
    RELEASE.mkdir(parents=True, exist_ok=True)
    frozen = json.loads((REPORT / 'frozen-runtime-final.json').read_text())
    assert frozen['success'] and frozen['frozen'] and not frozen['database_opened'] and not frozen['services_started']
    assert (REPORT / 'pyinstaller-final-exit.txt').read_text(encoding='utf-8-sig').strip() == '0'
    source = json.loads((REPORT / 'build-source-manifest-final.json').read_text(encoding='utf-8-sig'))
    changed = [name for name, expected in source.items() if not (ROOT / name).is_file() or digest(ROOT / name) != expected]
    assert not changed, 'Runtime source differs from completed build: ' + ', '.join(changed)
    extension_manifest = json.loads((EXTENSION / 'manifest.json').read_text())
    assert extension_manifest['version'] == frozen['extension_version']
    assert 'oauth2' not in extension_manifest, 'Owner OAuth client ID has not been provided'
    for file in (ROOT / 'shared/client').iterdir():
        assert file.read_bytes() == (EXTENSION / 'client' / file.name).read_bytes()
        assert file.read_bytes() == (WINDOWS / 'StrictRecords/_internal/shared/client' / file.name).read_bytes()
    for path in EXTENSION.rglob('*'):
        if path.is_file():
            assert digest(path) == digest(WINDOWS / 'StrictRecords/_internal/browser_extension' / path.relative_to(EXTENSION))

    for name in ('WINDOWS_EXTENSION_FEATURE_PARITY.md', 'WINDOWS_EXTENSION_PARITY_SETUP.md'):
        shutil.copy2(ROOT / 'docs' / name, WINDOWS / name)
        shutil.copy2(ROOT / 'docs' / name, RELEASE / name)
    shutil.copytree(ROOT / 'docs/contracts', WINDOWS / 'contracts', dirs_exist_ok=True)
    shutil.copytree(ROOT / 'docs/contracts', RELEASE / 'contracts', dirs_exist_ok=True)
    readme = '''# StrictRoutines Windows + extension release candidate

Windows: 2.5.0. Browser extension: 1.8.1 (retained extension ID).

Extract the Windows ZIP and keep StrictRecords.exe beside its entire _internal
directory. Exit the prior app normally before starting it. The current app is
not force-closed, replaced, or restarted by this package. Existing workspace data
is reused. No database, evidence, OAuth JSON, or token store is distributed.

Extract the extension ZIP into a persistent folder. Reload the existing unpacked
extension using the browser's extension manager. No browser security setting or
managed-install policy is changed by packaging. Pair each actual browser profile.

Read WINDOWS_EXTENSION_FEATURE_PARITY.md before production use. Acceptance is
NOT met: live Google/Drive consent,
official Taufa grading, and independent three-client cloud delivery are NOT
VERIFIED. The owner has not created an extension OAuth client or provided official
Taufa access/cloud hosting. Do not treat the screen layout as full parity.
The reproduced legacy College-plan collision has a tested data-preserving
migration. Its real user database upgrade remains NOT VERIFIED.

This candidate uses the 31 October saved source plus two final verified repairs.
Newer 1 November native-bot/runtime changes in the shared workspace are preserved
and excluded. The candidate does not auto-install or replace that newer app.

The Source ZIP contains code, public assets, tests and contracts. The cloud ZIP
is a deployment preparation bundle, not a running cloud service. Android is not
rebuilt or replaced by this release. SHA256SUMS.txt identifies each archive.
'''
    (RELEASE / 'README.md').write_text(readme, encoding='utf-8')
    shutil.copy2(RELEASE / 'README.md', WINDOWS / 'README.md')
    windows_files = safe_files(WINDOWS)
    file_manifest = [{'path':str(p.relative_to(WINDOWS)), 'bytes':p.stat().st_size, 'sha256':digest(p)} for p in windows_files]
    write_json(REPORT / 'windows-file-manifest.json', file_manifest)
    artifacts = []
    artifacts.append(archive(RELEASE / 'StrictRoutines-Windows-v2.5.0.zip',
                             ((p, str(p.relative_to(WINDOWS))) for p in windows_files)))
    artifacts.append(archive(RELEASE / 'StrictRoutines-Extension-v1.8.1.zip',
                             ((p, str(p.relative_to(EXTENSION))) for p in safe_files(EXTENSION))))
    folders = ['core', 'windows', 'cloud_points', 'browser_extension', 'browser_extension_monitor', 'shared', 'assets']
    allowed = {'.py', '.js', '.cjs', '.json', '.html', '.css', '.md', '.png', '.ico', '.txt', '.yaml'}
    source_files = []
    for name in folders:
        source_files.extend(p for p in (ROOT / name).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                            and (p.suffix.lower() in allowed or p.name in {'Dockerfile', 'Dockerfile.dockerignore'}))
    for name in ('tests', 'scripts'):
        source_files.extend(p for p in (ROOT / name).iterdir() if p.is_file() and p.suffix in {'.py', '.cjs'})
    source_files += list((ROOT / 'docs/contracts').glob('*'))
    source_files += list((ROOT / 'new_model/evidence_pipeline').glob('*.py'))
    source_files += [ROOT / p for p in ['android/app/src/main/java/com/strictrecords/app/MainActivity.kt',
        'android/app/src/main/java/com/strictrecords/app/AppUpdateManager.kt',
        'android/app/src/main/AndroidManifest.xml', 'android/app/build.gradle']]
    source_files += [ROOT / p for p in ['desktop_app.py', 'app.py', 'StrictRecordsApp.spec', 'requirements-runtime.txt',
        'app_icon.ico', 'app_logo.png', 'StrictRecordsBrowserBridge.dll', 'StrictRecords-connect.html',
        'data/motivation_track.wav', 'tools/configure_extension_oauth.py',
        'docs/WINDOWS_EXTENSION_FEATURE_PARITY.md', 'docs/WINDOWS_EXTENSION_PARITY_SETUP.md']]
    for path in source_files:
        assert path.is_file(), 'Missing source deliverable: '+str(path)
    artifacts.append(archive(RELEASE / 'StrictRoutines-Client-Source-20261031.zip',
                             ((p, str(p.relative_to(ROOT))) for p in sorted(set(source_files)))))
    cloud_files = [p for p in (ROOT / 'cloud_points').iterdir() if p.is_file() and p.suffix not in {'.pyc', '.enc'}]
    cloud_files += [ROOT / 'core/android_ledger.py', ROOT / 'docs/WINDOWS_EXTENSION_PARITY_SETUP.md']
    artifacts.append(archive(RELEASE / 'StrictRoutines-Cloud-Prepared-20261031.zip',
                             ((p, str(p.relative_to(ROOT))) for p in cloud_files)))
    (RELEASE / 'SHA256SUMS.txt').write_text(''.join(a['sha256']+'  '+Path(a['path']).name+'\n' for a in artifacts))
    ui = json.loads((REPORT / 'ui-final/shared-client-ui-results.json').read_text())
    result = dict(generated_at_utc=datetime.now(timezone.utc).isoformat(), release_status='RELEASE_CANDIDATE_PARTIAL_PARITY',
        final_acceptance_met=False, windows_version='2.5.0', extension_version=extension_manifest['version'],
        android_rebuilt=False, live_application_restarted=False, live_user_records_modified_by_packaging=False,
        executable=dict(path=str((WINDOWS/'StrictRecords/StrictRecords.exe').relative_to(WORKSPACE)),
            sha256=digest(WINDOWS/'StrictRecords/StrictRecords.exe')),
        artifacts=artifacts, runtime_source_snapshot_matches=True, runtime_source_files=len(source),
        source_isolated_from_newer_workspace_edits=True, newer_runtime_changes_merged=False,
        incremental_compiled_module_check=json.loads((REPORT/'incremental-module-verification.json').read_text()),
        windows_runtime_files=len(windows_files), frozen_runtime=frozen,
        focused_python=suite('final-targeted.xml'), regression_python=suite('regression-final.xml'),
        extension_javascript=dict(tests=33, failures=0, report='extension-final-tests.log'), ui=ui,
        live_google_signin='NOT VERIFIED', live_drive_round_trip='NOT VERIFIED', live_taufa_grading='NOT VERIFIED',
        independent_cloud_deployment='NOT VERIFIED', full_three_client_parity='NOT VERIFIED',
        native_frozen_gui_startup='NOT VERIFIED', live_browser_reload_and_sites='NOT VERIFIED',
        remaining_issues=['Official Taufa credentials/conversation contract not supplied', 'Extension OAuth client ID not created',
            'Cloud host/PostgreSQL provisioning absent', 'Drive crash-resume/change tracking incomplete',
            'Morning auto-entry and Android status ingestion incomplete', 'Legacy non-hourly AI paths need further audit',
            'Live additive schedule migration not performed; isolated preservation and rollback tests passed',
            'Newer shared-workspace runtime changes intentionally excluded from this candidate'])
    write_json(REPORT/'BUILD_INFO.json', result)
    write_json(RELEASE/'BUILD_INFO.json', result)
    print(json.dumps(dict(artifacts=artifacts,focused_python=result['focused_python'],regression_python=result['regression_python'],
                         runtime_verified=True,acceptance_met=False),indent=2))


if __name__ == '__main__':
    main()
