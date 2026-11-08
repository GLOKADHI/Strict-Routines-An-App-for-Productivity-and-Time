import json
import pytest
from windows.strict_notification_filter import StrictNotificationFilter,restore_saved


class Registry:
    def __init__(self,value=None):self.value={'exists':False} if value is None else dict(exists=True,value=value,kind=4)
    def read(self):return dict(self.value)
    def write(self,value):self.value=dict(value)


@pytest.mark.parametrize('original',[None,0,1])
def test_filter_restores_exact_previous_setting_and_does_not_touch_other_preferences(tmp_path,original):
    r=Registry(original);before=r.read();calls=[]
    guard=StrictNotificationFilter(tmp_path/'state.json',r,lambda:calls.append('watchdog'))
    assert guard.start()['active'] and r.read()['value']==1 and calls==['watchdog']
    assert guard.start()['active'] and len(calls)==1
    assert guard.restore() and r.read()==before and not guard.path.exists()


def test_external_change_is_not_overwritten(tmp_path):
    r=Registry(1);guard=StrictNotificationFilter(tmp_path/'state.json',r,lambda:None)
    guard.start();r.write(dict(exists=True,value=0,kind=4))
    assert not guard.restore() and r.read()['value']==0


def test_old_watchdog_cannot_restore_new_session_setting(tmp_path):
    r=Registry(0);guard=StrictNotificationFilter(tmp_path/'state.json',r,lambda:None)
    guard.start()
    assert not restore_saved(guard.path,r,token='old-token') and guard.path.exists()
    assert guard.restore()


def test_failed_watchdog_start_never_leaves_a_new_policy(tmp_path):
    r=Registry(0)
    def fail():raise OSError('synthetic launcher failure')
    guard=StrictNotificationFilter(tmp_path/'state.json',r,fail)
    assert not guard.start()['active'] and r.read()['value']==0 and not guard.path.exists()


def test_mirrored_android_banners_are_suppressed_but_own_alerts_remain(monkeypatch):
    from windows import notification_service
    calls=[]
    monkeypatch.setattr('windows.strict_notification_filter.session_filter_active',lambda:True)
    monkeypatch.setattr('windows.modern_toast.show_modern_toast',lambda **kw:calls.append(kw) or True)
    assert not notification_service._try_toast_notification('Other app','Message',notification_service.CATEGORY_ANDROID)
    assert calls==[]
    assert notification_service._try_toast_notification('StrictRecords','Reconnect iPad',notification_service.CATEGORY_FOCUS)
    assert len(calls)==1


def test_failed_registry_restore_keeps_recovery_snapshot(tmp_path):
    r=Registry(0);guard=StrictNotificationFilter(tmp_path/'state.json',r,lambda:None)
    guard.start()
    r.write=lambda _:None
    assert not guard.restore() and guard.path.exists()
