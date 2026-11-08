"""Start the private demo helper; optionally register this helper at user logon."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


def healthy(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
            return json.load(response).get('service') == 'paired-demo-access'
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--register-startup', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    database = Path(args.database).resolve(strict=True)
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    if not pythonw.is_file():
        raise RuntimeError('Windows Python runtime unavailable')
    command = [str(pythonw), str(root / 'scripts/run_paired_demo_service.py'),
               '--database', str(database), '--port', str(args.port)]
    started = None
    if not healthy(args.port):
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0
        started = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=info)
        for _ in range(25):
            if healthy(args.port):
                break
            if started.poll() is not None:
                break
            time.sleep(.2)
    if not healthy(args.port):
        if started and started.poll() is None:
            started.terminate()
        raise RuntimeError('Demo helper did not start; no startup entry installed')
    if args.register_startup:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            value = subprocess.list2cmdline(command)
            try:
                existing, _ = winreg.QueryValueEx(key, 'StrictRoutinesDemoAccess')
            except FileNotFoundError:
                existing = value
            if existing != value:
                raise RuntimeError('A different demo startup entry already exists; left unchanged')
            winreg.SetValueEx(key, 'StrictRoutinesDemoAccess', 0, winreg.REG_SZ, value)
    print(json.dumps({'healthy': True, 'listen': f'127.0.0.1:{args.port}',
                      'started_pid': started.pid if started else None,
                      'user_logon_startup': args.register_startup}))


if __name__ == '__main__':
    main()
