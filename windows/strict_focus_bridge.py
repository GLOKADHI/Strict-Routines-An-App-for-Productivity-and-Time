"""Authenticated loopback bridge from the development backend to the EXE view.

Only embedded-focus presentation crosses this bridge. The backend retains all
session, hardware, proof, score and admin authorization decisions.
"""
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import urllib.error
import urllib.request


def descriptor_path():
    from core.database import _default_database_path
    return Path(_default_database_path()).parent/'strict-focus-desktop-bridge.json'


def request_json(url,body,token=None,timeout=3):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request(url,data=json.dumps(body).encode(),headers=headers,method='POST')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=timeout) as response:return json.load(response)


class RemoteEmbeddedStrictFocus:
    def __init__(self,path=None):
        self.path=Path(path) if path else descriptor_path()
        self._owner_pid=None

    def _call(self,action,body=None,timeout=3):
        descriptor=json.loads(self.path.read_text(encoding='utf-8'))
        port=descriptor.get('port');token=descriptor.get('token','')
        if not isinstance(port,int) or isinstance(port,bool) or not 1<=port<=65535 or not isinstance(token,str) or len(token)!=64:
            raise ValueError('Invalid desktop focus bridge')
        result=request_json(f'http://127.0.0.1:{port}/focus/{action}',body or {},token,timeout)
        if result.get('instance')!=descriptor.get('instance'):raise ValueError('Desktop focus bridge changed')
        self._owner_pid=result.get('owner_pid')
        return result

    def available(self):
        try:return self._call('status',timeout=1).get('available') is True
        except Exception:return False

    def owns_foreground(self,pid):
        if self._owner_pid is None:self.available()
        return pid==self._owner_pid

    def focus(self):
        try:return self._call('focus').get('success') is True
        except Exception:return False

    def open(self,session_id,complete):
        # The callback remains backend-authorized. The EXE's button invokes the
        # existing local complete endpoint with this exact session ID.
        try:return self._call('open',dict(session_id=session_id),timeout=19)
        except Exception:return dict(success=False,error='The Windows focus view could not be reached. Keep the EXE open and retry.')

    def close(self):
        try:self._call('close')
        except Exception:pass

    def update(self,status,hardware):
        try:self._call('update',dict(status=status,hardware=hardware),timeout=1)
        except Exception:pass

    def integrity_events(self):
        try:return self._call('events',timeout=1).get('events',[])
        except Exception:return []

    def show(self):
        try:return self._call('show').get('success') is True
        except Exception:return False

    def update_setup_preview(self,preview):
        try:self._call('setup_preview',dict(preview=preview),timeout=.7)
        except Exception:pass


class DesktopFocusBridge:
    def __init__(self,api,controller,path=None):
        self.api,self.controller=api,controller
        self.path=Path(path) if path else descriptor_path()
        self.token=secrets.token_hex(32);self.instance=secrets.token_hex(16)
        self.server=None
        from windows.strict_setup_preview import SetupPreviewCache
        self.api._strict_setup_preview=SetupPreviewCache()

    def complete(self,session_id):
        origin=f'http://{self.api._host}:{self.api._port}'
        request=urllib.request.Request(origin+'/api/strict_session/complete',data=json.dumps(dict(session_id=session_id)).encode(),
            headers={'Content-Type':'application/json','Origin':origin,'X-StrictRoutines-Study':'1'},method='POST')
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=10) as response:return json.load(response)
        except Exception:return dict(success=False,error='Completion was not confirmed by the session backend.')

    def dispatch(self,action,body):
        if action=='setup_preview':
            self.api._strict_setup_preview.update(body.get('preview'))
            return dict(success=True)
        if action=='status':return dict(success=True,available=bool(self.controller.available()))
        if action=='open':
            session_id=body.get('session_id')
            if not isinstance(session_id,str) or not session_id.startswith('strict_sess_') or len(session_id)>128:
                raise ValueError('Invalid Strict Session identity')
            return self.controller.open(session_id,self.complete)
        if action=='close':self.controller.close();return dict(success=True)
        if action=='show':return dict(success=bool(self.controller.show()))
        if action=='focus':return dict(success=bool(self.controller.focus()))
        if action=='events':return dict(success=True,events=self.controller.integrity_events())
        if action=='update':
            status,hardware=body.get('status'),body.get('hardware')
            if not isinstance(status,dict) or not isinstance(hardware,dict):raise ValueError('Invalid focus status')
            self.controller.update(status,hardware);return dict(success=True)
        raise ValueError('Unknown focus action')

    def start(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                if self.client_address[0]!='127.0.0.1' or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+owner.token):
                    # Drain bounded rejected bodies so Windows does not replace
                    # the explicit 403 with a reset caused by unread socket data.
                    try:
                        size=int(self.headers.get('Content-Length','0'))
                        if 0<=size<=128*1024:
                            self.connection.settimeout(1);self.rfile.read(size)
                    except (ValueError,OSError):pass
                    self.send_error(403);return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<=size<=128*1024:raise ValueError('Focus request too large')
                    body=json.loads(self.rfile.read(size) or '{}')
                    if not isinstance(body,dict) or not self.path.startswith('/focus/'):raise ValueError('Invalid focus request')
                    result=owner.dispatch(self.path[7:],body);code=200
                except (ValueError,TypeError,KeyError):result=dict(success=False,error='Invalid focus request');code=400
                except Exception:result=dict(success=False,error='Windows focus view unavailable');code=503
                result['instance']=owner.instance
                result['owner_pid']=os.getpid()
                raw=json.dumps(result).encode();self.send_response(code)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
                self.send_header('Connection','close');self.end_headers();self.wfile.write(raw)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.new')
        temporary.write_text(json.dumps(dict(port=self.server.server_port,token=self.token,instance=self.instance,pid=os.getpid())),encoding='utf-8')
        os.replace(temporary,self.path)
        threading.Thread(target=self.server.serve_forever,name='StrictFocusDesktopBridge',daemon=True).start()

    def stop(self):
        if self.server:self.server.shutdown();self.server.server_close()
        try:
            if json.loads(self.path.read_text(encoding='utf-8')).get('instance')==self.instance:self.path.unlink()
        except (OSError,ValueError):pass
