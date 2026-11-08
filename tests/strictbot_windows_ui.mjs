// Reproducible UI behavior checks using the shipped JavaScript and a small DOM.
// These verify client behavior; they do not replace backend or real-device tests.
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../core/ui_dashboard.py', import.meta.url), 'utf8');
const script = source.slice(source.indexOf('// StrictBot uses authoritative APIs'), source.indexOf('// 10. LOAD & RENDER STATE'));
assert.ok(script.includes('strictbotPerformRequest'));

class Node {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.parent=null; this.dataset={}; this.style={}; this.attributes={}; this.events={}; this.value=''; this.hidden=false; this.disabled=false; this.isConnected=true; this.classes=new Set(); }
  set className(value) { this.classes=new Set(value.split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classes].join(' '); }
  get classList() { return {toggle:(name,force)=>{const on=force ?? !this.classes.has(name);on?this.classes.add(name):this.classes.delete(name);return on;},contains:name=>this.classes.has(name)}; }
  set textContent(value) { this.text=String(value); this.children=[]; }
  get textContent() { return (this.text || '')+this.children.map(child=>child.textContent).join(' '); }
  append(...nodes) { for (const node of nodes) { node.parent=this;this.children.push(node); } }
  replaceChildren(...nodes) { this.children=[];this.text='';this.append(...nodes); }
  remove() { if (this.parent) this.parent.children=this.parent.children.filter(node=>node!==this); this.isConnected=false; }
  get firstElementChild() { return this.children[0] || null; }
  setAttribute(key,value) { this.attributes[key]=String(value); }
  addEventListener(name,handler) { (this.events[name] ||= []).push(handler); }
  focus() { this.focused=true; }
  reportValidity() { return !this.required || !!this.value; }
  querySelectorAll(selector) {
    const found=[];
    const matches=node=>selector.startsWith('.')?node.classes.has(selector.slice(1)):selector==='[data-strictbot-operation]'?node.dataset.strictbotOperation!=null:node.tagName===selector;
    const visit=node=>{for (const child of node.children) {if (matches(child)) found.push(child);visit(child);}};
    visit(this);return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  async click() { for (const handler of this.events.click || []) await handler({}); }
}

function fixture(config={}) {
  const ids=new Map();
  const get=id=>{if (!ids.has(id)) ids.set(id,new Node());return ids.get(id);};
  const document={createElement:tag=>new Node(tag),getElementById:get,activeElement:new Node('button')};
  const panel=get('strictbotChatPanel');
  for (const id of ['strictbotChatStatus','strictbotSendButton','strictbotMicButton','strictbotMuteButton','strictbotStopButton','strictbotVoiceStatus','strictbotChatInput','strictbotOpenButton','strictbotExpandButton','strictbotChatContext','strictbotConversation']) panel.append(get(id));
  const storage=()=>{const data=new Map();return {getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};};
  const posts=[],navigations=[],toasts=[],timers=new Map();let timerId=0;
  const window={...(config.window || {})};
  const scope=vm.createContext({window,document,localStorage:storage(),sessionStorage:storage(),navigator:{language:'en-IN'},AbortController,console,
    setTimeout:(callback,delay)=>{timers.set(++timerId,{callback,delay});return timerId;},clearTimeout:id=>timers.delete(id),
    apiGet:async url=>config.get?await config.get(url):url.includes('/history')?{success:true,mode:'USER_INSTRUCTOR',history:[]}:{success:true,mode:'USER_INSTRUCTOR',context:{timezone:'Asia/Kolkata',schedule_version:4}},
    apiPost:async (url,data)=>{posts.push({url,data:JSON.parse(JSON.stringify(data))});return config.post?await config.post(url,data,posts.length):{success:true,state:'AWAITING_ADMIN',reply:'Awaiting administrator approval.'};},
    showToast:(...args)=>toasts.push(args),loadState:()=>{},loadNextDayScheduleUI:()=>{},
    closeModal:()=>{},openWakeupModal:()=>navigations.push('wake_up'),handleHourlyDashboardAction:()=>navigations.push('hourly'),navAppPage:name=>navigations.push(name),
    SpeechSynthesisUtterance:config.window?.SpeechSynthesisUtterance
  });
  vm.runInContext(script,scope);
  const run=code=>vm.runInContext(code,scope);
  run('initStrictBotChat(); strictbotChat.open = true; strictbotChat.muted = true;');
  return {run,get,posts,navigations,toasts,timers,scope,panel};
}

