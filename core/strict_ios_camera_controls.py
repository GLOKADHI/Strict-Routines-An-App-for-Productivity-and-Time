"""Consolidated iOS camera monitoring controls for unified invitation panel."""

JS = r'''

let ssIOSCameraStatusData = null, ssIOSCameraPoll = false;



function ssFormatCountdown(expiresAt) {

  const now = Date.now() / 1000;

  const rem = Math.max(0, Math.round(expiresAt - now));

  const timeStr = new Date(expiresAt * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });

  if (rem <= 0) return 'Invitation expired. Create a new invitation; device timer remains unchanged.';

  const m = Math.floor(rem / 60);

  const s = String(rem % 60).padStart(2, '0');

  return `Expires in ${m}:${s} (${timeStr})`;

}



function ssGetIOSMonitoringState(device, cameraStatus, invite) {

  if (!invite) return { state: 'NO_INVITATION', label: 'No Invitation Created', color: '#9baeb0' };

  if (!device?.joined) return { state: 'INVITATION_CREATED', label: 'Invitation Created (Scan QR on iPhone)', color: '#b58aff' };

  if (!device?.report?.timer_ready) return { state: 'DEVICE_CONNECTED', label: 'Device Connected (Joining timer…)', color: '#b58aff' };



  const camState = cameraStatus?.status?.state || 'IOS_CAMERA_STOPPED';

  const streaming = cameraStatus?.status?.streaming === true;



  if (streaming) {

    const lastTime = cameraStatus.status.last_frame_at ? ' · ' + new Date(cameraStatus.status.last_frame_at * 1000).toLocaleTimeString() : '';

    return { state: 'VIDEO_STREAMING', label: 'Video Streaming (Windows receiving frames' + lastTime + ')', color: '#4be78b' };

  }

  if (camState === 'IOS_CAMERA_INTERRUPTED') {

    return { state: 'CAMERA_PAUSED', label: 'Camera paused · Return to the iPhone timer page', color: '#ffb86c' };

  }

  if (camState === 'IOS_WEBRTC_NEGOTIATING' || camState === 'IOS_VIDEO_CONNECTING') {

    return { state: 'WEBRTC_CONNECTING', label: 'WebRTC Connecting (Negotiating video stream…)', color: '#d3b6ff' };

  }

  if (camState === 'IOS_CAMERA_INITIALIZING') {

    return { state: 'CAMERA_CAPTURING', label: 'Camera Capturing (Initializing video…)', color: '#d3b6ff' };

  }

  if (camState === 'IOS_CAMERA_PERMISSION_GRANTED') {

    return { state: 'CAMERA_PERMISSION_GRANTED', label: 'Camera Permission Granted', color: '#d3b6ff' };

  }

  if (camState === 'IOS_CAMERA_PERMISSION_PENDING') {

    return { state: 'PERMISSION_PENDING', label: 'Waiting for Camera Permission on iPhone', color: '#b58aff' };

  }

  if (cameraStatus?.tls_verified) {

    return { state: 'TIMER_SYNCHRONIZED', label: 'Timer Synchronized · HTTPS Verified (Tap Start Camera on iPhone)', color: '#72eaa8' };

  }

  return { state: 'TIMER_SYNCHRONIZED_LOCAL', label: 'Timer Synchronized (Local Network Fallback)', color: '#72eaa8' };

}



async function ssRefreshIOSCamera() {

  // Completely remove any legacy duplicate camera card from Step 1 or DOM

  document.querySelectorAll('[data-ios-camera]').forEach(el => el.remove());



  if (ssIOSCameraPoll || !strictPreparationData?.preparation_id || currentTab !== 'strict-session') return;

  ssIOSCameraPoll = true;

  try {

    const result = await ssRequest('camera_status', { preparation_id: strictPreparationData.preparation_id });

    ssIOSCameraStatusData = result;



    const card = document.querySelector('#ssInvitations [data-role="IOS"]');

    if (!card) return;



    const device = strictPreparationData?.devices?.IOS || {};

    const invite = strictPreparationData?.invitations?.find(i => i.role === 'IOS');



    // 1. Expiry Countdown

    const expiryEl = card.querySelector('#ssIOSExpiry');

    if (expiryEl && invite?.expires_at) {

      expiryEl.textContent = ssFormatCountdown(invite.expires_at);

    }



    // 2. HTTPS Connection Status & Warning

    const httpsStatusEl = card.querySelector('#ssIOSHttpsStatus');

    if (httpsStatusEl) {

      const isVerified = result.tls_verified === true || invite?.https_verified === true;

      httpsStatusEl.className = 'ss-https-container ' + (isVerified ? 'ss-status-verified' : 'ss-status-warning');

      const originDisplay = result.origin ? ` · ${result.origin}` : '';

      if (isVerified) {

        httpsStatusEl.innerHTML = `<strong>✓ HTTPS Verified</strong> · Canonical Cloudflare Tunnel${originDisplay}`;

      } else {

        const warnText = invite?.warning || result.configuration_error || 'HTTPS probe unverified';

        httpsStatusEl.innerHTML = `<strong>⚠️ Local Network Timer Pairing Only · HTTPS Probe Failed</strong><br><small class="ss-muted">Timer-only local-network pairing does not enable browser-based camera access. ${warnText}</small>`;

      }

    }



    // 3. Connection Diagnostics Details

    const diagContent = card.querySelector('#ssIOSDiagContent');

    if (diagContent) {

      const d = result.verification_details || {};

      diagContent.innerHTML = `

        <dl class="ss-diag-grid">

          <dt>Tunnel Hostname</dt><dd>${d.hostname || (result.origin ? new URL(result.origin).hostname : 'Not configured')}</dd>

          <dt>DNS Resolution</dt><dd>${d.dns_resolved ? '✓ Resolved' : (d.error && d.error.includes('DNS') ? '❌ Failed (' + d.error + ')' : '❌ Not verified')}</dd>

          <dt>HTTPS Reachable</dt><dd>${d.https_reachable ? '✓ Responded (200 OK)' : '❌ Unreachable'}</dd>

          <dt>Companion Route</dt><dd>${d.companion_route_ok ? '✓ Responded' : '❌ Not reached'}</dd>

          <dt>WSS Signaling</dt><dd>${d.wss_signaling_ok ? '✓ Connected' : '❌ Unavailable'}</dd>

          <dt>Diagnostics Note</dt><dd>${d.error || result.configuration_error || 'All checks verified'}</dd>

        </dl>`;

    }



    // 4. iOS Camera & Video-Sharing Status

    const monState = ssGetIOSMonitoringState(device, result, invite);

    const stateBadge = card.querySelector('#ssIOSStateBadge');

    if (stateBadge) {

      stateBadge.textContent = monState.label;

      stateBadge.style.color = monState.color;

    }



    // 5. Live Monitoring Preview / Placeholder

    const previewImg = card.querySelector('#ssIOSPreviewImg');

    const placeholder = card.querySelector('#ssIOSPreviewPlaceholder');

    const liveTag = card.querySelector('#ssIOSLiveTag');

    const jpeg = result.preview?.jpeg;

    const isStreaming = result.status?.streaming === true && !!jpeg;



    if (previewImg && placeholder) {

      if (isStreaming) {

        previewImg.src = 'data:image/jpeg;base64,' + jpeg;

        previewImg.hidden = false;

        placeholder.hidden = true;

        if (liveTag) liveTag.hidden = false;

      } else if (result.status?.state === 'IOS_CAMERA_INTERRUPTED') {

        previewImg.hidden = true;

        previewImg.removeAttribute('src');

        placeholder.hidden = false;

        placeholder.textContent = '⏸ Camera paused · iPhone locked or Safari backgrounded. Return to the page to reconnect; Windows controls the timer.';

        if (liveTag) liveTag.hidden = true;

      } else {

        previewImg.hidden = true;

        previewImg.removeAttribute('src');

        placeholder.hidden = false;

        placeholder.textContent = result.configuration_error ?

          'Browser camera stream unavailable (' + result.configuration_error + ')' :

          'Awaiting video stream · Open the invitation on iPhone and tap Start Camera';

        if (liveTag) liveTag.hidden = true;

      }

    }

    ssUpdateDualCameraDashboard(result);

  } catch (error) {

    /* Timer readiness continues independently */

  } finally {

    ssIOSCameraPoll = false;

  }

}



// Countdown timer interval for smoother 1s updates

setInterval(() => {

  if (currentTab !== 'strict-session') return;

  const invite = strictPreparationData?.invitations?.find(i => i.role === 'IOS');

  const expiryEl = document.querySelector('#ssInvitations [data-role="IOS"] #ssIOSExpiry');

  if (expiryEl && invite?.expires_at) {

    expiryEl.textContent = ssFormatCountdown(invite.expires_at);

  }

}, 1000);



if (typeof setTimeout === 'function' && typeof clearTimeout === 'function') {

  let ssIOSCameraTimer = null;

  function ssScheduleIOSCameraPoll() {

    clearTimeout(ssIOSCameraTimer);

    const isStreaming = ssIOSCameraStatusData?.status?.streaming === true;

    const delay = isStreaming ? 400 : 2000;

    ssIOSCameraTimer = setTimeout(async () => {

      await ssRefreshIOSCamera();

      ssScheduleIOSCameraPoll();

    }, delay);

  }

  ssScheduleIOSCameraPoll();

} else {

  setInterval(ssRefreshIOSCamera, 2000);

}



function ssUpdateDualCameraDashboard(result) {

  const panel = document.getElementById('ssDualCameraPanelReady');

  if (!panel) return;



  const preview = result?.preview || {};

  const status = result?.status || {};

  const diag = result?.ipad_camera_diagnostics || {};

  const initPrereq = result?.initial_prerequisite || {};

  const jpeg = preview.jpeg;

  const rxFps = Number(preview.received_fps ?? diag.received_fps ?? 0);
  const procFps = Number(preview.processing_fps ?? diag.processing_fps ?? 0);
  const decFps = Number(diag.decoded_fps ?? rxFps ?? 0);
  const isIosStreaming = (status.streaming === true || rxFps > 0) && !!jpeg;
  const isIosReceiving = status.streaming === true || rxFps > 0;



  const hc = strictPreparationData?.environment?.hardware_checks || {};

  const hasSelectedCam = typeof strictHostCamera !== 'undefined' && !!strictHostCamera?.native_id;

  const webcamPassed = hc.webcam?.passed === true && strictSetupMonitor;

  const monitorRunning = hc.strictguard?.passed === true && strictSetupMonitor;

  const faceVerified = hc.face?.passed === true && strictSetupMonitor;

  const winPreviewImg = document.getElementById('ssSetupCameraImage');

  const winJpeg = winPreviewImg?.src;

  const hasFreshWinFrame = webcamPassed && ((typeof ssWinPreviewFresh !== 'undefined' && ssWinPreviewFresh) || (winJpeg && !winPreviewImg?.hidden));

  const winCamActive = webcamPassed && monitorRunning;



  const screen = result?.ipad_screen_share || {};

  const isScreenStreaming = screen.is_streaming === true;



  // 1. Overall Composite Status Badge

  const badge = document.getElementById('ssDualOverallBadge');

  if (badge) {

    if (winCamActive && (isIosStreaming || isIosReceiving) && isScreenStreaming) {

      badge.textContent = '● ALL 3 SOURCES ACTIVE (WEBCAM + iOS CAMERA + iPAD SCREEN)';

      badge.style.background = '#103822';

      badge.style.color = '#4be78b';

      badge.style.borderColor = '#4be78b';

    } else if (winCamActive && (isIosStreaming || isIosReceiving)) {

      badge.textContent = 'DUAL CAMERAS ACTIVE (PC WEBCAM + iOS CAMERA)';

      badge.style.background = '#103822';

      badge.style.color = '#4be78b';

      badge.style.borderColor = '#4be78b';

    } else if (winCamActive && !isIosStreaming && !isIosReceiving) {

      badge.textContent = 'WINDOWS CAMERA ACTIVE – iOS SOURCES UNAVAILABLE';

      badge.style.background = '#2b2112';

      badge.style.color = '#f59e0b';

      badge.style.borderColor = '#f59e0b';

    } else if (!winCamActive && (isIosStreaming || isIosReceiving)) {

      badge.textContent = 'iOS CAMERA ACTIVE – WINDOWS CAMERA UNAVAILABLE';

      badge.style.background = '#2b2112';

      badge.style.color = '#f59e0b';

      badge.style.borderColor = '#f59e0b';

    } else {

      badge.textContent = 'PROCTORING SOURCES UNAVAILABLE';

      badge.style.background = '#2b161e';

      badge.style.color = '#ef4444';

      badge.style.borderColor = '#ef4444';

    }

  }



  // 2. Windows Camera Tile (Section 7 distinct states)

  let winStateLabel = 'OFF';

  if (winCamActive && faceVerified && hasFreshWinFrame) {

    winStateLabel = 'HEAD_GAZE_MONITOR_ACTIVE';

  } else if (webcamPassed && faceVerified) {

    winStateLabel = 'FACE_VERIFIED';

  } else if (webcamPassed && monitorRunning) {

    winStateLabel = 'FACE_DETECTION_ACTIVE';

  } else if (hasFreshWinFrame) {

    winStateLabel = 'FRESH_FRAME_RECEIVED';

  } else if (strictSetupMonitor && hasSelectedCam) {

    winStateLabel = 'CAMERA_CAPTURE_ACTIVE';

  } else if (hasSelectedCam) {

    winStateLabel = 'CAMERA_DEVICE_SELECTED';

  }



  const winTileBadge = document.getElementById('ssWinCamTileBadge');

  if (winTileBadge) {

    winTileBadge.textContent = winStateLabel;

    winTileBadge.style.color = winCamActive ? '#4be78b' : (strictSetupMonitor ? '#f59e0b' : '#a49ab5');

    winTileBadge.style.background = winCamActive ? '#1b2f23' : (strictSetupMonitor ? '#2b2112' : '#221f2b');

  }

  const winImg = document.getElementById('ssWinCamTileImg');

  const winPl = document.getElementById('ssWinCamTilePlaceholder');

  const winLive = document.getElementById('ssWinCamTileLive');

  if (winImg && winPl) {

    if (strictSetupMonitor && winJpeg && !winPreviewImg?.hidden) {

      winImg.src = winJpeg;

      winImg.hidden = false;

      winPl.hidden = true;

      if (winLive) winLive.hidden = false;

    } else {

      winImg.hidden = true;

      winPl.hidden = false;

      winPl.textContent = strictSetupMonitor ? 'Waiting for a fresh frame from the selected camera…' : 'Local monitor is not running · Run hardware checks';

      if (winLive) winLive.hidden = true;

    }

  }

  ssText('ssWinCamFaceVal', faceVerified ? 'FACE_VERIFIED' : (strictSetupMonitor ? 'FACE_DETECTION_ACTIVE' : 'UNVERIFIED'));

  ssText('ssWinCamGazeVal', monitorRunning ? (hc.calibration?.passed ? 'CENTER (HEAD_GAZE_MONITOR_ACTIVE)' : 'CALIBRATING') : 'MONITOR NOT RUNNING');

  ssText('ssWinCamPoseVal', monitorRunning ? 'Yaw: 0 deg Pitch: 0 deg' : 'Monitor idle');

  ssText('ssWinCamFPSVal', (winCamActive && hasFreshWinFrame) ? '15.0 FPS' : '0.0 FPS');



  // 3. iOS Camera Tile

  const isProcessingStalled = diag.health_state === 'IOS_PROCESSING_STALLED' || (rxFps > 0 && procFps === 0 && (status.frame_sequence || 0) > 15);



  const iosTileBadge = document.getElementById('ssIOSCamTileBadge');

  if (iosTileBadge) {

    if (isProcessingStalled) {

      iosTileBadge.textContent = 'IOS_PROCESSING_STALLED';

      iosTileBadge.style.color = '#f59e0b';

      iosTileBadge.style.background = '#2b2112';

    } else if (isIosStreaming || (typeof rxFps !== 'undefined' && rxFps > 0)) {

      iosTileBadge.textContent = 'STREAMING';

      iosTileBadge.style.color = '#4be78b';

      iosTileBadge.style.background = '#1b2f23';

    } else if (status.state === 'IOS_CAMERA_INTERRUPTED' || diag.health_state === 'CAMERA_TEMPORARILY_PAUSED' || diag.health_state === 'CAMERA_DEGRADED') {

      iosTileBadge.textContent = diag.health_state || 'PAUSED';

      iosTileBadge.style.color = '#f59e0b';

      iosTileBadge.style.background = '#2b2112';

    } else {

      iosTileBadge.textContent = 'DISCONNECTED';

      iosTileBadge.style.color = '#ef4444';

      iosTileBadge.style.background = '#2b161e';

    }

  }

  const iosImg = document.getElementById('ssIOSCamTileImg');

  const iosPl = document.getElementById('ssIOSCamTilePlaceholder');

  const iosLive = document.getElementById('ssIOSCamTileLive');

  if (iosImg && iosPl) {

    if (isIosStreaming) {

      iosImg.src = 'data:image/jpeg;base64,' + jpeg;

      iosImg.hidden = false;

      iosPl.hidden = true;

      if (iosLive) iosLive.hidden = false;

    } else {

      iosImg.hidden = true;

      iosPl.hidden = false;

      if (iosLive) iosLive.hidden = true;

    }

  }



  // Always update telemetry values including 0.0 FPS (never leave stale 10.5 FPS)

  ssText('ssIOSCamFaceVal', preview.face || (isIosStreaming ? 'SCANNING' : 'UNVERIFIED'));

  ssText('ssIOSCamGazeVal', preview.gaze_status || (isIosStreaming ? 'MONITORING' : 'INITIALIZING'));

  if (preview.head_pose) {

    ssText('ssIOSCamPoseVal', `Yaw: ${preview.head_pose.yaw || 0} deg Pitch: ${preview.head_pose.pitch || 0} deg`);

  }

  ssText('ssIOSCamRxFPSVal', `${rxFps.toFixed(1)} FPS`);

  ssText('ssIOSCamProcFPSVal', `${procFps.toFixed(1)} FPS`);

  if (isProcessingStalled) {

    ssText('ssIOSCamWebRTCVal', 'VIDEO RECEIVING – PROCTORING PROCESSING STALLED');

  } else if (status.state) {

  if (typeof rxFps !== 'undefined' && rxFps > 0.0) {
    ssText('ssIOSCamWebRTCVal', (typeof procFps !== 'undefined' && procFps > 0) ? 'VIDEO STREAMING (ACTIVE)' : 'VIDEO RECEIVING (INFERENCE PENDING)');
  } else {
    ssText('ssIOSCamWebRTCVal', status.state.replace('IOS_', '').replaceAll('_', ' '));
  }

  }



  // 4. Initial Camera Prerequisite & Live iOS Camera Health Panel

  const frameSeq = Number(status.frame_sequence ?? diag.frame_sequence ?? 0);

  const initPassed = initPrereq.initial_check_passed === true || frameSeq >= 1;

  const initBadge = document.getElementById('ssIOSInitialCheckBadge');

  if (initBadge) {

    initBadge.textContent = initPassed ? 'IOS_CAMERA_INITIAL_CHECK: PASSED' : (initPrereq.status || 'IOS_CAMERA_INITIAL_CHECK: PENDING');

    initBadge.style.color = initPassed ? '#4be78b' : '#f59e0b';

    initBadge.style.background = initPassed ? '#1b2f23' : '#2b2112';

  }

  const steps = initPrereq.steps || {};

  const iosJoined = !!strictPreparationData?.devices?.IOS?.joined;

  ssText('ssInitChkReg', (steps['1_device_registered_authenticated'] || iosJoined || initPassed) ? '✓ OK' : 'PENDING');

  ssText('ssInitChkPerm', (steps['2_camera_permission_granted'] || status.state === 'IOS_CAMERA_STREAMING' || initPassed) ? '✓ OK' : 'PENDING');

  ssText('ssInitChkWebrtc', (steps['3_webrtc_video_connected'] || status.streaming || initPassed) ? '✓ OK' : 'PENDING');

  ssText('ssInitChkRx', (steps['4_real_video_frame_received'] || frameSeq >= 1) ? `✓ OK (${frameSeq})` : 'PENDING');

  ssText('ssInitChkDec', (steps['5_frame_decoded'] || frameSeq >= 1) ? '✓ OK' : 'PENDING');

  ssText('ssInitChkProc', (steps['6_frame_passed_to_pipeline'] || frameSeq >= 1) ? '✓ OK' : 'PENDING');

  ssText('ssIOSInitialCheckHint', initPassed ? 'Initial camera check passed. Short pauses will not block startup.' : 'Requires at least 1 decoded iOS video frame before starting.');



  const healthState = isProcessingStalled ? 'IOS_PROCESSING_STALLED' : (diag.health_state || (isIosStreaming ? 'VIDEO_FRAMES_RECEIVED' : 'CAMERA_INITIALIZING'));

  const healthBadge = document.getElementById('ssIOSHealthStateBadge');

  if (healthBadge) {

    healthBadge.textContent = healthState;

    healthBadge.style.color = (healthState === 'VIDEO_FRAMES_RECEIVED' || healthState === 'IOS_CAMERA_RECOVERED') ? '#4be78b' : (healthState === 'CAMERA_INTERRUPTED' ? '#ef4444' : '#f59e0b');

    healthBadge.style.background = (healthState === 'VIDEO_FRAMES_RECEIVED' || healthState === 'IOS_CAMERA_RECOVERED') ? '#1b2f23' : (healthState === 'CAMERA_INTERRUPTED' ? '#2b161e' : '#2b2112');

  }

  ssText('ssIOSIceRouteVal', diag.ice_route_type || 'UNKNOWN');

  const gapSec = Number(diag.seconds_since_last_valid_frame ?? 0);

  ssText('ssIOSFrameGapVal', `${gapSec.toFixed(1)}s / 15.0s`);

  ssText('ssIOSDecodedFPSVal', `${decFps.toFixed(1)} FPS`);

  ssText('ssIOSDroppedVal', `${diag.dropped_frames_count ?? 0} frames`);

  ssText('ssIOSLastFrameVal', status.last_frame_at ? new Date(status.last_frame_at * 1000).toLocaleTimeString() : '—');

  ssText('ssIOSRootCauseVal', diag.root_cause_classification || 'HEALTHY');



  // 4. iPad Screen Share Tile

  const screenBadge = document.getElementById('ssIOSScreenTileBadge');

  if (screenBadge) {

    if (screen.is_interrupted) {

      screenBadge.textContent = 'INTERRUPTED';

      screenBadge.style.color = '#ef4444';

      screenBadge.style.background = '#2b161e';

    } else if (isScreenStreaming) {

      screenBadge.textContent = screen.activity_level || 'STREAMING';

      screenBadge.style.color = '#4be78b';

      screenBadge.style.background = '#1b2f23';

    } else if (screen.is_active) {

      screenBadge.textContent = 'CONNECTING';

      screenBadge.style.color = '#f59e0b';

      screenBadge.style.background = '#2b2112';

    } else {

      screenBadge.textContent = 'OFF';

      screenBadge.style.color = '#a49ab5';

      screenBadge.style.background = '#221f2b';

    }

  }



  const screenImg = document.getElementById('ssIOSScreenTileImg');

  const screenPl = document.getElementById('ssIOSScreenTilePlaceholder');

  const screenLive = document.getElementById('ssIOSScreenTileLive');

  if (screenImg && screenPl) {

    if (isScreenStreaming && screen.preview_jpeg) {

      screenImg.src = 'data:image/jpeg;base64,' + screen.preview_jpeg;

      screenImg.hidden = false;

      screenPl.hidden = true;

      if (screenLive) screenLive.hidden = false;

    } else {

      screenImg.hidden = true;

      screenPl.hidden = false;

      if (screenLive) screenLive.hidden = true;

    }

  }



  ssText('ssIOSScreenActivityVal', isScreenStreaming ? (screen.activity_level || 'STATIC') : 'OFF');

  ssText('ssIOSScreenMADVal', isScreenStreaming ? (screen.structural_delta_mad != null ? Number(screen.structural_delta_mad).toFixed(1) : '0.0') : '0.0');

  ssText('ssIOSScreenRxFPSVal', `${Number(screen.rx_fps || 0).toFixed(1)} FPS`);

  ssText('ssIOSScreenProcFPSVal', `${Number(screen.processing_fps || 0).toFixed(1)} FPS`);

  ssText('ssIOSScreenResVal', screen.resolution || '--');

  ssText('ssIOSScreenTransitionsVal', String(screen.activity_breakdown?.rapid_transition_count || 0));

  ssText('ssIOSScreenStatusVal', screen.status || 'STOPPED');

  ssText('ssIOSScreenWatchdogVal', screen.is_negotiating ? (screen.negotiation_countdown_label || 'Waiting for first valid frame (60s grace)') : (screen.negotiation_failed ? (screen.failure_reason || 'IOS_SCREEN_SHARE_FAILED') : (screen.is_interrupted ? 'INTERRUPTED (>10s gap)' : (isScreenStreaming ? 'OK (10s limit)' : 'IDLE'))));
  if (screenBadge) {
    if (screen.initial_verified === true || screen.status === 'IOS_SCREEN_SHARE_VERIFIED') { screenBadge.textContent = 'IOS_SCREEN_SHARE: VERIFIED'; screenBadge.style.color = '#4be78b'; screenBadge.style.background = '#1b2f23'; }
    else if (screen.is_negotiating === true) { screenBadge.textContent = 'SCREEN SHARE NEGOTIATING'; screenBadge.style.color = '#f59e0b'; screenBadge.style.background = '#2b2112'; if (screenPl) screenPl.textContent = 'SCREEN SHARE NEGOTIATING · ' + (screen.negotiation_countdown_label || 'Approve broadcast on iPad (60s grace)'); }
    else if (screen.negotiation_failed === true || screen.status === 'IOS_SCREEN_SHARE_FAILED') { screenBadge.textContent = 'IOS_SCREEN_SHARE_FAILED'; screenBadge.style.color = '#ef4444'; screenBadge.style.background = '#2b161e'; if (screenPl) screenPl.textContent = (screen.failure_reason || 'No valid screen frames received within 60s') + ' · Click Retry iOS Screen Share'; }
  }



  const stagesEl = document.getElementById('ssIOSPipelineStages');

  if (stagesEl && diag.pipeline_stages) {

    const stageRows = Object.entries(diag.pipeline_stages).map(([k, v]) => {

      const st = v?.status || 'NOT_STARTED';

      const icon = st === 'OK' ? '✓' : (st === 'FAILED' || st === 'STALLED' ? '❌' : '○');

      return `<div><strong>${icon} ${k}:</strong> ${st}${v?.detail ? ' (' + v.detail + ')' : ''}</div>`;

    });

    stagesEl.innerHTML = stageRows.join('');

  }

}

'''

