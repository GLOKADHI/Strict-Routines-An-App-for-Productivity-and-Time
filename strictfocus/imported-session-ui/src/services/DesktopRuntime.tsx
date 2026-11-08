/** Trusted local WebView2 presentation bridge. Session authority stays in Python. */
import React, {createContext, useCallback, useContext, useEffect, useState} from 'react';
import {IStrictSessionState} from '../types/strictSession';

export const isDesktopHost = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('host') === '1';
export type SiteKey = 'chatgpt' | 'leetcode';
export interface BrowserRuntime {state: string; url?: string; error?: string; canGoBack?: boolean; canGoForward?: boolean;}
export interface IOSCameraRuntime {state:string;streaming:boolean;last_frame_at:number|null;source:'iPhone camera';}
export interface IOSCameraPreview {jpeg:string|null;source:'iPhone camera';strictguard:'ACTIVE'|'UNVERIFIED';face:'VISIBLE'|'UNVERIFIED';observed_at:number|null;}
export interface DesktopRuntime {
  snapshot?: IStrictSessionState;
  status: Record<string, unknown>;
  hardware: Record<string, unknown>;
  offline: boolean;
  endEnabled: boolean;
  browser: Record<SiteKey, BrowserRuntime>;
  ios_camera?: IOSCameraRuntime;
  ios_camera_preview?: IOSCameraPreview;
  ios_invitation?: {success:boolean;error?:string;invitation?:{url:string;qr:string;expires_at:number}};
}
const initial: DesktopRuntime = {status:{},hardware:{},offline:true,endEnabled:false,browser:{chatgpt:{state:'loading'},leetcode:{state:'loading'}}};
const RuntimeContext = createContext(initial);
export function postDesktop(message: Record<string,unknown>) {
  if (isDesktopHost) window.chrome?.webview?.postMessage(message);
}
let queued = false;
export function measureDesktopLayout() {
  const rect = (node: Element | null) => {
    if (!node) return {x:0,y:0,width:0,height:0,visible:false};
    const r=node.getBoundingClientRect();
    return {x:r.x,y:r.y,width:r.width,height:r.height,visible:r.width>0 && r.height>0 && getComputedStyle(node).visibility !== 'hidden'};
  };
  return {type:'LAYOUT',viewport:{width:innerWidth,height:innerHeight},
    panes:{chatgpt:rect(document.querySelector('[data-native-pane="chatgpt"]')),leetcode:rect(document.querySelector('[data-native-pane="leetcode"]'))},
    camera:rect(document.querySelector('[data-camera-pip]')),
    overlayOpen:!!document.querySelector('dialog[open], [data-native-overlay="true"]')};
}
export function reportDesktopLayout() {
  if (!isDesktopHost || queued) return;
  queued = true;
  requestAnimationFrame(() => {
    queued=false;
    postDesktop(measureDesktopLayout());
  });
}
declare global {
  interface Window {
    StrictRecordsRuntime?: {apply:(payload:DesktopRuntime)=>void;getState:()=>DesktopRuntime;reportLayout:()=>void;getLayout:typeof measureDesktopLayout};
  }
}
export function DesktopRuntimeProvider({children}:{children:React.ReactNode}) {
  const [runtime,setRuntime]=useState(initial);
  const apply=useCallback((payload:DesktopRuntime)=> {
    if (!payload || typeof payload!=='object') return;
    setRuntime(prev=>({...prev,...payload,browser:{...prev.browser,...payload.browser}}));
  },[]);
  useEffect(()=> {
    if (!isDesktopHost) return;
    window.StrictRecordsRuntime={apply,getState:()=>runtime,reportLayout:reportDesktopLayout,getLayout:measureDesktopLayout};
    const listener=(event:{data:unknown})=> {
      try {
        const message=typeof event.data==='string'?JSON.parse(event.data):event.data;
        if (message?.type==='RUNTIME_STATE') apply(message.payload);
      } catch { /* Other site messages never enter this trusted view. */ }
    };
    window.chrome?.webview?.addEventListener('message',listener);
    return ()=>window.chrome?.webview?.removeEventListener('message',listener);
  },[apply,runtime]);
  useEffect(()=> {
    if (!isDesktopHost) return;
    const observer=new ResizeObserver(reportDesktopLayout);
    observer.observe(document.documentElement);
    const mutation=new MutationObserver(reportDesktopLayout);
    mutation.observe(document.getElementById('root')!,{childList:true,subtree:true,attributes:true,attributeFilter:['style','class','open']});
    window.addEventListener('resize',reportDesktopLayout);
    window.addEventListener('pointermove',reportDesktopLayout);
    postDesktop({type:'UI_READY'});reportDesktopLayout();
    return ()=>{observer.disconnect();mutation.disconnect();window.removeEventListener('resize',reportDesktopLayout);window.removeEventListener('pointermove',reportDesktopLayout);};
  },[]);
  useEffect(reportDesktopLayout,[runtime]);
  return <RuntimeContext.Provider value={runtime}>{children}</RuntimeContext.Provider>;
}
export const useDesktopRuntime=()=>useContext(RuntimeContext);
