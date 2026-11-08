"""Explicit offline UI fixture: no production database, camera, OS controls or sync."""
import json
import mimetypes
import sys
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main(directory):
    import webview
    from core.ui_dashboard import DASHBOARD_HTML
    from windows.strictbot_native import NativePresentation
    target=Path(directory).resolve();target.mkdir(parents=True,exist_ok=True)
    fixture=target/'ui-state.json'
    actions=target/'ui-actions.jsonl'
    class API:
        def window_minimize(self):pass
        def window_maximize(self):pass
        def window_close(self):window.destroy()
    serve_only='--serve-only' in sys.argv
    native=None if serve_only else NativePresentation(lambda event:actions.open('a',encoding='utf-8').write(json.dumps(event)+'\n'))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def reply(self,value,mime='application/json'):
            raw=json.dumps(value).encode() if mime=='application/json' else value
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
            self.send_header('Connection','close');self.send_header('Cache-Control','no-store');self.end_headers()
            self.wfile.write(raw);self.wfile.flush();self.close_connection=True
        def do_GET(self):
            if self.path=='/':
                script="<script>window.addEventListener('load',()=>{navAppPage('strict-session');loadStrictSessionData();setInterval(loadStrictSessionData,2000);});window.addEventListener('error',e=>fetch('/validation/error',{method:'POST',body:JSON.stringify({error:e.message,line:e.lineno})}));</script>"
                return self.reply(DASHBOARD_HTML.replace('</body>',script+'</body>').encode(),'text/html; charset=utf-8')
            if self.path.startswith('/api/strict_session/status'):
                data=json.loads(fixture.read_text())
                s=data['workflow']
                if native:
                    native.host.StudyStatus(json.dumps(dict(visible=s.get('show_hud',False),session_id=s.get('session_id'),phase='OFFLINE VALIDATION · '+s['state'].replace('_',' '),timer='15:00' if not s.get('focus_completed_at') else 'Study complete',hardware='Simulated camera · Bluetooth · Battery 82%',message='Offline UI fixture. No production session or reward.',end_enabled=s.get('end_enabled',False))))
                return self.reply(data)
            path=(ROOT/self.path.split('?',1)[0].lstrip('/')).resolve()
            if path.is_relative_to(ROOT) and path.is_file() and path.suffix in {'.png','.ico','.css','.js'}:
                return self.reply(path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            return self.reply({'success':False,'error':'Offline UI fixture','devices':[],'browsers':[],'targets':[]})
        def do_POST(self):
            raw=self.rfile.read(min(int(self.headers.get('Content-Length') or 0),8192))
            with actions.open('a',encoding='utf-8') as out:out.write(json.dumps({'path':self.path,'body':raw.decode(errors='replace')})+'\n')
            return self.reply({'success':True,'message':'Offline fixture action recorded; no production state changed.'})
    port=int(sys.argv[sys.argv.index('--port')+1]) if '--port' in sys.argv else 0
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    (target/'ui-connection.json').write_text(json.dumps({'pid':__import__('os').getpid(),'port':server.server_port,'production_services':False}))
    if serve_only:
        try:threading.Event().wait()
        finally:server.shutdown()
        return
    window=webview.create_window('StrictRecords — Strict Study UI Validation',url='http://127.0.0.1:'+str(server.server_port),js_api=API(),width=1180,height=850,resizable=True)
    try:webview.start(gui='edgechromium',private_mode=True,storage_path=str(target/'webview'))
    finally:native.stop();server.shutdown()


if __name__=='__main__':main(sys.argv[1])
