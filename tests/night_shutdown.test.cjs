const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{randomUUID}=require('node:crypto');
const source=fs.readFileSync('browser_extension/night_shutdown.js','utf8');
const settle=async()=>{for(let i=0;i<35;i++)await new Promise(r=>setImmediate(r));};
function fixture(shared={}){
  const state=shared.state||{},backups=shared.backups||{},events={},writes=shared.writes||[];
  let clock=1793550000000,offline=false,tabs=[{id:7,url:'https://www.youtube.com/watch?v=aqz-KE-bpKQ',active:true,windowId:1}],snapshots={7:{video_id:'aqz-KE-bpKQ',position:1122.123,duration:2536.2,state:'PAUSED',speed:1,title:'Fixture',channel:'Fixture channel',document_id:'doc7'}};
  const run={run_id:'contract',monitor_ms:clock-60000,capture_ms:clock+1000,shutdown_ms:clock+61000,controlled:1};
  class Clock extends Date{constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}}
  const event=name=>({addListener(fn){(events[name]??=[]).push(fn);}});
  const chrome={storage:{local:{async get(key){return {[key]:structuredClone(state[key])};},async set(values){for(const [key,value]of Object.entries(values)){state[key]=structuredClone(value);writes.push(['local',key]);}}}},
    runtime:{getURL:p=>'chrome-extension://id/'+p,onMessage:event('message'),onStartup:event('startup'),onInstalled:event('installed'),
      sendNativeMessage(host,msg,callback){queueMicrotask(()=>{
        if(offline){chrome.runtime.lastError={message:'offline'};callback();delete chrome.runtime.lastError;return;}
        if(msg.action==='night_context')callback({success:true,run,timezone:'Asia/Kolkata',schedule:{enabled:false}});
        if(msg.action==='night_checkpoint')callback({success:true});
        if(msg.action==='night_save'){assert.ok(state.nightShutdownRecords[msg.record.session_id]);backups[msg.record.session_id]=structuredClone(msg.record);writes.push(['native',msg.record.session_id]);callback({success:true,session_id:msg.record.session_id,backup_status:'NATIVE_BACKUP_SAVED',record:backups[msg.record.session_id]});}
        if(msg.action==='night_history')callback({success:true,records:Object.values(backups)});
        if(msg.action==='night_commit'){assert.ok(msg.session_ids.every(id=>backups[id]));writes.push(['commit',msg.run_id]);callback({success:true});}
      });}},
    alarms:{onAlarm:event('alarm'),async get(){return null;},async create(){}},
    tabs:{onActivated:event('activate'),onUpdated:event('update'),async query(){return tabs;},async get(id){return tabs.find(t=>t.id===id);},async sendMessage(id,msg){if(msg.type!=='NIGHT_CAPTURE_NOW')return {};const playback=snapshots[id];if(!playback)throw Error('inaccessible');return {success:true,playback,document_id:playback.document_id};}},
    windows:{async get(){return {focused:true};}},webNavigation:{async getFrame({tabId}){return {documentId:snapshots[tabId]?.document_id};}}};
  const context=vm.createContext({chrome,Date:Clock,URL,Map,Set,Promise,Intl,crypto:{randomUUID},setTimeout,clearTimeout,detectBrowserKey:async()=>'chrome',isCompanionPage:s=>s.url==='popup'});
  vm.runInContext(source,context);
  const request=(message,sender={url:'popup'})=>new Promise(resolve=>events.message[0](message,sender,resolve));
  return {state,backups,writes,events,context,run,request,set offline(v){offline=v;},set tabs(v){tabs=v;},set snapshots(v){snapshots=v;},async capture(){clock=run.capture_ms+25;events.alarm[0]({name:'strict-night-capture'});await settle();},async checkpoint(value=snapshots[7]){return request({type:'NIGHT_CHECKPOINT',playback:value},{frameId:0,tab:tabs[0],url:tabs[0].url,documentId:value.document_id});}};
}
test('paused actual media values are durable locally before native backup and commit',async()=>{
  const f=fixture();await settle();await f.capture();const r=Object.values(f.state.nightShutdownRecords)[0];
  assert.equal(r.playback_position,'00:18:42');assert.equal(r.playback_position_seconds,1122.123);assert.equal(r.total_duration,'00:42:16');assert.equal(r.playback_state,'PAUSED');assert.equal(r.capture_status,'CAPTURED');assert.equal(r.backup_status,'NATIVE_BACKUP_SAVED');
  assert.ok(f.writes.findIndex(x=>x[0]==='local'&&x[1]==='nightShutdownRecords')<f.writes.findIndex(x=>x[0]==='native'));assert.equal(f.writes.at(-1)[0],'commit');
});
test('worker restart reloads committed history without duplicate captures',async()=>{
  const f=fixture();await settle();await f.capture();const g=fixture(f);await settle();await g.capture();const result=await g.request({type:'NIGHT_HISTORY'});assert.equal(result.records.length,1);
});
test('native downtime preserves local capture and retry reconciles the backup',async()=>{
  const f=fixture();await settle();f.offline=true;await f.capture();assert.equal(Object.values(f.state.nightShutdownRecords)[0].backup_status,'NATIVE_BACKUP_PENDING');assert.equal(Object.keys(f.backups).length,0);
  f.offline=false;const result=await f.request({type:'NIGHT_HISTORY'});assert.equal(result.records[0].backup_status,'NATIVE_BACKUP_SAVED');assert.equal(Object.keys(f.backups).length,1);
});
test('closed tab uses honest LAST_OBSERVED checkpoint',async()=>{
  const f=fixture();await settle();await f.checkpoint();f.tabs=[];await f.capture();assert.equal(Object.values(f.state.nightShutdownRecords)[0].capture_status,'LAST_OBSERVED');
});
test('SPA changes replace the old video identity without labelling it fresh',async()=>{
  const f=fixture();await settle();await f.checkpoint();f.tabs=[{id:7,url:'https://www.youtube.com/watch?v=jNQXAC9IVRw',active:true,windowId:1}];f.snapshots={7:{video_id:'jNQXAC9IVRw',position:3.77,duration:19,state:'PLAYING',speed:1,title:'Other fixture',document_id:'doc7'}};await f.capture();
  const records=Object.values(f.state.nightShutdownRecords);assert.equal(records.length,1);assert.equal(records[0].video_id,'jNQXAC9IVRw');assert.equal(records[0].playback_position_seconds,3.77);
});
test('multiple playing tabs are committed together and foreground is primary',async()=>{
  const f=fixture();await settle();f.tabs=[{id:7,url:'https://youtu.be/aqz-KE-bpKQ',active:true,windowId:1},{id:8,url:'https://www.youtube.com/watch?v=jNQXAC9IVRw',active:false,windowId:1}];f.snapshots={7:{video_id:'aqz-KE-bpKQ',position:7,duration:30,state:'PAUSED',speed:1,document_id:'doc7'},8:{video_id:'jNQXAC9IVRw',position:12,duration:19,state:'PLAYING',speed:1,document_id:'doc8'}};await f.capture();
  const records=Object.values(f.state.nightShutdownRecords);assert.equal(records.length,2);assert.equal(records.find(r=>r.primary).tab_id,7);assert.equal(f.writes.at(-1)[0],'commit');
});
test('no watch video and inaccessible watch player produce different explicit records',async()=>{
  const f=fixture();await settle();f.tabs=[];await f.capture();assert.equal(Object.values(f.state.nightShutdownRecords)[0].capture_status,'NO_ACTIVE_YOUTUBE_VIDEO');
  const g=fixture();await settle();g.snapshots={};await g.capture();assert.equal(Object.values(g.state.nightShutdownRecords)[0].capture_status,'CAPTURE_FAILED');
});
