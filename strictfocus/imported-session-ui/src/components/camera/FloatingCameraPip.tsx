/**
 * StrictRecords — Floating Draggable, Resizable & Minimizable Camera PIP Component
 *
 * Features:
 * - In-place Minimize mode: Collapses into a compact, draggable 38px status pill
 *   (`[ ● LIVE | Camera · Gaze: Center | Restore ]`) so it never obstructs browser controls
 * - Dock-to-Bar / Hide mode (`X`): Docks completely to the Monitoring Bar / Footer with 1-click restore
 * - Smooth pointer-based dragging across the dual-browser workspace
 * - Corner grip resizing within desktop bounds
 * - Supports 3 distinct video sources:
 *   1. Windows EXE Host Native Frame Stream (`externalFrameDataUrl`)
 *   2. Real Browser Hardware Webcam (`navigator.mediaDevices.getUserMedia`)
 *   3. Clearly labeled Demo Simulation Feed (ONLY in DEMO mode; never shown in PRODUCTION mode)
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Camera,
  CameraOff,
  Eye,
  EyeOff,
  Maximize2,
  Minimize2,
  Minus,
  Move,
  ScanFace,
  X,
} from 'lucide-react';
import { useStrictSession } from '../../providers/StrictSessionContext';
import { STRICT_TOKENS } from '../../tokens/designTokens';
import {isDesktopHost} from '../../services/DesktopRuntime';
import {DesktopCameraPip} from './DesktopCameraPip';

export const FloatingCameraPip: React.FC = () => {
  if(isDesktopHost)return <DesktopCameraPip/>;
  const {
    state,
    isCameraPipVisible,
    setIsCameraPipVisible,
    isCameraPipMinimized,
    setIsCameraPipMinimized,
    toggleCameraPrivacyMask,
    setCameraSourceType,
  } = useStrictSession();

  const { camera, meta } = state;

  // Position (relative to top-right of workspace by default, matching screenshot)
  const [position, setPosition] = useState<{ x: number; y: number }>(() => ({
    x: typeof window !== 'undefined' ? Math.max(24, window.innerWidth - 290) : 960,
    y: 6,
  }));

  const [size, setSize] = useState<{ width: number; height: number }>({
    width: STRICT_TOKENS.layout.cameraDefaultWidth,
    height: STRICT_TOKENS.layout.cameraDefaultHeight,
  });

  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [isEnlarged, setIsEnlarged] = useState(false);
  const [webcamError, setWebcamError] = useState<string | null>(null);

  const dragOffsetRef = useRef<{ startX: number; startY: number; origX: number; origY: number }>({
    startX: 0,
    startY: 0,
    origX: 0,
    origY: 0,
  });

  const resizeStartRef = useRef<{
    startX: number;
    startY: number;
    origW: number;
    origH: number;
  }>({
    startX: 0,
    startY: 0,
    origW: STRICT_TOKENS.layout.cameraDefaultWidth,
    origH: STRICT_TOKENS.layout.cameraDefaultHeight,
  });

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);

  const effectiveWidth = isCameraPipMinimized ? 236 : size.width;
  const effectiveHeight = isCameraPipMinimized ? 40 : size.height;

  // Keep camera inside viewport on window resize
  useEffect(() => {
    const handleResize = () => {
      setPosition((prev) => ({
        x: Math.min(prev.x, Math.max(16, window.innerWidth - effectiveWidth - 16)),
        y: Math.min(prev.y, Math.max(6, window.innerHeight - effectiveHeight - 60)),
      }));
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [effectiveWidth, effectiveHeight]);

  // Manage real hardware webcam stream when `camera.sourceType === 'BROWSER_WEBCAM'`
  useEffect(() => {
    let cancelled = false;

    async function startWebcam() {
      setWebcamError(null);
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 640 }, height: { ideal: 360 } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        mediaStreamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
        }
      } catch (err) {
        if (!cancelled) {
          setWebcamError(
            err instanceof Error ? err.message : 'Camera permission denied or unavailable'
          );
        }
      }
    }

    if (camera.sourceType === 'BROWSER_WEBCAM' && isCameraPipVisible && !isCameraPipMinimized) {
      startWebcam();
    } else {
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((t) => t.stop());
        mediaStreamRef.current = null;
      }
    }

    return () => {
      cancelled = true;
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((t) => t.stop());
        mediaStreamRef.current = null;
      }
    };
  }, [camera.sourceType, isCameraPipVisible, isCameraPipMinimized]);

  // Pointer drag & resize listeners
  const handlePointerMove = useCallback(
    (e: PointerEvent) => {
      if (isDragging) {
        const dx = e.clientX - dragOffsetRef.current.startX;
        const dy = e.clientY - dragOffsetRef.current.startY;
        const maxX = Math.max(12, window.innerWidth - effectiveWidth - 12);
        const maxY = Math.max(4, window.innerHeight - effectiveHeight - 54);
        setPosition({
          x: Math.min(maxX, Math.max(12, dragOffsetRef.current.origX + dx)),
          y: Math.min(maxY, Math.max(4, dragOffsetRef.current.origY + dy)),
        });
      } else if (isResizing && !isCameraPipMinimized) {
        const dx = e.clientX - resizeStartRef.current.startX;
        const dy = e.clientY - resizeStartRef.current.startY;
        const nextW = Math.min(
          STRICT_TOKENS.layout.cameraMaxWidth,
          Math.max(STRICT_TOKENS.layout.cameraMinWidth, resizeStartRef.current.origW + dx)
        );
        const nextH = Math.min(
          STRICT_TOKENS.layout.cameraMaxHeight,
          Math.max(STRICT_TOKENS.layout.cameraMinHeight, resizeStartRef.current.origH + dy)
        );
        setSize({ width: nextW, height: nextH });
      }
    },
    [isDragging, isResizing, isCameraPipMinimized, effectiveWidth, effectiveHeight]
  );

  const handlePointerUp = useCallback(() => {
    setIsDragging(false);
    setIsResizing(false);
  }, []);

  useEffect(() => {
    if (!isDragging && !isResizing) return;
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
  }, [isDragging, isResizing, handlePointerMove, handlePointerUp]);

  const handleToggleSizePreset = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isCameraPipMinimized) {
      setIsCameraPipMinimized(false);
      return;
    }
    if (isEnlarged) {
      setSize({
        width: STRICT_TOKENS.layout.cameraDefaultWidth,
        height: STRICT_TOKENS.layout.cameraDefaultHeight,
      });
      setIsEnlarged(false);
    } else {
      const nextW = 340;
      const nextH = 210;
      setSize({ width: nextW, height: nextH });
      setPosition((prev) => ({
        x: Math.min(prev.x, Math.max(12, window.innerWidth - nextW - 16)),
        y: prev.y,
      }));
      setIsEnlarged(true);
    }
  };

  if (!isCameraPipVisible) {
    return null;
  }

  const isLiveActive = camera.isStreaming && camera.providerStatus === 'CONNECTED';
  const showDemoSimulatedFeed =
    meta.environmentMode === 'DEMO' && camera.sourceType === 'DEMO_SIMULATED';

  // ==========================================================================
  // COMPACT MINIMIZED PILL STATE
  // ==========================================================================
  if (isCameraPipMinimized) {
    return (
      <div
        style={{
          left: `${position.x}px`,
          top: `${position.y}px`,
          width: `${effectiveWidth}px`,
          height: `${effectiveHeight}px`,
        }}
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).closest('button')) return;
          setIsDragging(true);
          dragOffsetRef.current = {
            startX: e.clientX,
            startY: e.clientY,
            origX: position.x,
            origY: position.y,
          };
        }}
        onDoubleClick={() => setIsCameraPipMinimized(false)}
        title="Drag to reposition · Double-click to restore camera preview"
        className={`fixed z-30 rounded-full px-3 flex items-center justify-between gap-2 select-none transition-shadow ${
          isDragging ? 'cursor-grabbing' : 'cursor-grab'
        } ${
          isLiveActive
            ? 'bg-[#0D1714]/95 border-2 border-[#22C55E] shadow-[0_0_18px_rgba(34,197,94,0.28)]'
            : 'bg-[#111722]/95 border-2 border-slate-600 shadow-xl'
        } backdrop-blur-md`}
      >
        {/* Left: Live Status Dot + Gaze Label */}
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`w-2.5 h-2.5 rounded-full shrink-0 ${
              isLiveActive ? 'bg-[#22C55E] shadow-[0_0_6px_#22C55E]' : 'bg-slate-400'
            }`}
          />
          <span className="text-[11px] font-bold text-white whitespace-nowrap">
            {isLiveActive ? 'LIVE' : 'CAM'}
          </span>
          <span className="text-slate-500">·</span>
          <span className="text-[11px] text-slate-300 truncate">
            {camera.gazeDirection === 'CENTER'
              ? 'Gaze: Center'
              : `Gaze: ${camera.gazeDirection}`}
          </span>
        </div>

        {/* Right: Restore & Dock Buttons */}
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setIsCameraPipMinimized(false);
            }}
            title="Restore Camera Preview"
            className="w-6 h-6 rounded-full bg-[#1C2838] hover:bg-[#22C55E] text-slate-200 hover:text-slate-950 flex items-center justify-center transition-colors cursor-pointer"
          >
            <Maximize2 className="w-3 h-3" />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setIsCameraPipVisible(false);
            }}
            title="Hide Camera Preview to Bar"
            className="w-6 h-6 rounded-full hover:bg-red-500/20 text-slate-400 hover:text-red-400 flex items-center justify-center transition-colors cursor-pointer"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    );
  }

  // ==========================================================================
  // FULL EXPANDED / RESIZABLE PIP VIDEO PREVIEW
  // ==========================================================================
  return (
    <div
      style={{
        left: `${position.x}px`,
        top: `${position.y}px`,
        width: `${size.width}px`,
        height: `${size.height}px`,
      }}
      onPointerDown={(e) => {
        if ((e.target as HTMLElement).closest('button')) return;
        setIsDragging(true);
        dragOffsetRef.current = {
          startX: e.clientX,
          startY: e.clientY,
          origX: position.x,
          origY: position.y,
        };
      }}
      onDoubleClick={() => setIsCameraPipMinimized(true)}
      className={`fixed z-30 rounded-2xl overflow-hidden select-none transition-shadow group ${
        isDragging ? 'cursor-grabbing' : 'cursor-grab'
      } ${
        isLiveActive
          ? 'border-2 border-[#22C55E] shadow-[0_0_24px_rgba(34,197,94,0.32)]'
          : 'border-2 border-slate-600 shadow-2xl'
      } bg-[#0D131C]`}
    >
      {/* Top Overlay Controls Bar */}
      <div className="absolute top-2 left-2.5 right-2.5 flex items-center justify-between z-20 pointer-events-none">
        {/* Top-Left LIVE / Status Pill */}
        <div
          className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold flex items-center gap-1.5 shadow-md pointer-events-auto ${
            isLiveActive
              ? 'bg-[#22C55E] text-white'
              : 'bg-slate-800/90 text-slate-300 border border-slate-600'
          }`}
        >
          <span
            className={`w-2 h-2 rounded-full ${
              isLiveActive ? 'bg-white' : 'bg-slate-400'
            }`}
          />
          <span>{isLiveActive ? 'LIVE' : 'STANDBY'}</span>
        </div>

        {/* Top-Right Action Capsule (`Privacy Mask`, `Minimize (—)`, `Expand`, `Close`) */}
        <div className="bg-black/65 backdrop-blur-md border border-white/15 rounded-full px-2.5 py-1 flex items-center gap-2 pointer-events-auto">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              toggleCameraPrivacyMask();
            }}
            title={camera.isPrivacyMasked ? 'Unmask Camera Feed' : 'Privacy Blur Camera'}
            className="text-slate-200 hover:text-white transition-colors cursor-pointer"
          >
            {camera.isPrivacyMasked ? (
              <Eye className="w-3.5 h-3.5 text-amber-400" />
            ) : (
              <EyeOff className="w-3.5 h-3.5" />
            )}
          </button>

          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setIsCameraPipMinimized(true);
            }}
            title="Minimize Camera Preview to Compact Pill"
            className="text-slate-200 hover:text-[#22C55E] transition-colors cursor-pointer"
          >
            <Minus className="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            onClick={handleToggleSizePreset}
            title={isEnlarged ? 'Restore Default PIP Size' : 'Enlarge PIP Size'}
            className="text-slate-200 hover:text-white transition-colors cursor-pointer"
          >
            {isEnlarged ? (
              <Minimize2 className="w-3.5 h-3.5" />
            ) : (
              <Maximize2 className="w-3.5 h-3.5" />
            )}
          </button>

          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setIsCameraPipVisible(false);
            }}
            title="Hide Camera Preview to Bar"
            className="text-slate-200 hover:text-red-400 transition-colors cursor-pointer"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Main Camera Viewport */}
      <div
        className={`w-full h-full relative flex items-center justify-center overflow-hidden ${
          camera.isPrivacyMasked ? 'blur-md scale-105' : ''
        } transition-all duration-200`}
      >
        {/* Case 1: Real Hardware Webcam */}
        {camera.sourceType === 'BROWSER_WEBCAM' && (
          <>
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className="w-full h-full object-cover transform -scale-x-100"
            />
            {webcamError && (
              <div className="absolute inset-0 bg-[#0E141E] p-3 flex flex-col items-center justify-center text-center">
                <CameraOff className="w-6 h-6 text-red-400 mb-1" />
                <p className="text-[10px] text-slate-300 leading-tight">{webcamError}</p>
                {meta.environmentMode === 'DEMO' && (
                  <button
                    type="button"
                    onClick={() => setCameraSourceType('DEMO_SIMULATED')}
                    className="mt-2 px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-[10px] text-slate-200 cursor-pointer"
                  >
                    Revert to Demo Feed
                  </button>
                )}
              </div>
            )}
          </>
        )}

        {/* Case 2: Windows EXE Host Pushed Frame Stream */}
        {camera.sourceType === 'HOST_NATIVE_STREAM' && camera.externalFrameDataUrl && (
          <img
            src={camera.externalFrameDataUrl}
            alt="Host Native Proctoring Feed"
            referrerPolicy="no-referrer"
            className="w-full h-full object-cover"
          />
        )}

        {/* Case 3: Demo Mode Simulated Feed (NEVER rendered in Production Mode) */}
        {showDemoSimulatedFeed && (
          <div className="w-full h-full relative bg-gradient-to-b from-[#2B353E] via-[#1E262F] to-[#141A22] flex flex-col items-center justify-end overflow-hidden">
            {/* Ambient room studio background elements */}
            <div className="absolute inset-x-0 top-0 h-1/3 bg-gradient-to-b from-slate-300/15 to-transparent" />
            <div className="absolute left-4 top-6 bottom-0 w-7 bg-[#1A382E]/50 border-r border-emerald-900/30" />

            {/* Stylized Study Subject Silhouette + Proctoring Face Tracking Reticle */}
            <div className="relative z-10 flex flex-col items-center mb-[-4px]">
              {/* Face Tracking Bounding Box */}
              <div className="relative w-16 h-20 flex flex-col items-center justify-center">
                {/* Green Face Mesh Corner Brackets */}
                <span className="absolute top-0 left-0 w-2.5 h-2.5 border-t-2 border-l-2 border-[#22C55E]" />
                <span className="absolute top-0 right-0 w-2.5 h-2.5 border-t-2 border-r-2 border-[#22C55E]" />
                <span className="absolute bottom-0 left-0 w-2.5 h-2.5 border-b-2 border-l-2 border-[#22C55E]" />
                <span className="absolute bottom-0 right-0 w-2.5 h-2.5 border-b-2 border-r-2 border-[#22C55E]" />

                {/* Head & Hair Silhouette */}
                <div className="w-11 h-13 rounded-full bg-[#B58969] relative flex flex-col items-center overflow-hidden shadow-inner">
                  <div className="w-full h-4 bg-[#1E1B18] rounded-t-full" />
                  {/* Eyes shifting with gazeDirection */}
                  <div
                    className={`flex items-center gap-2.5 mt-2.5 transition-transform duration-200 ${
                      camera.gazeDirection === 'LEFT'
                        ? '-translate-x-1'
                        : camera.gazeDirection === 'RIGHT'
                          ? 'translate-x-1'
                          : camera.gazeDirection === 'AWAY'
                            ? 'translate-y-1 opacity-40'
                            : ''
                    }`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-[#1E1B18]" />
                    <span className="w-1.5 h-1.5 rounded-full bg-[#1E1B18]" />
                  </div>
                  <div className="w-3 h-1 border-b border-[#6E4730] mt-2.5 rounded-full" />
                </div>
              </div>

              {/* Shoulders / Coral Hoodie matching screenshot */}
              <div className="w-36 h-12 rounded-t-3xl bg-gradient-to-b from-[#D96B52] to-[#B54B35] shadow-md flex items-start justify-center pt-1.5">
                <div className="w-7 h-3 rounded-b-full bg-[#242933]" />
              </div>
            </div>

            {/* Bottom-left Demo Mode Indicator & Real Webcam Switcher */}
            <div className="absolute bottom-1.5 left-2 right-7 flex items-center justify-between z-20">
              <span className="px-1.5 py-0.5 rounded bg-black/60 text-[9px] font-mono text-amber-300 border border-amber-500/30">
                DEMO FEED · Gaze: {camera.gazeDirection}
              </span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setCameraSourceType('BROWSER_WEBCAM');
                }}
                className="opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded bg-emerald-600/90 hover:bg-emerald-500 text-[9px] font-semibold text-white cursor-pointer"
              >
                Use Real Cam
              </button>
            </div>
          </div>
        )}

        {/* Case 4: Production Mode Awaiting Host / No Active Stream */}
        {!showDemoSimulatedFeed &&
          camera.sourceType !== 'BROWSER_WEBCAM' &&
          !camera.externalFrameDataUrl && (
            <div className="w-full h-full bg-[#0E131D] p-3 flex flex-col items-center justify-center text-center">
              <ScanFace className="w-7 h-7 text-slate-500 mb-1.5" />
              <div className="text-[11px] font-semibold text-slate-300">
                {meta.environmentMode === 'PRODUCTION'
                  ? 'Awaiting Host Camera Stream'
                  : 'Camera Stream Inactive'}
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5">
                No simulated video shown in Production
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setCameraSourceType('BROWSER_WEBCAM');
                }}
                className="mt-2 px-2.5 py-1 rounded bg-[#182234] hover:bg-blue-600/30 border border-blue-500/40 text-[10px] font-medium text-blue-300 flex items-center gap-1 cursor-pointer"
              >
                <Camera className="w-3 h-3" />
                <span>Connect Hardware Webcam</span>
              </button>
            </div>
          )}
      </div>

      {/* Drag Hint Badge on Hover */}
      <div className="absolute bottom-1.5 left-2.5 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity">
        <Move className="w-3 h-3 text-white/60" />
      </div>

      {/* Bottom-Right Diagonal Resize Grip Handle (`//` matching reference screenshot) */}
      <div
        onPointerDown={(e) => {
          e.stopPropagation();
          setIsResizing(true);
          resizeStartRef.current = {
            startX: e.clientX,
            startY: e.clientY,
            origW: size.width,
            origH: size.height,
          };
        }}
        title="Drag to resize Camera PIP"
        className="absolute bottom-0 right-0 w-6 h-6 cursor-nwse-resize flex items-end justify-end p-1.5 z-30 text-white/80 hover:text-white"
      >
        <svg viewBox="0 0 12 12" className="w-3 h-3 stroke-current stroke-[1.8]">
          <line x1="11" y1="3" x2="3" y2="11" />
          <line x1="11" y1="7" x2="7" y2="11" />
        </svg>
      </div>
    </div>
  );
};
