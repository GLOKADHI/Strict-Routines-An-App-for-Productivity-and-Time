const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');new vm.Script(source);
const elements=new Map();
for(const id of ['strictStudyPhase','strictStudyTimer','strictStudyWindow','strictStudyWait','strictStudyHardware',
 'hourlyStatusBadge','hourlyCountdown','strictStudyMessage','strictStudyComplete','strictStudyResume','strictStudyConsent','strictStudyDevice'])
 elements.set(id,{textContent:'',value:'',style:{},checked:false,disabled:false,replaceChildren(){}});
let now=1000,calls=[];
const sandbox={document:{hidden:false,getElementById:id=>elements.get(id)},performance:{now:()=>now},
 setInterval(){},Date,console,Option:function(label,value){this.text=label;this.value=value;},
 showToast(){},loadStrictSessionData:async()=>{},fetch:async(url,options)=>{calls.push({url,options});return{json:async()=>({success:true})}}};
vm.createContext(sandbox);
const from=source.indexOf("let strictSessionState = 'NORMAL';"),to=source.indexOf('async function loadStrictSessionData()',from);
assert(from>0&&to>from);vm.runInContext(source.slice(from,to),sandbox);
const run=script=>vm.runInContext(script,sandbox);
run(`strictSessionData={workflow:{state:'ACTIVE_FOCUS',focus_remaining_seconds:900,window_remaining_seconds:1200,monitoring_ok:true},study_hardware:{camera:'ACTIVE',monitoring_ok:true,bluetooth_connected:true}};strictStudySnapshotAt=1000;renderStrictStudy();`);
assert.equal(elements.get('strictStudyTimer').textContent,'15:00');
assert(elements.get('strictStudyComplete').disabled);
assert.equal(elements.get('strictStudyComplete').style.opacity,'.45');
now+=1000;run('renderStrictStudy()');assert.equal(elements.get('strictStudyTimer').textContent,'14:59');
run(`strictSessionData.workflow={state:'AWAITING_ANDROID_SUBMISSION',focus_completed_at:'now',end_enabled:false};renderStrictStudy();`);
assert.equal(elements.get('strictStudyTimer').textContent,'Completed');assert(elements.get('strictStudyComplete').disabled);
run(`strictSessionData.workflow={state:'END_SESSION_ENABLED',session_id:'strict-current',sync_verified:true,end_enabled:true};renderStrictStudy();`);
assert(!elements.get('strictStudyComplete').disabled);
assert.equal(elements.get('strictStudyComplete').style.opacity,'1');
run(`strictSessionData.study_hardware.monitoring_ok=false;renderStrictStudy();`);assert(elements.get('strictStudyComplete').disabled);
run(`strictSessionData.study_hardware.monitoring_ok=true;renderStrictStudy();`);now+=13000;run('renderStrictStudy()');assert(elements.get('strictStudyComplete').disabled);
now=2000;run('renderStrictStudy()');
(async()=>{await run('completeStrictStudyUI()');assert.equal(calls.length,1);const {url,options}=calls[0];
 assert.equal(url,'/api/strict_session/complete');assert.equal(options.headers['X-StrictRoutines-Study'],'1');
 assert.equal(JSON.parse(options.body).session_id,'strict-current');
 assert.equal(source.includes('let selectedFocusMinutes = 25'),true);console.log('Strict study UI: 14 checks passed');})();
