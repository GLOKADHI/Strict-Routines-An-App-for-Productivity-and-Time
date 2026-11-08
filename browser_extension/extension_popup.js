(() => {
  const get=id=>document.getElementById(id);
  let busy=false, polling, lastPolicy=null, sppCountdownTimer=null, lastSppData=null;
  function text(id,value){const el=get(id);if(el)el.textContent=value;}
  function badge(id,value,tone='neutral'){const el=get(id);if(el){el.className='badge '+tone;el.textContent=value;}}
  function send(type,extra) {
    return new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>reject(Error('The extension did not respond. Refresh this panel or reload the extension.')),15000);
      const payload=extra?Object.assign({type},extra):{type};
      try {chrome.runtime.sendMessage(payload,result=>{
        clearTimeout(timeout);const error=chrome.runtime.lastError;
        if(error)reject(Error('Extension connection interrupted. Close and reopen this panel.'));
        else if(!result?.success)reject(Error(result?.error || 'The extension could not load its status.'));
        else resolve(result);
      });}catch(_){clearTimeout(timeout);reject(Error('Open this page from the installed StrictRoutines extension.'));}
    });
  }
  async function api(path,body) {
    const response=await fetch('http://127.0.0.1:8765'+path,{cache:'no-store',signal:AbortSignal.timeout(5000),
      ...(body?{method:'POST',headers:{'Content-Type':'application/json','X-StrictRoutines-Setup':'1'},body:JSON.stringify(body)}:{})});
    const data=await response.json();if(!response.ok || data.success===false)throw Error(data.error || 'Desktop request failed.');return data;
  }
  function formatMmSs(sec) {
    const rem=Math.max(0,Math.round(Number(sec)||0));
    const m=Math.floor(rem/60), s=rem%60;
    return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  }
  function renderCustomDomains(customDomains) {
    const listEl=get('sppCustomDomainsList');
    if(!listEl)return;
    const entries=Object.entries(customDomains||{});
    if(!entries.length){
      listEl.innerHTML='<p class="note">No custom domains configured yet. Built-in banking, crypto, email &amp; checkout domains are always protected.</p>';
      return;
    }
    const nodes=entries.map(([dom,cat])=>{
      const row=document.createElement('div');
      row.style.cssText='display:flex;justify-content:space-between;align-items:center;padding:4px 8px;background:#0f172a;border:1px solid #1e293b;border-radius:6px;font-size:11.5px;';
      const info=document.createElement('span');
      info.innerHTML=`<strong style="color:#f8fafc;">${dom}</strong> <span style="color:#94a3b8;font-size:10.5px;">(${cat})</span>`;
      const btn=document.createElement('button');
      btn.type='button';
      btn.className='outline danger';
      btn.style.cssText='padding:2px 6px;min-height:22px;font-size:10.5px;';
      btn.textContent='Remove';
      btn.addEventListener('click',async()=>{
        try {
          const res=await api('/api/sensitive_page_protection/config',{remove_domains:[dom]});
          if(res?.status)renderSensitiveProtection(res.status);
          text('sppConfigMsg',`Removed ${dom}`);
        } catch (err) {
          text('sppConfigMsg',err.message);
        }
      });
      row.append(info,btn);
      return row;
    });
    listEl.replaceChildren(...nodes);
  }
  function renderSensitiveProtection(spp) {
    if(!spp)return;
    lastSppData=spp;
    renderCustomDomains(spp.custom_domains||{});
    const detailsBox=get('sppActiveDetails');
    const recBox=get('sppRecorderWarningBox');
    const riskBox=get('sppRiskAlertBox');
    const pagesUl=get('sppSensitivePagesList');
    const capsUl=get('sppCaptureActivityList');
    const dismissBtn=get('btnSppDismissNotice');
    const showActive=Boolean(spp.persistent_alert_active || spp.protection_active || spp.visual_monitoring_paused || (spp.sensitive_page_capture_risk_event && !spp.alert_dismissed));

    if(showActive) {
      const isRisk=Boolean(spp.capture_risk_active);
      const stillOpen=spp.protection_state==='SENSITIVE_PAGE_STILL_OPEN_PROTECTED';
      badge('sppBadge',isRisk?'RISK DETECTED':(stillOpen?'Still Open · Protected':'Protection Active'),isRisk?'err':'warn');
      text('sppStatusDetail',isRisk
        ? (spp.risk_alert_heading || 'SENSITIVE PAGE + SCREEN RECORDING DETECTED')
        : (stillOpen
          ? 'Sensitive page still open – visual protection remains active.'
          : `SENSITIVE PAGE PROTECTION ACTIVE — Visual monitoring paused: ${spp.cooldown_formatted || '05:00'}`));
      if(detailsBox)detailsBox.style.display='block';
      text('sppBannerText',spp.panel_header || 'SENSITIVE PAGE PROTECTION');
      const cdText=stillOpen?'HOLD':(spp.cooldown_formatted || formatMmSs(spp.cooldown_remaining_seconds));
      text('sppCountdownBadge',cdText);
      text('sppCooldownText',stillOpen?'HOLD (Page Open)':`${cdText} remaining`);
      text('sppVisualCaptureStateText',spp.strict_records_visual_capture_state || 'PAUSED');
      text('sppCategoryText',spp.current_category || 'SENSITIVE_PAGE');
      text('sppDomainText',spp.matched_domain || 'Protected origin');
      text('sppAlertMsgText',spp.alert_message || 'StrictRecords visual screen monitoring has been paused temporarily to protect your sensitive information.');
      text('sppActionStatusText',spp.panel_action_status || (spp.can_dismiss ? 'All sensitive pages and detected captures are closed.' : 'Close all sensitive pages and stop detected recording or screen-sharing activity.'));

      if(riskBox){
        if(isRisk){
          riskBox.style.display='block';
          text('sppRiskHeadingText',spp.risk_alert_heading || 'SENSITIVE PAGE + SCREEN RECORDING DETECTED');
          text('sppRiskBodyText',spp.risk_alert_body || 'Your browser currently contains a sensitive financial or personal page while a screen-capture activity is detected. Close all active screen-recording and screen-sharing applications before continuing.');
        } else {
          riskBox.style.display='none';
        }
      }
      if(pagesUl){
        const pages=Array.isArray(spp.sensitive_pages_summary)?spp.sensitive_pages_summary:[];
        pagesUl.innerHTML=pages.length
          ? pages.map(p=>`<li>• <strong>${p.label || p.matched_domain}</strong> – <span style="color:${p.status==='OPEN'?'#f87171':'#94a3b8'};font-weight:700;">${p.status || 'OPEN'}</span></li>`).join('')
          : '<li style="color:#94a3b8;">• None currently open</li>';
      }
      if(capsUl){
        const caps=Array.isArray(spp.capture_activity_summary)?spp.capture_activity_summary:[];
        capsUl.innerHTML=caps.length
          ? caps.map(c=>`<li>• <strong>${c.name}</strong> – <span style="color:${c.is_active?'#fbbf24':'#94a3b8'};font-weight:700;">${c.classification || c.status}</span></li>`).join('')
          : '<li style="color:#94a3b8;">• No active external/browser capture detected</li>';
      }
      if(dismissBtn){
        dismissBtn.disabled=!spp.can_dismiss;
      }
      if(recBox){
        if(spp.external_recorder_detected && spp.external_recorder_warning){
          recBox.style.display='block';
          recBox.textContent=spp.external_recorder_warning;
        } else {
          recBox.style.display='none';
        }
      }
      if(!sppCountdownTimer){
        sppCountdownTimer=setInterval(()=>{
          if(!lastSppData || (!lastSppData.protection_active && !lastSppData.visual_monitoring_paused))return;
          if(lastSppData.cooldown_remaining_seconds>0){
            lastSppData.cooldown_remaining_seconds=Math.max(0,lastSppData.cooldown_remaining_seconds-1);
            const fmt=formatMmSs(lastSppData.cooldown_remaining_seconds);
            lastSppData.cooldown_formatted=fmt;
            text('sppCountdownBadge',fmt);
            text('sppCooldownText',`${fmt} remaining`);
          }
        },1000);
      }
    } else {
      if(sppCountdownTimer){clearInterval(sppCountdownTimer);sppCountdownTimer=null;}
      const resumed=spp.protection_state==='VISUAL_MONITORING_RESUMED';
      badge('sppBadge',resumed?'Visual Monitoring Resumed':'Standby',resumed?'ok':'neutral');
      text('sppStatusDetail','Visual screen monitoring active · Automatically pauses for 5 minutes on banking, crypto, email & payment checkout pages.');
      if(detailsBox)detailsBox.style.display='none';
    }
  }
  function renderPolicy(data) {
    lastPolicy=data;
    const managed=data.installation_mode==='ADMINISTRATOR_MANAGED' && data.policy_enforced===true;
    get('btnUnlockPrompt').hidden=!managed;
    if(data.maintenance_mode){
      badge('mgmtBadge','Maintenance','warn');
      const remaining=Math.max(0,Math.ceil((Number(data.unlocked_until)||0)-Date.now()/1000));
      text('mgmtDetail',`Maintenance access ends in ${Math.ceil((remaining || data.unlocked_remaining_seconds || 0)/60)} minutes. Browser policy status refreshes automatically.`);
    }else if(managed){badge('mgmtBadge','Managed','ok');text('mgmtDetail','Removal protection is confirmed by the desktop browser policy.');}
    else{badge('mgmtBadge','Standard');text('mgmtDetail',data.installation_mode==='ADMINISTRATOR_MANAGED'?'Management was requested; applied protection is not confirmed.':'Managed removal protection is not active. Use this browser’s extension manager for normal management.');}
  }
  function renderRows(rows) {
    const focusedName=document.activeElement?.closest('details.site')?.querySelector('.site-name')?.textContent;
    const nodes=rows.map(row=>{
      const details=document.createElement('details');details.className='site';
      const summary=document.createElement('summary');
      const name=document.createElement('span');name.className='site-name';name.textContent=row.name;
      const state=document.createElement('span');
      const unavailable=row.unavailable===true;
      const allowed=!unavailable && row.restricted===false;
      state.className='badge '+(unavailable?'warn':allowed?'ok':'neutral');state.textContent=unavailable?'Check failed':allowed?'Allowed':'Restricted';
      summary.append(name);if(row.current){const current=document.createElement('span');current.className='site-current';current.textContent='Current site';summary.append(current);}summary.append(state);
      const reason=document.createElement('p');
      reason.textContent=(row.statusText || row.reason || 'No confirmed decision.')+
        (Number.isFinite(row.requiredScore)?` Required: ${row.requiredScore} points.`:'')+
        (row.remaining_seconds>0?` ${Math.ceil(row.remaining_seconds/60)} minutes remaining.`:'');
      details.append(summary,reason);return details;
    });
    const openNames=new Set([...get('siteList').querySelectorAll('details[open]')].map(node=>node.querySelector('.site-name')?.textContent));
    nodes.forEach(node=>{node.open=openNames.has(node.querySelector('.site-name')?.textContent);});
    get('siteList').replaceChildren(...nodes);
    if(focusedName)nodes.find(node=>node.querySelector('.site-name')?.textContent===focusedName)?.querySelector('summary')?.focus({preventScroll:true});
  }
  async function refresh() {
    if(busy || document.hidden)return;busy=true;get('btnConnect').disabled=true;text('dashboardError','');
    try {
      const [dashboard,policy,spp]=await Promise.allSettled([
        send('STRICTBOT_DASHBOARD_STATUS'),
        api('/api/browser_extensions/lock_status'),
        api('/api/sensitive_page_protection/status')
      ]);
      if(spp.status==='fulfilled'){renderSensitiveProtection(spp.value);}
      else{
        try{const fb=await send('STRICTBOT_GET_SENSITIVE_PROTECTION_STATUS');renderSensitiveProtection(fb);}catch(_){}
      }
      if(dashboard.status==='rejected')throw dashboard.reason;
      const data=dashboard.value,connected=data.connection.success===true;
      badge('connBadge',connected?'Connected':'Unavailable',connected?'ok':'err');
      text('connDetail',connected?'Your desktop is reachable. Website decisions are shown below.':data.connection.error || 'Open StrictRoutines on this PC, then refresh.');
      const failed=data.rows.filter(row=>row.unavailable),scores=data.rows.map(row=>row.currentScore ?? row.score).filter(Number.isFinite);
      const consistent=scores.length>0 && scores.every(score=>score===scores[0]);
      text('confirmedScore',consistent?String(scores[0]):'—');
      text('decisionHealth',failed.length?`${failed.length} of ${data.rows.length} access checks unavailable. Existing restrictions remain until confirmed.`:consistent?'All website decisions confirmed.':'Access decisions are updating; refresh if the score remains unavailable.');
      get('decisionHealth').className='status-line '+(failed.length?'warn':'');
      text('monitorStatus',data.monitoring.configured?(data.monitoring.connected?'Independent monitoring connected.':'Independent monitoring is configured but not currently confirmed.'):'Independent Status Helper is not configured for this installation. Set it up in desktop Controls.');
      text('extensionVersion','v'+data.version);text('browserLabel',`${data.browser || 'Browser'} · this profile`);
      text('browserIdentity',`Installed companion v${data.version}. This panel shows the code currently loaded in this browser.`);
      text('lastChecked','Checked '+new Date(data.checkedAt).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit',hour12:true}));
      renderRows(data.rows);
      if(policy.status==='fulfilled'){renderPolicy(policy.value);}else{badge('mgmtBadge','Unknown','warn');text('mgmtDetail',policy.reason.message);}

    }catch(error){badge('connBadge','Needs attention','warn');text('dashboardError',error.message);text('decisionHealth','Website access is unconfirmed. Retry to load current decisions.');text('confirmedScore','—');
      get('siteList').querySelectorAll('.badge').forEach(node=>{node.className='badge warn';node.textContent='Unconfirmed';});
    }
    finally{busy=false;get('btnConnect').disabled=false;}
  }
  const tabs=[...document.querySelectorAll('[data-view]')];
  function view(name,focus=false){
    tabs.forEach(tab=>{const selected=tab.dataset.view===name;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;get('view-'+tab.dataset.view).hidden=!selected;if(selected&&focus)tab.focus();});
    if(name==='planning')document.dispatchEvent(new CustomEvent('strict-planning-open'));
    if(name==='history')document.dispatchEvent(new CustomEvent('strict-history-open'));
    if(name==='scheduler')document.dispatchEvent(new CustomEvent('strict-scheduler-open'));

  }
  tabs.forEach((tab,index)=>{
    tab.addEventListener('click',()=>view(tab.dataset.view));
    tab.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowLeft'?-1:1)+tabs.length)%tabs.length;view(tabs[next].dataset.view,true);}});
  });
  function openPage(page){chrome.tabs.create({url:page.startsWith('http')?page:chrome.runtime.getURL(page)},()=>{if(chrome.runtime.lastError)text('dashboardError','Could not open the requested page.');});}
  get('btnConnect').addEventListener('click',refresh);
  get('btnFullClient')?.addEventListener('click',()=>openPage('client/client.html'));
  get('btnDashboard').addEventListener('click',()=>openPage('http://127.0.0.1:8765/'));
  get('btnConfig').addEventListener('click',()=>openPage('lock_prompt.html'));
  get('btnRequestUninstall').addEventListener('click',()=>{if(window.confirm('Opening this page starts a timed removal-authorization attempt. Continue?'))openPage('uninstall_challenge.html');});
  get('btnUnlockPrompt').addEventListener('click',()=>{get('pinBox').hidden=false;get('adminPin').value='';text('pinMsg','');get('adminPin').focus();});
  get('btnCancelPin').addEventListener('click',()=>{get('pinBox').hidden=true;get('adminPin').value='';get('btnUnlockPrompt').focus();});
  get('btnSubmitPin').addEventListener('click',async()=>{
    const pin=get('adminPin').value.trim();if(!pin){text('pinMsg','Enter your administrator PIN.');return;}
    get('btnSubmitPin').disabled=true;
    try{await api('/api/browser_extensions/unlock',{admin_pin:pin,duration_minutes:10});get('pinBox').hidden=true;await refresh();}
    catch(error){text('pinMsg',error.message);}finally{get('adminPin').value='';get('btnSubmitPin').disabled=false;}
  });
  get('btnSppDismissNotice')?.addEventListener('click',async()=>{
    try{
      const res=await api('/api/sensitive_page_protection/dismiss',{admin_override:false});
      renderSensitiveProtection(res);
    }catch(_){
      try{const r2=await send('STRICTBOT_DISMISS_SENSITIVE_ALERT',{admin_override:false});renderSensitiveProtection(r2);}catch(__){}
    }
  });
  get('btnSppAddDomain')?.addEventListener('click',async()=>{
    const domInput=get('sppCustomDomainInput');
    const catSelect=get('sppCustomCategorySelect');
    const dom=(domInput?.value||'').trim().toLowerCase();
    const cat=(catSelect?.value||'PAYMENT_CHECKOUT').trim();
    if(!dom){text('sppConfigMsg','Enter a domain (e.g. portal.mybank.com).');return;}
    try{
      const res=await api('/api/sensitive_page_protection/config',{add_domains:{[dom]:cat}});
      if(domInput)domInput.value='';
      if(res?.status)renderSensitiveProtection(res.status);
      text('sppConfigMsg',`Added ${dom} (${cat})`);
    }catch(err){
      text('sppConfigMsg',err.message);
    }
  });
  function start(){clearInterval(polling);if(!document.hidden){refresh();polling=setInterval(refresh,5000);}}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearInterval(polling);else start();});
  window.addEventListener('pagehide',()=>clearInterval(polling));start();
})();
