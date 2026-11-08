const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const raw=fs.readFileSync(require('node:path').join(__dirname,'../core/strict_ios_camera_client.py'),'utf8');
const normalized=raw.replace(/\r\n/g,'\n');
const source=normalized.split("JS = r'''\n")[1].split("\n'''")[0];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(options={}){
 const nodes=new Map(),events={},windowEvents={},apiCalls=[],mediaCalls=[],sockets=[],peers=[],timers=new Map(),storage=new Map();let timerID=0;
 class Element{constructor(){this.children=[];this.hidden=false;this.disabled=false;this.dataset={};}replaceChildren(){this.children=[];}append(...children){this.children.push(...children);}addEventListener(name,fn){this[name]=fn;}async play(){}}
 for(const id of ['cameraState','cameraHelp','startCamera','stopCamera','cameraActive','cameraPlaceholder','cameraDiagnostics','cameraPreview','companionTimer','timerSyncStatus','fullscreenTimer'])nodes.set(id,new Element());
 const track={kind:'video',stopped:false,stop(){this.stopped=true;}};
 const stream={getTracks:()=>[track],getVideoTracks:()=>[track],getAudioTracks:()=>[]};
 const document={hidden:false,getElementById:id=>nodes.get(id),createElement:()=>new Element(),addEventListener:(name,fn)=>events[name]=fn};
 const pageClasses=new Set();document.body={classList:{contains:name=>pageClasses.has(name),add:name=>pageClasses.add(name),remove:name=>pageClasses.delete(name)}};
 document.documentElement={requestFullscreen:async()=>{if(options.fullscreenDenied)throw Error('Unsupported');document.fullscreenElement=document.documentElement;}};
 document.exitFullscreen=async()=>{document.fullscreenElement=null;};
 const window={isSecureContext:options.secure!==false,addEventListener:(name,fn)=>windowEvents[name]=fn};window.self=window;window.top=window;
 const navigator={mediaDevices:{getUserMedia:async constraints=>{mediaCalls.push(constraints);if(options.mediaError)throw Object.assign(Error(options.mediaError),{name:options.mediaError});return stream;}},permissions:{query:async()=>({state:'prompt'})}};
 class WebSocket{
  constructor(url){this.url=url;this.readyState=0;this.sent=[];sockets.push(this);}
  send(value){this.sent.push(JSON.parse(value));}
  close(){this.readyState=3;this.closed=true;}
  open(){this.readyState=1;this.onopen();}
  receive(value){this.onmessage?.({data:JSON.stringify(value)});}
 }
 class Peer{
  constructor(config){this.config=config;this.tracks=[];peers.push(this);}
  addTrack(track,stream){this.tracks.push({track,stream});}
  async createOffer(){return{type:'offer',sdp:'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n'};}
  async setLocalDescription(description){this.localDescription=description;if(options.earlyICE)this.onicecandidate?.({candidate:{candidate:'candidate:before-offer',sdpMid:'0',sdpMLineIndex:0}});}
  async setRemoteDescription(description){this.remoteDescription=description;}
  async addIceCandidate(){}
  close(){this.closed=true;}
 }
 const binding={role:'IOS',preparation_id:'prep-1',credential:'registered-companion-secret',expires_at:Date.now()+600000};
 if(!options.unregistered)storage.set('strictrecords.timer.binding.v2',JSON.stringify(binding));
 const session=new Map();
 if(options.timerJoin)session.set('strictrecords.ios.join.token','obsolete-enrollment-token');
 const sandbox={window,document,navigator,crypto:require('node:crypto').webcrypto,location:{protocol:options.secure===false?'http:':'https:',host:'camera.example.test',hash:options.timerJoin?'#timer-enrollment-token':options.noToken?'':'#one-time-secret',search:'?preparation_id=prep-1',pathname:'/ios-camera'},
 history:{replaceState(...args){sandbox.cleanedURL=args.at(-1);const url=new URL(sandbox.cleanedURL,'https://camera.example.test');Object.assign(sandbox.location,{hash:url.hash,search:url.search,pathname:url.pathname});}},localStorage:{getItem:key=>{if(options.blockedStorage)throw Error('Storage blocked');return storage.get(key)||null;},setItem:(key,value)=>{if(options.blockedStorage)throw Error('Storage blocked');storage.set(key,value);},removeItem:key=>{if(options.blockedStorage)throw Error('Storage blocked');storage.delete(key);}},sessionStorage:{getItem:key=>{if(options.blockedStorage)throw Error('Storage blocked');return session.get(key)||null;},setItem:(key,value)=>{if(options.blockedStorage)throw Error('Storage blocked');session.set(key,value);},removeItem:key=>{if(options.blockedStorage)throw Error('Storage blocked');session.delete(key);}},URLSearchParams,AbortController,RTCPeerConnection:Peer,WebSocket,console,
 setTimeout(fn,delay){const id=++timerID;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},
 setInterval(fn,delay){const id=++timerID;timers.set(id,{fn,delay});return id;},clearInterval(id){timers.delete(id);},
 fetch:async(url,request)=>{const body=JSON.parse(request.body);apiCalls.push({url,request,body});const denied=(url.endsWith('/grant')&&options.grantDenied)||(url.endsWith('/heartbeat')&&options.timerDisconnected);return{ok:!denied,status:denied?409:200,json:async()=>denied?{success:false,error:'Windows unavailable.'}:url.endsWith('/join')?{...binding,success:true}:({success:true,token:'fresh-camera-token',camera_credential:'camera-secret',preparation_id:'prep-1',session_id:'session-1',credential_expires_at:Date.now()+180000,timer:{remaining_seconds:321,label:'Focus session'},ice_servers:[{urls:'turns:relay.example.test',username:'short-lived',credential:'relay-secret'}]})};}};
 vm.createContext(sandbox);
 let bootstrapDone;
 if(options.timerJoin){
  const joinRaw=fs.readFileSync(require('node:path').join(__dirname,'../core/strict_ios_join.py'),'utf8').replace(/\r\n/g,'\n');
  const joinSource=joinRaw.split("BOOTSTRAP = r'''\n")[1].split("\n'''")[0].replace("await import('/ios-camera/client.js');","await window.__loadCameraClient();");
  window.__loadCameraClient=()=>vm.runInContext(source,sandbox);
  bootstrapDone=vm.runInContext(joinSource,sandbox);
 }else vm.runInContext(source,sandbox);
 return{...sandbox,camera:window.strictIOSCamera,bootstrapDone,nodes,events,windowEvents,apiCalls,mediaCalls,sockets,peers,track,stream,timers,storage,session};
}
test('opening page records invitation metadata but never captures camera or redeems automatically',async()=>{
 const f=fixture();await tick();assert(!f.cleanedURL?.includes('one-time-secret'));
 assert.equal(f.mediaCalls.length,0);assert.equal(f.apiCalls.filter(call=>call.url==='/ios-camera/opened').length,1);assert.equal(f.apiCalls.filter(call=>call.url==='/ios-camera/redeem').length,0);assert.equal(f.camera.state,'IOS_INVITE_OPENED');
});
test('explicit start redeems registered participant and requests video only',async()=>{
 const f=fixture();await f.camera.start();
 const redeem=f.apiCalls.filter(call=>call.url==='/ios-camera/redeem');assert.equal(redeem.length,1);assert.equal(redeem[0].body.companion_credential,'registered-companion-secret');
 assert.equal(redeem[0].body.preparation_id,'prep-1');assert.equal(f.mediaCalls[0].audio,false);
 assert.equal(f.mediaCalls[0].video.frameRate.max,15);assert.equal(f.mediaCalls[0].video.facingMode.ideal,'user');
 assert.equal(f.sockets[0].url,'wss://camera.example.test/ios-camera/signaling');
 assert(!f.sockets[0].url.includes('secret'));assert.equal(f.camera.confirmedFrames,false);
 f.sockets[0].open();assert.equal(f.sockets[0].sent[0].type,'authenticate');
 f.sockets[0].receive({type:'authenticated',ice_servers:[]});await tick();
 assert.equal(f.peers[0].tracks.length,1);assert.equal(f.peers[0].tracks[0].track.kind,'video');
 assert(f.sockets[0].sent.some(value=>value.type==='offer'));
 assert.equal(f.camera.state,'IOS_VIDEO_CONNECTING');
 assert(!f.sockets[0].sent.some(value=>value.state==='IOS_CAMERA_STREAMING'));
 assert(f.apiCalls.every(call=>!('jpeg' in call.body)&&!('frame' in call.body)));
});
test('streaming requires Windows frame confirmation, not connected signaling or ICE',async()=>{
 const f=fixture();await f.camera.start();const socket=f.sockets[0];socket.open();socket.receive({type:'authenticated'});await tick();
 const peer=f.peers[0];peer.connectionState='connected';peer.onconnectionstatechange();
 assert.equal(f.camera.state,'IOS_VIDEO_CONNECTING');assert.equal(f.camera.confirmedFrames,false);
 socket.receive({type:'state',state:'IOS_CAMERA_STREAMING',frames_received:0});await tick();
 // A state name without actual received-frame evidence is never accepted.
 assert.notEqual(f.camera.state,'IOS_CAMERA_STREAMING');
 socket.receive({type:'receiver_frames',session_id:'session-1',frames_received:3,last_frame_at:123});await tick();
 assert.equal(f.camera.state,'IOS_CAMERA_STREAMING');assert.equal(f.camera.confirmedFrames,true);
});
test('background interruption releases camera and preserves session for explicit resume',async()=>{
 const f=fixture();await f.camera.start();const credential=f.camera.credential;
 f.document.hidden=true;f.events.visibilitychange();
 assert.equal(f.track.stopped,true);assert.equal(f.camera.stream,null);assert.equal(f.camera.state,'IOS_CAMERA_INTERRUPTED');
 assert.equal(f.camera.credential,credential);assert.equal(f.camera.sessionID,'session-1');
 f.document.hidden=false;f.events.visibilitychange();await tick();
 assert.equal(f.mediaCalls.length,1);assert.match(f.nodes.get('cameraHelp').textContent,/Resume/);
});
test('stop action ends capture and transport without clearing the registered timer',async()=>{
 const f=fixture();await f.camera.start();const socket=f.sockets[0];socket.open();f.camera.stop();
 assert.equal(f.track.stopped,true);assert.equal(socket.closed,true);assert.equal(f.camera.stream,null);
 assert.equal(f.camera.state,'IOS_CAMERA_STOPPED');assert.equal(f.nodes.get('cameraActive').hidden,true);
 assert.equal(f.camera.credential,'camera-secret');
});
test('server session end releases all media and prevents silent restart',async()=>{
 const f=fixture();await f.camera.start();f.sockets[0].receive({type:'stop'});await tick();
 assert.equal(f.track.stopped,true);assert.equal(f.camera.closed,true);assert.equal(f.camera.credential,null);
 await f.camera.start();assert.equal(f.mediaCalls.length,1);assert.equal(f.camera.stream,null);
});
test('insecure context and unregistered participant fail before camera request',async()=>{
 for(const options of [{secure:false},{unregistered:true}]){
  const f=fixture(options);await f.camera.start();assert.equal(f.mediaCalls.length,0);assert.equal(f.apiCalls.filter(call=>call.url==='/ios-camera/redeem').length,0);
  assert.equal(f.camera.state,'IOS_CAMERA_STOPPED');
 }
});
test('permission error remains accurately identified and never falls back to microphone',async()=>{
 for(const name of ['NotAllowedError','NotFoundError','NotReadableError','OverconstrainedError','SecurityError','TypeError','NetworkError']){
  const f=fixture({mediaError:name});await f.camera.start();assert.equal(f.camera.diagnostics.camera_error,name);
  assert.equal(f.mediaCalls.length,1);assert.equal(f.mediaCalls[0].audio,false);assert.equal(f.camera.stream,null);
 }
});
test('network reconnect preserves the session and reuses authenticated grant',async()=>{
 const f=fixture();await f.camera.start();f.sockets[0].open();f.sockets[0].onclose();
 assert.equal(f.camera.state,'IOS_RECONNECTING');const retry=[...f.timers.values()].find(timer=>timer.delay===1000);assert(retry);retry.fn();
 assert.equal(f.sockets.length,2);assert.equal(f.apiCalls.filter(call=>call.url==='/ios-camera/redeem').length,1);assert.equal(f.camera.sessionID,'session-1');assert.equal(f.mediaCalls.length,1);
});
test('credential rotation reauthenticates current WSS socket without restarting camera',async()=>{
 const f=fixture();await f.camera.start();const socket=f.sockets[0];socket.open();
 f.camera.applyCredential({camera_credential:'rotated-camera-secret',preparation_id:'prep-1',session_id:'session-1',credential_expires_at:Date.now()+180000,ice_servers:[]});
 assert.equal(socket.sent.at(-1).type,'authenticate');assert.equal(socket.sent.at(-1).credential,'rotated-camera-secret');
 assert.equal(f.camera.credential,'rotated-camera-secret');assert.equal(f.mediaCalls.length,1);assert.equal(f.camera.stream,f.stream);
 assert.equal(f.sockets.length,1);assert.equal(f.apiCalls.filter(call=>call.url==='/ios-camera/redeem').length,1);
});
test('camera denial reaches Windows as diagnostic metadata before signaling exists',async()=>{
 const f=fixture({mediaError:'NotAllowedError'});await f.camera.start();await f.camera.metadataQueue;
 const report=f.apiCalls.filter(call=>call.url==='/ios-camera/status').at(-1);assert(report);
 assert.equal(report.body.state,'IOS_CAMERA_REVOKED');assert.equal(report.body.diagnostics.error_name,'NotAllowedError');
 assert.equal(report.body.diagnostics.audio_tracks_present,false);assert.equal(report.body.diagnostics.protocol,'https:');
 assert.equal(f.sockets.length,0);assert(!('jpeg' in report.body));
});
test('local ICE discovered during setLocalDescription is sent after the offer',async()=>{
 const f=fixture({earlyICE:true});await f.camera.start();const socket=f.sockets[0];socket.open();socket.receive({type:'authenticated'});await tick();
 const order=socket.sent.map(value=>value.type);assert(order.indexOf('offer')>=0);assert(order.indexOf('candidate')>order.indexOf('offer'));
});
test('camera page preserves registered timer identity and only receives authoritative countdown',async()=>{
 const f=fixture();await tick();
 const timer=f.apiCalls.find(call=>call.url==='/api/strict_session/preparation/heartbeat');assert(timer);
 assert.equal(timer.body.preparation_id,'prep-1');assert.equal(timer.body.role,'IOS');assert.equal(timer.body.credential,'registered-companion-secret');
 assert.equal(timer.body.report.timer_ready,true);assert.equal(timer.body.report.visible,true);assert.equal(timer.body.report.client_version,'StrictRecords iOS Camera Companion v1');
 assert.equal(f.nodes.get('companionTimer').textContent,'05:21');
 const lease=JSON.parse(f.storage.get('strictrecords.camera.timer-lease.v1'));assert.equal(lease.preparation_id,'prep-1');assert(lease.expires_at>Date.now());
 assert.equal(JSON.parse(f.storage.get('strictrecords.timer.binding.v2')).credential,'registered-companion-secret');assert.equal(f.mediaCalls.length,0);
 assert(f.apiCalls.every(call=>!call.url.endsWith('/start')&&!call.url.includes('/score')&&!call.url.includes('/points')));
 f.document.hidden=true;f.events.visibilitychange();await tick();
 assert(!f.storage.has('strictrecords.camera.timer-lease.v1'));
 assert.equal(f.apiCalls.filter(call=>call.url==='/api/strict_session/preparation/heartbeat').at(-1).body.report.visible,false);
});

