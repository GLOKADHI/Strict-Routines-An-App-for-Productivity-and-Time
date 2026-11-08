(async () => {
  const managers = {chrome:'chrome://extensions/',edge:'edge://extensions/',brave:'brave://extensions/',
    comet:'chrome://extensions/',vivaldi:'vivaldi://extensions/',opera:'opera://extensions/',arc:'chrome://extensions/'};
  let timer;
  let latest;
  function render() {
    const stale = !latest || Date.now() - latest.checked_at > 60000;
    const state = stale ? 'MONITORING STATUS UNKNOWN' : latest.state || 'MONITORING STATUS UNKNOWN';
    const label=state.replaceAll('_',' ').toLowerCase();
    document.getElementById('status').textContent = stale ? 'Monitoring status unknown · reconnect to confirm points and protection.' :
      `${label[0].toUpperCase()+label.slice(1)} · ${Number.isFinite(latest.pending_penalty)?latest.pending_penalty:'Unknown'} pending · ${Number.isFinite(latest.total_penalty)?latest.total_penalty:'Unknown'} confirmed points.`;
    const checked=document.getElementById('checked');if(checked)checked.textContent=latest?.checked_at?'Last checked '+new Date(latest.checked_at).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit',hour12:true}):'No confirmed check yet.';
    const active = !stale && latest.is_interrupted && ['GRACE_PERIOD','PENALTY_ACTIVE'].includes(latest.state);
    const remaining = active ? Math.max(0,Math.ceil((latest.grace_deadline*1000-Date.now())/1000)) : 0;
    document.getElementById('countdown').textContent = active ? `Grace remaining: ${String(Math.floor(remaining/60)).padStart(2,'0')}:${String(remaining%60).padStart(2,'0')}` : '';
    document.getElementById('deadline').textContent = active ? `Penalty begins at: ${new Date(latest.grace_deadline*1000).toLocaleString()}. −1 point per complete verified minute after grace.` : '';
  }
  async function load() {
    ({latestStatus:latest} = await chrome.storage.local.get('latestStatus'));
    render();
    if (latest) document.getElementById('grace').value = String(latest.grace_duration_minutes || 5);
    if (!timer) timer=setInterval(render,1000);
  }
  await load();
  chrome.storage.onChanged.addListener((changes,area) => {if(area==='local' && changes.latestStatus) load();});
  addEventListener('pagehide',() => {if(timer) clearInterval(timer);});
  document.getElementById('restore').addEventListener('click',() => {
    const url=managers[globalThis.STRICTROUTINES_MONITOR?.browser_key];
    if(url) chrome.tabs.create({url},()=>{if(chrome.runtime?.lastError)document.getElementById('result').textContent='Could not open this browser’s extension manager.';});
  });
  document.getElementById('save').addEventListener('click', async () => {
    const status = document.getElementById('result');
    try {
      const response = await fetch('http://127.0.0.1:8765/api/browser_extensions/set_grace_period', {
        method:'POST', credentials:'omit', signal:AbortSignal.timeout(4000),
        headers:{'Content-Type':'application/json','X-StrictRoutines-Setup':'1'},
        body:JSON.stringify({minutes:Number(document.getElementById('grace').value),installation_id:globalThis.STRICTROUTINES_MONITOR?.installation_id})});
      const result = await response.json();
      status.textContent = response.ok && result.success ? 'Saved. An active episode keeps its existing grace period.' : (result.error || 'Could not save.');
    } catch (_) { status.textContent = 'Windows companion unavailable.'; }
  });
})();
