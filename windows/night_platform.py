"""Windows adapters for the existing watcher. No timers or scheduled tasks."""
import json
import os
import subprocess
from datetime import datetime,timedelta


def logon_session_identity():
    """Read this user's logon LUID; it changes after a Fast Startup sign-in."""
    if os.name!='nt':return ''
    import ctypes
    from ctypes import wintypes as w
    import struct
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);api=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel.GetCurrentProcess.restype=w.HANDLE
    api.OpenProcessToken.argtypes=[w.HANDLE,w.DWORD,ctypes.POINTER(w.HANDLE)]
    api.GetTokenInformation.argtypes=[w.HANDLE,w.DWORD,w.LPVOID,w.DWORD,ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    token=w.HANDLE();size=w.DWORD()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(),8,ctypes.byref(token)):return ''
    try:
        api.GetTokenInformation(token,10,None,0,ctypes.byref(size))
        if size.value<16:return ''
        buffer=ctypes.create_string_buffer(size.value)
        if not api.GetTokenInformation(token,10,buffer,size,ctypes.byref(size)):return ''
        low,high=struct.unpack_from('<II',buffer.raw,8)
        return f'{high:08x}:{low:08x}'
    finally:kernel.CloseHandle(token)


def request_shutdown(run,force_unrelated=False):
    if run.get('controlled'):return dict(requested=False,reason='Controlled run suppresses OS shutdown')
    # /t > 0 implicitly forces closure; /t 0 preserves the explicit safety flag.
    args=[os.path.join(os.environ.get('SystemRoot','C:/Windows'),'System32/shutdown.exe'),'/s','/t','0']
    if force_unrelated:args.append('/f')
    result=subprocess.run(args,capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return dict(requested=result.returncode==0,returncode=result.returncode)


def shutdown_evidence(run,boot_time):
    """Require a prior request, clean OS stop and subsequent startup in order."""
    if not run.get('request_timestamp'):return False
    start=datetime.fromisoformat(run['request_timestamp']);end=datetime.now().astimezone()+timedelta(minutes=1)
    script="""$events=Get-WinEvent -FilterHashtable @{LogName='System';Id=1074,6006,6005;StartTime=[datetime]'%s';EndTime=[datetime]'%s'} -ErrorAction Stop; @($events | Select-Object Id,TimeCreated,Message) | ConvertTo-Json -Compress"""%(start.isoformat(),end.isoformat())
    try:
        proc=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=12,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if proc.returncode:return False
        events=json.loads(proc.stdout);events=events if isinstance(events,list) else [events]
        # PowerShell JSON dates have /Date(milliseconds)/ form on Windows 5.
        import re
        def when(e):
            value=e['TimeCreated'];match=re.search(r'Date\((\d+)',value)
            return int(match[1])/1000 if match else datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
        requested=[when(e) for e in events if e['Id']==1074 and 'shutdown.exe' in str(e.get('Message','')).lower()]
        stopped=[when(e) for e in events if e['Id']==6006]
        started=[when(e) for e in events if e['Id']==6005]
        return any(start.timestamp()-.5<=a<=start.timestamp()+30 and a<=b<=a+600 and b<c<=end.timestamp() for a in requested for b in stopped for c in started)
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):return False


def close_targets(run,settings,force=False,exclusions=()):
    """Close explicit targets, or only verified owned identities in a live run."""
    from windows.strictbot_targets import capture_application,close_application
    from windows.strictbot_process import protected
    import psutil
    results=[]
    if run.get('controlled'):
        identities=json.loads(run['process_scope'])
        for identity in identities:
            if not identity.get('scoped'):raise ValueError('Controlled runs require descendant-only process scope')
    else:
        from core.windows_app_catalog import BROWSERS
        names={str(v).lower().strip() for v in [*settings.restricted_windows_apps,*settings.local_music_windows_apps] if str(v).lower().strip().endswith('.exe')}
        names.update(BROWSERS)  # Whole-browser closing belongs only to this saved Night stage.
        identities=[];seen=set()
        for process in psutil.process_iter(['pid','name','create_time']):
            name=str(process.info['name'] or '').lower()
            if name not in names or name in seen or protected(name,exclusions):continue
            try:identities.append(capture_application(process.pid,name,process.info['create_time'],exclusions));seen.add(name)
            except (psutil.Error,OSError,ValueError):results.append(dict(name=name,exited=False,status='BROWSER_CLOSE_FAILED'))
    for identity in identities:
        exited=close_application(identity,force=force,exclusions=exclusions)
        results.append(dict(name=identity['root']['name'],pid=identity['root']['pid'],exited=exited,status='EXIT_VERIFIED' if exited else 'BROWSER_CLOSE_FAILED'))
    return results


def finalize_when_browsers_closed(store,run):
    """An exited browser cannot wake MV3. Reconcile its durable checkpoints.

    This is allowed only when every configured browser process is verifiably
    absent. A live/inaccessible browser still requires its extension commit.
    """
    if run.get('controlled'):return
    from core.windows_app_catalog import BROWSERS
    from windows.night_shutdown import timestamp
    import psutil
    try:
        if any(str(p.info.get('name') or '').lower() in BROWSERS for p in psutil.process_iter(['name'])):return
    except psutil.Error:return
    with store.connect() as c:
        profiles=[r[0] for r in c.execute('SELECT profile_id FROM night_profiles WHERE run_id=?',(run['run_id'],))] or ['windows-no-browser']
        checkpoints=[json.loads(r[0]) for r in c.execute('SELECT payload FROM night_checkpoints')]
    for profile in profiles:
        existing=[r for r in store.history(profile) if r['session_id'].startswith(run['run_id']+':'+profile+':')]
        if existing:store.commit_profile(run['run_id'],profile,[r['session_id'] for r in existing]);continue
        candidates=[r for r in checkpoints if r['profile_id']==profile and r['session_id'].startswith(run['run_id']+':')]
        stamp=timestamp()
        if candidates:
            item=max(candidates,key=lambda r:r.get('observed_timestamp',r['capture_timestamp']))
            item=dict(item,capture_status='LAST_OBSERVED',observed_timestamp=item['capture_timestamp'],extension_storage_status='CHECKPOINT_PERSISTED_RECONCILE_RECORD_ON_STARTUP')
        else:
            item=dict(session_id=run['run_id']+':'+profile+':none',profile_id=profile,tab_id=-1,document_id='none',capture_status='NO_ACTIVE_YOUTUBE_VIDEO',extension_storage_status='NOT_APPLICABLE_NO_BROWSER')
        item.update(capture_timestamp=stamp,capture_date=stamp[:10],timezone=str(datetime.now().astimezone().tzinfo),capture_source='WINDOWS_VERIFIED_BROWSER_EXIT',capture_delay_ms=max(0,int(datetime.now().timestamp()*1000)-run['capture_ms']))
        store.save(item);store.commit_profile(run['run_id'],profile,[item['session_id']])
