const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(options = {}) {
  const listeners = {};
  const elements = {connect:{disabled:false,addEventListener(name,fn){listeners[name]=fn;}},
    status:{className:'',textContent:''},'connection-details':{textContent:''}};
  const timers=[], requests=[];
  const context=vm.createContext({document:{getElementById:id=>elements[id]},AbortController,
    setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){},
    async fetch(url,settings){
      requests.push({url,settings});
      if(options.offline) throw Error('Failed to fetch');
      if(options.hang) return new Promise(()=>{});
      return {ok:!options.httpError,status:options.httpError || 200,async json(){
        return {connected:!options.unconfirmed,monitor_running:!options.stopped,
          native_browser:{connected:!options.unsupported,error:options.unsupported?'Component unavailable.':'',
            browsers:options.browsers || []}};
      }};
    }});
  vm.runInContext(fs.readFileSync('browser_extension/popup.js','utf8'),context);
  return {elements,requests,timers,click:()=>listeners.click()};
}

test('plain HTML connects without chrome, storage, runtime, or an extension',async()=>{
  const page=setup(); await page.click();
  assert.equal(page.elements.status.className,'ok');
  assert.match(page.elements.status.textContent,/no extension installation is needed/);
  assert.match(page.requests[0].url,/127\.0\.0\.1:8765\/api\/restriction\/desktop_connection\?connect=1$/);
  assert.equal(page.elements.connect.disabled,false);
});
test('a desktop connection does not fabricate native browser capability',async()=>{
  const page=setup({unsupported:true});await page.click();
  assert.equal(page.elements.status.className,'warning');
  assert.match(page.elements.status.textContent,/monitoring is not ready/);
  assert.match(page.elements['connection-details'].textContent,/Component unavailable/);
});
test('URL-only browser support is reported separately from exact tab control',async()=>{
  const page=setup({browsers:[{browser:'comet.exe',url_detection:true,exact_tab_close:false}]});
  await page.click();
  assert.match(page.elements['connection-details'].textContent,/website detection available; exact tab closure is unavailable/);
});
test('no observed browser is not described as a verified browser',async()=>{
  const page=setup();await page.click();
  assert.match(page.elements['connection-details'].textContent,/connection alone does not confirm/);
});
test('stopped background monitor cannot appear ready',async()=>{
  const page=setup({stopped:true});await page.click();
  assert.equal(page.elements.status.className,'warning');
});
test('HTTP failure reports the actual local app error',async()=>{
  const page=setup({httpError:503});await page.click();
  assert.match(page.elements.status.textContent,/StrictRoutines returned 503/);
  assert.equal(page.elements.connect.disabled,false);
});
test('offline desktop gives a visible error and permits retry',async()=>{
  const page=setup({offline:true});await page.click();
  assert.match(page.elements.status.textContent,/Could not reach StrictRoutines/);
  assert.equal(page.elements.connect.disabled,false);
});
test('unconfirmed response cannot appear connected',async()=>{
  const page=setup({unconfirmed:true});await page.click();
  assert.match(page.elements.status.textContent,/did not confirm the connection/);
  assert.equal(page.elements.status.className,'error');
});
test('hanging request times out; repeated clicks do not duplicate requests',async()=>{
  const page=setup({hang:true});const first=page.click();await page.click();
  await Promise.resolve();assert.equal(page.requests.length,1);
  page.timers[0]();await first;
  assert.equal(page.elements.connect.disabled,false);
  assert.equal(page.requests[0].settings.signal.aborted,true);
});
