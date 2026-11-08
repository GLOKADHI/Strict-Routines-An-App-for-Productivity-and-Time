"""Test the actual running watcher and isolated Test Mode without fake live points."""
from pathlib import Path
import json
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
API='http://127.0.0.1:8765'

def status():return json.load(urllib.request.urlopen(API+'/api/restriction/status',timeout=6))

def attempt(number,media=True):
    import psutil
    folder=ROOT/'.codex-work/night-running-watcher'/str(number)
    folder.mkdir(parents=True,exist_ok=True)
    name='vlc.exe' if media else 'chrome.exe'
    args=['C:/Program Files (x86)/VideoLAN/VLC/vlc.exe','--intf=dummy','--no-one-instance','--no-media-library'] if media else ['C:/Program Files/Google/Chrome/Application/chrome.exe','--headless=new','--no-first-run','--user-data-dir='+str(folder),'about:blank']
    process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    started=time.monotonic();identity=None
    try:
        from windows.strictbot_targets import capture_application,verify_application_exit,close_application
        try:identity=capture_application(process.pid,name,psutil.Process(process.pid).create_time(),scoped=True)
        except psutil.NoSuchProcess:pass
        while time.monotonic()-started<(25 if media else 6):
            exited=verify_application_exit(identity) if identity else process.poll() is not None
            if exited:
                if not media:raise RuntimeError('A daytime browser was closed by production enforcement')
                return dict(application=name,pid=process.pid,exit_verified=True,elapsed_seconds=round(time.monotonic()-started,3))
            time.sleep(.3)
        if not media:return dict(application=name,pid=process.pid,remained_running=True,daytime_browser_access=True,observed_seconds=round(time.monotonic()-started,3))
        raise RuntimeError('The actual production watcher did not close its restricted media target')
    finally:
        if identity and not verify_application_exit(identity):close_application(identity,force=True)
        elif not identity and process.poll() is None:process.terminate()

def main():
    import sys
    sys.path.insert(0,str(ROOT))
    before=status();assert before['simulated_test_mode'] is False
    daytime=attempt(0,media=False)
    first=attempt(1)
    request=urllib.request.Request(API+'/api/restriction/simulate_restore',data=json.dumps({'score':999}).encode(),headers={'Content-Type':'application/json','X-StrictRoutines-Setup':'1'},method='POST')
    preview=json.load(urllib.request.urlopen(request,timeout=6))
    assert preview['mode']=='TEST_MODE' and preview['production_unchanged'] is True
    after=status();assert after['simulated_test_mode'] is False and after['current_score']==before['current_score']
    second=attempt(2)
    result=dict(real_production_service=True,actual_score=before['current_score'],required_score=before['required_score'],daytime_browser=daytime,before=first,isolated_test_response=preview,after_test_mode=second,production_points_unchanged=True,os_shutdown_called=False)
    path=ROOT/'reports/night-production-20261101/running-production-watcher.json';path.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':main()