test('combined timer page requests camera grant only on Start, and waits for active Windows session',async()=>{
 const f=fixture({noToken:true,grantDenied:true});await tick();
 assert.equal(f.apiCalls.filter(c=>c.url.endsWith('/grant')).length,0);
 await f.camera.start();assert.equal(f.mediaCalls.length,0);
 assert.match(f.nodes.get('cameraHelp').textContent,/Windows unavailable/);
 const ready=fixture({noToken:true});await ready.camera.start();
 assert.equal(ready.apiCalls.filter(c=>c.url.endsWith('/grant')).length,1);
 assert.equal(ready.mediaCalls.length,1);
 assert.equal(ready.apiCalls.find(c=>c.url.endsWith('/redeem')).body.token,'fresh-camera-token');
});
test('display follows paused authority and freezes estimates when heartbeat is stale',()=>{
 const f=fixture();f.camera.timerSnapshot={remaining_seconds:120,running:false};
 f.camera.timerSnapshotAt=Date.now()-20000;f.camera.drawTimer();assert.equal(f.nodes.get('companionTimer').textContent,'02:00');
 f.camera.timerSnapshot.running=true;f.camera.drawTimer();assert.equal(f.nodes.get('companionTimer').textContent,'01:50');
});

test('real combined bootstrap consumes timer enrollment and obtains a separate camera grant',async()=>{
 const f=fixture({timerJoin:true,unregistered:true});await f.bootstrapDone;await tick();
 const camera=f.window.strictIOSCamera;assert(camera);
 assert.equal(f.apiCalls.find(c=>c.url.endsWith('/join')).body.token,'timer-enrollment-token');
 assert.equal(f.mediaCalls.length,0);assert.equal(f.session.size,0);
 assert(!f.apiCalls.some(c=>c.url.endsWith('/opened')||c.url.endsWith('/redeem')));
 await camera.start();
 assert.equal(f.apiCalls.filter(c=>c.url.endsWith('/grant')).length,1);
 const redemption=f.apiCalls.find(c=>c.url.endsWith('/redeem'));
 assert.equal(redemption.body.token,'fresh-camera-token');
 assert.notEqual(redemption.body.token,'timer-enrollment-token');
 assert.equal(f.mediaCalls.length,1);assert.equal(f.mediaCalls[0].audio,false);
});

