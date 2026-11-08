/**
 * StrictRecords — Compact Desktop Window Header
 *
 * Renders the top Windows title bar with dynamic device count, session status,
 * Study Mode profile selector, Environment Mode Switcher (DEMO vs PRODUCTION),
 * Host Bridge Inspector trigger, clock, and native window controls.
 */

import React, { useState } from 'react';
import {
  Check,
  ChevronDown,
  Code2,
  FlaskConical,
  Lock,
  Maximize2,
  Minimize2,
  Minus,
  Radio,
  ShieldAlert,
  Square,
  X,
} from 'lucide-react';
import { useStrictSession } from '../../providers/StrictSessionContext';
import {
  countConnectedDevices,
  sendCommandToHostBridge,
} from '../../services/SessionEngine';
import {isDesktopHost} from '../../services/DesktopRuntime';
import {DesktopHeader} from '../integration/DesktopChrome';

interface CompactHeaderProps {
  onOpenIntegrationModal: () => void;
  onRequestEndSessionModal: () => void;
}

const STUDY_PROFILES = [
  {
    label: 'Study Mode (DSA + Practice)',
    focus: 'DSA + Practice',
    domains: ['chatgpt.com', 'leetcode.com'],
  },
  {
    label: 'Competitive Coding Lockdown',
    focus: 'Algorithms Only',
    domains: ['leetcode.com', 'codeforces.com'],
  },
  {
    label: 'System Design & Docs Mode',
    focus: 'Architecture + Reference',
    domains: ['chatgpt.com', 'leetcode.com', 'developer.mozilla.org'],
  },
];

