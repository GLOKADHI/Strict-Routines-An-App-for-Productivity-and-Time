/* App credentials belong only to trusted extension contexts, never page scripts. */
(() => {
  for (const area of [chrome.storage.local, chrome.storage.session]) {
    if (typeof area?.setAccessLevel === 'function') Promise.resolve(area.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'})).catch(()=>{});
  }
  function origin(value) {
    const u=new URL(value);
    if(u.username||u.password||u.search||u.hash||!['','/'].includes(u.pathname))throw Error('Use a service origin without credentials or a path.');
    const p=u.hostname.split('.').map(Number), privateIp=p.length===4&&p.every(x=>Number.isInteger(x)&&x>=0&&x<=255)&&
      (p[0]===10||p[0]===127||(p[0]===192&&p[1]===168)||(p[0]===172&&p[1]>=16&&p[1]<=31)||(p[0]===100&&p[1]>=64&&p[1]<=127));
    if(u.protocol!=='https:'&&!(u.protocol==='http:'&&(privateIp||u.hostname==='localhost'||u.hostname.endsWith('.ts.net'))))throw Error('Use HTTPS or your private paired PC address.');
    return u.origin;
  }
  const allowed = new Set(['/api/client/state','/api/client/calendar','/api/client/routine','/api/client/account',
    '/api/client/demo','/api/taufa/evaluate_unified','/api/taufa/status','/api/gym_mode/status','/api/gym_mode/activate']);
  async function request(path,body) {
    if(!allowed.has(new URL(path,'http://client').pathname)||!path.startsWith('/api/')||path.startsWith('//'))throw Error('Unsupported client endpoint.');
    const saved=await chrome.storage.local.get('strictClientBinding'), binding=saved.strictClientBinding;
    if(!binding?.device_token)throw Object.assign(Error('Pair this browser profile with the PC first.'),{code:'PAIRING_REQUIRED'});
    const response=await fetch(origin(binding.origin)+path,{method:body===undefined?'GET':'POST',cache:'no-store',credentials:'omit',redirect:'error',
      signal:AbortSignal.timeout(path.includes('evaluate')?90000:15000),headers:{'Content-Type':'application/json','X-Device-Id':binding.device_id,'X-Device-Token':binding.device_token},
      ...(body===undefined?{}:{body:JSON.stringify(body)})});
    const data=await response.json();
    if(!response.ok)throw Object.assign(Error(data.error||data.message||`Request failed (${response.status})`),{status:response.status,code:data.code,data});
    return data;
  }
  async function pair(value,pin) {
    const root=origin(value), old=await chrome.storage.local.get('strictClientDeviceId');
    const device=old.strictClientDeviceId||'browser_'+crypto.randomUUID();
    const response=await fetch(root+'/api/device/pairing/handshake',{method:'POST',redirect:'error',credentials:'omit',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({manual_pin:String(pin),device_id:device,device_name:'StrictRoutines browser profile',device_type:'BROWSER',platform:'BROWSER',app_version:chrome.runtime.getManifest().version}),signal:AbortSignal.timeout(15000)});
    const data=await response.json();
    if(!response.ok||!data.success||!data.device_token||!data.account_id)throw Error(data.error||data.message||'Pairing was not confirmed.');
    await chrome.storage.local.set({strictClientDeviceId:device,strictClientBinding:{origin:root,device_id:device,device_token:data.device_token,account_id:data.account_id}});
    return {success:true,account_id:data.account_id,origin:root};
  }
  globalThis.StrictClientBridge=Object.freeze({request,pair});
})();
