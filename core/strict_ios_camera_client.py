"""First-party, explicit-consent, video-only iOS camera WebRTC sender.

The existing timer companion is deliberately independent. Camera credentials live
only in this page's memory; no media frames or recordings are sent through REST.
"""

HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#101019"><title>StrictRecords – iPhone Camera Companion</title>
<link rel="stylesheet" href="/ios-camera/client.css"></head>
<body><main>
<header>
  <div class="brand"><strong>◈ StrictRecords</strong><span>iPhone Camera Companion</span></div>
  <span id="timerSyncStatus" class="timer-badge" data-state="pending">Connecting to Windows</span>
</header>
<section class="session-box">
  <span>Connected Windows Session:</span> <strong id="sessionDisplay">Connecting to Windows…</strong>
</section>
<section class="media-health-card" id="mediaHealthSection">
  <div class="health-header">
    <span class="health-title">iOS Media Health:</span>
    <strong id="compactMediaStatus" data-state="pending">Initializing…</strong>
  </div>
  <div class="health-details">
    <div class="health-source-pill"><span>Camera:</span> <span id="camHealthPill" class="pill" data-state="inactive">Off</span></div>
    <div class="health-source-pill"><span>Screen:</span> <span id="scrHealthPill" class="pill" data-state="inactive">Off</span></div>
    <div class="health-hb-time"><span>Last Heartbeat:</span> <span id="lastHeartbeatTime">None</span></div>
  </div>
</section>
<section class="timer-card">
  <div id="companionTimer" role="timer" aria-label="Windows session countdown">--:--</div>
  <p id="timerPhase">Waiting for Windows to start session</p>
  <button id="fullscreenTimer" class="display-control" type="button">Expand to Fullscreen</button>
</section>
<section class="camera-card">
  <div class="permission-box"><span id="cameraPermissionStatus">Camera permission: Not requested</span></div>
  <div class="preview">
    <video id="cameraPreview" muted autoplay playsinline aria-label="Your camera preview"></video>
    <span id="cameraActive" hidden>● VIDEO STREAMING TO WINDOWS</span>
    <span id="cameraPlaceholder">Camera preview is off</span>
  </div>
  <div class="actions">
    <button id="startCamera" type="button" disabled>Start Camera</button>
    <button id="stopCamera" type="button" disabled>Stop Camera</button>
  </div>
  <p id="cameraState" role="status" aria-live="polite">Connecting this iPad to Windows before camera setup…</p>
  <p id="cameraHelp">Start Camera asks permission to send video to your Windows session for additional validation. No microphone or recording. Keep this page visible; locking or backgrounding it interrupts camera and timer reporting.</p>
</section>
<section class="camera-card screen-card" id="screenShareSection">
  <div class="permission-box"><span id="screenCapStatus">iPad Screen Sharing: Detecting capability…</span></div>
  <div class="preview">
    <video id="screenPreview" muted autoplay playsinline aria-label="Your iPad screen share preview"></video>
    <span id="screenActive" hidden>● iPAD SCREEN STREAMING TO WINDOWS</span>
    <span id="screenPlaceholder">Screen sharing is off</span>
  </div>
  <div class="actions">
    <button id="startScreenShare" type="button" disabled>Share iPad Screen</button>
    <button id="stopScreenShare" type="button" disabled>Stop Screen Share</button>
  </div>
  <p id="screenState" role="status" aria-live="polite">Tap 'Share iPad Screen' to stream your display during session.</p>
  <p id="screenHelp">Whole-device iPad screen sharing streams your display directly to the Windows proctoring engine. Zero microphone access. Keep study apps visible.</p>
</section>
<details id="diagnosticsDetails">
  <summary>Connection diagnostics & logs</summary>
  <dl id="cameraDiagnostics"></dl>
  <div style="margin-top:12px;display:flex;gap:8px;">
    <button id="exportDiagnosticsBtn" type="button" style="background:#23202b;border:1px solid #484157;color:#e8eded;font-size:0.85rem;min-height:38px;padding:8px 14px;">📋 Export Diagnostics</button>
  </div>
