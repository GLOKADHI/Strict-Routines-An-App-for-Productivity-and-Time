"""Per-prerequisite setup, retesting and clearly separated manual reviews.

Reviews live only in this window's memory, expire in ten minutes, and never enter
the server's readiness decision, points ledger, history or cloud synchronization.
"""
CSS=r'''
.ss-checks li{grid-template-columns:20px minmax(0,1fr) 36px;align-items:start}
.ss-check-state{display:inline-block;font-size:.66rem;line-height:1.4;padding:3px 7px;margin-top:5px;border-radius:7px;background:#2e2938;color:#d7c8e8}
.ss-check-state[data-state=verified]{background:#153629;color:#72eaa8}.ss-check-state[data-state=manual]{background:#3b301c;color:#f5d38e}.ss-check-state[data-state=not_required]{background:#252a31;color:#b4bfca}
.ss-check-gear{display:grid;place-items:center;width:36px;min-height:36px;border:1px solid #4b3c60;background:#25202f;border-radius:10px;color:#d4b1ff;cursor:pointer}
.ss-check-gear:hover{background:#352844;border-color:#ab79ea}.ss-check-gear:focus-visible{outline:2px solid #c995ff;outline-offset:3px}.ss-check-gear svg{width:18px;height:18px;pointer-events:none}
#ssCheckDialog{color:var(--text,#e9e7ef);background:#15141e;border:1px solid #80619e;border-radius:20px;padding:24px;width:min(560px,calc(100vw - 32px));max-height:85vh;inset:0;margin:auto;overflow:auto;box-sizing:border-box;box-shadow:0 24px 70px #0009}
#ssCheckDialog::backdrop{background:#05030cc9}.ss-check-dialog-head{display:flex;align-items:start;justify-content:space-between;gap:20px}#ssCheckDialog h2{font-size:1.2rem;line-height:1.4;margin:0}
#ssCheckDialog p{font-size:.88rem;line-height:1.75;color:var(--text-secondary,#b6adc5)}#ssCheckDialog .ss-check-live{padding:14px;border:1px solid #3c3549;background:#1e1a28;border-radius:12px}
.ss-check-dialog-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px}.ss-check-dialog-actions button{white-space:normal;text-align:left;min-height:40px}.ss-check-manual-note{border-left:3px solid #d3a857;padding-left:12px}
'''

HTML=r'''
<dialog id="ssCheckDialog" aria-labelledby="ssCheckDialogTitle" aria-describedby="ssCheckDialogHelp">
 <div class="ss-check-dialog-head"><h2 id="ssCheckDialogTitle">Configure &amp; test</h2><button type="button" class="btn btn-outline" id="ssCheckDialogClose" autofocus>Close</button></div>
 <p class="ss-check-live" id="ssCheckDialogStatus" role="status" aria-live="polite"></p>
 <p id="ssCheckDialogHelp"></p><p class="ss-check-manual-note" id="ssCheckDialogManual" hidden></p>
 <div class="ss-check-dialog-actions" id="ssCheckDialogActions"></div>
 <p id="ssCheckDialogResult" role="status" aria-live="polite"></p>
</dialog>
'''

