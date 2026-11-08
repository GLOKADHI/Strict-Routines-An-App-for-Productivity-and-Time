const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
new vm.Script(source);
const nodes=new Map(), categories=[];
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.style={};this.dataset={};this.hidden=false;this.value='';this.textContent='';}
 setAttribute(n,v){this[n]=v;}
 appendChild(child){child.parent=this;this.children.push(child);if(child.id)nodes.set(child.id,child);}
 replaceChildren(){this.children=[];}
 getClientRects(){return this.hidden||this.style.display==='none'||this.style.visibility==='hidden'?[]:[{width:500,height:300}];}
}
for(const id of ['hourlyEligibilityNotice','hourlySubmissionOverlay','hourlyFloatingPill','hourlySubmitBtn','hourlyDescriptionInput','hourlyDashboardBanner','hourlyEditSection','hourlyReceivedSection','hourlyModalSlotDisplay','hourlyModalBoundsDisplay','hourlyModalActIdDisplay','hourlyModalCountdownDisplay','hourlyFloatingCountdown','dashboardBannerAIStatus']){
 const e=new Element('div');e.id=id;nodes.set(id,e);
}
nodes.get('hourlySubmissionOverlay').style.display='none';
nodes.get('hourlyFloatingPill').style.display='none';
for(let i=0;i<3;i++)categories.push(new Element('button'));
const document={hidden:false,body:new Element('body'),createElement:t=>new Element(t),getElementById:id=>nodes.get(id)||null,
 addEventListener:()=>{},querySelectorAll:selector=>selector==='.hourly-act-pill'?categories:[]};
let clock=Date.parse('2026-10-17T07:49:00+05:30');
class Clock extends Date {constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}}
const sandbox={document,window:{addEventListener:()=>{}},Date:Clock,performance:{now:()=>clock},Promise,setInterval:()=>1,console,
 getComputedStyle:e=>({display:e.style.display||'block',visibility:e.style.visibility||'visible'}),
 loadState:()=>{},loadStrictSessionData:()=>{},showToast:()=>{},openWakeupModal:()=>{},navAppPage:()=>{},openActivityProof:()=>{},
 fetch:async()=>({ok:true,json:async()=>sandbox.response})};
