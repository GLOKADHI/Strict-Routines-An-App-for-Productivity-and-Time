"""Thread-affinity regressions with fake WinForms dispatch; no desktop UI changes."""
import ast
from pathlib import Path
from types import SimpleNamespace
import threading

from windows.webview_lifecycle import post_memory_target, post_to_ui, post_recovery_handlers


def test_hiding_queues_memory_change_without_touching_com_on_worker():
    queued=[];touches=[]
    class Webview:
        @property
        def CoreWebView2(self):
            touches.append(threading.get_ident())
            return core
    core=SimpleNamespace(MemoryUsageTargetLevel="Normal")
    native=SimpleNamespace(browser=SimpleNamespace(webview=Webview()),BeginInvoke=queued.append)
    assert post_memory_target(native,"Low",level_resolver=lambda value:value)
    assert not touches and core.MemoryUsageTargetLevel=="Normal" and len(queued)==1
    ui=threading.Thread(target=queued.pop());ui.start();ui.join(1)
    assert touches==[ui.ident] and core.MemoryUsageTargetLevel=="Low"


def test_failed_dispatch_never_runs_com_inline():
    def deny(callback):raise RuntimeError('Window is closing')
    calls=[]
    native=SimpleNamespace(BeginInvoke=deny)
    assert not post_to_ui(native,lambda:calls.append(True))
    assert calls==[]


def test_closed_or_uninitialized_webview_does_not_stop_other_services():
    queued=[]
    native=SimpleNamespace(browser=SimpleNamespace(webview=None),BeginInvoke=queued.append)
    assert post_memory_target(native,"Low",level_resolver=lambda value:value)
    queued.pop()()
    assert not post_memory_target(None,"Low")


def test_hide_worker_no_longer_accesses_webview_com_properties():
    tree=ast.parse(Path('desktop_app.py').read_text(encoding='utf-8-sig'))
    api=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='DesktopApi')
    hide=next(node for node in api.body if isinstance(node,ast.FunctionDef) and node.name=='hide_to_tray')
    attrs={node.attr for node in ast.walk(hide) if isinstance(node,ast.Attribute)}
    assert 'CoreWebView2' not in attrs and 'MemoryUsageTargetLevel' not in attrs
    assert any(isinstance(node,ast.Name) and node.id=='post_memory_target' for node in ast.walk(hide))


def test_recovery_subscription_and_reload_are_queued_on_ui_thread():
    queued=[];callbacks=[];touches=[];reloads=[]
    class Event:
        def __iadd__(self, callback):callbacks.append(callback);return self
    core=SimpleNamespace(ProcessFailed=Event())
    class Webview:
        @property
        def CoreWebView2(self):touches.append(threading.get_ident());return core
        def Reload(self):reloads.append(threading.get_ident())
    native=SimpleNamespace(browser=SimpleNamespace(webview=Webview()),BeginInvoke=queued.append)
    assert post_recovery_handlers(native,'http://127.0.0.1:8765')
    assert touches==[] and callbacks==[]
    queued.pop()()
    assert touches==[threading.get_ident()] and len(callbacks)==1
    callbacks[0](None,SimpleNamespace(ProcessFailedKind='RenderProcessExited'))
    assert reloads==[threading.get_ident()]