</details>
<p class="fine">Video-only encrypted stream. Microphone is strictly never requested or accessed. Video streaming is confirmed only after Windows receives actual frames.</p>
</main><script src="/ios-camera/client.js" defer></script></body></html>'''

CSS = r'''
*{box-sizing:border-box}body{margin:0;background:#101019;color:#e8eded;font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,sans-serif}
main{max-width:680px;margin:auto;padding:max(20px,env(safe-area-inset-top)) 18px max(24px,env(safe-area-inset-bottom));display:flex;flex-direction:column;gap:18px}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px}
.brand strong{font-size:1.15rem;color:#b58aff}
.brand span{font-size:.85rem;color:#9baeb0}
.timer-badge{padding:4px 10px;border-radius:20px;background:#153629;color:#72eaa8;font-size:.75rem;font-weight:600;border:1px solid #2a7550}
.timer-badge[data-state="pending"]{background:#242932;color:#b8c2cf;border-color:#414b5a}.timer-badge[data-state="warning"]{background:#332b1d;color:#e5c483;border-color:#665332}
.session-box{background:#161622;border:1px solid #362a47;border-radius:12px;padding:12px 16px;font-size:.85rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.session-box strong{color:#d6beff;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.timer-card{background:linear-gradient(145deg,#1f1a2e,#12111a);border:1px solid #49385f;border-radius:18px;padding:24px 18px;text-align:center;box-shadow:0 8px 24px rgba(0,0,0,0.3)}
#companionTimer{font-size:clamp(4.2rem,18vw,7.5rem);font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1;letter-spacing:-.04em;color:#f3eeff;text-shadow:0 0 40px rgba(181,138,255,0.35)}
#timerPhase{margin:6px 0 0;font-size:1.05rem;color:#cbb5ef;font-weight:500}
.display-control{flex:none;min-width:0;min-height:42px;margin-top:12px;padding:7px 14px;font-size:.85rem;background:#23202b;border-color:#484157}
body.timer-expanded main{position:fixed;inset:0;max-width:none;background:#101019;overflow:auto}
body.timer-expanded .timer-card{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center}
body.timer-expanded .camera-card{align-self:center;width:min(100%,380px)}
.camera-card{background:#161622;border:1px solid #362a47;border-radius:18px;padding:18px;display:flex;flex-direction:column;gap:14px}
.permission-box{font-size:.82rem;color:#b4a8c8}
.preview{position:relative;aspect-ratio:4/3;max-height:48dvh;overflow:hidden;background:#090c12;border:1px solid #354548;border-radius:14px;display:grid;place-items:center}
video{width:100%;height:100%;object-fit:contain}
.preview video:not([src]){position:absolute}
#cameraPlaceholder{position:absolute;color:#9baeb0;font-size:.9rem;text-align:center;padding:12px}
#cameraActive{position:absolute;top:10px;left:10px;padding:4px 10px;border-radius:6px;background:#103526e8;color:#80e4ae;font-size:.72rem;font-weight:600;letter-spacing:.02em}
#cameraState{color:#e8eded;font-weight:650;overflow-wrap:anywhere;margin:0;font-size:.95rem}
#cameraHelp{color:#9baeb0;font-size:.82rem;line-height:1.5;margin:0}
.actions{display:flex;gap:12px;flex-wrap:wrap}
button{flex:1;min-height:50px;min-width:140px;padding:12px 20px;border:1px solid #b88cff;border-radius:12px;background:linear-gradient(130deg,#9333ea,#6b21a8);color:white;font:600 1.05rem system-ui,-apple-system,sans-serif;cursor:pointer;transition:opacity 0.2s}
button:disabled{opacity:.4;cursor:default}
#stopCamera{background:#23202b;border-color:#484157;color:#e8eded}
details{background:#161622;border:1px solid #362a47;border-radius:12px;padding:12px}
summary{cursor:pointer;font-size:.85rem;color:#cbb5ef;font-weight:600}
dl{display:grid;grid-template-columns:minmax(120px,1fr) minmax(0,2fr);gap:8px;font-size:.8rem;margin:10px 0 0}
dt{color:#9baeb0}dd{margin:0;overflow-wrap:anywhere;color:#e8eded}
.diag-cat-title{grid-column:1/-1;font-weight:700;color:#cbb5ef;margin-top:6px;padding-top:4px;border-top:1px solid #282436}
.media-health-card{background:#161622;border:1px solid #362a47;border-radius:14px;padding:12px 16px;display:flex;flex-direction:column;gap:8px}
.health-header{display:flex;justify-content:space-between;align-items:center;font-size:0.88rem}
.health-title{color:#9baeb0}
#compactMediaStatus{color:#b58aff;font-size:0.88rem}
#compactMediaStatus[data-state="active"]{color:#72eaa8}
#compactMediaStatus[data-state="warning"]{color:#e5c483}
#compactMediaStatus[data-state="stopped"]{color:#b8c2cf}
.health-details{display:flex;gap:12px;font-size:0.78rem;flex-wrap:wrap;color:#9baeb0}
.pill{padding:2px 8px;border-radius:10px;font-weight:600}
.pill[data-state="active"]{background:#153629;color:#72eaa8}
.pill[data-state="stopped"]{background:#23202b;color:#b8c2cf}
.pill[data-state="warning"]{background:#332b1d;color:#e5c483}
.pill[data-state="inactive"]{background:#1c1b24;color:#7e8b8c}
.fine{font-size:.78rem;color:#7e8b8c;line-height:1.6;margin:0;text-align:center}
[hidden]{display:none!important}
'''

JS = r'''
(() => {
  'use strict';
  const LABELS = {
    IOS_INVITE_OPENED:'Invitation opened',
    IOS_INVITE_REDEEMED:'Device Connected',
    IOS_DEVICE_AUTHENTICATED:'Timer Synchronized',
    IOS_CAMERA_PERMISSION_PENDING:'Waiting for camera permission',
    IOS_CAMERA_PERMISSION_GRANTED:'Camera permission granted',
    IOS_CAMERA_INITIALIZING:'Initializing camera',
    IOS_WEBRTC_NEGOTIATING:'Negotiating encrypted video',
    IOS_VIDEO_CONNECTING:'Connecting video to Windows',
    IOS_CAMERA_STREAMING:'Video Streaming (Windows receiving frames)',
    IOS_CAMERA_INTERRUPTED:'Camera paused — return to this page to reconnect',
    IOS_CAMERA_STOPPED:'Camera Disconnected (Tap Start Camera to stream)',
    IOS_CAMERA_REVOKED:'Camera access revoked',
    IOS_RECONNECTING:'Reconnecting camera',
    IOS_INVITE_EXPIRED:'Invitation expired',
    IOS_CONNECTION_FAILED:'Connection failed (Tap Start Camera to retry)'
  };
  const ERRORS = {
    NotAllowedError:'Camera permission was denied or blocked. Allow Camera for this StrictRecords site in Safari settings, then tap Start Camera.',
    NotFoundError:'No camera is available on this device.',
    NotReadableError:'The camera could not start. Close other camera apps and try again.',
    OverconstrainedError:'The camera cannot satisfy the requested video settings. Try again with another available camera.',
    SecurityError:'The browser blocked camera access. Use the trusted HTTPS invitation in a top-level Safari page.',
    TypeError:'Camera APIs or video constraints are unavailable. Verify this is the trusted HTTPS page.',
    NetworkError:'The connection to Windows failed. Keep the current session open and retry.',
    AbortError:'The camera or connection request was interrupted. Tap Start Camera to retry.'
  };
  const node = id => document.getElementById(id);
  const secondsToMs = value => Number(value) < 1e12 ? Number(value) * 1000 : Number(value);
  let inviteToken = location.hash.slice(1);
  const query = new URLSearchParams(location.search);
  const preparationID = query.get('preparation_id') || query.get('pid') || '';
  history.replaceState(null,'',location.pathname + (preparationID?'?preparation_id='+encodeURIComponent(preparationID):''));

  // Negotiation Controller States
  const NEG_STATES = {
    NEW: 'NEW',
    CREATING_OFFER: 'CREATING_OFFER',
    OFFER_SENT: 'OFFER_SENT',
    REMOTE_OFFER_RECEIVED: 'REMOTE_OFFER_RECEIVED',
    SETTING_REMOTE_DESCRIPTION: 'SETTING_REMOTE_DESCRIPTION',
    CREATING_ANSWER: 'CREATING_ANSWER',
    ANSWER_SENT: 'ANSWER_SENT',
    SETTING_REMOTE_ANSWER: 'SETTING_REMOTE_ANSWER',
    STABLE: 'STABLE',
    FAILED: 'FAILED'
  };

  class NegotiationController {
    constructor(client) {
      this.client = client;
      this.state = NEG_STATES.NEW;
      this.connectionId = '';
      this.txCounter = 0;
      this.makingOffer = false;
      this.ignoreOffer = false;
      this.isPolite = false; // iOS is impolite offerer
    }
    reset() {
      this.state = NEG_STATES.NEW;
      this.connectionId = 'conn_ios_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7);
      this.makingOffer = false;
      this.ignoreOffer = false;
    }
    nextTxId() {
      return 'tx_' + (++this.txCounter) + '_' + Date.now();
    }
    setState(newState) {
      this.state = newState;
      this.client.diagnostics.negotiation_state = newState;
      this.client.render();
    }
  }

  class StrictIOSCamera {
    constructor() {
      this.credential = null; this.expiresAt = 0; this.sessionID = null;
      this.stream = null; this.peer = null; this.socket = null; this.pendingCandidates = [];
      this.wanted = false; this.closed = false; this.starting = false; this.generation = 0;
      this.reconnectTimer = null; this.refreshTimer = null; this.reconnectAttempt = 0;
      this.maxReconnectAttempts = 5;
      this.metadataQueue = Promise.resolve();
      this.integritySequence = Date.now(); this.timerConnected = false;
      this.timerSnapshot = null; this.timerSnapshotAt = 0;
      this.state = 'IOS_INVITE_OPENED'; this.confirmedFrames = false; this.lastFrameAt = null;
      this.negotiation = new NegotiationController(this);

      // Bounded watchdog timeouts
      this.signalingTimeout = null;
      this.offerAnswerTimeout = null;
      this.iceTimeout = null;
      this.frameConfirmationTimeout = null;

      this.diagnostics = {
        // Signaling
        signaling: 'not_connected',
        websocket_url: location.host ? 'wss://' + location.host + '/ios-camera/signaling' : 'pending',
        // Negotiation
        negotiation_state: 'NEW',
        connection_id: 'none',
        // ICE
        ice_gathering: 'new',
        ice_connection: 'new',
        peer_connection: 'not_connected',
        // Camera Sender
        secure_context: window.isSecureContext === true,
        protocol: location.protocol,
        media_devices: !!navigator.mediaDevices,
        get_user_media: typeof navigator.mediaDevices?.getUserMedia === 'function',
        permission: 'unsupported',
        video_track: 'off',
        track_dimensions: 'none',
        frames_sent: 0,
        packets_sent: 0,
        camera_error: 'none',
        // Windows Receiver
        received_frames: 'unverified',
        processed_frames: 0,
        confirmed_fps: 0,
        last_frame_at: 'none'
      };
      this.render(); this.queryPermission(); this.opened = this.observeOpened();
      this.timerHeartbeat(); this.timerInterval = setInterval(() => this.timerHeartbeat(), 5000);
      this.timerDrawInterval = setInterval(() => this.drawTimer(), 250);
      this.statsInterval = setInterval(() => this.pollWebrtcStats(), 2000);
      this.mediaHeartbeatSeq = 0;
      this.capturedFramesCamera = 0;
      this.lastCapturedTimeCamera = 0;
      this.capturedFramesScreen = 0;
      this.lastCapturedTimeScreen = 0;
      this.explicitStopCamera = null;
      this.explicitStopScreen = null;
      this.screenSharingWanted = false;
      this.lastMediaHeartbeatTime = 0;
      this.lastMediaHeartbeatAck = 0;
      this.mediaHeartbeatInterval = setInterval(() => this.sendMediaHeartbeat(), 5000);

      this.setupDiagnosticsExport();
    }

    clearWatchdogs() {
      clearTimeout(this.signalingTimeout); this.signalingTimeout = null;
      clearTimeout(this.offerAnswerTimeout); this.offerAnswerTimeout = null;
      clearTimeout(this.iceTimeout); this.iceTimeout = null;
      clearTimeout(this.frameConfirmationTimeout); this.frameConfirmationTimeout = null;
    }

    setupDiagnosticsExport() {
      const btn = node('exportDiagnosticsBtn');
      if (btn) {
        btn.addEventListener('click', async () => {
          const exportData = {
            export_timestamp: new Date().toISOString(),
            app: 'StrictRecords iOS Companion',
            session_id: this.sessionID || 'none',
            state: this.state,
            confirmed_frames: this.confirmedFrames,
            diagnostics: { ...this.diagnostics }
          };
          const jsonStr = JSON.stringify(exportData, null, 2);
          try {
            if (navigator.clipboard?.writeText) {
              await navigator.clipboard.writeText(jsonStr);
              btn.textContent = '✓ Copied to Clipboard!';
              setTimeout(() => { btn.textContent = '📋 Export Diagnostics'; }, 2500);
            } else {
              prompt('Copy diagnostics JSON:', jsonStr);
            }
          } catch (_) {
            prompt('Copy diagnostics JSON:', jsonStr);
          }
        });
      }
    }

    reportStage(stageName, success = true, err = '') {
      this.send({
        type: 'stage_event',
        stage: stageName,
        connection_id: this.negotiation.connectionId,
        source_id: 'IOS_FRONT_CAMERA',
        success: !!success,
        error: err ? String(err) : ''
      });
    }

    async pollWebrtcStats() {
      if(this.closed || !this.peer || this.socket?.readyState !== 1) return;
      try {
        const track = this.stream?.getVideoTracks?.()?.[0];
        const settings = track?.getSettings?.() || {};
        const out = {
          trackReadyState: track?.readyState || 'ended',
          trackMuted: track?.muted === true,
          trackEnabled: track?.enabled !== false,
          captureWidth: settings.width || 0,
          captureHeight: settings.height || 0,
          framesEncoded: 0, framesSent: 0, packetsSent: 0, bytesSent: 0, framesPerSecond: 0
        };
        if(typeof this.peer.getStats === 'function') {
          const report = await this.peer.getStats();
          report.forEach(s => {
            if(s.type === 'outbound-rtp' && (s.kind === 'video' || s.mediaType === 'video')) {
              out.framesEncoded = Number(s.framesEncoded || 0);
              out.framesSent = Number(s.framesSent || s.framesEncoded || 0);
              out.packetsSent = Number(s.packetsSent || 0);
              out.bytesSent = Number(s.bytesSent || 0);
              out.framesPerSecond = Number(s.framesPerSecond || 0);
              if(s.frameWidth) out.captureWidth = Number(s.frameWidth);
              if(s.frameHeight) out.captureHeight = Number(s.frameHeight);
            }
          });
        }
        this.diagnostics.frames_sent = out.framesSent;
        this.diagnostics.packets_sent = out.packetsSent;
        this.send({ type: 'webrtc_stats', stats: out });
      } catch(_) {}
    }

    async sendMediaHeartbeat() {
      if (this.closed) return;
      const nowEpoch = Date.now() / 1000;
      let binding; try { binding = this.registration(); } catch(_) {}
      const activeSession = this.timerSnapshot?.session_id || this.sessionID || '';
      const deviceId = binding?.credential || this.credential || 'ios_device';
      const seq = ++this.mediaHeartbeatSeq;

      // Source A: IOS_FRONT_CAMERA
      const camTrack = this.stream?.getVideoTracks?.()[0];
      const camActive = !!(this.wanted && this.stream && camTrack);
      const camReadyState = camTrack ? camTrack.readyState : (this.explicitStopCamera ? 'ended' : 'none');
      const camMuted = camTrack ? camTrack.muted : false;
      const camEnabled = camTrack ? camTrack.enabled : false;
      const camPerm = this.diagnostics.permission === 'granted' ? 'granted' : (this.diagnostics.permission === 'denied' ? 'denied' : 'prompt');

      const camSource = {
        session_id: activeSession,
        device_id: deviceId,
        connection_generation: this.generation,
        source_id: 'IOS_FRONT_CAMERA',
        heartbeat_seq: seq,
        timestamp: nowEpoch,
        user_sharing_enabled: this.wanted,
        permission_status: camPerm,
        capture_session_status: camActive ? 'capturing' : (this.wanted ? 'starting' : 'inactive'),
        video_track_enabled: camEnabled,
        video_track_ready_state: camReadyState,
        video_track_muted: camMuted,
        captured_frames: this.capturedFramesCamera,
        last_captured_timestamp: this.lastCapturedTimeCamera,
        encoded_frames: this.diagnostics.frames_sent || this.capturedFramesCamera,
        transmitted_frames: this.diagnostics.frames_sent || 0,
        explicit_user_stop: this.explicitStopCamera,
        capture_error: this.diagnostics.camera_error !== 'none' ? this.diagnostics.camera_error : null
      };

      // Source B: IOS_FULL_SCREEN_SHARE
      const scrTrack = this.screenStream?.getVideoTracks?.()[0] || this.screenTrack;
      const scrActive = !!(this.screenSharingWanted && this.screenStream && scrTrack);
      const scrReadyState = scrTrack ? scrTrack.readyState : (this.explicitStopScreen ? 'ended' : 'none');
      const scrMuted = scrTrack ? scrTrack.muted : false;
      const scrEnabled = scrTrack ? scrTrack.enabled : false;

      const scrSource = {
        session_id: activeSession,
        device_id: deviceId,
        connection_generation: this.generation,
        source_id: 'IOS_FULL_SCREEN_SHARE',
        heartbeat_seq: seq,
        timestamp: nowEpoch,
        user_sharing_enabled: !!this.screenSharingWanted,
        permission_status: scrTrack ? 'granted' : (this.screenSharingWanted ? 'prompt' : 'unknown'),
        capture_session_status: scrActive ? 'capturing' : (this.screenSharingWanted ? 'starting' : 'inactive'),
        video_track_enabled: scrEnabled,
        video_track_ready_state: scrReadyState,
        video_track_muted: scrMuted,
        captured_frames: this.capturedFramesScreen,
        last_captured_timestamp: this.lastCapturedTimeScreen,
        encoded_frames: this.screenFramesSent || this.capturedFramesScreen,
        transmitted_frames: this.screenFramesSent || 0,
        explicit_user_stop: this.explicitStopScreen,
        capture_error: null
      };

      const heartbeatMsg = {
        type: 'IOS_MEDIA_HEARTBEAT',
        credential: this.credential,
        session_id: activeSession,
        device_id: deviceId,
        connection_generation: this.generation,
        heartbeat_seq: seq,
        timestamp: nowEpoch,
        sources: {
          'IOS_FRONT_CAMERA': camSource,
          'IOS_FULL_SCREEN_SHARE': scrSource
        }
      };

      this.lastMediaHeartbeatTime = nowEpoch;

      let sentWss = false;
      if (this.socket && this.socket.readyState === 1 && this.diagnostics.signaling === 'authenticated') {
        try {
          this.socket.send(JSON.stringify(heartbeatMsg));
          sentWss = true;
        } catch(_) {}
      }

      if (!sentWss && this.credential) {
        try {
          await this.http('/ios-camera/heartbeat', heartbeatMsg);
        } catch(_) {
          try {
            await this.http('/api/strict_session/ios/media_heartbeat', heartbeatMsg);
          } catch(_) {}
        }
      }
      this.render();
    }

    render(help) {
      node('cameraState').textContent = LABELS[this.state] || this.state;
      if(help) node('cameraHelp').textContent = help;
      node('startCamera').disabled = this.starting || this.closed || !!this.stream;
      node('startCamera').textContent = this.credential ? 'Resume Camera' : 'Start Camera';
      node('stopCamera').disabled = !this.stream && !this.wanted;
      node('cameraActive').hidden = !(this.stream && this.confirmedFrames);
      node('cameraPlaceholder').hidden = !!this.stream;

      const mediaStatusNode = node('compactMediaStatus');
      if (mediaStatusNode) {
        let statusText = 'AWAITING SHARING';
        let statusState = 'pending';
        if (this.stream && this.confirmedFrames) {
          statusText = 'CAMERA ACTIVE & STREAMING';
          statusState = 'active';
        } else if (this.explicitStopCamera && !this.wanted) {
          statusText = 'CAMERA STOPPED BY USER';
          statusState = 'stopped';
        } else if (this.wanted) {
          statusText = 'CAMERA NEGOTIATING';
          statusState = 'warning';
        }
        mediaStatusNode.textContent = statusText;
        mediaStatusNode.dataset.state = statusState;

        const camPill = node('camHealthPill');
        if (camPill) {
          if (this.stream && this.confirmedFrames) {
            camPill.textContent = 'Active (Streaming)';
            camPill.dataset.state = 'active';
          } else if (this.explicitStopCamera) {
            camPill.textContent = 'Stopped by User';
            camPill.dataset.state = 'stopped';
          } else if (this.wanted) {
            camPill.textContent = 'Starting...';
            camPill.dataset.state = 'warning';
          } else {
            camPill.textContent = 'Off';
            camPill.dataset.state = 'inactive';
          }
        }

        const scrPill = node('scrHealthPill');
        if (scrPill) {
          if (this.screenStream) {
            scrPill.textContent = 'Active (Streaming)';
            scrPill.dataset.state = 'active';
          } else if (this.explicitStopScreen) {
            scrPill.textContent = 'Stopped by User';
            scrPill.dataset.state = 'stopped';
          } else if (this.screenSharingWanted) {
            scrPill.textContent = 'Starting...';
            scrPill.dataset.state = 'warning';
          } else {
            scrPill.textContent = 'Off';
            scrPill.dataset.state = 'inactive';
          }
        }

        const hbTime = node('lastHeartbeatTime');
        if (hbTime) {
          hbTime.textContent = this.lastMediaHeartbeatTime ? new Date(this.lastMediaHeartbeatTime * 1000).toLocaleTimeString() : 'None';
        }
      }

      const list = node('cameraDiagnostics');
      if (list) {
        list.replaceChildren();
        const categories = {
          'LAYER A – SIGNALING': ['signaling', 'websocket_url', 'negotiation_state', 'connection_id'],
          'LAYER B – NETWORK TRANSPORT': ['ice_connection', 'ice_gathering', 'peer_connection', 'rtt_ms', 'jitter_ms', 'packets_lost', 'outbound_bitrate_kbps', 'congestion_tier'],
          'LAYER C – MEDIA (CAMERA)': ['permission', 'video_track', 'track_dimensions', 'camera_sent_fps', 'frames_sent', 'packets_sent', 'camera_error'],
          'LAYER D – WINDOWS INFERENCE & PROCESSING': ['windows_stage', 'received_frames', 'confirmed_fps', 'windows_decoded_fps', 'windows_processed_fps', 'last_frame_at']
        };
        for(const [catTitle, fields] of Object.entries(categories)) {
          const catHeader = document.createElement('div');
          catHeader.className = 'diag-cat-title';
          catHeader.textContent = catTitle;
          list.append(catHeader);
          for(const f of fields) {
            const val = this.diagnostics[f] !== undefined ? this.diagnostics[f] : 'n/a';
            const title = document.createElement('dt');
            const detail = document.createElement('dd');
            title.textContent = f.replaceAll('_', ' ');
            detail.textContent = String(val);
            list.append(title, detail);
          }
        }
      }
    }

    metadata() {
      return {
        ...this.diagnostics,
        visible: !document.hidden,
        audio_tracks_present: !!this.stream?.getAudioTracks().length,
        error_name: this.diagnostics.camera_error,
        api: this.diagnostics.api_tls === 'HTTPS_response_received' ? 'connected' : this.diagnostics.api_tls === 'failed' ? 'failed' : 'pending',
        signaling: this.diagnostics.signaling === 'authenticated' ? 'connected' : this.diagnostics.signaling === 'disconnected' ? 'closed' : this.diagnostics.signaling,
        peer: this.diagnostics.peer_connection,
        track: this.diagnostics.video_track === 'off' ? 'ended' : this.diagnostics.video_track
      };
    }

    transition(state, help) {
      this.state = state;
      this.render(help);
      if(!state.startsWith('IOS_CAMERA_') && !['IOS_WEBRTC_NEGOTIATING', 'IOS_VIDEO_CONNECTING', 'IOS_RECONNECTING', 'IOS_CONNECTION_FAILED'].includes(state)) return;
      if(state === 'IOS_CAMERA_STREAMING') return;
      const report = { type: 'status', state, diagnostics: this.metadata() };
      if(this.socket?.readyState === 1) this.send(report);
      else if(this.credential) {
        const credential = this.credential;
        this.metadataQueue = this.metadataQueue.catch(() => {}).then(() => {
          if(this.closed || state !== this.state) return;
          return this.api('status', { camera_credential: credential, state, diagnostics: report.diagnostics });
        }).catch(() => {});
      }
    }

    async queryPermission() {
      try {
        if(!navigator.permissions?.query) return;
        const permission = await navigator.permissions.query({ name: 'camera' });
        this.diagnostics.permission = permission.state;
        const pStatus = node('cameraPermissionStatus');
        if(pStatus) pStatus.textContent = 'Camera permission: ' + (permission.state === 'granted' ? 'Granted (Video only · No microphone)' : permission.state === 'denied' ? 'Denied in Safari' : 'Not requested');
        permission.onchange = () => {
          this.diagnostics.permission = permission.state;
          const ps = node('cameraPermissionStatus');
          if(ps) ps.textContent = 'Camera permission: ' + (permission.state === 'granted' ? 'Granted (Video only · No microphone)' : permission.state === 'denied' ? 'Denied in Safari' : 'Not requested');
          if(permission.state === 'denied') this.stop('IOS_CAMERA_REVOKED', 'Camera permission was revoked.');
          else this.render();
        };
        this.render();
      } catch (_) { this.diagnostics.permission = 'unsupported'; this.render(); }
    }

    registration() {
      let binding;
      try { binding = window.strictIOSTimerBinding || JSON.parse(localStorage.getItem('strictrecords.timer.binding.v2') || 'null'); } catch (_) {}
      if(!binding || binding.role !== 'IOS' || binding.preparation_id !== preparationID ||
          typeof binding.credential !== 'string' || binding.expires_at <= Date.now()) {
        const error = Error('Join this session as the registered iPhone/iPad timer companion on this trusted origin first.');
        error.name = 'RegistrationRequired';
        throw error;
      }
      return binding;
    }

    async observeOpened() {
      if(!inviteToken || location.protocol !== 'https:' || window.isSecureContext !== true) return;
      try {
        this.reportStage('1_ios_invitation_auth', true);
        await this.api('opened', { token: inviteToken });
      } catch(error) {
        this.reportStage('1_ios_invitation_auth', false, error.message);
        this.diagnostics.camera_error = error.name || 'InvitationError';
        this.state = error.status === 403 || error.status === 409 ? 'IOS_INVITE_EXPIRED' : 'IOS_INVITE_OPENED';
        this.render(error.message);
      }
    }

    async api(action, body) { return this.http('/ios-camera/' + action, body); }

    async http(url, body) {
      const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(body),
          cache: 'no-store',
          redirect: 'error',
          signal: controller.signal
        });
        this.diagnostics.api_tls = location.protocol === 'https:' ? 'HTTPS_response_received' : 'insecure';
        let result;
        try { result = await response.json(); } catch(_) { throw Object.assign(Error('The secure camera API returned an invalid response.'), { name: 'NetworkError' }); }
        if(!response.ok || result.success === false) {
          const error = Error(result.error || 'Camera authentication failed.');
          error.name = result.code || 'CameraAuthenticationError';
          error.status = response.status;
          throw error;
        }
        return result;
      } catch(error) {
        if(error.name === 'TypeError' || error.name === 'NetworkError' || error.name === 'AbortError') this.diagnostics.api_tls = 'failed';
        throw error;
      } finally {
        clearTimeout(timeout);
        this.render();
      }
    }

    async timerHeartbeat(forceHidden = false) {
      if(this.closed || this.timerBusy) return;
      let binding; try { binding = this.registration(); } catch(_) { return; }
      const visible = !forceHidden && !document.hidden;
      const leaseKey = 'strictrecords.camera.timer-lease.v1';
      try { if(visible) localStorage.setItem(leaseKey, JSON.stringify({ preparation_id: preparationID, expires_at: Date.now() + 15000 })); else localStorage.removeItem(leaseKey); } catch(_) {}
      this.timerBusy = true;
      const nowMs = Date.now();
      const seq = ++(this.timerSeq || (this.timerSeq = 0));

      // 1. Primary Control Plane: Send IOS_TIMER_HEARTBEAT over persistent WebSocket
      const timerMsg = {
        type: 'IOS_TIMER_HEARTBEAT',
        session_id: this.timerSnapshot?.session_id || this.sessionID || '',
        device_id: binding.credential || this.credential || 'ios_companion',
        connection_id: this.negotiation?.connectionId || this.diagnostics?.connection_id || '',
        connection_generation: this.connectionGeneration || 1,
        sequence_number: seq,
        client_monotonic_timestamp: globalThis.performance?.now?.() ?? nowMs,
        reported_session_state: this.timerSnapshot?.state || 'WAITING'
      };

      if (this.socket && this.socket.readyState === WebSocket.OPEN && this.diagnostics.signaling === 'authenticated') {
        try { this.socket.send(JSON.stringify(timerMsg)); } catch (_) {}
      }

      // 2. HTTPS synchronization & state polling
      try {
        const fullscreen = !!(document.fullscreenElement || document.webkitFullscreenElement);
        const report = {
          timer_ready: true, fullscreen, display_mode: fullscreen ? 'fullscreen' : 'expanded', visible,
          online: navigator.onLine !== false, client_version: 'StrictRecords iOS Camera Companion v1',
          integrity: {
            sequence: (this.integritySequence = Math.max(nowMs, (this.integritySequence || 0) + 1)),
            state: visible ? 'FOREGROUND' : 'BACKGROUND_REASON_UNKNOWN',
            reason: visible ? 'RESUMED' : 'PAGE_HIDDEN',
            session_id: this.timerSnapshot?.session_id || ''
          }
        };

        const result = await this.http('/api/strict_session/preparation/heartbeat', {
          preparation_id: binding.preparation_id, role: 'IOS', credential: binding.credential,
          report, sequence_number: seq
        });

        this.timerConnected = true;
        this.lastTimerHeartbeatAck = Date.now();
        this.timerSnapshot = result.timer;
        this.timerSnapshotAt = globalThis.performance?.now?.() ?? nowMs;
        this.drawTimer();
        const activeSession = result.timer?.session_id || result.session_id;
        const sDisplay = node('sessionDisplay');
        if(sDisplay) sDisplay.textContent = activeSession ? 'Active Session: ' + activeSession.slice(0, 10) : 'Preparation: ' + (binding.preparation_id || '').slice(0, 8);
        const tPhase = node('timerPhase');
        if(tPhase) tPhase.textContent = result.timer?.label || (result.timer?.running ? 'Focus in progress' : 'Waiting for Windows start');
        this.updateTimerStatusUI('TIMER CONNECTED');
      } catch(_) {
        const gap = (Date.now() - (this.lastTimerHeartbeatAck || 0)) / 1000;
        if (gap < 20) {
          this.updateTimerStatusUI('TIMER TEMPORARILY RECONNECTING');
        } else if (gap < 60) {
          this.updateTimerStatusUI('TIMER SYNCHRONIZING');
        } else {
          this.timerConnected = false;
          this.updateTimerStatusUI('TIMER CONNECTION LOST');
        }
      } finally {
        this.timerBusy = false;
      }
    }

    updateTimerStatusUI(stateLabel) {
      const sBadge = node('timerSyncStatus');
      const cTimer = node('companionTimer');
      if (sBadge) {
        if (stateLabel === 'TIMER CONNECTED') {
          sBadge.textContent = '● Synchronized with Windows (TIMER CONNECTED)';
          sBadge.dataset.state = 'active';
          sBadge.style.color = '#4be78b';
        } else if (stateLabel === 'TIMER TEMPORARILY RECONNECTING') {
          sBadge.textContent = '● TIMER TEMPORARILY RECONNECTING';
          sBadge.dataset.state = 'warning';
          sBadge.style.color = '#f59e0b';
        } else if (stateLabel === 'TIMER SYNCHRONIZING') {
          sBadge.textContent = '● TIMER SYNCHRONIZING…';
          sBadge.dataset.state = 'warning';
          sBadge.style.color = '#a78bfa';
        } else if (stateLabel === 'TIMER CONNECTION LOST') {
          sBadge.textContent = '✕ TIMER CONNECTION LOST – Reconnecting';
          sBadge.dataset.state = 'error';
          sBadge.style.color = '#ef4444';
        }
      }
      if (cTimer) {
        cTimer.title = stateLabel + ' · ' + (this.timerSnapshot?.label || 'Waiting for session');
      }
    }

    drawTimer() {
      const now = globalThis.performance?.now?.() ?? Date.now();
      const age = Math.max(0, (now - this.timerSnapshotAt) / 1000);
      const value = this.timerSnapshot?.remaining_seconds;
      if(typeof value !== 'number' || !Number.isFinite(value)) { node('companionTimer').textContent = '--:--'; return; }
      const remaining = Math.max(0, Math.ceil(value - (this.timerSnapshot.running === true ? Math.min(age, 10) : 0)));
      node('companionTimer').textContent = Math.floor(remaining / 60).toString().padStart(2, '0') + ':' + (remaining % 60).toString().padStart(2, '0');
      if(age > 12) node('companionTimer').title = 'Connection stale — waiting for the Windows timer.';
    }

    async redeem() {
      if(this.credential) return;
      const registration = this.registration();
      if(!inviteToken) {
        const grant = await this.api('grant', { preparation_id: preparationID, companion_credential: registration.credential });
        if(typeof grant.token !== 'string') throw Error('Start the session on Windows before enabling the camera.');
        inviteToken = grant.token;
      }
      await this.opened;
      this.reportStage('2_device_registration', true);
      const result = await this.api('redeem', { token: inviteToken, preparation_id: preparationID, companion_credential: registration.credential });
      this.applyCredential(result);
      inviteToken = '';
      this.transition('IOS_INVITE_REDEEMED');
    }

    applyCredential(result) {
      if(typeof result.camera_credential !== 'string' || !result.camera_credential || result.preparation_id !== preparationID || !result.session_id) {
        throw Object.assign(Error('Camera credential does not match this session.'), { name: 'SessionMismatch' });
      }
      if(this.sessionID && this.sessionID !== result.session_id) {
        throw Object.assign(Error('Camera session identity changed.'), { name: 'SessionMismatch' });
      }
      this.credential = result.camera_credential;
      this.sessionID = result.session_id;
      this.expiresAt = secondsToMs(result.credential_expires_at);
      this.iceServers = result.ice_servers || [];
      this.send({ type: 'authenticate', credential: this.credential });
      this.scheduleRefresh();
    }

    scheduleRefresh() {
      clearTimeout(this.refreshTimer);
      if(!this.credential || !Number.isFinite(this.expiresAt)) return;
      this.refreshTimer = setTimeout(async () => {
        try {
          const result = await this.api('refresh', { camera_credential: this.credential });
          this.applyCredential(result);
        } catch(error) {
          this.diagnostics.camera_error = error.name || 'RefreshError';
          if(error.status === 403 || Date.now() >= this.expiresAt) this.end('Camera authorization expired. Open a fresh invitation.');
          else this.refreshTimer = setTimeout(() => this.scheduleRefresh(), 3000);
        }
      }, Math.max(1000, this.expiresAt - Date.now() - 30000));
    }

    async start() {
      if(this.starting || this.stream || this.closed) return;
      this.starting = true; this.wanted = true; const generation = ++this.generation;
      this.diagnostics.camera_error = 'none';
      try {
        if(location.protocol !== 'https:' || window.isSecureContext !== true) throw Object.assign(Error(ERRORS.SecurityError), { name: 'SecurityError' });
        if(window.top !== window.self) throw Object.assign(Error('Open the invitation directly in Safari, outside an iframe.'), { name: 'SecurityError' });
        if(typeof navigator.mediaDevices?.getUserMedia !== 'function') throw Object.assign(Error(ERRORS.TypeError), { name: 'TypeError' });
        if(document.hidden) throw Object.assign(Error('Keep this page visible before starting the camera.'), { name: 'CameraBackgrounded' });

        await this.redeem();
        if(generation !== this.generation) return;
        this.transition('IOS_DEVICE_AUTHENTICATED');
        this.transition('IOS_CAMERA_PERMISSION_PENDING');

        // Request camera with conservative constraints compatible with Safari
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: 'user' },
            width: { ideal: 640, max: 640 },
            height: { ideal: 480, max: 480 },
            frameRate: { ideal: 15, max: 15 }
          },
          audio: false
        });

        if(generation !== this.generation || document.hidden || !this.wanted) {
          stream.getTracks().forEach(track => track.stop());
          return;
        }
        if(stream.getAudioTracks().length) {
          stream.getTracks().forEach(track => track.stop());
          throw Object.assign(Error('Unexpected audio track was rejected.'), { name: 'UnexpectedAudioTrack' });
        }

        const track = stream.getVideoTracks()[0];
        if(!track) throw Object.assign(Error(ERRORS.NotFoundError), { name: 'NotFoundError' });

        // Pre-flight track validation
        if((track.readyState && track.readyState !== 'live') || track.enabled === false || track.muted === true) {
          throw Object.assign(Error('Camera track initialized in non-live or muted state.'), { name: 'NotReadableError' });
        }

        const settings = track.getSettings?.() || {};
        this.diagnostics.track_dimensions = `${settings.width || 640}x${settings.height || 480}`;
        this.stream = stream;
        this.diagnostics.permission = 'granted';
        this.diagnostics.video_track = 'live';

        this.reportStage('5_camera_permission_track', true);

        const pStat = node('cameraPermissionStatus');
        if(pStat) pStat.textContent = 'Camera permission: Granted (Video only · No microphone)';

        track.onended = () => {
          if(this.stream === stream) this.stop('IOS_CAMERA_INTERRUPTED', 'The camera stopped. Tap Resume Camera when it is available.');
        };
        track.onmute = () => {
          if(this.stream === stream) {
            this.confirmedFrames = false;
            this.transition('IOS_CAMERA_INTERRUPTED', 'The camera is temporarily unavailable.');
          }
        };
        track.onunmute = () => {
          if(this.stream === stream) {
            this.transition('IOS_VIDEO_CONNECTING');
            this.connect();
          }
        };

        node('cameraPreview').srcObject = stream;
        try { await node('cameraPreview').play(); } catch(_) {}
        this.explicitStopCamera = null;
        const camVid = node('cameraPreview');
        if (camVid && 'requestVideoFrameCallback' in camVid) {
          const onCamFrame = (now, meta) => {
            if (this.stream === stream) {
              this.capturedFramesCamera = meta?.presentedFrames || (this.capturedFramesCamera + 1);
              this.lastCapturedTimeCamera = Date.now() / 1000;
              try { camVid.requestVideoFrameCallback(onCamFrame); } catch(_) {}
            }
          };
          try { camVid.requestVideoFrameCallback(onCamFrame); } catch(_) {}
        }

        this.transition('IOS_CAMERA_PERMISSION_GRANTED');
        this.transition('IOS_CAMERA_INITIALIZING');
        this.connect();
      } catch(error) {
        this.diagnostics.camera_error = error.name || 'UnknownError';
        this.reportStage('5_camera_permission_track', false, error.message);
        this.stop(error.name === 'NotAllowedError' ? 'IOS_CAMERA_REVOKED' : error.name === 'InvitationExpired' ? 'IOS_INVITE_EXPIRED' : 'IOS_CAMERA_STOPPED', ERRORS[error.name] || error.message);
      } finally {
        this.starting = false;
        this.render();
      }
    }

    send(message) {
      if(this.socket?.readyState === 1) {
        try { this.socket.send(JSON.stringify(message)); } catch(_) {}
      }
    }

    connect() {
      if(!this.wanted || !this.stream || document.hidden || this.closed) return;
      this.disconnectTransport();
      this.clearWatchdogs();
      this.confirmedFrames = false;
      this.pendingCandidates = [];
      this.negotiation.reset();
      this.diagnostics.connection_id = this.negotiation.connectionId;

      // Reuse existing open control plane WebSocket socket if active
      if(this.socket && this.socket.readyState === WebSocket.OPEN) {
        if(this.diagnostics.signaling === 'authenticated') {
          this.offer();
        } else {
          this.send({
            type: 'authenticate',
            credential: this.credential,
            connection_id: this.negotiation.connectionId
          });
        }
        return;
      }

      const wsUrl = 'wss://' + location.host + '/ios-camera/signaling';
      this.diagnostics.websocket_url = wsUrl;
      const socket = new WebSocket(wsUrl);
      this.socket = socket;
      this.diagnostics.signaling = 'connecting';
      this.transition('IOS_WEBRTC_NEGOTIATING');

      // 1. Signaling Timeout Watchdog (10 seconds)
      this.signalingTimeout = setTimeout(() => {
        if(this.socket === socket && this.diagnostics.signaling !== 'authenticated') {
          this.reportStage('3_signaling_ws_connection', false, 'Signaling connection timed out (10s)');
          this.reconnect('Signaling connection timed out after 10s. Retrying.');
        }
      }, 10000);

      socket.onopen = () => {
        if(this.socket === socket) {
          this.send({
            type: 'authenticate',
            credential: this.credential,
            connection_id: this.negotiation.connectionId
          });
          this.reportStage('3_signaling_ws_connection', true);
        }
      };

      socket.onmessage = event => {
        this.message(event, socket).catch(error => {
          this.diagnostics.camera_error = error.name || 'SignalingError';
          this.reconnect('Video negotiation failed: ' + error.message);
        });
      };

      socket.onerror = () => {
        if(this.socket === socket) {
          this.reportStage('3_signaling_ws_connection', false, 'WebSocket error encountered');
          this.diagnostics.signaling = 'failed';
          this.render('Secure signaling failed. Check network or Cloudflare tunnel status.');
        }
      };

      socket.onclose = () => {
        if(this.socket === socket) {
          this.socket = null;
          this.diagnostics.signaling = 'disconnected';
          this.reconnect('Signaling connection dropped. Reconnecting.');
        }
      };
    }

    async message(event, socket) {
      if(this.socket !== socket) return;
      if(typeof event.data !== 'string' || event.data.length > 131072) throw Error('Invalid signaling message length');
      const message = JSON.parse(event.data);

      if(message.type === 'authenticated') {
        clearTimeout(this.signalingTimeout);
        this.diagnostics.signaling = 'authenticated';
        this.reconnectAttempt = 0;

        // Stage 4: Peer Connection Creation
        this.reportStage('4_webrtc_peer_connection_create', true);
        const peer = new RTCPeerConnection({
          iceServers: message.ice_servers || this.iceServers || [],
          bundlePolicy: 'max-bundle'
        });
        this.peer = peer;
        this.diagnostics.peer_connection = peer.connectionState;

        // Stage 6: Video Track Sender Creation
        const localCandidates = [];
        let offerSent = false;
        const vTracks = this.stream?.getVideoTracks() || [];
        for(const track of vTracks) {
          peer.addTrack(track, this.stream);
        }
        this.reportStage('6_video_sender_transceiver_create', true);

        // Screen share uses an independent peer connection (IOS_FULL_SCREEN_SHARE)
        if(this.screenTrack && this.screenStream) {
          this.connectScreenShare().catch(() => {});
        }

        // ICE candidate trickle with standard field support
        peer.onicecandidate = ev => {
          if(this.peer !== peer || !ev.candidate) {
            if(!ev.candidate) {
              this.diagnostics.ice_gathering = 'complete';
              this.render();
            }
            return;
          }
          const candObj = ev.candidate.toJSON ? ev.candidate.toJSON() : ev.candidate;
          if(offerSent) {
            this.send({
              type: 'candidate',
              candidate: candObj,
              connection_id: this.negotiation.connectionId,
              tx_id: this.negotiation.nextTxId()
            });
            this.reportStage('14_ice_candidate_delivery', true);
          } else {
            localCandidates.push(candObj);
          }
        };

        peer.onicegatheringstatechange = () => {
          if(this.peer !== peer) return;
          this.diagnostics.ice_gathering = peer.iceGatheringState;
          if(peer.iceGatheringState === 'complete') {
            this.reportStage('13_local_ice_candidate_gathering', true);
          }
          this.render();
        };

        peer.oniceconnectionstatechange = () => {
          if(this.peer !== peer) return;
          this.diagnostics.ice_connection = peer.iceConnectionState;
          if(peer.iceConnectionState === 'connected' || peer.iceConnectionState === 'completed') {
            clearTimeout(this.iceTimeout);
            this.reportStage('16_ice_connectivity_checks', true);
          } else if(peer.iceConnectionState === 'failed') {
            this.reportStage('16_ice_connectivity_checks', false, 'ICE connection failed');
            this.reconnect('ICE connectivity checks failed. Retrying.');
          }
          this.render();
        };

        peer.onconnectionstatechange = () => {
          if(this.peer !== peer) return;
          this.diagnostics.peer_connection = peer.connectionState;
          if(peer.connectionState === 'failed' || peer.connectionState === 'disconnected') {
            this.reconnect('WebRTC connection dropped (' + peer.connectionState + '). Reconnecting.');
          } else if(peer.connectionState === 'connected') {
            this.transition('IOS_VIDEO_CONNECTING', 'Encrypted media stream established. Awaiting frame reception confirmation from Windows.');
            // Start First Frame Confirmation Timeout (10 seconds)
            clearTimeout(this.frameConfirmationTimeout);
            this.frameConfirmationTimeout = setTimeout(() => {
              if(!this.confirmedFrames) {
                this.reportStage('22_windows_frame_queue_insertion', false, 'Windows did not confirm frame reception within 10s');
                this.reconnect('Media stream connected but no frames acknowledged by Windows. Retrying connection.');
              }
            }, 10000);
          } else {
            this.render();
          }
        };

        // Stage 7: Generate SDP Offer
        this.negotiation.setState(NEG_STATES.CREATING_OFFER);
        this.negotiation.makingOffer = true;
        const txId = this.negotiation.nextTxId();
        const offer = await peer.createOffer();
        if(this.peer !== peer) return;

        if(/(?:^|\r?\n)m=audio\s/.test(offer.sdp || '')) {
          throw Error('Unexpected audio negotiation rejected.');
        }

        await peer.setLocalDescription(offer);
        if(this.peer !== peer) return;

        this.reportStage('7_sdp_offer_generation', true);
        this.negotiation.setState(NEG_STATES.OFFER_SENT);

        // Send Offer to Windows with explicit connection_id and tx_id
        this.send({
          type: 'offer',
          sdp: peer.localDescription.sdp,
          connection_id: this.negotiation.connectionId,
          tx_id: txId
        });
        offerSent = true;

        // 2. Offer/Answer Timeout (10s)
        this.offerAnswerTimeout = setTimeout(() => {
          if(this.negotiation.state === NEG_STATES.OFFER_SENT) {
            this.reportStage('10_sdp_answer_generation', false, 'Timed out waiting for Windows SDP answer');
            this.reconnect('SDP offer/answer negotiation timed out (10s). Retrying.');
          }
        }, 10000);

        // 3. ICE Connection Timeout (15s)
        this.iceTimeout = setTimeout(() => {
          if(this.peer?.iceConnectionState !== 'connected' && this.peer?.iceConnectionState !== 'completed') {
            this.reportStage('16_ice_connectivity_checks', false, 'ICE connectivity timed out (15s)');
            this.reconnect('ICE connection timed out after 15s. Retrying.');
          }
        }, 15000);

        // Flush early gathered local candidates
        for(const cand of localCandidates) {
          this.send({
            type: 'candidate',
            candidate: cand,
            connection_id: this.negotiation.connectionId,
            tx_id: this.negotiation.nextTxId()
          });
          this.reportStage('14_ice_candidate_delivery', true);
        }
        this.transition('IOS_VIDEO_CONNECTING');

      } else if(message.type === 'answer' && (message.source_id === 'IOS_SCREEN_SHARE' || message.source_id === 'IOS_FULL_SCREEN_SHARE') && this.screenPeer) {
        if(typeof message.sdp !== 'string' || /(?:^|\r?\n)m=audio\s/.test(message.sdp)) {
          throw Error('Invalid video-only screen share answer received from Windows.');
        }
        await this.screenPeer.setRemoteDescription({ type: 'answer', sdp: message.sdp });
        const sState = node('screenState');
        if (sState) sState.textContent = '● iPad Full-Screen Share streaming to Windows (Verifying 3s stability)';
      } else if(message.type === 'answer' && this.peer) {
        clearTimeout(this.offerAnswerTimeout);
        if(typeof message.sdp !== 'string' || /(?:^|\r?\n)m=audio\s/.test(message.sdp)) {
          throw Error('Invalid video-only answer received from Windows.');
        }
        this.reportStage('11_sdp_answer_delivery_ios', true);
        this.negotiation.setState(NEG_STATES.SETTING_REMOTE_DESCRIPTION);
        const peer = this.peer;
        await peer.setRemoteDescription({ type: 'answer', sdp: message.sdp });
        this.reportStage('12_remote_description_installation_ios', true);
        this.negotiation.setState(NEG_STATES.STABLE);

        // Flush any candidates received while remote description was pending
        for(const cand of this.pendingCandidates.splice(0)) {
          try { await peer.addIceCandidate(cand); } catch(_) {}
        }

      } else if(message.type === 'request_screen_share') {
        const sState = node('screenState');
        if (sState) sState.textContent = 'SCREEN SHARE NEGOTIATING (60s grace) — Tap Share iPad Screen below to begin.';
        const startBtn = node('startScreenShare');
        if (startBtn) { startBtn.disabled = false; startBtn.focus?.(); }
      } else if(message.type === 'candidate' && this.peer) {
        if(this.peer.remoteDescription) {
          try {
            await this.peer.addIceCandidate(message.candidate);
            this.reportStage('15_remote_ice_candidate_installation', true);
          } catch(e) {
            this.reportStage('15_remote_ice_candidate_installation', false, e.message);
          }
        } else {
          this.pendingCandidates.push(message.candidate);
        }

      } else if(message.type === 'IOS_CAMERA_FRAME_READY' || message.type === 'receiver_frames' || message.type === 'state') {
        // Authenticated Frame Reception Confirmation Protocol
        if(message.session_id && message.session_id !== this.sessionID) {
          throw Error('Session identity mismatch in frame confirmation');
        }

        let confirmed = false;
        let procSeq = 0;
        let fps = 0;
        let lastTs = null;

        if (message.type === 'IOS_TIMER_HEARTBEAT_ACK') {
          this.lastTimerHeartbeatAck = Date.now();
          if (message.authoritative_timer_state) {
            this.timerConnected = true;
            this.timerSnapshot = message.authoritative_timer_state;
            this.timerSnapshotAt = globalThis.performance?.now?.() ?? Date.now();
            this.drawTimer();
            this.updateTimerStatusUI('TIMER CONNECTED');
          }
          return;
        }

        if (message.type === 'IOS_MEDIA_HEARTBEAT_ACK') {
          this.lastMediaHeartbeatAck = Date.now() / 1000;
          return;
        }

        if(message.type === 'IOS_CAMERA_FRAME_READY') {
          procSeq = Number(message.processed_frame_sequence || 0);
          const decSeq = Number(message.decoded_frame_sequence || 0);
          const rxSeq = Number(message.received_frame_sequence || 0);
          fps = Number(message.fps || 0);
          lastTs = message.inference_timestamp ? new Date(message.inference_timestamp * 1000).toLocaleTimeString() : new Date().toLocaleTimeString();
          // Frame confirmation requires actual processed or decoded frames
          confirmed = procSeq > 0 || decSeq > 0 || rxSeq > 0;
        } else if(message.type === 'receiver_frames') {
          procSeq = Number(message.frames_received || 0);
          lastTs = message.last_frame_at || new Date().toLocaleTimeString();
          confirmed = procSeq > 0;
        } else if(message.state === 'IOS_CAMERA_STREAMING') {
          procSeq = Number(message.frames_received || 0);
          confirmed = procSeq > 0;
        }

        if(confirmed) {
          clearTimeout(this.frameConfirmationTimeout);
          this.confirmedFrames = true;
          this.lastFrameAt = lastTs;
          this.diagnostics.received_frames = 'confirmed_by_windows';
          this.diagnostics.processed_frames = procSeq;
          this.diagnostics.confirmed_fps = fps.toFixed(1);
          this.diagnostics.last_frame_at = lastTs;
          this.reportStage('23_windows_camera_inference_processing', true);
          this.transition('IOS_CAMERA_STREAMING', '● Video streaming confirmed — Windows is processing camera frames.');
        } else if(message.state && message.state !== 'IOS_CAMERA_STREAMING' && LABELS[message.state]) {
          this.confirmedFrames = false;
          this.diagnostics.received_frames = 'unverified';
          this.transition(message.state);
        }

      } else if(message.type === 'stop') {
        this.end('Strict Session ended or camera access was revoked.');
      } else if(message.type === 'reconnect') {
        this.reconnect(message.reason || 'Re-establishing WebRTC camera stream.');
      } else if(message.type === 'error') {
        if(message.code === 'Unauthorized' || message.code === 'SessionEnded') {
          this.end('Camera authorization is no longer valid.');
        } else {
          throw Error('Camera signaling request error: ' + (message.error || message.code));
        }
      }
    }

    reconnect(help) {
      if(!this.wanted || !this.stream || document.hidden || this.closed) return;
      this.clearWatchdogs();
      this.confirmedFrames = false;
      this.diagnostics.received_frames = 'unverified';

      // Anti-looping Reconnect Policy: Capped Exponential Backoff, Max 5 Retries
      if(this.reconnectAttempt >= this.maxReconnectAttempts) {
        this.negotiation.setState(NEG_STATES.FAILED);
        this.transition('IOS_CONNECTION_FAILED', 'Connection could not be established after 5 attempts. Verify network connectivity, then tap Start Camera to retry.');
        node('startCamera').disabled = false;
        node('startCamera').textContent = 'Retry Camera Connection';
        return;
      }

      const attempt = ++this.reconnectAttempt;
      const delayMs = Math.min(15000, 1000 * Math.pow(2, Math.min(4, attempt - 1)));
      const attemptHelp = `${help} (Attempt ${attempt}/${this.maxReconnectAttempts} in ${Math.round(delayMs / 1000)}s)`;

      this.transition('IOS_RECONNECTING', attemptHelp);

      if(this.reconnectTimer) return;
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect();
      }, delayMs);
    }

    disconnectTransport() {
      this.clearWatchdogs();
      // Architectural separation: media reset MUST NOT close the persistent control plane socket!
      const peer = this.peer;
      this.peer = null;
      if(peer) {
        peer.onconnectionstatechange = null;
        peer.onicecandidate = null;
        peer.oniceconnectionstatechange = null;
        peer.onicegatheringstatechange = null;
        try { peer.close(); } catch(_) {}
      }
    }

    disconnectAll() {
      this.disconnectTransport();
      const socket = this.socket;
      this.socket = null;
      if(socket) {
        socket.onclose = null;
        socket.onmessage = null;
        socket.onerror = null;
        try { socket.close(); } catch(_) {}
      }
    }

    pause(help = 'Camera paused. Tap Resume Camera when ready.') {
      this.confirmedFrames = false;
      this.transition('IOS_CAMERA_INTERRUPTED', help);
      this.disconnectTransport();
      const stream = this.stream;
      this.stream = null;
      if(stream) stream.getTracks().forEach(track => { track.onended = null; track.onmute = null; track.onunmute = null; track.stop(); });
      node('cameraPreview').srcObject = null;
      this.diagnostics.video_track = 'paused';
      this.diagnostics.peer_connection = 'paused';
      this.diagnostics.signaling = 'disconnected';
      this.diagnostics.received_frames = 'unverified';
      this.render(help);
    }

    stop(state = 'IOS_CAMERA_STOPPED', help = 'Camera off. Tap Resume Camera to start again.') {
      if (state === 'IOS_CAMERA_STOPPED') {
        const evId = 'stop_cam_' + Math.random().toString(36).substring(2, 10);
        this.explicitStopCamera = {
          event_id: evId,
          reason: 'USER_STOPPED',
          timestamp: Date.now() / 1000,
          user_initiated: true
        };
        const stopEvt = {
          type: 'IOS_CAMERA_STOPPED',
          event_id: evId,
          session_id: this.sessionId,
          device_id: this.deviceId || this.credential,
          connection_id: this.connectionId,
          source_id: 'IOS_FRONT_CAMERA',
          event_id: 'stop_cam_' + Math.random().toString(36).substring(2, 10),
          reason: 'USER_STOPPED_CAMERA',
          timestamp: Date.now() / 1000,
          user_initiated: true,
          track_state: 'STOPPED'
        };
        this.send(stopEvt);
        if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
          try {
            fetch('/api/strict_session/ios/media_stopped', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(stopEvt)
            });
          } catch (_) {}
        }
      }
      this.wanted = false;
      ++this.generation;
      this.confirmedFrames = false;
      this.reconnectAttempt = 0;
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
      this.clearWatchdogs();
      this.transition(state, help);
      this.disconnectTransport();
      if(state === 'IOS_CAMERA_REVOKED') {
        this.credential = null;
        this.expiresAt = 0;
        clearTimeout(this.refreshTimer);
      }
      const stream = this.stream;
      this.stream = null;
      if(stream) stream.getTracks().forEach(track => { track.onended = null; track.onmute = null; track.onunmute = null; track.stop(); });
      node('cameraPreview').srcObject = null;
      this.diagnostics.video_track = 'off';
      this.diagnostics.peer_connection = 'closed';
      this.diagnostics.signaling = 'disconnected';
      this.diagnostics.received_frames = 'unverified';
      this.render(help);
    }

    checkScreenCapability() {
      const sup = typeof navigator.mediaDevices?.getDisplayMedia === 'function';
      const cap = node('screenCapStatus');
      const startBtn = node('startScreenShare');
      if (sup) {
        if (cap) cap.textContent = 'iPad Screen Sharing: Available (Tap Share iPad Screen below)';
        if (startBtn) startBtn.disabled = false;
      } else {
        if (cap) cap.textContent = 'iPad Screen Sharing: Native Broadcast / iPadOS 16+ required';
        const sState = node('screenState');
        if (sState) sState.textContent = 'Whole-device screen capture requires iPadOS 16+ or the StrictRecords Broadcast Upload Extension.';
        if (startBtn) startBtn.disabled = true;
      }
    }

    async connectScreenShare() {
      if (!this.screenTrack || !this.screenStream || !this.socket || this.socket.readyState !== 1) return;
      if (this.screenPeer) {
        try { this.screenPeer.close(); } catch (_) {}
        this.screenPeer = null;
      }
      const screenPeer = new RTCPeerConnection({
        iceServers: this.iceServers || [],
        bundlePolicy: 'max-bundle'
      });
      this.screenPeer = screenPeer;
      const screenConnId = 'scr_' + Math.random().toString(36).slice(2, 10);
      const localCands = [];
      let offerSent = false;
      this.screenSender = screenPeer.addTrack(this.screenTrack, this.screenStream);
      const sources = { [this.screenTrack.id]: 'IOS_FULL_SCREEN_SHARE' };
      this.send({ type: 'track_sources', sources });

      screenPeer.onicecandidate = ev => {
        if (this.screenPeer !== screenPeer || !ev.candidate) return;
        const candObj = ev.candidate.toJSON ? ev.candidate.toJSON() : ev.candidate;
        if (offerSent) {
          this.send({
            type: 'candidate',
            candidate: candObj,
            connection_id: screenConnId,
            tx_id: this.negotiation.nextTxId(),
            source_id: 'IOS_FULL_SCREEN_SHARE'
          });
        } else {
          localCands.push(candObj);
        }
      };

      const txId = this.negotiation.nextTxId();
      const offer = await screenPeer.createOffer();
      if (this.screenPeer !== screenPeer) return;
      await screenPeer.setLocalDescription(offer);
      this.send({
        type: 'offer',
        sdp: screenPeer.localDescription.sdp,
        connection_id: screenConnId,
        tx_id: txId,
        source_id: 'IOS_FULL_SCREEN_SHARE'
      });
      offerSent = true;
      for (const cand of localCands) {
        this.send({
          type: 'candidate',
          candidate: cand,
          connection_id: screenConnId,
          tx_id: this.negotiation.nextTxId(),
          source_id: 'IOS_FULL_SCREEN_SHARE'
        });
      }
    }

    async startScreenShare() {
      if (typeof navigator.mediaDevices?.getDisplayMedia !== 'function') {
        const sState = node('screenState');
        if (sState) sState.textContent = 'getDisplayMedia is unavailable in this Safari version. Whole-device capture requires iPadOS 16+ or the native Broadcast Extension.';
        return;
      }
      try {
        await this.redeem();
        // Notify Windows immediately so the 60-second initial negotiation timer begins
        this.send({ type: 'screen_negotiating', force_restart: true, source_id: 'IOS_FULL_SCREEN_SHARE' });
        const sState = node('screenState');
        if (sState) sState.textContent = 'SCREEN SHARE NEGOTIATING (60s grace) — Approve screen broadcast prompt...';
        const stream = await navigator.mediaDevices.getDisplayMedia({ video: { displaySurface: 'monitor' }, audio: false });
        if (stream.getAudioTracks().length) stream.getAudioTracks().forEach(t => t.stop());
        const track = stream.getVideoTracks()[0];
        if (!track) throw Error('No screen video track returned');
        this.screenStream = stream;
        this.screenTrack = track;
        const sPrev = node('screenPreview');
        if (sPrev) { sPrev.srcObject = stream; try { await sPrev.play(); } catch (_) {} }
        const sActive = node('screenActive'); if (sActive) sActive.hidden = false;
        const sPl = node('screenPlaceholder'); if (sPl) sPl.hidden = true;
        const startBtn = node('startScreenShare'); if (startBtn) startBtn.disabled = true;
        const stopBtn = node('stopScreenShare'); if (stopBtn) stopBtn.disabled = false;
        if (sState) sState.textContent = '● Connecting iPad Full-Screen Share to Windows...';
        track.onended = () => this.stopScreenShare();

        if (this.socket && this.socket.readyState === 1 && this.diagnostics.signaling === 'authenticated') {
          await this.connectScreenShare();
        } else if (this.wanted && this.stream) {
          this.connect();
        } else {
          const wsUrl = 'wss://' + location.host + '/ios-camera/signaling';
          const socket = new WebSocket(wsUrl);
          this.socket = socket;
          socket.onopen = () => {
            if (this.socket === socket) {
              this.send({ type: 'authenticate', credential: this.credential, connection_id: this.negotiation.connectionId });
              this.send({ type: 'screen_negotiating', force_restart: false, source_id: 'IOS_FULL_SCREEN_SHARE' });
            }
          };
          socket.onmessage = ev => {
            try {
              const msg = JSON.parse(ev.data);
              if (msg.type === 'authenticated') {
                this.diagnostics.signaling = 'authenticated';
                this.iceServers = msg.ice_servers || this.iceServers || [];
                this.connectScreenShare().catch(() => {});
              } else {
                this.message(ev, socket).catch(() => {});
              }
            } catch (_) {}
          };
        }
      } catch (err) {
        const sState = node('screenState');
        if (sState) sState.textContent = 'Screen share cancelled: ' + (err.message || err.name);
        this.stopScreenShare();
      }
    }

    stopScreenShare(reason = 'USER_STOPPED') {
      const evId = 'stop_scr_' + Math.random().toString(36).substring(2, 10);
      const isUser = (reason === 'USER_STOPPED' || reason === 'USER_STOPPED_BROADCAST');
      this.explicitStopScreen = {
        event_id: evId,
        reason: reason,
        timestamp: Date.now() / 1000,
        user_initiated: isUser
      };
      this.screenSharingWanted = false;
      const stopEvt = {
        event_id: evId,
        type: 'IOS_SCREEN_SHARE_STOPPED',
        session_id: this.sessionId,
        device_id: this.deviceId || this.credential,
        connection_id: this.screenConnectionId || this.connectionId,
        source_id: 'IOS_FULL_SCREEN_SHARE',
        event_id: 'stop_scr_' + Math.random().toString(36).substring(2, 10),
        reason: reason,
        timestamp: Date.now() / 1000,
        user_initiated: true,
        track_state: 'STOPPED'
      };
      this.send(stopEvt);
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
        try {
          fetch('/api/strict_session/ios/media_stopped', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(stopEvt)
          });
        } catch (_) {}
      }
      if (this.screenStream) {
        this.screenStream.getTracks().forEach(t => { t.onended = null; t.stop(); });
        this.screenStream = null;
      }
      this.screenTrack = null;
      if (this.screenPeer) {
        try { this.screenPeer.close(); } catch (_) {}
        this.screenPeer = null;
      }
      this.screenSender = null;
      const sPrev = node('screenPreview'); if (sPrev) sPrev.srcObject = null;
      const sActive = node('screenActive'); if (sActive) sActive.hidden = true;
      const sPl = node('screenPlaceholder'); if (sPl) sPl.hidden = false;
      const startBtn = node('startScreenShare'); if (startBtn) startBtn.disabled = typeof navigator.mediaDevices?.getDisplayMedia !== 'function';
      const stopBtn = node('stopScreenShare'); if (stopBtn) stopBtn.disabled = true;
      const sState = node('screenState'); if (sState) sState.textContent = 'Screen sharing stopped.';
    }

    end(help) {
      this.stopScreenShare();
      this.stop('IOS_CAMERA_STOPPED', help);
      this.closed = true;
      this.credential = null;
      clearTimeout(this.refreshTimer);
      clearInterval(this.timerInterval);
      clearInterval(this.timerDrawInterval);
      clearInterval(this.statsInterval);
      try { localStorage.removeItem('strictrecords.camera.timer-lease.v1'); } catch(_) {}
      this.render(help);
    }
  }

  const camera = new StrictIOSCamera();
  node('startCamera').addEventListener('click', () => camera.start());
  node('stopCamera').addEventListener('click', () => camera.stop());
  node('startScreenShare')?.addEventListener('click', () => camera.startScreenShare());
  node('stopScreenShare')?.addEventListener('click', () => camera.stopScreenShare());
  camera.checkScreenCapability();
  node('fullscreenTimer')?.addEventListener('click', async () => {
    const isExpanded = document.body.classList.contains('timer-expanded');
    if (isExpanded) {
      document.body.classList.remove('timer-expanded');
      try {
        const exit = document.exitFullscreen || document.webkitExitFullscreen;
        if (exit && (document.fullscreenElement || document.webkitFullscreenElement)) {
          await exit.call(document);
        }
      } catch(_) {}
      node('fullscreenTimer').textContent = 'Expand to Fullscreen';
    } else {
      document.body.classList.add('timer-expanded');
      try {
        if (!/iPad|iPhone|iPod/.test(navigator.userAgent)) {
          const root = document.documentElement;
          const enter = root.requestFullscreen;
          if (enter && !document.fullscreenElement) await enter.call(root);
        }
      } catch(_) {}
      node('fullscreenTimer').textContent = 'Restore page';
    }
    camera.timerHeartbeat();
  });
  for(const event of ['fullscreenchange', 'webkitfullscreenchange']) document.addEventListener(event, () => camera.timerHeartbeat());
  document.addEventListener('visibilitychange', () => {
    if(document.hidden && camera.stream) camera.pause('Camera Paused while backgrounded. Return here and tap Resume Camera.');
    else if(!document.hidden && !camera.stream && camera.credential) camera.render('Session preserved. Tap Resume Camera to restore video.');
    camera.timerHeartbeat();
  });
  window.addEventListener('pagehide', (event) => {
    if (event.persisted) return;
    if (document.body.classList.contains('timer-expanded')) return;
  });
  window.addEventListener('online', () => { if(camera.wanted) camera.reconnect('Network restored. Reconnecting video.'); });
  window.strictIOSCamera = camera;
})();
'''