vm.createContext(sandbox);
vm.runInContext("let currentHourlyStatusData=null;function renderHourlyDashboardCard(data){currentHourlyStatusData=data;}function removeHourlyAttachedImage(){hourlyAttachedImageFile=null;hourlyAttachedImageBase64=null;}",sandbox);
function include(from,to){const start=source.indexOf(from),end=source.indexOf(to,start);assert(start>=0&&end>start);vm.runInContext(source.slice(start,end),sandbox);}
include('const visibleRefreshJobs =','// 2. TOAST NOTIFICATIONS');
include('let currentHourlyActivity =','const hourlyProofEventsHandled =');
include('let taskReminderSnapshot =','let activityProofRequest =');
include('function updateHourlyModalDisplay(','function dismissHourlyDashboardBanner(');
include('function minimizeHourlySubmissionOverlay(','function selectHourlyActivityPill(');
include('function isWakeupActivityObj(','function handleHourlyProofSubmittedEvent(');
include('function handleAiReviewCompletedEvent(','let currentHourlyStatusData =');
include('async function pollHourlyStatus()','function updateHourlyModalDisplay(');
function hour(h,phase='OPEN',status='OPEN'){
 const at=(hh,mm)=>`2026-10-17T${String(hh).padStart(2,'0')}:${String(mm).padStart(2,'0')}:00+05:30`;
 return {activity_id:'hour-'+h,type:'HOURLY',title:`Hourly (${h-1}:00-${h}:00)`,schedule_version:1,
 window_start:at(h-1,50),window_end:at(h,10),scheduled_time:at(h,0),status,reminder_phase:phase,can_submit:phase==='OPEN'};
}
function at(time){clock=Date.parse('2026-10-17T'+time+':00+05:30');}
function send(tasks,alert=true){return sandbox.window.deliverTaskSubmissionReminders({tasks,alert,delivery_keys:alert?Object.fromEntries(tasks.filter(t=>['OPEN','UPCOMING','REJECTED','BLOCKED_WAKEUP'].includes(t.reminder_phase)).map(t=>[t.activity_id+':'+t.reminder_phase,t.activity_id])):{}});}
function read(expr){return vm.runInContext(expr,sandbox);}
let checks=0;
function equal(actual,expected){assert.equal(actual,expected);checks++;}
(async()=>{
 let row=hour(8,'UPCOMING','SCHEDULED');let result=send([row]);
 equal(result.displayed_keys.length,1);equal(nodes.get('hourlySubmissionOverlay').style.display,'none');equal(nodes.get('taskReminderAction').disabled,true);
 at('07:50');row=hour(8);result=send([row]);
 equal(result.displayed_keys.length,1);equal(nodes.get('hourlySubmissionOverlay').style.display,'flex');equal(nodes.get('hourlySubmitBtn').disabled,false);
 equal(read('currentHourlyActivity.activity_id'),'hour-8');equal(sandbox.window._hourlyRemainingSeconds,1200);
 at('08:00');read('updateRealTimeClock()');equal(sandbox.window._hourlyRemainingSeconds,600);
 read('closeHourlySubmissionOverlay()');sandbox.response={success:true,state:'OPEN',is_window_open:true,hourly_activity:row,server_time:new Clock().toISOString()};
 await read('pollHourlyStatus()');equal(nodes.get('hourlySubmissionOverlay').style.display,'none');
 equal(nodes.get('taskSubmissionReminders').hidden,false);
 read('showHourlySubmissionOverlay()');equal(sandbox.window._hourlyRemainingSeconds,600);
 clock=Date.parse('2026-10-17T08:09:59.500+05:30');read('updateRealTimeClock()');equal(sandbox.window._hourlyRemainingSeconds,0);equal(nodes.get('hourlySubmissionOverlay').style.display,'flex');
 at('08:10');read('updateRealTimeClock()');equal(nodes.get('hourlySubmissionOverlay').style.display,'none');equal(nodes.get('hourlySubmitBtn').disabled,true);
 equal(read('showHourlySubmissionOverlay()'),false);equal(send([row]).displayed_keys.length,0);
 read("currentHourlyActivity.status='VERIFIED';hourlySelectedActivity='CODING';hourlyPendingSubmissionId='old-id';hourlyAttachedImageFile={name:'old'};hourlyAttachedImageBase64='old';");
 nodes.get('hourlyDescriptionInput').value='old description';
 const pending={...hour(8),status:'VALIDATING',reminder_phase:'REVIEW_PENDING',can_submit:false};
 at('08:49');send([pending,hour(9,'UPCOMING','SCHEDULED')]);equal(nodes.get('hourlySubmissionOverlay').style.display,'none');
 at('08:50');const next=hour(9);sandbox.response={success:true,state:'OPEN',is_window_open:true,hourly_activity:next,server_time:new Clock().toISOString(),review_info:null};
 await read('pollHourlyStatus()');equal(nodes.get('hourlySubmissionOverlay').style.display,'flex');equal(read('currentHourlyActivity.activity_id'),'hour-9');
 equal(read('hourlySelectedActivity'),'');equal(read('hourlyPendingSubmissionId'),null);equal(read('hourlyAttachedImageFile'),null);equal(nodes.get('hourlyDescriptionInput').value,'');
 const gym={...next,activity_id:'gym',type:'GYM',title:'Gym'},travel={...next,activity_id:'trip',type:'TRAVEL',title:'Travel'};
 result=send([gym,travel,pending,next]);equal(read('taskReminderSelected'),'hour-9');equal(result.displayed_keys.length,3);equal(read('taskReminderSnapshot.length'),4);
 nodes.get('hourlyDescriptionInput').value='new description';read("hourlySelectedActivity='READING'");send([gym,travel,pending,next],false);
 equal(nodes.get('hourlyDescriptionInput').value,'new description');equal(read('hourlySelectedActivity'),'READING');
 nodes.get('dashboardBannerAIStatus').textContent='current';read("handleAiReviewCompletedEvent({activity_id:'hour-8',decision:'APPROVED'})");equal(nodes.get('dashboardBannerAIStatus').textContent,'current');
 sandbox.response={success:true,state:'VALIDATING',is_window_open:false,hourly_activity:{...next,status:'VALIDATING'},server_time:new Clock().toISOString()};
 await read('pollHourlyStatus()');equal(nodes.get('hourlySubmissionOverlay').style.display,'none');equal(read('showHourlySubmissionOverlay()'),false);
 at('09:11');send([{...next,status:'VALIDATING',reminder_phase:'REVIEW_PENDING',can_submit:false}],false);equal(nodes.get('taskReminderAction').disabled,true);
 read('showHourlySubmissionOverlay(true)');equal(nodes.get('hourlySubmitBtn').disabled,true);
 at('19:50');result=send([hour(20)]);equal(nodes.get('hourlySubmissionOverlay').style.display,'flex');equal(read('currentHourlyActivity.activity_id'),'hour-20');equal(result.displayed_keys.length,1);
 for(let i=0;i<1000;i++)send([hour(20)],false);equal(document.body.children.length,1);
 at('20:50');const blocked={...hour(21),submission_eligible:false,eligibility_status:'BLOCKED_WAKEUP',reminder_phase:'BLOCKED_WAKEUP',can_submit:false};
 result=send([blocked]);equal(result.displayed_keys.length,1);equal(nodes.get('hourlySubmissionOverlay').style.display,'flex');
 equal(nodes.get('hourlyEligibilityNotice').style.display,'block');equal(nodes.get('hourlyEditSection').style.display,'none');equal(nodes.get('hourlySubmitBtn').disabled,true);
 equal(nodes.get('taskReminderAction').disabled,false);
 sandbox.response={success:true,state:'BLOCKED_WAKEUP',is_window_open:false,hourly_activity:blocked};
 await read('pollHourlyStatus()');equal(nodes.get('hourlySubmissionOverlay').style.display,'flex');
 result=send([{...blocked,submission_eligible:true,eligibility_status:'ELIGIBLE',reminder_phase:'OPEN',can_submit:true}]);
 equal(result.displayed_keys.length,1);equal(nodes.get('hourlyEditSection').style.display,'block');equal(nodes.get('hourlySubmitBtn').disabled,false);
 equal(source.includes('z-index:999998;box-shadow:0 16px 48px'),true);
 // Native Windows handshake: no visible form before the topmost robot yields.
 read('closeHourlySubmissionOverlay()');
 let grant=false, requests=0, reported=[];
 sandbox.window.pywebview={api:{request_hourly_overlay:async()=>{requests++;return {granted:grant};},
   hourly_overlay_visibility:async(...args)=>{reported.push(args);}}};
 equal(read('showHourlySubmissionOverlay()'),false);
 await new Promise(resolve=>setImmediate(resolve));
 equal(nodes.get('hourlySubmissionOverlay').style.display,'none');
 grant=true;
 equal(read('showHourlySubmissionOverlay()'),false);
 await new Promise(resolve=>setImmediate(resolve));
 equal(nodes.get('hourlySubmissionOverlay').style.display,'flex');
 read('reportDesktopHourlyVisibility()');
 equal(reported.at(-1)[2],true);
 read('minimizeHourlySubmissionOverlay(); reportDesktopHourlyVisibility();');
 equal(reported.at(-1)[2],false);
 equal(read('desktopHourlyGrant'),null);
 equal(requests,2);
 console.log(`Hourly popup UI: ${checks} checks passed, including repeated morning/evening windows, dismissal recovery, exact deadlines, overlap and previous-proof isolation.`);
})().catch(e=>{console.error(e);process.exitCode=1;});
