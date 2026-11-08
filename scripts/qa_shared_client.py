"""Render the real shared-client assets against an isolated backend/database."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine
from core.server import StrictRecordsRequestHandler
from playwright.sync_api import sync_playwright


def main():
    report=Path(sys.argv[1]).resolve();report.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        db=StrictRecordsDB(str(Path(directory)/'qa.sqlite'))
        class Handler(StrictRecordsRequestHandler):
            def _get_windows_submission_identity(self):
                return 'user_lok','fixture-windows'
        Handler.db=db;Handler.engine=ScoringEngine(db,storage_root=str(Path(directory)/'evidence'))
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        from core.client_account import ClientAccountConnection
        from core.google_auth import GoogleAuthConfig,GoogleAuthManager
        auth=GoogleAuthManager(GoogleAuthConfig(str(Path(directory)/'client.json')),
            session_file=str(Path(directory)/'session.enc'))
        server.client_account_connection=ClientAccountConnection(db,auth=auth)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        results=[];errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,channel='msedge')
                for width in [1180,800,420]:
                    page=browser.new_page(viewport={'width':width,'height':950})
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/client-assets/client.html',wait_until='networkidle',timeout=20000)
                    page.wait_for_function("document.getElementById('connection').textContent.startsWith('Connected')",timeout=10000)
                    for section in ['overview','routine','calendar','validation','account','demo']:
                        page.locator(f'nav button[data-view="{section}"]').click()
                        page.wait_for_timeout(150)
                        overflow=page.evaluate('document.documentElement.scrollWidth > innerWidth')
                        if overflow:raise AssertionError(f'Horizontal overflow at {width}px in {section}')
                        page.screenshot(path=str(report/f'client-{width}-{section}.png'),full_page=True)
                    page.locator('nav button[data-view="routine"]').click()
                    page.locator('#gym').select_option('YES')
                    assert page.locator('#gymTiming').is_visible()
                    page.locator('#gym').select_option('NO')
                    assert not page.locator('#gymTiming').is_visible()
                    results.append({'width':width,'sections_rendered':6,'horizontal_overflow':False,'gym_conditional_controls':'PASS'})
                    page.close()
                browser.close()
        finally:
            server.shutdown();server.server_close();worker.join(3)
    output={'isolated_database':True,'live_user_records_modified':False,'screens':results,'javascript_errors':errors,
            'oauth_consent_performed':False,'demo_activation_consumed':False}
    (report/'shared-client-ui-results.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps(output,indent=2))
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
