/* Account-wide reward UI. The desktop proxy holds credentials; no cloud token
 * or local activation deadline is written into extension storage. */
(function(root) {
  function remaining(state, started, elapsed) {
    const active=state?.active_session?.reward || state?.active_session;
    if(!state?.success || !active || active.state!=='ACTIVE' || !Number.isFinite(state.server_now_ms) ||
       active.expiry_ms-active.activation_ms!==1200000 || state.server_now_ms<active.activation_ms ||
       elapsed<started || elapsed-started>30000)return 0;
    const verified=Math.min(active.expiry_ms-state.server_now_ms,Number.isFinite(active.remaining_ms)?active.remaining_ms:1200000);
    return Math.max(0,Math.floor((verified-(elapsed-started))/1000));
  }
  root.StrictYouTubeReward={remaining};
  if(typeof document==='undefined')return;
  const status=document.getElementById('youtubeRewardStatus');if(!status)return;
  const select=document.getElementById('youtubeRewardSelect'),claim=document.getElementById('youtubeRewardClaim');
  let current=null,started=0,busy=false;
  async function api(path,body) {
    const response=await fetch('http://127.0.0.1:8765/api/rewards/youtube/'+path,{cache:'no-store',signal:AbortSignal.timeout(12000),
      ...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    const data=await response.json();if(!response.ok || !data.success)throw Error(data.error || 'Reward unconfirmed');return data;
  }
  function draw() {
    const left=remaining(current,started,performance.now());
    const active=current?.active_session?.reward || current?.active_session;
    if(left>0)status.textContent='YOUTUBE REWARD ACTIVE — '+String(Math.floor(left/60)).padStart(2,'0')+':'+String(left%60).padStart(2,'0')+' · expires '+new Date(active.expiry_ms).toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'})+' IST'+(active.active_client_id===current.client_id?'':' · another device');
    else if(active)status.textContent='Reward expiry or synchronization requires a fresh check. Normal access rules apply.';
    claim.disabled=busy || left>0 || !select.value;
  }
  async function refresh() {
    if(busy || document.hidden)return;busy=true;
    const began=performance.now();
    try {
      current=await api('status');started=began;
      const previous=select.value;select.replaceChildren(new Option('Choose a confirmed reward',''));
      for(const r of current.available_rewards || [])select.add(new Option('20 minutes · '+r.hourly_occurrence_id,r.reward_id));
      if([...select.options].some(o=>o.value===previous))select.value=previous;
      if(!current.active_session)status.textContent=(current.available_rewards?.length || 0)+' rewards available · actual points unchanged';
    } catch(error){current=null;status.textContent='Reward unavailable · '+error.message;}
    finally {busy=false;draw();}
  }
  select.addEventListener('change',draw);
  claim.addEventListener('click',async()=>{
    if(busy || !select.value || !confirm('Activate Single YouTube Reward for exactly 20 minutes across your authorized devices? Actual points remain unchanged.'))return;
    busy=true;claim.disabled=true;
    try {await api('start',{reward_id:select.value});}catch(error){status.textContent=error.message;}
    finally {busy=false;await refresh();}
  });
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  refresh();setInterval(refresh,10000);setInterval(draw,1000);
})(globalThis);
