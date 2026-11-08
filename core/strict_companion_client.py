"""Timer companion reconnect lifecycle with optional iOS screen share."""
JS=r'''
let binding=null,joined=false,busy=false,connected=false,manuallyDisconnected=false,displayMode='page',snapshot=null,snapshotAt=0,lastSuccess=0,nativeFullscreenUsed=false,integritySequence=Date.now();
let screenStream=null,screenTrack=null,screenCaptureTimer=null,latestScreenJpeg=null;
const BINDING_KEY='strictrecords.timer.binding.v2',INVITE_KEY='strictrecords.timer.pending.v2';
const output=document.getElementById('state');
function stored(kind,key,value){try{const store=globalThis[kind+'Storage'];if(value===null){store.removeItem(key);return true;}if(value!==undefined){store.setItem(key,JSON.stringify(value));return true;}return JSON.parse(store.getItem(key)||'null');}catch(e){return value===undefined?null:false;}}
let pending=stored('session',INVITE_KEY);if(pending?.expires_at<Date.now())pending=null;
let token=location.hash.slice(1)||pending?.token||'',nonce=pending?.token===token?pending.nonce:null;
history.replaceState(null,'',location.pathname);
function savedBinding(){const saved=stored('local',BINDING_KEY);return saved&&saved.expires_at>Date.now()&&saved.role==='IOS'&&typeof saved.preparation_id==='string'&&/^[A-Za-z0-9_-]{32,128}$/.test(saved.credential||'')?saved:null;}
function persistBinding(){return stored('local',BINDING_KEY,{preparation_id:binding.preparation_id,role:binding.role,credential:binding.credential,expires_at:Date.now()+43200000,display_requested:joined&&!manuallyDisconnected,native_fullscreen_used:nativeFullscreenUsed});}
function forgetPending(){stored('session',INVITE_KEY,null);token='';nonce=null;}
function actualDisplayMode(){if(navigator.standalone===true||window.matchMedia?.('(display-mode: standalone)').matches)return 'standalone';return document.fullscreenElement||document.webkitFullscreenElement?'fullscreen':null;}
function displayStatus(){const mode=displayMode==='fullscreen'?'Fullscreen timer':displayMode==='standalone'?'Home Screen timer':displayMode==='expanded'?'Expanded timer (browser fullscreen unavailable)':displayMode==='resume_required'?'Timer resumed — tap Resume fullscreen':'Timer page';document.getElementById('displayStatus').textContent=mode+(connected?' · connected to Windows':' · not connected');document.getElementById('fullscreen').textContent=displayMode==='resume_required'?'Resume fullscreen':'Enter fullscreen';}
function syncDisplayMode(){const actual=actualDisplayMode();if(actual){displayMode=actual;if(actual==='fullscreen')nativeFullscreenUsed=true;}else if(joined&&!manuallyDisconnected){displayMode='expanded';document.body.classList.add('expanded');}else displayMode='page';displayStatus();}
async function request(action,body){const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),6000);try{const r=await fetch('/api/strict_session/preparation/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store',signal:controller.signal});const d=await r.json();if(!r.ok||!d.success){const error=Error(d.error||'Windows host unavailable');error.status=r.status;throw error;}return d;}finally{clearTimeout(timeout);}}
function drawTimer(){const now=performance.now(),age=Math.max(0,(now-snapshotAt)/1000);let seconds=snapshot?.remaining_seconds;if(typeof seconds==='number'&&Number.isFinite(seconds)){seconds=Math.max(0,Math.ceil(seconds-(joined&&snapshot.running?Math.min(age,10):0)));document.getElementById('timer').textContent=Math.floor(seconds/60).toString().padStart(2,'0')+':'+(seconds%60).toString().padStart(2,'0');}else document.getElementById('timer').textContent='--:--';document.getElementById('phase').textContent=snapshot?.label||(connected?'Waiting for Windows to start':'Not connected to Windows');if(joined&&lastSuccess&&now-lastSuccess>12000){connected=false;displayStatus();output.textContent='Connection lost. Retrying automatically; the display stops estimating after 10 seconds.';}}

async function startScreenShare(){
  // Timer companions do not upload sampled camera/screen frames. The separate
  // trusted camera invitation uses video-only WebRTC and explicit consent.
  output.textContent='Camera monitoring uses a separate secure camera invitation from Windows. This timer does not capture your camera or screen.';
}
function stopScreenShare(){
  if(screenCaptureTimer){clearInterval(screenCaptureTimer);screenCaptureTimer=null;}
  screenStream?.getTracks().forEach(track=>track.stop());
  screenTrack=null;screenStream=null;latestScreenJpeg=null;
}

async function heartbeat(){
  if(!binding||busy)return;
  const cameraLease=stored('local','strictrecords.camera.timer-lease.v1');
  if(document.hidden&&cameraLease?.preparation_id===binding.preparation_id&&cameraLease.expires_at>Date.now())return;
  busy=true;
  const report={
    timer_ready:joined,
    fullscreen:!!actualDisplayMode(),
    display_mode:displayMode,
    visible:!document.hidden,
    online:navigator.onLine,
    screen_sharing:false,
    integrity:{sequence:(integritySequence=Math.max(Date.now(),integritySequence+1)),state:document.hidden?'BACKGROUND_REASON_UNKNOWN':'FOREGROUND',reason:document.hidden?'PAGE_HIDDEN':'RESUMED',session_id:snapshot?.session_id||''},
    client_version:'StrictRecords Timer Companion v5'
  };
  try{
    const at=performance.now();
    const result=await request('heartbeat',{...binding,report});
    snapshot=result.timer;snapshotAt=performance.now();lastSuccess=snapshotAt;connected=true;
    document.getElementById('identity').textContent=binding.role+' · Timer';
    output.textContent=manuallyDisconnected?'Disconnected':joined?'Connected to Windows · '+Math.round(snapshotAt-at)+' ms':'Connection restored. Tap Resume fullscreen timer.';
    displayStatus();drawTimer();
  }catch(e){
    connected=false;
    if([403,409].includes(e.status)){
      stored('local',BINDING_KEY,null);binding=null;joined=false;
      document.getElementById('join').hidden=false;
      const sbtn=document.getElementById('screenshare');if(sbtn)sbtn.hidden=true;stopScreenShare();
      document.getElementById('identity').textContent='Not connected';
      output.textContent='This connection expired or was replaced. Create a fresh iPhone/iPad QR code in Windows Join Devices.';
    }else output.textContent=e.name==='AbortError'?'Windows did not respond. Retrying the saved connection…':e.message;
    displayStatus();
  }finally{busy=false;}
}

async function enterDisplay(){if(actualDisplayMode()){syncDisplayMode();return;}const root=document.documentElement,fullscreen=root.requestFullscreen||root.webkitRequestFullscreen;try{if(!fullscreen)throw Error('Fullscreen unavailable');await fullscreen.call(root);displayMode='fullscreen';nativeFullscreenUsed=true;}catch(e){displayMode=nativeFullscreenUsed?'resume_required':'expanded';document.body.classList.add('expanded');}displayStatus();}
async function claimInvitation(){if(!token)throw Error('Open a fresh iPhone/iPad QR code from Windows Join Devices.');if(!nonce){const bytes=new Uint8Array(32);globalThis.crypto.getRandomValues(bytes);nonce=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');}stored('session',INVITE_KEY,{token,nonce,expires_at:Date.now()+600000});return request('join',{token,client_nonce:nonce,client_info:{platform:navigator.platform||'',max_touch_points:navigator.maxTouchPoints||0}});}
document.getElementById('join').onclick=async()=>{const button=document.getElementById('join');button.disabled=true;try{const display=enterDisplay();manuallyDisconnected=false;if(!binding){try{binding=await claimInvitation();}catch(error){const saved=savedBinding();if(!saved)throw error;binding=saved;await heartbeat();if(!binding||!connected)throw error;}}await display;joined=true;button.hidden=true;document.getElementById('fullscreen').hidden=false;const sbtn=document.getElementById('screenshare');if(sbtn)sbtn.hidden=true;document.getElementById('stop').hidden=false;const persisted=persistBinding();forgetPending();await heartbeat();if(connected&&!persisted)output.textContent+=' · Browser storage is unavailable; keep this tab open.';}catch(e){joined=false;connected=false;displayStatus();output.textContent=e.name==='AbortError'?'Connection timed out. Tap Join again to retry the same request.':e.message;}finally{button.disabled=false;}};
document.getElementById('fullscreen').onclick=async()=>{await enterDisplay();if(binding)persistBinding();await heartbeat();};
const shareBtn=document.getElementById('screenshare');if(shareBtn)shareBtn.onclick=async()=>{if(screenTrack)stopScreenShare();else await startScreenShare();};
document.getElementById('stop').onclick=async()=>{joined=false;manuallyDisconnected=true;stored('local',BINDING_KEY,null);stopScreenShare();const sbtn=document.getElementById('screenshare');if(sbtn)sbtn.hidden=true;await heartbeat();document.getElementById('join').textContent='Reconnect timer';document.getElementById('join').hidden=false;document.getElementById('fullscreen').hidden=true;document.getElementById('stop').hidden=true;};
function displayChanged(){syncDisplayMode();if(binding&&joined)persistBinding();heartbeat();}
async function resumeVisibleTimer(){if(document.hidden){await heartbeat();return;}if(!binding){await restoreConnection();return;}if(joined&&!manuallyDisconnected){syncDisplayMode();document.getElementById('join').hidden=true;document.getElementById('fullscreen').hidden=false;const sbtn=document.getElementById('screenshare');if(sbtn)sbtn.hidden=true;document.getElementById('stop').hidden=false;drawTimer();}await heartbeat();}
document.addEventListener('fullscreenchange',displayChanged);document.addEventListener('webkitfullscreenchange',displayChanged);document.addEventListener('visibilitychange',resumeVisibleTimer);window.addEventListener('pageshow',resumeVisibleTimer);window.addEventListener('online',resumeVisibleTimer);setInterval(()=>heartbeat(),5000);setInterval(drawTimer,250);
async function restoreConnection(){if(!token){binding=savedBinding();if(binding){nativeFullscreenUsed=binding.native_fullscreen_used===true;joined=binding.display_requested===true;document.getElementById('join').textContent='Resume fullscreen timer';document.getElementById('join').hidden=joined;document.getElementById('fullscreen').hidden=!joined;const sbtn=document.getElementById('screenshare');if(sbtn)sbtn.hidden=true;document.getElementById('stop').hidden=!joined;if(joined)syncDisplayMode();output.textContent='Reconnecting to Windows…';await heartbeat();}}}
const restorePromise=restoreConnection();

function reportPageExit(event){if(!binding||!joined)return;const report={timer_ready:true,fullscreen:false,display_mode:displayMode,visible:false,online:navigator.onLine,client_version:'StrictRecords Timer Companion v5',integrity:{sequence:(integritySequence=Math.max(Date.now(),integritySequence+1)),state:'BACKGROUND_REASON_UNKNOWN',reason:'PAGE_EXIT_OBSERVED',session_id:snapshot?.session_id||''}};try{const payload=JSON.stringify({...binding,report});if(navigator.sendBeacon?.('/api/strict_session/preparation/heartbeat',new Blob([payload],{type:'application/json'})))return;fetch('/api/strict_session/preparation/heartbeat',{method:'POST',headers:{'Content-Type':'application/json'},body:payload,keepalive:true}).catch(()=>{});}catch(e){}}
window.addEventListener('pagehide',reportPageExit);
'''
