"""One QR enrolls the existing timer; camera permission stays a separate action."""
BOOTSTRAP = r'''
(async()=>{
 const key='strictrecords.ios.join.pending.v1',bindingKey='strictrecords.timer.binding.v2';
 let pending=null;try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch(_){}
 const token=location.hash.slice(1)||pending?.token||'';
 const pid=new URLSearchParams(location.search).get('preparation_id')||'';
 history.replaceState(null,'',location.pathname+'?preparation_id='+encodeURIComponent(pid));
 try{
  let binding=null;
  if(token){
   let nonce=pending?.token===token?pending.nonce:null;
   if(!nonce){const bytes=new Uint8Array(32);crypto.getRandomValues(bytes);nonce=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');}
   try{sessionStorage.setItem(key,JSON.stringify({token,nonce}));}catch(_){}
   const response=await fetch('/api/strict_session/preparation/join',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,client_nonce:nonce,client_info:{platform:navigator.platform||'',max_touch_points:navigator.maxTouchPoints||0}}),cache:'no-store'});
   binding=await response.json();if(!response.ok||!binding.success)throw Error(binding.error||'Joining failed.');
   if(binding.role!=='IOS'||binding.preparation_id!==pid)throw Error('Invitation does not match this iOS session.');
   binding.expires_at=Date.now()+43200000;
   try{localStorage.setItem(bindingKey,JSON.stringify(binding));}catch(_){}
   try{sessionStorage.removeItem(key);}catch(_){}
  }else{
   try{binding=JSON.parse(localStorage.getItem(bindingKey)||'null');}catch(_){}
   if(!binding||binding.preparation_id!==pid||binding.expires_at<Date.now())throw Error('Open a fresh iOS QR from Windows.');
  }
  // Enrollment and camera grants are different capabilities. Never pass the
  // consumed timer token into the camera redemption path, including old tabs.
  window.strictIOSTimerBinding=binding;
  try{sessionStorage.removeItem('strictrecords.ios.join.token');}catch(_){}
  // Import only first-party code. Start Camera still requires an explicit click.
  await import('/ios-camera/client.js');
 }catch(error){
  document.getElementById('cameraState').textContent='Timer enrollment failed: '+error.message;
  document.getElementById('cameraHelp').textContent='Open a fresh QR directly in Safari. Camera stays off until this iPad joins Windows.';
  document.getElementById('startCamera').disabled=true;
 }
})();
'''


def create_join_invitation(manager, account, pid):
    import base64
    import io
    import qrcode
    gateway = manager.ios_camera_gateway
    if not gateway or not gateway.ready:
        raise ValueError(gateway.error if gateway else manager.ios_camera_configuration_error)
    # Do not silently replace an unrelated preparation when an old UI sends a PID.
    current = manager.preparation.current(account)
    if current['preparation_id'] != pid:
        raise PermissionError('Open the current Strict Session before creating its iOS QR.')
    result = manager.preparation.invite(account, 'IOS', gateway.origin, ttl=180)
    invitation = result['invitation']
    token = invitation['url'].split('#', 1)[1]
    from urllib.parse import quote
    invitation['url'] = gateway.origin + '/strict-session/companion?preparation_id=' + quote(pid, safe='') + '#' + token
    image = io.BytesIO()
    qrcode.make(invitation['url']).save(image, format='PNG')
    invitation['qr'] = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()
    invitation['purpose'] = 'IOS_TIMER_AND_CAMERA'
    invitation['https_verified'] = True
    invitation['verification_details'] = getattr(gateway, 'verification_details', {})
    manager.preparation.links[(pid, 'IOS')] = invitation
    return result
