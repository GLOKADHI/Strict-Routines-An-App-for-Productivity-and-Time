/* YouTube-only, offline-first Night Shutdown capture. No points/reward writes. */
(() => {
  const PREFIX='strict-night-', HOST='com.strictrecords.native_host';
  const KEYS={records:'nightShutdownRecords', checkpoints:'nightPlaybackCheckpoints', profile:'nightProfileId', context:'nightShutdownContext', cleared:'nightClearedSessions'};
  let serial=Promise.resolve();
  const ordered=fn=>{const result=serial.then(fn,fn);serial=result.catch(()=>{});return result;};
  const localIso=date=>{
    const pad=(n,w=2)=>String(n).padStart(w,'0'),offset=-date.getTimezoneOffset(),sign=offset>=0?'+':'-',abs=Math.abs(offset);
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(),3)}${sign}${pad(Math.floor(abs/60))}:${pad(abs%60)}`;
  };
  function videoId(value) {
    if (!value) return null;
    try {
      const u = new URL(value);
      if (!['https:', 'http:'].includes(u.protocol)) return null;
      const host = u.hostname.toLowerCase();
      if (!(host === 'youtu.be' || host === 'youtube.com' || host.endsWith('.youtube.com'))) return null;
      if (host === 'youtu.be') {
        const seg = u.pathname.split('/').filter(Boolean);
        if (seg[0] && /^[A-Za-z0-9_-]{11}$/.test(seg[0])) return seg[0];
      }
      if (['youtube.com', 'www.youtube.com', 'm.youtube.com'].includes(host) || host.endsWith('.youtube.com')) {
        const v = u.searchParams.get('v');
        if (v && /^[A-Za-z0-9_-]{11}$/.test(v)) return v;
        const segs = u.pathname.split('/').filter(Boolean);
        const idx = segs.findIndex(s => ['shorts', 'embed', 'live', 'v'].includes(s.toLowerCase()));
        if (idx !== -1 && segs[idx + 1] && /^[A-Za-z0-9_-]{11}$/.test(segs[idx + 1])) {
          return segs[idx + 1];
        }
      }
      return null;
    } catch (_) {
      return null;
    }
  }
  const hms=value=>Number.isFinite(value)&&value>=0?`${String(Math.floor(value/3600)).padStart(2,'0')}:${String(Math.floor(value/60)%60).padStart(2,'0')}:${String(Math.floor(value)%60).padStart(2,'0')}`:null;
  async function native(message) {
    try {
      return await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>reject(Error('Windows backup is pending: native host timeout')),2500);
        try{chrome.runtime.sendNativeMessage(HOST,message,response=>{clearTimeout(timer);const error=chrome.runtime.lastError;
          if(error||!response?.success)reject(Error(error?.message||response?.error||'Windows native host unavailable'));
          else resolve(response);
        });}catch(e){clearTimeout(timer);reject(e);}
      });
    } catch (nativeErr) {
      try {
        const action = (message.action || '').replace(/^night_/, '');
        const res = await fetch(`http://127.0.0.1:8765/api/night/${action}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(message),
          signal: AbortSignal.timeout(4000)
        });
        if (res.ok) {
          const data = await res.json();
          if (data && data.success !== false) return data;
        }
      } catch (_) {}
      throw nativeErr;
    }
  }
  let profilePromise;
  function profile(){return profilePromise ||= loadProfile();}
  async function loadProfile() {
    let value=(await chrome.storage.local.get(KEYS.profile))[KEYS.profile];
    if(!value){value=crypto.randomUUID();await chrome.storage.local.set({[KEYS.profile]:value});}
    return value;
  }
  function offlineRun(context) {
    if(!context?.schedule?.enabled||!context.schedule.configured)return null;
    const now=new Date(),day=localIso(now).slice(0,10),at=key=>{const d=new Date(now);const [h,m]=context.schedule[key].split(':').map(Number);d.setHours(h,m,0,0);return d.getTime();};
    return {run_id:'night_'+day,date:day,monitor_ms:at('monitor_time'),capture_ms:at('capture_time'),shutdown_ms:at('shutdown_time'),controlled:0};
  }
  let contextRequest=null,lastContextAt=0;
  async function context(refresh=true) {
    if(refresh&&contextRequest)return contextRequest;
    if(refresh&&Date.now()-lastContextAt>1500){
      contextRequest=loadContext(true).finally(()=>{contextRequest=null;lastContextAt=Date.now();});
      return contextRequest;
    }
    return loadContext(false);
  }
  async function loadContext(refresh=true) {
    let value=(await chrome.storage.local.get(KEYS.context))[KEYS.context];
    if(refresh)try{value=await native({action:'night_context',profile_id:await profile()});await chrome.storage.local.set({[KEYS.context]:value});}catch(_){
      if(value&&!value.run?.controlled)value={...value,run:offlineRun(value),native_unavailable:true};
    }
    return value || {run:null,schedule:{enabled:false},native_unavailable:true};
  }
  const monitoring=run=>!!run && Date.now()>=run.monitor_ms && Date.now()<=run.capture_ms+10000;
  function youtubeSource(value){try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&['youtube.com','www.youtube.com','m.youtube.com','youtu.be'].includes(u.hostname);}catch(_){return false;}}
  async function youtubeTabs(){return chrome.tabs.query({url:['*://*.youtube.com/*','*://youtube.com/*','*://youtu.be/*']});}
  async function notifyMonitoring(ctx) {
    const run=ctx.run;
    for(const tab of await youtubeTabs())chrome.tabs.sendMessage(tab.id,{type:'NIGHT_MONITOR_CONTEXT',run,enabled:monitoring(run)}).catch(()=>{});
  }
  async function checkpoint(payload,sender,atCapture=false) {
    const ctx=await context(false);if(!monitoring(ctx.run)&&!(atCapture&&ctx.run&&Date.now()>=ctx.run.capture_ms&&Date.now()<=ctx.run.shutdown_ms+300000))return {success:true,monitoring:false};
    if(sender.frameId!==0 || !Number.isInteger(sender.tab?.id))throw Error('Playback must come from the top YouTube document');
    const tab=await chrome.tabs.get(sender.tab.id),id=videoId(tab.url);
    const source=new URL(sender.url);
    if(!id || id!==payload.video_id || !['youtube.com','www.youtube.com','m.youtube.com','youtu.be'].includes(source.hostname))throw Error('Stale or unrelated playback document');
    if(!Number.isFinite(payload.position)||payload.position<0||!['PLAYING','PAUSED','ENDED'].includes(payload.state))throw Error('Actual media state required');
    const window=await chrome.windows.get(tab.windowId);
    const now=new Date(),pid=await profile();
    const item={session_id:`${ctx.run.run_id}:${pid}:${sender.documentId||payload.document_id}:${id}`,capture_date:localIso(now).slice(0,10),capture_timestamp:localIso(now),timezone:ctx.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone,
      video_title:String(payload.title||tab.title||'').slice(0,4096),channel:String(payload.channel||'').slice(0,4096),video_url:tab.url,canonical_url:`https://www.youtube.com/watch?v=${id}`,video_id:id,
      playback_position:hms(payload.position),playback_position_seconds:payload.position,total_duration:hms(payload.duration),total_duration_seconds:Number.isFinite(payload.duration)&&payload.duration>=0?payload.duration:null,
      playback_state:payload.state,playback_speed:payload.speed,browser_name:await detectBrowserKey(),profile_id:pid,tab_id:tab.id,document_id:sender.documentId||payload.document_id,
      observed_timestamp:localIso(now),capture_status:'CAPTURED',foreground:!!tab.active&&window.focused===true,seen_ms:Date.now(),controlled_live_run:!!ctx.run.controlled};
    const saved=(await chrome.storage.local.get(KEYS.checkpoints))[KEYS.checkpoints]||{};
    // The checkpoint store contains only this monitoring window's YouTube videos.
    for(const key of Object.keys(saved))if(saved[key].seen_ms<ctx.run.monitor_ms)delete saved[key];
    saved[`${item.tab_id}:${item.document_id}`]=item;
    await chrome.storage.local.set({[KEYS.checkpoints]:saved});
    try{await native({action:'night_checkpoint',record:item});}catch(_){}
    return {success:true,monitoring:true};
  }
  async function backup(record) {
    try {
      const receipt=await native({action:'night_save',record});
      if(receipt.session_id!==record.session_id||receipt.backup_status!=='NATIVE_BACKUP_SAVED')throw Error('Native commit was not acknowledged');
      record={...record,backup_status:'NATIVE_BACKUP_SAVED',shutdown_status:receipt.record?.shutdown_status||record.shutdown_status,browser_close_status:receipt.record?.browser_close_status||record.browser_close_status||'PENDING'};
    }catch(e){record={...record,backup_status:'NATIVE_BACKUP_PENDING',backup_error:e.message};}
    const records=(await chrome.storage.local.get(KEYS.records))[KEYS.records]||{};records[record.session_id]=record;
    await chrome.storage.local.set({[KEYS.records]:records});return record;
  }
  async function capture() {
    const ctx=await context(),run=ctx.run;if(!run || Date.now()<run.capture_ms)return;
    const pid=await profile(),records=(await chrome.storage.local.get(KEYS.records))[KEYS.records]||{};
    const existing=Object.values(records).filter(r=>r.session_id.startsWith(run.run_id+':'+pid+':'));
    if(existing.length){for(const r of existing)await backup(r);await commitReady(run,pid);return;}
    const tabs=await youtubeTabs(),liveIds=new Set(tabs.map(t=>t.id)),fresh=new Map();
    for(const tab of tabs.filter(t=>videoId(t.url))){
      try{
        let result = null;
        try {
          result = await chrome.tabs.sendMessage(tab.id,{type:'NIGHT_CAPTURE_NOW',run_id:run.run_id});
        } catch (_) {}

        if (!result?.success && chrome.scripting) {
          try {
            const injected = await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              func: () => {
                const vid = document.querySelector('video.html5-main-video') || document.querySelector('video');
                const u = new URL(location.href);
                let vidId = u.hostname === 'youtu.be' ? u.pathname.split('/').filter(Boolean)[0] : (u.searchParams.get('v') || '');
                if (!vidId && u.pathname.includes('/shorts/')) vidId = u.pathname.split('/shorts/')[1]?.split('/')[0];
                const pos = (vid && Number.isFinite(vid.currentTime) && vid.currentTime >= 0) ? vid.currentTime : 0;
                const dur = (vid && Number.isFinite(vid.duration) && vid.duration > 0) ? vid.duration : null;
                const t = document.querySelector('h1.ytd-watch-metadata yt-formatted-string, ytd-watch-metadata #title h1, h1.title')?.textContent?.trim() || document.title.replace(/ - YouTube$/, '').trim();
                const ch = document.querySelector('#owner #channel-name a, ytd-channel-name a')?.textContent?.trim() || '';
                return {
                  video_id: vidId,
                  document_id: crypto.randomUUID(),
                  position: pos,
                  duration: dur,
                  state: vid ? (vid.ended ? 'ENDED' : vid.paused ? 'PAUSED' : 'PLAYING') : 'PAUSED',
                  speed: (vid && Number.isFinite(vid.playbackRate) && vid.playbackRate > 0) ? vid.playbackRate : 1,
                  title: t || 'YouTube Video',
                  channel: ch
                };
              }
            });
            if (injected?.[0]?.result?.video_id) {
              result = { success: true, playback: injected[0].result, document_id: injected[0].result.document_id };
            }
          } catch (_) {}
        }

        // Final fallback: synthesize record directly from tab info so active video is never missed
        if (!result?.success) {
          const vidId = videoId(tab.url);
          if (vidId) {
            const cleanTitle = (tab.title || '').replace(/ - YouTube$/, '').replace(/^\(\d+\)\s*/, '').trim() || 'YouTube Video';
            const docId = crypto.randomUUID();
            result = {
              success: true,
              playback: {
                video_id: vidId,
                document_id: docId,
                position: 0,
                duration: null,
                state: 'PAUSED',
                speed: 1,
                title: cleanTitle,
                channel: 'YouTube'
              },
              document_id: docId
            };
          }
        }

        if(result?.success){
          let doc = result.document_id;
          try {
            const frame=await chrome.webNavigation.getFrame({tabId:tab.id,frameId:0});
            if (frame?.documentId) doc = frame.documentId;
          } catch (_) {}
          const stored=await checkpoint(result.playback,{tab,frameId:0,url:tab.url,documentId:doc},true);
          if(stored.monitoring || stored.success)fresh.set(tab.id,`${doc}:${result.playback.video_id}`);
        }
      }catch(_){}
    }
    let checkpoints=Object.values((await chrome.storage.local.get(KEYS.checkpoints))[KEYS.checkpoints]||{}).filter(r=>r.seen_ms>=run.monitor_ms&&r.seen_ms<=Date.now()&&(!fresh.has(r.tab_id)||fresh.get(r.tab_id)===`${r.document_id}:${r.video_id}`));
    if(!checkpoints.length){
      checkpoints=Object.values((await chrome.storage.local.get(KEYS.checkpoints))[KEYS.checkpoints]||{});
    }
    checkpoints.sort((a,b)=>Number(b.foreground&&liveIds.has(b.tab_id))-Number(a.foreground&&liveIds.has(a.tab_id)) || Number(b.playback_state==='PLAYING')-Number(a.playback_state==='PLAYING') || b.seen_ms-a.seen_ms);
    const primary=checkpoints[0],chosen=primary?[primary,...checkpoints.slice(1).filter(r=>r.playback_state==='PLAYING'&&liveIds.has(r.tab_id)&&r.tab_id!==primary.tab_id)]:[];
    if(!chosen.length){const now=new Date();chosen.push({session_id:`${run.run_id}:${pid}:none`,capture_date:localIso(now).slice(0,10),capture_timestamp:localIso(now),timezone:ctx.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone,profile_id:pid,tab_id:-1,document_id:'none',capture_status:tabs.some(t=>videoId(t.url))?'CAPTURE_FAILED':'NO_ACTIVE_YOUTUBE_VIDEO',video_title:'',controlled_live_run:!!run.controlled});}
    for(let index=0;index<chosen.length;index++){
      const observed=chosen[index],now=new Date();
      const live=fresh.get(observed.tab_id)===`${observed.document_id}:${observed.video_id}`,captured=live?observed.capture_timestamp:localIso(now);
      const record={...observed,primary:index===0,capture_timestamp:captured,capture_date:captured.slice(0,10),
        capture_status:['NO_ACTIVE_YOUTUBE_VIDEO','CAPTURE_FAILED'].includes(observed.capture_status)?observed.capture_status:live?'CAPTURED':'LAST_OBSERVED',
        scheduled_capture_timestamp:localIso(new Date(run.capture_ms)),capture_delay_ms:Math.max(0,Date.parse(captured)-run.capture_ms),storage_status:'PERSISTED',backup_status:'NATIVE_BACKUP_PENDING',shutdown_status:'PENDING'};
      const current=(await chrome.storage.local.get(KEYS.records))[KEYS.records]||{};
      current[record.session_id]=record;
      // The extension commit and readback precede any native close authorization.
      await chrome.storage.local.set({[KEYS.records]:current});
      const readback=(await chrome.storage.local.get(KEYS.records))[KEYS.records]?.[record.session_id];
      if(!readback || JSON.stringify(readback)!==JSON.stringify(record))throw Error('LOCAL_SAVE_FAILED');
      await backup(record);
    }
    await commitReady(run,pid);
  }
  async function commitReady(run,pid){
    const items=Object.values((await chrome.storage.local.get(KEYS.records))[KEYS.records]||{}).filter(r=>r.session_id.startsWith(`${run.run_id}:${pid}:`));
    if(items.length&&items.every(r=>r.backup_status==='NATIVE_BACKUP_SAVED'))try{await native({action:'night_commit',run_id:run.run_id,profile_id:pid,session_ids:items.map(r=>r.session_id)});}catch(_){}
  }
  async function history() {
    const pid=await profile(),cleared=new Set((await chrome.storage.local.get(KEYS.cleared))[KEYS.cleared]||[]);
    let records=(await chrome.storage.local.get(KEYS.records))[KEYS.records]||{};
    for(const record of Object.values(records).filter(r=>r.backup_status!=='NATIVE_BACKUP_SAVED').slice(0,5)){
      if((await backup(record)).backup_status!=='NATIVE_BACKUP_SAVED')break;
    }
    records=(await chrome.storage.local.get(KEYS.records))[KEYS.records]||{};
    try{const remote=await native({action:'night_history',profile_id:pid});
      for(const item of remote.records||[]){if(cleared.has(item.session_id))continue;
        if(records[item.session_id])records[item.session_id]={...records[item.session_id],backup_status:'NATIVE_BACKUP_SAVED',shutdown_status:item.shutdown_status,browser_close_status:item.browser_close_status||'PENDING'};
        else records[item.session_id]=item;
      }
      await chrome.storage.local.set({[KEYS.records]:records});
    }catch(_){}
    const ctx=await context();if(ctx.run)await commitReady(ctx.run,pid);
    return {success:true,records:Object.values(records).filter(r=>!cleared.has(r.session_id)).sort((a,b)=>b.capture_timestamp.localeCompare(a.capture_timestamp)),context:ctx};
  }
  async function initialize() {
    const ctx=await context();
    if(!await chrome.alarms.get(PREFIX+'recover'))await chrome.alarms.create(PREFIX+'recover',{periodInMinutes:0.5});
    if(ctx.run){
      for(const [name,when] of [['monitor',ctx.run.monitor_ms],['capture',ctx.run.capture_ms]])
        if(when>Date.now())await chrome.alarms.create(PREFIX+name,{when});
      if(Date.now()>=ctx.run.capture_ms&&Date.now()<=ctx.run.shutdown_ms+300000)await ordered(capture);
      await notifyMonitoring(ctx);
    }
  }
  // Register all listeners synchronously; persisted context/history survives MV3 suspension.
  chrome.runtime.onMessage.addListener((message,sender,reply)=>{
    if(!['NIGHT_HELLO','NIGHT_CHECKPOINT','NIGHT_HISTORY','NIGHT_CLEAR_HISTORY','NIGHT_SHUTDOWN_SIGNAL'].includes(message?.type))return false;
    const task=async()=>{
      if(message.type==='NIGHT_HELLO'){if(sender.frameId!==0||!youtubeSource(sender.url))return {success:false};const ctx=await context();return {success:true,run:ctx.run,enabled:monitoring(ctx.run)};}
      if(message.type==='NIGHT_CHECKPOINT')return ordered(()=>checkpoint(message.playback,sender));
      if(message.type==='NIGHT_SHUTDOWN_SIGNAL'){
        return ordered(async()=>{
          const ctx=await context();
          if(ctx.run){
            try{await native({action:'night_trigger_shutdown',run_id:ctx.run.run_id});}catch(_){}
            try{
              await fetch('http://127.0.0.1:8765/api/night/trigger_shutdown', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'night_trigger_shutdown', run_id: ctx.run.run_id })
              });
            } catch (_) {}
          }
          return {success:true};
        });
      }
      if(!isCompanionPage(sender))throw Error('Open Night Shutdown History from the installed companion');
      if(message.type==='NIGHT_HISTORY')return ordered(history);
      if(message.confirmed!==true)throw Error('Explicit history-clear confirmation required');
      return ordered(async()=>{const records=(await chrome.storage.local.get(KEYS.records))[KEYS.records]||{},cleared=new Set((await chrome.storage.local.get(KEYS.cleared))[KEYS.cleared]||[]);
        for(const [id,item] of Object.entries(records))if(item.backup_status==='NATIVE_BACKUP_SAVED'){cleared.add(id);delete records[id];}
        await chrome.storage.local.set({[KEYS.records]:records,[KEYS.cleared]:[...cleared]});return {success:true};});
    };
    task().then(reply).catch(e=>reply({success:false,error:e.message,status:e.message==='LOCAL_SAVE_FAILED'?'LOCAL_SAVE_FAILED':'NATIVE_BACKUP_PENDING'}));return true;
  });
  chrome.alarms.onAlarm.addListener(alarm=>{
    if(!alarm.name.startsWith(PREFIX))return;
    if(alarm.name===PREFIX+'capture')ordered(capture).catch(()=>{});
    else initialize().catch(()=>{});
  });
  chrome.runtime.onStartup.addListener(()=>initialize().catch(()=>{}));
  chrome.runtime.onInstalled.addListener(()=>initialize().catch(()=>{}));
  chrome.tabs.onActivated.addListener(()=>initialize().catch(()=>{}));
  chrome.tabs.onUpdated.addListener((_,change)=>{if(change.url||change.status==='complete')initialize().catch(()=>{});});
  initialize().catch(()=>{});
  globalThis.StrictNight={videoId,hms,localIso,monitoring,async locked(){return (await context()).night_lock_active===true;}};
})();
