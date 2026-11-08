/** Embedded adaptations of the supplied desktop chrome; host-authoritative controls. */
import React,{useEffect,useRef} from 'react';
import {Lock,ShieldAlert,ShieldCheck,Minus,Square,X,Camera,ChevronUp,ChevronDown,Radio,Clock} from 'lucide-react';
import {useStrictSession} from '../../providers/StrictSessionContext';
import {postDesktop,reportDesktopLayout,useDesktopRuntime} from '../../services/DesktopRuntime';
import {formatSecondsToTimer} from '../../services/SessionEngine';

export function DesktopHeader({onRequestEndSessionModal}:{onRequestEndSessionModal:()=>void}) {
  const {state,openMonitoringSection}=useStrictSession();
  const runtime=useDesktopRuntime();
  const live=state.proctoring.monitoringActive;
  return <header data-testid="session-header" className="h-[38px] bg-[#0B0F17] border-b border-[#1E293B] px-3 flex items-center justify-between gap-3 select-none shrink-0">
    <div className="flex items-center gap-3 min-w-0">
      <div className="flex items-center gap-2 shrink-0"><div className="w-5 h-5 rounded bg-blue-600 flex items-center justify-center"><Lock className="w-3 h-3 text-white"/></div><h1 className="text-[13px] tracking-tight font-semibold whitespace-nowrap">StrictRecords <span className="text-slate-400 font-normal">— Strict Session</span></h1></div>
      <button type="button" onClick={()=>openMonitoringSection('OVERVIEW')} className={`hidden sm:flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] border whitespace-nowrap ${live?'text-green-400 border-green-800 bg-[#111C18]':'text-slate-300 border-slate-700 bg-slate-800/60'}`}>{live?<ShieldCheck className="w-3 h-3"/>:<ShieldAlert className="w-3 h-3"/>}<span>{runtime.offline?'Offline UI preview':live?'Strict Session Active':'Monitoring check required'}</span></button>
      <span className="hidden xl:block text-[11px] text-slate-400 px-2.5 py-0.5 rounded-full bg-[#151C28] border border-[#263347]">{state.browserRestrictions.sessionProfileLabel}</span>
    </div>
    <div className="flex items-center gap-2 shrink-0"><span className={`text-[10px] font-semibold rounded px-2 py-0.5 border ${state.meta.environmentMode==='DEMO'?'text-amber-300 border-amber-800 bg-amber-950/30':'text-green-400 border-green-900 bg-green-950/20'}`}>{runtime.offline?'OFFLINE DEMO':state.meta.environmentMode}</span><time className="hidden lg:block text-[11px] text-slate-400 tabular-nums">{state.meta.currentTimeFormatted}</time><div className="flex items-center gap-0.5 border-l border-slate-800 pl-1">
      <button type="button" aria-label="Minimize Strict Session" onClick={()=>postDesktop({type:'WINDOW_CONTROL',payload:{action:'MINIMIZE'}})} className="window-button"><Minus className="w-3.5 h-3.5"/></button>
      <button type="button" aria-label="Maximize Strict Session" onClick={()=>postDesktop({type:'WINDOW_CONTROL',payload:{action:'MAXIMIZE'}})} className="window-button"><Square className="w-3 h-3"/></button>
      <button type="button" aria-label="End Strict Session" onClick={onRequestEndSessionModal} className="window-button hover:text-red-400"><X className="w-3.5 h-3.5"/></button>
    </div></div>
  </header>;
}

export function DesktopFooter({onRequestEndSessionModal}:{onRequestEndSessionModal:()=>void}) {
  const {state,isMonitoringExpanded,setIsMonitoringExpanded,isCameraPipVisible,setIsCameraPipVisible,setIsCameraPipMinimized}=useStrictSession();
  const runtime=useDesktopRuntime();
  return <footer className="h-[48px] bg-[#0B0F17] border-t border-[#1E293B] px-3.5 flex items-center justify-between gap-3 shrink-0">
    <div className="flex items-center gap-3 min-w-0"><button type="button" data-testid="monitoring-toggle" aria-label="Toggle monitoring details" aria-expanded={isMonitoringExpanded} onClick={()=>setIsMonitoringExpanded(v=>!v)} className="w-8 h-8 rounded-lg border border-[#243044] flex items-center justify-center shrink-0 text-slate-300 hover:bg-slate-800">{isMonitoringExpanded?<ChevronDown className="w-4 h-4"/>:<ChevronUp className="w-4 h-4"/>}</button><p className="text-xs text-slate-400 truncate">Allowed: <span className="text-slate-200">{state.browserRestrictions.allowedDomains.join(', ')}</span><span className="hidden md:inline"> <span className="mx-3 text-slate-700">|</span>Focus: <span className="text-slate-200">{state.browserRestrictions.focusLabel}</span></span></p></div>
    <div className="flex items-center gap-3 shrink-0"><button type="button" data-testid="camera-restore" aria-label="Restore camera preview" onClick={()=>{setIsCameraPipVisible(true);setIsCameraPipMinimized(false);}} className={`flex items-center gap-1.5 text-xs px-2 py-1.5 rounded-md hover:bg-slate-800 ${isCameraPipVisible?'text-slate-500':'text-green-300'}`}><Camera className="w-4 h-4"/><span className="hidden lg:inline">Camera preview</span></button><span className="hidden xl:inline text-[10px] text-slate-500">{runtime.offline?'Preview only · no monitoring':'Synchronized with Windows'}</span><button type="button" data-testid="session-end" disabled={!runtime.endEnabled} onClick={onRequestEndSessionModal} className="px-4 py-1.5 rounded-xl bg-[#161116] border border-red-500/65 disabled:border-slate-700 disabled:text-slate-500 text-xs font-semibold text-white flex items-center gap-2 whitespace-nowrap"><span className="w-3 h-3 border-2 border-current rounded-sm"/>{state.meta.environmentMode==='DEMO'?'Exit Demo / Restore Desktop':'End Session'}</button></div>
  </footer>;
}

