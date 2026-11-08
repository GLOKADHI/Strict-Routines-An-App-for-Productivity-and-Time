/**
 * StrictRecords — Bottom Session Policy & Control Footer
 *
 * Matches the reference screenshot bottom bar:
 * - Left: Expand policy drawer button (`^`) + `Allowed: chatgpt.com, leetcode.com | Focus: DSA + Practice`
 * - Center/Right: Camera PIP restore button (if hidden) + Mode Telemetry Notice
 * - Right: Red-bordered `[ □ End Session ]` button
 */

import React from 'react';
import { Camera, ChevronDown, ChevronUp, Code2, FlaskConical, Radio } from 'lucide-react';
import { useStrictSession } from '../../providers/StrictSessionContext';
import {isDesktopHost} from '../../services/DesktopRuntime';
import {DesktopFooter} from '../integration/DesktopChrome';

interface SessionFooterProps {
  onRequestEndSessionModal: () => void;
  onOpenIntegrationModal: () => void;
}

export const SessionFooter: React.FC<SessionFooterProps> = ({
  onRequestEndSessionModal,
  onOpenIntegrationModal,
}) => {
  if(isDesktopHost)return <DesktopFooter onRequestEndSessionModal={onRequestEndSessionModal}/>;
  const {
    state,
    isMonitoringExpanded,
    activeMonitoringTab,
    openMonitoringSection,
    setIsMonitoringExpanded,
    isCameraPipVisible,
    setIsCameraPipVisible,
    isCameraPipMinimized,
    setIsCameraPipMinimized,
  } = useStrictSession();

  const { browserRestrictions, meta } = state;
  const isPolicyDrawerOpen =
    isMonitoringExpanded && activeMonitoringTab === 'BROWSER_POLICY';

  const handleTogglePolicyDrawer = () => {
    if (isPolicyDrawerOpen) {
      setIsMonitoringExpanded(false);
    } else {
      openMonitoringSection('BROWSER_POLICY');
    }
  };

  return (
    <footer
      aria-label="Session Policy & Termination Controls"
      className="h-[48px] bg-[#0B0F17] border-t border-[#1E293B] px-3.5 flex items-center justify-between gap-4 shrink-0 z-20"
    >
      {/* Left: Policy Drawer Toggle (`^`) + Allowed Domains & Focus */}
      <div className="flex items-center gap-3 min-w-0">
        <button
          type="button"
          onClick={handleTogglePolicyDrawer}
          title={
            isPolicyDrawerOpen
              ? 'Hide Browser Restriction Policy Details'
              : 'Inspect & Edit Allowed Domains Policy'
          }
          className={`w-8 h-8 rounded-lg border flex items-center justify-center transition-colors cursor-pointer shrink-0 ${
            isPolicyDrawerOpen
              ? 'bg-[#172234] border-blue-500/50 text-blue-400'
              : 'bg-[#111722] hover:bg-[#171F2E] border-[#243044] text-slate-300'
          }`}
        >
          {isPolicyDrawerOpen ? (
            <ChevronDown className="w-4 h-4" />
          ) : (
            <ChevronUp className="w-4 h-4" />
          )}
        </button>

        <div className="flex items-center gap-3 text-xs truncate">
          <button
            type="button"
            onClick={() => openMonitoringSection('BROWSER_POLICY')}
            className="text-slate-300 hover:text-white transition-colors truncate cursor-pointer"
          >
            <span className="text-slate-400">Allowed: </span>
            <span className="font-semibold text-slate-100">
              {browserRestrictions.allowedDomains.join(', ')}
            </span>
          </button>

          <span className="text-slate-600 select-none">|</span>

          <div className="text-slate-300 whitespace-nowrap">
            <span className="text-slate-400">Focus: </span>
            <span className="font-semibold text-slate-100">
              {browserRestrictions.focusLabel}
            </span>
          </div>
        </div>
      </div>

      {/* Right: Restore Camera PIP + Environment Status + End Session Button */}
      <div className="flex items-center gap-3 shrink-0">
        {(!isCameraPipVisible || isCameraPipMinimized) && (
          <button
            type="button"
            onClick={() => {
              setIsCameraPipVisible(true);
              setIsCameraPipMinimized(false);
            }}
            className="px-3 py-1.5 rounded-lg bg-[#131C2B] hover:bg-[#1A263B] border border-[#2A3B57] text-xs font-medium text-emerald-300 flex items-center gap-1.5 transition-colors cursor-pointer"
          >
            <Camera className="w-3.5 h-3.5" />
            <span>Restore Camera Preview</span>
          </button>
        )}

        {/* Environment Mode Status Indicator */}
        <button
          type="button"
          onClick={onOpenIntegrationModal}
          title="Click to open Windows EXE WebView2 Host Bridge Console & Integration Docs"
          className="hidden lg:flex items-center gap-1.5 text-[11px] text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
        >
          {meta.environmentMode === 'DEMO' ? (
            <>
              <FlaskConical className="w-3.5 h-3.5 text-amber-400" />
              <span>Demo Mode (Simulated Providers)</span>
            </>
          ) : (
            <>
              <Radio className="w-3.5 h-3.5 text-emerald-400" />
              <span>
                Production Mode (
                {meta.hostBridgeConnected ? 'Host Connected' : 'Awaiting Host Bridge'})
              </span>
            </>
          )}
          <span className="text-slate-600">·</span>
          <Code2 className="w-3.5 h-3.5 text-blue-400" />
          <span className="underline decoration-slate-600 underline-offset-2">
            Integration SDK
          </span>
        </button>

        {/* Primary End Session CTA matching screenshot */}
        <button
          type="button"
          onClick={onRequestEndSessionModal}
          className="px-5 py-1.5 rounded-xl bg-[#161116] hover:bg-[#27151C] border border-[#EF4444]/65 hover:border-[#EF4444] shadow-[0_0_16px_rgba(239,68,68,0.15)] flex items-center gap-2.5 text-xs font-semibold text-white transition-all cursor-pointer whitespace-nowrap"
        >
          <span className="w-3.5 h-3.5 rounded-[3px] border-2 border-[#EF4444] inline-block shrink-0" />
          <span>End Session</span>
        </button>
      </div>
    </footer>
  );
};