test('camera streaming badge waits for decoded frame confirmation',async()=>{
 const f=fixture();await f.camera.start();
 assert.equal(f.nodes.get('cameraActive').hidden,true);
 const socket=f.sockets[0];socket.open();
 socket.receive({type:'receiver_frames',session_id:'session-1',frames_received:2});await tick();
 assert.equal(f.nodes.get('cameraActive').hidden,false);
 f.camera.stop();assert.equal(f.nodes.get('cameraActive').hidden,true);
});

test('failed timer heartbeat cannot retain a synchronized badge',async()=>{
 const f=fixture({timerDisconnected:true});await tick();
 assert.equal(f.camera.timerConnected,false);
 assert.equal(f.nodes.get('timerSyncStatus').dataset.state,'warning');
 assert.match(f.nodes.get('timerSyncStatus').textContent,/Reconnecting/);
});

test('timer expansion preserves true fullscreen reporting and supports Safari fallback',async()=>{
 for(const fullscreenDenied of [false,true]){
  const f=fixture({fullscreenDenied});await tick();
  await f.nodes.get('fullscreenTimer').click();await tick();
  assert.equal(f.document.body.classList.contains('timer-expanded'),true);
  assert.equal(!!f.document.fullscreenElement,!fullscreenDenied);
  assert.equal(f.nodes.get('fullscreenTimer').textContent,'Restore page');
  const report=f.apiCalls.filter(c=>c.url.endsWith('/heartbeat')).at(-1).body.report;
  assert.equal(report.fullscreen,!fullscreenDenied);
  await f.nodes.get('fullscreenTimer').click();await tick();
  assert.equal(f.document.body.classList.contains('timer-expanded'),false);
  assert.equal(!!f.document.fullscreenElement,false);
 }
});

