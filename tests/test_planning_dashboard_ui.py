"""Run the actual dashboard and extension planning scripts with fixture HTTP state."""
import json
from pathlib import Path
import pytest
from core.ui_dashboard import DASHBOARD_HTML
from tests.test_pairing_auto_refresh import browser

@pytest.fixture
def dashboard(browser):
    p=browser.new_page(viewport={'width':1280,'height':900})
    p.route('**/*',lambda r:r.fulfill(content_type='text/html' if r.request.url=='http://127.0.0.1:8123/' else 'application/json',body=DASHBOARD_HTML if r.request.url=='http://127.0.0.1:8123/' else '{}'))
    p.goto('http://127.0.0.1:8123/')
    p.evaluate('''() => {
      window.fixtureDecision={account_id:'user_lok',date:'2026-10-21',timezone:'Asia/Kolkata',gym_planned:null,revision:0,status:'GYM_DECISION_REQUIRED'};
      window.fixtureState={date:'2026-10-21',decision:fixtureDecision,message:'Choose a Gym decision',lives_balance:3,is_active:false,eligible_sessions:[],activation_window_open:true};
      window.posts=[];window.offlineFixture=false;
      apiGet=async path=>{if(offlineFixture)throw Error('Fixture offline');return path.startsWith('/api/gym/decision')?{success:true,decision:fixtureDecision}:{success:true,state:fixtureState};};
      apiPost=async(path,body)=>{posts.push({path,body});if(offlineFixture)throw Error('Fixture offline');fixtureDecision={...fixtureDecision,gym_planned:body.gym_planned,revision:fixtureDecision.revision+1,status:body.gym_planned?'GYM_PLANNED':'GYM_NOT_PLANNED'};fixtureState.decision=fixtureDecision;return {success:true,decision:fixtureDecision};};
      localStorage.clear();
    }''')
    yield p
    p.close()

def test_independent_college_and_gym_fields_exist_and_unanswered_cannot_activate(dashboard):
    p=dashboard
    assert p.locator('#schedCollegeDecision').count()==1 and p.locator('#schedGymDecision').count()==1
    p.evaluate("loadDailyGymChoice('sched','2026-10-21')")
    assert p.locator('#schedGymDecision').input_value()==''
    p.evaluate('refreshGymModeUI()')
    assert p.locator('#gymActivate').is_disabled()

def test_saved_no_and_offline_yes_cannot_claim_sync_success(dashboard):
    p=dashboard;p.evaluate("loadDailyGymChoice('sched','2026-10-21')")
    p.locator('#schedGymDecision').select_option('NO',force=True)
    assert p.evaluate("saveDailyGymChoice('sched','2026-10-21')")
    assert p.locator('#schedGymDecision').input_value()=='NO'
    assert 'unavailable' in p.locator('#schedGymDecisionStatus').text_content()
    p.evaluate('offlineFixture=true')
    p.locator('#schedGymDecision').select_option('YES',force=True)
    assert not p.evaluate("saveDailyGymChoice('sched','2026-10-21')")
    pending=json.loads(p.evaluate("localStorage.getItem('gymChoicePending')"))
    assert pending['gym_planned'] and pending['expected_revision']==1
    assert 'pending' in p.locator('#schedGymDecisionStatus').text_content()
    assert p.locator('#gymActivate').is_disabled()

def test_revocation_is_labelled_pending_until_effective(dashboard):
    p=dashboard
    p.evaluate("fixtureDecision={...fixtureDecision,gym_planned:false,revision:2,status:'GYM_NOT_PLANNED',sync_status:'REVOCATION_PENDING'};loadDailyGymChoice('sched','2026-10-21')")
    assert 'restoring normal restrictions' in p.locator('#schedGymDecisionStatus').text_content()

@pytest.fixture
def extension(browser):
    p=browser.new_page()
    p.route('**/*',lambda r:r.fulfill(content_type='text/html',body='<input id="gymDate"><select id="gymChoice"><option value=""></option><option>YES</option><option>NO</option></select><p id="gymStatus"></p><p id="gymChoiceStatus"></p><button id="gymSave">Save</button>'))
    p.goto('http://127.0.0.1:8123/')
    p.clock.install()
    p.evaluate('''() => {
      window.fixtureChoice={account_id:'user_lok',date:'2026-10-21',gym_planned:true,revision:1,status:'GYM_PLANNED'};
      window.fixtureStore={};window.fixtureOffline=false;window.fixturePosts=[];
      window.chrome={storage:{local:{get:async key=>({[key]:fixtureStore[key]}),set:async obj=>Object.assign(fixtureStore,obj),remove:async key=>{delete fixtureStore[key];}}}};
      window.fetch=async(path,opts)=>{if(fixtureOffline)throw Error('Disconnected');if(opts?.method==='POST'){fixturePosts.push(JSON.parse(opts.body));throw Error('Disconnected');}return {json:async()=>path.includes('/api/gym/decision')?{success:true,decision:fixtureChoice}:{success:true,state:{date:'2026-10-20',message:'Fixture authority',lives_balance:3},planning:{'2026-10-21':{gym:fixtureChoice}}}};};
    }''')
    p.add_script_tag(content=(Path(__file__).resolve().parents[1]/'browser_extension/gym_planning.js').read_text())
    p.wait_for_function("document.getElementById('gymChoice').value==='YES'")
    yield p
    p.close()

