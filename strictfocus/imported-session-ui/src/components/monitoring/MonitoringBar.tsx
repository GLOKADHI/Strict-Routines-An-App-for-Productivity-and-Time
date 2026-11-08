/**
 * StrictRecords — Compact Monitoring & Telemetry Strip
 *
 * Matches the desktop proctoring bar layout from the reference design while keeping
 * every indicator strictly bound to the external state provider (`useStrictSession`).
 * In PRODUCTION mode without an active Host Bridge, indicators accurately reflect
 * "Awaiting Host" / "Unverified" instead of simulated green checkmarks.
 */

import React from 'react';
import {
  AlertTriangle,
  BatteryCharging,
  BatteryMedium,
  Camera,
  Check,
  ChevronDown,
  ChevronUp,
  Eye,
  MonitorUp,
  Pause,
  Play,
  Power,
  ScanFace,
  ShieldCheck,
  Volume2,
  VolumeX,
  Zap,
} from 'lucide-react';
import { useStrictSession } from '../../providers/StrictSessionContext';
import {
  computeSessionHealth,
  formatSecondsToTimer,
} from '../../services/SessionEngine';
import {isDesktopHost,useDesktopRuntime} from '../../services/DesktopRuntime';
import { CompanionPlatform } from '../../types/strictSession';

/** Custom crisp SVG icons for Windows PC, Android, and Apple iOS platforms */
const WindowsLogoIcon: React.FC<{ className?: string }> = ({ className = 'w-4 h-4' }) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M3 3h8.5v8.5H3V3zm9.5 0H21v8.5h-8.5V3zM3 12.5h8.5V21H3v-8.5zm9.5 0H21V21h-8.5v-8.5z" />
  </svg>
);

const AndroidLogoIcon: React.FC<{ className?: string }> = ({ className = 'w-4 h-4' }) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M17.6 9.48l1.84-3.18c.16-.31.04-.69-.26-.85-.29-.15-.65-.06-.83.22l-1.88 3.24a11.43 11.43 0 0 0-8.94 0L5.65 5.67c-.19-.28-.54-.37-.83-.22-.3.16-.42.54-.26.85l1.84 3.18C3.92 11.03 2.28 13.67 2 16.8h20c-.28-3.13-1.92-5.77-4.4-7.32zM7 14c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm10 0c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1z" />
  </svg>
);

const AppleLogoIcon: React.FC<{ className?: string }> = ({ className = 'w-4 h-4' }) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M15.96 6.33c.67-.81 1.12-1.94 1-3.08-.96.04-2.13.64-2.82 1.45-.61.7-.15 1.85-.99 2.97 1.08.08 2.14-.53 2.81-1.34z" />
  </svg>
);

