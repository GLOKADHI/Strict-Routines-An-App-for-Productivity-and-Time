const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),crypto=require('crypto').webcrypto;
const source=fs.readFileSync(process.argv[2],'utf8');
function page(supportsFullscreen=true){
 const nodes=new Map(['join','fullscreen','stop','identity','state','timer','phase','displayStatus'].map(id=>[id,{hidden:false,textContent:''}]));const events={},calls=[];let fullscreenCalls=0;
 const document={hidden:false,fullscreenElement:null,getElementById:id=>nodes.get(id),body:{classList:{add(){}}},documentElement:{},addEventListener:(name,handler)=>events[name]=handler};
 if(supportsFullscreen)document.documentElement.requestFullscreen=async()=>{fullscreenCalls++;document.fullscreenElement={};};
 const sandbox={document,navigator:{onLine:true},window:{addEventListener:(name,handler)=>events[name]=handler},location:{hash:'#test-invite',pathname:'/strict-session/companion'},history:{replaceState(){}},performance:{now:()=>1000},Date,crypto,AbortController,setTimeout,clearTimeout,setInterval(){},
 fetch:async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return{ok:true,json:async()=>url.endsWith('/join')?{success:true,preparation_id:'prep',role:'IOS',credential:'c'.repeat(64)}:{success:true,timer:{remaining_seconds:120,running:true,label:'Focus'}}};}};
 Object.defineProperty(sandbox.navigator,'mediaDevices',{get(){throw Error('No media access allowed');}});
 vm.createContext(sandbox);vm.runInContext(source,sandbox);return{nodes,events,calls,document,run:s=>vm.runInContext(s,sandbox),fullscreenCalls:()=>fullscreenCalls};
}
(async()=>{
 const p=page();await p.nodes.get('join').onclick();assert.equal(p.fullscreenCalls(),1);
 p.document.hidden=true;await p.events.visibilitychange();assert.equal(p.calls.at(-1).body.report.visible,false);
 p.document.fullscreenElement=null;p.events.fullscreenchange();await new Promise(resolve=>setImmediate(resolve));
 p.document.hidden=false;await p.events.visibilitychange();assert.equal(p.fullscreenCalls(),1); // Unlock cannot silently obtain a gesture.
 assert.equal(p.run('joined'),true);assert.equal(p.run('displayMode'),'expanded');assert(!p.nodes.get('fullscreen').hidden);
 assert.equal(p.calls.at(-1).body.report.fullscreen,false);assert.equal(p.calls.at(-1).body.report.timer_ready,true);assert.equal(p.nodes.get('timer').textContent,'02:00');
 assert.equal(p.calls.at(-1).body.report.integrity.state,'FOREGROUND');
 await p.nodes.get('fullscreen').onclick();assert.equal(p.fullscreenCalls(),2);assert(p.calls.at(-1).body.report.fullscreen);assert(p.calls.at(-1).body.report.timer_ready);
 p.document.hidden=true;await p.events.visibilitychange();p.document.hidden=false;await p.events.pageshow();assert.equal(p.fullscreenCalls(),2);assert.equal(p.run('displayMode'),'fullscreen'); // Retained native fullscreen needs no extra request.
 assert.equal(p.calls.filter(c=>c.url.endsWith('/join')).length,1);
 const fallback=page(false);await fallback.nodes.get('join').onclick();assert.equal(fallback.run('displayMode'),'expanded');fallback.document.hidden=true;await fallback.events.visibilitychange();fallback.document.hidden=false;await fallback.events.visibilitychange();assert.equal(fallback.run('displayMode'),'expanded');assert.equal(fallback.calls.at(-1).body.report.fullscreen,false);assert(fallback.calls.at(-1).body.report.timer_ready);
 console.log('Companion visibility: lock/unlock, retained fullscreen, one-tap restoration, no duplicate join, truthful fallback and zero media access passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
