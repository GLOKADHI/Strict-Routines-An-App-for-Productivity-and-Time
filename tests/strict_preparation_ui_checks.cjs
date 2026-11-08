const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
class Element{constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={};this.hidden=false;this.disabled=false;this.value='';}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(k,v){this[k]=v;}addEventListener(){}}
const ids=['ssDevicePanels','ssDuration','ssEnvironment','ssNextWindow','ssUntilWindow','ssReadiness','ssJoinStatus','ssReadySummary','ssContinueReady','btnStartStrictSession','ssInvitations','ssPrecheckView','ssJoinView','ssReadyView','ssFocusView','ssMediaConsent','ssTestMediaButton','ssMediaHelp','ssMediaMessage','ssPrivacyButtons','ssCameraInput','ssMicrophoneInput','ssLoadInputsButton','ssInputStatus'];
const nodes=new Map(ids.map(id=>[id,new Element('div')]));const steps=['PRECHECK','JOIN','READY','FOCUS'].map(view=>{const e=new Element('button');e.dataset.ssStep=view;return e;});
const calls=[],toasts=[],constraints=[],nativeLabels=[],stopped=[],mediaEvents={};let response;
let devices=[{kind:'videoinput',deviceId:'built',label:'Built-in Camera'},{kind:'videoinput',deviceId:'virtual',label:'Virtual Camera'},{kind:'audioinput',deviceId:'internal',label:'Internal Microphone'},{kind:'audioinput',deviceId:'usb',label:'USB Microphone'}];
const media={enumerateDevices:async()=>devices,addEventListener:(name,fn)=>mediaEvents[name]=fn};
const sandbox={document:{hidden:false,getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),querySelectorAll:()=>steps,addEventListener(){}},window:{isSecureContext:true,pywebview:{api:{resolve_strict_camera:async label=>{nativeLabels.push(label);return{success:true,native_id:'b'.repeat(64),name:label};}}}},navigator:{onLine:true,mediaDevices:media},currentTab:'dashboard',strictStudyStarting:false,strictSessionData:{workflow:{state:'IDLE'}},showToast:(m)=>toasts.push(m),navAppPage:async p=>sandbox.currentTab=p,setInterval(){},Date,console,
fetch:async(url,options)=>{calls.push({url,options});return{ok:true,json:async()=>JSON.parse(JSON.stringify(response))};}};
function state(ready=false){const device={ready,joined:ready,fresh:ready,status:ready?'Ready':'Not joined',report:{camera:ready,microphone:ready,online:ready,secure_context:ready,visible:ready}};return{success:true,preparation_id:'prep-current',ready,devices:{WINDOWS:device,ANDROID:device,IOS:device},invitations:[],environment:{ready,network_message:'Observed network check',browser_message:'Embedded browser',allowlist_configured:true,restriction_status:'Configured',focus_seconds:900,window_in_seconds:1200,window:{window_start:'2026-11-02T15:30:00+05:30'}}};}
vm.createContext(sandbox);vm.runInContext(source,sandbox);const run=s=>vm.runInContext(s,sandbox);
(async()=>{
 response=state();await run("navAppPage('strict-session')");
 assert.equal(sandbox.currentTab,'strict-session');assert.equal(nodes.get('ssPrecheckView').hidden,false);
 assert.equal(nodes.get('ssReadyView').hidden,true);assert(nodes.get('btnStartStrictSession').disabled);
 assert.equal(calls.filter(c=>c.url.endsWith('/check')).length,1);assert(!calls.some(c=>c.url==='/api/strict_session/start'));
 assert.equal(nodes.get('ssDevicePanels').children.length,3);
 assert.equal(constraints.length,0);assert.equal(nodes.get('ssCameraInput').children.length,3);
 await run('strictLoadInputs(true)');assert.equal(constraints.length,0); // Consent is required even for discovery capture.
 await run("strictPreparationStep('JOIN')");assert(!nodes.get('ssJoinView').hidden);assert(nodes.get('ssContinueReady').disabled);
 sandbox.fetch=async(url,options)=>{calls.push({url,options});return{ok:false,json:async()=>({success:false,error:'Three devices required'})};};
 await run("strictPreparationStep('READY')");assert.equal(run('strictPreparationView'),'JOIN');assert(toasts.includes('Three devices required'));
 response=state(true);sandbox.fetch=async()=>({ok:true,json:async()=>response});await run('refreshStrictPreparation(true)');await run("strictPreparationStep('READY')");
 assert(!nodes.get('ssReadyView').hidden);assert(!nodes.get('btnStartStrictSession').disabled);
 response=state(false);await run('refreshStrictPreparation(true)');assert(nodes.get('btnStartStrictSession').disabled);
 await run("navAppPage('dashboard')");assert.equal(sandbox.currentTab,'dashboard');
 await run("navAppPage('strict-session')");nodes.get('ssMediaConsent').checked=true;
 media.getUserMedia=async choice=>{constraints.push(choice);return{getTracks:()=>[{stop:()=>stopped.push('discovery')} ]};};
 await run('strictLoadInputs(true)');assert.equal(stopped.pop(),'discovery');assert.equal(run('strictHostMedia'),null);
 nodes.get('ssCameraInput').value='virtual';nodes.get('ssMicrophoneInput').value='usb';await run('strictInputChanged()');
 let finishPermission;media.getUserMedia=choice=>{constraints.push(choice);return new Promise(resolve=>finishPermission=resolve);};
 const testing=run('testStrictPreparationMedia()');await Promise.resolve();await run("navAppPage('dashboard')");
 finishPermission({getTracks:()=>[{stop:()=>stopped.push(true)}]});await testing;
 assert.equal(stopped.length,1);assert.equal(run('strictHostMedia'),null);
 await run("navAppPage('strict-session')");
 nodes.get('ssCameraInput').value='virtual';nodes.get('ssMicrophoneInput').value='usb';await run('strictInputChanged()');
 let attempts=0;media.getUserMedia=async()=>{attempts++;throw Object.assign(Error('denied'),{name:'NotAllowedError'});};
 nodes.get('ssMediaConsent').checked=false;await run('testStrictPreparationMedia()');assert.equal(attempts,0);
 nodes.get('ssMediaConsent').checked=true;await run('testStrictPreparationMedia()');
 assert.equal(attempts,1);assert(!nodes.get('ssMediaHelp').hidden);assert(!nodes.get('ssPrivacyButtons').hidden);
 assert.match(nodes.get('ssMediaMessage').textContent,/privacy settings/);assert(!nodes.get('ssTestMediaButton').disabled);
 await run('ssHostHeartbeat()');assert.match(nodes.get('ssMediaMessage').textContent,/privacy settings/);
 const tracks=[{kind:'video',label:'Virtual Camera',getSettings:()=>({deviceId:'virtual'}),readyState:'live',enabled:true,stop:()=>stopped.push(true)},{kind:'audio',label:'USB Microphone',getSettings:()=>({deviceId:'usb'}),readyState:'live',enabled:true,stop:()=>stopped.push(true)}];
 media.getUserMedia=async choice=>{attempts++;constraints.push(choice);return{getTracks:()=>tracks};};
 await run('testStrictPreparationMedia()');assert.equal(attempts,2);assert(nodes.get('ssMediaHelp').hidden);assert.equal(run('strictHostMedia.getTracks().length'),2);
 assert.equal(constraints.at(-1).video.deviceId.exact,'virtual');assert.equal(constraints.at(-1).audio.deviceId.exact,'usb');assert.equal(nativeLabels.at(-1),'Virtual Camera');
 const before=attempts;nodes.get('ssCameraInput').value='built';await run('strictInputChanged()');assert.equal(attempts,before);assert.equal(run('strictHostMedia'),null);assert.equal(run('strictHostCamera'),null);
 media.getUserMedia=async()=>{throw Object.assign(Error('missing'),{name:'OverconstrainedError'});};
 await run('testStrictPreparationMedia()');assert.match(nodes.get('ssMediaMessage').textContent,/unavailable/);assert(nodes.get('ssPrivacyButtons').hidden);
 mediaEvents.devicechange();assert(nodes.get('ssTestMediaButton').disabled);assert.equal(run('strictInputsStale'),true);
 devices=devices.filter(d=>d.deviceId!=='built');await run('strictLoadInputs(false)');assert.equal(nodes.get('ssCameraInput').value,'');assert(nodes.get('ssTestMediaButton').disabled);
 console.log('Prerequisite UI: navigation, input inventory, exact selection, consent, denial, retry, cancellation, switching and unplug checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
