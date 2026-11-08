/**
 * StrictRecords — Expandable Monitoring Details Panel
 *
 * Provides granular inspection and control for all external state interfaces:
 * - Overview & Timer Presets
 * - Camera, Face & Gaze Telemetry
 * - Proctoring Violations & Process Guard
 * - Companion Devices (PC, Android, iOS) & Bluetooth Peripherals
 * - Screen Capture & Multi-Display Lockdown
 * - Browser Restriction Policy & Navigation Audit Log
 */

import React, { useState } from 'react';
import {
  AlertTriangle,
  Bluetooth,
  Camera,
  CheckCircle2,
  Clock,
  Eye,
  Globe,
  Laptop,
  Monitor,
  Plus,
  RefreshCw,
  Shield,
  Smartphone,
  Trash2,
  Video,
  X,
} from 'lucide-react';
import {
  IStrictSessionContextValue,
  useStrictSession,
} from '../../providers/StrictSessionContext';
import { formatSecondsToTimer } from '../../services/SessionEngine';
import {isDesktopHost} from '../../services/DesktopRuntime';
import {DesktopMonitoringDetails} from '../integration/DesktopChrome';

const TABS: {
  id: IStrictSessionContextValue['activeMonitoringTab'];
  label: string;
  icon: React.FC<{ className?: string }>;
}[] = [
  { id: 'OVERVIEW', label: 'Session & Timer', icon: Clock },
  { id: 'CAMERA_GAZE', label: 'Camera & Gaze', icon: Camera },
  { id: 'VIOLATIONS', label: 'Violations Log', icon: AlertTriangle },
  { id: 'DEVICES_BLUETOOTH', label: 'Devices & Bluetooth', icon: Bluetooth },
  { id: 'SCREEN_PROCTORING', label: 'Screen & Proctoring', icon: Monitor },
  { id: 'BROWSER_POLICY', label: 'Browser Restrictions', icon: Globe },
];

