/**
 * StrictRecords — 50:50 Dual-Browser Workspace Container
 *
 * Maintains the signature 50:50 side-by-side split browser layout with a central
 * interactive `<->` splitter control. Users can drag to adjust the split ratio
 * between 25% and 75% or double-click the `<->` handle to snap back to 50:50.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeftRight } from 'lucide-react';
import { STRICT_TOKENS } from '../../tokens/designTokens';
import { BrowserPane } from './BrowserPane';
import {isDesktopHost} from '../../services/DesktopRuntime';
import {DesktopSplitWorkspace} from './DesktopSplitWorkspace';

export const SplitBrowserWorkspace: React.FC<{
  children?: React.ReactNode;
}> = ({ children }) => {
  if(isDesktopHost)return <DesktopSplitWorkspace>{children}</DesktopSplitWorkspace>;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [leftWidthPercent, setLeftWidthPercent] = useState<number>(
    STRICT_TOKENS.layout.defaultSplitRatio
  );
  const [isDraggingSplit, setIsDraggingSplit] = useState<boolean>(false);
  const [isSwapped, setIsSwapped] = useState<boolean>(false);

  const handlePointerMove = useCallback(
    (e: PointerEvent) => {
      if (!isDraggingSplit || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const rawPercent = ((e.clientX - rect.left) / rect.width) * 100;
      const clamped = Math.min(
        STRICT_TOKENS.layout.maxPaneWidthPercent,
        Math.max(STRICT_TOKENS.layout.minPaneWidthPercent, rawPercent)
      );
      setLeftWidthPercent(clamped);
    },
    [isDraggingSplit]
  );

  const handlePointerUp = useCallback(() => {
    setIsDraggingSplit(false);
  }, []);

  useEffect(() => {
    if (!isDraggingSplit) return;
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
  }, [isDraggingSplit, handlePointerMove, handlePointerUp]);

  return (
    <main
      ref={containerRef}
      aria-label="50:50 Split Restricted Browser Workspace"
      className="flex-1 relative flex items-stretch px-3 py-2.5 gap-0 overflow-hidden bg-[#080B11]"
    >
      {/* Left Browser Pane */}
      <div
        style={{ width: `calc(${leftWidthPercent}% - 8px)` }}
        className="h-full shrink-0 overflow-hidden"
      >
        <BrowserPane
          key={isSwapped ? 'pane-left-leetcode' : 'pane-left-chatgpt'}
          paneId="LEFT"
          initialUrl={isSwapped ? 'https://leetcode.com' : 'https://chatgpt.com'}
        />
      </div>

      {/* Central Resizable Splitter & `<->` Handle */}
      <div className="w-4 h-full flex items-center justify-center relative shrink-0 z-10">
        {/* Vertical Divider Track */}
        <div
          onPointerDown={() => setIsDraggingSplit(true)}
          className={`w-[2px] h-full cursor-col-resize transition-colors ${
            isDraggingSplit ? 'bg-blue-500' : 'bg-transparent hover:bg-blue-500/40'
          }`}
        />

        {/* Circular `<->` Control Button matching reference screenshot */}
        <button
          type="button"
          onPointerDown={(e) => {
            e.stopPropagation();
            setIsDraggingSplit(true);
          }}
          onDoubleClick={() => {
            setLeftWidthPercent(50);
            setIsSwapped((s) => !s);
          }}
          title="Drag to resize split ratio · Double-click to reset 50:50 & swap panes"
          className={`absolute w-7 h-7 rounded-full bg-[#151D2B] hover:bg-[#1E293D] border flex items-center justify-center shadow-lg transition-colors cursor-ew-resize ${
            isDraggingSplit
              ? 'border-blue-400 text-blue-300 scale-105'
              : 'border-[#33435E] text-slate-200'
          }`}
        >
          <ArrowLeftRight className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Right Browser Pane */}
      <div
        style={{ width: `calc(${100 - leftWidthPercent}% - 8px)` }}
        className="h-full shrink-0 overflow-hidden"
      >
        <BrowserPane
          key={isSwapped ? 'pane-right-chatgpt' : 'pane-right-leetcode'}
          paneId="RIGHT"
          initialUrl={isSwapped ? 'https://chatgpt.com' : 'https://leetcode.com'}
        />
      </div>

      {/* Floating Overlays (Draggable/Resizable Camera PIP) */}
      {children}
    </main>
  );
};
