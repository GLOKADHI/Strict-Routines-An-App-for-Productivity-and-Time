const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const targets = JSON.parse(fs.readFileSync('browser_extension/targets.json','utf8'));

function setup(options = {}) {
  let listener;
  const removed = [], created = [];
  const tab = {id:7, url:'https://masstamilan.dev/music', windowId:2, active:true};
  const events = {};
  const event = name => ({addListener(fn) { events[name] = fn; }});
  const chrome = {
    runtime: {getURL:path=>'extension://'+path, onInstalled:event('installed'), onStartup:event('startup'), onMessage:{addListener(fn){listener=fn;}}},
    storage:{local:{async get(){return {browserKey:'comet'};}}},
    alarms:{create(){},onAlarm:event('alarm')},
    tabs:{onRemoved:event('removed'),onActivated:event('activated'),onUpdated:event('updated'),
      async get() {return {...tab};},
      async query() { if(options.queryFails) throw Error('query'); return options.onlyTab ? [{id:7}] : [{id:7},{id:8}]; },
      async create(value){created.push({...value});return {id:9};},
      async remove(id){removed.push(id);}}
  };
  const context = vm.createContext({chrome,navigator:{userAgent:'Chrome/140'},URL,AbortSignal,Date,Map,Set,console:{info(){}},
    fetch: async url => {
      if(url.startsWith('extension:')) return {json:async()=>targets};
      if(options.offline) throw Error('offline');
      if(options.navigate) tab.url = 'https://bank.example';
      return {ok:true,json:async()=>({restricted:options.allowed ? false : true, score:0, requiredScore:60})};
    }});
  vm.runInContext(fs.readFileSync('browser_extension/service_worker.js','utf8'), context);
  return {context,tab,removed,created,events,
    request(type='STRICTBOT_CHECK_ACCESS', sender={tab:{...tab},frameId:0}) {
      return new Promise(resolve=>listener({type},sender,resolve));
    }};
}

test('an older planning acknowledgement cannot erase a newer queued choice',async()=>{
  const app=setup();let pending={request_id:'request-old',expected_revision:1};let release,started;
  const reached=new Promise(resolve=>{started=resolve;});
  app.context.chrome.storage.local={get:async key=>({[key]:pending}),remove:async()=>{pending=null;},set:async obj=>{pending=obj.pendingGymChoice;}};
  app.context.fetch=async()=>({json:()=>new Promise(resolve=>{release=resolve;started();})});
  const sending=app.context.flushPendingPlanningChoice('pendingGymChoice','/api/gym/decision');
  await reached;pending={request_id:'request-new',expected_revision:2};release({success:true});await sending;
  assert.equal(pending.request_id,'request-new');
});