test('retry after camera denial requests a fresh grant instead of reusing a revoked bearer',async()=>{
 const f=fixture({mediaError:'NotAllowedError'});await f.camera.start();await tick();
 assert.equal(f.camera.credential,null);
 const denied=f.apiCalls.find(c=>c.url.endsWith('/status')&&c.body.state==='IOS_CAMERA_REVOKED');
 assert.equal(denied.body.camera_credential,'camera-secret');
 f.navigator.mediaDevices.getUserMedia=async constraints=>{f.mediaCalls.push(constraints);return f.stream;};
 await f.camera.start();
 assert.equal(f.apiCalls.filter(c=>c.url.endsWith('/grant')).length,1);
 assert.equal(f.apiCalls.filter(c=>c.url.endsWith('/redeem')).at(-1).body.token,'fresh-camera-token');
 assert.equal(f.camera.stream,f.stream);
});

test('timer integrity uses the authoritative study ID independently of the preparation camera grant',async()=>{
 const f=fixture();await tick();
 f.camera.sessionID='prep_camera_scope';
 f.camera.timerSnapshot={session_id:'actual-windows-study',remaining_seconds:300,running:true};
 await f.camera.timerHeartbeat();
 const beat=f.apiCalls.filter(c=>c.url.endsWith('/heartbeat')).at(-1);
 assert.equal(beat.body.report.integrity.session_id,'actual-windows-study');
 assert.notEqual(beat.body.report.integrity.session_id,f.camera.sessionID);
});

test('combined enrollment and explicit camera request work when browser storage is blocked',async()=>{
 const f=fixture({timerJoin:true,unregistered:true,blockedStorage:true});
 await f.bootstrapDone;await tick();const camera=f.window.strictIOSCamera;
 assert(camera);assert.equal(f.storage.has('strictrecords.timer.binding.v2'),false);
 assert.equal(f.window.strictIOSTimerBinding.preparation_id,'prep-1');
 await camera.start();assert.equal(f.mediaCalls.length,1);
 assert.equal(f.mediaCalls[0].audio,false);
 assert(f.apiCalls.some(c=>c.url.endsWith('/grant')));
});