test('untrusted assistant replies remain text and navigate only to real local screens',async()=>{
  const f=fixture();
  f.run("strictbotRenderResponse({success:true,reply:'<img src=x onerror=alert(1)>',actions:[{type:'navigate',target:'javascript:alert(1)',label:'Unsafe target'}]},'req-1',false)");
  const conversation=f.get('strictbotConversation');
  assert.ok(conversation.textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(conversation.querySelectorAll('img').length,0);
  await conversation.querySelector('button').click();
  assert.equal(f.navigations.length,0);
  assert.ok(f.toasts.length);
});

test('schedule confirmation is explicit and a successful request cannot be clicked again',async()=>{
  const f=fixture();
  f.run("strictbotRenderResponse({success:true,reply:'Proposed only.',actions:[{type:'confirm',action:'confirm_schedule_change',label:'Review',inputs:{proposal_id:'p1'}}]},'req-2',false)");
  const button=f.get('strictbotConversation').querySelector('button');
  await button.click();
  assert.equal(f.posts.length,0);
  let card=f.get('strictbotConversation').querySelector('.srbot-confirm');
  await card.querySelectorAll('button')[1].click();
  assert.equal(f.posts.length,0);
  await button.click();card=f.get('strictbotConversation').querySelector('.srbot-confirm');
  await card.querySelector('button').click();
  assert.equal(f.posts.length,1);
  assert.equal(f.posts[0].data.action,'confirm_schedule_change');
  assert.equal(f.posts[0].data.confirmation,true);
  assert.equal(button.disabled,true);
  assert.ok(f.get('strictbotConversation').textContent.includes('Awaiting administrator approval.'));
});

test('protected actions request confirmation even when mislabeled as a direct request',async()=>{
  const f=fixture();
  f.run("strictbotRenderResponse({success:true,reply:'Proposed.',actions:[{type:'request',action:'approve_schedule_change',label:'Approve',inputs:{proposal_id:'p2'}}]},'req-3',false)");
  await f.get('strictbotConversation').querySelector('button').click();
  assert.equal(f.posts.length,0);
  const card=f.get('strictbotConversation').querySelector('.srbot-confirm');
  card.querySelector('textarea').value='Travel emergency confirmed by administrator';
  await card.querySelector('button').click();
  assert.equal(f.posts[0].data.inputs.exception_reason,'Travel emergency confirmed by administrator');
});

test('rejection reason is required and never fabricated',async()=>{
  const f=fixture();
  f.run("strictbotRenderResponse({success:true,reply:'Review.',actions:[{type:'confirm',action:'reject_schedule_change',label:'Reject',inputs:{proposal_id:'p3'}}]},'req-4',false)");
  await f.get('strictbotConversation').querySelector('button').click();
  const card=f.get('strictbotConversation').querySelector('.srbot-confirm');
  await card.querySelector('button').click();
  assert.equal(f.posts.length,0);
  card.querySelector('textarea').value='Essential event details missing';
  await card.querySelector('button').click();
  assert.equal(f.posts[0].data.inputs.reason,'Essential event details missing');
});

test('uncertain network results retain the original request ID on retry',async()=>{
  const f=fixture({post:async(url,data,index)=>{if(index===1)throw new Error('Connection lost');return {success:true,state:'APPLIED',reply:'Service confirmed the existing operation.'};}});
  await f.run("strictbotPerformRequest({kind:'action',action:'confirm_schedule_change',inputs:{proposal_id:'p4'},request_id:'stable-id'})");
  assert.equal(f.run('strictbotChat.pendingRequest.request_id'),'stable-id');
  assert.ok(f.get('strictbotConversation').textContent.includes('No completion is confirmed.'));
  const retry=f.get('strictbotConversation').querySelectorAll('button').find(node=>node.textContent==='Retry original request');
  await retry.click();
  assert.deepEqual(f.posts.map(item=>item.data.request_id),['stable-id','stable-id']);
  assert.equal(f.run('strictbotChat.pendingRequest'),null);
});

test('context refresh is open-panel scoped, event deduplicated, and does not speak',async()=>{
  const f=fixture();
  f.run("queueStrictBotContextRefresh('event-1'); queueStrictBotContextRefresh('event-1');");
  assert.equal(f.timers.size,1);
  await [...f.timers.values()][0].callback();
  assert.ok(f.get('strictbotChatContext').textContent.includes('Schedule v4'));
  assert.equal(f.run('strictbotChat.speaking'),false);
  f.run("strictbotChat.open=false; queueStrictBotContextRefresh('event-2');");
  assert.equal(f.timers.size,1);
});

test('voice input starts only on an explicit action and denied permission preserves text',()=>{
  let instance;
  class Recognition { constructor(){instance=this;this.starts=0;} start(){this.starts++;this.onstart();} stop(){this.onend();} abort(){this.onend();} }
  const f=fixture({window:{SpeechRecognition:Recognition}});
  assert.equal(instance.continuous,false);
  assert.equal(instance.starts,0);
  f.run('toggleStrictBotListening()');
  assert.equal(instance.starts,1);
  instance.onerror({error:'not-allowed'});instance.onend();
  assert.ok(f.get('strictbotVoiceStatus').textContent.includes('Microphone permission denied'));
  assert.equal(f.get('strictbotSendButton').disabled,false);
});

test('speech speaks only a new reply and stops when interrupted',()=>{
  const spoken=[];let cancellations=0;
  class Utterance {constructor(text){this.text=text;}}
  const f=fixture({window:{SpeechSynthesisUtterance:Utterance,speechSynthesis:{speak:item=>{spoken.push(item);item.onstart();},cancel:()=>cancellations++}}});
  f.run("strictbotChat.muted=false; speakStrictBotReply('Verified service result.','speech-1');");
  for (const item of [...f.timers.values()]) if(item.delay===0)item.callback();
  assert.equal(spoken.length,1);
  assert.equal(f.run('strictbotChat.speaking'),true);
  f.run("speakStrictBotReply('Verified service result.','speech-1'); stopStrictBotVoice();");
  assert.equal(spoken.length,1);
  assert.equal(f.run('strictbotChat.speaking'),false);
  assert.ok(cancellations>=2);
});

test('backend uppercase navigation and administrator mode use the real local screens',()=>{
  const f=fixture();
  f.run("strictbotNavigate('WAKEUP'); strictbotAdoptMode('ADMINISTRATOR_PA');");
  assert.deepEqual(f.navigations,['wake_up']);
  assert.equal(f.run('strictbotChat.mode'),'Administrator PA');
});

test('history reload restores current saved proposals with explicit confirmation cards',async()=>{
  const f=fixture({get:async()=>({success:true,mode:'USER_INSTRUCTOR',conversation_id:'restored',history:[{role:'assistant',text:'Saved proposal.',status:'PENDING'}],
    proposals:[{proposal_id:'saved-p1',status:'AWAITING_ADMIN',reason:'Travel',before:{gym_time:'18:00'},after:{gym_time:'20:00'}}],
    actions:[{type:'confirm',action:'cancel_schedule_change',label:'Cancel saved proposal',inputs:{proposal_id:'saved-p1'}}]})});
  await f.run('refreshStrictBotHistory()');
  assert.ok(f.get('strictbotConversation').textContent.includes('AWAITING_ADMIN'));
  await f.get('strictbotConversation').querySelector('button').click();
  assert.equal(f.posts.length,0);
  assert.ok(f.get('strictbotConversation').querySelector('.srbot-confirm'));
});

test('session mode changes clear actor-scoped history and prevent cross-session mutation retry',()=>{
  const f=fixture();
  f.run("strictbotChat.mode='Administrator PA'; strictbotChat.conversationId='old-admin-chat'; strictbotChat.speaking=true; strictbotRememberPending({kind:'action',action:'approve_schedule_change',inputs:{proposal_id:'p9'},request_id:'old-op',clientMode:'Administrator PA'}); strictbotAdoptMode('USER_INSTRUCTOR');");
  assert.equal(f.run('strictbotChat.conversationId'),null);
  assert.equal(f.run('strictbotChat.speaking'),false);
  assert.equal(f.run('strictbotChat.pendingRequest.previousSession'),true);
  assert.ok(f.get('strictbotConversation').textContent.includes('previous session'));
  assert.equal(f.get('strictbotConversation').querySelectorAll('button').some(button=>button.textContent==='Retry original request'),false);
});
