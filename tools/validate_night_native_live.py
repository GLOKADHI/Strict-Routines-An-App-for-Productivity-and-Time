"""Real, headless process checks; no UI inputs, fake scores or OS shutdown.

Only processes started by this script are attached. User browser instances and
the original points database are never written or terminated.
"""
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def main():
    import sys
    sys.path.insert(0,str(ROOT))
    from core.database import StrictRecordsDB
    from core.scoring_engine import ScoringEngine
    from core.windows_app_catalog import apply_production_catalog
    from windows.restriction_manager import YouTubeRestrictionManager
    from windows.strictbot_targets import capture_application,verify_application_exit,close_application
    from windows.night_shutdown import NightStore
    import psutil
    work=ROOT/'.codex-work/night-live-native';work.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect('file:'+str(ROOT/'data/strictrecords.db')+'?mode=ro',uri=True) as source,sqlite3.connect(work/'current-data-copy.sqlite') as target:source.backup(target)
    db=StrictRecordsDB(str(work/'current-data-copy.sqlite'))
    with patch('windows.night_shutdown.NightStore',return_value=NightStore(work/'night.sqlite')):apply_production_catalog(db)
    manager=YouTubeRestrictionManager(db,ScoringEngine(db=db,storage_root=str(work/'evidence')))
    points=manager.get_points_status(force=True);results=[]
    programs=[('chrome.exe',Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),lambda n:['--headless=new','--no-first-run','--user-data-dir='+str(work/f'chrome-{n}'),'about:blank']),
        ('vlc.exe',Path('C:/Program Files (x86)/VideoLAN/VLC/vlc.exe'),lambda n:['--intf=dummy','--no-one-instance','--no-media-library'])]
    for name,executable,args in programs:
        if not executable.is_file():results.append(dict(application=name,status='NOT_INSTALLED'));continue
        for attempt in range(2):
            child=subprocess.Popen([str(executable),*args(attempt)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            identity=None
            try:
                time.sleep(.8)
                identity=capture_application(child.pid,name,expected_started=psutil.Process(child.pid).create_time(),scoped=True)
                decision=manager._configured_native_decision(name,0,{})
                if not decision or decision.get('action')!='CLOSE_APP':raise AssertionError('Current real-data policy did not authorize closure')
                closed=manager.close_restricted_window(0,child.pid,name,action='CLOSE_APP',target_app=decision['target_app'],process_identity=identity)
                exited=verify_application_exit(identity)
                results.append(dict(application=name,attempt='REOPEN' if attempt else 'INITIAL',pid=child.pid,decision=decision['action'],required_score=decision.get('required_score'),policy_reason=decision.get('reason'),close_returned=closed,exit_verified=exited,scope='OWNED_PROCESS_AND_DESCENDANTS'))
                if not closed or not exited:raise AssertionError('Actual process exit was not verified')
            finally:
                if identity is not None and not verify_application_exit(identity):close_application(identity,force=True)
                elif identity is None and child.poll() is None:child.terminate()
    result=dict(mode='PRODUCTION_COMPONENT_LIVE_VALIDATION',score_source='READ_ONLY_SNAPSHOT_OF_CURRENT_USER_DATA',score=points,process_checks=results,windows_shutdown_requested=False,
        youtube_playback_tested=False,limitations=['Headless native process checks do not validate YouTube playback or extension popup persistence.','Normal production access decisions and real process closing were exercised; the active protected EXE was not replaced.'])
    path=ROOT/'reports/night-production-20261101/native-live-results.json';path.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
