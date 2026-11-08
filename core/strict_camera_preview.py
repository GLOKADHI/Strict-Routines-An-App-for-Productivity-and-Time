"""Consent-gated local setup preview; frames never enter HTTP state or storage."""
CSS=r'''
.ss-camera-preview{display:grid;grid-template-columns:minmax(180px,300px) minmax(180px,1fr);gap:20px;align-items:center;background:#14151d;border:1px solid var(--ss-line);border-radius:18px;padding:18px;margin-bottom:18px}
.ss-camera-frame{position:relative;aspect-ratio:4/3;background:#090c12;border:1px solid #514565;border-radius:12px;overflow:hidden;display:grid;place-items:center}
.ss-camera-frame img{width:100%;height:100%;object-fit:contain;position:absolute;inset:0}.ss-camera-placeholder{color:#b6adc5;text-align:center;padding:24px;font-size:.85rem;line-height:1.7}
.ss-camera-live{position:absolute;left:9px;top:9px;border-radius:6px;background:#0a291ee8;color:#70edb0;padding:3px 7px;font-size:.65rem}.ss-camera-preview h3{margin:0 0 10px;font-size:1rem}.ss-camera-feedback{display:grid;gap:10px;list-style:none;padding:0;margin:0}.ss-camera-feedback li{padding:8px 10px;border-radius:8px;background:#24202c;color:#d0bfdf;font-size:.8rem;line-height:1.6}.ss-camera-feedback li[data-ok=true]{background:#153629;color:#72eaa8}.ss-camera-preview p{font-size:.78rem;line-height:1.6;color:var(--text-secondary)}
@media(max-width:750px){.ss-camera-preview{grid-template-columns:1fr}.ss-camera-frame{width:min(100%,320px);justify-self:center}}
'''
HTML=r'''
<section class="ss-camera-preview" id="ssSetupCameraPanel" aria-label="Live camera setup preview">
 <div class="ss-camera-frame"><img id="ssSetupCameraImage" alt="Live view from your selected camera" hidden><div class="ss-camera-placeholder" id="ssSetupCameraPlaceholder">Your camera preview appears here.<br>Choose a camera, accept the local test explanation and run hardware checks.</div><span class="ss-camera-live" id="ssSetupCameraLive" hidden>● LIVE · LOCAL</span></div>
 <div><h3>See your camera setup</h3><ul class="ss-camera-feedback"><li id="ssPreviewFace">Face: start the camera test</li><li id="ssPreviewLight">Lighting: start the camera test</li><li id="ssPreviewCalibration">Calibration: start the camera test</li></ul><p id="ssCameraPreviewHint" role="status">No camera is opened by viewing this page.</p><button type="button" class="btn btn-outline" onclick="openStrictCheckSettings('WINDOWS','webcam')">⚙ Camera settings &amp; test</button><p>Memory-only preview. No recording or upload. Microphone is not accessed.</p></div>
</section>
'''
JS=r'''
let ssSetupPreviewBusy=false,ssSetupPreviewLastFrame=null,ssWinPreviewFresh=false;
function ssSetupPreviewAllowed(){return currentTab==='strict-session'&&!document.hidden&&strictSetupMonitor&&!ssActive()&&(!document.getElementById('ssPrecheckView')?.hidden||!document.getElementById('ssJoinView')?.hidden||!document.getElementById('ssReadyView')?.hidden)&&document.getElementById('ssMediaConsent')?.checked===true;}
function clearStrictSetupPreview(message='Camera preview is off. Choose a camera and run hardware checks.'){
 ssSetupPreviewLastFrame=null;ssWinPreviewFresh=false;
 const image=document.getElementById('ssSetupCameraImage');if(image){image.hidden=true;image.removeAttribute?.('src');}
 const placeholder=document.getElementById('ssSetupCameraPlaceholder');if(placeholder){placeholder.hidden=false;placeholder.textContent=message;}
 const live=document.getElementById('ssSetupCameraLive');if(live)live.hidden=true;
 const winImg=document.getElementById('ssWinCamTileImg');if(winImg){winImg.hidden=true;winImg.removeAttribute?.('src');}
 const winPl=document.getElementById('ssWinCamTilePlaceholder');if(winPl){winPl.hidden=false;winPl.textContent=strictSetupMonitor?'Waiting for a fresh frame from the selected camera…':'Local monitor is not running · Run hardware checks';}
 const winLive=document.getElementById('ssWinCamTileLive');if(winLive)winLive.hidden=true;
 if(typeof ssUpdateDualCameraDashboard==='function'&&typeof ssIOSCameraStatusData!=='undefined')ssUpdateDualCameraDashboard(ssIOSCameraStatusData);
}
function ssCameraFeedback(data,active){const checks=data?.environment?.hardware_checks||{};return [
 ['ssPreviewFace','Face',checks.face?.passed===true,'Whole face visible','Show one whole face inside the picture'],
 ['ssPreviewLight','Lighting',checks.lighting?.passed===true,'Clear','Add soft light in front of your face; clean the lens'],
 ['ssPreviewCalibration','Calibration',checks.calibration?.passed===true,'Ready','Look straight at the screen briefly']
 ].map(([id,label,passed,good,help])=>({id,ok:active&&passed,text:label+': '+(!active?'start the camera test':passed?good:help)}));}
function renderStrictCameraFeedback(data){const active=ssSetupPreviewAllowed();for(const item of ssCameraFeedback(data,active)){const element=document.getElementById(item.id);if(element){element.textContent=item.text;element.dataset.ok=String(item.ok);}}
 ssText('ssCameraPreviewHint',active?(data?.environment?.hardware_checks?.webcam?.passed?'Use this picture to adjust your position and lighting.':'Waiting for a fresh frame from the selected camera…'):'Choose a camera below, accept the test explanation and click Run hardware checks.');
 if(!active)clearStrictSetupPreview();
}
async function updateStrictSetupPreview(){
 if(!ssSetupPreviewAllowed()){clearStrictSetupPreview();return;}if(ssSetupPreviewBusy)return;
 const api=window.pywebview?.api;if(!api?.get_strict_setup_preview){clearStrictSetupPreview('Live setup preview requires the updated Windows EXE.');return;}
 ssSetupPreviewBusy=true;const generation=strictMediaGeneration;let expired=false;
 const timeout=setTimeout(()=>{expired=true;if(generation===strictMediaGeneration)clearStrictSetupPreview('Camera preview is delayed. Retry the camera test if it does not resume.');},1500);
 try{const preview=await api.get_strict_setup_preview();if(expired||generation!==strictMediaGeneration||!ssSetupPreviewAllowed()){clearStrictSetupPreview();return;}
  if(!preview?.current||typeof preview.jpeg!=='string'||!preview.jpeg||preview.jpeg.length>65536){clearStrictSetupPreview('Waiting for a current camera picture. If this continues, check camera settings or retry the test.');return;}
  ssWinPreviewFresh=true;
  const frame=preview.frame_id??preview.jpeg;if(frame===ssSetupPreviewLastFrame)return;ssSetupPreviewLastFrame=frame;
  const dataUri='data:image/jpeg;base64,'+preview.jpeg;
  const image=document.getElementById('ssSetupCameraImage');if(image){image.src=dataUri;image.hidden=false;}
  const pl=document.getElementById('ssSetupCameraPlaceholder');if(pl)pl.hidden=true;
  const lv=document.getElementById('ssSetupCameraLive');if(lv)lv.hidden=false;
  const winImg=document.getElementById('ssWinCamTileImg');if(winImg){winImg.src=dataUri;winImg.hidden=false;}
  const winPl=document.getElementById('ssWinCamTilePlaceholder');if(winPl)winPl.hidden=true;
  const winLive=document.getElementById('ssWinCamTileLive');if(winLive)winLive.hidden=false;
  if(typeof ssUpdateDualCameraDashboard==='function'&&typeof ssIOSCameraStatusData!=='undefined')ssUpdateDualCameraDashboard(ssIOSCameraStatusData);
 }catch(error){clearStrictSetupPreview('The camera picture is unavailable. Open camera settings and retry the test.');}finally{clearTimeout(timeout);ssSetupPreviewBusy=false;}
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)clearStrictSetupPreview();});
setInterval(updateStrictSetupPreview,150);
'''
