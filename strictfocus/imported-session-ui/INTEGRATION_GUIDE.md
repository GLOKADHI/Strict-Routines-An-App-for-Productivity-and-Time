# StrictRecords — Strict Session Frontend Integration Guide

This frontend is architected as a modular, production-integration-ready React + TypeScript application for embedding inside a **Windows Desktop EXE (WebView2 / CEF)** while supporting standalone design and functional verification in **Google AI Studio**.

---

## 1. Project Structure

```text
/src
 ├── tokens/
 │    └── designTokens.ts            # Centralized colors, surfaces, layout geometry & status tokens
 ├── types/
 │    └── strictSession.ts           # Contracts for Timer, Battery, Camera, Screen, Proctoring, Bluetooth, Devices, Browser Policy & IPC
 ├── services/
 │    └── SessionEngine.ts           # Pure business logic (URL whitelist evaluation, time formatting, WebView2 message dispatch)
 ├── providers/
 │    ├── statePresets.ts            # Un-mocked Production initial state vs. clearly labeled Demo mock state
 │    └── StrictSessionContext.tsx   # External State Provider + global `window.StrictRecordsBridge` + `chrome.webview` listener
 ├── components/
 │    ├── header/
 │    │    └── CompactHeader.tsx     # Windows desktop titlebar, session status, profile selector & DEMO/PRODUCTION toggle
 │    ├── monitoring/
 │    │    ├── MonitoringBar.tsx     # Compact telemetry strip (Monitoring status, Camera, Face, Gaze, Violations, Audio, Screen, 3-Devices, Timer, Battery)
 │    │    └── MonitoringDetailsPanel.tsx # Expandable deep-dive inspection drawer
 │    ├── browser/
 │    │    ├── SplitBrowserWorkspace.tsx  # 50:50 resizable dual-browser workspace with `<->` splitter
 │    │    ├── BrowserPane.tsx            # Reusable browser chrome, tabs, history stack & domain restriction enforcement
 │    │    └── apps/
 │    │         ├── ChatGptStudyView.tsx  # Interactive study assistant workspace view
 │    │         └── LeetCodeStudyView.tsx # Interactive problem list, filter & code runner view
 │    ├── camera/
 │    │    └── FloatingCameraPip.tsx      # Draggable & resizable floating camera PIP (Host stream, Real Webcam, or Demo feed)
 │    ├── footer/
 │    │    └── SessionFooter.tsx          # Allowed domains bar, focus indicator & End Session CTA
 │    ├── integration/
 │    │    └── IntegrationDocsModal.tsx   # Live Host Bridge Simulator & C# WebView2 integration snippets
 │    └── modals/
 │         └── EndSessionModal.tsx        # Session termination confirmation & telemetry summary
 └── App.tsx
```

---

## 2. Production Mode vs. Demo Mode Guarantee

- **`PRODUCTION` Mode (`createProductionInitialState`)**:
  - Never displays simulated or fake positive monitoring results.
  - Every indicator (`timer`, `battery`, `camera`, `screenCapture`, `proctoring`, `bluetooth`, `deviceConnections`) initializes with `providerStatus: 'AWAITING_HOST'` and `null` / `UNVERIFIED` values until populated by the Windows EXE Host Bridge.
- **`DEMO` Mode (`createDemoInitialState`)**:
  - Clearly labeled in the UI header and footer (`DEMO MOCK`).
  - Pre-loads the reference study session (`09:59 / 10:00`, `55% Charging`, `PC Host ✓`, `Android Connected ✓`, `iOS Connected ✓`, `Allowed: chatgpt.com, leetcode.com`) so every UI control can be tested standalone.

---

## 3. Windows Desktop EXE (WebView2) Two-Way Integration

### A. Switching to Production Mode on Startup

In your Windows EXE (`CoreWebView2.NavigationCompleted`):

```csharp
await webView.CoreWebView2.ExecuteScriptAsync(
    "window.StrictRecordsBridge?.setEnvironmentMode('PRODUCTION');"
);
```

### B. Pushing Host Telemetry into the Frontend (`Host -> UI`)

You can push state updates either via `CoreWebView2.PostWebMessageAsJson(json)` or via `window.StrictRecordsBridge.dispatchHostEvent(eventObject)`.

Supported Inbound Event Types (`HostInboundEvent` in `src/types/strictSession.ts`):
- `HOST_STATE_SNAPSHOT` — Full or partial `IStrictSessionState` hydration
- `HOST_TIMER_UPDATE` — Updates `ITimerState` (`remainingSeconds`, `totalDurationSeconds`, `runState`)
- `HOST_BATTERY_UPDATE` — Updates `IBatteryState` (`percentage`, `isCharging`, `powerSource`)
- `HOST_CAMERA_UPDATE` — Updates `ICameraState` (`isConnected`, `faceDetected`, `gazeDirection`, `externalFrameDataUrl`)
- `HOST_SCREEN_CAPTURE_UPDATE` — Updates `IScreenCaptureState` (`screenShareActive`, `displayCount`, `primaryResolution`)
- `HOST_PROCTORING_UPDATE` — Updates `IProctoringState` (`monitoringActive`, `screenProctoringEnabled`, `audioState`, `violationCount`)
- `HOST_BLUETOOTH_UPDATE` — Updates `IBluetoothState` (`adapterState`, `devices`)
- `HOST_DEVICES_UPDATE` — Updates `IDeviceConnectionsState` (`PC`, `ANDROID`, `IOS` companion slots)
- `HOST_BROWSER_POLICY_UPDATE` — Updates `IBrowserRestrictionState` (`allowedDomains`, `focusLabel`, `sessionProfileLabel`)
- `HOST_RECORD_VIOLATION` — Appends a new `IViolationEvent` and increments `violationCount`

### C. Receiving UI Commands in the Windows Host (`UI -> Host`)

The frontend automatically posts structured JSON messages to `window.chrome.webview.postMessage(command)` whenever the user interacts with host-governed controls (`HostCommandMessage` in `src/types/strictSession.ts`):
- `SESSION_REQUEST_END`
- `TIMER_CONTROL` (`PAUSE` | `RESUME` | `RESET`)
- `BROWSER_NAVIGATION_ATTEMPT` (`pane`, `url`, `allowed`)
- `CAMERA_CONTROL` (`TOGGLE_PRIVACY_MASK` | `REQUEST_WEBCAM` | `RELEASE_WEBCAM`)
- `VIOLATION_REPORTED`
- `WINDOW_CONTROL` (`MINIMIZE` | `MAXIMIZE` | `CLOSE`)
- `DEVICE_RESYNC_REQUEST`
