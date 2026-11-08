/* Persistent history UI: values are always rendered as text, never HTML. */
(() => {
  const get=id=>document.getElementById(id),list=get('nightHistoryList');if(!list)return;
  const send=(type,extra={})=>chrome.runtime.sendMessage({type,...extra});
  const text=(tag,value,className='')=>{const el=document.createElement(tag);el.textContent=value;el.className=className;return el;};
  const label=value=>String(value||'Unknown').replaceAll('_',' ').toLowerCase().replace(/^./,c=>c.toUpperCase());
  function resume(record){
    if(!/^[A-Za-z0-9_-]{11}$/.test(record.video_id||'')||!Number.isFinite(record.playback_position_seconds))return null;
    return `https://www.youtube.com/watch?v=${record.video_id}&t=${Math.floor(record.playback_position_seconds)}s`;
  }
  function card(record){
    const el=document.createElement('article');el.className='card-box night-record';
    el.append(text('p',record.capture_date+(record.controlled_live_run?' · Controlled live validation':''),'eyebrow'));
    el.append(text('h2',record.video_title||label(record.capture_status)));
    if(record.channel)el.append(text('p',record.channel,'card-detail'));
    if(record.video_id){
      el.append(text('p',`${record.playback_position||'Unknown'} / ${record.total_duration||'Unknown duration'} · ${label(record.playback_state)}`,'night-timeline'));
      el.append(text('p',record.canonical_url,'night-url'));
    }
    const details=document.createElement('dl');details.className='night-states';
    for(const [key,value] of [['Captured',record.capture_timestamp],['Capture',label(record.capture_status)],['Local storage',label(record.extension_storage_status||record.storage_status)],['Windows backup',label(record.backup_status)],['App closure',label(record.browser_close_status)],['Shutdown',label(record.shutdown_status)]])details.append(text('dt',key),text('dd',value||'Unknown'));
    el.append(details);
    if(record.capture_status==='LAST_OBSERVED')el.append(text('p',`Last observed: ${record.observed_timestamp}. The player was unavailable at capture.`,'note'));
    if(record.capture_delay_ms>1000)el.append(text('p',`Captured ${(record.capture_delay_ms/1000).toFixed(1)} seconds after the scheduled deadline.`,'note'));
    const actions=document.createElement('div');actions.className='night-actions';
    const url=resume(record);
    if(url){const open=text('button','Open video','primary');open.onclick=()=>chrome.tabs.create({url});
      const copy=text('button','Copy URL');copy.onclick=async()=>{try{await navigator.clipboard.writeText(record.canonical_url);get('nightStatus').textContent='Canonical URL copied.';}catch(_){get('nightStatus').textContent='Copy failed. Select the URL from View details.';}};actions.append(open,copy);}
    const view=document.createElement('details');view.className='advanced';view.append(text('summary','View details'),text('pre',JSON.stringify(record,null,2)));el.append(actions,view);return el;
  }
  async function refresh(){
    get('nightStatus').textContent='Loading saved sessions…';
    try{const result=await send('NIGHT_HISTORY');if(!result?.success)throw Error(result?.error||'History unavailable');
      list.replaceChildren(...result.records.map(card));
      get('nightStatus').textContent=result.records.length?`${result.records.length} saved session(s). History is stored on this browser profile and backed up on Windows.`:'No saved Night Shutdown sessions yet.';
      get('nightSchedule').textContent=result.context?.schedule?.enabled?`${result.context.schedule.monitor_time} monitor · ${result.context.schedule.capture_time} save & lock · ${result.context.schedule.shutdown_time} shutdown`:'Night schedule has not been enabled on this PC.';
    }catch(e){get('nightStatus').textContent=e.message;}
  }
  get('nightRefresh').addEventListener('click',refresh);
  get('nightClear').addEventListener('click',async()=>{
    if(!window.confirm('Clear this browser’s backed-up Night History? Pending backups and Windows copies will be kept.'))return;
    const result=await send('NIGHT_CLEAR_HISTORY',{confirmed:true});if(result?.success)refresh();else get('nightStatus').textContent=result?.error||'History could not be cleared.';
  });
  document.addEventListener('strict-history-open',refresh);refresh();
  globalThis.StrictNightHistory={resume};
})();
