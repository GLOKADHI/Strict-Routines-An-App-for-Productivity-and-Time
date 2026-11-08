/**
 * StrictRecords — External State Presets (Production vs. Demo Mode)
 *
 * CRITICAL GUARANTEE:
 * - `createProductionInitialState()` NEVER contains simulated or fake positive monitoring data.
 *   All indicators start in an explicit `AWAITING_HOST` / `UNVERIFIED` / `null` state until
 *   populated by the Windows EXE WebView2 Host Bridge or real browser hardware APIs.
 * - `createDemoInitialState()` provides clearly identified mock data for standalone testing
 *   and design verification in Google AI Studio.
 */

import { formatDesktopClock } from '../services/SessionEngine';
import { IStrictSessionState } from '../types/strictSession';

export function createProductionInitialState(): IStrictSessionState {
  return {
    meta: {
      sessionId: 'PROD-AWAITING-HANDSHAKE',
      environmentMode: 'PRODUCTION',
      hostBridgeConnected: false,
      hostApplicationVersion: null,
      currentTimeFormatted: formatDesktopClock(),
    },
    timer: {
      providerStatus: 'AWAITING_HOST',
      runState: 'AWAITING_HOST',
      remainingSeconds: 0,
      totalDurationSeconds: 0,
      elapsedSeconds: 0,
      allowUserPause: false,
      sessionStartedAtIso: null,
      targetEndAtIso: null,
    },
    battery: {
      providerStatus: 'AWAITING_HOST',
      percentage: null,
      isCharging: null,
      powerSource: 'UNKNOWN',
      estimatedMinutesRemaining: null,
      powerSavingModeActive: false,
    },
    camera: {
      providerStatus: 'AWAITING_HOST',
      isConnected: false,
      isStreaming: false,
      sourceType: 'NONE',
      deviceName: null,
      resolution: null,
      fps: null,
      faceDetected: null,
      faceConfidencePct: null,
      gazeDirection: 'UNVERIFIED',
      isPrivacyMasked: false,
      externalFrameDataUrl: null,
    },
    screenCapture: {
      providerStatus: 'AWAITING_HOST',
      screenShareActive: null,
      displayCount: null,
      primaryResolution: null,
      captureFps: null,
      multiMonitorBlocked: false,
      hardwareAcceleration: false,
      lastCaptureTimestampIso: null,
    },
    proctoring: {
      providerStatus: 'AWAITING_HOST',
      monitoringActive: false,
      screenProctoringEnabled: null,
      audioState: 'UNVERIFIED',
      ambientNoiseDb: null,
      violationCount: 0,
      maxAllowedViolations: 15,
      violations: [],
      activeProcessGuard: false,
      flaggedProcesses: [],
    },
    bluetooth: {
      providerStatus: 'AWAITING_HOST',
      adapterState: 'AWAITING_HOST',
      wirelessAudioBlocked: false,
      devices: [],
    },
    deviceConnections: {
      providerStatus: 'AWAITING_HOST',
      devices: {
        PC: {
          platform: 'PC',
          label: 'PC',
          status: 'AWAITING_HOST',
          deviceName: null,
          isVerified: false,
          batteryPct: null,
          lastHeartbeatMs: null,
          lockdownActive: false,
        },
        ANDROID: {
          platform: 'ANDROID',
          label: 'Android',
          status: 'AWAITING_HOST',
          deviceName: null,
          isVerified: false,
          batteryPct: null,
          lastHeartbeatMs: null,
          lockdownActive: false,
        },
        IOS: {
          platform: 'IOS',
          label: 'iOS',
          status: 'AWAITING_HOST',
          deviceName: null,
          isVerified: false,
          batteryPct: null,
          lastHeartbeatMs: null,
          lockdownActive: false,
        },
      },
    },
    browserRestrictions: {
      providerStatus: 'AWAITING_HOST',
      allowedDomains: ['chatgpt.com', 'leetcode.com'],
      focusLabel: 'DSA + Practice',
      sessionProfileLabel: 'Study Mode (DSA + Practice)',
      blockExternalClipboard: true,
      blockDevToolsShortcut: true,
      blockFileDownloads: true,
      navigationAuditLog: [],
    },
  };
}

