"""Provider contract checks with fake secrets and responses; no live API calls."""
import io
import json

from core.cloudflare_turn_service import CloudflareTURNService


def test_generates_ice_servers_at_documented_provider_endpoint(monkeypatch):
    service = CloudflareTURNService()
    monkeypatch.setattr(service, '_get_setting', lambda key, default='': {
        'CLOUDFLARE_TURN_KEY_ID': 'test-key',
        'CLOUDFLARE_TURN_API_TOKEN': 'test-secret',
    }.get(key, default))
    requests = []

    def provider(request, timeout):
        requests.append(request)
        return io.BytesIO(json.dumps({'iceServers': [
            {'urls': ['stun:stun.cloudflare.com:3478']},
            {'urls': ['turns:turn.cloudflare.com:443?transport=tcp'],
             'username': 'ephemeral', 'credential': 'ephemeral-secret'},
        ]}).encode())

    monkeypatch.setattr('urllib.request.urlopen', provider)
    result = service.generate_ice_servers('session', 'camera', ttl_seconds=600)
    assert requests[0].full_url == 'https://rtc.live.cloudflare.com/v1/turn/keys/test-key/credentials/generate-ice-servers'
    assert requests[0].method == 'POST'
    assert json.loads(requests[0].data) == {'ttl': 600}
    assert requests[0].headers['Authorization'] == 'Bearer test-secret'
    assert result[1]['username'] == 'ephemeral'
    assert 'test-secret' not in json.dumps(result)
