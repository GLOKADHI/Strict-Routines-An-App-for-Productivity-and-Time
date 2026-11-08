"""Fresh read-only Taufa check using the retained real ledger and exported cache.

Run from the project root. No production SQLite connection permits writes.
"""
from pathlib import Path
import argparse,contextlib,hashlib,json,sqlite3,uuid,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from datetime import datetime,timezone
from cloud_points.protocol import THREAD_ID,sanitize_envelope
from cloud_points.taufa_cached_session import CapturedSessionTaufa,default_vault_path
from core.android_ledger import PREFIX,checkpoint

def main():
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--output',required=True)
    p.add_argument('--session-file');p.add_argument('--account',default='user_lok');p.add_argument('--date')
    args=p.parse_args();database=Path(args.database).resolve();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    class ReadOnlyDB:
        @contextlib.contextmanager
        def _connect(self):
            conn=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
            try:yield conn
            finally:conn.close()
    db=ReadOnlyDB()
    date=args.date
    if not date:
        with db._connect() as conn:
            rows=conn.execute('SELECT value FROM system_config WHERE key LIKE ? ORDER BY updated_at DESC',(PREFIX+args.account+':%',))
            for row in rows:
                value=json.loads(row['value'])
                if not value.get('is_dev_mode'):
                    date=value['date'];break
    receipt=checkpoint(db,date,account_id=args.account) if date else None
    if not receipt:raise ValueError('No retained authoritative ledger checkpoint is available for a truthful live check')
    payload={'schema':1,'account_id':args.account,'device_id':receipt['device_id'],'date':date,'timezone':receipt['timezone'],
        'revision':receipt['revision'],'event_count':receipt['event_count'],'confirmed_score':receipt['confirmed_balance'],
        'raw_verified_points':receipt['raw_verified_points'],'score_events':receipt['transactions']}
    raw=json.dumps(payload);clean=sanitize_envelope({'payload_json':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()})
    events=json.loads(clean['payload_json'])['score_events']
    snapshot_file=out/'live-snapshot.json'
    if snapshot_file.exists():snapshot=json.loads(snapshot_file.read_text())
    else:
        snapshot={'request_id':'read_only_'+uuid.uuid4().hex,'thread_id':THREAD_ID,'ledger_revision':receipt['revision'],
            'authenticated_user_reference':args.account,'current_calculated_balance':receipt['confirmed_balance'],
            'last_successfully_synchronized_revision':0,'reporting_date':date,'events':events,
            'source_received_at':receipt['received_at'],'context':'HISTORICAL_RETAINED_CHECKPOINT_NOT_TODAYS_SCORE'}
        snapshot_file.write_text(json.dumps(snapshot,indent=2),encoding='utf-8')
    before=hashlib.sha256(database.read_bytes()).hexdigest();started=datetime.now(timezone.utc).isoformat()
    adapter=CapturedSessionTaufa(args.session_file or default_vault_path())
    report={'started_at':started,'reporting_date':date,'ledger_revision':receipt['revision'],'event_count':len(events),
        'thread_id':THREAD_ID,'transport':'CAPTURED_EXTENSION_SESSION','historical_checkpoint':True,'production_points_changed':False}
    try:
        result=adapter.reconcile(snapshot);report.update(success=True,status=adapter.status,result=result)
    except Exception as error:report.update(success=False,status=adapter.status,error_code=str(error))
    report['database_sha256_unchanged']=before==hashlib.sha256(database.read_bytes()).hexdigest()
    (out/'live-result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='result'},indent=2))
    if report.get('success'):
        print(json.dumps({'reconciliation_status':result['reconciliation_status'],'reported_balance':result['reported_balance'],
            'calculated_balance':result['calculated_balance'],'requires_human_review':result['requires_human_review']},indent=2))

if __name__=='__main__':main()