export const MonitoringBar: React.FC = () => {
  const {
    state,
    isMonitoringExpanded,
    setIsMonitoringExpanded,
    openMonitoringSection,
    isCameraPipVisible,
    setIsCameraPipVisible,
    isCameraPipMinimized,
    setIsCameraPipMinimized,
    toggleTimerPause,
    toggleAudioMute,
    toggleScreenShareState,
    toggleScreenProctoringState,
    cycleDemoGazeDirection,
    toggleDemoDeviceState,
  } = useStrictSession();

  const runtime=useDesktopRuntime();
  const health = computeSessionHealth(state);
  const { camera, proctoring, screenCapture, deviceConnections, timer, battery } = state;

  const timerProgressPct =
    timer.totalDurationSeconds > 0
      ? Math.min(100, Math.max(0, (timer.remainingSeconds / timer.totalDurationSeconds) * 100))
      : 0;

  const renderCheckOrStatus = (verified: boolean | null, fallbackText = 'Unverified') => {
    if (verified === true) {
      return <Check className="w-3 h-3 text-[#22C55E] inline stroke-[2.8]" />;
    }
    if (verified === false) {
      return <span className="text-red-400 font-semibold">!</span>;
    }
    return <span className="text-slate-500">{fallbackText}</span>;
  };

  return (
    <section
      aria-label="Session Monitoring Telemetry Bar"
      data-embedded={isDesktopHost ? "true" : undefined}
      data-testid="monitoring-bar"
      className="bg-[#0B0F17] border-b border-[#1E293B] px-3 py-2 flex items-center justify-between gap-2.5 shrink-0 z-20 overflow-x-auto"
    >
      {/* 1. Primary Monitoring Status Box */}
      <button
        data-monitor="health"
        type="button"
        onClick={() => openMonitoringSection('OVERVIEW')}
        className={`flex items-center gap-2.5 px-3.5 py-2 rounded-xl border transition-all shrink-0 cursor-pointer text-left ${
          health.isNominal
            ? 'bg-[#0C1D17] border-[#22C55E]/60 shadow-[0_0_15px_rgba(34,197,94,0.12)] hover:border-[#22C55E]'
            : health.isAwaitingHost
              ? 'bg-[#121824] border-slate-700 hover:border-slate-500'
              : 'bg-amber-950/30 border-amber-500/50 hover:border-amber-400'
        }`}
      >
        <div
          className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${
            health.isNominal
              ? 'bg-[#22C55E]/20 text-[#22C55E]'
              : health.isAwaitingHost
                ? 'bg-slate-800 text-slate-400'
                : 'bg-amber-500/20 text-amber-400'
          }`}
        >
          <ShieldCheck className="w-5 h-5 stroke-[2.2]" />
        </div>
        <div className="leading-tight">
          <div
            className={`text-xs font-bold tracking-tight whitespace-nowrap ${
              health.isNominal
                ? 'text-[#22C55E]'
                : health.isAwaitingHost
                  ? 'text-slate-200'
                  : 'text-amber-300'
            }`}
          >
            {health.label}
          </div>
          <div className="text-[10px] text-slate-400 whitespace-nowrap mt-0.5">
            {health.sublabel}
          </div>
        </div>
      </button>

      {/* 2. Individual Proctoring Indicators */}
      <div data-monitor="indicators" className="flex items-center gap-1 xl:gap-2 shrink-0">
        {/* Camera (Click to restore if minimized/hidden, or toggle minimize/expand) */}
        <button
          type="button"
          onClick={() => {
            if (!isCameraPipVisible) {
              setIsCameraPipVisible(true);
              setIsCameraPipMinimized(false);
            } else if (isCameraPipMinimized) {
              setIsCameraPipMinimized(false);
            } else {
              setIsCameraPipMinimized(true);
            }
          }}
          title={
            !isCameraPipVisible || isCameraPipMinimized
              ? 'Click to Restore Floating Camera Preview'
              : 'Click to Minimize Floating Camera Preview'
          }
          className={`flex flex-col items-center justify-center px-2.5 py-1.5 rounded-lg hover:bg-[#151D2A] transition-colors cursor-pointer min-w-[64px] ${
            !isCameraPipVisible || isCameraPipMinimized
              ? 'bg-[#121A26] border border-[#243248]'
              : ''
          }`}
        >
          <Camera
            className={`w-4 h-4 mb-1 ${
              camera.isConnected ? 'text-slate-200' : 'text-slate-500'
            }`}
          />
          <div className="text-[11px] text-slate-300 flex items-center gap-1 whitespace-nowrap">
            <span>
              {!isCameraPipVisible
                ? 'Camera (Hidden)'
                : isCameraPipMinimized
                  ? 'Camera (Min)'
                  : 'Camera'}
            </span>
            {renderCheckOrStatus(
              camera.providerStatus === 'CONNECTED' ? camera.isConnected : null,
              '—'
            )}
          </div>
        </button>

        {/* Face */}
        <button
          type="button"
          onClick={() => openMonitoringSection('CAMERA_GAZE')}
          title="Inspect Face Detection Confidence"
          className="flex flex-col items-center justify-center px-2.5 py-1.5 rounded-lg hover:bg-[#151D2A] transition-colors cursor-pointer min-w-[58px]"
        >
          <ScanFace
            className={`w-4 h-4 mb-1 ${
              camera.faceDetected ? 'text-slate-200' : 'text-slate-500'
            }`}
          />
          <div className="text-[11px] text-slate-300 flex items-center gap-1 whitespace-nowrap">
            <span>Face</span>
            {renderCheckOrStatus(camera.faceDetected, '—')}
          </div>
        </button>

        {/* Gaze */}
        <button
          type="button"
          onClick={() => {
            if (state.meta.environmentMode === 'DEMO' && !isDesktopHost) {
              cycleDemoGazeDirection();
            } else {
              openMonitoringSection('CAMERA_GAZE');
            }
          }}
          title={
            state.meta.environmentMode === 'DEMO' && !isDesktopHost
              ? 'Click to cycle Gaze Direction in Demo Mode'
              : 'Inspect Gaze Vector Telemetry'
          }
          className="flex flex-col items-center justify-center px-2.5 py-1.5 rounded-lg hover:bg-[#151D2A] transition-colors cursor-pointer min-w-[82px]"
        >
          <Eye
            className={`w-4 h-4 mb-1 ${
              camera.gazeDirection === 'CENTER'
                ? 'text-slate-200'
                : camera.gazeDirection === 'UNVERIFIED'
                  ? 'text-slate-500'
                  : 'text-amber-400'
            }`}
          />
          <div className="text-[11px] text-slate-300 whitespace-nowrap">
            <span>Gaze: </span>
            <span
              className={
                camera.gazeDirection === 'CENTER'
                  ? 'text-slate-100 font-medium'
                  : camera.gazeDirection === 'UNVERIFIED'
                    ? 'text-slate-500'
                    : 'text-amber-400 font-semibold'
              }
            >
              {camera.gazeDirection === 'CENTER'
                ? 'Center'
                : camera.gazeDirection === 'UNVERIFIED'
                  ? 'Unverified'
                  : camera.gazeDirection}
            </span>
          </div>
        </button>

        {/* Violations */}
        <button
          type="button"
          onClick={() => openMonitoringSection('VIOLATIONS')}
          title="View Session Violations Audit Log"
          className="flex flex-col items-center justify-center px-2.5 py-1.5 rounded-lg hover:bg-[#151D2A] transition-colors cursor-pointer min-w-[92px]"
        >
          <AlertTriangle
            className={`w-4 h-4 mb-1 ${
              proctoring.violationCount > 0 ? 'text-amber-400' : 'text-slate-300'
            }`}
          />
          <div className="text-[11px] text-slate-300 whitespace-nowrap tabular-nums">
            <span>Violations: </span>
            <span
              className={
                proctoring.violationCount > 0
                  ? 'text-amber-400 font-semibold'
                  : 'text-slate-100 font-medium'
              }
            >
              {proctoring.totalViolationCount ?? proctoring.violationCount}
            </span>
          </div>
        </button>

        {/* Audio */}
        <button
          type="button"
          onClick={toggleAudioMute}
          title={isDesktopHost?"Inspect host audio monitoring state":"Toggle audio sample state"}
          className="flex flex-col items-center justify-center px-2.5 py-1.5 rounded-lg hover:bg-[#151D2A] transition-colors cursor-pointer min-w-[82px]"
        >
          {proctoring.audioState === 'ACTIVE_LISTENING' ? (
            <Volume2 className="w-4 h-4 mb-1 text-[#22C55E]" />
          ) : (
            <VolumeX className="w-4 h-4 mb-1 text-slate-300" />
          )}
          <div className="text-[11px] text-slate-300 whitespace-nowrap">
            <span>Audio: </span>
            <span className="text-slate-100 font-medium">
              {proctoring.audioState === 'MUTED'
                ? 'Muted'
                : proctoring.audioState === 'ACTIVE_LISTENING'
                  ? 'Live'
                  : 'Unverified'}
            </span>
          </div>
        </button>

        {/* Screen Share */}
        <button
          type="button"
          onClick={toggleScreenShareState}
          title="Inspect display capture status"
          className="flex flex-col items-center justify-center px-2.5 py-1.5 rounded-lg hover:bg-[#151D2A] transition-colors cursor-pointer min-w-[96px]"
        >
          <MonitorUp
            className={`w-4 h-4 mb-1 ${
              screenCapture.screenShareActive ? 'text-[#22C55E]' : 'text-slate-500'
            }`}
          />
          <div className="text-[11px] whitespace-nowrap">
            <span
              className={
                screenCapture.screenShareActive ? 'text-[#22C55E]' : 'text-slate-400'
              }
            >
              Screen Share:{' '}
            </span>
            <span
              className={`font-bold ${
                screenCapture.screenShareActive
                  ? 'text-[#22C55E]'
                  : screenCapture.screenShareActive === false
                    ? 'text-red-400'
                    : 'text-slate-500'
              }`}
            >
              {screenCapture.screenShareActive === true
                ? 'ON'
                : screenCapture.screenShareActive === false
                  ? 'OFF'
                  : '—'}
            </span>
          </div>
        </button>

        {/* Screen Proctoring */}
        <button
          type="button"
          onClick={toggleScreenProctoringState}
          title="Inspect screen proctoring status"
          className="flex flex-col items-center justify-center px-2.5 py-1.5 rounded-lg hover:bg-[#151D2A] transition-colors cursor-pointer min-w-[114px]"
        >
          <ShieldCheck
            className={`w-4 h-4 mb-1 ${
              proctoring.screenProctoringEnabled ? 'text-slate-200' : 'text-slate-500'
            }`}
          />
          <div className="text-[11px] text-slate-300 whitespace-nowrap">
            <span>Screen Proctoring: </span>
            <span
              className={`font-bold ${
                proctoring.screenProctoringEnabled
                  ? 'text-[#22C55E]'
                  : proctoring.screenProctoringEnabled === false
                    ? 'text-red-400'
                    : 'text-slate-500'
              }`}
            >
              {proctoring.screenProctoringEnabled === true
                ? 'ON'
                : proctoring.screenProctoringEnabled === false
                  ? 'OFF'
                  : '—'}
            </span>
          </div>
        </button>
      </div>

      {/* Subtle Vertical Separator */}
      <div data-monitor="separator" className="h-9 w-[1px] bg-[#1E293B] shrink-0" />

      {/* 3. Multi-Device Companion Cluster (PC / Android / iOS) */}
      <div data-monitor="devices" className="bg-[#0F141E] border border-[#1E293B] rounded-xl px-3.5 py-1.5 flex items-center gap-4 shrink-0">
        {((isDesktopHost ? ['PC','IOS'] : ['PC','ANDROID','IOS']) as CompanionPlatform[]).map((platform) => {
          const slot = deviceConnections.devices[platform];
          const isConnected = slot.status === 'HOST' || slot.status === 'CONNECTED';
          const statusLabel =
            slot.status === 'HOST'
              ? 'Host'
              : slot.status === 'CONNECTED'
                ? 'Connected'
                : slot.status === 'AWAITING_HOST'
                  ? 'Standby'
                  : 'Offline';

          return (
            <button
              key={platform}
              type="button"
              onClick={() => {
                if (state.meta.environmentMode === 'DEMO' && !isDesktopHost) {
                  toggleDemoDeviceState(platform);
                } else {
                  openMonitoringSection('DEVICES_BLUETOOTH');
                }
              }}
              title={
                state.meta.environmentMode === 'DEMO' && !isDesktopHost
                  ? `Click to toggle ${slot.label} connection state`
                  : `Inspect ${slot.label} companion connection`
              }
              className="flex flex-col items-center justify-center text-center group cursor-pointer min-w-[54px]"
            >
              <div className="mb-0.5 transition-transform group-hover:scale-105">
                {platform === 'PC' && (
                  <WindowsLogoIcon
                    className={`w-4 h-4 ${
                      isConnected ? 'text-[#38BDF8]' : 'text-slate-500'
                    }`}
                  />
                )}
                {platform === 'ANDROID' && (
                  <AndroidLogoIcon
                    className={`w-4 h-4 ${
                      isConnected ? 'text-[#22C55E]' : 'text-slate-500'
                    }`}
                  />
                )}
                {platform === 'IOS' && (
                  <AppleLogoIcon
                    className={`w-4 h-4 ${
                      isConnected ? 'text-slate-100' : 'text-slate-500'
                    }`}
                  />
                )}
              </div>
              <div className="text-[10px] font-semibold text-slate-200 leading-tight">
                {slot.label}
              </div>
              <div className="text-[10px] text-slate-400 flex items-center gap-0.5 leading-tight whitespace-nowrap">
                <span>{statusLabel}</span>
                {isConnected && (
                  <Check className="w-2.5 h-2.5 text-[#22C55E] stroke-[3]" />
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* 4. Session Countdown Timer Card */}
      <button
        data-monitor="timer"
        type="button"
        onClick={toggleTimerPause}
        title={
          timer.runState === 'AWAITING_HOST'
            ? 'Timer awaiting Windows Host Bridge initialization'
            : isDesktopHost ? 'Windows controls the session timer' : 'Click to Pause / Resume Session Timer'
        }
        className="bg-[#0F141E] hover:bg-[#141B29] border border-[#1E293B] rounded-xl px-4 py-1.5 min-w-[188px] flex flex-col justify-center transition-colors cursor-pointer shrink-0"
      >
        <div className="flex items-center justify-center gap-2">
          {timer.runState === 'PAUSED' ? (
            <Play className="w-4 h-4 text-amber-400 fill-amber-400/20 shrink-0" />
          ) : timer.runState === 'RUNNING' ? (
            <Power className="w-4 h-4 text-slate-200 shrink-0" />
          ) : (
            <Pause className="w-4 h-4 text-slate-500 shrink-0" />
          )}
          <div className="font-mono tabular-nums tracking-tight whitespace-nowrap">
            {timer.providerStatus === 'AWAITING_HOST' ? (
              <span className="text-sm font-semibold text-slate-400">
                --:-- / --:--
              </span>
            ) : (
              <>
                <span className="text-[18px] font-bold text-white">
                  {formatSecondsToTimer(timer.remainingSeconds)}
                </span>
                <span className="text-[13px] text-slate-400 font-medium">
                  {' '}
                  / {formatSecondsToTimer(timer.totalDurationSeconds)}
                </span>
              </>
            )}
          </div>
        </div>
        {/* Progress Bar */}
        <div className="w-full h-1.5 bg-[#1B2434] rounded-full overflow-hidden mt-1.5">
          <div
            className={`h-full rounded-full transition-all duration-300 ${
              timer.runState === 'PAUSED'
                ? 'bg-amber-400'
                : 'bg-[#22C55E] shadow-[0_0_8px_#22C55E]'
            }`}
            style={{ width: `${timerProgressPct}%` }}
          />
        </div>
      </button>

      {/* 5. Battery & Power Telemetry Card */}
      <button
        data-monitor="battery"
        type="button"
        onClick={() => openMonitoringSection('OVERVIEW')}
        title="Inspect System Power & Battery Telemetry"
        className="bg-[#0F141E] hover:bg-[#141B29] border border-[#1E293B] rounded-xl px-3.5 py-1.5 min-w-[114px] flex flex-col items-center justify-center transition-colors cursor-pointer shrink-0"
      >
        <div className="flex items-center gap-1.5">
          {battery.isCharging ? (
            <BatteryCharging className="w-5 h-5 text-[#22C55E]" />
          ) : (
            <BatteryMedium className="w-5 h-5 text-slate-300" />
          )}
          <span className="text-[16px] font-bold text-white font-mono tabular-nums">
            {battery.percentage !== null ? `${battery.percentage}%` : '--%'}
          </span>
        </div>
        <div className="flex items-center gap-1 text-[10px] text-slate-300 mt-0.5 whitespace-nowrap">
          {battery.isCharging ? (
            <>
              <Zap className="w-3 h-3 text-[#22C55E] fill-[#22C55E]" />
              <span>Charging</span>
            </>
          ) : battery.providerStatus === 'AWAITING_HOST' ? (
            <span className="text-slate-500">Awaiting Host</span>
          ) : (
            <span>On Battery</span>
          )}
        </div>
      </button>

      {/* 6. Expandable Monitoring Details Toggle Button */}
      <button
        data-monitor="toggle"
        type="button"
        onClick={() => setIsMonitoringExpanded((prev) => !prev)}
        aria-expanded={isMonitoringExpanded}
        title={
          isMonitoringExpanded
            ? 'Collapse Monitoring Details Drawer'
            : 'Expand Detailed Monitoring & Hardware Telemetry'
        }
        className={`w-8 h-9 rounded-lg border flex items-center justify-center transition-colors cursor-pointer shrink-0 ${
          isMonitoringExpanded
            ? 'bg-[#172234] border-blue-500/50 text-blue-400'
            : 'bg-[#0F141E] hover:bg-[#161F2E] border-[#1E293B] text-slate-300'
        }`}
      >
        {isMonitoringExpanded ? (
          <ChevronUp className="w-4 h-4" />
        ) : (
          <ChevronDown className="w-4 h-4" />
        )}
      </button>
    </section>
  );
};
