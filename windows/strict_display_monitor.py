"""Desktop-only, read-only display observation and in-window warning banner.

Works when the EXE is attached to a separate development backend. No scheduler,
score mutation, media capture, session control or server restart is performed.
"""
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time

from windows.strict_display_warnings import DisplayWarningTracker
from core.strict_participants import COMPANION_ROLES

logger=logging.getLogger('StrictDisplayMonitor')


def read_display_snapshot(path, *, now=None, account='user_lok'):
    now=time.time() if now is None else now
    result=dict(preparation=None,windows_expected=False)
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as connection:
        connection.row_factory=sqlite3.Row
        row=connection.execute("SELECT * FROM strict_preparations WHERE account_id=? AND status IN ('PENDING','ACTIVE') ORDER BY created DESC LIMIT 1",(account,)).fetchone()
        if not row:
            return result
        values=dict(row)
        if values['status']=='PENDING' and now>=values.get('expires_at',values['created']+3600):
            return result
        devices={}
        for participant in connection.execute('SELECT role,seen,report_json FROM strict_join_participants WHERE preparation_id=?',(values['preparation_id'],)):
            if participant['role'] not in COMPANION_ROLES:continue
            devices[participant['role']]=dict(joined=True,fresh=bool(participant['seen'] and 0<=now-participant['seen']<20),report=json.loads(participant['report_json']))
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='strict_mobile_integrity'").fetchone():
            for participant in connection.execute('SELECT * FROM strict_mobile_integrity WHERE preparation_id=?',(values['preparation_id'],)):
                if participant['role'] in devices:devices[participant['role']].update(session_interrupted=bool(participant['confirmed']),integrity_state=participant['state'],integrity_reason=participant['reason'] if 'reason' in participant.keys() else '')
        result['preparation']=dict(preparation_id=values['preparation_id'],devices=devices)
        if values['status']=='ACTIVE':
            study=connection.execute("SELECT data_json FROM strict_study_sessions WHERE account_id=? ORDER BY updated_at DESC LIMIT 1",(account,)).fetchone()
            if study:
                value=json.loads(study['data_json'])
                result['windows_expected']=value.get('monitoring_resumed') is True and value.get('state') not in {'IDLE','FAILED','ABANDONED','SESSION_FINALIZED','REWARD_ACTIVE'}
    return result


class StrictDisplayMonitor:
    def __init__(self,api,path):
        self.api,self.path=api,path
        self.stop_event=threading.Event()
        self.lock=threading.Lock()
        self.tracker=DisplayWarningTracker()
        self.native=dict(known=False)
        self.result=dict(warnings=[],new_warnings=[],restored=[])
        self.timer=None;self.panel=None;self.label=None

    def start(self):
        if self.timer is not None:return
        native=getattr(self.api._window,'native',None)
        if native is None:return
        from System import Action
        from windows.webview_lifecycle import post_to_ui
        def attach():
            from System.Drawing import Color,Font,Point,Size
            from System.Windows.Forms import Panel,Label,Timer
            if self.timer is not None:return
            panel=Panel();panel.BackColor=Color.FromArgb(57,39,16);panel.Visible=False
            label=Label();label.ForeColor=Color.FromArgb(255,217,151);label.Font=Font('Segoe UI',10)
            label.Location=Point(14,8);label.AutoSize=False
            panel.Controls.Add(label);native.Controls.Add(panel)
            self.panel,self.label=panel,label
            timer=Timer();timer.Interval=500
            def tick(*_):
                if getattr(self.api,'is_exiting',False) or native.IsDisposed:
                    timer.Stop();self.stop_event.set();return
                try:
                    from System.Windows.Forms import Screen
                    import ctypes
                    foreground=ctypes.windll.user32.GetForegroundWindow
                    foreground.restype=ctypes.c_void_p
                    bounds=Screen.FromControl(native).Bounds
                    full=(str(native.FormBorderStyle) in {'None','0'} and str(native.WindowState) in {'Maximized','2'}
                          and native.Bounds.Left<=bounds.Left and native.Bounds.Top<=bounds.Top
                          and native.Bounds.Right>=bounds.Right and native.Bounds.Bottom>=bounds.Bottom)
                    visible=bool(native.Visible and str(native.WindowState) not in {'Minimized','1'})
                    observation=dict(known=True,visible=visible,fullscreen=full,
                        foreground=int(foreground() or 0)==int(native.Handle.ToInt64()),focus_view_visible=True)
                    with self.lock:
                        self.native=observation;warnings=list(self.result['warnings'])
                    panel.Visible=bool(warnings)
                    if warnings:
                        text='STRICT SESSION · DISPLAY WARNING\n'+'\n'.join(item['message'] for item in warnings)
                        label.Text=text
                        height=30+len(warnings)*23
                        panel.Location=Point(0,0);panel.Size=Size(max(100,native.ClientSize.Width),height)
                        label.Size=Size(max(80,native.ClientSize.Width-28),height-12)
                        panel.BringToFront()
                except Exception:
                    with self.lock:self.native=dict(known=False)
                    logger.debug('Display observation unavailable',exc_info=True)
            timer.Tick+=tick;timer.Start();self.timer=timer
            native.FormClosed+=lambda *_:self.stop_event.set()
            threading.Thread(target=self._watch,name='StrictDisplayWarnings',daemon=True).start()
        post_to_ui(native,attach,Action)

    def _watch(self):
        while not self.stop_event.is_set():
            try:
                snapshot=read_display_snapshot(self.path)
                with self.lock:native=dict(self.native)
                result=self.tracker.update(snapshot['preparation'],native,windows_expected=snapshot['windows_expected'])
                with self.lock:self.result=result
                if result['new_warnings']:
                    # A tray balloon keeps the warning observable if the EXE is
                    # hidden. The persistent banner remains inside its main form.
                    from windows.tray_manager import StrictRecordsTrayManager
                    tray=StrictRecordsTrayManager.get_existing_instance()
                    if tray and tray.is_running:
                        tray.show_balloon('Strict Session display warning','\n'.join(item['message'] for item in result['new_warnings']))
            except Exception:
                logger.debug('Display warning read unavailable',exc_info=True)
            self.stop_event.wait(1)

    def status(self):
        with self.lock:return json.loads(json.dumps(self.result))
