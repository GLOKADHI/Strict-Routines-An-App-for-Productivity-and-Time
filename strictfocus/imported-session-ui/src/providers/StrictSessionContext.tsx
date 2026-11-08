/**
 * StrictRecords — External State Provider & WebView2 Bridge Context
 *
 * Drives all UI monitoring indicators from external state providers.
 * - In `PRODUCTION` mode: Listens for real Windows EXE WebView2 messages (`window.chrome.webview`
 *   and `window.StrictRecordsBridge.dispatchHostEvent`). Never shows simulated monitoring data.
 * - In `DEMO` mode: Clearly marks mock state and enables interactive scenario testing.
 */

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { formatDesktopClock, sendCommandToHostBridge } from '../services/SessionEngine';
import {
  CameraSourceType,
  CompanionPlatform,
  EnvironmentMode,
  GazeDirection,
  HostInboundEvent,
  IStrictSessionState,
  IViolationEvent,
} from '../types/strictSession';
import { createDemoInitialState, createProductionInitialState } from './statePresets';
import {isDesktopHost,postDesktop,useDesktopRuntime} from '../services/DesktopRuntime';

export interface IStrictSessionContextValue {
  state: IStrictSessionState;
  isMonitoringExpanded: boolean;
  activeMonitoringTab:
    | 'OVERVIEW'
    | 'CAMERA_GAZE'
    | 'VIOLATIONS'
    | 'DEVICES_BLUETOOTH'
    | 'SCREEN_PROCTORING'
    | 'BROWSER_POLICY';
  isCameraPipVisible: boolean;
  isCameraPipMinimized: boolean;
  isSessionEnded: boolean;
  sessionEndSummary: {
    endedAtIso: string;
    reason: string;
    elapsedSeconds: number;
    violationsCount: number;
  } | null;

  // Actions
  setEnvironmentMode: (mode: EnvironmentMode) => void;
  setIsMonitoringExpanded: React.Dispatch<React.SetStateAction<boolean>>;
  setActiveMonitoringTab: (
    tab: IStrictSessionContextValue['activeMonitoringTab']
  ) => void;
  openMonitoringSection: (
    tab: IStrictSessionContextValue['activeMonitoringTab']
  ) => void;
  setIsCameraPipVisible: React.Dispatch<React.SetStateAction<boolean>>;
  setIsCameraPipMinimized: React.Dispatch<React.SetStateAction<boolean>>;
  dispatchHostEvent: (event: HostInboundEvent) => void;

  // Subsystem functional handlers
  toggleTimerPause: () => void;
  resetTimerToPreset: (durationSeconds: number) => void;
  toggleCameraPrivacyMask: () => void;
  setCameraSourceType: (source: CameraSourceType, deviceName?: string) => void;
  toggleAudioMute: () => void;
  toggleScreenShareState: () => void;
  toggleScreenProctoringState: () => void;
  recordViolation: (
    violation: Omit<IViolationEvent, 'id' | 'timestampIso' | 'resolved'>
  ) => void;
  resolveViolation: (violationId: string) => void;
  clearViolations: () => void;
  logBrowserNavigation: (
    paneId: 'LEFT' | 'RIGHT',
    requestedUrl: string,
    allowed: boolean,
    reason?: string
  ) => void;
  addAllowedDomain: (domain: string) => void;
  removeAllowedDomain: (domain: string) => void;
  cycleDemoGazeDirection: () => void;
  toggleDemoDeviceState: (platform: CompanionPlatform) => void;
  toggleBluetoothDeviceAllowed: (deviceId: string) => void;
  endStrictSession: (reason?: string) => void;
  restartStrictSession: () => void;
}

const StrictSessionContext = createContext<IStrictSessionContextValue | null>(null);