export function DesktopEndModal({isOpen,onClose}:{isOpen:boolean;onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);const runtime=useDesktopRuntime();const {state}=useStrictSession();
  useEffect(()=>{if(isOpen)dialog.current?.showModal();else dialog.current?.close();reportDesktopLayout();},[isOpen]);
  return <dialog data-testid="session-end-dialog" ref={dialog} onCancel={onClose} onClose={onClose} className="desktop-end-dialog w-[440px] max-w-[calc(100vw-32px)] bg-[#111722] text-slate-100 border border-[#283548] rounded-2xl p-0 shadow-2xl">
    <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between"><h2 className="text-sm font-semibold">{state.meta.environmentMode==='DEMO'?'Exit demo?':'End Strict Session?'}</h2><button type="button" aria-label="Close confirmation" onClick={onClose}><X className="w-4 h-4"/></button></div>
    <div className="p-5"><p className="text-xs text-slate-400 leading-relaxed">{runtime.endEnabled?'Windows will verify and finalize this request. The session remains active until the host confirms completion.':'The session cannot finish yet. Complete its study, proof and connection requirements first.'}</p><dl className="grid grid-cols-2 gap-3 mt-4"><div className="bg-[#0B0F17] rounded-lg p-3"><dt className="text-[10px] text-slate-500">Remaining</dt><dd className="font-mono mt-1">{formatSecondsToTimer(state.timer.remainingSeconds)}</dd></div><div className="bg-[#0B0F17] rounded-lg p-3"><dt className="text-[10px] text-slate-500">Violations</dt><dd className="font-mono mt-1">{state.proctoring.violationCount} / {state.proctoring.maxAllowedViolations}</dd></div></dl></div>
    <div className="border-t border-slate-800 px-5 py-3 flex justify-end gap-3"><button type="button" onClick={onClose} className="text-xs text-slate-300 px-3 py-1.5">Continue session</button><button type="button" data-testid="session-end-confirm" disabled={!runtime.endEnabled} onClick={()=>postDesktop({type:'SESSION_REQUEST_END'})} className="text-xs bg-red-600 disabled:bg-slate-800 disabled:text-slate-500 rounded-lg px-4 py-1.5">{state.meta.environmentMode==='DEMO'?'Exit Demo':'Request completion'}</button></div>
  </dialog>;
}

export function DesktopMonitoringDetails() {
  const {state,isMonitoringExpanded,setIsMonitoringExpanded}=useStrictSession();const runtime=useDesktopRuntime();
  if(!isMonitoringExpanded)return null;
  const h=runtime.hardware;
  const rows=[['Camera',h.camera],['Face',h.face_tracking],['Gaze',state.camera.gazeDirection],['Screen capture',state.screenCapture.screenShareActive],['Screen proctoring',state.proctoring.screenProctoringEnabled],['Bluetooth audio',h.bluetooth_headphone_connected],['Wired audio',h.wired_headphone_connected],['Speakers muted',h.speaker_muted],['iOS connected',runtime.status.ios_connected],['Android proof app',runtime.status.android_connected],['Notification filter',h.notification_filter_active],['Browser policy','chatgpt.com · leetcode.com']];
  const label=(value:unknown)=>value===true?'VERIFIED':value===false?'NOT ACTIVE':value==null?'UNVERIFIED':String(value);
  return <section data-testid="monitoring-details" aria-label="Expanded monitoring diagnostics" className="bg-[#0D121B] border-b border-[#243044] px-4 py-3 shrink-0 max-h-[35vh] overflow-auto"><div className="flex items-center justify-between mb-3"><h2 className="text-xs font-semibold flex items-center gap-2"><ShieldCheck className="w-4 h-4 text-slate-400"/>Runtime diagnostics <span className="text-[10px] text-slate-500 font-normal">Read-only · Windows verified state</span></h2><button type="button" aria-label="Close monitoring details" onClick={()=>setIsMonitoringExpanded(false)}><X className="w-4 h-4 text-slate-400"/></button></div><dl className="grid grid-cols-2 lg:grid-cols-4 gap-2">{rows.map(([key,value])=><div key={String(key)} className="bg-[#111925] rounded-lg px-3 py-2 border border-[#1E293B]"><dt className="text-[10px] text-slate-500">{String(key)}</dt><dd className="text-[11px] text-slate-200 mt-1 break-words">{runtime.offline && key!=='Browser policy'?'UNVERIFIED · offline preview':label(value)}</dd></div>)}</dl><p className="text-[10px] text-slate-500 mt-2">{String(h.proctor_message || 'Monitoring settings and session policy are managed by the existing Windows prerequisite checks.')}</p><p className="text-[10px] text-amber-300 mt-2">{String(h.visual_earbud_message || 'Visual earbud recognition is unavailable; connected audio devices are checked by Windows.')}</p><ol className="mt-3 space-y-1 text-xs">{state.proctoring.violations.slice(-12).reverse().map(event=><li key={event.id} className="rounded border border-amber-900/40 bg-amber-950/15 p-2 text-amber-200"><strong>{event.category.replaceAll('_',' ')}</strong> � {event.description}</li>)}</ol></section>;
}