export const MonitoringDetailsPanel: React.FC = () => {
  if(isDesktopHost)return <DesktopMonitoringDetails/>;
  const {
    state,
    isMonitoringExpanded,
    setIsMonitoringExpanded,
    activeMonitoringTab,
    setActiveMonitoringTab,
    isCameraPipVisible,
    setIsCameraPipVisible,
    resetTimerToPreset,
    toggleTimerPause,
    setCameraSourceType,
    toggleCameraPrivacyMask,
    cycleDemoGazeDirection,
    recordViolation,
    resolveViolation,
    clearViolations,
    toggleDemoDeviceState,
    toggleBluetoothDeviceAllowed,
    addAllowedDomain,
    removeAllowedDomain,
  } = useStrictSession();

  const [newDomainInput, setNewDomainInput] = useState('');

  if (!isMonitoringExpanded) {
    return null;
  }

  const handleAddDomain = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newDomainInput.trim()) return;
    addAllowedDomain(newDomainInput);
    setNewDomainInput('');
  };

  return (
    <section
      aria-label="Expanded Monitoring Telemetry Details"
      className="bg-[#0D121B] border-b border-[#243044] px-4 py-3 shadow-2xl z-20 shrink-0"
    >
      {/* Top Sub-Navigation & Environment Badge */}
      <div className="flex items-center justify-between gap-4 pb-2.5 mb-3 border-b border-[#1E293B] overflow-x-auto">
        <div className="flex items-center gap-1.5">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeMonitoringTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveMonitoringTab(tab.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer whitespace-nowrap ${
                  isActive
                    ? 'bg-[#192334] text-white border border-[#2E3E58]'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-[#131B28]'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                <span>{tab.label}</span>
                {tab.id === 'VIOLATIONS' && state.proctoring.violationCount > 0 && (
                  <span className="ml-1 px-1.5 py-0.2 bg-amber-500/20 text-amber-300 rounded text-[10px] font-mono tabular-nums">
                    {state.proctoring.violationCount}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <span className="text-[11px] text-slate-400 font-mono">
            Session ID: <strong className="text-slate-200">{state.meta.sessionId}</strong>
          </span>
          <button
            type="button"
            onClick={() => setIsMonitoringExpanded(false)}
            className="p-1 rounded-md hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
            title="Close Details Panel"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Tab Content Area */}
      <div className="min-h-[120px] max-h-[210px] overflow-y-auto pr-1">
        {/* 1. OVERVIEW & TIMER */}
        {activeMonitoringTab === 'OVERVIEW' && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-[11px] text-slate-400 font-medium">
                Host Provider Bridge
              </div>
              <div className="text-sm font-semibold text-white mt-1">
                {state.meta.hostBridgeConnected
                  ? 'Connected (Active Telemetry)'
                  : 'Awaiting Windows EXE Host'}
              </div>
              <div className="text-[11px] text-slate-400 mt-1 truncate">
                {state.meta.hostApplicationVersion ||
                  'No WebView2 Host Handshake Received'}
              </div>
            </div>

            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-[11px] text-slate-400 font-medium">
                Session Timer Controls
              </div>
              <div className="flex items-baseline gap-2 mt-1 font-mono tabular-nums">
                <span className="text-lg font-bold text-white">
                  {formatSecondsToTimer(state.timer.remainingSeconds)}
                </span>
                <span className="text-xs text-slate-400">
                  elapsed: {formatSecondsToTimer(state.timer.elapsedSeconds)}
                </span>
              </div>
              <div className="flex items-center gap-1.5 mt-2">
                <button
                  type="button"
                  onClick={toggleTimerPause}
                  className="px-2.5 py-1 rounded bg-[#1B2538] hover:bg-[#24324B] text-[11px] font-medium text-slate-200 border border-[#2C3B57] cursor-pointer"
                >
                  {state.timer.runState === 'RUNNING' ? 'Pause' : 'Resume'}
                </button>
                <button
                  type="button"
                  onClick={() => resetTimerToPreset(10 * 60)}
                  className="px-2 py-1 rounded bg-[#151D2B] hover:bg-[#1E293B] text-[11px] text-slate-300 border border-[#243044] cursor-pointer"
                >
                  10m
                </button>
                <button
                  type="button"
                  onClick={() => resetTimerToPreset(25 * 60)}
                  className="px-2 py-1 rounded bg-[#151D2B] hover:bg-[#1E293B] text-[11px] text-slate-300 border border-[#243044] cursor-pointer"
                >
                  25m
                </button>
                <button
                  type="button"
                  onClick={() => resetTimerToPreset(60 * 60)}
                  className="px-2 py-1 rounded bg-[#151D2B] hover:bg-[#1E293B] text-[11px] text-slate-300 border border-[#243044] cursor-pointer"
                >
                  60m
                </button>
              </div>
            </div>

            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-[11px] text-slate-400 font-medium">
                Power & Battery Interface
              </div>
              <div className="text-sm font-semibold text-white mt-1 font-mono tabular-nums">
                Level:{' '}
                {state.battery.percentage !== null
                  ? `${state.battery.percentage}%`
                  : 'Unverified'}{' '}
                · {state.battery.powerSource}
              </div>
              <div className="text-[11px] text-slate-400 mt-1">
                Est. Remaining:{' '}
                {state.battery.estimatedMinutesRemaining !== null
                  ? `${state.battery.estimatedMinutesRemaining} mins`
                  : 'N/A'}
              </div>
            </div>

            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3 flex flex-col justify-between">
              <div>
                <div className="text-[11px] text-slate-400 font-medium">
                  Floating Camera PIP Window
                </div>
                <div className="text-xs text-slate-300 mt-1">
                  Status: {isCameraPipVisible ? 'Visible on Workspace' : 'Minimized'}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setIsCameraPipVisible((v) => !v)}
                className="mt-2 w-full py-1.5 rounded bg-emerald-600/20 hover:bg-emerald-600/30 border border-emerald-500/40 text-emerald-300 text-xs font-medium transition-colors cursor-pointer"
              >
                {isCameraPipVisible ? 'Hide Floating Camera PIP' : 'Restore Floating Camera PIP'}
              </button>
            </div>
          </div>
        )}

        {/* 2. CAMERA & GAZE */}
        {activeMonitoringTab === 'CAMERA_GAZE' && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-[11px] text-slate-400 font-medium">
                Video Input Source
              </div>
              <div className="text-xs font-semibold text-slate-100 mt-1 truncate">
                {state.camera.deviceName || 'No Active Camera Device'}
              </div>
              <div className="text-[11px] text-slate-400 mt-1 font-mono tabular-nums">
                Stream: {state.camera.resolution || '--'} @{' '}
                {state.camera.fps ? `${state.camera.fps} FPS` : '--'}
              </div>
              <div className="flex flex-wrap items-center gap-1.5 mt-2.5">
                <button
                  type="button"
                  onClick={() => setCameraSourceType('BROWSER_WEBCAM')}
                  className={`px-2.5 py-1 rounded text-[11px] font-medium border cursor-pointer ${
                    state.camera.sourceType === 'BROWSER_WEBCAM'
                      ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-300'
                      : 'bg-[#182130] border-[#263347] text-slate-300 hover:text-white'
                  }`}
                >
                  Use Real Webcam
                </button>
                <button
                  type="button"
                  onClick={() => setCameraSourceType('DEMO_SIMULATED')}
                  className={`px-2.5 py-1 rounded text-[11px] font-medium border cursor-pointer ${
                    state.camera.sourceType === 'DEMO_SIMULATED'
                      ? 'bg-amber-500/20 border-amber-500/50 text-amber-300'
                      : 'bg-[#182130] border-[#263347] text-slate-300 hover:text-white'
                  }`}
                >
                  Simulated Feed
                </button>
                <button
                  type="button"
                  onClick={toggleCameraPrivacyMask}
                  className="px-2.5 py-1 rounded text-[11px] font-medium bg-[#182130] border border-[#263347] text-slate-300 hover:text-white cursor-pointer"
                >
                  {state.camera.isPrivacyMasked ? 'Unmask Feed' : 'Privacy Blur'}
                </button>
              </div>
            </div>

            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-[11px] text-slate-400 font-medium">
                Biometric Face & Gaze Tracking
              </div>
              <div className="flex items-center justify-between mt-1.5">
                <span className="text-xs text-slate-300">Face Presence:</span>
                <span
                  className={`text-xs font-semibold ${
                    state.camera.faceDetected
                      ? 'text-[#22C55E]'
                      : state.camera.faceDetected === false
                        ? 'text-red-400'
                        : 'text-slate-500'
                  }`}
                >
                  {state.camera.faceDetected === true
                    ? `Verified (${state.camera.faceConfidencePct ?? 98}%)`
                    : state.camera.faceDetected === false
                      ? 'Missing'
                      : 'Unverified'}
                </span>
              </div>
              <div className="flex items-center justify-between mt-1.5">
                <span className="text-xs text-slate-300">Gaze Vector:</span>
                <span className="text-xs font-mono font-semibold text-white">
                  {state.camera.gazeDirection}
                </span>
              </div>
              <button
                type="button"
                onClick={cycleDemoGazeDirection}
                className="mt-2.5 w-full py-1 rounded bg-[#182130] hover:bg-[#222F44] border border-[#2A3952] text-[11px] text-slate-200 flex items-center justify-center gap-1.5 cursor-pointer"
              >
                <Eye className="w-3.5 h-3.5 text-blue-400" />
                <span>Test Gaze Vector Change</span>
              </button>
            </div>

            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3 flex flex-col justify-between">
              <div>
                <div className="text-[11px] text-slate-400 font-medium">
                  Floating Overlay Controls
                </div>
                <p className="text-[11px] text-slate-400 mt-1 leading-relaxed">
                  The camera PIP window can be dragged anywhere in the dual-browser
                  workspace or resized from its bottom-right grip.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsCameraPipVisible(true)}
                className="mt-2 py-1.5 rounded bg-blue-600/20 hover:bg-blue-600/30 border border-blue-500/40 text-blue-300 text-xs font-medium flex items-center justify-center gap-1.5 cursor-pointer"
              >
                <Video className="w-3.5 h-3.5" />
                <span>Focus Camera PIP</span>
              </button>
            </div>
          </div>
        )}

        {/* 3. VIOLATIONS LOG */}
        {activeMonitoringTab === 'VIOLATIONS' && (
          <div className="space-y-2.5">
            <div className="flex items-center justify-between">
              <div className="text-xs text-slate-300">
                Recorded Session Violations:{' '}
                <strong className="text-white font-mono tabular-nums">
                  {state.proctoring.violationCount} / {state.proctoring.maxAllowedViolations}
                </strong>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() =>
                    recordViolation({
                      category: 'GAZE_AWAY',
                      severity: 'LOW',
                      description: 'Sustained off-screen gaze detected (>4.0s)',
                    })
                  }
                  className="px-2.5 py-1 rounded bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/40 text-amber-300 text-[11px] font-medium cursor-pointer"
                >
                  + Log Sample Gaze Alert
                </button>
                {state.proctoring.violations.length > 0 && (
                  <button
                    type="button"
                    onClick={clearViolations}
                    className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] flex items-center gap-1 cursor-pointer"
                  >
                    <Trash2 className="w-3 h-3" />
                    <span>Clear Log</span>
                  </button>
                )}
              </div>
            </div>

            {state.proctoring.violations.length === 0 ? (
              <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-4 text-center text-xs text-slate-400">
                Zero policy violations recorded during this session. Try navigating to an
                unauthorized website (e.g., youtube.com) in the browser below to test live
                violation logging.
              </div>
            ) : (
              <div className="space-y-1.5">
                {state.proctoring.violations.map((v) => (
                  <div
                    key={v.id}
                    className="bg-[#121824] border border-[#1E293B] rounded-lg px-3 py-2 flex items-center justify-between gap-3"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <AlertTriangle
                        className={`w-4 h-4 shrink-0 ${
                          v.resolved ? 'text-slate-500' : 'text-amber-400'
                        }`}
                      />
                      <div className="min-w-0">
                        <div className="text-xs font-medium text-slate-200 truncate">
                          {v.description}
                        </div>
                        <div className="text-[10px] text-slate-400 font-mono tabular-nums">
                          {v.category} · Severity: {v.severity} ·{' '}
                          {new Date(v.timestampIso).toLocaleTimeString()}
                        </div>
                      </div>
                    </div>
                    {!v.resolved ? (
                      <button
                        type="button"
                        onClick={() => resolveViolation(v.id)}
                        className="px-2 py-1 rounded bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 text-emerald-300 text-[10px] font-medium shrink-0 cursor-pointer"
                      >
                        Acknowledge
                      </button>
                    ) : (
                      <span className="text-[10px] text-slate-500 flex items-center gap-1 shrink-0">
                        <CheckCircle2 className="w-3 h-3" /> Resolved
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 4. DEVICES & BLUETOOTH */}
        {activeMonitoringTab === 'DEVICES_BLUETOOTH' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* Companion Devices */}
            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-xs font-semibold text-slate-200 mb-2 flex items-center gap-1.5">
                <Smartphone className="w-3.5 h-3.5 text-blue-400" />
                <span>Multi-Device Companion Lockdown (3 Slots)</span>
              </div>
              <div className="space-y-2">
                {(['PC', 'ANDROID', 'IOS'] as const).map((platform) => {
                  const dev = state.deviceConnections.devices[platform];
                  const isOnline = dev.status === 'HOST' || dev.status === 'CONNECTED';
                  return (
                    <div
                      key={platform}
                      className="flex items-center justify-between bg-[#0E131D] border border-[#1E293B] rounded px-2.5 py-1.5"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <Laptop className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                        <div className="min-w-0">
                          <div className="text-xs font-medium text-slate-200 truncate">
                            {dev.label}: {dev.deviceName || 'Unbound Slot'}
                          </div>
                          <div className="text-[10px] text-slate-400 font-mono tabular-nums">
                            Status: {dev.status} · Battery:{' '}
                            {dev.batteryPct !== null ? `${dev.batteryPct}%` : '--'} · Ping:{' '}
                            {dev.lastHeartbeatMs !== null ? `${dev.lastHeartbeatMs}ms` : '--'}
                          </div>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => toggleDemoDeviceState(platform)}
                        className={`px-2 py-1 rounded text-[10px] font-semibold border cursor-pointer ${
                          isOnline
                            ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300'
                            : 'bg-slate-800 border-slate-700 text-slate-400'
                        }`}
                      >
                        {isOnline ? 'Connected' : 'Reconnect'}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Bluetooth Peripherals */}
            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
                  <Bluetooth className="w-3.5 h-3.5 text-blue-400" />
                  <span>Bluetooth & Peripheral Security</span>
                </div>
                <span className="text-[10px] font-mono text-slate-400">
                  Adapter: {state.bluetooth.adapterState}
                </span>
              </div>
              {state.bluetooth.devices.length === 0 ? (
                <div className="text-xs text-slate-400 py-4 text-center">
                  No Bluetooth peripherals reported by Host Bridge.
                </div>
              ) : (
                <div className="space-y-1.5">
                  {state.bluetooth.devices.map((bt) => (
                    <div
                      key={bt.id}
                      className="flex items-center justify-between bg-[#0E131D] border border-[#1E293B] rounded px-2.5 py-1.5"
                    >
                      <div>
                        <div className="text-xs font-medium text-slate-200">
                          {bt.name}
                        </div>
                        <div className="text-[10px] text-slate-400 font-mono tabular-nums">
                          Category: {bt.category} · RSSI: {bt.rssiDbm ?? '--'} dBm
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => toggleBluetoothDeviceAllowed(bt.id)}
                        className={`px-2 py-0.5 rounded text-[10px] font-semibold border cursor-pointer ${
                          bt.isAllowed
                            ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300'
                            : 'bg-red-500/15 border-red-500/40 text-red-300'
                        }`}
                      >
                        {bt.isAllowed ? 'Permitted' : 'Blocked'}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* 5. SCREEN CAPTURE & PROCTORING */}
        {activeMonitoringTab === 'SCREEN_PROCTORING' && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-[11px] text-slate-400 font-medium">
                Screen Capture Engine
              </div>
              <div className="text-xs font-semibold text-white mt-1">
                Primary Display: {state.screenCapture.primaryResolution || 'Unverified'}
              </div>
              <div className="text-[11px] text-slate-400 mt-1 font-mono tabular-nums">
                Active Displays: {state.screenCapture.displayCount ?? '--'} · Capture FPS:{' '}
                {state.screenCapture.captureFps ?? '--'}
              </div>
            </div>

            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-[11px] text-slate-400 font-medium">
                OS Process & Window Guard
              </div>
              <div className="text-xs font-semibold text-white mt-1 flex items-center gap-1.5">
                <Shield className="w-3.5 h-3.5 text-[#22C55E]" />
                <span>
                  {state.proctoring.activeProcessGuard
                    ? 'Active (Background Blacklist Enforced)'
                    : 'Standby'}
                </span>
              </div>
              <div className="text-[11px] text-slate-400 mt-1">
                Multi-Monitor Lock:{' '}
                {state.screenCapture.multiMonitorBlocked ? 'Enforced' : 'Unverified'}
              </div>
            </div>

            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-[11px] text-slate-400 font-medium">
                Acoustic Environment
              </div>
              <div className="text-xs font-semibold text-white mt-1">
                Microphone Policy: {state.proctoring.audioState}
              </div>
              <div className="text-[11px] text-slate-400 mt-1 font-mono tabular-nums">
                Noise Floor:{' '}
                {state.proctoring.ambientNoiseDb !== null
                  ? `${state.proctoring.ambientNoiseDb} dB`
                  : 'Unverified'}
              </div>
            </div>
          </div>
        )}

        {/* 6. BROWSER RESTRICTIONS */}
        {activeMonitoringTab === 'BROWSER_POLICY' && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-xs font-semibold text-slate-200 mb-2">
                Whitelisted Domains Policy
              </div>
              <div className="flex flex-wrap gap-1.5 mb-2.5">
                {state.browserRestrictions.allowedDomains.map((domain) => (
                  <span
                    key={domain}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-[#182234] border border-[#2A3B56] text-xs font-mono text-slate-200"
                  >
                    <span>{domain}</span>
                    {state.browserRestrictions.allowedDomains.length > 1 && (
                      <button
                        type="button"
                        onClick={() => removeAllowedDomain(domain)}
                        className="text-slate-400 hover:text-red-400 cursor-pointer"
                        title={`Remove ${domain} from allowed list`}
                      >
                        <X className="w-3 h-3" />
                      </button>
                    )}
                  </span>
                ))}
              </div>
              <form onSubmit={handleAddDomain} className="flex items-center gap-2">
                <input
                  type="text"
                  value={newDomainInput}
                  onChange={(e) => setNewDomainInput(e.target.value)}
                  placeholder="Add allowed domain (e.g. geeksforgeeks.org)"
                  className="flex-1 bg-[#0B0F17] border border-[#243044] rounded px-2.5 py-1 text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none focus:border-blue-500"
                />
                <button
                  type="submit"
                  className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium flex items-center gap-1 cursor-pointer"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>Whitelist</span>
                </button>
              </form>
            </div>

            <div className="bg-[#121824] border border-[#1E293B] rounded-lg p-3">
              <div className="text-xs font-semibold text-slate-200 mb-2 flex items-center justify-between">
                <span>Recent Navigation Audit Trail</span>
                <RefreshCw className="w-3 h-3 text-slate-400" />
              </div>
              <div className="space-y-1 max-h-[110px] overflow-y-auto">
                {state.browserRestrictions.navigationAuditLog.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center justify-between text-[11px] font-mono bg-[#0E131D] px-2 py-1 rounded border border-[#1E293B]"
                  >
                    <span className="text-slate-300 truncate max-w-[260px]">
                      [{item.paneId}] {item.requestedUrl}
                    </span>
                    <span
                      className={
                        item.allowed
                          ? 'text-[#22C55E] font-semibold'
                          : 'text-red-400 font-semibold'
                      }
                    >
                      {item.allowed ? 'ALLOWED' : 'BLOCKED'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
};
