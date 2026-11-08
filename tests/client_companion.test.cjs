const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..','browser_extension');
function fixture({oauth=false}={}){
 const local={},calls=[],levels=[];
 const area={get:async key=>({[key]:local[key]}),set:async data=>Object.assign(local,data),setAccessLevel:async data=>levels.push(data.accessLevel)};
 const chrome={storage:{local:area,session:{setAccessLevel:area.setAccessLevel}},runtime:{getManifest:()=>({version:'1.8.0',...(oauth?{oauth2:{client_id:'fixture.apps.googleusercontent.com'}}:{})})},identity:{
  getAuthToken:async()=>({token:'private-fixture-token'}),removeCachedAuthToken:async()=>{},clearAllCachedAuthTokens:async()=>{},onSignInChanged:{addListener:()=>{}}}};
 const sandbox={chrome,URL,URLSearchParams,AbortSignal,crypto:require('node:crypto').webcrypto,console,fetch:async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>url.includes('userinfo')?{sub:'fixture-sub',email:'fixture@example.test',name:'Fixture'}:{success:true}};}};
 vm.createContext(sandbox);
 for(const name of ['client_bridge.js','google_identity.js'])vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),sandbox);
 return {sandbox,local,calls,levels};
}
test('private storage areas are restricted to trusted extension contexts',()=>{
 assert.deepEqual(fixture().levels,['TRUSTED_CONTEXTS','TRUSTED_CONTEXTS']);
});
test('unconfigured OAuth reports setup required without requesting tokens',async()=>{
 const {sandbox,calls}=fixture();const status=await sandbox.StrictClientGoogle.action('status');
 assert.equal(status.configured,false);assert.equal(status.authenticated,false);assert.equal(calls.length,0);
 await assert.rejects(()=>sandbox.StrictClientGoogle.action('sign-in'),/OAuth client ID/);
});
test('authenticated Google status contains no access or refresh tokens',async()=>{
 const {sandbox,calls}=fixture({oauth:true});const result=await sandbox.StrictClientGoogle.action('sign-in');
 assert.equal(result.authenticated,true);assert.equal(result.user.sub,'fixture-sub');
 assert.equal(JSON.stringify(result).includes('private-fixture-token'),false);
 assert.match(result.drive_state,/required/);assert.equal(calls[0].options.headers.Authorization,'Bearer private-fixture-token');
});
test('shared API cannot be used before device pairing',async()=>{
 const {sandbox,calls}=fixture();await assert.rejects(()=>sandbox.StrictClientBridge.request('/api/client/state'),/Pair this browser/);assert.equal(calls.length,0);
});
test('arbitrary API paths are not exposed by the extension bridge',async()=>{
 const {sandbox,local,calls}=fixture();local.strictClientBinding={origin:'http://127.0.0.1:8765',device_id:'fixture',device_token:'private-device-token'};
 await assert.rejects(()=>sandbox.StrictClientBridge.request('/api/admin/reset',{}),/Unsupported/);
 await assert.rejects(()=>sandbox.StrictClientBridge.request('//evil.example/api/client/state'),/Unsupported/);assert.equal(calls.length,0);
});
test('public plaintext and crafted private-host names are rejected',async()=>{
 const {sandbox,calls}=fixture();
 for(const origin of ['http://evil.example:8765','http://100.fake.93.80.125:8765','http://user:secret@127.0.0.1:8765'])await assert.rejects(()=>sandbox.StrictClientBridge.pair(origin,'123456'));
 assert.equal(calls.length,0);
});
test('authorized requests retain device credentials in headers and never follow redirects',async()=>{
 const {sandbox,local,calls}=fixture();local.strictClientBinding={origin:'http://100.93.80.125:8765',device_id:'fixture',device_token:'private-device-token'};
 const result=await sandbox.StrictClientBridge.request('/api/client/state');assert.equal(result.success,true);
 assert.equal(calls[0].options.headers['X-Device-Token'],'private-device-token');assert.equal(calls[0].options.redirect,'error');assert.equal(calls[0].url.includes('private-device-token'),false);
});
