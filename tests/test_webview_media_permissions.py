"""No hardware access: permission events, native deferrals and consent decisions."""
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from windows import webview_lifecycle as lifecycle

STATES = NS(Default='Default', Allow='Allow', Deny='Deny')


def event(kind='Camera', uri='http://127.0.0.1:8765/'):
    deferred = NS(Complete=Mock())
    return NS(PermissionKind=kind, Uri=uri, GetDeferral=Mock(return_value=deferred)), deferred


@pytest.mark.parametrize('kind', ['Camera', 'Microphone'])
@pytest.mark.parametrize('consent', [True, False])
def test_user_decision_is_deferred_and_never_persisted(kind, consent):
    args, deferred = event(kind)
    queued = []
    prompt = Mock(return_value=consent)
    lifecycle.request_media_permission(NS(Source=args.Uri), args, STATES, prompt,
                                       lambda action: queued.append(action) or True)
    assert args.State == 'Deny' and args.Handled and args.SavesInProfile is False
    prompt.assert_not_called()
    deferred.Complete.assert_not_called()
    queued.pop()()
    prompt.assert_called_once_with(kind)
    assert args.State == ('Allow' if consent else 'Deny')
    deferred.Complete.assert_called_once()


@pytest.mark.parametrize('kind,uri', [
    ('Camera', 'https://chatgpt.com'), ('Camera', 'http://127.0.0.1:9999'),
    ('Camera', 'http://127.0.0.1.evil.test:8765'), ('Camera', 'http://user@127.0.0.1:8765'),
    ('Microphone', 'file:///dashboard.html'), ('ClipboardRead', 'http://127.0.0.1:8765'),
])
def test_other_origins_and_permissions_receive_no_host_grant(kind, uri):
    args, deferred = event(kind, uri)
    prompt, schedule = Mock(), Mock()
    lifecycle.request_media_permission(NS(Source='http://127.0.0.1:8765/'), args, STATES, prompt, schedule)
    assert args.State == 'Default' and args.Handled is False
    prompt.assert_not_called()
    schedule.assert_not_called()
    deferred.Complete.assert_not_called()


def test_navigation_during_prompt_never_grants_new_origin():
    args, deferred = event()
    core = NS(Source=args.Uri)
    queued = []
    def prompt(kind):
        core.Source = 'https://unrelated.test'
        return True
    lifecycle.request_media_permission(core, args, STATES, prompt, lambda action: queued.append(action) or True)
    queued.pop()()
    assert args.State == 'Deny'
    deferred.Complete.assert_called_once()


@pytest.mark.parametrize('dispatch', [False, True])
def test_closed_window_or_failed_prompt_completes_without_access(dispatch):
    args, deferred = event()
    prompt = Mock(side_effect=RuntimeError('Window closed'))
    def schedule(action):
        if dispatch:
            action()
        return dispatch
    lifecycle.request_media_permission(NS(Source=args.Uri), args, STATES, prompt, schedule)
    assert args.State == 'Deny'
    deferred.Complete.assert_called_once()


def test_hook_is_idempotent_and_resets_only_local_media_profile_state(monkeypatch):
    callbacks = []
    class Event:
        def __iadd__(self, callback):
            callbacks.append(callback)
            return self
    module = NS(CoreWebView2PermissionState=STATES,
                CoreWebView2PermissionKind=NS(Camera='Camera', Microphone='Microphone'))
    monkeypatch.setitem(sys.modules, 'Microsoft.Web.WebView2.Core', module)
    monkeypatch.setattr(lifecycle, '_ensure_webview2_assembly', lambda: None)
    reset = Mock()
    core = NS(Source='http://127.0.0.1:8765/', PermissionRequested=Event(), Profile=NS(SetPermissionStateAsync=reset))
    lifecycle.ensure_webview2_permissions(core)
    lifecycle.ensure_webview2_permissions(core)
    assert len(callbacks) == 1
    assert reset.call_count == 2
    assert [c.args for c in reset.call_args_list] == [
        ('Camera', 'http://127.0.0.1:8765', 'Default'),
        ('Microphone', 'http://127.0.0.1:8765', 'Default')]
