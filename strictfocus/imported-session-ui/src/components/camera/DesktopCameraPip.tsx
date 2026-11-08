/** Independent, source-labelled Windows and iPhone camera previews. */
import React,{useCallback,useEffect,useRef,useState} from 'react';
import {CameraOff,EyeOff,Maximize2,Minimize2,Move,X} from 'lucide-react';
import {useStrictSession} from '../../providers/StrictSessionContext';
import {reportDesktopLayout,useDesktopRuntime} from '../../services/DesktopRuntime';
import {IOSCameraLink} from './IOSCameraLink';

interface Geometry{x:number;y:number;width:number;height:number;}
const KEY='strictrecords.zip.camera.geometry.v1';
const initial=():Geometry=>{try{const value=JSON.parse(localStorage.getItem(KEY)||'null');if(value && ['x','y','width','height'].every(k=>typeof value[k]==='number'&&Number.isFinite(value[k])) && value.width>0 && value.height>0)return value;}catch{}return{x:Math.max(12,innerWidth-264),y:150,width:240,height:135};};
export function DesktopCameraPip() {
  const {state,isCameraPipVisible,setIsCameraPipVisible,isCameraPipMinimized,setIsCameraPipMinimized}=useStrictSession();
  const runtime=useDesktopRuntime();const [geometry,setGeometry]=useState(initial);const [dragging,setDragging]=useState(false);
  const [source,setSource]=useState<'WINDOWS'|'IPHONE'>('WINDOWS');
  const gesture=useRef<{direction:string;x:number;y:number;geometry:Geometry}|null>(null);
  const bounds=useCallback(()=>{
    const r=document.querySelector('[data-study-workspace]')?.getBoundingClientRect();
    return{x:r?.left??0,y:r?.top??100,right:r?.right??innerWidth,bottom:r?.bottom??innerHeight-48};
  },[]);
  const clamp=useCallback((g:Geometry)=>{
    const r=bounds();const available=Math.max(80,Math.min(r.right-r.x-24,(r.bottom-r.y-24)*16/9));
    const width=Math.min(480,available,Math.max(Math.min(180,available),g.width));const height=Math.round(width*9/16);
    return{x:Math.max(r.x+12,Math.min(r.right-width-12,g.x)),y:Math.max(r.y+12,Math.min(r.bottom-height-12,g.y)),width,height};
  },[bounds]);
  useEffect(()=>{
    const resize=()=>setGeometry(g=>clamp(g));
    const observer=new ResizeObserver(resize);const workspace=document.querySelector('[data-study-workspace]');if(workspace)observer.observe(workspace);
    window.addEventListener('resize',resize);resize();return()=>{window.removeEventListener('resize',resize);observer.disconnect();};
  },[clamp]);
  useEffect(()=>{
    const move=(e:PointerEvent)=>{
      const start=gesture.current;if(!start)return;
      const dx=e.clientX-start.x,dy=e.clientY-start.y;const g=start.geometry;
      if(start.direction==='drag'){setGeometry(clamp({...g,x:g.x+dx,y:g.y+dy}));return;}
      const d=start.direction;let delta=d.includes('w')?-dx:d.includes('e')?dx:d.includes('n')?-dy*16/9:dy*16/9;
      if(d.length===2 && Math.abs(dy*16/9)>Math.abs(dx))delta=d.includes('n')?-dy*16/9:dy*16/9;
      const w=Math.max(180,Math.min(480,g.width+delta));const h=Math.round(w*9/16);
      setGeometry(clamp({x:d.includes('w')?g.x+g.width-w:d.includes('e')?g.x:g.x+(g.width-w)/2,y:d.includes('n')?g.y+g.height-h:d.includes('s')?g.y:g.y+(g.height-h)/2,width:w,height:h}));
    };
    const up=()=>{gesture.current=null;setDragging(false);};
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);
    return()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);};
  },[clamp]);
  useEffect(()=>{try{localStorage.setItem(KEY,JSON.stringify(geometry));}catch{}reportDesktopLayout();},[geometry,isCameraPipVisible,isCameraPipMinimized]);
  const start=(e:React.PointerEvent,direction:string)=>{if(e.button!==0)return;e.preventDefault();e.stopPropagation();gesture.current={direction,x:e.clientX,y:e.clientY,geometry};setDragging(true);try{e.currentTarget.setPointerCapture(e.pointerId);}catch{}};
  if(!isCameraPipVisible)return null;
  const iphone=source==='IPHONE';
  const iosStatus=runtime.ios_camera,iosPreview=runtime.ios_camera_preview;
  const iosFrame=!runtime.offline&&iosStatus?.streaming===true&&iosStatus.state==='IOS_CAMERA_STREAMING'&&iosPreview?.source==='iPhone camera'&&iosPreview.jpeg?`data:image/jpeg;base64,${iosPreview.jpeg}`:null;
  const frame=iphone?iosFrame:!runtime.offline && state.camera.sourceType==='HOST_NATIVE_STREAM'?state.camera.externalFrameDataUrl:null;
  const live=!!frame && (iphone?iosStatus?.streaming===true:state.camera.isStreaming && state.camera.providerStatus==='CONNECTED');
  const sourceLabel=iphone?'iPhone camera':'Windows webcam';
  const previewDetail=iphone?`${sourceLabel} · ${iosStatus?.state||'UNVERIFIED'} · StrictGuard ${iosPreview?.strictguard||'UNVERIFIED'}${iosStatus?.last_frame_at?' · last frame '+new Date(iosStatus.last_frame_at*1000).toLocaleTimeString():''}`:sourceLabel;
  return <aside data-camera-pip data-testid="camera-pip" aria-label="Floating camera preview" style={{left:geometry.x,top:geometry.y,width:geometry.width,height:isCameraPipMinimized?40:geometry.height}} className={`fixed z-30 rounded-xl overflow-hidden select-none border-2 ${live?'border-[#22C55E]':'border-slate-600'} bg-[#0D131C] shadow-xl ${dragging?'cursor-grabbing':''}`}>
    <div data-testid="camera-drag" onPointerDown={e=>start(e,'drag')} className="absolute inset-x-0 top-0 h-7 flex items-center justify-between px-2 z-20 bg-black/65 cursor-grab">
      <span className={`text-[9px] font-bold flex items-center gap-1.5 ${live?'text-green-400':'text-slate-400'}`}><span className={`w-1.5 h-1.5 rounded-full ${live?'bg-green-400':'bg-slate-500'}`}/>{runtime.offline?'DEMO':live?'LIVE':'UNVERIFIED'}</span>
      <select aria-label="Camera preview source" title={previewDetail} value={source} onChange={event=>setSource(event.target.value as 'WINDOWS'|'IPHONE')} onPointerDown={event=>event.stopPropagation()} className="min-w-0 max-w-[105px] bg-transparent text-[9px] text-slate-200 outline-none" style={{colorScheme:'dark'}}><option value="WINDOWS">Windows webcam</option><option value="IPHONE">iPhone camera</option></select>
      <div className="flex items-center gap-1" onPointerDown={e=>e.stopPropagation()}><button type="button" data-testid="camera-hide" aria-label="Hide camera preview" onClick={()=>setIsCameraPipVisible(false)} className="camera-action"><EyeOff className="w-3 h-3"/></button><button type="button" aria-label={isCameraPipMinimized?'Expand camera preview':'Minimize camera preview'} onClick={()=>setIsCameraPipMinimized(v=>!v)} className="camera-action">{isCameraPipMinimized?<Maximize2 className="w-3 h-3"/>:<Minimize2 className="w-3 h-3"/>}</button></div>
    </div>
    {!isCameraPipMinimized && <div className="w-full h-full flex items-center justify-center" title={previewDetail}>{frame?<img src={frame} alt={`Current ${sourceLabel} preview`} width={geometry.width} height={geometry.height} className="w-full h-full object-cover"/>:<div className="px-3 pt-6 text-center"><CameraOff className="w-5 h-5 text-slate-500 mx-auto mb-1.5"/><p className="text-[9px] tracking-wide text-slate-400 leading-relaxed">{runtime.offline?'CAMERA PREVIEW UNAVAILABLE — DEMO':iphone?`iPhone camera · ${iosStatus?.state?.replaceAll('IOS_','').replaceAll('_',' ')||'NOT CONNECTED'}`:'Waiting for a verified Windows webcam frame'}</p></div>}</div>}
    {!isCameraPipMinimized&&<IOSCameraLink/>}
    {!isCameraPipMinimized && (['n','s','e','w','nw','ne','sw','se'] as const).map(d=><div key={d} data-testid={`camera-resize-${d}`} aria-label={`Resize camera ${d}`} onPointerDown={e=>start(e,d)} style={{position:'absolute',zIndex:40,...(d.includes('n')?{top:0}:d.includes('s')?{bottom:0}:{top:7,bottom:7}),...(d.includes('w')?{left:0}:d.includes('e')?{right:0}:{left:7,right:7}),width:d.includes('w')||d.includes('e')?7:undefined,height:d.includes('n')||d.includes('s')?7:undefined,cursor:`${d}-resize`}}/>)}
  </aside>;
}
