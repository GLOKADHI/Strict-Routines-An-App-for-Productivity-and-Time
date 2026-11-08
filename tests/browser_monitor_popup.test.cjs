const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../browser_extension_monitor/popup.js'),'utf8');
async function fixture(browser='chrome') {
  let now=1800000000000, latest={state:'GRACE_PERIOD',is_interrupted:true,checked_at:now,
    grace_deadline:now/1000+299,pending_penalty:0,total_penalty:0,grace_duration_minutes:5};
  const controls={},timers=new Map(),listeners={},opened=[];
  const element=id=>controls[id]??=( {textContent:'',value:'',addEventListener:(name,callback)=>listeners[id+':'+name]=callback} );
  class ClockDate extends Date {static now(){return now;}}
  const context=vm.createContext({Date:ClockDate,console,AbortSignal,STRICTROUTINES_MONITOR:{browser_key:browser,installation_id:'chosen'},
    document:{getElementById:element},addEventListener:(name,callback)=>listeners[name]=callback,
    setInterval:callback=>{const id=timers.size+1;timers.set(id,callback);return id;},clearInterval:id=>timers.delete(id),
    chrome:{storage:{local:{get:async()=>({latestStatus:latest})},onChanged:{addListener:cb=>listeners.change=cb}},
      tabs:{create:value=>opened.push(value)}},fetch:async()=>({ok:true,json:async()=>({success:true})})});
  await vm.runInContext(code,context);
  return {controls,timers,listeners,opened,advance:seconds=>{now+=seconds*1000;for(const callback of [...timers.values()])callback();},
    update:async changes=>{latest={...latest,...changes,checked_at:now};listeners.change({latestStatus:{}},'local');await new Promise(resolve=>setImmediate(resolve));}};
}
test('countdown comes from the actual deadline and clears after operational restoration',async()=>{
  const f=await fixture();assert.match(f.controls.countdown.textContent,/04:59/);
  f.advance(2);assert.match(f.controls.countdown.textContent,/04:57/);
  await f.update({state:'ENABLED',is_interrupted:false});assert.equal(f.timers.size,1);assert.equal(f.controls.deadline.textContent,'');
  f.advance(61);assert.match(f.controls.status.textContent,/Monitoring status unknown/);
});
test('stale status becomes unknown without inventing disablement',async()=>{
  const f=await fixture();f.advance(61);assert.match(f.controls.status.textContent,/Monitoring status unknown/);assert.equal(f.timers.size,1);
});
for(const [browser,url] of Object.entries({chrome:'chrome://extensions/',edge:'edge://extensions/',brave:'brave://extensions/',
  comet:'chrome://extensions/',vivaldi:'vivaldi://extensions/',opera:'opera://extensions/',arc:'chrome://extensions/'})) {
  test(`${browser} restore action uses its own extension manager`,async()=>{
    const f=await fixture(browser);f.listeners['restore:click']();assert.equal(f.opened[0].url,url);
  });
}