def test_extension_receives_remote_no_revision_without_reopening(extension):
    p=extension
    p.evaluate("fixtureChoice={...fixtureChoice,gym_planned:false,revision:2,status:'GYM_NOT_PLANNED'}")
    p.clock.fast_forward(15000)
    p.wait_for_function("document.getElementById('gymChoice').value==='NO'")
    assert 'unavailable' in p.locator('#gymChoiceStatus').text_content()

def test_extension_retains_original_revision_and_request_on_failure(extension):
    p=extension
    p.locator('#gymChoice').select_option('NO')
    p.locator('#gymSave').click()
    p.wait_for_function('fixtureStore.pendingGymChoice?.gym_planned===false')
    pending=p.evaluate('fixtureStore.pendingGymChoice')
    assert pending['expected_revision']==1 and pending['request_id']
    assert 'pending' in p.locator('#gymChoiceStatus').text_content()

def test_extension_dirty_edit_keeps_original_revision_after_remote_choice(extension):
    p=extension
    p.locator('#gymChoice').select_option('NO')
    p.evaluate("fixtureChoice={...fixtureChoice,gym_planned:false,revision:2,status:'GYM_NOT_PLANNED'}")
    p.clock.fast_forward(15000)
    p.wait_for_function("document.getElementById('gymChoiceStatus').textContent.includes('another device')")
    p.locator('#gymSave').click()
    p.wait_for_function('fixtureStore.pendingGymChoice?.request_id')
    assert p.evaluate('fixtureStore.pendingGymChoice.expected_revision')==1

def test_windows_old_receipt_preserves_newer_outbox_request(dashboard):
    p=dashboard
    p.evaluate('''() => {
      localStorage.setItem('gymChoicePending',JSON.stringify({request_id:'old-request',expected_revision:1}));
      apiPost=()=>new Promise(resolve=>{window.releasePlanning=resolve;});
      window.sendingPlanning=flushPlanningChoice('gymChoicePending','/api/gym/decision');
    }''')
    p.evaluate("localStorage.setItem('gymChoicePending',JSON.stringify({request_id:'new-request',expected_revision:2}));releasePlanning({success:true})")
    p.evaluate('sendingPlanning')
    assert json.loads(p.evaluate("localStorage.getItem('gymChoicePending')"))['request_id']=='new-request'


def test_dashboard_requires_final_wakeup_and_all_bath_occurrences(dashboard):
    p=dashboard
    state={'daily_record':{'wakeup_status':'PENDING','bath_status':'ACTIVE','breakdown':{'Wake-up':5,'Bath':5}},
           'wakeup_session':{'camera_verified':1,'attendance_status':'PRESENT'},
           'daily_routine_gate':{'timezone':'Asia/Kolkata'},
           'scheduled_activities':[
               {'type':'BATH','is_enabled':1,'status':'VERIFIED','window_start':'2026-10-20T07:00:00+05:30','window_end':'2026-10-20T10:00:00+05:30'},
               {'type':'BATH','is_enabled':1,'status':'OPEN','window_start':'2026-10-20T16:00:00+05:30','window_end':'2026-10-20T21:00:00+05:30'}]}
    p.evaluate('(state)=>renderCockpitState(state)',state)
    assert p.locator('#taskWakeupStatus').text_content()=='PENDING'
    assert p.locator('#taskBathStatus').text_content()=='PARTIAL'
    assert p.locator('#overviewBathReward').text_content()=='1 / 2 proofs accepted'
    assert '4:00 PM' in p.locator('#overviewBathTarget').text_content()
    state['daily_record']['wakeup_status']='COMPLETED'
    state['scheduled_activities'][1]['status']='VERIFIED'
    p.evaluate('(state)=>renderCockpitState(state)',state)
    assert p.locator('#taskWakeupStatus').text_content()=='COMPLETED'
    assert p.locator('#taskBathStatus').text_content()=='COMPLETED'
    state['scheduled_activities']=[]
    p.evaluate('(state)=>renderCockpitState(state)',state)
    assert p.locator('#taskBathStatus').text_content()=='NOT PLANNED'
