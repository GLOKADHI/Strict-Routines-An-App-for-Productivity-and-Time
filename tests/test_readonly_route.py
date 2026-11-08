from pathlib import Path
from types import SimpleNamespace
import unittest,ast,textwrap,sys,json
from unittest.mock import patch
r=Path(__file__).parent
real=r.parent
sys.path.insert(0,str(real))
server=(real/'core/server.py').read_text(encoding='utf-8')
a=server.index('        if path == "/api/restrictions/desktop_access":')
b=server.index('        if path in ("/api/restrictions/status"',a)
scope={};exec('def route(self,path):\n'+textwrap.indent(textwrap.dedent(server[a:b]),'    '),scope)
desktop=ast.parse((real/'desktop_app.py').read_text(encoding='utf-8'))
cls=next(n for n in desktop.body if isinstance(n,ast.ClassDef) and n.name=='DesktopApi')
method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='get_desktop_restriction_status')
import urllib.request
client_scope={'urllib':__import__('urllib'),'json':json}
exec(compile(ast.Module(body=[method],type_ignores=[]),'<native bridge>','exec'),client_scope)
class ReadonlyRouteTests(unittest.TestCase):
    def call(self,local,engine):
        results=[];h=SimpleNamespace(_is_local_request=lambda:local,restriction_mgr=engine,_send_json=lambda data,**kw:results.append((data,kw)))
        scope['route'](h,'/api/restrictions/desktop_access');return results[0]
    def test_local_route_returns_live_engine_snapshot(self):
        expected={'success':True,'source':'WINDOWS_BROWSER_ACCESS_ENGINE','status':{'youtube':{'access':'BLOCKED'}}}
        self.assertEqual(self.call(True,SimpleNamespace(get_desktop_access_status=lambda:expected)),(expected,{}))
    def test_nonlocal_request_is_rejected(self):
        self.assertEqual(self.call(False,None)[1]['status'],403)
    def test_unavailable_engine_is_not_saved_allow(self):
        data,status=self.call(True,None);self.assertFalse(data['success']);self.assertEqual(status['status'],503)
    def test_attached_exe_uses_readonly_route_not_browser_visit(self):
        fake=SimpleNamespace(restriction_mgr=None)
        payload={'success':True,'source':'WINDOWS_BROWSER_ACCESS_ENGINE','status':{'youtube':{'access':'BLOCKED'}}}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(payload).encode()
        with patch.dict(sys.modules,{'core.server':SimpleNamespace(StrictRecordsRequestHandler=fake)}),patch('urllib.request.urlopen',return_value=Response()) as request:
            self.assertEqual(client_scope['get_desktop_restriction_status'](SimpleNamespace(_host='127.0.0.1',_port=8765)),payload)
            self.assertEqual(request.call_args.args[0].full_url,'http://127.0.0.1:8765/api/restrictions/desktop_access')
            self.assertEqual(request.call_args.kwargs['timeout'],5.0)
    def test_embedded_exe_reads_existing_engine_directly(self):
        payload={'success':True};fake=SimpleNamespace(restriction_mgr=SimpleNamespace(get_desktop_access_status=lambda:payload))
        with patch.dict(sys.modules,{'core.server':SimpleNamespace(StrictRecordsRequestHandler=fake)}),patch('urllib.request.urlopen') as request:
            self.assertEqual(client_scope['get_desktop_restriction_status'](SimpleNamespace()),payload);request.assert_not_called()
if __name__=='__main__':unittest.main(verbosity=2)