export function createDemoInitialState(): IStrictSessionState {
  const nowIso = new Date().toISOString();
  return {
    meta: {
      sessionId: 'DEMO-SR-2025-0127',
      environmentMode: 'DEMO',
      hostBridgeConnected: true,
      hostApplicationVersion: 'StrictRecords.Desktop.Win64 v3.4.2 (DEMO MOCK)',
      currentTimeFormatted: '10:00 AM, Mon Jan 27, 2025',
    },
    timer: {
      providerStatus: 'CONNECTED',
      runState: 'RUNNING',
      remainingSeconds: 9 * 60 + 59, // 09:59 as in reference screenshot
      totalDurationSeconds: 10 * 60, // 10:00 as in reference screenshot
      elapsedSeconds: 1,
      allowUserPause: true,
      sessionStartedAtIso: nowIso,
      targetEndAtIso: new Date(Date.now() + 599 * 1000).toISOString(),
    },
    battery: {
      providerStatus: 'CONNECTED',
      percentage: 55,
      isCharging: true,
      powerSource: 'AC_CHARGING',
      estimatedMinutesRemaining: 145,
      powerSavingModeActive: false,
    },
    camera: {
      providerStatus: 'CONNECTED',
      isConnected: true,
      isStreaming: true,
      sourceType: 'DEMO_SIMULATED',
      deviceName: 'Integrated IR FHD Webcam (Demo Feed)',
      resolution: '1280x720',
      fps: 30,
      faceDetected: true,
      faceConfidencePct: 98.4,
      gazeDirection: 'CENTER',
      isPrivacyMasked: false,
      externalFrameDataUrl: null,
    },
    screenCapture: {
      providerStatus: 'CONNECTED',
      screenShareActive: true,
      displayCount: 1,
      primaryResolution: '1920x1080 @ 60Hz',
      captureFps: 15,
      multiMonitorBlocked: true,
      hardwareAcceleration: true,
      lastCaptureTimestampIso: nowIso,
    },
    proctoring: {
      providerStatus: 'CONNECTED',
      monitoringActive: true,
      screenProctoringEnabled: true,
      audioState: 'MUTED',
      ambientNoiseDb: -48,
      violationCount: 0,
      maxAllowedViolations: 15,
      violations: [],
      activeProcessGuard: true,
      flaggedProcesses: [],
    },
    bluetooth: {
      providerStatus: 'CONNECTED',
      adapterState: 'RESTRICTED',
      wirelessAudioBlocked: true,
      devices: [
        {
          id: 'bt-1',
          name: 'MX Master 3S Mouse',
          category: 'MOUSE',
          isAllowed: true,
          connected: true,
          rssiDbm: -52,
        },
        {
          id: 'bt-2',
          name: 'Keychron K2 Pro',
          category: 'KEYBOARD',
          isAllowed: true,
          connected: true,
          rssiDbm: -49,
        },
        {
          id: 'bt-3',
          name: 'Sony WH-1000XM5 (Audio Blocked)',
          category: 'AUDIO_HEADSET',
          isAllowed: false,
          connected: false,
          rssiDbm: -71,
        },
      ],
    },
    deviceConnections: {
      providerStatus: 'CONNECTED',
      devices: {
        PC: {
          platform: 'PC',
          label: 'PC',
          status: 'HOST',
          deviceName: 'WIN-WORKSTATION-01 (WebView2 Host)',
          isVerified: true,
          batteryPct: 55,
          lastHeartbeatMs: 4,
          lockdownActive: true,
        },
        ANDROID: {
          platform: 'ANDROID',
          label: 'Android',
          status: 'CONNECTED',
          deviceName: 'Pixel 8 Pro · StrictCompanion',
          isVerified: true,
          batteryPct: 82,
          lastHeartbeatMs: 18,
          lockdownActive: true,
        },
        IOS: {
          platform: 'IOS',
          label: 'iOS',
          status: 'CONNECTED',
          deviceName: 'iPad Air (5th Gen) · Guided Lock',
          isVerified: true,
          batteryPct: 91,
          lastHeartbeatMs: 22,
          lockdownActive: true,
        },
      },
    },
    browserRestrictions: {
      providerStatus: 'CONNECTED',
      allowedDomains: ['chatgpt.com', 'leetcode.com'],
      focusLabel: 'DSA + Practice',
      sessionProfileLabel: 'Study Mode (DSA + Practice)',
      blockExternalClipboard: true,
      blockDevToolsShortcut: true,
      blockFileDownloads: true,
      navigationAuditLog: [
        {
          id: 'nav-init-1',
          timestampIso: nowIso,
          paneId: 'LEFT',
          requestedUrl: 'https://chatgpt.com',
          allowed: true,
        },
        {
          id: 'nav-init-2',
          timestampIso: nowIso,
          paneId: 'RIGHT',
          requestedUrl: 'https://leetcode.com',
          allowed: true,
        },
      ],
    },
  };
}
