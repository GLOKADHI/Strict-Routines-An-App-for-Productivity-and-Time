const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../browser_extension/extension_popup.js'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){
  const ids={},timers=new Map(),events={};let runtimeError=null,reads=0,stall=false;
  let result={success:true,connection:{success:true},monitoring:{configured:false},version:'1.7.0',browser:'comet',checkedAt:Date.now(),
    rows:[{name:'Spotify',restricted:false,currentScore:60,requiredScore:60,reason:'Music score requirement met.'}]};
  const document={hidden:false,activeElement:null,getElementById:id=>ids[id]??=(new Element()),
    createElement:tag=>new Element(tag),addEventListener:(name,fn)=>events[name]=fn};
  class Element{
    constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.dataset={};this.className='';this.hidden=false;this.disabled=false;this.textContent='';}
    append(...nodes){nodes.forEach(n=>{n.parent=this;this.children.push(n);});}
    replaceChildren(...nodes){this.children=[];this.append(...nodes);}
    addEventListener(name,fn){this.listeners[name]=fn;}
    setAttribute(name,value){this[name]=value;}
    focus(){document.activeElement=this;}
    closest(selector){return this.tag==='details'&&selector==='details.site'?this:this.parent?.closest(selector);}
    querySelectorAll(selector){const all=this.children.flatMap(n=>[n,...n.querySelectorAll('*')]);return all.filter(n=>selector==='*'||
      selector==='details[open]'&&n.tag==='details'&&n.open||selector==='.badge'&&n.className.split(' ').includes('badge')||
      selector==='.site-name'&&n.className==='site-name'||selector==='summary'&&n.tag==='summary');}
    querySelector(selector){return this.querySelectorAll(selector)[0];}
  }
  const tabs=['status','planning','settings'].map(view=>{const n=new Element('button');n.dataset.view=view;return n;});
  document.querySelectorAll=selector=>selector==='[data-view]'?tabs:[];
  const runtime={getURL:path=>'extension://'+path,get lastError(){reads++;return runtimeError;},sendMessage(_,callback){if(!stall)callback(result);}};
  vm.runInNewContext(source,{document,window:{confirm:()=>false,addEventListener:(name,fn)=>events[name]=fn},chrome:{runtime,tabs:{create(){}}},
    AbortSignal,Date,Error,Promise,Number,Set,CustomEvent:class{},console,fetch:async()=>({ok:true,json:async()=>({success:true,installation_mode:'STANDARD'})}),
    setTimeout:(fn,delay)=>{const id=timers.size+1;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),setInterval:()=>1,clearInterval(){}});
  return {ids,document,tabs,timers,get reads(){return reads;},error:v=>runtimeError=v,stall:v=>stall=v,result:v=>result=v};
}
test('popup shows confirmed per-site decisions and actual installed version',async()=>{
  const f=fixture();await settle();assert.equal(f.ids.confirmedScore.textContent,'60');assert.equal(f.ids.extensionVersion.textContent,'v1.7.0');
  assert.equal(f.ids.siteList.querySelector('.badge').textContent,'Allowed');assert.equal(f.ids.btnConnect.disabled,false);
});
test('popup consumes closed-port lastError and clears stale allowed presentation',async()=>{
  const f=fixture();await settle();f.error({message:'The message port closed before a response was received.'});
  f.ids.btnConnect.listeners.click();await settle();assert.ok(f.reads>=2);assert.match(f.ids.dashboardError.textContent,/interrupted/);
  assert.equal(f.ids.siteList.querySelector('.badge').textContent,'Unconfirmed');assert.equal(f.ids.btnConnect.disabled,false);
});
test('missing popup response reaches deadline and permits retry',async()=>{
  const f=fixture();await settle();f.stall(true);f.ids.btnConnect.listeners.click();
  [...f.timers.values()].find(t=>t.delay===15000).fn();await settle();
  assert.match(f.ids.dashboardError.textContent,/did not respond/);assert.equal(f.ids.btnConnect.disabled,false);
});
test('automatic refresh preserves expanded site and keyboard focus',async()=>{
  const f=fixture();await settle();const row=f.ids.siteList.children[0];row.open=true;row.querySelector('summary').focus();
  f.ids.btnConnect.listeners.click();await settle();assert.equal(f.ids.siteList.children[0].open,true);
  assert.equal(f.document.activeElement,f.ids.siteList.children[0].querySelector('summary'));
});
