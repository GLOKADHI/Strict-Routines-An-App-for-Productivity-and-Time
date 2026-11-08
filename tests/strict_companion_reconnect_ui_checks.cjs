const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),crypto=require('crypto').webcrypto;
const source=fs.readFileSync(process.argv[2],'utf8');
function storage(){const data=new Map();return{getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};}
const local=storage(),session=storage();let lost=true,claims=[],status=200;
function page(hash=''){
 const nodes=new Map(['join','fullscreen','stop','identity','state','timer','phase','displayStatus'].map(id=>[id,{hidden:false,textContent:''}]));const calls=[];
 const doc={hidden:false,fullscreenElement:null,getElementById:id=>nodes.get(id),body:{classList:{add(){}}},documentElement:{requestFullscreen:async()=>doc.fullscreenElement={}},addEventListener(){}};
 const sandbox={document:doc,navigator:{onLine:true,platform:'MacIntel',maxTouchPoints:5},window:{addEventListener(){}},location:{hash,pathname:'/strict-session/companion'},history:{replaceState(){}},performance:{now:()=>1000},Date,crypto,AbortController,setTimeout,clearTimeout,setInterval(){},localStorage:local,sessionStorage:session,
 fetch:async(url,options)=>{const body=JSON.parse(options.body);calls.push({url,body});if(url.endsWith('/join')){claims.push(body);if(lost){lost=false;throw Object.assign(Error('lost response'),{name:'AbortError'});}return{ok:status===200,status,json:async()=>status===200?{success:true,preparation_id:'prep-reconnect',role:'IOS',credential:'c'.repeat(64)}:{success:false,error:'Invitation expired or already used'}};}return{ok:status===200,status,json:async()=>status===200?{success:true,timer:{state:'WAITING',remaining_seconds:2100,running:false,label:'Ready — start the session on Windows'}}:{success:false,error:'Invalid credential'}};}};
 Object.defineProperty(sandbox.navigator,'mediaDevices',{get(){throw Error('No media access allowed');}});
 vm.createContext(sandbox);vm.runInContext(source,sandbox);return{nodes,calls,run:s=>vm.runInContext(s,sandbox)};
}
(async()=>{
 const first=page('#test-invitation');await first.nodes.get('join').onclick();assert.match(first.nodes.get('state').textContent,/timed out/);assert.equal(first.run('binding'),null);
 const retry=page();await retry.run('restorePromise');await retry.nodes.get('join').onclick();
 assert.equal(claims.length,2);assert.equal(claims[0].token,claims[1].token);assert.equal(claims[0].client_nonce,claims[1].client_nonce);assert.equal(claims[0].client_nonce.length,64);
 assert.equal(claims[1].client_info.platform,'MacIntel');assert.equal(claims[1].client_info.max_touch_points,5);
 assert.equal(retry.run('connected'),true);assert.equal(retry.nodes.get('timer').textContent,'35:00');assert.equal(session.getItem('strictrecords.timer.pending.v2'),null);
 const restored=page();await restored.run('restorePromise');assert(restored.calls.every(c=>c.url.endsWith('/heartbeat')));assert.equal(restored.run('connected'),true);assert.equal(restored.run('joined'),true);assert(restored.nodes.get('join').hidden);assert(!restored.nodes.get('fullscreen').hidden);
 await restored.nodes.get('fullscreen').onclick();assert.equal(claims.length,2);assert.equal(restored.run('joined'),true);assert.equal(restored.nodes.get('timer').textContent,'35:00');
 status=403;const expired=page();await expired.run('restorePromise');assert.equal(expired.run('binding'),null);assert.equal(local.getItem('strictrecords.timer.binding.v2'),null);assert.match(expired.nodes.get('state').textContent,/fresh.*QR/);assert(!expired.nodes.get('join').hidden);
 console.log('Companion reconnect UI: lost-response retry, nonce reuse across reload, saved credential resume, planned timer and expired credential recovery passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
