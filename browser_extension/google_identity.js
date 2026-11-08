/* Google tokens stay inside the service worker / Chrome Identity token cache. */
(() => {
  const scopes=['openid','email','profile'], driveScope='https://www.googleapis.com/auth/drive.file';
  let profile=null, drive=null, lastToken=null;
  const configured=()=>Boolean(chrome.runtime.getManifest().oauth2?.client_id);
  async function token(extra=[],interactive=false) {
    if(!configured())throw Error('Configure a Chrome-extension OAuth client ID for this installed extension ID.');
    if(typeof chrome.identity?.getAuthToken!=='function')throw Error('This browser does not provide Google Chrome Identity. Independent Google sign-in is unavailable here.');
    const result=await chrome.identity.getAuthToken({interactive,scopes:[...scopes,...extra],enableGranularPermissions:true});
    const value=typeof result==='string'?result:result?.token;
    if(!value)throw Error('Google did not return an authorized token.');
    lastToken=value;return value;
  }
  async function google(url,value,options={}) {
    const response=await fetch(url,{...options,redirect:'error',headers:{...(options.headers||{}),Authorization:'Bearer '+value},signal:AbortSignal.timeout(20000)});
    if(!response.ok)throw Error(`Google request failed (${response.status}); reconnect or check permission.`);
    return response;
  }
  async function identify(value) {
    const result=await (await google('https://openidconnect.googleapis.com/v1/userinfo',value)).json();
    if(!result.sub||!result.email)throw Error('Google identity could not be verified.');
    if(profile?.sub!==result.sub)drive=null;
    profile={sub:result.sub,email:result.email,name:result.name};
  }
  async function verifyFolder(value,id) {
    if(!/^[A-Za-z0-9_-]{5,200}$/.test(id||''))throw Error('Select the existing shared StrictRecords folder ID.');
    const base='https://www.googleapis.com/drive/v3/files/';
    const folder=await (await google(base+id+'?fields=id,name,mimeType,trashed,owners(emailAddress)',value)).json();
    if(folder.name!=='StrictRecords'||folder.trashed||folder.mimeType!=='application/vnd.google-apps.folder'||!folder.owners?.some(x=>x.emailAddress?.toLowerCase()===profile.email.toLowerCase()))throw Error('The selected folder does not belong to this Google account.');
    const nonce=crypto.randomUUID(),boundary='strict_'+crypto.randomUUID().replaceAll('-','');let marker;
    try {
      const metadata={name:'strictrecords_verify_'+nonce+'.json',parents:[id],mimeType:'application/json'};
      const body=`--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify(metadata)}\r\n--${boundary}\r\nContent-Type: application/json\r\n\r\n${JSON.stringify({nonce})}\r\n--${boundary}--`;
      marker=await (await google('https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id',value,{method:'POST',headers:{'Content-Type':'multipart/related; boundary='+boundary},body})).json();
      if(!marker.id)throw Error('Drive did not confirm the verification upload.');
      const read=await (await google(base+encodeURIComponent(marker.id)+'?alt=media',value)).json();
      if(read.nonce!==nonce)throw Error('Drive verification content did not match.');
      drive={root_folder_id:id,verified_at:Date.now(),sub:profile.sub};
    } finally {
      if(marker?.id)await google(base+encodeURIComponent(marker.id),value,{method:'DELETE'});
    }
  }
  async function action(name,body={}) {
    if(name==='status') {
      if(!profile&&configured()) {
        try {await identify(await token([],false));} catch (_) {}
      }
      return status();
    }
    if(name==='sign-out') {
      if(lastToken) {
        await chrome.identity.removeCachedAuthToken({token:lastToken});
        await fetch('https://oauth2.googleapis.com/revoke',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({token:lastToken}),redirect:'error'});
      }
      if(chrome.identity?.clearAllCachedAuthTokens)await chrome.identity.clearAllCachedAuthTokens();
      profile=null;drive=null;lastToken=null;return status();
    }
    const value=await token(name==='grant-drive'||name==='select-folder'?[driveScope]:[],true);
    await identify(value);
    if(name==='grant-drive'||name==='select-folder') {
      let folder=body.folder_id;
      if(!folder) {
        const shared=await StrictClientBridge.request('/api/client/account');
        if(shared.user?.sub!==profile.sub)throw Error('Select the same Google account as the paired Windows client.');
        folder=shared.root_folder_id;
      }
      await verifyFolder(value,folder);
    }
    return status();
  }
  function status() {
    return {success:true,configured:configured(),authenticated:Boolean(profile),user:profile||{},
      google_state:profile?'Connected':'Not connected',drive_state:drive&&drive.sub===profile?.sub?'Connected · read/write verified':'Drive authorization / folder verification required',
      root_folder_id:drive?.root_folder_id||null,drive_verified_at:drive?.verified_at||null,
      sync_state:'Shared backend handles authoritative synchronization',
      message:configured()?'Google access is scoped to this extension.':'OAuth setup required; no client ID has been supplied for this extension.'};
  }
  chrome.identity?.onSignInChanged?.addListener(()=>{profile=null;drive=null;lastToken=null;});
  globalThis.StrictClientGoogle=Object.freeze({action,status});
})();