test('strict hostname allowlist rejects URLs containing names in paths or queries', () => {
  const {context} = setup();
  const match = context.serviceForUrl;
  for(const [key,target] of Object.entries(targets)) for(const domain of target.domains) {
    assert.equal(match('https://'+domain,targets),key);
    assert.equal(match('https://sub.'+domain,targets),key);
    assert.equal(match('https://'+domain+'.evil.example',targets),null);
  }
  for(const value of ['https://example.com/?q=masstamilan.dev','https://masstamilan.dev@bank.example','file://masstamilan.dev']) assert.equal(match(value,targets),null);
});
test('polling keeps one session; a real activation creates a new session', async () => {
  const app=setup(); const first=await app.request();
  for(let i=0;i<50;i++) assert.equal((await app.request()).sessionId,first.sessionId);
  app.events.activated({tabId:7}); assert.notEqual((await app.request()).sessionId,first.sessionId);
});
test('Back Home removes only the verified tab',async()=>{
  const app=setup(); assert.equal((await app.request('STRICTBOT_CLOSE_RESTRICTED_TAB')).success,true);
  assert.deepEqual(app.removed,[7]); assert.deepEqual(app.created,[]);
});
test('the last restricted tab gets a replacement so the window remains',async()=>{
  const app=setup({onlyTab:true}); await app.request('STRICTBOT_CLOSE_RESTRICTED_TAB');
  assert.deepEqual(app.created,[{windowId:2,active:true}]); assert.deepEqual(app.removed,[7]);
});
test('an unlock between display and click allows the tab',async()=>{
  const app=setup({allowed:true}); assert.equal((await app.request('STRICTBOT_CLOSE_RESTRICTED_TAB')).unlocked,true);
  assert.deepEqual(app.removed,[]);
});
test('navigation to another domain cancels removal',async()=>{
  const app=setup({navigate:true}); assert.equal((await app.request('STRICTBOT_CLOSE_RESTRICTED_TAB')).success,false);
  assert.deepEqual(app.removed,[]);
});
test('a hidden tab or failed sibling query is never removed',async()=>{
  const hidden=setup(); hidden.tab.active=false;
  await hidden.request('STRICTBOT_CLOSE_RESTRICTED_TAB'); assert.deepEqual(hidden.removed,[]);
  const failed=setup({queryFails:true}); await failed.request('STRICTBOT_CLOSE_RESTRICTED_TAB'); assert.deepEqual(failed.removed,[]);
});
test('simultaneous clicks cannot duplicate a tab removal',async()=>{
  const app=setup();
  await Promise.all([app.request('STRICTBOT_CLOSE_RESTRICTED_TAB'),app.request('STRICTBOT_CLOSE_RESTRICTED_TAB')]);
  assert.deepEqual(app.removed,[7]);
});
test('offline state has no invented score',async()=>{
  const app=setup({offline:true}); const status=await app.request();
  assert.equal(status.unavailable,true); assert.equal(status.score,null); assert.equal(status.restricted,true);
});
test('non-top-frame and unrelated senders cannot close tabs',async()=>{
  const app=setup();
  await app.request('STRICTBOT_CLOSE_RESTRICTED_TAB',{tab:{...app.tab},frameId:1});
  app.tab.url='https://bank.example'; await app.request('STRICTBOT_CLOSE_RESTRICTED_TAB');
  assert.deepEqual(app.removed,[]);
});


test('dashboard document and toolbar use the same trusted extension identity',async()=>{
  const app=setup();
  app.context.chrome.runtime.getManifest=()=>({version:'1.7.0'});
  app.context.fetch=async url=>({ok:true,json:async()=>url.startsWith('extension:')?targets:url.includes('/heartbeat')?{success:true,connected:true}:{restricted:false,currentScore:80,requiredScore:80}});
  const result=await app.request('STRICTBOT_DASHBOARD_STATUS',{url:'extension://extension_popup.html',tab:{id:9},frameId:0});
  assert.equal(result.success,true);assert.equal(result.connection.success,true);assert.equal(result.rows.length,Object.keys(targets).length);
  assert.ok(result.rows.every(row=>row.restricted===false));assert.equal(result.version,'1.7.0');
});

test('server decision error is not mislabeled as a disconnected desktop',async()=>{
  const app=setup();
  app.context.fetch=async url=>({ok:!url.includes('/browser_status'),status:503,json:async()=>url.startsWith('extension:')?targets:url.includes('/heartbeat')?{success:true}:{code:'DECISION_SERVICE_ERROR',error:'Desktop connected; access check failed.'}});
  const result=await app.request();
  assert.equal(result.restricted,true);assert.equal(result.unavailable,true);assert.equal(result.code,'DECISION_SERVICE_ERROR');
  assert.match(result.statusText,/access check failed/);assert.equal(result.currentScore,null);
});

test('a newly allowed decision reaches the content script without stale blocking',async()=>{
  const app=setup();let allow=false;
  app.context.fetch=async url=>({ok:true,json:async()=>url.startsWith('extension:')?targets:{restricted:!allow,currentScore:allow?60:59,requiredScore:60}});
  assert.equal((await app.request()).restricted,true);allow=true;
  const result=await app.request();assert.equal(result.restricted,false);assert.equal(result.currentScore,60);
});
