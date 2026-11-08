"""Offline native 3D diagnostic mode. Exits before database, server or monitoring startup."""
import argparse
import json
from pathlib import Path
import queue
import time
from windows.strictbot_native import NativePresentation
from windows.strictbot_preferences import Preferences


def main():
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('StrictRecords.StrictBot.Preview')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strictbot-preview',action='store_true')
    parser.add_argument('--strictbot-settings',action='store_true')
    parser.add_argument('--stage',choices=('entry','walking','slow_down','stop','turn','face_front','point','speak','idle'))
    parser.add_argument('--duration',type=float,default=0)
    parser.add_argument('--preferences')
    parser.add_argument('--report')
    args=parser.parse_args()
    events=queue.Queue()
    presentation=NativePresentation(events.put,Preferences(args.preferences))
    if args.strictbot_settings:presentation.settings()
    else:
        presentation.show(-1,dict(target_app='StrictBot Preview',preview=True))
        if args.stage:presentation.host.DebugStage(args.stage)
    started=time.monotonic()
    try:
        while not args.duration or time.monotonic()-started<args.duration:
            try:event=events.get(timeout=.1)
            except queue.Empty:continue
            action=event.get('action')
            if action=='native_error':raise RuntimeError(str(event.get('values','Native presentation failed')))
            if action=='home':break
            if action=='close':presentation.host.Result(-1,False,'PREVIEW — No restricted application is attached.',False)
            if action=='settings_closed' and args.strictbot_settings and not presentation.metrics().get('visible'):break
            if action=='save_settings':
                try:
                    presentation.store.save(event.get('values') or {})
                    presentation.configure()
                    presentation.host.SettingsSaved(True,'Settings saved')
                except OSError:
                    presentation.host.SettingsSaved(False,'Could not save. Try again.')
        if args.report:
            report=presentation.metrics()
            report.update(mode='native_3d_preview',production_policy_changed=False,processes_closed=False)
            Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
    finally:presentation.stop()


if __name__=='__main__':main()
