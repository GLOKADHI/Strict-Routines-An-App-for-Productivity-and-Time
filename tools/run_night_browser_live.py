"""Controlled real-browser Night run. OS shutdown is always suppressed.

Uses actual extension captures, the production coordinator and verified close
helper. Only the explicitly selected Comet executable/user/session is attached.
"""
from pathlib import Path
import json
import psutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from windows.night_shutdown import NightStore,NightCoordinator,canonical
from windows.night_platform import close_targets
from windows.strictbot_targets import capture_application,verify_application_exit
from windows.restriction_popup import RestrictionPopupController

def main():
    profile=sys.argv[1];executable=Path(sys.argv[2]).resolve();store=NightStore()
    attached=[];closures=[]
    def identities():
        result=[]
        for process in psutil.process_iter(['pid','name','exe','cmdline']):
            try:
                if Path(process.info['exe'] or '').resolve()!=executable:continue
                if any(str(v).startswith('--type=') for v in process.info['cmdline'] or []):continue
                item=capture_application(process.pid,process.name(),process.create_time(),scoped=True)
                result.append(item)
            except (psutil.Error,OSError,ValueError):continue
        return result
    initial=identities()
    if not initial:raise RuntimeError('The selected real browser is not running')
    now=int(time.time()*1000);capture_after=int(sys.argv[3]) if len(sys.argv)>3 else 70;end_after=int(sys.argv[4]) if len(sys.argv)>4 else 110
    run=store.controlled_run(profile,initial,now+5000,now+capture_after*1000,now+end_after*1000)
    retained=list(initial)
    def close(current):
        live=[item for item in retained if not verify_application_exit(item)]
        known={item["root"]["pid"] for item in live}
        live.extend(item for item in identities() if item["root"]["pid"] not in known)
        retained[:]=live
        items=live;current=dict(current,process_scope=canonical(items))
        results=close_targets(current,None,force=True)
        if results:closures.extend(results);print(json.dumps(dict(event='ACTUAL_CLOSE',at=time.time(),results=results)),flush=True)
        return results
    popup=RestrictionPopupController(lambda *_:False)
    coordinator=NightCoordinator(store,close,lambda *_:(_ for _ in ()).throw(AssertionError('OS shutdown must not be called')),popup.show_night,psutil.boot_time)
    print(json.dumps(dict(event='READY',run_id=run['run_id'],monitor_ms=run['monitor_ms'],capture_ms=run['capture_ms'],shutdown_ms=run['shutdown_ms'])),flush=True)
    try:
        while int(time.time()*1000)<=run['shutdown_ms']+5000:
            coordinator.tick();current=store.run(run['run_id'])
            if current['state']=='COMPLETE':break
            time.sleep(.5)
    finally:popup.stop()
    result=dict(run=store.run(run['run_id']),records=[r for r in store.history(profile) if r['session_id'].startswith(run['run_id']+':')],actual_closures=closures,windows_shutdown_called=False)
    path=ROOT/'reports/night-production-20261101/browser-live-results.json';path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(dict(event='DONE',report=str(path),captures=len(result['records']),closures=len(closures))),flush=True)

if __name__=='__main__':main()
