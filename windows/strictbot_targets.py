"""Verified application identities shared by automatic closing and the native CLOSE IT action."""
import logging
import os
import time
import ctypes
from windows.strictbot_process import protected

log=logging.getLogger("StrictBotTargets")


def _path(value):return os.path.normcase(os.path.normpath(value))


def _session(pid):
    value=ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(pid,ctypes.byref(value)):
        raise OSError("Cannot verify the Windows process session.")
    return value.value


def _identity(process):
    return dict(pid=process.pid,started=process.create_time(),path=_path(process.exe()),
                name=process.name().lower(),parent=process.ppid(),user=process.username(),session=_session(process.pid))


def capture_application(pid,name,expected_started=None,exclusions=(),scoped=False):
    import psutil
    if pid in (0,os.getpid()) or protected(name,exclusions):
        raise ValueError("This process is protected.")
    process=psutil.Process(pid)
    root=_identity(process)
    if root["user"].casefold()!=psutil.Process().username().casefold() or root["session"]!=_session(os.getpid()):
        raise ValueError("The application belongs to a different Windows user or session.")
    if root["name"]!=name.lower() or not root["path"]:
        raise ValueError("Application identity did not match.")
    if expected_started is not None and abs(root["started"]-expected_started)>.01:
        raise ValueError("The process ID now belongs to a different launch.")
    result=dict(root=root,members=[root],scoped=bool(scoped))
    result["members"]=_collect(result,exclusions)
    return result


def _matching_process(member,exclusions):
    import psutil
    try:
        process=psutil.Process(member["pid"])
        if abs(process.create_time()-member["started"])>.01:return None
        if (_path(process.exe())!=member["path"] or protected(process.name(),exclusions)
                or process.username()!=member["user"] or _session(process.pid)!=member["session"]):return None
        return process
    except psutil.NoSuchProcess:return None


def _collect(identity,exclusions=()):
    """Refresh same-executable instances and retain verified children after their parent exits."""
    import psutil
    root=identity["root"]
    if protected(root["name"],exclusions):raise ValueError("Protected application.")
    members={}
    for item in identity.get("members",[]):
        if _matching_process(item,exclusions) is not None:members[item["pid"]]=item
    roots=[]
    if identity.get('scoped'):
        process=_matching_process(root,exclusions)
        if process is not None:roots.append(process)
    for process in psutil.process_iter(["pid","name"]):
        if identity.get('scoped'):break
        if str(process.info.get("name") or "").lower()!=root["name"]:continue
        try:
            if (_path(process.exe())==root["path"] and not protected(process.name(),exclusions)
                    and process.username()==root["user"] and _session(process.pid)==root["session"]):
                item=_identity(process);members[process.pid]=item;roots.append(process)
        except psutil.NoSuchProcess:continue
        # AccessDenied is intentionally not silently treated as an exited process.
    folder=os.path.dirname(root["path"])
    for process in roots:
        try:
            for child in process.children(recursive=True):
                try:
                    item=_identity(child)
                    if (not protected(item["name"],exclusions) and item["user"]==root["user"] and item["session"]==root["session"]
                            and os.path.commonpath((folder,item["path"]))==folder):
                        members[child.pid]=item
                except (psutil.NoSuchProcess,ValueError):continue
        except psutil.NoSuchProcess:continue
    return list(members.values())


def verify_application_exit(identity,exclusions=()):
    try:
        members=_collect(identity,exclusions)
        identity["members"]=members
        return not members
    except Exception:
        log.exception("Application exit could not be verified")
        return False


def close_application(identity,force=False,grace=.8,exclusions=()):
    import psutil
    import win32gui,win32process,win32con
    root=identity["root"]
    if protected(root["name"],exclusions) or root["pid"]==os.getpid():return False
    try:
        members=_collect(identity,exclusions)
        identity["members"]=members
        if not members:return True
        sent=[0];pids={m["pid"] for m in members}
        def graceful(window,extra):
            if win32gui.IsWindowVisible(window) and win32process.GetWindowThreadProcessId(window)[1] in pids:
                win32gui.PostMessage(window,win32con.WM_CLOSE,0,0);sent[0]+=1
        ready=time.monotonic()+min(.5,grace)
        while True:
            win32gui.EnumWindows(graceful,None)
            if sent[0] or time.monotonic()>=ready:break
            time.sleep(.04)
        processes=[p for p in (_matching_process(m,exclusions) for m in members) if p is not None]
        if processes:psutil.wait_procs(processes,timeout=grace)
        members=_collect(identity,exclusions);identity["members"]=members
        if force and members:
            by_pid={item["pid"]:item for item in members}
            def depth(item):
                seen=set();parent=item["parent"]
                while parent in by_pid and parent not in seen:
                    seen.add(parent);parent=by_pid[parent]["parent"]
                return len(seen)
            for item in sorted(members,key=depth,reverse=True):
                process=_matching_process(item,exclusions)
                if process is not None:
                    try:process.terminate()
                    except psutil.NoSuchProcess:pass
            processes=[p for p in (_matching_process(m,exclusions) for m in members) if p is not None]
            if processes:psutil.wait_procs(processes,timeout=1.2)
        return verify_application_exit(identity,exclusions)
    except Exception:
        log.exception("Verified application close failed for pid=%s",root.get("pid"))
        return False
