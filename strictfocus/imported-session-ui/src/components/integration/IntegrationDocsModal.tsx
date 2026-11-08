/**
 * StrictRecords — Windows EXE WebView2 Integration Documentation & Live Bridge Simulator
 *
 * Provides complete C# / C++ WebView2 integration snippets, TypeScript interface contracts,
 * and a live Host Event Injector so developers can verify how Production Mode behaves when
 * external telemetry arrives from the Windows desktop host.
 */

import React, { useState } from 'react';
import {
  Check,
  Code2,
  Copy,
  Cpu,
  FlaskConical,
  Layers,
  Play,
  Radio,
  Terminal,
  X,
} from 'lucide-react';
import { useStrictSession } from '../../providers/StrictSessionContext';

interface IntegrationDocsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const IntegrationDocsModal: React.FC<IntegrationDocsModalProps> = ({
  isOpen,
  onClose,
}) => {
  const { state, setEnvironmentMode, dispatchHostEvent } = useStrictSession();
  const [activeSection, setActiveSection] = useState<
    'SIMULATOR' | 'WEBVIEW2_CSHARP' | 'ARCHITECTURE'
  >('SIMULATOR');
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  if (!isOpen) return null;

  const copyCode = (key: string, text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 1800);
  };

  // Inject a realistic Production Host Telemetry Handshake into Production Mode
  const handleSimulateProductionHandshake = () => {
    setEnvironmentMode('PRODUCTION');
    setTimeout(() => {
      dispatchHostEvent({
        type: 'HOST_STATE_SNAPSHOT',
        payload: {
          meta: {
            sessionId: 'PROD-WIN64-99412',
            environmentMode: 'PRODUCTION',
            hostBridgeConnected: true,
            hostApplicationVersion: 'StrictRecords.Host.exe v4.1.0 (WebView2 Runtime)',
            currentTimeFormatted: state.meta.currentTimeFormatted,
          },
          timer: {
            providerStatus: 'CONNECTED',
            runState: 'RUNNING',
            remainingSeconds: 45 * 60,
            totalDurationSeconds: 45 * 60,
            elapsedSeconds: 0,
            allowUserPause: false,
            sessionStartedAtIso: new Date().toISOString(),
            targetEndAtIso: new Date(Date.now() + 45 * 60000).toISOString(),
          },
          battery: {
            providerStatus: 'CONNECTED',
            percentage: 88,
            isCharging: true,
            powerSource: 'AC_CHARGING',
            estimatedMinutesRemaining: 240,
            powerSavingModeActive: false,
          },
          camera: {
            providerStatus: 'CONNECTED',
            isConnected: true,
            isStreaming: true,
            sourceType: 'BROWSER_WEBCAM',
            deviceName: 'Windows Biometric Camera Service',
            resolution: '1920x1080',
            fps: 60,
            faceDetected: true,
            faceConfidencePct: 99.2,
            gazeDirection: 'CENTER',
            isPrivacyMasked: false,
            externalFrameDataUrl: null,
          },
          screenCapture: {
            providerStatus: 'CONNECTED',
            screenShareActive: true,
            displayCount: 1,
            primaryResolution: '2560x1440 @ 144Hz',
            captureFps: 30,
            multiMonitorBlocked: true,
            hardwareAcceleration: true,
            lastCaptureTimestampIso: new Date().toISOString(),
          },
          proctoring: {
            providerStatus: 'CONNECTED',
            monitoringActive: true,
            screenProctoringEnabled: true,
            audioState: 'MUTED',
            ambientNoiseDb: -54,
            violationCount: 0,
            maxAllowedViolations: 15,
            violations: [],
            activeProcessGuard: true,
            flaggedProcesses: [],
          },
          deviceConnections: {
            providerStatus: 'CONNECTED',
            devices: {
              PC: {
                platform: 'PC',
                label: 'PC',
                status: 'HOST',
                deviceName: 'DESKTOP-STRICT-HOST',
                isVerified: true,
                batteryPct: 88,
                lastHeartbeatMs: 1,
                lockdownActive: true,
              },
              ANDROID: {
                platform: 'ANDROID',
                label: 'Android',
                status: 'CONNECTED',
                deviceName: 'Galaxy S24 Ultra (USB/ADB Lock)',
                isVerified: true,
                batteryPct: 94,
                lastHeartbeatMs: 9,
                lockdownActive: true,
              },
              IOS: {
                platform: 'IOS',
                label: 'iOS',
                status: 'CONNECTED',
                deviceName: 'iPhone 16 Pro (StrictCompanion)',
                isVerified: true,
                batteryPct: 79,
                lastHeartbeatMs: 14,
                lockdownActive: true,
              },
            },
          },
        },
      });
    }, 60);
  };

  const csharpSnippet = `// C# (.NET 8 / WPF / WinForms) WebView2 Host Integration
using Microsoft.Web.WebView2.Core;
using System.Text.Json;

private async void InitializeStrictSessionWebView()
{
    await webView.EnsureCoreWebView2Async();

    // 1. Listen for outbound UI commands (End Session, URL Audit, Timer, Window Controls)
    webView.CoreWebView2.WebMessageReceived += (sender, args) =>
    {
        string json = args.WebMessageAsJson;
        // Example: {"type":"BROWSER_NAVIGATION_ATTEMPT","payload":{"pane":"LEFT","url":"https://leetcode.com","allowed":true}}
        HandleFrontendCommand(json);
    };

    // 2. Switch frontend into strict PRODUCTION mode (disables all mock demo telemetry)
    await webView.CoreWebView2.ExecuteScriptAsync(
        "window.StrictRecordsBridge?.setEnvironmentMode('PRODUCTION');"
    );

    // 3. Push real-time hardware & proctoring telemetry via PostWebMessageAsJson
    var timerPayload = new {
        type = "HOST_TIMER_UPDATE",
        payload = new {
            runState = "RUNNING",
            remainingSeconds = 599,
            totalDurationSeconds = 600,
            elapsedSeconds = 1
        }
    };
    webView.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(timerPayload));
}`;

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="w-full max-w-4xl bg-[#0E131D] border border-[#283548] rounded-2xl shadow-2xl flex flex-col max-h-[88vh] overflow-hidden">
        {/* Modal Header */}
        <div className="px-5 py-3.5 bg-[#121824] border-b border-[#1E293B] flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-blue-500/15 border border-blue-500/30 flex items-center justify-center text-blue-400">
              <Code2 className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-white">
                StrictRecords — Windows EXE / WebView2 Integration & Bridge Console
              </h2>
              <p className="text-[11px] text-slate-400">
                External State Provider Architecture · Zero Hardcoded Production States
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="px-5 py-2 bg-[#0B0F17] border-b border-[#1E293B] flex items-center gap-2">
          <button
            type="button"
            onClick={() => setActiveSection('SIMULATOR')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 cursor-pointer ${
              activeSection === 'SIMULATOR'
                ? 'bg-blue-600/20 text-blue-300 border border-blue-500/40'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Terminal className="w-3.5 h-3.5" />
            <span>Live Host Bridge Simulator</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveSection('WEBVIEW2_CSHARP')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 cursor-pointer ${
              activeSection === 'WEBVIEW2_CSHARP'
                ? 'bg-blue-600/20 text-blue-300 border border-blue-500/40'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>C# / WebView2 Host Integration Code</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveSection('ARCHITECTURE')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 cursor-pointer ${
              activeSection === 'ARCHITECTURE'
                ? 'bg-blue-600/20 text-blue-300 border border-blue-500/40'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>Modular Architecture & Contracts</span>
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-5 overflow-y-auto space-y-4 text-xs flex-1 select-text">
          {activeSection === 'SIMULATOR' && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {/* Mode 1: DEMO MODE */}
                <div
                  className={`p-4 rounded-xl border ${
                    state.meta.environmentMode === 'DEMO'
                      ? 'bg-amber-950/20 border-amber-500/50'
                      : 'bg-[#121824] border-[#1E293B]'
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-bold text-amber-300 flex items-center gap-1.5">
                      <FlaskConical className="w-4 h-4" />
                      <span>DEMO MODE (Standalone AI Studio Testing)</span>
                    </span>
                    {state.meta.environmentMode === 'DEMO' && (
                      <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 text-[10px] font-semibold">
                        ACTIVE
                      </span>
                    )}
                  </div>
                  <p className="text-slate-400 leading-relaxed mb-3">
                    Populates the UI with clearly identified mock data matching the
                    reference design (`09:59 / 10:00`, `55% Charging`, `3 Devices Connected`)
                    for standalone UI/UX testing.
                  </p>
                  <button
                    type="button"
                    onClick={() => setEnvironmentMode('DEMO')}
                    className="px-3 py-1.5 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 text-amber-200 font-semibold cursor-pointer"
                  >
                    Activate Demo Mode (Mock Data)
                  </button>
                </div>

                {/* Mode 2: PRODUCTION MODE */}
                <div
                  className={`p-4 rounded-xl border ${
                    state.meta.environmentMode === 'PRODUCTION'
                      ? 'bg-emerald-950/20 border-emerald-500/50'
                      : 'bg-[#121824] border-[#1E293B]'
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-bold text-emerald-300 flex items-center gap-1.5">
                      <Radio className="w-4 h-4" />
                      <span>PRODUCTION MODE (Zero Simulated Data)</span>
                    </span>
                    {state.meta.environmentMode === 'PRODUCTION' && (
                      <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-[10px] font-semibold">
                        ACTIVE
                      </span>
                    )}
                  </div>
                  <p className="text-slate-400 leading-relaxed mb-3">
                    Starts with all monitoring indicators in explicit `AWAITING_HOST` /
                    `Unverified` state. Never displays simulated monitoring results. Only
                    updates when external host events arrive.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => setEnvironmentMode('PRODUCTION')}
                      className="px-3 py-1.5 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/40 text-emerald-200 font-semibold cursor-pointer"
                    >
                      Reset to Clean Production Standby
                    </button>
                    <button
                      type="button"
                      onClick={handleSimulateProductionHandshake}
                      className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold flex items-center gap-1 cursor-pointer"
                    >
                      <Play className="w-3 h-3 fill-current" />
                      <span>Inject Sample Host Handshake</span>
                    </button>
                  </div>
                </div>
              </div>

              {/* Granular Host Event Dispatchers */}
              <div className="bg-[#121824] border border-[#1E293B] rounded-xl p-4">
                <div className="font-semibold text-white mb-2.5">
                  Test Dispatching Individual `window.StrictRecordsBridge.dispatchHostEvent(...)` Payloads
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() =>
                      dispatchHostEvent({
                        type: 'HOST_BATTERY_UPDATE',
                        payload: {
                          percentage: 22,
                          isCharging: false,
                          powerSource: 'BATTERY_DISCHARGING',
                          estimatedMinutesRemaining: 38,
                        },
                      })
                    }
                    className="px-2.5 py-1.5 rounded bg-[#1A2333] hover:bg-[#232F45] border border-[#2D3C56] text-slate-200 cursor-pointer"
                  >
                    Push Low Battery (22% Discharging)
                  </button>

                  <button
                    type="button"
                    onClick={() =>
                      dispatchHostEvent({
                        type: 'HOST_CAMERA_UPDATE',
                        payload: {
                          isConnected: true,
                          isStreaming: true,
                          faceDetected: false,
                          gazeDirection: 'AWAY',
                        },
                      })
                    }
                    className="px-2.5 py-1.5 rounded bg-[#1A2333] hover:bg-[#232F45] border border-[#2D3C56] text-slate-200 cursor-pointer"
                  >
                    Push Face Missing / Gaze Away
                  </button>

                  <button
                    type="button"
                    onClick={() =>
                      dispatchHostEvent({
                        type: 'HOST_RECORD_VIOLATION',
                        payload: {
                          category: 'PROCESS_BLACKLIST',
                          severity: 'HIGH',
                          description: 'Blocked background process detected: Discord.exe',
                        },
                      })
                    }
                    className="px-2.5 py-1.5 rounded bg-[#1A2333] hover:bg-[#232F45] border border-[#2D3C56] text-amber-300 cursor-pointer"
                  >
                    Push Process Violation (Discord.exe)
                  </button>

                  <button
                    type="button"
                    onClick={() =>
                      dispatchHostEvent({
                        type: 'HOST_BROWSER_POLICY_UPDATE',
                        payload: {
                          allowedDomains: [
                            'chatgpt.com',
                            'leetcode.com',
                            'cppreference.com',
                          ],
                          focusLabel: 'C++ Competitive Programming',
                        },
                      })
                    }
                    className="px-2.5 py-1.5 rounded bg-[#1A2333] hover:bg-[#232F45] border border-[#2D3C56] text-emerald-300 cursor-pointer"
                  >
                    Push New Allowed Domain (cppreference.com)
                  </button>
                </div>
              </div>
            </div>
          )}

          {activeSection === 'WEBVIEW2_CSHARP' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-slate-300 font-medium">
                  Windows Desktop EXE (C# WebView2) Two-Way IPC Wiring
                </span>
                <button
                  type="button"
                  onClick={() => copyCode('csharp', csharpSnippet)}
                  className="px-2.5 py-1 rounded bg-[#1A2333] hover:bg-[#243046] text-slate-200 flex items-center gap-1.5 cursor-pointer"
                >
                  {copiedKey === 'csharp' ? (
                    <>
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                      <span>Copied</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3.5 h-3.5" />
                      <span>Copy C# Snippet</span>
                    </>
                  )}
                </button>
              </div>
              <pre className="p-4 rounded-xl bg-[#090D14] border border-[#1E293B] font-mono text-[11px] text-blue-200 overflow-x-auto leading-relaxed">
                <code>{csharpSnippet}</code>
              </pre>
            </div>
          )}

          {activeSection === 'ARCHITECTURE' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="bg-[#121824] border border-[#1E293B] rounded-xl p-3.5 space-y-1.5">
                <div className="font-bold text-white">
                  1. State Interfaces (`src/types/strictSession.ts`)
                </div>
                <p className="text-slate-400 leading-relaxed">
                  Strictly typed contracts for `ITimerState`, `IBatteryState`,
                  `ICameraState`, `IScreenCaptureState`, `IProctoringState`,
                  `IBluetoothState`, `IDeviceConnectionsState`, and
                  `IBrowserRestrictionState`.
                </p>
              </div>

              <div className="bg-[#121824] border border-[#1E293B] rounded-xl p-3.5 space-y-1.5">
                <div className="font-bold text-white">
                  2. Pure Business Logic (`src/services/SessionEngine.ts`)
                </div>
                <p className="text-slate-400 leading-relaxed">
                  Decoupled URL whitelist evaluator, timer formatter, device slot counter,
                  and WebView2 `window.chrome.webview.postMessage` bridge adapter.
                </p>
              </div>

              <div className="bg-[#121824] border border-[#1E293B] rounded-xl p-3.5 space-y-1.5">
                <div className="font-bold text-white">
                  3. External Provider (`src/providers/StrictSessionContext.tsx`)
                </div>
                <p className="text-slate-400 leading-relaxed">
                  Exposes `window.StrictRecordsBridge` globally and subscribes to
                  `window.chrome.webview` messages. Guarantees Production Mode never shows
                  simulated telemetry.
                </p>
              </div>

              <div className="bg-[#121824] border border-[#1E293B] rounded-xl p-3.5 space-y-1.5">
                <div className="font-bold text-white">
                  4. Design Tokens (`src/tokens/designTokens.ts`)
                </div>
                <p className="text-slate-400 leading-relaxed">
                  Centralized color, surface, typography, and layout geometry tokens
                  calibrated for 720p, 1080p, and 1440p Windows desktop DPI scaling.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
