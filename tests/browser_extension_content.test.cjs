const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../browser_extension/content.js'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(options={}) {
  const events={},windowEvents={},timers=new Map(),messages=[];
  let error=null,errorReads=0,ui={},deferred=false,reply={restricted:true,service:'instagram',currentScore:79,requiredScore:80,statusText:'One more point required.'};
  const media={duration:options.duration??60,currentTime:0,tagName:'VIDEO',addEventListener(){},classList:{add(){},remove(){}},style:{},paused:false,pause(){this.paused=true;this.pauses=(this.pauses||0)+1;}};
  function node(tag='div') {
    return {tag,style:{overflow:'auto'},inert:false,isConnected:false,hidden:false,disabled:false,listeners:{},children:[],
      append(...children){this.children.push(...children);children.forEach(n=>n.isConnected=true);},
      appendChild(child){this.append(child);return child;},
      remove(){this.isConnected=false;},focus(){document.activeElement=this;if(this.shadow)this.shadow.activeElement=this;},
      addEventListener(name,fn){this.listeners[name]=fn;},
      click(){return this.listeners.click?.();},
      classList:{add(){},remove(){},contains(){return false;}},
      querySelector(selector){return selector==='img'?this.image:undefined;},
      attachShadow(){const shadow=node('shadow');shadow.getElementById=id=>ui[id];this.shadowRoot=shadow;return shadow;},
      set innerHTML(value){this.html=value;this.image=node('img');const ids=[...value.matchAll(/id="([^"]+)"/g)].map(m=>m[1]);if(ids.length)ui=Object.fromEntries(ids.map(id=>[id,node('button')]));},
      get textContent(){return this.text||'';},set textContent(value){this.text=value;}};
  }
  const body=node('body'),html=node('html');body.isConnected=true;html.isConnected=true;
  const document={body,documentElement:html,hidden:false,activeElement:null,
    getElementById:()=>null,
    title:options.title??'Linear algebra - YouTube',scripts:[],
    querySelector:selector=>selector==='video'?media:selector.includes('h1')?{textContent:options.title??'Linear algebra'}:selector.includes('#description')?{textContent:options.description??'A detailed lesson about matrices, eigenvalues, and linear transformations.'}:null,
    createElement:node,addEventListener:(name,fn)=>events[name]=fn,querySelectorAll:selector=>selector.includes('video')||selector.includes('audio')?[media]:[]};
  const runtime={onMessage:{addListener(fn){events.runtimeMessage=fn;}},getURL:path=>'chrome-extension://test/'+path,
    get lastError(){errorReads++;return error;},
    sendMessage(message,callback){messages.push({message,callback});if(!deferred)callback(error?undefined:reply);}};
  const window={location:new URL(options.url??'https://www.instagram.com/'),postMessage(){},addEventListener:(name,fn)=>windowEvents[name]=fn};window.top=window;
  const instrumented=source.replace(/\}\)\(\);\s*$/, 'globalThis.__reviewTest={triggerEducationalRequestFromOverlay,removePopup,ensurePopup};\n})();');
  const context={window,document,URL,console,chrome:{runtime,storage:{local:{async get(){return {};},async set(){}},onChanged:{addListener(){}}}},MutationObserver:class{observe(){}disconnect(){}},
    setInterval:(fn,delay)=>{const id=timers.size+1;timers.set(id,{fn,delay,interval:true});return id;},
    setTimeout:(fn,delay)=>{const id=timers.size+1;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),clearInterval:id=>timers.delete(id)};
  vm.runInNewContext(instrumented,context);
  return {document,body,html,media,messages,timers,events,windowEvents,api:context.__reviewTest,window,get ui(){return ui;},get errorReads(){return errorReads;},
    setReply:value=>reply=value,setError:value=>error=value,setDeferred:value=>deferred=value};
}
test('blocked page uses its exact decision, shields the body and pauses playback',async()=>{
  const f=fixture();await settle();
  assert.equal(f.ui.title.textContent,'Instagram is restricted');assert.match(f.ui.score.textContent,/79.*80.*1 more/);
  assert.equal(f.body.inert,true);assert.equal(f.body.style.overflow,'hidden');assert.equal(f.media.paused,true);
  const played={pause(){this.paused=true;}};f.events.play({target:played});assert.equal(played.paused,true);
});
test('new allowed response immediately removes shielding and restores scrolling',async()=>{
  const f=fixture();await settle();f.setReply({restricted:false,currentScore:80,requiredScore:80});
  f.ui.retry.listeners.click();await settle();assert.equal(f.body.inert,false);assert.equal(f.body.style.overflow,'auto');
  assert.equal(f.html.style.overflow,'auto');
});
test('closed message port is consumed and retry can recover without an unchecked runtime error',async()=>{
  const f=fixture();await settle();f.setError({message:'The message port closed before a response was received.'});
  f.ui.retry.listeners.click();await settle();assert.ok(f.errorReads>=2);assert.match(f.ui.reason.textContent,/disconnected/);
  f.setError(null);f.setReply({restricted:false});f.ui.retry.listeners.click();await settle();assert.equal(f.body.inert,false);
});
test('stalled callback ends at the explicit deadline and restores retry controls',async()=>{
  const f=fixture();await settle();f.setDeferred(true);f.ui.retry.listeners.click();
  const deadline=[...f.timers.values()].find(t=>t.delay===12000);assert.ok(deadline);deadline.fn();await settle();
  assert.match(f.ui.reason.textContent,/timed out/);assert.equal(f.ui.retry.disabled,false);
});
test('late response cannot rebuild the modal after pagehide',async()=>{
  const f=fixture();await settle();f.setDeferred(true);f.ui.retry.listeners.click();f.windowEvents.pagehide();
  f.messages.at(-1).callback({restricted:true});await settle();assert.equal(f.body.inert,false);
});
test('restoring a cached page shields it until the desktop confirms access again',async()=>{
  const f=fixture();await settle();f.windowEvents.pagehide();f.setDeferred(true);f.windowEvents.pageshow();
  assert.equal(f.body.inert,true);assert.equal(f.ui.title.textContent,'Checking website access');
});
test('malformed status cannot silently grant access',async()=>{
  const f=fixture();await settle();f.setReply({});f.ui.retry.listeners.click();await settle();assert.equal(f.body.inert,true);
});
test('escape stays in the modal rather than exposing the restricted page',async()=>{
  const f=fixture();await settle();let prevented=false;
  f.events.keydown({key:'Escape',preventDefault(){prevented=true;},stopPropagation(){}});
  assert.ok(prevented);assert.equal(f.document.activeElement,f.ui.close);assert.equal(f.body.inert,true);
});


