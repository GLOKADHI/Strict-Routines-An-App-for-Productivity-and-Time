import React,{useEffect,useRef,useState} from 'react';
import {postDesktop,reportDesktopLayout,useDesktopRuntime} from '../../services/DesktopRuntime';

export function IOSCameraLink(){
 const runtime=useDesktopRuntime(),dialog=useRef<HTMLDialogElement>(null);
 const [waiting,setWaiting]=useState(false),[now,setNow]=useState(Date.now());
 useEffect(()=>{setWaiting(false);},[runtime.ios_invitation]);
 useEffect(()=>{const id=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(id);},[]);
 const response=runtime.ios_invitation,invite=response?.invitation;
 const expired=!!invite&&invite.expires_at*1000<=now;
 const create=()=>{setWaiting(true);postDesktop({type:'IOS_CAMERA_INVITE'});};
 const open=()=>{dialog.current?.showModal();reportDesktopLayout();if(!invite||expired)create();};
 return <>
  <button type="button" onClick={open} className="absolute bottom-2 left-2 z-20 rounded bg-slate-800 px-2 py-1 text-[10px] text-slate-100 border border-slate-600" title="Connect the iOS timer and camera">iOS QR / link</button>
  <dialog ref={dialog} aria-labelledby="ios-camera-link-title" onClose={reportDesktopLayout} className="ios-camera-link-dialog bg-[#151b23] text-slate-100 rounded-xl border border-slate-600 p-5 max-w-[480px] w-[calc(100vw-32px)] max-h-[85vh] overflow-auto">
   <h2 id="ios-camera-link-title" className="text-lg font-semibold">iOS timer &amp; camera</h2>
   <p className="text-sm text-slate-300 mt-2">Scan with your iPhone or iPad and open in Safari. The timer follows Windows. Tap Start Camera to allow video only.</p>
   <div role="status" className="my-4 text-sm">{waiting?'Creating a secure invitation…':response?.success===false?response.error:expired?'Invitation expired. Create a fresh QR.':invite?'Temporary invitation · expires '+new Date(invite.expires_at*1000).toLocaleTimeString():'No invitation yet.'}</div>
   {invite&&!expired&&!waiting&&<div className="grid gap-3"><img src={invite.qr} alt="Temporary iOS timer and camera QR" className="w-56 h-56 max-w-full mx-auto"/><input readOnly value={invite.url} aria-label="Temporary iOS invitation link" onFocus={event=>event.currentTarget.select()} className="w-full bg-slate-950 text-xs p-3 rounded border border-slate-600"/></div>}
   <p className="text-xs text-slate-400 my-3">Camera state: {runtime.ios_camera?.state?.replaceAll('_',' ')||'UNVERIFIED'}. The webcam and iPhone feeds remain separate. No microphone or video recording.</p>
   <div className="flex gap-3 justify-end"><button type="button" disabled={waiting} onClick={create} className="rounded bg-slate-700 px-3 py-2 text-sm disabled:opacity-50">Create new QR</button><form method="dialog"><button autoFocus className="rounded border border-slate-600 px-3 py-2 text-sm">Close</button></form></div>
  </dialog>
 </>;
}
