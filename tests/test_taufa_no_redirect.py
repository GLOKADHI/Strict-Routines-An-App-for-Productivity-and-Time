"""No network is opened: a fixture HTTPS handler returns redirect responses."""
import io
from email.message import Message
import urllib.error
import urllib.request
import urllib.response

import pytest

from core.taufa_client import _open_taufa_request


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_session_cookie_request_never_follows_provider_redirect(monkeypatch, code):
    requests = []

    class FixtureHTTPS(urllib.request.HTTPSHandler):
        handler_order = 100

        def https_open(self, request):
            requests.append(request)
            headers = Message()
            headers["Location"] = "https://another-host.invalid/capture"
            reply = urllib.response.addinfourl(io.BytesIO(b"redirect"), headers, request.full_url, code)
            reply.msg = "Moved"
            return reply

    real_build_opener = urllib.request.build_opener
    monkeypatch.setattr("core.taufa_client.urllib.request.build_opener",
                        lambda *handlers: real_build_opener(*handlers, FixtureHTTPS()))
    request = urllib.request.Request("https://taufa.ai/api/auth/session",
                                     headers={"Cookie": "authjs.session-token=fixture-only"})
    with pytest.raises(urllib.error.HTTPError) as error:
        _open_taufa_request(request, timeout=2)
    assert error.value.code == code
    error.value.close()
    assert len(requests) == 1
    assert requests[0].full_url == "https://taufa.ai/api/auth/session"


@pytest.mark.parametrize("url", [
    "http://taufa.ai/api/auth/session",
    "https://taufa.ai.evil.invalid/api/auth/session",
    "https://elsewhere.invalid/api/auth/session",
    "https://user:password@taufa.ai/api/auth/session",
    "https://taufa.ai:444/api/auth/session",
])
def test_provider_origin_is_checked_before_constructing_transport(monkeypatch, url):
    def unexpected_transport(*args, **kwargs):
        pytest.fail("Unexpected network transport construction")
    monkeypatch.setattr("core.taufa_client.urllib.request.build_opener", unexpected_transport)
    with pytest.raises(ValueError, match="fixed HTTPS provider origin"):
        _open_taufa_request(urllib.request.Request(url), timeout=2)