JS=r'''
const ssCheckManualReviews=new Map();
let ssOpenCheck=null,ssCheckBusy=false;
const ssCheckInfo={
 webcam:{title:'Selected camera',help:'Choose the built-in or virtual camera you intend to use. Accept the setup explanation, then run the real hardware checks. Permission dialogs require your decision.',actions:['camera','hardware','refresh']},
 strictguard:{title:'Local face/head monitor',help:'This local monitor needs a live camera frame. It estimates head direction; it cannot prove eye-only gaze. Run hardware checks and keep one face visible.',actions:['hardware','refresh']},
 face:{title:'Face visible',help:'Move far enough from the camera for your whole face to fit inside the frame. Keep one face visible and avoid covering it. The check updates while setup monitoring runs.',actions:['camera','hardware','refresh']},
 lighting:{title:'Lighting and sharpness',help:'Face a soft light source, clean the camera lens and avoid a bright window behind you. Adjust lighting physically; then run the hardware check again.',actions:['hardware','refresh']},
 calibration:{title:'Neutral calibration',help:'Look straight at the screen briefly while the selected camera runs. Restart the hardware checks to recalibrate if you moved the camera or changed your normal position.',actions:['hardware','refresh']},
 bluetooth_monitoring:{title:'Audio-device scan',help:'Keep Bluetooth enabled. Windows checks connected audio devices, rather than merely remembered pairings. Refresh the current scan; if it is unverified, rerun the hardware checks.',actions:['refresh','hardware']},
 prohibited_bluetooth:{title:'Bluetooth headphones / earbuds',help:'Disconnect headphones or earbuds such as AirPods, Sony WF-1000XM5 or CMF Buds from this PC. Bluetooth itself and non-audio Bluetooth devices may remain enabled. Refresh to see the real connection state.',actions:['refresh','hardware']},
 wired_headphones:{title:'Wired headset',help:'Unplug wired or USB headphones and remove adapters from the 3.5 mm socket. Refresh to check detection. A driver that cannot reliably report the jack is shown separately as a manual check.',actions:['refresh','hardware']},
 speaker_output:{title:'Verified speaker mute',help:'With your setup consent, the hardware test mutes active outputs and reads the mute state back. It restores each output’s previous state when you stop checks. A manual review cannot substitute for verified mute.',actions:['hardware','stop','refresh']},
 notifications:{title:'Notification filtering',help:'With setup consent, the check verifies Windows app-toast suppression while StrictRecords native alerts remain available. Stop checks to restore the previous setting. System security prompts and arbitrary custom windows cannot be guaranteed silent.',actions:['hardware','stop','refresh']},
 physical_jack:{title:'Physical audio jack',help:'Compare a plugged and empty socket using Refresh results. The Realtek driver on this PC previously reported a connection even with the socket empty. A manual inspection can be recorded for this preparation; it does not certify the driver or bypass a detected headset.',actions:['refresh','manual'],manual:'I inspected the socket: no wired earphones or adapter are plugged in.'},
 earbud_wearing:{title:'Earbud-wearing check',help:'The installed model checks face and head position, not objects worn in your ears. Visually inspect both ears yourself, remove all earbuds/headphones, then record your manual review. Connected-device checks still apply independently.',actions:['refresh','manual'],manual:'I checked both ears: I am not wearing earbuds or headphones.'},
 microphone:{title:'Microphone',help:'Microphone capture is not part of this session. This row is Not required, rather than an unfinished permission check. No microphone permission or recording will be requested.',actions:['refresh']},
 connectivity:{title:'Connectivity',help:'Keep this PC and the iPad on a network where they can reach each other. Refresh the current host/iPad reports. If the iPad is disconnected, reopen its timer or use the joining controls.',actions:['refresh','join']},
 visibility:{title:'Window visibility',help:'Keep StrictRecords visible on Windows and keep the iPad timer page visible during setup. Refresh after returning to the app. No manual confirmation can replace a current device heartbeat.',actions:['refresh','join']},
 timer:{title:'iPad timer',help:'Open the current iPad invitation and tap Join & open timer. Keep the timer visible. Joining never starts the study countdown; only Start on Windows does.',actions:['join','refresh']},
 display:{title:'iPad display mode',help:'Use Resume fullscreen timer on the iPad. Some Safari contexts allow only an expanded timer. The actual reported display mode is shown; Windows cannot force iOS kiosk mode.',actions:['join','refresh']},
 screenshare:{title:'iOS Full-Screen Sharing (Mandatory)',help:'Tap Share iPad Screen on your iPhone/iPad companion and approve the broadcast prompt. Windows allows up to 60 seconds for initial negotiation and requires 3 seconds of stable decoded screen frames before enabling Start Strict Session.',actions:['join','refresh']},front_camera:{title:'iOS Front Camera (Mandatory)',help:'Tap Start Camera on your iPhone/iPad companion to stream the front camera for face and head-pose monitoring.',actions:['join','refresh']}
};
function ssCheckReviewSignature(data,key){const hc=data?.environment?.hardware_checks||{};return JSON.stringify([data?.preparation_id,data?.environment?.hardware_camera_id,hc[key]?.status,hc.prohibited_bluetooth?.status,hc.wired_headphones?.status]);}
function ssCheckPresentation(role,key,passed,data,now=Date.now()){
 const check=role==='WINDOWS'?data?.environment?.hardware_checks?.[key]:null;
 if(key==='microphone')return {state:'not_required',label:'Not required',symbol:'—'};
 if(key==='screenshare'){if(passed===true)return {state:'verified',label:'IOS_SCREEN_SHARE: VERIFIED',symbol:'✓'};const iosDev=data?.devices?.IOS||{};if(iosDev.screen_share_negotiating)return {state:'pending',label:iosDev.screen_share_countdown_label||'SCREEN SHARE NEGOTIATING',symbol:'◔'};if(iosDev.screen_share_failed)return {state:'pending',label:'IOS_SCREEN_SHARE_FAILED · Retry',symbol:'✕'};return {state:'pending',label:'Mandatory · Share iPad Screen',symbol:'○'};}
 if(passed===true)return {state:'verified',label:'Verified',symbol:'✓'};
 if(check?.passed===false)return {state:'pending',label:'Action required',symbol:'○'};
 if(role==='WINDOWS'&&ssCheckInfo[key]?.manual){
  const review=ssCheckManualReviews.get(role+'.'+key);
  const valid=review&&now>=review.at&&now-review.at<600000&&review.signature===ssCheckReviewSignature(data,key);
  if(review&&!valid)ssCheckManualReviews.delete(role+'.'+key);
  return valid?{state:'manual',label:'Manually checked · this setup',symbol:'✓'}:{state:'pending',label:'Manual check available',symbol:'○'};
 }
 return {state:'pending',label:'Check pending',symbol:'○'};
}
function ssRememberCheckReview(role,key,data,now=Date.now()){
 if(role!=='WINDOWS'||!ssCheckInfo[key]?.manual)throw Error('This prerequisite requires an actual device check.');
 const hc=data?.environment?.hardware_checks||{};
 if(hc.prohibited_bluetooth?.detected_device||hc.wired_headphones?.detected_device)throw Error('Disconnect the detected headset before recording this review.');
 ssCheckManualReviews.set(role+'.'+key,{at:now,signature:ssCheckReviewSignature(data,key)});
}
function ssCheckGear(role,key,title){
 const button=ssElement('button');button.type='button';button.className='ss-check-gear';button.dataset.ssSetting=role+'.'+key;
 button.setAttribute('aria-label','Configure and test '+title+' ('+(role==='IOS'?'iPad':'Windows')+')');button.title='Configure & test '+title;
 button.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m9.5 3-.7 2.2-2 .9-2.1-.7-2 3.4 1.6 1.7v2.3l-1.6 1.7 2 3.4 2.1-.7 2 .9.7 2.2h4l.7-2.2 2-.9 2.1.7 2-3.4-1.6-1.7v-2.3l1.6-1.7-2-3.4-2.1.7-2-.9L13.5 3z"/><circle cx="11.5" cy="11.7" r="3"/></svg>';
 button.addEventListener('click',()=>openStrictCheckSettings(role,key));return button;
}
function ssCheckTrigger(role,key){return [...document.querySelectorAll('[data-ss-setting]')].find(e=>e.dataset.ssSetting===role+'.'+key);}
function ssCheckLiveStatus(role,key){
 const d=strictPreparationData||{},hc=d.environment?.hardware_checks||{};
 if(role==='WINDOWS'&&hc[key])return hc[key].message;
 const device=d.devices?.[role]||{},report=device.report||{};
 if(key==='microphone')return 'Not required. No microphone is accessed.';
 if(key==='screenshare')return device.fresh&&report.screen_sharing?'Live iOS screen stream active.':'Tap Share iOS screen on the companion to stream.';
 if(key==='display')return device.fresh&&report.timer_ready?(report.fullscreen?'Fullscreen reported by iPad.':report.display_mode==='expanded'?'Expanded timer: browser fullscreen unavailable.':'Timer is displayed in the page.'):'No current iPad display report.';
 if(key==='timer')return device.fresh&&report.timer_ready?'Current iPad timer report received.':'Join the current iPad timer invitation.';
 if(key==='visibility')return device.fresh&&report.visible?'Window is visible in the current device report.':'Return the device to its timer/application and refresh.';
 return device.fresh&&report.online?'Current online device report received.':'A current online device report is required.';
}
function openStrictCheckSettings(role,key){
 if(!ssCheckInfo[key]||!['WINDOWS','IOS'].includes(role))return;
 ssOpenCheck={role,key};const dialog=document.getElementById('ssCheckDialog');
 ssText('ssCheckDialogTitle',ssCheckInfo[key].title+' · '+(role==='IOS'?'iPad':'Windows'));
 ssText('ssCheckDialogHelp',ssCheckInfo[key].help);ssText('ssCheckDialogResult','');
 renderStrictCheckDialog();if(!dialog.open)dialog.showModal();
}
function renderStrictCheckDialog(){
 if(!ssOpenCheck)return;const {role,key}=ssOpenCheck,info=ssCheckInfo[key];
 ssText('ssCheckDialogStatus',ssCheckLiveStatus(role,key));
 const note=document.getElementById('ssCheckDialogManual');note.hidden=!info.manual;
 note.textContent=info.manual?'Manual reviews are labelled separately, apply only to this preparation for up to 10 minutes, and reset when relevant device status changes. They never replace required camera, mute, audio-connection or iPad checks.':'';
 const actions=document.getElementById('ssCheckDialogActions');
 if(actions.dataset.identity===role+'.'+key)return;
 actions.replaceChildren();actions.dataset.identity=role+'.'+key;
 const labels={camera:'Choose / configure camera',hardware:'Run real hardware checks',refresh:'Refresh current results',stop:'Stop checks / restore audio',join:'Open iPad joining controls',manual:info.manual};
 for(const action of info.actions){const button=ssElement('button',labels[action]);button.type='button';button.className=action==='manual'?'ss-primary':'btn btn-outline';button.dataset.checkAction=action;
  button.addEventListener('click',()=>strictCheckAction(action));actions.append(button);}
}
async function strictCheckAction(action){
 if(ssCheckBusy||!ssOpenCheck)return;const {role,key}=ssOpenCheck,dialog=document.getElementById('ssCheckDialog');
 try{
  if(action==='manual'){
   ssRememberCheckReview(role,key,strictPreparationData);renderStrictPreparation();
   ssText('ssCheckDialogResult',ssCheckInfo[key].manual+' Saved as a manual review for this setup.');return;
  }
  if(action==='camera'||action==='hardware'){
   dialog.close();ssApplyView('PRECHECK');
   const target=document.getElementById('ssCameraInput');target?.scrollIntoView({block:'center'});target?.focus({preventScroll:true});
   if(action==='camera')return;
   if(!document.getElementById('ssMediaConsent')?.checked){showToast('Read and accept the setup test explanation, then use Run hardware checks.','info');return;}
   await testStrictPreparationMedia();return;
  }
  if(action==='join'){dialog.close();await strictPreparationStep('JOIN');return;}
  ssCheckBusy=true;
  if(action==='stop')await stopStrictPreparationMedia();
  strictPreparationData=await ssRequest('check');renderStrictPreparation();renderStrictCheckDialog();
  ssText('ssCheckDialogResult',action==='stop'?'Checks stopped. Previous audio and notification settings restored.':'Results refreshed from the current device reports.');
 }catch(error){ssText('ssCheckDialogResult',error.message);}finally{ssCheckBusy=false;}
}
document.getElementById('ssCheckDialogClose').addEventListener('click',()=>document.getElementById('ssCheckDialog').close());
document.getElementById('ssCheckDialog').addEventListener('close',()=>{const previous=ssOpenCheck;ssOpenCheck=null;document.getElementById('ssCheckDialogActions').dataset.identity='';if(previous)ssCheckTrigger(previous.role,previous.key)?.focus({preventScroll:true});});
'''
