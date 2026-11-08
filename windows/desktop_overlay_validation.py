"""Opt-in offline EXE validation: real UI and proof acceptance, isolated test DB.

Does not start production monitoring, Night shutdown, sync, OCR or cloud workers.
The loopback test endpoint exists only in this explicit diagnostic process.
"""
import json
import mimetypes
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading


def run(api_class, directory):
    import sys
    import webview
    import ctypes
    user32=ctypes.windll.user32
    user32.OpenDesktopW.restype=ctypes.c_void_p;user32.SetThreadDesktop.argtypes=[ctypes.c_void_p]
    desktop=user32.OpenDesktopW('Default',0,False,0x01FF)
    if desktop:user32.SetThreadDesktop(desktop)
    from core.database import StrictRecordsDB
    from core.scoring_engine import ScoringEngine
    from core.daily_routine_gate import DailyRoutineGate
    from core.hourly_submission_service import HourlySubmissionService, HourlySubmissionError
    from core.time_window_engine import now_ist
    from core.ui_dashboard import DASHBOARD_HTML
    from core.server import parse_multipart_form
    from windows.restriction_popup import RestrictionPopupController

    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    db = StrictRecordsDB(db_path=str(root/'isolated-attendance.db'))
    engine = ScoringEngine(db, storage_root=str(root/'evidence'))
    now = now_ist()
    DailyRoutineGate.seed_approved_wakeup(db, now.date().isoformat(), completed_hm='00:00')
    row = dict(activity_id='OVERLAY-VALIDATION-HOURLY', type='HOURLY', title='Hourly Activity Check-In',
               date=now.date().isoformat(), status='OPEN', account_id='user_lok', schedule_version=1,
               scheduled_time=now.isoformat(), window_start=(now-timedelta(minutes=1)).isoformat(),
               notification_time=(now-timedelta(minutes=2)).isoformat(),
               window_end=(now+timedelta(minutes=19)).isoformat(), can_submit=True, submission_eligible=True,
               reminder_phase='OPEN', reminder_identity='isolated-overlay-hourly@1',
               details_json=json.dumps({'hour_slot':'Overlay validation','hour':now.hour}))
    with db._connect() as conn:
        columns={r[1] for r in conn.execute('PRAGMA table_info(scheduled_activities)')}
        stored={k:v for k,v in row.items() if k in columns}
        conn.execute('INSERT OR REPLACE INTO scheduled_activities ('+','.join(stored)+') VALUES ('+','.join('?' for _ in stored)+')',tuple(stored.values()))
    active = {'hourly':False, 'session':100, 'restricted':False}
    states=[]
    from windows.strict_session_preparation import StrictSessionPreparation
    preparation=StrictSessionPreparation(db,lambda _:dict(ready=False,internet=False,secure_browser=True,
        browser_message='Offline EXE validation · real checks run in production',allowlist_configured=True,
        restriction_status='Not activated in validation',network_message='External connectivity not tested',
        window=row,window_in_seconds=1200,focus_seconds=900,reward_max_seconds=1200))
    focus_preview=None

    def home(session, data):
        api.show_dashboard(tab='cockpit')
        return not active['restricted']

    def close(session, data):
        active['restricted']=False
        return {'verified':True, 'message':'Isolated validation target closed.'}

    controller=RestrictionPopupController(home, close)

    class API(api_class):
        def _native_strictbot_controller(self):return controller

    api=API(icon_path=str(Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'app_icon.ico'))
    controller.on_open_submission=api._open_coordinated_hourly

    def snapshot():
        metrics=controller.native_presentation.metrics() if hasattr(controller,'native_presentation') else {}
        result=dict(frozen=bool(getattr(sys,'frozen',False)),production_services_started=False,
                    production_data_opened=False, state=controller.overlays.snapshot(), native=metrics,
                    validation_target_restricted=active['restricted'])
        with db._connect() as conn:
            result['accepted_proofs']=conn.execute("SELECT COUNT(DISTINCT related_record_id) FROM evidence_files WHERE record_type='Hourly'").fetchone()[0]
            result['evidence_files']=conn.execute('SELECT COUNT(*) FROM evidence_files').fetchone()[0]
            result['activity_status']=conn.execute('SELECT status FROM scheduled_activities WHERE activity_id=?',(row['activity_id'],)).fetchone()[0]
        return result

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self, value, status=200, mime='application/json'):
            raw=json.dumps(value).encode() if mime=='application/json' else value
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)));self.send_header('Connection','close');self.send_header('Cache-Control','no-store');self.end_headers()
            for offset in range(0,len(raw),65536):self.wfile.write(raw[offset:offset+65536])
            self.wfile.flush();self.close_connection=True
        def do_GET(self):
            if self.path.startswith('/api/strict_session/preparation/status'):
                return self.reply(preparation.state('user_lok'))
            if self.path=='/api/strict_session/status':
                return self.reply(dict(state='NORMAL',workflow={'state':'IDLE'},study_hardware={},android_proof_devices=[]))
            if self.path=='/':
                instrumentation="<script>window.overlayValidationErrors=[];window.addEventListener('error',e=>{window.overlayValidationErrors.push(e.message+' @'+e.lineno);fetch('/validation/error',{method:'POST',body:JSON.stringify({message:e.message,line:e.lineno})})});window.addEventListener('load',()=>fetch('/validation/error',{method:'POST',body:JSON.stringify({loaded:true,open:typeof window.openCoordinatedHourly,show:typeof showHourlySubmissionOverlay})}));</script>"
                return self.reply(DASHBOARD_HTML.replace('<head>','<head>'+instrumentation,1).encode(),mime='text/html; charset=utf-8')
            if self.path=='/validation/state':return self.reply(snapshot())
            if self.path=='/validation/focus-frontend':
                from System import Action
                done=threading.Event();value={}
                def measure():
                    try:
                        p=api._focus_layout_preview
                        if not p.ready:
                            value.update(success=True,closed=True,completion_calls=getattr(api,'_frontend_completion_calls',0));return
                        if hasattr(p,'chrome_view'):
                            import hashlib
                            value.update(success=True,frontend='supplied-react-zip',
                                executable_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                                layout=p._last_layout,browser=p.browser,end_enabled=p.end_enabled,commands=p.ui_command_audit,
                                native_panes={key:dict(x=view.Left,y=view.Top,width=view.Width,height=view.Height,
                                    visible=view.Visible,handle=int(view.Handle.ToInt64())) for key,view in p.views.items()},
                                completion_calls=getattr(api,'_frontend_completion_calls',0))
                            return
                        value.update(success=True,header_height=p.chrome.header.Height,expanded=p.chrome.expanded,
                            panes={key:dict(width=pane.Width,height=pane.Height,visible=pane.Visible) for key,pane in p.panes.items()},
                            workspace=dict(width=p.workspace.Width,height=p.workspace.Height),
                            camera=dict(left=p.camera_overlay.window.Left,top=p.camera_overlay.window.Top,
                                width=p.camera_overlay.window.Width,height=p.camera_overlay.window.Height,
                                hidden=p.camera_overlay.is_user_hidden,visible=p.camera_overlay.window.Visible),
                            visible_exit_controls=[p.labels[key].Text for key in ('end','controls') if p.labels[key].Visible],
                            diagnostics={key:p.chrome.nodes[key].Text for key in ('Screen sharing','Screen monitoring','Wired audio')})
                        value['websites']={key:dict(state=ui.state,website_title=ui.nodes['title'].Text,
                            explanation=ui.nodes['message'].Text,address=ui.address.Text,
                            toolbar_width=ui.toolbar.Width,buttons={name:dict(left=b.Left,right=b.Right,width=b.Width,
                                top=b.Top,bottom=b.Bottom,visible=b.Visible,accessible_name=b.AccessibleName) for name,b in ui.buttons.items()}) for key,ui in p.site_panes.items()}
                    except Exception as error:value.update(success=False,error=str(error))
                    finally:done.set()
                api._window.native.BeginInvoke(Action(measure));done.wait(5)
                return self.reply(value)
            if self.path.startswith('/api/hourly/status'):
                r=dict(row)
                with db._connect() as conn:r['status']=conn.execute('SELECT status FROM scheduled_activities WHERE activity_id=?',(row['activity_id'],)).fetchone()[0]
                return self.reply(dict(success=True,state=('OPEN' if r['status']=='OPEN' else r['status']) if active['hourly'] else 'ALL_CLOSED',
                                       is_window_open=active['hourly'] and r['status']=='OPEN',hourly_activity=r if active['hourly'] else None,server_time=now_ist().isoformat()))
            image=self.path.split('?',1)[0]
            if image in {'/app_logo.png','/favicon.ico','/strictbot_head.png','/strictbot_compact.png','/strictbot_full.png'}:
                base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
                file=base/image.lstrip('/') if image in {'/app_logo.png','/favicon.ico'} else base/'assets'/image.lstrip('/')
                if image=='/favicon.ico':file=base/'app_icon.ico'
                if file.is_file():return self.reply(file.read_bytes(),mime=mimetypes.guess_type(str(file))[0] or 'image/png')
            if self.path.startswith('/assets/'):
                base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
                file=(base/self.path.split('?',1)[0].lstrip('/')).resolve()
                if file.is_relative_to(base.resolve()) and file.is_file():return self.reply(file.read_bytes(),mime=mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
            return self.reply({'success':False,'error':'Offline overlay validation'})
        def do_POST(self):
            raw=self.rfile.read(min(int(self.headers.get('Content-Length','0')),15*1024*1024))
            if self.path.startswith('/api/strict_session/preparation/'):
                body=json.loads(raw or '{}');action=self.path.rsplit('/',1)[-1]
                if action=='invite':return self.reply(preparation.invite('user_lok',body.get('role'),'http://127.0.0.1:'+str(self.server.server_port)))
                if action=='host':return self.reply(preparation.host_report('user_lok',body.get('preparation_id'),body.get('report')))
                if action=='check':return self.reply(preparation.state('user_lok',True))
                return self.reply({'success':False,'error':'Devices are unconfirmed in offline validation'},409)
            if self.path=='/validation/error':
                with (root/'javascript.jsonl').open('a',encoding='utf-8') as out:out.write(raw.decode()+'\n')
                return self.reply({'success':True})
            if self.path=='/validation/event':
                event=json.loads(raw);action=event['action']
                if action in {'zip_script','zip_resize','zip_capture','zip_close'}:
                    from System import Action
                    done=threading.Event();result={}
                    def operation():
                        try:
                            p=api._focus_layout_preview
                            if action=='zip_close':
                                p.close();result.update(success=True);done.set();return
                            if action=='zip_resize':
                                from System.Drawing import Size,Point
                                from System.Windows.Forms import FormWindowState
                                native=api._window.native;native.WindowState=FormWindowState.Normal
                                native.MinimumSize=Size(0,0);native.Location=Point(-20000,-20000)
                                native.ClientSize=Size(int(event['width']),int(event['height']))
                                result.update(success=True);done.set();return
                            if action=='zip_script':
                                task=p.chrome_view.CoreWebView2.ExecuteScriptAsync(str(event['script']))
                                from System.Windows.Forms import Timer
                                poll=Timer();poll.Interval=25
                                def completed(*_):
                                    if not task.IsCompleted:return
                                    poll.Stop()
                                    try:result.update(success=True,value=json.loads(str(task.Result)))
                                    except Exception as error:result.update(success=False,error=str(error))
                                    finally:poll.Dispose();done.set()
                                poll.Tick+=completed;poll.Start();return
                            from System.IO import MemoryStream
                            from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
                            name=str(event.get('name','zip-session.png'))
                            if Path(name).name!=name or not name.endswith('.png'):raise ValueError('Invalid capture name')
                            stream=MemoryStream();task=p.chrome_view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream)
                            from System.Windows.Forms import Timer
                            poll=Timer();poll.Interval=25
                            def save_capture(*_):
                                if not task.IsCompleted:return
                                poll.Stop()
                                try:
                                    if task.IsFaulted:raise RuntimeError('WebView capture failed')
                                    file=root/name;file.write_bytes(bytes(stream.ToArray()))
                                    result.update(success=True,image_path=str(file))
                                except Exception as error:result.update(success=False,error=str(error))
                                finally:poll.Dispose();stream.Dispose();done.set()
                            poll.Tick+=save_capture;poll.Start()
                        except Exception as error:result.update(success=False,error=str(error));done.set()
                    api._window.native.BeginInvoke(Action(operation));done.wait(15)
                    return self.reply(result)
                if action=='focus_layout':
                    from windows.embedded_strict_focus import EmbeddedStrictFocus
                    preview=EmbeddedStrictFocus(api)
                    api._frontend_completion_calls=0
                    def finish_frontend(identity):
                        if identity!='offline-native-layout':raise ValueError('Wrong fixture identity')
                        api._frontend_completion_calls+=1;preview.close()
                    result=preview.open('offline-native-layout',finish_frontend,offline_validation=True)
                    api._focus_layout_preview=preview
                    return self.reply(result)
                elif action=='focus_frontend_demo':
                    # UI-only fixture. No session, camera, policy or reward starts.
                    api._focus_layout_preview.update(dict(mode='DEMO',state='OFFLINE_UI_PREVIEW',focus_remaining_seconds=599,
                        focus_target_seconds=600,elapsed_seconds=1,ios_connected=None,android_connected=None),
                        dict(camera='NOT_STARTED',face_tracking='UNVERIFIED',attention_monitoring='STOPPED',
                            proctor_message='Offline demo · no real monitoring or session is running.',
                            battery_percent=None,charging=None,camera_preview=dict(current=False,jpeg=None)))
                    return self.reply(dict(success=True,frontend_only=True,production_session_started=False))
                elif action=='focus_frontend_checks':
                    from System import Action
                    from windows.strict_focus_ui_checks import check
                    done=threading.Event();result={}
                    def run_checks():
                        try:result.update(check(api._focus_layout_preview,api._window.native,root))
                        except Exception as error:
                            import traceback
                            result.update(success=False,error=str(error),traceback=traceback.format_exc())
                        finally:done.set()
                    api._window.native.BeginInvoke(Action(run_checks));done.wait(25)
                    return self.reply(result)
                elif action=='focus_frontend_finish_check':
                    from System import Action
                    done=threading.Event();result={}
                    def finish_check():
                        try:
                            api._focus_layout_preview.labels['end'].PerformClick()
                            result.update(success=True,clicked_fixture_exit=True)
                        except Exception as error:result.update(success=False,error=str(error))
                        finally:done.set()
                    api._window.native.BeginInvoke(Action(finish_check));done.wait(5)
                    return self.reply(result)
                elif action=='focus_snapshot':
                    import base64,io
                    from PIL import Image as PILImage,ImageDraw
                    image=PILImage.new('RGB',(160,120),'#19283d');draw=ImageDraw.Draw(image)
                    draw.rectangle((30,15,130,108),outline='#af8aff',width=3);draw.text((42,50),'TEST ONLY',fill='white')
                    memory=io.BytesIO();image.save(memory,format='JPEG')
                    preview=api._focus_layout_preview
                    preview.update(dict(state='OFFLINE_LAYOUT',focus_remaining_seconds=2052,focus_target_seconds=2100,elapsed_seconds=48,ios_connected=None),
                        dict(camera='OFFLINE_TEST',face_tracking='SIMULATED',lighting='TEST_ONLY',face_framing='TEST_ONLY',
                             proctor_message='Offline diagnostic: no camera, monitoring or notification suppression is active.',
                             battery_percent=65,charging=False,camera_preview=dict(current=True,jpeg=base64.b64encode(memory.getvalue()).decode())))
                    from System import Action
                    done=threading.Event();result={}
                    def capture():
                        try:
                            from System.Drawing import Bitmap,Rectangle,Point
                            from System.Windows.Forms import FormWindowState
                            import ctypes
                            # Render this fixture's controls offscreen, without
                            # activating a window or capturing the user's desktop.
                            native=api._window.native
                            native.WindowState=FormWindowState.Normal
                            native.Location=Point(-20000,-20000)
                            user32=ctypes.windll.user32
                            user32.ShowWindow.argtypes=[ctypes.c_void_p,ctypes.c_int]
                            user32.ShowWindow(int(native.Handle.ToInt64()),4)
                            native.PerformLayout()
                            header=preview.labels['header'];bar=preview.labels['tabbar'];picture=preview.labels['preview']
                            bitmap=Bitmap(header.Width,header.Height+bar.Height)
                            header.DrawToBitmap(bitmap,Rectangle(0,0,header.Width,header.Height))
                            bar.DrawToBitmap(bitmap,Rectangle(0,header.Height,bar.Width,bar.Height))
                            bitmap.Save(str(root/'focus-header.png'));bitmap.Dispose()
                            result.update(success=True,tab_count=len(preview.views),active_tab=preview.active_tab,
                                preview_loaded=picture.Image is not None,preview_width=picture.Width,preview_height=picture.Height,
                                header_width=header.Width,preview_right=picture.Right,battery_text=preview.labels['battery'].Text)
                            preview.tab_buttons['leetcode'].PerformClick()
                            result['leetcode_switch']=preview.active_tab=='leetcode' and preview.views['leetcode'].Visible
                            preview.tab_buttons['chatgpt'].PerformClick()
                            result['chatgpt_switch']=preview.active_tab=='chatgpt' and preview.views['chatgpt'].Visible
                            native.Hide()
                        except Exception as error:result.update(success=False,error=str(error))
                        finally:done.set()
                    api._window.native.BeginInvoke(Action(capture));done.wait(5)
                    return self.reply(result)
                elif action=='focus_split_check':
                    from System import Action
                    done=threading.Event();result={}
                    def check():
                        try:
                            import ctypes
                            from System.Drawing import Point,Size,Bitmap,Rectangle
                            from System.Windows.Forms import FormWindowState,MouseEventArgs,MouseButtons
                            preview=api._focus_layout_preview;native=api._window.native
                            native.WindowState=FormWindowState.Normal;native.Location=Point(-20000,-20000)
                            native.MinimumSize=Size(0,0);native.ClientSize=Size(1440,900)
                            user32=ctypes.windll.user32;user32.ShowWindow.argtypes=[ctypes.c_void_p,ctypes.c_int]
                            user32.ShowWindow(int(native.Handle.ToInt64()),4);native.PerformLayout();preview._arrange()
                            workspace=preview.workspace
                            initial_handles={key:int(view.Handle.ToInt64()) for key,view in preview.views.items()}
                            initial_sources={key:str(view.CoreWebView2.Source) for key,view in preview.views.items()}
                            result['two_visible']=all(pane.Visible for pane in preview.panes.values())
                            header,down,move,up=preview._drag_handlers['chatgpt']
                            def drag_to(x,y):
                                down(header,MouseEventArgs(MouseButtons.Left,1,15,10,0))
                                target=header.PointToClient(workspace.PointToScreen(Point(x,y)))
                                args=MouseEventArgs(MouseButtons.Left,1,target.X,target.Y,0)
                                move(header,args);up(header,args)
                            drag_to(workspace.Width-60,80)
                            result['header_drag_swaps']=preview.pane_layout.order==['leetcode','chatgpt']
                            order=list(preview.pane_layout.order);drag_to(-40,80)
                            result['outside_drop_cannot_detach']=preview.pane_layout.order==order and len(preview.views)==2
                            ddown,dmove,dup=preview._divider_handlers
                            ddown(preview.splitter,MouseEventArgs(MouseButtons.Left,1,3,90,0))
                            target=preview.splitter.PointToClient(workspace.PointToScreen(Point(round(workspace.Width*.65),90)))
                            args=MouseEventArgs(MouseButtons.Left,1,target.X,target.Y,0)
                            dmove(preview.splitter,args);dup(preview.splitter,args)
                            result['divider_resizes']=.60<preview.pane_layout.ratio<.70
                            result['browser_handles_preserved']=initial_handles=={key:int(view.Handle.ToInt64()) for key,view in preview.views.items()}
                            result['browser_addresses_preserved']=initial_sources=={key:str(view.CoreWebView2.Source) for key,view in preview.views.items()}
                            preview.pane_layout.side_by_side=False;preview._select_tab('leetcode')
                            result['single_tab_mode']=preview.panes['leetcode'].Visible and not preview.panes['chatgpt'].Visible
                            preview.pane_layout.side_by_side=True;preview.pane_layout.ratio=.5
                            if preview.pane_layout.order[0]!='chatgpt':preview.pane_layout.swap()
                            preview._select_tab('chatgpt')
                            result['window_sizes']=[]
                            for width,height in ((1440,900),(960,760),(600,760)):
                                native.ClientSize=Size(width,height);native.PerformLayout();preview._arrange()
                                bounds=preview.pane_layout.geometry(workspace.Width,workspace.Height)
                                result['window_sizes'].append(dict(width=width,actual_workspace=workspace.Width,split=bounds['divider'] is not None,
                                    visible_panes=sum(1 for pane in preview.panes.values() if pane.Visible)))
                                bitmap=Bitmap(preview.panel.Width,preview.panel.Height)
                                preview.panel.DrawToBitmap(bitmap,Rectangle(0,0,bitmap.Width,bitmap.Height))
                                bitmap.Save(str(root/f'focus-workspace-{width}.png'));bitmap.Dispose()
                            result['small_window_fallback']=result['window_sizes'][-1]['visible_panes']==1
                            result['success']=all(result[key] for key in ('two_visible','header_drag_swaps','outside_drop_cannot_detach',
                                'divider_resizes','browser_handles_preserved','browser_addresses_preserved','single_tab_mode','small_window_fallback'))
                            native.ClientSize=Size(1440,900);preview._arrange();native.Hide()
                            (root/'split-check.json').write_text(json.dumps(result,indent=2))
                        except Exception as error:result.update(success=False,error=str(error))
                        finally:done.set()
                    api._window.native.BeginInvoke(Action(check));done.wait(10)
                    return self.reply(result)
                elif action=='focus_render_pages':
                    # Only the opt-in fixture's offline pages are captured.
                    from System import Action
                    done=threading.Event();result={};capture_state={}
                    def begin_capture():
                        try:
                            import ctypes
                            from System.Drawing import Point,Size
                            from System.IO import MemoryStream
                            from System.Windows.Forms import Timer
                            from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
                            preview=api._focus_layout_preview;native=api._window.native
                            native.ClientSize=Size(1440,900);native.Location=Point(-20000,-20000)
                            user32=ctypes.windll.user32;user32.ShowWindow.argtypes=[ctypes.c_void_p,ctypes.c_int]
                            user32.ShowWindow(int(native.Handle.ToInt64()),4);preview._arrange()
                            pending={};documents={}
                            for key,view in preview.views.items():
                                stream=MemoryStream()
                                pending[key]=(view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream),stream)
                                documents[key]=view.CoreWebView2.ExecuteScriptAsync('JSON.stringify({ready:document.readyState,text:document.body?.innerText.slice(0,180),url:location.href})')
                            timer=Timer();timer.Interval=100;capture_state['timer']=timer
                            def poll(*_):
                                if not all(task.IsCompleted for task,stream in pending.values()) or not all(task.IsCompleted for task in documents.values()):return
                                timer.Stop()
                                try:
                                    from System.Drawing import Bitmap,Rectangle
                                    from PIL import Image as PILImage
                                    rect=preview.panel
                                    bitmap=Bitmap(rect.Width,rect.Height)
                                    rect.DrawToBitmap(bitmap,Rectangle(0,0,bitmap.Width,bitmap.Height))
                                    bitmap.Save(str(root/'focus-workspace-native.png'));bitmap.Dispose()
                                    composite=PILImage.open(root/'focus-workspace-native.png').convert('RGB')
                                    pages={}
                                    for key,(task,stream) in pending.items():
                                        if task.IsFaulted:raise RuntimeError('Offline WebView capture failed')
                                        raw=bytes(stream.ToArray());stream.Dispose();file=root/f'{key}-offline.png';file.write_bytes(raw)
                                        view=preview.views[key];point=rect.PointToClient(view.PointToScreen(Point(0,0)))
                                        page=PILImage.open(file).convert('RGB').resize((view.Width,view.Height))
                                        composite.paste(page,(point.X,point.Y));pages[key]=dict(width=view.Width,height=view.Height)
                                    composite.save(root/'focus-workspace-preview.png')
                                    loaded={}
                                    for key,task in documents.items():
                                        decoded=json.loads(str(task.Result))
                                        loaded[key]=json.loads(decoded) if isinstance(decoded,str) else decoded
                                    result.update(success=all('OFFLINE LAYOUT PREVIEW' in page.get('text','') for page in loaded.values()),
                                        offline_pages_only=True,pages=pages,documents=loaded,navigation=preview.offline_navigation)
                                except Exception as error:result.update(success=False,error=str(error))
                                finally:native.Hide();timer.Dispose();done.set()
                            timer.Tick+=poll;timer.Start()
                        except Exception as error:result.update(success=False,error=str(error));done.set()
                    api._window.native.BeginInvoke(Action(begin_capture));done.wait(15)
                    return self.reply(result)
                elif action in {'display_warning','display_page_exit','display_restored'}:
                    # This endpoint exists only in the opt-in isolated harness.
                    # Technical fixture rows never reach the user's DB or ledger.
                    from windows.strict_display_monitor import StrictDisplayMonitor
                    from windows.strict_session_preparation import digest
                    import time
                    pid=preparation.current('user_lok')['preparation_id']
                    report=dict(timer_ready=True,fullscreen=action=='display_restored',visible=True,online=True,
                                display_mode='fullscreen' if action=='display_restored' else 'resume_required')
                    with db._connect() as conn:
                        conn.execute('INSERT INTO strict_join_participants(preparation_id,role,credential_hash,seen,report_json) VALUES(?,?,?,?,?) ON CONFLICT(preparation_id,role) DO UPDATE SET seen=excluded.seen,report_json=excluded.report_json',
                            (pid,'IOS',digest('isolated fixture credential'),time.time(),json.dumps(report)))
                        from core.strict_mobile_integrity import SCHEMA as INTEGRITY_SCHEMA
                        conn.execute(INTEGRITY_SCHEMA)
                        conn.execute('INSERT OR REPLACE INTO strict_mobile_integrity VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (pid,'IOS','offline-fixture',1,'FOREGROUND' if action=='display_restored' else 'BACKGROUND_REASON_UNKNOWN',
                             1 if action=='display_warning' else 0,'BROWSER',time.time(),time.time()+600,'PAGE_EXIT_OBSERVED' if action=='display_page_exit' else ''))
                    if not getattr(api,'_strict_display_monitor',None):
                        api._strict_display_monitor=StrictDisplayMonitor(api,db.db_path);api._strict_display_monitor.start()
                    return self.reply(dict(success=True,fixture_database_only=True))
                elif action=='robot':
                    active['session']+=1;active['restricted']=True
                    controller.show(active['session'],dict(target_app='Isolated media target',score=0,required_score=80,reason='Offline overlay validation',robot_preview=True))
                elif action=='hourly':active['hourly']=True;api.coordinate_hourly_tasks([row])
                elif action=='resolve':active['restricted']=False;controller.close()
                elif action=='expire':active['hourly']=False;api.coordinate_hourly_tasks([])
                elif action=='repeat':
                    for _ in range(20):
                        api.coordinate_hourly_tasks([row] if active['hourly'] else [])
                        controller.show(active['session'],dict(target_app='Isolated media target',score=0,required_score=80,reason='Offline overlay validation',robot_preview=True,update_only=True))
                elif action=='stop':
                    controller.stop();threading.Thread(target=api._window.destroy,daemon=True).start()
                else:return self.reply({'error':'Unknown validation event'},400)
                result=snapshot();states.append(dict(event=event,**result))
                (root/'states.json').write_text(json.dumps(states,indent=2),encoding='utf-8')
                return self.reply(result)
            if self.path=='/api/hourly/submit':
                try:
                    fields,files=parse_multipart_form(raw,self.headers.get('Content-Type',''))
                    upload=files.get('evidence') or {}
                    accepted=HourlySubmissionService(db,engine).accept(fields,file_bytes=upload.get('data'),filename=upload.get('filename',''),account_id='user_lok',device_id='windows_overlay_validation',platform='WINDOWS')
                    api.hourly_overlay_submitted(row['activity_id'],1)
                    api._window.evaluate_js("closeHourlySubmissionOverlay();")
                    (root/'accepted.json').write_text(json.dumps(accepted,indent=2),encoding='utf-8')
                    return self.reply(accepted)
                except HourlySubmissionError as error:return self.reply({'success':False,'error':str(error)},400)
            return self.reply({'success':False,'error':'Offline overlay validation'})

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    api._port=server.server_port
    threading.Thread(target=server.serve_forever,daemon=True).start()
    (root/'connection.json').write_text(json.dumps(dict(port=server.server_port,pid=__import__('os').getpid(),frozen=bool(getattr(sys,'frozen',False)))),encoding='utf-8')
    window=webview.create_window('StrictRecords — Offline Overlay Validation',url='http://127.0.0.1:'+str(server.server_port),js_api=api,width=1280,height=800,frameless=True,resizable=True,hidden='--hidden-ui-check' in sys.argv)
    api.set_window(window)
    window.events.minimized+=lambda:api._overlay_main_visibility(False)
    window.events.restored+=lambda:api._overlay_main_visibility(True)
    def loaded():
        # Isolated transport only. No main production state, sockets or cloud endpoints.
        window.evaluate_js("loadState=()=>{};loadStrictSessionData=()=>{};")
    window.events.loaded+=loaded
    try:webview.start(gui='edgechromium',storage_path=str(root/'webview'),private_mode=True)
    finally:
        (root/'final-state.json').write_text(json.dumps(snapshot(),indent=2),encoding='utf-8')
        controller.stop();server.shutdown()
