"""Exercise real navigation handlers without opening a site or Windows camera."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from windows.strict_browser_pane import StudyBrowserPane,placeholder_geometry


class Event:
    def __init__(self):self.callbacks=[]
    def __iadd__(self,callback):self.callbacks.append(callback);return self
    def fire(self,**values):
        for callback in self.callbacks:callback(None,NS(**values))


def pane():
    view=StudyBrowserPane.__new__(StudyBrowserPane)
    core=NS(**{name:Event() for name in ('NavigationStarting','NavigationCompleted','SourceChanged','ProcessFailed')})
    core.Navigate=Mock();core.Source='https://chatgpt.com/'
    view.view=NS(CoreWebView2=core);view.offline=False;view._disposed=False
    view.retry_url=core.Source;view._navigation_id=None;view._ignored_navigation_ids=set();view._had_ready_page=False
    view._load_started=None;view.state='ready';view.states=[]
    view.address=NS(Text='');view.tips=NS(SetToolTip=Mock())
    def state(name,message=None):
        view.state=name;view.states.append((name,message))
    view.show_state=state
    view.attach_navigation(lambda uri:uri.startswith('https://chatgpt.com/'))
    return view,core


def start(core,identity,uri='https://chatgpt.com/',cancel=False):
    core.NavigationStarting.fire(NavigationId=identity,Uri=uri,Cancel=cancel)


def finish(core,identity,success=True):
    core.NavigationCompleted.fire(NavigationId=identity,IsSuccess=success,WebErrorStatus='OperationCanceled')


def test_superseded_navigation_cannot_replace_newer_loading_state():
    view,core=pane();start(core,1);start(core,2)
    finish(core,1,False);assert view.state=='loading'
    finish(core,2);assert view.state=='ready'


def test_blocked_navigation_cannot_hide_a_usable_approved_page():
    view,core=pane();start(core,1);finish(core,1)
    count=len(view.states);start(core,2,'https://example.invalid/',cancel=True);finish(core,2,False)
    assert view.state=='ready' and len(view.states)==count


def test_current_timeout_is_recoverable_by_later_success(monkeypatch):
    view,core=pane();start(core,1);view._load_started=10
    monkeypatch.setattr('windows.strict_browser_pane.time.monotonic',lambda:26)
    view.on_timeout();assert view.state=='error'
    finish(core,1);assert view.state=='ready'


def test_process_failure_and_retry_use_existing_approved_site_only():
    view,core=pane();core.ProcessFailed.fire();assert view.state=='error'
    core.Navigate.side_effect=RuntimeError('offline');view.retry();assert view.state=='error'
    core.Navigate.side_effect=None;view.retry()
    assert view.state=='loading';core.Navigate.assert_called_with('https://chatgpt.com/')


def test_short_error_viewports_scroll_a_non_overlapping_recovery_card():
    for height in (100,140,176,200,400):
        x,y,width,card_height=placeholder_geometry(320,height,'error')
        assert x>=0 and y>=0 and width<=320 and card_height==280
        assert card_height-42>191  # Retry lies below the complete message.


def test_offline_handlers_do_not_navigate_or_claim_a_live_page():
    view,core=pane();view.offline=True
    start(core,1);finish(core,1);core.ProcessFailed.fire();view.retry()
    core.Navigate.assert_not_called();assert view.states==[]


def test_actual_native_browser_state_controls_construct_with_valid_enum_types():
    import pytest
    clr=pytest.importorskip('clr');clr.AddReference('System.Windows.Forms');clr.AddReference('System.Drawing')
    from System.Windows.Forms import Panel,BorderStyle
    real_view=Panel()
    ui=StudyBrowserPane('chatgpt','ChatGPT','https://chatgpt.com/',real_view,lambda _:None,lambda:None,lambda:None,offline=True)
    try:
        for state in ('offline','loading','error','ready','offline'):
            ui.show_state(state)
            assert ui.state==state
        assert ui.card.BorderStyle==getattr(BorderStyle,'None')
    finally:
        ui.dispose();ui.host.Dispose()
