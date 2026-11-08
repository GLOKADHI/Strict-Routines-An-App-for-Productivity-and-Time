/**
 * StrictRecords — Strict Session State & Host Integration Interfaces
 *
 * All UI monitoring components consume these interfaces exclusively through
 * external state providers. Production mode NEVER injects simulated or fake
 * positive monitoring states.
 */

export type EnvironmentMode = 'PRODUCTION' | 'DEMO';

export type ProviderConnectionStatus = 'CONNECTED' | 'AWAITING_HOST' | 'DISCONNECTED' | 'ERROR';

// ============================================================================
// 1. TIMER INTERFACE
// ============================================================================
export type TimerRunState = 'IDLE' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'AWAITING_HOST';

export interface ITimerState {
  providerStatus: ProviderConnectionStatus;
  runState: TimerRunState;
  remainingSeconds: number;
  totalDurationSeconds: number;
  elapsedSeconds: number;
  allowUserPause: boolean;
  sessionStartedAtIso: string | null;
  targetEndAtIso: string | null;
}

// ============================================================================
// 2. BATTERY & POWER INTERFACE
// ============================================================================
export type PowerSourceType = 'AC_CHARGING' | 'BATTERY_DISCHARGING' | 'FULL' | 'UNKNOWN';

export interface IBatteryState {
  providerStatus: ProviderConnectionStatus;
  percentage: number | null; // null in production when host/OS battery telemetry hasn't reported
  isCharging: boolean | null;
  powerSource: PowerSourceType;
  estimatedMinutesRemaining: number | null;
  powerSavingModeActive: boolean;
}

// ============================================================================
// 3. CAMERA, FACE & GAZE INTERFACE
// ============================================================================
export type GazeDirection =
  | 'CENTER'
  | 'LEFT'
  | 'RIGHT'
  | 'UP'
  | 'DOWN'
  | 'AWAY'
  | 'MULTIPLE_FACES'
  | 'UNVERIFIED';

export type CameraSourceType =
  | 'HOST_NATIVE_STREAM'
  | 'BROWSER_WEBCAM'
  | 'DEMO_SIMULATED'
  | 'NONE';

export interface ICameraState {
  providerStatus: ProviderConnectionStatus;
  isConnected: boolean;
  isStreaming: boolean;
  sourceType: CameraSourceType;
  deviceName: string | null;
  resolution: string | null;
  fps: number | null;
  faceDetected: boolean | null; // null when unverified in Production
  faceConfidencePct: number | null;
  gazeDirection: GazeDirection;
  isPrivacyMasked: boolean;
  externalFrameDataUrl: string | null; // Used when Windows EXE pushes base64/shared-buffer frames
}

// ============================================================================
// 4. SCREEN CAPTURE INTERFACE
// ============================================================================
export interface IScreenCaptureState {
  providerStatus: ProviderConnectionStatus;
  screenShareActive: boolean | null; // null when awaiting host verification in Production
  displayCount: number | null;
  primaryResolution: string | null;
  captureFps: number | null;
  multiMonitorBlocked: boolean;
  hardwareAcceleration: boolean;
  lastCaptureTimestampIso: string | null;
}

// ============================================================================
// 5. PROCTORING & VIOLATIONS INTERFACE
// ============================================================================
export type AudioMonitorState = 'MUTED' | 'ACTIVE_LISTENING' | 'DISABLED' | 'UNVERIFIED';

export type ViolationSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export type ViolationCategory =
  | 'GAZE_AWAY'
  | 'FACE_MISSING'
  | 'UNAUTHORIZED_URL'
  | 'SECONDARY_DEVICE_DROP'
  | 'PROCESS_BLACKLIST'
  | 'BLUETOOTH_AUDIO'
  | 'WINDOW_FOCUS_LOST'
  | 'SCREEN_SHARE_INTERRUPTED';

export interface IViolationEvent {
  id: string;
  timestampIso: string;
  category: ViolationCategory;
  severity: ViolationSeverity;
  description: string;
  resolved: boolean;
}

export interface IProctoringState {
  totalViolationCount?: number;
  providerStatus: ProviderConnectionStatus;
  monitoringActive: boolean;
  screenProctoringEnabled: boolean | null;
  audioState: AudioMonitorState;
  ambientNoiseDb: number | null;
  violationCount: number;
  maxAllowedViolations: number;
  violations: IViolationEvent[];
  activeProcessGuard: boolean;
  flaggedProcesses: string[];
}

// ============================================================================
// 6. BLUETOOTH & PERIPHERALS INTERFACE
// ============================================================================
export type BluetoothAdapterState = 'ON' | 'OFF' | 'RESTRICTED' | 'AWAITING_HOST';

export interface IBluetoothPeripheral {
  id: string;
  name: string;
  category: 'AUDIO_HEADSET' | 'KEYBOARD' | 'MOUSE' | 'WEARABLE' | 'UNKNOWN';
  isAllowed: boolean;
  connected: boolean;
  rssiDbm: number | null;
}

export interface IBluetoothState {
  providerStatus: ProviderConnectionStatus;
  adapterState: BluetoothAdapterState;
  wirelessAudioBlocked: boolean;
  devices: IBluetoothPeripheral[];
}

// ============================================================================
// 7. MULTI-DEVICE CONNECTIONS INTERFACE (PC / Android / iOS)
// ============================================================================
export type CompanionPlatform = 'PC' | 'ANDROID' | 'IOS';

