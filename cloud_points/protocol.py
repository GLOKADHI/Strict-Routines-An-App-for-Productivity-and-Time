"""No scoring authority is delegated to clients, aggregate fields or an AI response."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from core.android_ledger import _authorized_negative

THREAD_ID = '891a3c1e-eed2-460e-bc05-6b8ac94bec5b'
IST = ZoneInfo('Asia/Kolkata')
AUDIT_FIELDS = {'action','reason','approval_source','approved_by','activity_id','submission_id','evidence_id','related_task_id'}
EVENT_FIELDS = {'event_id','event_type','points','date','timestamp','eligible','rule_version','evidence_id','related_task_id','idempotency_key'}

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def slot_at(moment):
    if moment.tzinfo is None:
        raise ValueError('Scheduler requires an aware clock')
    local=moment.astimezone(IST)
    hour=local.hour if local.hour>=19 else local.hour//2*2
    return local.replace(hour=hour,minute=0,second=0,microsecond=0)

def next_slot(moment):
    slot=slot_at(moment)
    return slot+timedelta(hours=1 if slot.hour>=18 else 2)

def sanitize_envelope(envelope):
    raw=envelope.get('payload_json','')
    if not isinstance(raw,str) or len(raw.encode())>2_000_000:
        raise ValueError('Ledger snapshot exceeds its bounded size')
    if hashlib.sha256(raw.encode()).hexdigest()!=envelope.get('sha256'):
        raise ValueError('Ledger checksum mismatch')
    p=json.loads(raw)
    clean={key:p[key] for key in ['schema','account_id','device_id','date','timezone','revision','event_count','confirmed_score','raw_verified_points']}
    clean['is_dev_mode']=p.get('is_dev_mode',False)
    clean['score_events']=[]
    for event in p['score_events']:
        row={key:value for key,value in event.items() if key in EVENT_FIELDS}
        audit=event.get('audit') or {}
        if not isinstance(audit,dict):raise ValueError('Invalid event audit reference')
        row['audit']={key:str(value)[:256] for key,value in audit.items() if key in AUDIT_FIELDS and isinstance(value,(str,int))}
        author=row['audit'].get('approved_by','')
        if author and author.lower() not in {'system','auto','unknown'} and not author.startswith('adminref_'):
            row['audit']['approved_by']='adminref_'+hashlib.sha256(author.encode()).hexdigest()[:24]
        # Human names, free-form descriptions, files, tokens and credentials are omitted.
        row['audit'].pop('reason',None)
        if audit.get('action')=='INITIAL_POINTS_RESET' and audit.get('reason'):
            row['audit']['reason']='Explicit user reset'
        row['idempotency_key']=str(row.get('idempotency_key') or row['event_id'])
        clean['score_events'].append(row)
    raw=canonical(clean)
    return {'payload_json':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest()}

def validate(envelope,account,device):
    clean=sanitize_envelope(envelope)
    p=json.loads(clean['payload_json'])
    if p['schema']!=1 or p['account_id']!=account or p['device_id']!=device or p['is_dev_mode'] is not False:
        raise ValueError('Ledger account, device or production scope mismatch')
    if p['timezone']!='Asia/Kolkata' or datetime.strptime(p['date'],'%Y-%m-%d').date().isoformat()!=p['date']:
        raise ValueError('Invalid ledger reporting date/timezone')
    if type(p['revision']) is not int or p['revision']<1 or not 0<=len(p['score_events'])<=10000 or p['event_count']!=len(p['score_events']):
        raise ValueError('Invalid revision or event count')
    ids=set();keys=set();awards=set();raw=0
    for e in p['score_events']:
        if not isinstance(e.get('event_id'),str) or not 1<=len(e['event_id'])<=200 or e['event_id'] in ids or e['idempotency_key'] in keys:
            raise ValueError('Duplicate event identity/idempotency key')
        ids.add(e['event_id']);keys.add(e['idempotency_key'])
        if e.get('date')!=p['date'] or type(e.get('points')) is not int or abs(e['points'])>1_000_000 or type(e.get('eligible')) is not bool:
            raise ValueError('Invalid event amount or reporting scope')
        datetime.fromisoformat(e['timestamp'])
        if not e.get('event_type') or type(e.get('rule_version',1)) is not int:raise ValueError('Missing source policy')
        if e['eligible'] and e['points']<0 and not _authorized_negative(e):raise ValueError('Deduction lacks existing policy authorization')
        if e['eligible'] and e['points']>0 and e['event_type'] in {'PROOF_APPROVED','TASK_VERIFIED'}:
            ref=e.get('related_task_id') or e['audit'].get('activity_id') or e['audit'].get('submission_id')
            if not ref or not (e.get('evidence_id') or e['audit'].get('evidence_id')):raise ValueError('Award lacks verification/source reference')
            if ref in awards:raise ValueError('Duplicate award for source record')
            awards.add(ref)
        raw+=e['points'] if e['eligible'] else 0
    # This is the existing APK ledger cap, not a new award calculation.
    if type(p['confirmed_score']) is not int or p['confirmed_score']!=min(100,raw) or p['raw_verified_points']!=raw:
        raise ValueError('Aggregate disagrees with existing event ledger')
    return p,clean

def validate_taufa(result,request):
    if not isinstance(result,dict):raise ValueError('Free-form AI answers are not accepted')
    required={'request_id','thread_id','ledger_revision','reconciliation_status','reported_balance','calculated_balance','duplicate_event_ids','missing_event_ids','discrepancies','reasoning_summary','requires_human_review'}
    if not required<=result.keys():raise ValueError('Incomplete structured reconciliation')
    if any(result[k]!=request[k] for k in ['request_id','thread_id','ledger_revision']):raise ValueError('Unrelated or stale AI response')
    if result['thread_id']!=THREAD_ID or result['reconciliation_status'] not in {'CONSISTENT','DISCREPANCY','NEEDS_REVIEW','FAILED'}:raise ValueError('Invalid reconciliation status')
    if type(result['requires_human_review']) is not bool or not isinstance(result['reasoning_summary'],str) or len(result['reasoning_summary'])>4000:raise ValueError('Invalid AI response schema')
    if any(not isinstance(result[k],list) or len(result[k])>1000 for k in ['duplicate_event_ids','missing_event_ids','discrepancies']):raise ValueError('Invalid discrepancy schema')
    if any(type(result[k]) is not int and result[k] is not None for k in ['reported_balance','calculated_balance']):raise ValueError('Invalid AI balances')
    if result['reconciliation_status']=='CONSISTENT' and (request['current_calculated_balance'] is None or result['requires_human_review'] or any(result[k] for k in ['duplicate_event_ids','missing_event_ids','discrepancies']) or result['calculated_balance']!=request['current_calculated_balance'] or result['reported_balance']!=request['current_calculated_balance']):raise ValueError('AI consistency claim contradicts the ledger')
    return result
