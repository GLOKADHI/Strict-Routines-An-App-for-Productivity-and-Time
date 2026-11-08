const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const path = require('path');
const js = fs.readFileSync(process.env.STRICTRECORDS_UI_SOURCE || path.join(__dirname, '..', 'core', 'ui_dashboard.py'), 'utf8');
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {textContent:'', style:{}, dataset:{}, classList:{toggle(){},add(){},remove(){}},setAttribute(k,v){this[k]=v;},removeAttribute(k){delete this[k];}});
  return nodes.get(id);
}
let handlers={}, requests=[], timerId=0, timers=new Map(), fetchImpl;
const context = vm.createContext({console, Date, AbortController,
  document:{hidden:false,getElementById:node,addEventListener:(name,fn)=>handlers[name]=fn},
  window:{_strictRecordsHidden:false,addEventListener:(name,fn)=>handlers[name]=fn,pywebview:{api:{get_desktop_restriction_status:()=>fetchImpl('/native/access',{}).then(r=>r.json())}}},
  setTimeout:(fn,ms)=>{const id=++timerId;timers.set(id,{fn,ms});return id;},
  clearTimeout:id=>timers.delete(id),
  fetch:(url,opts)=>{requests.push({url,opts});return fetchImpl(url,opts);},
  updateRestrictionUI:()=>{}, renderRestrictionTargets:()=>{}
});
const success = status => ({success:true,source:'WINDOWS_BROWSER_ACCESS_ENGINE',status});
const poll = js.slice(js.indexOf('let isPollingRestriction = false;'),js.indexOf('let lastRestrictionStatus = null;'));
const sidebar = js.slice(js.indexOf('let isFetchingSidebarRestrictions = false;'),js.indexOf('function retrySidebarRestriction('));
vm.runInContext('let lastRestrictionStatus = null;'+poll+sidebar,context);
function run(code){return vm.runInContext(code,context);}
const allow={enforcementStatus:'APPLIED',effectiveAccessAllowed:true,effectiveRestrictionEnabled:false,policyReason:'Administrator authorization'};
const block={enforcementStatus:'APPLIED',effectiveAccessAllowed:false,effectiveRestrictionEnabled:true,policyReason:'Score below threshold'};
context.allow=allow;context.block=block;
let cases=0;
function check(state,yt,ig){assert.equal(node('sidebarRestrictionsBadge').textContent,state);assert.equal(node('sidebarYtChipLabel').textContent,'YT: '+yt);assert.equal(node('sidebarIgChipLabel').textContent,'IG: '+ig);cases++;}
run('renderCompactRestrictionStatus({youtube:allow,instagram:block})');check('PARTIAL','ALLOWED','BLOCKED');
assert.match(node('sidebarYtChip').title,/Administrator authorization/);
assert.match(node('sidebarRestrictionsFreshness').textContent,/1 blocked · 1 allowed · Checked/);
run('renderCompactRestrictionStatus({youtube:block,instagram:block})');check('BLOCKED','BLOCKED','BLOCKED');
run('renderCompactRestrictionStatus({youtube:allow,instagram:allow})');check('ALLOWED','ALLOWED','ALLOWED');
run("renderCompactRestrictionStatus({youtube:{...allow,enforcementStatus:'FAILED'},instagram:block})");check('UNKNOWN','UNKNOWN','BLOCKED');
run("renderCompactRestrictionStatus({youtube:{...allow,enforcementStatus:'PENDING'},instagram:block})");check('APPLYING','APPLYING','BLOCKED');
run('renderCompactRestrictionStatus({youtube:allow})');check('UNKNOWN','ALLOWED','UNKNOWN');
run('renderCompactRestrictionStatus({youtube:{...allow,effectiveRestrictionEnabled:true},instagram:block})');check('UNKNOWN','UNKNOWN','BLOCKED');
run('renderCompactRestrictionStatus(null,true)');check('OFFLINE','OFFLINE','OFFLINE');
async function main(){
  fetchImpl=async url=> url==='/native/access' ? {ok:true,json:async()=>success({youtube:block,instagram:allow})} : {ok:false,status:503};
  await run('pollRestrictionState()');check('PARTIAL','BLOCKED','ALLOWED');
  assert.equal(requests.length,1);assert(requests.every(r=>r.opts.cache==='no-store'&&r.opts.signal));cases++;
  fetchImpl=async()=>{throw new Error('offline')};
  await run('fetchSidebarRestrictions()');check('OFFLINE','OFFLINE','OFFLINE');
  fetchImpl=async()=>({ok:true,json:async()=>success({youtube:allow,instagram:allow})});
  await run('fetchSidebarRestrictions()');check('ALLOWED','ALLOWED','ALLOWED');
  fetchImpl=async()=>({ok:true,json:async()=>({})});
  await run('fetchSidebarRestrictions()');check('OFFLINE','OFFLINE','OFFLINE');
  requests=[];context.document.hidden=true;await run('pollRestrictionState()');assert.equal(requests.length,0);cases++;
  context.document.hidden=false;fetchImpl=async()=>({ok:true,json:async()=>success({youtube:block,instagram:block})});
  handlers.focus();await new Promise(resolve=>setImmediate(resolve));check('BLOCKED','BLOCKED','BLOCKED');
  assert(handlers.visibilitychange&&handlers['strictrecords-visibility']);cases++;
  // Abort a genuinely unfinished request and verify the fetch guard recovers.
  let resolveNative; fetchImpl=()=>new Promise(resolve=>{resolveNative=resolve;});
  const pending=run('fetchSidebarRestrictions()');await new Promise(resolve=>setImmediate(resolve));
  const countBeforeDuplicate=requests.length;await run('fetchSidebarRestrictions()');assert.equal(requests.length,countBeforeDuplicate);cases++;
  [...timers.values()].filter(t=>t.ms===8000).forEach(t=>t.fn());await pending;
  check('OFFLINE','OFFLINE','OFFLINE');assert.equal(run('isFetchingSidebarRestrictions'),false);cases++;
  resolveNative({json:async()=>success({youtube:block,instagram:block})});await new Promise(resolve=>setImmediate(resolve));
  delete context.window.pywebview;await run('fetchSidebarRestrictions()');check('OFFLINE','OFFLINE','OFFLINE');
  console.log(`${cases} live-status checks passed`);
}
main().catch(error=>{console.error(error);process.exitCode=1;});
