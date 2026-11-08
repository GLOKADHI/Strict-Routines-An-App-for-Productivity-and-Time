const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
const nodes=new Map(['join','fullscreen','stop','identity','state','timer','phase','displayStatus'].map(id=>[id,{hidden:false,disabled:false,textContent:''}]));
const events={},calls=[],order=[];let now=1000,timer={state:'WAITING',remaining_seconds:null,running:false,label:'Waiting for Windows'};
const document={hidden:false,fullscreenElement:null,getElementById:id=>nodes.get(id),body:{classList:{add(){}}},documentElement:{requestFullscreen:async()=>{order.push('fullscreen');document.fullscreenElement={};}},addEventListener:(key,fn)=>events[key]=fn};
const navigator={onLine:true};Object.defineProperty(navigator,'mediaDevices',{get(){throw Error('Timer companion must never request media APIs');}});
const sandbox={document,navigator,window:{isSecureContext:false,addEventListener(){}},location:{hash:'#fake-invite',pathname:'/strict-session/companion'},history:{replaceState(){}},performance:{now:()=>now},setInterval(){},console,
fetch:async(url,options)=>{const body=JSON.parse(options.body);calls.push({url,body});order.push(url.endsWith('/join')?'join':'heartbeat');return{ok:true,json:async()=>url.endsWith('/join')?{success:true,preparation_id:'prep',role:'IOS',credential:'scoped-test'}:{success:true,timer}};}};
Object.assign(sandbox,{crypto:require('crypto').webcrypto,AbortController,setTimeout,clearTimeout});
vm.createContext(sandbox);vm.runInContext(source,sandbox);const run=s=>vm.runInContext(s,sandbox);
(async()=>{
 assert.equal(calls.length,0);await run('drawTimer()');assert.equal(nodes.get('timer').textContent,'--:--');
 await nodes.get('join').onclick();assert(order.indexOf('fullscreen')<order.indexOf('join'));
 assert.equal(run('joined'),true);assert.equal(run('displayMode'),'fullscreen');assert.equal(nodes.get('timer').textContent,'--:--');
 const report=calls.at(-1).body.report;assert(report.timer_ready&&report.fullscreen);assert(!('camera' in report)&&!('microphone' in report)&&!('secure_context' in report));
 assert(calls.every(c=>c.url.endsWith('/join')||c.url.endsWith('/heartbeat')));
 timer={state:'ACTIVE_FOCUS',remaining_seconds:120,running:true,label:'Active Focus'};await run('heartbeat()');assert.equal(nodes.get('timer').textContent,'02:00');
 now+=3000;run('drawTimer()');assert.equal(nodes.get('timer').textContent,'01:57');
 now+=20000;run('drawTimer()');assert.equal(nodes.get('timer').textContent,'01:50');assert.match(nodes.get('state').textContent,/Connection lost/);
 now+=20000;run('drawTimer()');assert.equal(nodes.get('timer').textContent,'01:50');
 timer={state:'ACTIVE_FOCUS',remaining_seconds:117,running:false,label:'Active Focus'};await run('heartbeat()');now+=4000;run('drawTimer()');assert.equal(nodes.get('timer').textContent,'01:57');
 document.fullscreenElement=null;await events.fullscreenchange();await new Promise(resolve=>setImmediate(resolve));assert.equal(calls.at(-1).body.report.timer_ready,true);
 delete document.documentElement.requestFullscreen;await nodes.get('fullscreen').onclick();assert.equal(run('displayMode'),'resume_required');assert.equal(calls.at(-1).body.report.fullscreen,false);assert.equal(calls.at(-1).body.report.timer_ready,true);assert.match(nodes.get('displayStatus').textContent,/Resume fullscreen/);
 document.hidden=true;await events.visibilitychange();await new Promise(resolve=>setImmediate(resolve));assert.equal(calls.at(-1).body.report.visible,false);
 await nodes.get('stop').onclick();assert.equal(run('joined'),false);assert.equal(calls.at(-1).body.report.timer_ready,false);
 console.log('Timer companion UI: HTTP, zero media access, user-gesture fullscreen, fallback, sync, pause, stale connection and disconnect checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
