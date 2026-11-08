"""Send lifecycle events only to the opt-in offline EXE test harness. No UI input."""
import argparse
import json
from pathlib import Path
import time
import urllib.request

parser=argparse.ArgumentParser()
parser.add_argument('action',choices=('state','walking','speech','robot','hourly','repeat','resolve','expire','stop'))
parser.add_argument('--folder',default='reports/desktop-overlay-20261102/exe-live')
parser.add_argument('--save')
args=parser.parse_args()
root=Path(args.folder).resolve()
assert root.is_relative_to(Path.cwd()/'reports/desktop-overlay-20261102')
connection=json.loads((root/'connection.json').read_text())
url='http://127.0.0.1:'+str(int(connection['port']))

def state():return json.loads(urllib.request.urlopen(url+'/validation/state',timeout=5).read())
def event(action):
    request=urllib.request.Request(url+'/validation/event',data=json.dumps({'action':action}).encode(),headers={'Content-Type':'application/json'})
    return json.loads(urllib.request.urlopen(request,timeout=5).read())

if args.action in ('walking','speech'):
    event('expire');event('robot')
    desired='walking' if args.action=='walking' else 'speak'
    deadline=time.monotonic()+35
    while time.monotonic()<deadline:
        current=state()
        if current['native'].get('stage')==desired:break
        time.sleep(.08)
    else:raise RuntimeError('The real robot did not reach '+desired)
    event('hourly')
    time.sleep(.3)
    result=state();result['hourly_triggered_during']=desired
else:
    result=state() if args.action=='state' else event(args.action)
    if args.action!='state':time.sleep(.35);result=state()
if args.save:(root/args.save).write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