export const CompactHeader: React.FC<CompactHeaderProps> = ({
  onOpenIntegrationModal,
  onRequestEndSessionModal,
}) => {
  if(isDesktopHost)return <DesktopHeader onRequestEndSessionModal={onRequestEndSessionModal}/>;
  const {
    state,
    setEnvironmentMode,
    dispatchHostEvent,
    openMonitoringSection,
  } = useStrictSession();

  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const { connectedCount, totalSlots } = countConnectedDevices(state.deviceConnections);
  const isDemo = state.meta.environmentMode === 'DEMO';
  const isSessionActive =
    state.proctoring.monitoringActive && state.proctoring.providerStatus === 'CONNECTED';

  const handleSelectProfile = (profile: (typeof STUDY_PROFILES)[number]) => {
    dispatchHostEvent({
      type: 'HOST_BROWSER_POLICY_UPDATE',
      payload: {
        sessionProfileLabel: profile.label,
        focusLabel: profile.focus,
        allowedDomains: profile.domains,
      },
    });
    setIsProfileMenuOpen(false);
  };

  const handleWindowControl = (action: 'MINIMIZE' | 'MAXIMIZE' | 'CLOSE') => {
    sendCommandToHostBridge({ type: 'WINDOW_CONTROL', payload: { action } });
    if (action === 'MAXIMIZE') {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen?.().catch(() => {});
        setIsFullscreen(true);
      } else {
        document.exitFullscreen?.().catch(() => {});
        setIsFullscreen(false);
      }
    } else if (action === 'CLOSE') {
      onRequestEndSessionModal();
    }
  };

  return (
    <header className="h-[38px] bg-[#0B0F17] border-b border-[#1E293B] px-3 flex items-center justify-between select-none shrink-0 z-30">
      {/* Left Zone: Brand Lockup + Session Status + Study Mode Selector */}
      <div className="flex items-center gap-3 min-w-0">
        {/* Brand Icon & Title */}
        <div className="flex items-center gap-2 shrink-0">
          <div className="w-5 h-5 rounded bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center shadow-sm shadow-blue-500/20">
            <Lock className="w-3 h-3 text-white stroke-[2.5]" />
          </div>
          <div className="text-[13px] tracking-tight flex items-center gap-1.5 whitespace-nowrap">
            <span className="font-bold text-white">StrictRecords</span>
            <span className="text-slate-400 font-normal">
              — Strict Session ({connectedCount} / {totalSlots} Devices)
            </span>
          </div>
        </div>

        {/* Strict Session Active Status Control */}
        <button
          type="button"
          onClick={() => openMonitoringSection('OVERVIEW')}
          title="Click to inspect real-time session monitoring state"
          className={`flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium border transition-colors whitespace-nowrap shrink-0 cursor-pointer ${
            isSessionActive
              ? 'bg-[#111C18] border-[#22C55E]/35 text-[#22C55E] hover:bg-[#162620]'
              : 'bg-slate-800/80 border-slate-700 text-slate-300 hover:bg-slate-800'
          }`}
        >
          {isSessionActive ? (
            <>
              <span className="w-2 h-2 rounded-full bg-[#22C55E] shadow-[0_0_6px_#22C55E]" />
              <span>Strict Session Active</span>
            </>
          ) : (
            <>
              <ShieldAlert className="w-3 h-3 text-amber-400" />
              <span>
                {state.meta.environmentMode === 'PRODUCTION' && !state.meta.hostBridgeConnected
                  ? 'Awaiting Host EXE Bridge'
                  : 'Session Standby'}
              </span>
            </>
          )}
        </button>

        {/* Study Mode Profile Selector */}
        <div className="relative shrink-0">
          <button
            type="button"
            onClick={() => setIsProfileMenuOpen((v) => !v)}
            className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-[#151C28] hover:bg-[#1C2536] border border-[#263347] text-[11px] text-slate-200 font-medium transition-colors whitespace-nowrap cursor-pointer"
          >
            <span>{state.browserRestrictions.sessionProfileLabel}</span>
            <ChevronDown className="w-3 h-3 text-slate-400" />
          </button>

          {isProfileMenuOpen && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setIsProfileMenuOpen(false)}
              />
              <div className="absolute left-0 top-full mt-1.5 w-64 bg-[#111722] border border-[#263347] rounded-lg shadow-2xl py-1 z-50">
                <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-400 border-b border-[#1E293B]">
                  Select Session Policy Profile
                </div>
                {STUDY_PROFILES.map((profile) => {
                  const isSelected =
                    state.browserRestrictions.sessionProfileLabel === profile.label;
                  return (
                    <button
                      key={profile.label}
                      type="button"
                      onClick={() => handleSelectProfile(profile)}
                      className="w-full px-3 py-2 text-left hover:bg-[#192231] flex items-start justify-between gap-2 transition-colors"
                    >
                      <div>
                        <div className="text-xs font-medium text-slate-100">
                          {profile.label}
                        </div>
                        <div className="text-[10px] text-slate-400 mt-0.5">
                          Allowed: {profile.domains.join(', ')}
                        </div>
                      </div>
                      {isSelected && (
                        <Check className="w-3.5 h-3.5 text-[#22C55E] shrink-0 mt-0.5" />
                      )}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Right Zone: Environment Mode Switcher, Bridge Docs, Clock, Window Controls */}
      <div className="flex items-center gap-2.5 shrink-0">
        {/* DEMO vs PRODUCTION Mode Segmented Switch */}
        <div
          className="flex items-center bg-[#121824] p-0.5 rounded-md border border-[#243044]"
          title="Switch between clearly identified DEMO mock telemetry and strict PRODUCTION mode"
        >
          <button
            type="button"
            onClick={() => setEnvironmentMode('DEMO')}
            className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold transition-colors cursor-pointer whitespace-nowrap ${
              isDemo
                ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <FlaskConical className="w-3 h-3" />
            <span>DEMO MOCK</span>
          </button>
          <button
            type="button"
            onClick={() => setEnvironmentMode('PRODUCTION')}
            className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold transition-colors cursor-pointer whitespace-nowrap ${
              !isDemo
                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Radio className="w-3 h-3" />
            <span>PRODUCTION</span>
          </button>
        </div>

        {/* WebView2 Host Bridge & SDK Inspector Trigger */}
        <button
          type="button"
          onClick={onOpenIntegrationModal}
          className="flex items-center gap-1.5 px-2 py-1 rounded bg-[#151D2A] hover:bg-[#1E293B] border border-[#283548] text-[11px] text-slate-300 font-medium transition-colors cursor-pointer whitespace-nowrap"
          title="Open Windows EXE WebView2 Integration Bridge & State Simulator"
        >
          <Code2 className="w-3.5 h-3.5 text-blue-400" />
          <span className="hidden xl:inline">Host Bridge SDK</span>
        </button>

        {/* System Timestamp */}
        <span className="text-[11px] text-slate-300 font-mono tabular-nums whitespace-nowrap px-1">
          {state.meta.currentTimeFormatted}
        </span>

        {/* Divider */}
        <div className="h-3.5 w-[1px] bg-[#243044]" />

        {/* Native Desktop Window Controls */}
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => handleWindowControl('MINIMIZE')}
            title="Minimize Window (Dispatches WINDOW_CONTROL to WebView2 Host)"
            className="w-6 h-6 rounded hover:bg-slate-800 flex items-center justify-center text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
          >
            <Minus className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => handleWindowControl('MAXIMIZE')}
            title="Toggle Fullscreen / Maximize Window"
            className="w-6 h-6 rounded hover:bg-slate-800 flex items-center justify-center text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
          >
            {isFullscreen ? (
              <Minimize2 className="w-3 h-3" />
            ) : (
              <Square className="w-3 h-3" />
            )}
          </button>
          <button
            type="button"
            onClick={() => handleWindowControl('CLOSE')}
            title="End Session / Close Window"
            className="w-6 h-6 rounded hover:bg-red-500/20 flex items-center justify-center text-slate-400 hover:text-red-400 transition-colors cursor-pointer"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </header>
  );
};
