"""Windows preparation input selection; no automatic capture on page entry."""
CSS=r'''
.ss-inputs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:18px;padding:18px;background:#14131c;border:1px solid var(--ss-line);border-radius:14px}
.ss-inputs label{display:grid;gap:8px;font-size:.85rem;color:var(--text-secondary)}
.ss-inputs select{width:100%;min-width:0;padding:12px;border-radius:10px;border:1px solid #625174;background:#211c2c;color:var(--text);font:inherit;text-overflow:ellipsis}
.ss-inputs select:focus-visible{outline:2px solid var(--ss-purple);outline-offset:3px}.ss-inputs .ss-input-note{grid-column:1/-1;margin:0;font-size:.8rem;color:var(--text-secondary);line-height:1.6}
.ss-inputs .ss-no-mic-badge{display:flex;align-items:center;padding:12px;border-radius:10px;border:1px solid #30363d;background:#161b22;color:#8b949e;font-size:0.85rem}
@media(max-width:650px){.ss-inputs{grid-template-columns:1fr}}
'''
HTML=r'''
<div class="ss-inputs">
 <label for="ssCameraInput">Camera<select id="ssCameraInput" onchange="strictInputChanged()"><option value="">Choose a camera…</option></select></label>
 <label>Microphone Status
  <div class="ss-no-mic-badge">🛡️ Not required &amp; not accessed</div>
 </label>
 <p class="ss-input-note" id="ssInputStatus" role="status">Show available camera inputs after agreeing below, then choose and test your camera. Virtual cameras are included when Windows exposes them. Microphone access is never requested or required.</p>
</div>
'''
JS=r'''
let strictInputDevices=[],strictInputsLoading=false,strictInputsStale=true,strictHostCamera=null;
function strictSavedInput(kind,value){try{const key='strictrecords.input.'+kind;if(value!==undefined)localStorage.setItem(key,value);return localStorage.getItem(key)||'';}catch(e){return '';}}
function strictInputSelection(){return {camera:document.getElementById('ssCameraInput')?.value||''};}
function strictInputsChosen(){const s=strictInputSelection();return !strictInputsStale&&strictInputDevices.some(d=>d.kind==='videoinput'&&d.deviceId===s.camera);}
function strictInputControls(){const cam=document.getElementById('ssCameraInput'),loadBtn=document.getElementById('ssLoadInputsButton');if(cam)cam.disabled=strictInputsLoading||strictMediaTesting;if(loadBtn)loadBtn.disabled=strictInputsLoading||strictMediaTesting;const testBtn=document.getElementById('ssTestMediaButton');if(testBtn)testBtn.disabled=strictInputsLoading||strictMediaTesting||!strictInputsChosen();}
function strictPopulateInputs(devices){strictInputDevices=devices.filter(d=>d.kind==='videoinput'&&d.deviceId);const select=document.getElementById('ssCameraInput');if(!select)return;const previous=select.value||strictSavedInput('camera');const options=[ssElement('option','Choose a camera…')];options[0].value='';let number=0;for(const device of strictInputDevices){const option=ssElement('option',device.label||'Camera '+(++number));option.value=device.deviceId;options.push(option);}select.replaceChildren(...options);select.value=options.some(o=>o.value===previous)?previous:'';strictInputsStale=false;strictInputControls();}
async function strictLoadInputs(askPermission=true){
 if(strictInputsLoading||strictMediaTesting)return;
 if(askPermission&&!document.getElementById('ssMediaConsent')?.checked){showToast('Accept the local input test explanation first.','info');return;}
 if(!navigator.mediaDevices?.enumerateDevices){ssText('ssInputStatus','Input selection requires the Windows EXE WebView.');return;}
 strictInputsLoading=true;strictInputControls();let discovery=null;const generation=askPermission?(await stopStrictPreparationMedia(),strictMediaGeneration):strictMediaGeneration;
 try{if(askPermission)discovery=await navigator.mediaDevices.getUserMedia({video:true});const devices=await navigator.mediaDevices.enumerateDevices();if(generation!==strictMediaGeneration||currentTab!=='strict-session'||(askPermission&&!document.getElementById('ssMediaConsent')?.checked))return;
  strictPopulateInputs(devices);ssText('ssInputStatus',askPermission?'Choose your camera, then test it. Changing the camera requires a fresh test.':'If names are hidden or an input is missing, use Show available inputs and approve access.');
 }catch(e){if(generation===strictMediaGeneration&&currentTab==='strict-session'){strictInputsStale=true;try{const devices=await navigator.mediaDevices.enumerateDevices();if(generation===strictMediaGeneration&&currentTab==='strict-session')strictPopulateInputs(devices);}catch(ignored){}ssText('ssInputStatus','The default camera could not be opened. Choose another available camera and test it, or approve access and refresh the list.');if(['NotAllowedError','PermissionDeniedError','SecurityError'].includes(e.name)){document.getElementById('ssMediaHelp').hidden=false;document.getElementById('ssPrivacyButtons').hidden=false;ssText('ssMediaMessage','Allow access in the permission prompt, or open Windows privacy settings below and allow camera access for desktop apps.');}}}
 finally{discovery?.getTracks().forEach(t=>t.stop());strictInputsLoading=false;strictInputControls();if(askPermission)await ssHostHeartbeat();}
}
async function strictInputChanged(){const s=strictInputSelection();strictSavedInput('camera',s.camera);await stopStrictPreparationMedia();ssText('ssInputStatus','Camera changed. Test the selected camera before continuing.');strictInputControls();}
async function testStrictPreparationMedia(){
 if(strictMediaTesting||strictInputsLoading)return;
 if(!document.getElementById('ssMediaConsent')?.checked){showToast('Read and accept the local camera test explanation first.','info');return;}
 if(!strictInputsChosen()){showToast('Show available inputs and choose a camera first.','info');return;}
 const selected=strictInputSelection(),button=document.getElementById('ssTestMediaButton');strictMediaTesting=true;strictInputControls();document.getElementById('ssMediaHelp').hidden=true;let candidate=null;let generation=strictMediaGeneration;
  try{await stopStrictPreparationMedia(false);strictSetupMonitor=false;await ssRequest('stop_hardware');generation=strictMediaGeneration;candidate=await navigator.mediaDevices.getUserMedia({video:{deviceId:{exact:selected.camera}}});
  const cancelled=()=>generation!==strictMediaGeneration||currentTab!=='strict-session'||!document.getElementById('ssMediaConsent')?.checked;
  if(cancelled())return;
  const tracks=candidate.getTracks(),camera=tracks.find(t=>t.kind==='video');
  if(!camera||camera.getSettings().deviceId!==selected.camera)throw Error('The browser did not confirm the selected camera. Refresh the input list and test again.');
  const api=window.pywebview?.api;if(!api?.resolve_strict_camera)throw Error('Test the selected camera in the Windows EXE so it can be matched to the study monitor.');
  const native=await api.resolve_strict_camera(camera.label);if(cancelled())return;if(!native.success)throw Error(native.error);
  candidate.getTracks().forEach(t=>t.stop());candidate=null;strictHostCamera=native;strictHostMedia=null;strictSetupMonitor=true;strictPreparationData=await ssRequest('hardware',{preparation_id:strictPreparationData.preparation_id,camera_id:native.native_id,consent:true});button.textContent='Run hardware checks again';ssText('ssInputStatus','Tested: '+camera.label);await ssHostHeartbeat();document.getElementById('ssSetupCameraPanel')?.scrollIntoView({block:'center'});updateStrictSetupPreview();
 }catch(e){strictSetupMonitor=false;await ssRequest('stop_hardware').catch(()=>{});if(generation!==strictMediaGeneration||currentTab!=='strict-session')return;const denied=['NotAllowedError','PermissionDeniedError','SecurityError'].includes(e.name);const messages={NotFoundError:'The selected camera is no longer available. Refresh the inputs and choose again.',OverconstrainedError:'The selected camera is unavailable. Refresh the list and choose an available camera.',NotReadableError:'The selected camera is busy. Close other apps using it, then retry.',AbortError:'The input check was interrupted. Retry when the camera is available.'};const message=denied?'Access was not allowed. Retry and choose Yes in the permission prompt. If Windows still blocks access, open privacy settings below and allow camera access for desktop apps, then retry.':(messages[e.name]||e.message||'The selected camera could not be confirmed.');document.getElementById('ssMediaHelp').hidden=false;document.getElementById('ssPrivacyButtons').hidden=!denied;ssText('ssMediaMessage',message);button.textContent='Retry selected camera';await ssRequest('host',{preparation_id:strictPreparationData?.preparation_id,report:{error:e.name,visible:!document.hidden,online:navigator.onLine,secure_context:window.isSecureContext}}).catch(()=>{});
 }finally{candidate?.getTracks().forEach(t=>t.stop());strictMediaTesting=false;strictInputControls();}
}
navigator.mediaDevices?.addEventListener?.('devicechange',()=>{strictInputsStale=true;stopStrictPreparationMedia();strictInputControls();ssText('ssInputStatus','Camera changed or disconnected. Show available inputs, choose and test again.');});
'''
