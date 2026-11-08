"""Session-only Windows toast suppression; StrictRecords' native alerts remain.

Uses the documented per-user NoToastApplicationNotification policy. Never reads
or deletes other apps' notifications, and never changes security notifications.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import psutil

KEY=r'Software\Policies\Microsoft\Windows\CurrentVersion\PushNotifications'
VALUE='NoToastApplicationNotification'


def state_path():
    from core.database import _default_database_path
    return Path(_default_database_path()).parent/'strict_session_notifications.json'


class Registry:
    def read(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY) as key:
                value,kind=winreg.QueryValueEx(key,VALUE)
                return dict(exists=True,value=value,kind=kind)
        except FileNotFoundError:return dict(exists=False)

    def write(self,saved):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,KEY) as key:
            if saved['exists']:winreg.SetValueEx(key,VALUE,0,saved['kind'],saved['value'])
            else:
                try:winreg.DeleteValue(key,VALUE)
                except FileNotFoundError:pass


def _owner_alive(record):
    try:return abs(psutil.Process(record['pid']).create_time()-record['created'])<0.1
    except (psutil.Error,KeyError,TypeError):return False


def session_filter_active():
    """Read-only guard for relayed third-party Android notification banners."""
    try:
        record=json.loads(state_path().read_text())
        return _owner_alive(record) and Registry().read()==dict(exists=True,value=1,kind=4)
    except Exception:return False


def restore_saved(path,registry=None,*,token=None):
    path=Path(path);registry=registry or Registry()
    if not path.exists():return True
    record=json.loads(path.read_text())
    if token and record.get('token')!=token:return False
    old=record['original']
    if old.get('exists') and (old.get('kind')!=4 or old.get('value') not in (0,1)):
        raise ValueError('Unexpected saved notification setting')
    current=registry.read()
    # Do not overwrite a later administrator/user change to this one value.
    if current!=dict(exists=True,value=1,kind=4):
        path.unlink()
        return current==old
    registry.write(old)
    if registry.read()!=old:return False
    path.unlink()
    return True


def watch():
    path=state_path()
    if not path.exists():return
    record=json.loads(path.read_text());token=record['token']
    while path.exists():
        current=json.loads(path.read_text())
        if current.get('token')!=token:return
        if not _owner_alive(record):
            restore_saved(path,token=token);return
        time.sleep(1)


class StrictNotificationFilter:
    def __init__(self,path=None,registry=None,launcher=None):
        self.path=Path(path) if path else state_path();self.registry=registry or Registry()
        self.launcher=launcher or self._launch_watchdog
        self.token=None;self.active=False

    def recover(self):
        try:
            if self.path.exists():
                record=json.loads(self.path.read_text())
                if not _owner_alive(record):return restore_saved(self.path,self.registry,token=record['token'])
            return True
        except Exception:return False

    def _launch_watchdog(self):
        if getattr(sys,'frozen',False):args=[sys.executable,'--notification-watchdog']
        else:args=[sys.executable,'-m','windows.strict_notification_filter','--watch']
        subprocess.Popen(args,cwd=str(Path(__file__).resolve().parents[1]),
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def start(self):
        if self.active:return self.status()
        try:
            self.recover()
            if self.path.exists():raise RuntimeError('Another session owns the notification filter')
            original=self.registry.read()
            if original.get('exists') and (original.get('kind')!=4 or original.get('value') not in (0,1)):
                raise ValueError('Notification policy is not a supported DWORD')
            self.token=secrets.token_hex(16)
            record=dict(original=original,token=self.token,pid=os.getpid(),created=psutil.Process().create_time())
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(record));os.replace(temporary,self.path)
            self.launcher()
            self.registry.write(dict(exists=True,value=1,kind=4))
            self.active=self.registry.read()==dict(exists=True,value=1,kind=4)
            if not self.active:raise RuntimeError('Notification policy write was not confirmed')
            return self.status()
        except Exception:
            try:
                if self.token:restore_saved(self.path,self.registry,token=self.token)
            except Exception:pass
            self.active=False
            return dict(active=False,error='Windows notification suppression could not be activated.')

    def status(self):
        try:active=self.active and self.registry.read()==dict(exists=True,value=1,kind=4)
        except Exception:active=False
        return dict(active=bool(active),scope='Windows app toast banners',strictrecords_alerts='Native alerts remain visible')

    def restore(self):
        try:
            result=restore_saved(self.path,self.registry,token=self.token) if self.token else True
            self.active=False;self.token=None
            return result
        except Exception:return False


if __name__=='__main__' and '--watch' in sys.argv:watch()