export const StrictSessionProvider: React.FC<{
  initialMode?: EnvironmentMode;
  children: React.ReactNode;
}> = ({ initialMode = 'DEMO', children }) => {
  const runtime=useDesktopRuntime();
  const [state, setState] = useState<IStrictSessionState>(() =>
    isDesktopHost || initialMode === 'PRODUCTION' ? createProductionInitialState() : createDemoInitialState()
  );
  useEffect(()=>{if(isDesktopHost && runtime.snapshot)setState(runtime.snapshot);},[runtime.snapshot]);

  const [isMonitoringExpanded, setIsMonitoringExpanded] = useState<boolean>(false);
  const [activeMonitoringTab, setActiveMonitoringTab] =
    useState<IStrictSessionContextValue['activeMonitoringTab']>('OVERVIEW');
  const [isCameraPipVisible, setIsCameraPipVisible] = useState<boolean>(true);
  const [isCameraPipMinimized, setIsCameraPipMinimized] = useState<boolean>(false);
  const [isSessionEnded, setIsSessionEnded] = useState<boolean>(false);
  const [sessionEndSummary, setSessionEndSummary] =
    useState<IStrictSessionContextValue['sessionEndSummary']>(null);

  const stateRef = useRef<IStrictSessionState>(state);
  stateRef.current = state;

  // Switch between DEMO mode (mock telemetry) and PRODUCTION mode (strictly un-mocked external host state)
  const setEnvironmentMode = useCallback((mode: EnvironmentMode) => {
    if(isDesktopHost)return;
    setIsSessionEnded(false);
    setSessionEndSummary(null);
    if (mode === 'PRODUCTION') {
      setState(createProductionInitialState());
    } else {
      setState(createDemoInitialState());
    }
  }, []);

  // Central dispatcher for inbound events from Windows EXE Host (WebView2) or Integration Simulator
  const dispatchHostEvent = useCallback((event: HostInboundEvent) => {
    if(isDesktopHost)return;
    setState((prev) => {
      const updatedMeta = {
        ...prev.meta,
        hostBridgeConnected: true,
      };

      switch (event.type) {
        case 'HOST_STATE_SNAPSHOT':
          return {
            ...prev,
            ...event.payload,
            meta: {
              ...updatedMeta,
              ...(event.payload.meta || {}),
            },
          };

        case 'HOST_TIMER_UPDATE':
          return {
            ...prev,
            meta: updatedMeta,
            timer: {
              ...prev.timer,
              providerStatus: 'CONNECTED',
              ...event.payload,
            },
          };

        case 'HOST_BATTERY_UPDATE':
          return {
            ...prev,
            meta: updatedMeta,
            battery: {
              ...prev.battery,
              providerStatus: 'CONNECTED',
              ...event.payload,
            },
          };

        case 'HOST_CAMERA_UPDATE':
          return {
            ...prev,
            meta: updatedMeta,
            camera: {
              ...prev.camera,
              providerStatus: 'CONNECTED',
              ...event.payload,
            },
          };

        case 'HOST_SCREEN_CAPTURE_UPDATE':
          return {
            ...prev,
            meta: updatedMeta,
            screenCapture: {
              ...prev.screenCapture,
              providerStatus: 'CONNECTED',
              ...event.payload,
            },
          };

        case 'HOST_PROCTORING_UPDATE':
          return {
            ...prev,
            meta: updatedMeta,
            proctoring: {
              ...prev.proctoring,
              providerStatus: 'CONNECTED',
              ...event.payload,
            },
          };

        case 'HOST_BLUETOOTH_UPDATE':
          return {
            ...prev,
            meta: updatedMeta,
            bluetooth: {
              ...prev.bluetooth,
              providerStatus: 'CONNECTED',
              ...event.payload,
            },
          };

        case 'HOST_DEVICES_UPDATE':
          return {
            ...prev,
            meta: updatedMeta,
            deviceConnections: {
              ...prev.deviceConnections,
              providerStatus: 'CONNECTED',
              ...event.payload,
              devices: {
                ...prev.deviceConnections.devices,
                ...(event.payload.devices || {}),
              },
            },
          };

        case 'HOST_BROWSER_POLICY_UPDATE':
          return {
            ...prev,
            meta: updatedMeta,
            browserRestrictions: {
              ...prev.browserRestrictions,
              providerStatus: 'CONNECTED',
              ...event.payload,
            },
          };

        case 'HOST_RECORD_VIOLATION': {
          const newViolation: IViolationEvent = {
            id: `v-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            timestampIso: new Date().toISOString(),
            resolved: false,
            ...event.payload,
          };
          return {
            ...prev,
            meta: updatedMeta,
            proctoring: {
              ...prev.proctoring,
              providerStatus: 'CONNECTED',
              violationCount: prev.proctoring.violationCount + 1,
              violations: [newViolation, ...prev.proctoring.violations],
            },
          };
        }

        default:
          return prev;
      }
    });
  }, []);

  // Register global `window.StrictRecordsBridge` & WebView2 message listener
  useEffect(() => {
    window.StrictRecordsBridge = {
      dispatchHostEvent,
      getStateSnapshot: () => stateRef.current,
      setEnvironmentMode,
    };

    const handleWebViewMessage = (event: { data: HostInboundEvent | string }) => {
      try {
        const parsed: HostInboundEvent =
          typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
        if (parsed && typeof parsed === 'object' && 'type' in parsed) {
          dispatchHostEvent(parsed);
        }
      } catch {
        // Ignore malformed non-JSON messages
      }
    };

    window.chrome?.webview?.addEventListener('message', handleWebViewMessage);
    return () => {
      window.chrome?.webview?.removeEventListener('message', handleWebViewMessage);
    };
  }, [dispatchHostEvent, setEnvironmentMode]);

  // Active countdown timer tick (only ticks when timer.runState === 'RUNNING')
  useEffect(() => {
    if (isDesktopHost || isSessionEnded || state.timer.runState !== 'RUNNING') {
      return;
    }

    const interval = window.setInterval(() => {
      setState((prev) => {
        if (prev.timer.runState !== 'RUNNING') return prev;
        if (prev.timer.remainingSeconds <= 1) {
          return {
            ...prev,
            timer: {
              ...prev.timer,
              remainingSeconds: 0,
              elapsedSeconds: prev.timer.totalDurationSeconds,
              runState: 'COMPLETED',
            },
          };
        }
        return {
          ...prev,
          timer: {
            ...prev.timer,
            remainingSeconds: prev.timer.remainingSeconds - 1,
            elapsedSeconds: prev.timer.elapsedSeconds + 1,
          },
        };
      });
    }, 1000);

    return () => window.clearInterval(interval);
  }, [state.timer.runState, isSessionEnded]);

  // Live clock update in Production mode
  useEffect(() => {
    if (state.meta.environmentMode !== 'PRODUCTION') return;
    const clockTimer = window.setInterval(() => {
      setState((prev) => ({
        ...prev,
        meta: {
          ...prev.meta,
          currentTimeFormatted: formatDesktopClock(),
        },
      }));
    }, 15000);
    return () => window.clearInterval(clockTimer);
  }, [state.meta.environmentMode]);

  const openMonitoringSection = useCallback(
    (tab: IStrictSessionContextValue['activeMonitoringTab']) => {
      setActiveMonitoringTab(tab);
      setIsMonitoringExpanded(true);
    },
    []
  );

  const toggleTimerPause = useCallback(() => {
    setState((prev) => {
      if (prev.timer.runState === 'AWAITING_HOST') return prev;
      const nextRunState = prev.timer.runState === 'RUNNING' ? 'PAUSED' : 'RUNNING';
      sendCommandToHostBridge({
        type: 'TIMER_CONTROL',
        payload: { action: nextRunState === 'PAUSED' ? 'PAUSE' : 'RESUME' },
      });
      return {
        ...prev,
        timer: {
          ...prev.timer,
          runState: nextRunState,
        },
      };
    });
  }, []);

  const resetTimerToPreset = useCallback((durationSeconds: number) => {
    sendCommandToHostBridge({
      type: 'TIMER_CONTROL',
      payload: { action: 'RESET' },
    });
    setState((prev) => ({
      ...prev,
      timer: {
        ...prev.timer,
        providerStatus: 'CONNECTED',
        runState: 'RUNNING',
        totalDurationSeconds: durationSeconds,
        remainingSeconds: durationSeconds,
        elapsedSeconds: 0,
      },
    }));
  }, []);

  const toggleCameraPrivacyMask = useCallback(() => {
    sendCommandToHostBridge({
      type: 'CAMERA_CONTROL',
      payload: { action: 'TOGGLE_PRIVACY_MASK' },
    });
    setState((prev) => ({
      ...prev,
      camera: {
        ...prev.camera,
        isPrivacyMasked: !prev.camera.isPrivacyMasked,
      },
    }));
  }, []);

  const setCameraSourceType = useCallback((source: CameraSourceType, deviceName?: string) => {
    setState((prev) => ({
      ...prev,
      camera: {
        ...prev.camera,
        providerStatus: source === 'NONE' ? prev.camera.providerStatus : 'CONNECTED',
        isConnected: source !== 'NONE',
        isStreaming: source !== 'NONE',
        sourceType: source,
        deviceName:
          deviceName ??
          (source === 'BROWSER_WEBCAM'
            ? 'Hardware USB/Integrated Webcam (Live)'
            : source === 'DEMO_SIMULATED'
              ? 'Integrated IR FHD Webcam (Demo Feed)'
              : null),
        faceDetected: source === 'NONE' ? null : true,
        gazeDirection: source === 'NONE' ? 'UNVERIFIED' : 'CENTER',
      },
    }));
  }, []);

  const toggleAudioMute = useCallback(() => {
    setState((prev) => {
      if (prev.proctoring.providerStatus === 'AWAITING_HOST') return prev;
      const nextAudio =
        prev.proctoring.audioState === 'MUTED' ? 'ACTIVE_LISTENING' : 'MUTED';
      return {
        ...prev,
        proctoring: {
          ...prev.proctoring,
          audioState: nextAudio,
        },
      };
    });
  }, []);

  const toggleScreenShareState = useCallback(() => {
    setState((prev) => {
      if (prev.screenCapture.providerStatus === 'AWAITING_HOST') return prev;
      const nextActive = !prev.screenCapture.screenShareActive;
      return {
        ...prev,
        screenCapture: {
          ...prev.screenCapture,
          screenShareActive: nextActive,
          lastCaptureTimestampIso: nextActive ? new Date().toISOString() : prev.screenCapture.lastCaptureTimestampIso,
        },
      };
    });
  }, []);

  const toggleScreenProctoringState = useCallback(() => {
    setState((prev) => {
      if (prev.proctoring.providerStatus === 'AWAITING_HOST') return prev;
      return {
        ...prev,
        proctoring: {
          ...prev.proctoring,
          screenProctoringEnabled: !prev.proctoring.screenProctoringEnabled,
        },
      };
    });
  }, []);

  const recordViolation = useCallback(
    (violation: Omit<IViolationEvent, 'id' | 'timestampIso' | 'resolved'>) => {
      sendCommandToHostBridge({
        type: 'VIOLATION_REPORTED',
        payload: violation,
      });

      setState((prev) => {
        const eventObj: IViolationEvent = {
          id: `v-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          timestampIso: new Date().toISOString(),
          resolved: false,
          ...violation,
        };
        return {
          ...prev,
          proctoring: {
            ...prev.proctoring,
            violationCount: prev.proctoring.violationCount + 1,
            violations: [eventObj, ...prev.proctoring.violations],
          },
        };
      });
    },
    []
  );

  const resolveViolation = useCallback((violationId: string) => {
    setState((prev) => ({
      ...prev,
      proctoring: {
        ...prev.proctoring,
        violations: prev.proctoring.violations.map((v) =>
          v.id === violationId ? { ...v, resolved: true } : v
        ),
      },
    }));
  }, []);

  const clearViolations = useCallback(() => {
    setState((prev) => ({
      ...prev,
      proctoring: {
        ...prev.proctoring,
        violationCount: 0,
        violations: [],
      },
    }));
  }, []);

  const logBrowserNavigation = useCallback(
    (paneId: 'LEFT' | 'RIGHT', requestedUrl: string, allowed: boolean, reason?: string) => {
      sendCommandToHostBridge({
        type: 'BROWSER_NAVIGATION_ATTEMPT',
        payload: { pane: paneId, url: requestedUrl, allowed },
      });

      setState((prev) => {
        const entry = {
          id: `nav-${Date.now()}-${Math.random().toString(36).slice(2, 5)}`,
          timestampIso: new Date().toISOString(),
          paneId,
          requestedUrl,
          allowed,
          reason,
        };

        let nextProctoring = prev.proctoring;
        if (!allowed) {
          const violationEvent: IViolationEvent = {
            id: `v-nav-${Date.now()}`,
            timestampIso: entry.timestampIso,
            category: 'UNAUTHORIZED_URL',
            severity: 'MEDIUM',
            description: `Blocked navigation attempt in ${paneId} pane: ${requestedUrl}`,
            resolved: false,
          };
          nextProctoring = {
            ...prev.proctoring,
            violationCount: prev.proctoring.violationCount + 1,
            violations: [violationEvent, ...prev.proctoring.violations],
          };
        }

        return {
          ...prev,
          proctoring: nextProctoring,
          browserRestrictions: {
            ...prev.browserRestrictions,
            navigationAuditLog: [entry, ...prev.browserRestrictions.navigationAuditLog.slice(0, 49)],
          },
        };
      });
    },
    []
  );

  const addAllowedDomain = useCallback((domain: string) => {
    const cleaned = domain.toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '').replace(/\/.*$/, '').trim();
    if (!cleaned || !cleaned.includes('.')) return;

    setState((prev) => {
      if (prev.browserRestrictions.allowedDomains.includes(cleaned)) return prev;
      return {
        ...prev,
        browserRestrictions: {
          ...prev.browserRestrictions,
          allowedDomains: [...prev.browserRestrictions.allowedDomains, cleaned],
        },
      };
    });
  }, []);

  const removeAllowedDomain = useCallback((domain: string) => {
    setState((prev) => ({
      ...prev,
      browserRestrictions: {
        ...prev.browserRestrictions,
        allowedDomains: prev.browserRestrictions.allowedDomains.filter((d) => d !== domain),
      },
    }));
  }, []);

  const cycleDemoGazeDirection = useCallback(() => {
    const order: GazeDirection[] = ['CENTER', 'LEFT', 'RIGHT', 'AWAY', 'MULTIPLE_FACES'];
    setState((prev) => {
      if (prev.meta.environmentMode === 'PRODUCTION' && prev.camera.providerStatus === 'AWAITING_HOST') {
        return prev;
      }
      const idx = order.indexOf(prev.camera.gazeDirection);
      const nextGaze = order[(idx + 1) % order.length];
      const nextFaceDetected = nextGaze !== 'AWAY';
      return {
        ...prev,
        camera: {
          ...prev.camera,
          gazeDirection: nextGaze,
          faceDetected: nextFaceDetected,
        },
      };
    });
  }, []);

  const toggleDemoDeviceState = useCallback((platform: CompanionPlatform) => {
    setState((prev) => {
      const current = prev.deviceConnections.devices[platform];
      if (prev.meta.environmentMode === 'PRODUCTION' && prev.deviceConnections.providerStatus === 'AWAITING_HOST') {
        sendCommandToHostBridge({ type: 'DEVICE_RESYNC_REQUEST', payload: { platform } });
        return prev;
      }
      const nextStatus =
        current.status === 'DISCONNECTED'
          ? platform === 'PC'
            ? 'HOST'
            : 'CONNECTED'
          : 'DISCONNECTED';

      return {
        ...prev,
        deviceConnections: {
          ...prev.deviceConnections,
          devices: {
            ...prev.deviceConnections.devices,
            [platform]: {
              ...current,
              status: nextStatus,
              isVerified: nextStatus !== 'DISCONNECTED',
            },
          },
        },
      };
    });
  }, []);

  const toggleBluetoothDeviceAllowed = useCallback((deviceId: string) => {
    setState((prev) => ({
      ...prev,
      bluetooth: {
        ...prev.bluetooth,
        devices: prev.bluetooth.devices.map((d) =>
          d.id === deviceId ? { ...d, isAllowed: !d.isAllowed, connected: !d.isAllowed } : d
        ),
      },
    }));
  }, []);

  const endStrictSession = useCallback(
    (reason = 'User requested session termination') => {
      if(isDesktopHost){postDesktop({type:'SESSION_REQUEST_END'});return;}
      const endedAtIso = new Date().toISOString();
      sendCommandToHostBridge({
        type: 'SESSION_REQUEST_END',
        payload: { reason, timestampIso: endedAtIso },
      });
      setSessionEndSummary({
        endedAtIso,
        reason,
        elapsedSeconds: stateRef.current.timer.elapsedSeconds,
        violationsCount: stateRef.current.proctoring.violationCount,
      });
      setIsSessionEnded(true);
      setState((prev) => ({
        ...prev,
        timer: {
          ...prev.timer,
          runState: 'PAUSED',
        },
        proctoring: {
          ...prev.proctoring,
          monitoringActive: false,
        },
      }));
    },
    []
  );

  const restartStrictSession = useCallback(() => {
    setIsSessionEnded(false);
    setSessionEndSummary(null);
    setState((prev) =>
      prev.meta.environmentMode === 'PRODUCTION'
        ? createProductionInitialState()
        : createDemoInitialState()
    );
  }, []);

  return (
    <StrictSessionContext.Provider
      value={{
        state,
        isMonitoringExpanded,
        activeMonitoringTab,
        isCameraPipVisible,
        isCameraPipMinimized,
        isSessionEnded,
        sessionEndSummary,
        setEnvironmentMode:isDesktopHost?()=>{}:setEnvironmentMode,
        setIsMonitoringExpanded,
        setActiveMonitoringTab,
        openMonitoringSection,
        setIsCameraPipVisible,
        setIsCameraPipMinimized,
        dispatchHostEvent:isDesktopHost?()=>{}:dispatchHostEvent,
        toggleTimerPause:isDesktopHost?()=>openMonitoringSection('OVERVIEW'):toggleTimerPause,
        resetTimerToPreset:isDesktopHost?()=>{}:resetTimerToPreset,
        toggleCameraPrivacyMask,
        setCameraSourceType:isDesktopHost?()=>{}:setCameraSourceType,
        toggleAudioMute:isDesktopHost?()=>openMonitoringSection('DEVICES_BLUETOOTH'):toggleAudioMute,
        toggleScreenShareState:isDesktopHost?()=>openMonitoringSection('SCREEN_PROCTORING'):toggleScreenShareState,
        toggleScreenProctoringState:isDesktopHost?()=>openMonitoringSection('SCREEN_PROCTORING'):toggleScreenProctoringState,
        recordViolation:isDesktopHost?()=>{}:recordViolation,
        resolveViolation:isDesktopHost?()=>{}:resolveViolation,
        clearViolations:isDesktopHost?()=>{}:clearViolations,
        logBrowserNavigation:isDesktopHost?()=>{}:logBrowserNavigation,
        addAllowedDomain:isDesktopHost?()=>{}:addAllowedDomain,
        removeAllowedDomain:isDesktopHost?()=>{}:removeAllowedDomain,
        cycleDemoGazeDirection:isDesktopHost?()=>openMonitoringSection('CAMERA_GAZE'):cycleDemoGazeDirection,
        toggleDemoDeviceState:isDesktopHost?()=>openMonitoringSection('DEVICES_BLUETOOTH'):toggleDemoDeviceState,
        toggleBluetoothDeviceAllowed:isDesktopHost?()=>{}:toggleBluetoothDeviceAllowed,
        endStrictSession,
        restartStrictSession:isDesktopHost?()=>{}:restartStrictSession,
      }}
    >
      {children}
    </StrictSessionContext.Provider>
  );
};

export function useStrictSession(): IStrictSessionContextValue {
  const ctx = useContext(StrictSessionContext);
  if (!ctx) {
    throw new Error('useStrictSession must be used within a StrictSessionProvider');
  }
  return ctx;
}
