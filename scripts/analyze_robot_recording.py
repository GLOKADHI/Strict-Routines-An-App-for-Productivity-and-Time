"""Qualify a real Android robot-demo capture; zero-frame/screen-off runs cannot pass."""
import argparse,json,statistics,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('telemetry');p.add_argument('--video');p.add_argument('--output',required=True)
args=p.parse_args();data=json.loads(Path(args.telemetry).read_text(encoding='utf-8-sig'))
required={'ENTERING','WALKING','SLOWING_DOWN','STOPPING','TURNING','STANDING','GESTURING','SPEAKING','IDLE'}
samples=data.get('samples',[])
cruise=[s for s in samples if s['state']=='WALKING' and s['speed']>23]
idle=[s for s in samples if s['state']=='IDLE']
def fps(rows):
    return 3000*(len(rows)-1)/(rows[-1]['ms']-rows[0]['ms']) if len(rows)>1 and rows[-1]['ms']>rows[0]['ms'] else None
period=None
if len(cruise)>1:
    c0=cruise[0]['cycles']+cruise[0]['phase'];c1=cruise[-1]['cycles']+cruise[-1]['phase']
    if c1>c0:period=(cruise[-1]['ms']-cruise[0]['ms'])/1000/(c1-c0)
result={'device':data.get('device'),'sdk':data.get('sdk'),'frames':data.get('frames',0),
    'stages_seen':data.get('states',[]),'missing_stages':sorted(required-set(data.get('states',[]))),
    'completed_gait_cycles':max((s['cycles'] for s in samples),default=0),
    'ready_at_ms':data.get('ready_at_ms'),'cruise_cycle_seconds':period,
    'cruise_callback_fps_estimate':fps(cruise),'idle_callback_fps_estimate':fps(idle),
    'max_planted_foot_slide_units':data.get('max_planted_foot_slide_units'),
    'both_feet_airborne_frames':data.get('both_feet_airborne_frames'),
    'average_ui_draw_cpu_ms':data.get('average_ui_draw_cpu_ms'),
    'speech_source':data.get('speech_source'),'audible_tts':'NOT VERIFIED',
    'fixture_scoring_actions':False,'long_run_battery_drain':'NOT VERIFIED'}
if args.video:
    raw=subprocess.check_output(['C:/ffmpeg/bin/ffprobe.exe','-v','error','-show_entries','format=duration,size:stream=codec_name,width,height,nb_frames','-of','json',args.video])
    result['video']=json.loads(raw)
passed=(data.get('complete') and result['frames']>=200 and not result['missing_stages']
    and result['completed_gait_cycles']>=3 and result['max_planted_foot_slide_units']<=.01
    and result['both_feet_airborne_frames']==0 and samples[-1]['state']=='IDLE')
result['movement_verification']='PASS_RUNNING_APK_SEQUENCE' if passed else 'NOT_VERIFIED_INCOMPLETE_CAPTURE'
Path(args.output).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
raise SystemExit(0 if passed else 1)
