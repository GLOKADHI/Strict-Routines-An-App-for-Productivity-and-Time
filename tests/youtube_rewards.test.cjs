const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={};vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../browser_extension/youtube_rewards.js'),'utf8'),context);
const remaining=context.StrictYouTubeReward.remaining;
const state={success:true,server_now_ms:1000,active_session:{state:'ACTIVE',activation_ms:1000,expiry_ms:1201000}};
test('extension displays the same fixed server deadline and cannot restart it',()=>{
  assert.equal(remaining(state,10,1010),1199);
  assert.equal(remaining({...state,server_now_ms:1200000},10,1010),0);
});
test('offline, corrupt, stale and rollback receipts do not invent access',()=>{
  assert.equal(remaining(null,0,0),0);
  assert.equal(remaining(state,10,9),0);
  assert.equal(remaining(state,10,30011),0);
  assert.equal(remaining({...state,active_session:{...state.active_session,expiry_ms:1300000}},0,0),0);
});
test('a refreshed display never extends the verified remaining playback lease',()=>{
  const fixed={...state,active_session:{...state.active_session,remaining_ms:1000}};
  assert.equal(remaining(fixed,0,0),1);
  assert.equal(remaining(fixed,0,1000),0);
});
