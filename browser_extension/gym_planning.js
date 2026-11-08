(() => {
  const date=document.getElementById('gymDate'), choice=document.getElementById('gymChoice');
  const status=document.getElementById('gymStatus'), saved=document.getElementById('gymChoiceStatus'), button=document.getElementById('gymSave');
  let record=null, busy=false, dirty=false;
  choice.addEventListener('change',()=>{dirty=true;});
  function renderChoice(next) {
    if(dirty && record && next.revision!==record.revision){saved.textContent='Choice changed on another device · refresh before saving';return;}
    record=next;
    if(!dirty) choice.value=record.gym_planned===true?'YES':record.gym_planned===false?'NO':'';
    saved.textContent=record.sync_status==='REVOCATION_PENDING'?'Gym marked No · restoring normal restrictions':
      record.gym_planned===true?'Gym planned · saved on desktop':
      record.gym_planned===false?'Gym not planned · GYM Mode unavailable for this date':'Choose Yes or No · no Gym access until confirmed';
  }
  async function read(path,body) {
    const response=await fetch('http://127.0.0.1:8765'+path,{cache:'no-store',signal:AbortSignal.timeout(5000),
      ...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    return response.json();
  }
  async function loadChoice() {
    const response=await read('/api/gym/decision?date='+encodeURIComponent(date.value));
    if(!response.success)throw Error(response.error || 'Decision unavailable');
    renderChoice(response.decision);
  }
  async function refresh() {
    if(busy)return;busy=true;
    try {
      const result=await read('/api/gym_mode/status');
      if(!result.success || !result.state)throw Error('PC unavailable');
      const state=result.state;
      status.textContent=state.message+' · '+state.lives_balance+'/3 lives'+(state.proof_status?' · '+state.proof_status:'');
      if(!date.value){const [y,m,d]=state.date.split('-').map(Number);date.value=new Date(Date.UTC(y,m-1,d+1)).toISOString().slice(0,10);await loadChoice();}
      document.dispatchEvent(new CustomEvent('strict-planning-refresh',{detail:{online:true}}));
      const pending=(await chrome.storage.local.get('pendingGymChoice')).pendingGymChoice;
      if(!pending && result.planning?.[date.value]?.gym)renderChoice(result.planning[date.value].gym);
      if(pending?.date===date.value)saved.textContent=pending.error || 'Choice pending PC confirmation · no new Gym access';
    } catch(_){status.textContent='Offline · Gym state is not confirmed';document.dispatchEvent(new CustomEvent('strict-planning-refresh',{detail:{online:false}}));}
    finally {busy=false;}
  }
  date.addEventListener('change',()=>{dirty=false;record=null;loadChoice().catch(error=>{saved.textContent=error.message;});});
  button.addEventListener('click',async()=>{
    if(!choice.value){saved.textContent='Choose Yes or No first';return;}
    const planned=choice.value==='YES';button.disabled=true;
    try {
      if(!record || record.date!==date.value)await loadChoice();
      const body={account_id:record.account_id,date:date.value,gym_planned:planned,expected_revision:record.revision,
        timezone:record.timezone || 'Asia/Kolkata',request_id:crypto.randomUUID()};
      await chrome.storage.local.set({pendingGymChoice:body});
      const response=await read('/api/gym/decision',body);
      if(!response.success){
        await chrome.storage.local.set({pendingGymChoice:{...body,error:response.error,conflict:['REVISION_CONFLICT','ADMIN_REQUIRED','UNAUTHORIZED'].includes(response.code)}});
        throw Error(response.error || 'PC has not confirmed the choice');
      }
      await chrome.storage.local.remove('pendingGymChoice');dirty=false;await loadChoice();await refresh();
    } catch(error){saved.textContent=error.message+' · pending choices grant no Gym access';}
    finally {button.disabled=false;}
  });
  document.addEventListener('strict-planning-open',()=>{dirty=false;record=null;loadChoice().then(refresh).catch(error=>{saved.textContent=error.message;});});
  refresh();setInterval(()=>{if(!document.hidden)refresh();},15000);
})();
