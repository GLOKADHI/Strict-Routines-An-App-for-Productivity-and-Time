import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_google_identity_is_extension_scoped_and_requires_owner_configuration():
    folder=ROOT/'browser_extension'
    manifest=json.loads((folder/'manifest.json').read_text())
    assert 'identity' in manifest['permissions'] and 'oauth2' not in manifest
    assert any('googleapis.com' in x for x in manifest['host_permissions'])
    for file in ['content.js']:
        text=(folder/file).read_text(encoding='utf-8')
        for forbidden in ['STRICTBOT_GOOGLE_','/api/google/','launchWebAuthFlow','getAuthToken','Google Drive','btnGdrive']:
            assert forbidden not in text
    assert 'STRICTBOT_CHECK_ACCESS' in (folder/'service_worker.js').read_text()
    identity=(folder/'google_identity.js').read_text()
    assert 'getAuthToken' in identity and 'StrictClientGoogle' in identity
    assert 'client_secret' not in identity and 'localStorage' not in identity
    assert 'TRUSTED_CONTEXTS' in (folder/'client_bridge.js').read_text()
    assert 'strictGoogleSession' in (folder/'service_worker.js').read_text() # Legacy token cache is removed on update.