test('YouTube overlay request reaches educational evaluation without an undefined entry point',async()=>{
  const f=fixture({url:'https://www.youtube.com/watch?v=abcdefghijk'});await settle();
  f.setDeferred(true);f.api.triggerEducationalRequestFromOverlay('abcdefghijk');await settle();
  assert.ok(f.messages.some(m=>m.message.type==='STRICTBOT_REQUEST_EDUCATIONAL_VIDEO'));
  assert.match(f.ui.eduReviewBadge.textContent,/STAGE 3/);
});

test('closing a popup while Taufa replies does not access cleared UI or unlock playback',async()=>{
  const f=fixture({url:'https://www.youtube.com/watch?v=abcdefghijk'});await settle();
  f.setDeferred(true);f.api.triggerEducationalRequestFromOverlay('abcdefghijk');await settle();
  const pending=f.messages.findLast(m=>m.message.type==='STRICTBOT_REQUEST_EDUCATIONAL_VIDEO');assert.ok(pending);
  f.api.removePopup();pending.callback({success:true,decision:'EDUCATIONAL',duration_seconds:60});await settle();
  assert.equal(f.document.body.inert,false);
  assert.equal([...f.timers.values()].filter(t=>t.delay===800).length,0);
});

test('an old review cannot change a replacement popup',async()=>{
  const f=fixture({url:'https://www.youtube.com/watch?v=abcdefghijk'});await settle();
  f.setDeferred(true);f.api.triggerEducationalRequestFromOverlay('abcdefghijk');await settle();
  const old=f.messages.findLast(m=>m.message.type==='STRICTBOT_REQUEST_EDUCATIONAL_VIDEO');
  f.api.removePopup();f.api.ensurePopup();const replacement=f.ui;
  old.callback({success:true,decision:'NOT_EDUCATIONAL',reason:'old response'});await settle();
  assert.equal(f.ui,replacement);assert.notEqual(replacement.eduMetaStatus.textContent,'ACCESS REJECTED');
});

test('native metadata fallback can update title and description before evaluation',async()=>{
  const f=fixture({url:'https://www.youtube.com/watch?v=abcdefghijk',title:'YouTube',description:'',duration:0});await settle();
  f.setDeferred(true);f.api.triggerEducationalRequestFromOverlay('abcdefghijk');await settle();
  const metadata=f.messages.findLast(m=>m.message.type==='STRICTBOT_NATIVE_EXTRACT_METADATA');assert.ok(metadata);
  metadata.callback({success:true,title:'Calculus introduction',channel:'Teacher',duration_seconds:60,description:'A full introduction to differentiation and limits.'});await settle();
  assert.equal(f.ui.eduMetaTitle.textContent,'Calculus introduction');
  const request=f.messages.findLast(m=>m.message.type==='STRICTBOT_REQUEST_EDUCATIONAL_VIDEO');assert.ok(request);
  assert.equal(request.message.payload.title,'Calculus introduction');
});

test('a second overlay click does not submit a duplicate in-flight educational request',async()=>{
  const f=fixture({url:'https://www.youtube.com/watch?v=abcdefghijk'});await settle();
  f.setDeferred(true);f.api.triggerEducationalRequestFromOverlay('abcdefghijk');await settle();
  f.api.triggerEducationalRequestFromOverlay('abcdefghijk');await settle();
  assert.equal(f.messages.filter(m=>m.message.type==='STRICTBOT_REQUEST_EDUCATIONAL_VIDEO').length,1);
});
