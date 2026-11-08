const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={};this.hidden=false;this.disabled=false;}
 append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}
 replaceChild(next,previous){const index=this.children.indexOf(previous);assert(index>=0);this.children[index]=next;}
 setAttribute(k,v){this[k]=v;}addEventListener(){}
}
const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);};
const sandbox={document:{getElementById:node,createElement:tag=>new Element(tag),querySelectorAll:()=>[],addEventListener(){}},
 window:{},navigator:{},setInterval(){},Date,console,navAppPage:async()=>{},strictStudyStarting:false,
 strictSessionData:{workflow:{state:'IDLE'}},currentTab:'strict-session'};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
const run=code=>vm.runInContext(code,sandbox);
function status(){return{ready:true,devices:{WINDOWS:{joined:true,fresh:true,ready:true,status:'Ready',report:{}},
 IOS:{joined:true,fresh:true,ready:true,status:'Foreground',report:{timer_ready:true}},
 ANDROID:{joined:false,fresh:false,ready:false,status:'Disconnected',report:{}}},
 environment:{ready:true,internet:true,secure_browser:true,allowlist_configured:true,window:{window_start:'2026-11-03T10:50:00+05:30'}},
 invitations:[{role:'ANDROID',url:'old-android-invitation',qr:'old',expires_at:2000000000},{role:'IOS',url:'ios-invitation',qr:'ios',expires_at:2000000000}]};}
function render(value){sandbox.value=value;run('strictPreparationData=value;renderStrictPreparation();renderStrictInvitations()');}
const good=status();render(good);
assert.equal(node('ssDevicePanels').children.length,2);
const originalPanels=[...node('ssDevicePanels').children];
render(good);
assert.strictEqual(node('ssDevicePanels').children[0],originalPanels[0],'Unchanged Windows controls must survive polling');
assert.strictEqual(node('ssDevicePanels').children[1],originalPanels[1],'Unchanged iPad controls must survive polling');
assert.equal(originalPanels.flatMap(card=>card.children.find(child=>child.tag==='ul').children).filter(row=>row.children.some(child=>child.dataset.ssSetting)).length,21);
assert.equal(node('ssInvitations').children.length,1);
assert(!node('ssContinueReady').disabled);assert(node('ssJoinBlockers').hidden);
assert.match(node('ssJoinStatus').textContent,/WINDOWS: Ready.*IOS: Ready/);
assert(!node('ssJoinStatus').textContent.includes('ANDROID'));
const blocked=status();blocked.ready=false;blocked.environment.ready=false;blocked.environment.secure_browser=false;blocked.environment.window=null;
render(blocked);assert(node('ssContinueReady').disabled);assert(!node('ssJoinBlockers').hidden);
assert.match(node('ssContinueReady').title,/Windows study browser.*No eligible proof window/);
assert.equal(node('ssJoinBlockers').children[1].children.length,2);
const stale=status();stale.ready=false;stale.devices.IOS.ready=false;stale.devices.IOS.fresh=false;
render(stale);assert(node('ssContinueReady').disabled);assert.match(node('ssContinueReady').title,/iPhone\/iPad: waiting for a current report/);
assert(!node('ssContinueReady').title.includes('Android'));
render(good);assert(!node('ssContinueReady').disabled);assert(node('ssJoinBlockers').hidden);
const noCamera=status();noCamera.ready=false;noCamera.devices.IOS.ready=false;noCamera.devices.IOS.camera_required=true;noCamera.devices.IOS.camera_ready=false;
render(noCamera);assert(node('ssContinueReady').disabled);assert(node('btnStartStrictSession').disabled);
assert.match(node('ssContinueReady').title,/Start Camera.*live video in Windows/);
console.log('Windows+iOS UI: two panels, iOS-only invitations, ignored legacy Android, accurate blockers, stale iOS gate and recovery passed');
