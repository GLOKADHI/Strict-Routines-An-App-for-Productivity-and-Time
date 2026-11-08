/** ZIP browser chrome over a real host-owned site WebView2, never an iframe. */
import React,{useEffect,useState} from 'react';
import {ArrowLeft,ArrowRight,ChevronDown,Lock,RotateCw,ShieldAlert,LoaderCircle,Globe} from 'lucide-react';
import {postDesktop,SiteKey,useDesktopRuntime} from '../../services/DesktopRuntime';
import {OpenAILogoGlyph} from './apps/ChatGptStudyView';
import {LeetCodeLogoGlyph} from './apps/LeetCodeStudyView';

export function DesktopBrowserPane({site}:{site:SiteKey}) {
  const runtime=useDesktopRuntime();
  const state=runtime.browser[site] || {state:'loading'};
  const title=site==='chatgpt'?'ChatGPT':"LeetCode — The World's Leading";
  const [address,setAddress]=useState(state.url || `https://${site}.com/`);
  useEffect(()=>{setAddress(state.url || `https://${site}.com/`);},[state.url,site]);
  const navigate=(action:'back'|'forward'|'reload'|'navigate')=>postDesktop({type:'NAVIGATE',pane:site,action,url:address});
  const icon=site==='chatgpt'?<OpenAILogoGlyph className="w-4 h-4 text-slate-200"/>:<LeetCodeLogoGlyph className="w-4 h-4"/>;
  const isReady=!runtime.offline && state.state==='ready';
  return <section aria-label={`${site} browser pane`} className="h-full w-full flex flex-col bg-[#12161F] border border-[#243044] rounded-xl overflow-hidden shadow-xl">
    <div className="h-9 bg-[#11151E] px-2 pt-1.5 flex items-center gap-2 border-b border-[#1E2636] shrink-0">
      <span className="w-6 h-6 rounded bg-[#1E2636] flex items-center justify-center text-slate-400" aria-hidden="true"><ChevronDown className="w-3.5 h-3.5"/></span>
      <div className="h-7 max-w-[260px] min-w-0 px-3 rounded-t-lg bg-[#202733] flex items-center gap-2 text-xs text-slate-100"><span className="shrink-0">{icon}</span><span className="truncate">{title}</span></div>
      <span className="text-[10px] text-slate-500 ml-auto px-2 whitespace-nowrap">Approved workspace</span>
    </div>
    <form className="h-10 px-2.5 bg-[#171D28] flex items-center gap-1.5 border-b border-[#263044] shrink-0" onSubmit={e=>{e.preventDefault();navigate('navigate');}}>
      <button type="button" aria-label={`Back in ${site}`} disabled={runtime.offline || !state.canGoBack} onClick={()=>navigate('back')} className="browser-icon"><ArrowLeft className="w-4 h-4"/></button>
      <button type="button" aria-label={`Forward in ${site}`} disabled={runtime.offline || !state.canGoForward} onClick={()=>navigate('forward')} className="browser-icon"><ArrowRight className="w-4 h-4"/></button>
      <button type="button" aria-label={`Reload ${site}`} disabled={runtime.offline} onClick={()=>navigate('reload')} className="browser-icon"><RotateCw className="w-4 h-4"/></button>
      <label className="flex items-center min-w-0 flex-1 rounded-full border border-[#283548] bg-[#0D121B] px-3 gap-2 h-7"><Lock className="w-3 h-3 text-slate-400 shrink-0"/><span className="sr-only">{title} address</span><input value={address} onChange={e=>setAddress(e.target.value)} readOnly={runtime.offline} className="w-full min-w-0 bg-transparent text-[11px] text-slate-200 outline-none select-text" spellCheck={false}/></label>
    </form>
    <div data-native-pane={site} data-state={isReady?'ready':runtime.offline?'offline':state.state} className="relative flex-1 min-h-0 overflow-auto bg-[#10151D]">
      {!isReady && <div role="status" className="absolute inset-0 min-h-[180px] flex items-center justify-center p-5">
        <div className="max-w-[290px] text-center">
          <span className="mx-auto mb-3 w-9 h-9 flex items-center justify-center rounded-xl bg-[#1A2431] text-slate-300">{runtime.offline?icon:state.state==='error'?<ShieldAlert className="w-5 h-5 text-amber-400"/>:<LoaderCircle className="w-5 h-5 animate-spin"/>}</span>
          <h2 className="text-sm font-semibold text-slate-100">{site==='chatgpt'?'ChatGPT':'LeetCode'}</h2>
          <p className="text-[11px] text-slate-500 mt-1 break-all">https://{site}.com</p>
          <p className={`text-[10px] font-semibold tracking-wider mt-3 ${runtime.offline?'text-amber-400':'text-slate-300'}`}>{runtime.offline?'OFFLINE DEMO':state.state==='error'?'CONNECTION NEEDS ATTENTION':'LOADING APPROVED WEBSITE'}</p>
          <p className="text-[11px] text-slate-400 mt-2 leading-relaxed">{runtime.offline?'Live content is unavailable in this preview.':state.error || 'Connecting to the real website in the secure study browser.'}</p>
          {!runtime.offline && state.state==='error' && <button type="button" data-testid={`retry-${site}`} onClick={()=>navigate('reload')} className="mt-3 px-4 py-1.5 rounded-md bg-[#153226] border border-green-800 text-xs text-green-300 flex items-center gap-2 mx-auto"><RotateCw className="w-3.5 h-3.5"/>Retry</button>}
        </div>
      </div>}
    </div>
  </section>;
}