export type DeviceLockState = 'HOST' | 'CONNECTED' | 'PAIRING' | 'DISCONNECTED' | 'AWAITING_HOST';

export interface IDeviceSlotState {
  platform: CompanionPlatform;
  label: string;
  status: DeviceLockState;
  deviceName: string | null;
  isVerified: boolean;
  batteryPct: number | null;
  lastHeartbeatMs: number | null;
  lockdownActive: boolean;
}

export interface IDeviceConnectionsState {
  providerStatus: ProviderConnectionStatus;
  devices: Record<CompanionPlatform, IDeviceSlotState>;
}

// ============================================================================
// 8. BROWSER RESTRICTIONS & SPLIT WORKSPACE INTERFACE
// ============================================================================
export interface IBrowserTab {
  id: string;
  title: string;
  url: string;
  domain: string;
  faviconType: 'CHATGPT' | 'LEETCODE' | 'DOCS' | 'BLOCKED' | 'GENERIC';
  isBlocked: boolean;
  blockedReason?: string;
  history: string[];
  historyIndex: number;
}

export interface IBrowserAttemptLog {
  id: string;
  timestampIso: string;
  paneId: 'LEFT' | 'RIGHT';
  requestedUrl: string;
  allowed: boolean;
  reason?: string;
}

export interface IBrowserRestrictionState {
  providerStatus: ProviderConnectionStatus;
  allowedDomains: string[];
  focusLabel: string;
  sessionProfileLabel: string;
  blockExternalClipboard: boolean;
  blockDevToolsShortcut: boolean;
  blockFileDownloads: boolean;
  navigationAuditLog: IBrowserAttemptLog[];
}

// ============================================================================
// 9. ROOT SESSION STATE & PROVIDER CONTRACT
// ============================================================================
export interface IStrictSessionMeta {
  sessionId: string;
  environmentMode: EnvironmentMode;
  hostBridgeConnected: boolean;
  hostApplicationVersion: string | null;
  currentTimeFormatted: string;
}

export interface IStrictSessionState {
  meta: IStrictSessionMeta;
  timer: ITimerState;
  battery: IBatteryState;
  camera: ICameraState;
  screenCapture: IScreenCaptureState;
  proctoring: IProctoringState;
  bluetooth: IBluetoothState;
  deviceConnections: IDeviceConnectionsState;
  browserRestrictions: IBrowserRestrictionState;
}

/**
 * Outbound commands sent from Frontend UI -> Windows EXE Host (WebView2)
 */
export type HostCommandMessage =
  | { type: 'SESSION_REQUEST_END'; payload: { reason: string; timestampIso: string } }
  | { type: 'TIMER_CONTROL'; payload: { action: 'PAUSE' | 'RESUME' | 'RESET' } }
  | { type: 'BROWSER_NAVIGATION_ATTEMPT'; payload: { pane: 'LEFT' | 'RIGHT'; url: string; allowed: boolean } }
  | { type: 'CAMERA_CONTROL'; payload: { action: 'TOGGLE_PRIVACY_MASK' | 'REQUEST_WEBCAM' | 'RELEASE_WEBCAM' } }
  | { type: 'VIOLATION_REPORTED'; payload: Omit<IViolationEvent, 'id' | 'timestampIso' | 'resolved'> }
  | { type: 'WINDOW_CONTROL'; payload: { action: 'MINIMIZE' | 'MAXIMIZE' | 'CLOSE' } }
  | { type: 'DEVICE_RESYNC_REQUEST'; payload: { platform?: CompanionPlatform } };

/**
 * Inbound events sent from Windows EXE Host (WebView2) -> Frontend UI
 */
export type HostInboundEvent =
  | { type: 'HOST_STATE_SNAPSHOT'; payload: Partial<IStrictSessionState> }
  | { type: 'HOST_TIMER_UPDATE'; payload: Partial<ITimerState> }
  | { type: 'HOST_BATTERY_UPDATE'; payload: Partial<IBatteryState> }
  | { type: 'HOST_CAMERA_UPDATE'; payload: Partial<ICameraState> }
  | { type: 'HOST_SCREEN_CAPTURE_UPDATE'; payload: Partial<IScreenCaptureState> }
  | { type: 'HOST_PROCTORING_UPDATE'; payload: Partial<IProctoringState> }
  | { type: 'HOST_BLUETOOTH_UPDATE'; payload: Partial<IBluetoothState> }
  | { type: 'HOST_DEVICES_UPDATE'; payload: Partial<IDeviceConnectionsState> }
  | { type: 'HOST_BROWSER_POLICY_UPDATE'; payload: Partial<IBrowserRestrictionState> }
  | { type: 'HOST_RECORD_VIOLATION'; payload: Omit<IViolationEvent, 'id' | 'timestampIso' | 'resolved'> };

declare global {
  interface Window {
    chrome?: {
      webview?: {
        postMessage: (message: HostCommandMessage | Record<string,unknown> | string) => void;
        addEventListener: (type: 'message', listener: (event: { data: any }) => void) => void;
        removeEventListener: (type: 'message', listener: (event: { data: any }) => void) => void;
      };
    };
    StrictRecordsBridge?: {
      dispatchHostEvent: (event: HostInboundEvent) => void;
      getStateSnapshot: () => IStrictSessionState;
      setEnvironmentMode: (mode: EnvironmentMode) => void;
    };
  }
}
