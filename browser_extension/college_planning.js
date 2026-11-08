(() => {
  const date=document.getElementById('gymDate'), choice=document.getElementById('collegeChoice');
  const status=document.getElementById('collegeStatus'), button=document.getElementById('collegeSave');
  let record=null,busy=false,dirty=false;
  async function call(path,body){
    const response=await fetch('http://127.0.0.1:8765'+path,{cache:'no-store',signal:AbortSignal.timeout(5000),
      ...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
    return response.json();
  }
  async function load(){
    if(busy || !date.value)return;busy=true;
    try {
      const result=await call('/api/planning/college?date='+encodeURIComponent(date.value));
      if(!result.success)throw Error(result.error || 'College decision unavailable');
      record=result.college_decision;
      choice.value=record.attends_college===true?'YES':record.attends_college===false?'NO':'';
      status.textContent=record.attends_college===true?'College day · two Bath occurrences':record.attends_college===false?
        'Home day · '+record.bath_plans.length+' configured Bath occurrence':'College decision required';
      const pending=(await chrome.storage.local.get('pendingCollegeChoice')).pendingCollegeChoice;
      if(pending?.date===date.value)status.textContent=pending.error || 'College choice pending PC confirmation';
    }catch(error){status.textContent=error.message;}finally{busy=false;}
  }
  date.addEventListener('change',()=>{dirty=false;load();});
  choice.addEventListener('change',()=>{dirty=true;});
  button.addEventListener('click',async()=>{
    if(!choice.value){status.textContent='Choose college attendance first';return;}
    const attends=choice.value==='YES';button.disabled=true;
    try {
      if(!record || record.date!==date.value)await load();
      if(!record)throw Error('Refresh the college plan first');
      const plans=attends?record.policy.college:record.attends_college===false?record.bath_plans:[];
      const body={account_id:record.account_id,date:date.value,attends_college:attends,bath_plans:plans,
        expected_revision:record.revision,timezone:record.timezone || 'Asia/Kolkata',request_id:crypto.randomUUID()};
      await chrome.storage.local.set({pendingCollegeChoice:body});
      const result=await call('/api/planning/college',body);
      if(!result.success){await chrome.storage.local.set({pendingCollegeChoice:{...body,conflict:true,error:result.error}});throw Error(result.error);}
      await chrome.storage.local.remove('pendingCollegeChoice');dirty=false;await load();
    }catch(error){status.textContent=error.message+' · plan not confirmed';}finally{button.disabled=false;}
  });
  document.addEventListener('strict-planning-refresh',event=>{
    if(!event.detail.online)status.textContent='Offline · last known college plan awaits refresh';
    else if(!dirty)load();
  });
  document.addEventListener('strict-planning-open',()=>{dirty=false;load();});
  if(date.value)load();
})();
