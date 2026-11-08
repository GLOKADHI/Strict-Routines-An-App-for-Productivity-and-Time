import React,{useEffect,useRef,useState} from 'react';
import {ArrowLeftRight,Columns2,Maximize2,RotateCcw} from 'lucide-react';
import {DesktopBrowserPane} from './DesktopBrowserPane';
import {reportDesktopLayout,SiteKey,useDesktopRuntime} from '../../services/DesktopRuntime';

export function DesktopSplitWorkspace({children}:{children?:React.ReactNode}) {
  const [ratio,setRatio]=useState(50);const [swapped,setSwapped]=useState(false);const [focus,setFocus]=useState<SiteKey|null>(null);
  const root=useRef<HTMLElement>(null);const drag=useRef(false);const runtime=useDesktopRuntime();
  useEffect(()=>{
    const move=(e:PointerEvent)=>{if(!drag.current || !root.current)return;const r=root.current.getBoundingClientRect();setRatio(Math.max(25,Math.min(75,(e.clientX-r.left)/r.width*100)));};
    const up=()=>{drag.current=false;};window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);
    return()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);};
  },[]);
  useEffect(reportDesktopLayout,[ratio,swapped,focus]);
  const sites:SiteKey[]=swapped?['leetcode','chatgpt']:['chatgpt','leetcode'];
  const buttons=[['split','Side by Side',()=>setFocus(null),Columns2],['swap','Swap Panes',()=>setSwapped(v=>!v),ArrowLeftRight],['chatgpt','Focus ChatGPT',()=>setFocus('chatgpt'),Maximize2],['leetcode','Focus LeetCode',()=>setFocus('leetcode'),Maximize2],['restore','Restore Split View',()=>{setRatio(50);setSwapped(false);setFocus(null);},RotateCcw]] as const;
  return <>
    {runtime.offline && <div className="h-6 px-4 flex items-center text-[10px] text-amber-300/80 bg-[#241E14] border-b border-amber-900/20 shrink-0">OFFLINE DEMO · No real monitoring or session is running.</div>}
    <nav aria-label="Browser layout" className="desktop-layout-toolbar shrink-0 h-9 px-3 flex items-center justify-center gap-1.5 bg-[#0B1018] border-b border-[#1E293B]">{buttons.map(([id,label,action,Icon])=><button type="button" key={id} data-testid={`layout-${id}`} aria-label={label} onClick={action} className="px-2.5 py-1 rounded-md border border-[#263347] bg-[#151D2A] hover:bg-[#1E293B] text-[10px] text-slate-300 flex items-center gap-1.5 whitespace-nowrap"><Icon className="w-3 h-3"/><span>{label}</span></button>)}</nav>
    <main ref={root} data-study-workspace aria-label="Split restricted browser workspace" className="flex-1 relative min-h-0 p-2.5 gap-0 overflow-hidden bg-[#080B11] grid" style={{gridTemplateColumns:focus?'minmax(0,1fr)':`minmax(0,${ratio}fr) 16px minmax(0,${100-ratio}fr)`}}>
      {sites.map((site,index)=><React.Fragment key={site}>
        <div className="h-full min-h-0 min-w-0 overflow-hidden" style={{display:focus && focus!==site?'none':undefined}}><DesktopBrowserPane site={site}/></div>
        {index===0 && !focus && <div className="h-full flex items-center justify-center relative shrink-0"><div className="absolute inset-y-0 w-px bg-[#243044]"/><button type="button" data-testid="layout-divider" role="separator" aria-orientation="vertical" aria-valuenow={Math.round(ratio)} aria-valuemin={25} aria-valuemax={75} aria-label="Resize browser panes" onKeyDown={e=>{if(e.key==='ArrowLeft'){e.preventDefault();setRatio(v=>Math.max(25,v-2));}if(e.key==='ArrowRight'){e.preventDefault();setRatio(v=>Math.min(75,v+2));}if(e.key==='Home'){e.preventDefault();setRatio(50);}}} onPointerDown={e=>{drag.current=true;try{e.currentTarget.setPointerCapture(e.pointerId);}catch{}e.preventDefault();}} onDoubleClick={()=>setRatio(50)} className="absolute w-7 h-7 rounded-full bg-[#151D2B] hover:bg-[#1E293D] border border-[#33435E] text-slate-200 flex items-center justify-center shadow-lg cursor-ew-resize z-10"><ArrowLeftRight className="w-3.5 h-3.5"/></button></div>}
      </React.Fragment>)}
      {children}
    </main>
  </>;
}
