"""Captured-session contracts; all provider responses in these tests are fixtures."""
import hashlib,json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from cloud_points.protocol import THREAD_ID,validate_taufa
from cloud_points.taufa_cached_session import CapturedSessionTaufa,CachedSessionError,build_prompt,compact_snapshot,response_json

def snapshot():
    return {'request_id':'fixture-request','thread_id':THREAD_ID,'ledger_revision':3,
        'authenticated_user_reference':'fixture-account','current_calculated_balance':4,
        'last_successfully_synchronized_revision':2,'reporting_date':'2026-10-30',
        'events':[{'event_id':'stable-event','idempotency_key':'stable-event','event_type':'MI_FITNESS_SLEEP_BONUS','points':4,'eligible':True,'date':'2026-10-30','timestamp':'2026-10-30T08:00:00','evidence_id':'proof-ref','rule_version':1}]}

def result(**changes):
    return dict({'request_id':'fixture-request','thread_id':THREAD_ID,'ledger_revision':3,'reconciliation_status':'CONSISTENT',
        'reported_balance':4,'calculated_balance':4,'duplicate_event_ids':[],'missing_event_ids':[],'discrepancies':[],
        'reasoning_summary':'Fixture arithmetic only','requires_human_review':False},**changes)

class Reply:
    def __init__(self,value,status=200):self.value=value;self.status_code=status;self.closed=False
    def iter_content(self,_):yield (json.dumps(self.value) if not isinstance(self.value,str) else self.value).encode()
    def close(self):self.closed=True

class Transport:
    def __init__(self):self.messages=[];self.calls=[];self.result=result();self.thread=THREAD_ID;self.auth_status=200;self.user='fixture-user';self.generating=False
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        assert url.startswith('https://taufa.ai/') and kwargs['allow_redirects'] is False
        if url.endswith('/api/auth/session'):return Reply({'user':{'id':self.user}},self.auth_status)
        if method=='GET':return Reply({'conversation':{'id':self.thread},'messages':self.messages,'generating':self.generating})
        message=kwargs['json']['messages'][0]
        # Real Taufa assigns its own message ID. The prompt and parent link bind it.
        self.messages=[dict(message,id='provider-assigned-user',conversationId=THREAD_ID),
            {'id':'provider-assistant','role':'assistant','conversationId':THREAD_ID,'parentMessageId':'provider-assigned-user',
             'parts':[{'type':'text','text':json.dumps(self.result)}]}]
        return Reply('data: [DONE]\n')

@pytest.fixture
def setup(tmp_path,monkeypatch):
    vault={'cookie_header':'fixture-cookie-not-real','conversation_id':THREAD_ID,'strictrecords_account_id':'fixture-account',
        'account_binding_sha256':hashlib.sha256(b'fixture-user').hexdigest()}
    monkeypatch.setattr('cloud_points.taufa_cached_session.load_vault',lambda *a,**k:vault)
    transport=Transport();adapter=CapturedSessionTaufa(tmp_path/'private.enc',transport=transport,sleep=lambda _:None)
    return adapter,transport,vault

def test_real_result_requires_persisted_thread_parent_prompt_and_request_identity(setup):
    adapter,transport,_=setup
    answer=adapter.reconcile(snapshot())
    assert answer['_provider_user_message_id']=='provider-assigned-user'
    assert answer['_transport']=='CAPTURED_EXTENSION_SESSION' and answer['calculated_balance']==4
    assert adapter.status=='VERIFIED_CAPTURED_EXTENSION_SESSION'
    assert sum(method=='POST' for method,_,_ in transport.calls)==1
    adapter.reconcile(snapshot())
    assert sum(method=='POST' for method,_,_ in transport.calls)==1

@pytest.mark.parametrize('mutation',['thread','account','session','scope','image','redirect'])
def test_wrong_session_thread_or_private_payload_stops_before_post(setup,mutation):
    adapter,transport,vault=setup;data=snapshot()
    if mutation=='thread':transport.thread='another-thread'
    if mutation=='account':transport.user='different-account'
    if mutation=='session':transport.auth_status=401
    if mutation=='scope':data['authenticated_user_reference']='another-account'
    if mutation=='image':data['events'][0]['image_base64']='private-image'
    if mutation=='redirect':transport.auth_status=302
    with pytest.raises(CachedSessionError):adapter.reconcile(data)
    assert not any(method=='POST' for method,_,_ in transport.calls)

@pytest.mark.parametrize('response',[result(request_id='other'),result(thread_id='other'),result(ledger_revision=1),result(calculated_balance=99)])
def test_unrelated_ai_answer_cannot_be_accepted(setup,response):
    adapter,transport,_=setup;transport.result=response
    with pytest.raises(CachedSessionError):adapter.reconcile(snapshot())

def test_a_matching_reply_on_an_unrelated_user_prompt_is_not_reused(setup):
    adapter,transport,_=setup
    transport.messages=[{'id':'other-user','role':'user','conversationId':THREAD_ID,'content':'unrelated'},
        {'id':'other-assistant','role':'assistant','conversationId':THREAD_ID,'parentMessageId':'other-user','content':json.dumps(result())}]
    answer=adapter.reconcile(snapshot());assert answer['_provider_user_message_id']=='provider-assigned-user'
    assert any(method=='POST' for method,_,_ in transport.calls)

def test_freeform_or_incomplete_generation_is_not_a_result(setup):
    for text in ['Balance looks good','{"calculated_balance":4}']:
        if text.startswith('{'):continue
        with pytest.raises(CachedSessionError):response_json(text)
    adapter,transport,_=setup;transport.generating=True
    with pytest.raises(CachedSessionError,match='NOT_YET_PERSISTED'):adapter.reconcile(snapshot())

def test_compact_table_preserves_each_event_and_references():
    source=snapshot();table=compact_snapshot(source)
    reconstructed=[]
    for row in table['event_rows']:
        item=dict(table['event_defaults']);item.update(zip(table['event_columns'],row));item.setdefault('idempotency_key',item['event_id']);reconstructed.append(item)
    assert reconstructed==source['events']
    assert 'fixture-account' not in build_prompt(source,'fixture-account')

def test_no_authority_balance_cannot_be_called_consistent():
    request=snapshot();request['current_calculated_balance']=None
    with pytest.raises(ValueError):validate_taufa(result(reported_balance=None,calculated_balance=None),request)

def test_proof_client_can_reuse_default_private_cache_without_creating_plaintext(tmp_path,monkeypatch):
    from core.taufa_client import TaufaClient
    monkeypatch.delenv('TAUFA_SESSION_FILE',raising=False)
    monkeypatch.setattr('core.database._default_database_path',lambda:str(tmp_path/'app.db'))
    vault=tmp_path/'protected.enc';vault.write_bytes(b'fixture-encrypted-by-provider')
    monkeypatch.setattr('cloud_points.taufa_cached_session.default_vault_path',lambda:vault)
    monkeypatch.setattr('cloud_points.taufa_cached_session.load_vault',lambda *a,**k:{'cookie_header':'fixture-only','conversation_id':THREAD_ID,'account_binding_sha256':'fixture'})
    client=TaufaClient();assert client.get_cookie_header()=='fixture-only'
    assert not (tmp_path/'taufa_session.json').exists()
    assert TaufaClient(str(tmp_path/'isolated.json')).get_cookie_header()==''
