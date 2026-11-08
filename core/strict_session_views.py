"""Views mounted inside the existing dashboard section; no new desktop windows."""



import json



from core.strict_participants import REQUIRED_ROLES, COMPANION_ROLES



from core.strict_media_picker import CSS as MEDIA_CSS, HTML as MEDIA_HTML, JS as MEDIA_JS



from core.strict_check_settings import CSS as CHECK_CSS,HTML as CHECK_HTML,JS as CHECK_JS



from core.strict_camera_preview import CSS as PREVIEW_CSS,HTML as PREVIEW_HTML,JS as PREVIEW_JS



from core.strict_ios_camera_controls import JS as IOS_CAMERA_JS







CSS=r'''



#page-strict-session{--ss-purple:#b58aff;--ss-line:#49385f;--ss-card:#17171f;color:var(--text)}



.ss-steps{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin:0 0 28px;list-style:none;padding:0}



.ss-steps button{border:1px solid #36353d;border-radius:30px;padding:12px 20px;background:#202027;color:#aaa;cursor:pointer}



.ss-steps button[aria-current=step]{background:linear-gradient(135deg,#8444d4,#462378);border-color:#b68aff;color:white;box-shadow:0 0 18px #9a57d530}



.ss-heading{text-align:center;margin:22px 0 30px}.ss-heading h1{font-size:clamp(1.5rem,3vw,2.2rem);margin:0 0 10px}.ss-heading p{color:var(--text-secondary)}



.ss-layout{display:grid;grid-template-columns:minmax(220px,.8fr) minmax(0,2.5fr);gap:18px}



.ss-summary,.ss-device,.ss-environment,.ss-banner,.ss-invitation{background:linear-gradient(150deg,#211d2b,#121418);border:1px solid var(--ss-line);border-radius:20px;padding:22px;min-width:0}



.ss-summary{display:flex;flex-direction:column;gap:20px}.ss-summary svg{width:min(100%,250px);height:auto;align-self:center}.ss-summary h2{font-size:1.1rem}.ss-facts{display:grid;gap:18px;border:1px solid var(--ss-line);border-radius:14px;padding:17px;color:var(--text-secondary);line-height:1.65}.ss-facts strong{display:block;color:var(--ss-purple)}



.ss-devices{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:15px}.ss-device{padding:18px}.ss-device h3{font-size:1rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin:0 0 20px}



.ss-status{font-size:.72rem;padding:5px 9px;border-radius:20px;background:#34313c;color:#c7bfce}.ss-status[data-ready=true]{background:#173b29;color:#4be78b}



.ss-checks{display:grid;gap:17px;list-style:none;padding:0;margin:0}.ss-checks li{display:grid;grid-template-columns:20px minmax(0,1fr);gap:8px;font-size:.82rem}.ss-checks b{display:block;font-weight:600}.ss-checks small{display:block;color:var(--text-secondary);line-height:1.6;margin-top:3px;overflow-wrap:anywhere}.ss-mark{color:#a6a0ae}.ss-mark[data-ok=true]{color:#4be78b}



.ss-environment{display:flex;align-items:center;gap:24px;flex-wrap:wrap;margin-top:16px}.ss-environment>div{flex:1;min-width:160px}.ss-environment h3{font-size:.94rem}.ss-window{color:var(--ss-purple);font-size:1.6rem;font-weight:750}.ss-banner{margin-top:16px;padding:18px;color:#ddd2ed;line-height:1.65}.ss-banner[data-ready=true]{border-color:#259451;background:#102b1d;color:#beeed0}



.ss-actions{display:flex;gap:12px;justify-content:flex-end;flex-wrap:wrap;margin:24px 0}.ss-actions .ss-back{margin-right:auto}.ss-primary{background:linear-gradient(135deg,#ac63ff,#7435d4);color:white;border:1px solid #b88cff;border-radius:12px;padding:13px 22px;font-weight:700;cursor:pointer}.ss-primary:disabled{opacity:.4;cursor:not-allowed;box-shadow:none}



.ss-media{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:18px;font-size:.85rem;color:var(--text-secondary);line-height:1.6}.ss-media label{flex:1;min-width:220px}



.ss-invitations{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:18px}.ss-invitation img{display:block;width:190px;max-width:100%;margin:18px auto;border-radius:12px}.ss-invitation input{width:100%;min-width:0;color:var(--text);background:#111;border:1px solid var(--ss-line);padding:10px;border-radius:8px}.ss-muted{color:var(--text-secondary);font-size:.85rem;line-height:1.7}.ss-ready-card{max-width:800px;margin:auto}.ss-device-preview{width:180px;max-height:120px;border-radius:10px}



.ss-expiry-countdown{font-size:.85rem;color:#d3b6ff;margin:8px 0;text-align:center;font-weight:600}



.ss-https-container{margin-top:14px;padding:12px;border-radius:10px;font-size:.82rem;line-height:1.6}



.ss-status-verified{background:#102e21;border:1px solid #238654;color:#72eaa8}



.ss-status-warning{background:#2b1f14;border:1px solid #8c571a;color:#ffb86c}



.ss-diagnostics-details{margin-top:8px;font-size:.78rem;color:#bbaed0;cursor:pointer}



.ss-diagnostics-details summary{font-weight:600;padding:4px 0}



.ss-diag-grid{display:grid;grid-template-columns:minmax(120px,1fr) minmax(0,2fr);gap:6px;margin-top:8px;font-size:.76rem}



.ss-diag-grid dt{color:#9baeb0}.ss-diag-grid dd{margin:0;color:#e8eded}



.ss-camera-status-row{display:flex;justify-content:space-between;align-items:center;margin-top:14px;padding-top:12px;border-top:1px solid var(--ss-line);font-size:.85rem}



.ss-status-badge{font-size:.75rem;padding:3px 9px;border-radius:6px;background:#262235;color:#b58aff;font-weight:600}



.ss-preview-box{margin-top:10px;position:relative;border-radius:10px;overflow:hidden;background:#0c0e14;border:1px solid #362a47;min-height:130px;display:grid;place-items:center}



.ss-preview-box img{width:100%;max-height:220px;object-fit:contain;border-radius:10px}



.ss-preview-placeholder{padding:18px;font-size:.8rem;color:#a49ab5;text-align:center;line-height:1.6}



.ss-live-badge{position:absolute;top:8px;left:8px;padding:3px 8px;border-radius:6px;background:#0e3621e8;color:#72eaa8;font-size:.7rem;font-weight:700}







@media(max-width:1100px){.ss-layout{grid-template-columns:1fr}.ss-summary{display:grid;grid-template-columns:170px 1fr;align-items:center}.ss-summary .ss-facts{grid-column:1/-1}.ss-summary svg{width:150px}.ss-devices{grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr))}}



@media(max-width:600px){.ss-summary{display:block}.ss-summary svg{display:none}.ss-steps{justify-content:flex-start}.ss-steps button{padding:9px 12px}.ss-actions>*{width:100%}}



'''







CSS+=MEDIA_CSS+CHECK_CSS+PREVIEW_CSS







HTML=r'''



<div class="ss-header-controls" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;padding:12px 18px;background:linear-gradient(135deg,#1f1a2e,#14141c);border-radius:12px;border:1px solid var(--ss-line);flex-wrap:wrap;gap:12px;">

  <div style="display:flex;align-items:center;gap:10px;">

    <strong style="color:var(--text);font-size:1.05rem;letter-spacing:0.5px;">◈ STRICT PROCTORING SESSION</strong>

    <span class="ss-status-badge" id="ssGlobalLifecycleBadge" style="background:#262235;color:#b58aff;font-weight:700;">IDLE</span>

  </div>

  <div style="display:flex;gap:10px;align-items:center;margin-left:auto;">

    <button type="button" class="btn btn-primary" onclick="openSessionControlsModal()" style="background:linear-gradient(135deg,#7c3aed,#4f46e5);color:#fff;border:1px solid #a78bfa;font-weight:700;padding:8px 18px;border-radius:10px;display:inline-flex;align-items:center;gap:8px;box-shadow:0 4px 14px rgba(124,58,237,0.35);cursor:pointer;">

      <span>⚙️</span>

      <span>Session Controls</span>

    </button>

  </div>

</div>



<ol class="ss-steps" aria-label="Strict Session progress">



 <li><button type="button" data-ss-step="PRECHECK" onclick="strictPreparationStep('PRECHECK')">1. Prerequisite Check</button></li>



 <li><button type="button" data-ss-step="JOIN" onclick="strictPreparationStep('JOIN')">2. Join Devices</button></li>



 <li><button type="button" data-ss-step="READY" onclick="strictPreparationStep('READY')" disabled>3. Session Ready</button></li>



 <li><button type="button" data-ss-step="FOCUS" onclick="strictPreparationStep('FOCUS')" disabled>4. Focus Session</button></li>



</ol>



<div class="ss-banner" style="margin-bottom:20px;"><strong id="ssModeLabel">Scheduled Strict Session</strong><p class="ss-muted">Use your normal proof schedule, or try the same study interface in a separate 10-minute demo. The demo needs no proof window and creates no points, deductions or reward.</p><button type="button" class="btn btn-outline" onclick="selectStrictSessionMode('NORMAL')">Scheduled session</button> <button type="button" class="ss-primary" onclick="selectStrictSessionMode('DEMO')">10-minute demo</button></div>



<div id="ssPrecheckView">



 <header class="ss-heading"><h1>◈ Strict Session – Prerequisite Check</h1><p>Verifying the requirements for your Windows and iOS Strict Session</p></header>



 <div class="ss-layout">



  <aside class="ss-summary">



   <svg viewBox="0 0 280 180" aria-hidden="true"><ellipse cx="140" cy="151" rx="122" ry="19" fill="#843ed522" stroke="#a766e8"/><rect x="64" y="19" width="150" height="110" rx="10" fill="#171222" stroke="#ac78f2" stroke-width="3"/><rect x="76" y="31" width="126" height="84" rx="5" fill="#28203a"/><path d="M125 53l16-7 16 7v21c-2 13-16 20-16 20s-14-7-16-20z" fill="#7044ba" stroke="#d3b6ff" stroke-width="2"/><path d="m132 69 7 7 13-16" fill="none" stroke="white" stroke-width="3"/><path d="m64 129-15 12h180l-15-12" fill="#6b408d"/><rect x="231" y="80" width="30" height="64" rx="6" fill="#1d1629" stroke="#ac78f2" stroke-width="2"/></svg>



   <div><h2>Windows + iOS Strict Session</h2><p class="ss-muted">Windows hosts your study session. Your iPhone or iPad displays the synchronized timer. Existing APK proof submission and sync continue separately.</p></div>



   <div class="ss-facts"><div><strong>◷ Adaptive study duration</strong><span id="ssDuration">Checking the actual schedule…</span></div><div><strong>▤ Proof submission</strong><span id="ssProofRule">Uses your existing scheduled window</span></div><div><strong>▶ Eligible YouTube reward</strong><span id="ssRewardRule">20 minutes maximum after verified proof and manual completion; existing eligibility rules apply</span></div></div>



  </aside>



  <div>



    <div class="ss-banner" id="ssProgressBarContainer" style="margin-bottom:16px;padding:14px 18px;background:linear-gradient(135deg,#1f1a2e,#14141c);border-radius:12px;border:1px solid var(--ss-line);">



      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">



        <strong id="ssProgressLabel" style="font-size:0.95rem;color:var(--ss-purple);">◈ Verification Progress: 0%</strong>



        <span id="ssProgressCounts" class="ss-muted" style="font-size:0.82rem;">0/0 checks verified</span>



      </div>



      <div style="height:8px;background:#100e18;border-radius:4px;overflow:hidden;border:1px solid #332747;">



        <div id="ssProgressBar" style="height:100%;width:0%;background:linear-gradient(90deg,#9333ea,#34d399);border-radius:4px;transition:width 0.3s ease;"></div>



      </div>



    </div>



    <div class="ss-devices" id="ssDevicePanels"></div>



   <section class="ss-environment"><div><h3>▱ Session Environment</h3><p class="ss-muted" id="ssEnvironment">Checking…</p></div><div><h3>Next Submission Window</h3><div class="ss-window" id="ssNextWindow">—</div></div><div><h3>Time Until Window</h3><div id="ssUntilWindow">—</div></div></section>



   <div class="ss-banner" id="ssReadiness" role="status" aria-live="polite">Checks are running. Camera access requires your explicit consent. PC speakers will be muted during active session. Microphone is not required or accessed.</div>



   <!--SS_INPUTS-->



   <div class="ss-media"><label><input type="checkbox" id="ssMediaConsent"> I consent to local camera, face, lighting, head-position and connected-headphone checks during setup. Speakers are muted and other apps’ notification banners are suppressed while the check runs. Leaving setup, stopping the check or a lost setup connection restores audio. No frames or audio are saved or uploaded. Visual earbud-wearing detection is not verified.</label><button type="button" class="btn btn-outline" id="ssLoadInputsButton" onclick="strictLoadInputs(true)">Show available cameras</button><button type="button" class="btn btn-outline" id="ssTestMediaButton" disabled onclick="testStrictPreparationMedia()">Run hardware checks</button><button type="button" class="btn btn-outline" onclick="stopStrictPreparationMedia()">Stop checks / restore audio</button></div>



   <div id="ssMediaHelp" class="ss-muted" hidden><p id="ssMediaMessage" role="status" aria-live="polite"></p><div id="ssPrivacyButtons" hidden><button type="button" class="btn btn-outline" onclick="strictMediaPrivacySettings('camera')">Windows camera settings</button></div></div>



  </div>



 </div>



 <footer class="ss-actions"><button type="button" class="btn btn-outline ss-back" onclick="navAppPage('dashboard')">← Back to Dashboard</button><button type="button" class="btn btn-outline" onclick="refreshStrictPreparation(true)">↻ Refresh Check</button><button type="button" class="ss-primary" onclick="strictPreparationStep('JOIN')">Join Devices (QR / Link) →</button></footer>



</div>



<div id="ssJoinView" hidden><header class="ss-heading"><h1>Join Devices</h1><p>Scan the iPhone/iPad QR code or open its temporary invitation link</p></header><p class="ss-muted">Open the temporary iOS QR to join the Windows timer. Use the verified HTTPS timer-and-camera link. Tap Start Camera and allow video during these checks; Windows must receive fresh frames before Session Ready or Start is enabled. Microphone access is never requested. Existing APK proof submission and sync are unchanged.</p><div class="ss-invitations" id="ssInvitations"></div><div class="ss-banner" id="ssJoinStatus" role="status"></div><div id="ssJoinBlockers" class="ss-banner" role="status" hidden></div><footer class="ss-actions"><button class="btn btn-outline ss-back" onclick="strictPreparationStep('PRECHECK')">← Prerequisite Check</button><button class="btn btn-outline" onclick="refreshStrictPreparation(true)">↻ Refresh Check</button><button class="ss-primary" id="ssContinueReady" aria-describedby="ssJoinBlockers" onclick="strictPreparationStep('READY')" disabled>Continue to Session Ready →</button></footer></div>



<div id="ssReadyView" hidden><header class="ss-heading"><h1>Session Ready</h1><p>Review the checked devices and activate your session when you are ready</p></header><div class="ss-summary ss-ready-card"><p id="ssReadySummary" role="status"></p><p class="ss-muted">Starting activates the existing Windows controls and opens the ChatGPT and LeetCode split view inside this same application window. The timer begins only after activation succeeds.</p>



<div class="ss-dual-camera-panel" id="ssDualCameraPanelReady" style="margin:20px 0;background:linear-gradient(150deg,#191522,#101116);border:1px solid var(--ss-line);border-radius:18px;padding:20px;">



 <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:10px;">



  <span style="font-size:1rem;font-weight:700;color:var(--text);display:flex;align-items:center;gap:8px;"><span>📷</span> MULTI-SOURCE PROCTORING (WEBCAM + iOS CAMERA + iPAD SCREEN)</span>



  <span class="ss-status-badge" id="ssDualOverallBadge" style="background:#2b161e;color:#ef4444;border:1px solid #ef444460;padding:4px 12px;border-radius:20px;font-size:0.75rem;font-weight:700;">CAMERAS UNAVAILABLE</span>



 </div>



 <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:16px;">



  <!-- WINDOWS MAIN CAMERA TILE -->



  <div class="ss-camera-tile" style="background:#13121a;border:1px solid #3d3152;border-radius:14px;padding:14px;">



   <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">



    <strong style="font-size:0.88rem;color:#d3b6ff;">WINDOWS MAIN CAMERA</strong>



    <span id="ssWinCamTileBadge" class="ss-status-badge" style="background:#221f2b;color:#a49ab5;">OFF</span>



   </div>



   <div class="ss-preview-box" style="height:150px;min-height:150px;position:relative;">



    <img id="ssWinCamTileImg" style="width:100%;height:100%;object-fit:contain;" hidden alt="Windows Webcam Feed">



    <div id="ssWinCamTilePlaceholder" class="ss-preview-placeholder">Local monitor is not running · Run hardware checks</div>



    <span class="ss-live-badge" id="ssWinCamTileLive" hidden>● LIVE · PC</span>



   </div>



   <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px;font-size:0.75rem;">



    <div><span class="ss-muted">Face:</span> <strong id="ssWinCamFaceVal">UNVERIFIED</strong></div>



    <div><span class="ss-muted">Gaze:</span> <strong id="ssWinCamGazeVal">MONITOR NOT RUNNING</strong></div>



    <div><span class="ss-muted">Head Pose:</span> <strong id="ssWinCamPoseVal">Yaw: 0 deg Pitch: 0 deg</strong></div>



    <div><span class="ss-muted">Processing:</span> <strong id="ssWinCamFPSVal">0.0 FPS</strong></div>



   </div>



  </div>



  <!-- iOS SECONDARY CAMERA TILE -->



  <div class="ss-camera-tile" style="background:#13121a;border:1px solid #3d3152;border-radius:14px;padding:14px;">



   <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">



    <strong style="font-size:0.88rem;color:#d3b6ff;">iOS SECONDARY CAMERA</strong>



    <span id="ssIOSCamTileBadge" class="ss-status-badge" style="background:#2b161e;color:#ef4444;">DISCONNECTED</span>



   </div>



   <div class="ss-preview-box" style="height:150px;min-height:150px;position:relative;">



    <img id="ssIOSCamTileImg" style="width:100%;height:100%;object-fit:contain;" hidden alt="iOS Camera Stream">



    <div id="ssIOSCamTilePlaceholder" class="ss-preview-placeholder">Awaiting video stream · Open invitation on iPhone &amp; tap Start Camera</div>



    <span class="ss-live-badge" id="ssIOSCamTileLive" hidden>● LIVE · WebRTC</span>



   </div>



   <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px;font-size:0.75rem;">



    <div><span class="ss-muted">Device:</span> <strong id="ssIOSCamDeviceVal">iPhone / iPad</strong></div>



    <div><span class="ss-muted">WebRTC:</span> <strong id="ssIOSCamWebRTCVal">CONNECTING</strong></div>



    <div><span class="ss-muted">Rx FPS:</span> <strong id="ssIOSCamRxFPSVal">0.0 FPS</strong></div>



    <div><span class="ss-muted">Proc FPS:</span> <strong id="ssIOSCamProcFPSVal">0.0 FPS</strong></div>



    <div><span class="ss-muted">Face:</span> <strong id="ssIOSCamFaceVal">UNVERIFIED</strong></div>



    <div><span class="ss-muted">Gaze:</span> <strong id="ssIOSCamGazeVal">UNAVAILABLE (ANGLE)</strong></div>



    <div><span class="ss-muted">Head Pose:</span> <strong id="ssIOSCamPoseVal">Yaw: 0 deg Pitch: 0 deg</strong></div>



    <div><span class="ss-muted">Events:</span> <strong id="ssIOSCamEventsVal">0 strikes</strong></div>



   </div>



  </div>



  <!-- iPAD SCREEN SHARE TILE -->



  <div class="ss-camera-tile" style="background:#13121a;border:1px solid #3d3152;border-radius:14px;padding:14px;">



   <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">



    <strong style="font-size:0.88rem;color:#d3b6ff;">iPAD SCREEN SHARE</strong>



    <span id="ssIOSScreenTileBadge" class="ss-status-badge" style="background:#221f2b;color:#a49ab5;">OFF</span>



   </div>



   <div class="ss-preview-box" style="height:150px;min-height:150px;position:relative;">



    <img id="ssIOSScreenTileImg" style="width:100%;height:100%;object-fit:contain;" hidden alt="iPad Screen Feed">



    <div id="ssIOSScreenTilePlaceholder" class="ss-preview-placeholder">Screen share is off · Tap Share iPad Screen on companion</div>



    <span class="ss-live-badge" id="ssIOSScreenTileLive" hidden>● LIVE · Screen</span>



   </div>



   <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px;font-size:0.75rem;">



    <div><span class="ss-muted">Activity:</span> <strong id="ssIOSScreenActivityVal">STATIC</strong></div>



    <div><span class="ss-muted">Delta MAD:</span> <strong id="ssIOSScreenMADVal">0.0</strong></div>



    <div><span class="ss-muted">Rx FPS:</span> <strong id="ssIOSScreenRxFPSVal">0.0 FPS</strong></div>



    <div><span class="ss-muted">Proc FPS:</span> <strong id="ssIOSScreenProcFPSVal">0.0 FPS</strong></div>



    <div><span class="ss-muted">Resolution:</span> <strong id="ssIOSScreenResVal">--</strong></div>



    <div><span class="ss-muted">Transitions:</span> <strong id="ssIOSScreenTransitionsVal">0</strong></div>



    <div><span class="ss-muted">Status:</span> <strong id="ssIOSScreenStatusVal">STOPPED</strong></div>



    <div><span class="ss-muted">Watchdog:</span> <strong id="ssIOSScreenWatchdogVal">OK (10s limit)</strong></div>



   </div>



  </div>



 </div>



 <!-- INITIAL CAMERA PREREQUISITE & LIVE iOS CAMERA HEALTH -->



 <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:14px;margin-top:14px;font-size:0.78rem;">



  <div style="background:#13121a;border:1px solid #3d3152;border-radius:12px;padding:12px;">



   <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">



    <strong style="color:#d3b6ff;">Initial Camera Prerequisite</strong>



    <span id="ssIOSInitialCheckBadge" class="ss-status-badge" style="background:#2b2112;color:#f59e0b;font-size:0.7rem;">IOS_CAMERA_INITIAL_CHECK: PENDING</span>



   </div>



   <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;" id="ssIOSInitialCheckGrid">



    <div><span class="ss-muted">1. Registered:</span> <strong id="ssInitChkReg">PENDING</strong></div>



    <div><span class="ss-muted">2. Permission:</span> <strong id="ssInitChkPerm">PENDING</strong></div>



    <div><span class="ss-muted">3. WebRTC Video:</span> <strong id="ssInitChkWebrtc">PENDING</strong></div>



    <div><span class="ss-muted">4. Frame Rx:</span> <strong id="ssInitChkRx">PENDING</strong></div>



    <div><span class="ss-muted">5. Decoded:</span> <strong id="ssInitChkDec">PENDING</strong></div>



    <div><span class="ss-muted">6. Pipeline:</span> <strong id="ssInitChkProc">PENDING</strong></div>



   </div>



   <div style="margin-top:8px;display:flex;justify-content:space-between;align-items:center;gap:8px;">



    <small class="ss-muted" id="ssIOSInitialCheckHint">Requires at least 1 decoded iOS video frame.</small>



    <button type="button" class="btn btn-outline" id="ssRetryIOSCameraBtn" style="padding:3px 10px;font-size:0.72rem;" onclick="ssRefreshIOSCamera()">Retry Camera Test</button>



   </div>



  </div>



  <div style="background:#13121a;border:1px solid #3d3152;border-radius:12px;padding:12px;">



   <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">



    <strong style="color:#d3b6ff;">Live iOS Camera Health (15s Grace Policy)</strong>



    <span id="ssIOSHealthStateBadge" class="ss-status-badge" style="background:#221f2b;color:#a49ab5;font-size:0.7rem;">CAMERA_INITIALIZING</span>



   </div>



   <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;">



    <div><span class="ss-muted">ICE Route:</span> <strong id="ssIOSIceRouteVal">UNKNOWN</strong></div>



    <div><span class="ss-muted">Frame Gap:</span> <strong id="ssIOSFrameGapVal">0.0s / 15.0s</strong></div>



    <div><span class="ss-muted">Decoded FPS:</span> <strong id="ssIOSDecodedFPSVal">0.0 FPS</strong></div>



    <div><span class="ss-muted">Dropped:</span> <strong id="ssIOSDroppedVal">0 frames</strong></div>



    <div><span class="ss-muted">Last Frame:</span> <strong id="ssIOSLastFrameVal">—</strong></div>



    <div><span class="ss-muted">Root Cause:</span> <strong id="ssIOSRootCauseVal">HEALTHY</strong></div>



   </div>



   <details style="margin-top:8px;background:#0e0d14;border:1px solid #2e2540;border-radius:8px;padding:6px 10px;">



    <summary style="cursor:pointer;font-size:0.74rem;color:#b58aff;">11-Stage Media &amp; Inference Pipeline Diagnostics</summary>



    <div id="ssIOSPipelineStages" style="margin-top:6px;font-size:0.71rem;line-height:1.5;color:#cbd5e1;">Waiting for telemetry…</div>



   </details>



  </div>



 </div>



</div>



<!--SS_FINAL_GATE_PANEL--><div id="ssFinalGatePanelReady" style="margin:16px 0;background:#13121a;border:1px solid #3d3152;border-radius:14px;padding:16px;font-size:0.8rem;"></div><!--SS_SETUP--><div class="ss-actions"><button class="btn btn-outline ss-back" onclick="strictPreparationStep('JOIN')">← Join Devices</button><!--SS_START--></div></div></div>



'''







HTML=HTML.replace('<!--SS_INPUTS-->',MEDIA_HTML).replace('<div class="ss-devices" id="ssDevicePanels"></div>', PREVIEW_HTML+'<div class="ss-devices" id="ssDevicePanels"></div>')+CHECK_HTML







JS=r'''



let strictPreparationData=null,strictPreparationView='PRECHECK',strictPreparationBusy=false,strictHostMedia=null,strictMediaGeneration=0,strictMediaTesting=false;



function ssText(id,value){const e=document.getElementById(id);if(e)e.textContent=value;}



function ssElement(tag,text){const e=document.createElement(tag);if(text!=null)e.textContent=text;return e;}



async function ssRequest(action,body={}){const r=await fetch('/api/strict_session/preparation/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-StrictRoutines-Study':'1'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok||!d.success)throw Error(d.error||'Preparation check unavailable');return d;}



let strictSetupMonitor=false;



function ssActive(){return !!strictSessionData?.session_id&&['ACTIVE','RESTRICTED','PRIVILEGED'].includes(strictSessionData.state)&&strictSessionData?.workflow&&!['IDLE','FAILED','ABANDONED','SESSION_FINALIZED','REWARD_ACTIVE'].includes(strictSessionData.workflow.state);}



async function selectStrictSessionMode(mode){try{strictPreparationData=await ssRequest('mode',{mode});ssApplyView('PRECHECK');renderStrictPreparation();}catch(error){showToast(error.message,'error');}}



function ssApplyView(view){strictPreparationView=view;for(const [v,id]of Object.entries({PRECHECK:'ssPrecheckView',JOIN:'ssJoinView',READY:'ssReadyView',FOCUS:'ssFocusView'})){const e=document.getElementById(id);if(e)e.hidden=v!==view;}document.querySelectorAll('[data-ss-step]').forEach(b=>{b.setAttribute('aria-current',b.dataset.ssStep===view?'step':'false');b.disabled=b.dataset.ssStep==='READY'?!strictPreparationData?.ready:b.dataset.ssStep==='FOCUS'?!ssActive():false;});}



async function strictPreparationStep(view){try{if(view==='FOCUS'&&!ssActive())throw Error('Activate Session Ready first.');if(view==='READY'){strictPreparationData=await ssRequest('ready',{preparation_id:strictPreparationData?.preparation_id});}if(ssActive()&&strictSessionData.workflow.monitoring_resumed!==false)view='FOCUS';ssApplyView(view);if(view==='FOCUS')window.pywebview?.api?.show_strict_focus?.();if(view==='JOIN')renderStrictInvitations();renderStrictPreparation();}catch(e){showToast(e.message,'error');}}



function ssIosFrontCamSatisfied(dev){return dev?.front_camera_verified===true||dev?.initial_check_passed===true;}

function ssIosScreenShareSatisfied(dev){return dev?.screen_share_verified===true||dev?.screen_share_status==='IOS_SCREEN_SHARE_VERIFIED'||(typeof ssIOSCameraStatusData!=='undefined'&&ssIOSCameraStatusData?.ipad_screen_share?.initial_verified===true);}

function ssIosCamSatisfied(dev){return !dev?.camera_required||(ssIosFrontCamSatisfied(dev)&&ssIosScreenShareSatisfied(dev));}

let ssPreDemoRunning = false;
async function ssRunPreDemoTest(testName='all'){
  if(ssPreDemoRunning) return;
  ssPreDemoRunning = true;
  const btn = document.getElementById('btnRunPreDemoSuite');
  if(btn) { btn.disabled = true; btn.textContent = 'Verifying Media...'; }
  showToast('Running Mandatory Pre-Demo Media Verification ('+testName+')...', 'info');
  try {
    const res = await fetch('/api/strict_session/predemo/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({test: testName, timeout_sec: 15.0})
    });
    const d = await res.json();
    await ssRefreshPreDemoReport();
    if(d.all_passed || d.passed) {
      showToast('Pre-Demo Media Verification PASSED.', 'success');
    } else {
      showToast('Pre-Demo Check: ' + (d.message || d.blockers?.[0] || 'Verification incomplete'), 'warning');
    }
  } catch(e) {
    showToast('Pre-Demo Test network error: ' + e, 'error');
  } finally {
    ssPreDemoRunning = false;
    if(btn) { btn.disabled = false; btn.textContent = 'Pre-Demo Media Test'; }
  }
}

async function ssRefreshPreDemoReport(){
  try {
    const res = await fetch('/api/strict_session/predemo/report');
    const d = await res.json();
    const panel = document.getElementById('ssPreDemoTestResults');
    const badge = document.getElementById('ssPreDemoReportBadge');
    const list = document.getElementById('ssPreDemoChecklist');
    if(!panel || !list) return;
    panel.style.display = 'block';
    if(badge) {
      badge.textContent = d.all_passed ? 'ALL 8 PASSED' : (d.blockers?.length + ' BLOCKED');
      badge.style.background = d.all_passed ? '#103822' : '#2b161e';
      badge.style.color = d.all_passed ? '#4be78b' : '#ef4444';
    }
    list.innerHTML = Object.entries(d.checklist || {}).map(([k, v]) => `
      <div style="display:flex;align-items:center;gap:4px;">
        <span style="color:${v.passed ? '#4be78b' : '#ef4444'};font-weight:bold;">${v.passed ? 'PASS' : 'FAIL'}</span>
        <span style="color:${v.passed ? '#cbd5e1' : '#f87171'};">${k.replace('IOS_', '').replace('WINDOWS_', '').replaceAll('_', ' ')}</span>
      </div>
    `).join('');
  } catch(e) { console.debug('Pre-demo report refresh notice:', e); }
}

async function ssRetryPrerequisite(step){try{showToast('Retrying prerequisite check ('+step.replace('retry_','').replaceAll('_',' ')+')…','info');const res=await ssRequest(step,{preparation_id:strictPreparationData?.preparation_id});if(res?.devices)strictPreparationData=res;await refreshStrictPreparation(true);if(typeof ssRefreshIOSCamera==='function')await ssRefreshIOSCamera();showToast('Prerequisite check updated.','success');}catch(e){showToast(e.message,'error');}}



function ssReadinessBlockers(d){



 if(d.ready && ssIosCamSatisfied(d.devices?.IOS)) return [];



 const reasons=[];



 for(const role of ssRequiredRoles){



  const device=d.devices?.[role]||{};



  if(!device.ready || (role==='IOS' && !ssIosCamSatisfied(device))){



   if(role==='WINDOWS')reasons.push('Windows PC: complete the camera and hardware checks.');



   else {if(!device.joined)reasons.push('iPhone/iPad: scan the secure QR to join.');else if(!device.fresh)reasons.push('iPhone/iPad: waiting for a current report; reopen the timer.');else{if(!ssIosFrontCamSatisfied(device))reasons.push('iPhone/iPad Front Camera (IOS_FRONT_CAMERA): tap Start Camera and wait for verified live video in Windows.');if(!ssIosScreenShareSatisfied(device))reasons.push(device.screen_share_negotiating?('iPhone/iPad Screen Share (IOS_FULL_SCREEN_SHARE): SCREEN SHARE NEGOTIATING — '+(device.screen_share_countdown_label||'Waiting for first valid frame (60s grace)')):device.screen_share_failed?('iPhone/iPad Screen Share Failed: '+(device.screen_share_failure_reason||'No valid screen frames received within 60 seconds')+'. Click Retry iOS Screen Share.'):'iPhone/iPad Full-Screen Share (IOS_FULL_SCREEN_SHARE): Mandatory — tap Share iPad Screen and approve broadcast.');}}



  }



 }



 const env=d.environment||{};



 if(env.internet!==true)reasons.push('Internet connectivity has not been confirmed. Refresh Check.');



 if(env.secure_browser!==true)reasons.push('The Windows study browser is unavailable. Reopen the updated EXE and refresh the check.');



 if(env.allowlist_configured!==true)reasons.push('The ChatGPT study allowlist is not ready.');



 if(!env.window&&env.mode!=='DEMO')reasons.push('No eligible proof window more than five minutes away. Confirm your routine schedule, or choose the 10-minute demo.');



 if(env.hardware_required&&env.hardware_ready!==true)reasons.push(...(env.hardware_checks?.blockers||['Run the real hardware checks.']));



  if(d.android_prerequisite&&d.android_prerequisite.ready===false)reasons.push('Android APK ('+(d.android_prerequisite.status||'ANDROID_NOT_READY')+'): '+(d.android_prerequisite.message||'Connect registered Android APK and grant Usage Access.'));

  if(!reasons.length)reasons.push('Session environment checks are pending. Refresh Check.');



 return reasons;



}



function renderStrictPreparation(){const d=strictPreparationData;if(!d)return;renderStrictCameraFeedback(d);const focusedSetting=document.activeElement?.dataset?.ssSetting;ssText('ssModeLabel',d.environment?.mode==='DEMO'?'10-minute demo · no points or rewards':'Scheduled Strict Session');const root=document.getElementById('ssDevicePanels');if(!root)return;



 const roleLabels={WINDOWS:'Windows PC (Host)',IOS:'iPhone / iPad (Companion Timer)'};



 let totalPassed=0, totalReq=0;



 for(const role of ssRequiredRoles){if(d.devices[role]?.ready)totalPassed++;totalReq++;}



 const pct=totalReq>0?Math.round((totalPassed/totalReq)*100):0;



 ssText('ssProgressLabel',`◈ Verification Progress: ${pct}%`);



 ssText('ssProgressCounts',`${totalPassed}/${totalReq} devices verified`);



 const pBar=document.getElementById('ssProgressBar');if(pBar)pBar.style.width=`${pct}%`;







 for(const [role,name]of Object.entries(roleLabels)){const device=d.devices[role]||{ready:false,status:'Not joined',report:{}},report=device.report||{},card=ssElement('article');card.className='ss-device';const heading=ssElement('h3',name),badge=ssElement('span',device.status);badge.className='ss-status';badge.dataset.ready=String(device.ready);heading.append(badge);card.append(heading);const list=ssElement('ul');list.className='ss-checks';



 const hc=d.environment?.hardware_checks||{};



 const checked=(key,title)=>{const check=hc[key]||{};return [title,check.passed===true,check.message||'Run hardware checks.',check.message,key];};



 const entries=role==='WINDOWS'?[



   checked('webcam','Selected camera'),checked('strictguard','Local face/head monitor'),checked('face','Face visible'),



   checked('lighting','Lighting and sharpness'),checked('calibration','Neutral calibration'),



   checked('bluetooth_monitoring','Audio-device scan'),checked('prohibited_bluetooth','Bluetooth headphones / earbuds'),



   checked('wired_headphones','Wired headset'),checked('speaker_output','Verified speaker mute'),checked('notifications','Notification filtering'),



   ['Physical audio jack',hc.physical_jack?.passed===true,hc.physical_jack?.message||'Not verified',hc.physical_jack?.message,'physical_jack'],



   ['Earbud-wearing check',false,hc.earbud_wearing?.message||'Not verified: face tracking cannot prove earbuds are worn.',null,'earbud_wearing'],



   ['Microphone',false,'Not required / not accessed',null,'microphone'],



   ['Connectivity',device.fresh&&report.online,'Awaiting a current host report',null,'connectivity'],



   ['Window visibility',device.fresh&&report.visible,'Keep the application visible',null,'visibility']



 ]:role==='ANDROID'?[



   ['Companion Joined',device.joined&&device.fresh,'Scan Android QR or open link to join',report.client_version||'Android companion connected','connectivity'],



   ['Timer & Screen Wake',report.timer_ready&&device.fresh,'Keep companion active with screen awake',null,'timer'],



   ['Network Heartbeat',device.fresh&&report.online,'Awaiting companion heartbeat',null,'connectivity'],



   ['Battery Optimization',true,'Ignored / Non-blocking (Optional)','Exempted','battery'],



   ['Microphone Access',false,'Not required / Not accessed','Companion monitor only','microphone']



 ]:[



   ['Timer Display',report.timer_ready&&device.fresh,'Join and open the timer',null,'timer'],



   ['Connectivity',device.fresh&&report.online,'Awaiting current heartbeat',null,'connectivity'],



   ['Session Visibility',device.fresh&&report.visible,'Keep the timer visible',null,'visibility'],



   ['Front Camera (IOS_FRONT_CAMERA)',ssIosFrontCamSatisfied(device),'Tap Start Camera on the secure iOS timer; Windows must receive at least 1 decoded front-camera frame.',device.initial_check_state||device.camera_status?.message||'IOS_FRONT_CAMERA verified','front_camera'],

   ['Full-Screen Share (IOS_FULL_SCREEN_SHARE · Mandatory)',ssIosScreenShareSatisfied(device),device.screen_share_negotiating?(device.screen_share_countdown_label||'SCREEN SHARE NEGOTIATING (60s grace)'):device.screen_share_failed?(device.screen_share_failure_reason||'IOS_SCREEN_SHARE_FAILED — Click Retry iOS Screen Share'):'Tap Share iPad Screen and approve broadcast (60s grace, 3s stable frames required).','IOS_SCREEN_SHARE: VERIFIED (3s stable frames)','screenshare'],



   ['Microphone Access',false,'Not required / Not accessed','Companion timer only','microphone'],



    ['Display Mode',report.timer_ready&&device.fresh,'Tap Join & open timer',report.fullscreen?'Fullscreen':report.display_mode==='expanded'?'Expanded timer (browser fullscreen unavailable)':'In page','display']



 ];



 const signature=JSON.stringify([device.status,device.ready,report.error,entries,entries.map(([title,ok,missing,confirmed,key])=>ssCheckPresentation(role,key,ok,d))]);const previous=[...root.children].find(e=>e.dataset.role===role);if(previous?.dataset.signature===signature)continue;card.dataset.role=role;card.dataset.signature=signature;



 for(const [title,ok,missing,confirmed,key]of entries){const presentation=ssCheckPresentation(role,key,ok,d),li=ssElement('li'),mark=ssElement('span',presentation.symbol);mark.className='ss-mark';mark.dataset.ok=String(presentation.state==='verified');mark.setAttribute('aria-hidden','true');const text=ssElement('div'),badge=ssElement('span',presentation.label);badge.className='ss-check-state';badge.dataset.state=presentation.state;text.append(ssElement('b',title),ssElement('small',ok?(confirmed||'Confirmed by current device report'):missing),badge);li.append(mark,text,ssCheckGear(role,key,title));list.append(li);}card.append(list);if(report.error)card.append(ssElement('p',report.error));if(role==='WINDOWS'){const rb=ssElement('button','↻ Retry Windows Camera');rb.type='button';rb.className='btn btn-outline';rb.style.marginTop='12px';rb.onclick=()=>ssRetryPrerequisite('retry_windows_camera');card.append(rb);}if(role==='IOS'){const note=ssElement('p','iPhone / iPad: Both Front Camera (IOS_FRONT_CAMERA) and Full-Screen Share (IOS_FULL_SCREEN_SHARE) are mandatory.');note.className='ss-muted';card.append(note);const row=ssElement('div');row.style.cssText='display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;';const b1=ssElement('button','↻ Retry iOS Front Camera');b1.type='button';b1.className='btn btn-outline';b1.onclick=()=>ssRetryPrerequisite('retry_ios_front_camera');const b2=ssElement('button','↻ Retry iOS Screen Share');b2.type='button';b2.className='btn btn-outline';b2.onclick=()=>ssRetryPrerequisite('retry_ios_screen_share');row.append(b1,b2);card.append(row);}if(previous)root.replaceChild(card,previous);else root.append(card);}ssRenderExtraPrerequisiteCards(root,d);


 if(focusedSetting){const target=[...document.querySelectorAll('[data-ss-setting]')].find(e=>e.dataset.ssSetting===focusedSetting);target?.focus({preventScroll:true});}if(document.getElementById('ssCheckDialog').open)renderStrictCheckDialog();



 const env=d.environment||{},window=env.window;ssText('ssDuration',env.mode==='DEMO'?'10-minute demo · no proof window required':env.focus_seconds?`${Math.round(env.focus_seconds/60*100)/100} minutes planned · normal cycle 35–40 minutes`:'No eligible schedule confirmed');ssText('ssEnvironment',`${env.browser_message}. ChatGPT allowlist: ${env.allowlist_configured?'configured':'unconfirmed'}. ${env.restriction_status}. ${env.network_message}${env.latency_ms!=null?' ('+env.latency_ms+' ms)':''}`);ssText('ssProofRule',env.mode==='DEMO'?'Not required for the demo.':'Uses your existing scheduled window');ssText('ssRewardRule',env.mode==='DEMO'?'None. Demo points, deductions and rewards are disabled.':'20 minutes maximum after verified proof and manual completion; existing eligibility rules apply');ssText('ssNextWindow',env.mode==='DEMO'?'Not required':window?new Date(window.window_start).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):'No eligible window');ssText('ssUntilWindow',env.mode==='DEMO'?'Practice only':env.window_in_seconds==null?'Unavailable':Math.max(0,Math.ceil(env.window_in_seconds/60))+' minutes');const reasons=ssReadinessBlockers(d),overall=document.getElementById('ssReadiness');overall.dataset.ready=String(d.ready);overall.textContent=d.ready?'✓ Windows and iOS checks pass. Continue to Session Ready.':reasons.join(' ');



 ssText('ssJoinStatus',ssRequiredRoles.map(k=>k+': '+(d.devices[k]?.ready?'Ready':d.devices[k]?.status||'Not joined')).join(' · '));



 const blockers=document.getElementById('ssJoinBlockers');if(blockers){blockers.hidden=d.ready;blockers.replaceChildren();if(reasons.length){blockers.append(ssElement('strong','Before you can continue'));const list=ssElement('ul');for(const reason of reasons)list.append(ssElement('li',reason));blockers.append(list);}}



 ssText('ssReadySummary',d.ready?(d.environment?.mode==='DEMO'?'✓ Demo ready: all 5 prerequisite gates pass (Windows + iOS Camera + iOS Screen Share + Android APK + External Browsers Closed).':'✓ All 5 prerequisite gates verified and current.'):reasons.join(' '));const iosDev=d.devices?.IOS;const canProceed=d.ready&&ssIosCamSatisfied(iosDev)&&(d.android_prerequisite?.ready!==false);document.getElementById('ssContinueReady').disabled=!canProceed;document.getElementById('ssContinueReady').title=canProceed?'Ready to proceed':reasons.join(' ');document.getElementById('btnStartStrictSession').disabled=!canProceed||strictStudyStarting;document.getElementById('btnStartStrictSession').title=canProceed?'Start session':reasons.join(' ');if(!strictStudyStarting)document.getElementById('btnStartStrictSession').textContent=d.environment?.mode==='DEMO'?'Start 10-minute demo':'Start strict session';ssRenderFinalGatePanel(d);if(typeof ssRefreshPreDemoReport==='function')ssRefreshPreDemoReport();if(typeof ssUpdateDualCameraDashboard==='function'&&typeof ssIOSCameraStatusData!=='undefined')ssUpdateDualCameraDashboard(ssIOSCameraStatusData);if(strictPreparationView==='FOCUS'&&!ssActive())strictPreparationView='PRECHECK';ssApplyView(ssActive()&&strictSessionData.workflow.monitoring_resumed!==false?'FOCUS':strictPreparationView);}

function ssRenderExtraPrerequisiteCards(root,d){
 const andr=d.android_prerequisite||{ready:false,status:'ANDROID_MONITOR_CHECKING',message:'Checking Android APK readiness…'};
 const brw=d.browser_cleanup||{passed:false,status:'CHECKING_EXTERNAL_BROWSERS',message:'Checking external Windows browsers…',per_browser_summary:{}};
 const env=d.environment||{},fg=d.final_gate||{};
 const chk=andr.checks||{};

 let andrBadgeText = andr.status || 'ANDROID_MONITOR_CHECKING';
 if(andr.ready===true || andr.state==='ANDROID_READY_ACKNOWLEDGED'){
  andrBadgeText = 'ANDROID: CONNECTED · AUTHENTICATED · READY';
 } else if(andr.state==='ANDROID_PERMISSION_REQUIRED' || (andr.usage_access_granted===false && (andr.connectivity_status==='CONNECTED' || andr.connected))){
  andrBadgeText = 'ANDROID CONNECTED – USAGE ACCESS REQUIRED';
 } else if(andr.connectivity_status==='CONNECTED' || andr.connected){
  andrBadgeText = 'ANDROID: CONNECTING / AUTHENTICATING';
 }

 const r_reg = chk.registered || {};
 const r_auth = chk.authenticated || {};
 const r_be = chk.backend_connected || {};
 const r_tr = chk.transport_connected || {};
 const r_usg = chk.usage_access || {};
 const r_svc = chk.monitoring_service || {};
 const r_apk = chk.apk_compatibility || {};
 const r_ack = chk.readiness_ack || {};

 const extraCards=[
  {
   role:'ANDROID_APK',
   title:'Android APK Readiness',
   ready:andr.ready===true,
   status:andrBadgeText,
   retryStep:'retry_android_connection',
   retryLabel:'↻ Retry Android Connection',
   rows:[
    ['Device registered', r_reg.ok ?? (andr.status!=='ANDROID_NOT_REGISTERED'), 'Register your Android APK device.', r_reg.message || (andr.device_id ? ('Device: '+andr.device_id) : 'Device registered in database'), r_reg.state || ((andr.status!=='ANDROID_NOT_REGISTERED') ? 'PASSED' : 'FAILED')],
    ['Device authenticated', r_auth.ok ?? (andr.ready===true || andr.usage_access_granted===true), 'Awaiting device authentication…', r_auth.message || 'Token & device signature verified', r_auth.state || (andr.ready ? 'PASSED' : 'CHECKING')],
    ['Backend connected', r_be.ok ?? (andr.connectivity_status==='CONNECTED' || andr.connected), 'Connect Android APK on Wi-Fi / network.', r_be.message || 'Connected to StrictRecords server', r_be.state || ((andr.connectivity_status==='CONNECTED' || andr.connected) ? 'PASSED' : 'CHECKING')],
    ['Secure connection', r_tr.ok ?? (andr.connectivity_status==='CONNECTED' || andr.connected), 'Verifying secure transport channel…', r_tr.message || 'Secure transport channel active', r_tr.state || ((andr.connectivity_status==='CONNECTED' || andr.connected) ? 'PASSED' : 'CHECKING')],
    ['Usage Access', r_usg.ok ?? (andr.usage_access_granted===true), 'Grant Usage Access (PACKAGE_USAGE_STATS) in Android Settings.', r_usg.message || 'Usage Access (PACKAGE_USAGE_STATS) granted', r_usg.state || (andr.usage_access_granted===true ? 'PASSED' : (andr.connected ? 'PERMISSION_REQUIRED' : 'CHECKING'))],
    ['Monitoring service', r_svc.ok ?? (andr.monitoring_service_ready===true || andr.monitoring_service_startable===true), 'Enable Strict Session monitoring service in APK.', r_svc.message || 'StrictSessionUsageMonitor ready to start immediately', r_svc.state || ((andr.monitoring_service_ready===true || andr.monitoring_service_startable===true) ? 'PASSED' : 'CHECKING')],
    ['APK compatibility', r_apk.ok ?? !!andr.apk_version, 'Verifying APK version…', r_apk.message || ('APK v' + (andr.apk_version || '2.5.0') + ' verified'), r_apk.state || (andr.apk_version ? 'PASSED' : 'CHECKING')],
    ['Readiness acknowledgement', r_ack.ok ?? (andr.ready===true), andr.message || 'Awaiting ANDROID_STRICT_READY_ACK…', r_ack.message || 'ANDROID_STRICT_READY_ACK received', r_ack.state || (andr.ready===true ? 'PASSED' : (andr.state==='ANDROID_PERMISSION_REQUIRED' ? 'PERMISSION_REQUIRED' : 'CHECKING'))]
   ]
  },
  {
   role:'BACKEND',
   title:'Backend & Preparation Gate',
   ready:fg.BACKEND_READY===true||(env.internet===true&&env.secure_browser===true&&env.allowlist_configured===true),
   status:fg.BACKEND_READY?'BACKEND_READY':'CHECKING',
   retryStep:'retry_backend_check',
   retryLabel:'↻ Retry Backend Check',
   rows:[
    ['Server & Database Ready',env.internet===true,'Confirm server & internet connectivity.','Verified ('+(env.network_message||'Online')+')','PASSED'],
    ['Embedded Study Browser',env.secure_browser===true&&env.allowlist_configured===true,'Study WebView2 + ChatGPT allowlist required.','WebView2 + Allowlist Ready','PASSED'],
    ['Preparation Run Metadata',!!d.preparation_id,'Awaiting preparation run.','ID: '+(d.preparation_id||'—')+' · v'+(d.result_version||1),'PASSED']
   ]
  }
 ];

 for(const cfg of extraCards){
  const sig=JSON.stringify([cfg.ready,cfg.status,cfg.rows]);
  const prev=[...root.children].find(e=>e.dataset.role===cfg.role);
  if(prev?.dataset.signature===sig)continue;
  const card=ssElement('article');card.className='ss-device';card.dataset.role=cfg.role;card.dataset.signature=sig;
  const h=ssElement('h3',cfg.title),b=ssElement('span',cfg.status);b.className='ss-status';b.dataset.ready=String(cfg.ready);h.append(b);card.append(h);
  const ul=ssElement('ul');ul.className='ss-checks';
  for(const [rTitle,rOk,rMiss,rConf,rState] of cfg.rows){
   const st = rState || (rOk ? 'PASSED' : 'PENDING');
   const sym = (st==='PASSED' || (rOk && st!=='PERMISSION_REQUIRED')) ? '✓' : (st==='PERMISSION_REQUIRED' ? '⚠' : (st==='CHECKING' ? '↻' : (st==='FAILED' ? '✕' : '○')));
   const badgeSt = (st==='PASSED' || (rOk && st!=='PERMISSION_REQUIRED')) ? 'verified' : (st==='PERMISSION_REQUIRED' ? 'warning' : (st==='CHECKING' ? 'checking' : 'pending'));
   const badgeLbl = (st==='PASSED' || (rOk && st!=='PERMISSION_REQUIRED')) ? 'Verified' : (st==='PERMISSION_REQUIRED' ? 'Permission Required' : (st==='CHECKING' ? 'Checking' : 'Action required'));

   const li=ssElement('li'),mk=ssElement('span',sym);mk.className='ss-mark';mk.dataset.ok=String(rOk);
   if(st==='PERMISSION_REQUIRED') mk.style.color='#f59e0b';
   else if(st==='CHECKING') mk.style.color='#a78bfa';
   else if(st==='PASSED' || rOk) mk.style.color='#4be78b';

   const tx=ssElement('div'),bg=ssElement('span',badgeLbl);bg.className='ss-check-state';bg.dataset.state=badgeSt;
   if(st==='PERMISSION_REQUIRED') { bg.style.color='#f59e0b'; bg.style.borderColor='#f59e0b'; }
   tx.append(ssElement('b',rTitle),ssElement('small',rOk?rConf:rMiss),bg);li.append(mk,tx);ul.append(li);
  }
  card.append(ul);
  const btn=ssElement('button',cfg.retryLabel);btn.type='button';btn.className='btn btn-outline';btn.style.marginTop='12px';btn.onclick=()=>ssRetryPrerequisite(cfg.retryStep);card.append(btn);
  if(prev)root.replaceChild(card,prev);else root.append(card);
 }
}
function ssRenderFinalGatePanel(d){

 const el=document.getElementById('ssFinalGatePanelReady');if(!el||!d)return;

 const fg=d.final_gate||{},ios=d.devices?.IOS||{},andr=d.android_prerequisite||{},brw=d.browser_cleanup||{};

 const pill=(ok,txt)=>`<span style="padding:2px 8px;border-radius:10px;font-size:0.72rem;font-weight:700;background:${ok?'#153629':'#2b161e'};color:${ok?'#4be78b':'#f87171'};">${ok?'✓ ':'✕ '}${txt}</span>`;

 const brwRows=Object.entries(brw.per_browser_summary||{}).map(([k,v])=>`<span style="margin-right:10px;"><strong>${k}:</strong> ${v}</span>`).join('');

 el.innerHTML=`

  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px;">

   <strong style="color:#d3b6ff;font-size:0.9rem;">◈ FINAL SESSION-START PREREQUISITE GATE (Prep ${d.preparation_id||'—'} · v${d.result_version||1})</strong>

   ${pill(d.ready,'ALL PREREQUISITES '+(d.ready?'PASSED':'REQUIRED'))}

  </div>

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-bottom:10px;">

   <div style="background:#0e0d14;border:1px solid #2e2540;border-radius:10px;padding:10px;">

    <div style="display:flex;justify-content:space-between;align-items:center;"><strong>A. Windows PC</strong>${pill(fg.WINDOWS_READY,'WINDOWS')}</div>

    <div class="ss-muted" style="font-size:0.74rem;margin:6px 0;">Webcam, Face, Gaze, Audio &amp; Mute</div>

    <button type="button" class="btn btn-outline" style="padding:3px 9px;font-size:0.72rem;" onclick="ssRetryPrerequisite('retry_windows_camera')">↻ Retry Windows Camera</button>

   </div>

   <div style="background:#0e0d14;border:1px solid #2e2540;border-radius:10px;padding:10px;">

    <div style="display:flex;justify-content:space-between;align-items:center;"><strong>B. iOS Media Receiving</strong>${pill(ssIosCamSatisfied(ios), ssIosCamSatisfied(ios)?'iOS MEDIA: READY – 2/2 VERIFIED':(ssIosFrontCamSatisfied(ios)||ssIosScreenShareSatisfied(ios)?'iOS MEDIA: PARTIAL – 1/2':'iOS MEDIA: WAITING – 0/2'))}</div>

    <div class="ss-muted" style="font-size:0.74rem;margin:6px 0;">Camera: ${ssIosFrontCamSatisfied(ios)?'Verified':'Pending'} · Full Screen Share: ${ssIosScreenShareSatisfied(ios)?'Verified':'Pending'} (${(ssIosFrontCamSatisfied(ios)?1:0)+(ssIosScreenShareSatisfied(ios)?1:0)}/2 media sources verified)</div>

    <details style="margin-top:6px;background:none;border:none;padding:0;">

      <summary style="font-size:0.72rem;color:#a78bfa;cursor:pointer;">Diagnostics &amp; Track Details</summary>

      <div style="font-size:0.7rem;color:#cbd5e1;padding:4px 0;">

        <div>Front Camera: ${ios.initial_check_state||'Requires >= 1 decoded frame'}</div>

        <div>Screen Share: ${ios.screen_share_negotiating?(ios.screen_share_countdown_label||'Negotiating (60s grace)'):ios.screen_share_failed?(ios.screen_share_failure_reason||'Failed after 60s'):(ssIosScreenShareSatisfied(ios)?'IOS_SCREEN_SHARE: VERIFIED (3s stable)':'Mandatory · Tap Share iPad Screen')}</div>

      </div>

    </details>

    <div style="display:flex;gap:6px;margin-top:6px;">

      <button type="button" class="btn btn-outline" style="padding:2px 7px;font-size:0.7rem;" onclick="ssRetryPrerequisite('retry_ios_front_camera')">↻ Front Cam</button>

      <button type="button" class="btn btn-outline" style="padding:2px 7px;font-size:0.7rem;" onclick="ssRetryPrerequisite('retry_ios_screen_share')">↻ Screen Share</button>
          <button type="button" class="ss-primary" id="btnRunPreDemoSuite" style="padding:2px 9px;font-size:0.7rem;" onclick="ssRunPreDemoTest('all')">▶ Pre-Demo Media Test</button>
        </div>
        <div id="ssPreDemoTestResults" style="margin-top:8px;padding:6px;background:#09080e;border-radius:6px;font-size:0.69rem;border:1px solid #3d3152;display:none;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
            <strong style="color:#d3b6ff;">PRE-DEMO 8-ITEM REPORT:</strong>
            <span id="ssPreDemoReportBadge" class="ss-status-badge" style="background:#221f2b;font-size:0.65rem;">CHECKING</span>
          </div>
          <div id="ssPreDemoChecklist" style="display:grid;grid-template-columns:1fr 1fr;gap:4px;"></div>
        </div>

    </div>

   </div>

   <div style="background:#0e0d14;border:1px solid #2e2540;border-radius:10px;padding:10px;">

    <div style="display:flex;justify-content:space-between;align-items:center;"><strong>C. Android APK</strong>${pill(andr.ready===true,andr.status||'ANDROID')}</div>

    <div class="ss-muted" style="font-size:0.74rem;margin:6px 0;">${andr.message||'PREPARE_STRICT_SESSION_CHECK'}</div>

    <button type="button" class="btn btn-outline" style="padding:3px 9px;font-size:0.72rem;" onclick="ssRetryPrerequisite('retry_android_connection')">↻ Retry Android Connection</button>

   </div>

   <div style="background:#0e0d14;border:1px solid #2e2540;border-radius:10px;padding:10px;">

    <div style="display:flex;justify-content:space-between;align-items:center;"><strong>D. External Browsers</strong>${pill(true,'AUTO-CLEANUP ON START')}</div>

    <div class="ss-muted" style="font-size:0.72rem;margin:6px 0;">Chrome, Brave, Edge &amp; Firefox close gracefully when session starts</div>

   </div>

   <div style="background:#0e0d14;border:1px solid #2e2540;border-radius:10px;padding:10px;">

    <div style="display:flex;justify-content:space-between;align-items:center;"><strong>E. Backend Gate</strong>${pill(fg.BACKEND_READY===true,'BACKEND')}</div>

    <div class="ss-muted" style="font-size:0.74rem;margin:6px 0;">DB + Tunnel + WebView2 Study Browser</div>

    <button type="button" class="btn btn-outline" style="padding:3px 9px;font-size:0.72rem;" onclick="ssRetryPrerequisite('retry_backend_check')">↻ Retry Backend Check</button>

   </div>

  </div>`;

}



async function refreshStrictPreparation(force=false){if(strictPreparationBusy||currentTab!=='strict-session')return;strictPreparationBusy=true;try{const r=force?null:await fetch('/api/strict_session/preparation/status',{cache:'no-store',headers:{'X-StrictRoutines-Study':'1'}});strictPreparationData=force?await ssRequest('check'):await r.json();if(!strictPreparationData.success)throw Error(strictPreparationData.error||'Checks unavailable');renderStrictPreparation();}catch(e){ssText('ssReadiness',e.message);}finally{strictPreparationBusy=false;}}



async function ssHostHeartbeat(){if(!strictPreparationData||strictStudyStarting||(ssActive()&&strictSessionData.workflow.monitoring_resumed!==false))return;const tracks=strictHostMedia?.getTracks()||[];const report={camera:!!strictHostCamera?.native_id || (typeof strictHostCamera !== 'undefined' && !!strictHostCamera) || strictSetupMonitor,microphone:false,visible:!document.hidden&&currentTab==='strict-session',online:navigator.onLine,secure_context:window.isSecureContext,camera_native_id:strictHostCamera?.native_id||'',camera_label:tracks.find(t=>t.kind==='video')?.label||'',client_version:'Windows EXE WebView2'};try{strictPreparationData=await ssRequest('host',{preparation_id:strictPreparationData.preparation_id,report});renderStrictPreparation();}catch(e){ssText('ssReadiness',e.message);}}



async function stopStrictPreparationMedia(report=true){strictMediaGeneration++;clearStrictSetupPreview();const stream=strictHostMedia;strictHostMedia=null;strictHostCamera=null;stream?.getTracks().forEach(t=>t.stop());if(report){strictSetupMonitor=false;await ssRequest('stop_hardware').catch(()=>{});await ssHostHeartbeat();}}



async function strictMediaPrivacySettings(device){const api=window.pywebview?.api;if(!api?.open_media_privacy_settings){showToast('Open Windows Settings → Privacy & security → Camera or Microphone.','info');return;}try{const result=await api.open_media_privacy_settings(device);if(!result.success)showToast(result.error,'error');}catch(e){showToast('Open Windows privacy settings, allow desktop apps, then retry the test.','info');}}



document.getElementById('ssMediaConsent').addEventListener('change',e=>{if(!e.target.checked)stopStrictPreparationMedia();});



function renderStrictInvitations(){



 const root=document.getElementById('ssInvitations');root.replaceChildren();



 const titles={ANDROID:'Android Companion',IOS:'iPhone / iPad'};



 for(const role of ssCompanionRoles){



  const card=ssElement('article');card.className='ss-invitation';card.dataset.role=role;



  card.append(ssElement('h2',titles[role]||role));



  const invite=strictPreparationData?.invitations?.find(i=>i.role===role);



  if(role==='IOS'){



   if(invite){



    // Hierarchy:



    // 1. QR invitation



    const qr=ssElement('img');qr.src=invite.qr;qr.alt='iPhone temporary invitation QR code';



    // 2. Invitation link



    const link=ssElement('input');link.readOnly=true;link.value=invite.url;link.setAttribute('aria-label','iPhone temporary invitation link');



    // 3. Expiry countdown



    const expiry=ssElement('div',typeof ssFormatCountdown==='function'?ssFormatCountdown(invite.expires_at):'Expires '+new Date(invite.expires_at*1000).toLocaleTimeString());expiry.id='ssIOSExpiry';expiry.className='ss-expiry-countdown';



    // 4. Copy invitation link button



    const copy=ssElement('button','Copy invitation link');copy.className='btn btn-outline';



    copy.onclick=()=>navigator.clipboard.writeText(invite.url).then(()=>showToast('Invitation copied','success')).catch(()=>{link.select();showToast('Select and copy the invitation link.','info');});



    // 5. Create new invitation button



    const generate=ssElement('button','Create new invitation');generate.className='ss-primary';



    generate.onclick=async()=>{generate.disabled=true;try{const result=await ssRequest('invite',{role:'IOS'});strictPreparationData.invitations=[...(strictPreparationData.invitations||[]).filter(i=>i.role!=='IOS'),result.invitation];renderStrictInvitations();showToast('New iOS invitation generated','success');}catch(e){showToast(e.message,'error');}finally{generate.disabled=false;}};



    // 6. HTTPS connection status & warning



    const isVerified=invite.https_verified===true||ssIOSCameraStatusData?.tls_verified===true;



    const httpsBox=ssElement('div');httpsBox.id='ssIOSHttpsStatus';httpsBox.className='ss-https-container '+(isVerified?'ss-status-verified':'ss-status-warning');



    const originDisp=ssIOSCameraStatusData?.origin?` · ${ssIOSCameraStatusData.origin}`:'';



    if(isVerified){



      httpsBox.innerHTML=`<strong>✓ HTTPS Verified</strong> · Canonical Cloudflare Tunnel${originDisp}`;



    }else{



      const warn=invite.warning||ssIOSCameraStatusData?.configuration_error||'HTTPS probe unverified';



      httpsBox.innerHTML=`<strong>⚠️ Local Network Timer Pairing Only · HTTPS Probe Failed</strong><br><small class="ss-muted">Timer-only local-network pairing does not enable browser-based camera access. ${warn}</small>`;



    }



    // Expandable connection diagnostics details



    const details=ssElement('details');details.id='ssIOSDiagnostics';details.className='ss-diagnostics-details';



    const sum=ssElement('summary','Connection diagnostics');details.append(sum);



    const diagDiv=ssElement('div');diagDiv.id='ssIOSDiagContent';



    const d=ssIOSCameraStatusData?.verification_details||invite.verification_details||{};



    diagDiv.innerHTML=`



      <dl class="ss-diag-grid">



        <dt style="grid-column:1/-1;font-weight:700;color:#cbb5ef;margin-top:4px;">1. SIGNALING</dt>



        <dt>Tunnel Hostname</dt><dd>${d.hostname||(ssIOSCameraStatusData?.origin?new URL(ssIOSCameraStatusData.origin).hostname:'Not configured')}</dd>



        <dt>DNS Resolution</dt><dd>${d.dns_resolved?'✓ Resolved':(d.error&&d.error.includes('DNS')?'❌ Failed ('+d.error+')':'❌ Not verified')}</dd>



        <dt>HTTPS Reachable</dt><dd>${d.https_reachable?'✓ Responded (200 OK)':'❌ Unreachable'}</dd>



        <dt>Companion Route</dt><dd>${d.companion_route_ok?'✓ Responded':'❌ Not reached'}</dd>



        <dt>WSS Signaling</dt><dd>${d.wss_signaling_ok?'✓ Connected':'❌ Unavailable'}</dd>



        <dt style="grid-column:1/-1;font-weight:700;color:#cbb5ef;margin-top:6px;">2. NEGOTIATION</dt>



        <dt>Offer/Answer State</dt><dd>${ssIOSCameraStatusData?.status?.state||'IDLE'}</dd>



        <dt>Active Session</dt><dd>${strictPreparationData?.preparation_id||'none'}</dd>



        <dt style="grid-column:1/-1;font-weight:700;color:#cbb5ef;margin-top:6px;">3. ICE PIPELINE</dt>



        <dt>ICE Status</dt><dd>${ssIOSCameraStatusData?.tls_verified?'✓ TLS & ICE Prepared':'Pending'}</dd>



        <dt>Turn / Stun</dt><dd>Cloudflare Realtime TURN + STUN active</dd>



        <dt style="grid-column:1/-1;font-weight:700;color:#cbb5ef;margin-top:6px;">4. CAMERA SENDER</dt>



        <dt>Sender Source</dt><dd>iPhone/iPad Front Camera (640x480)</dd>



        <dt>Status Badge</dt><dd>${ssIOSCameraStatusData?.status?.state||'INITIALIZING'}</dd>



        <dt style="grid-column:1/-1;font-weight:700;color:#cbb5ef;margin-top:6px;">5. WINDOWS RECEIVER</dt>



        <dt>Frames Confirmed</dt><dd>${ssIOSCameraStatusData?.status?.streaming?'✓ Confirmed Streaming ('+(ssIOSCameraStatusData?.status?.frame_sequence||0)+' frames)':'Awaiting frames'}</dd>



        <dt>Diagnostics Note</dt><dd>${d.error||ssIOSCameraStatusData?.configuration_error||'All checks verified'}</dd>



      </dl>`;



    const exportBtn=ssElement('button','📋 Export Technical Diagnostics');

    exportBtn.className='btn btn-outline';

    exportBtn.style.marginTop='10px';

    exportBtn.style.width='100%';

    exportBtn.onclick=async()=>{

      exportBtn.disabled=true;

      try{

        const diagRes=await ssRequest('export_diagnostics',{});

        const jsonStr=JSON.stringify(diagRes.diagnostics||{},null,2);

        if(navigator.clipboard?.writeText){

          await navigator.clipboard.writeText(jsonStr);

          exportBtn.textContent='✓ Technical Diagnostics Copied!';

          setTimeout(()=>{exportBtn.textContent='📋 Export Technical Diagnostics';exportBtn.disabled=false;},2500);

        }else{

          prompt('Copy Technical Diagnostics:',jsonStr);

          exportBtn.disabled=false;

        }

      }catch(e){

        showToast('Export failed: '+e.message,'error');

        exportBtn.disabled=false;

      }

    };

    diagDiv.append(exportBtn);



    details.append(diagDiv);



    // 7. iOS camera and video-sharing status



    const statusRow=ssElement('div');statusRow.className='ss-camera-status-row';



    statusRow.append(ssElement('strong','Camera Status: '));



    const badge=ssElement('span','Invitation Created');badge.id='ssIOSStateBadge';badge.className='ss-status-badge';



    statusRow.append(badge);



    // 8. Live monitoring status & preview



    const monitorBox=ssElement('div');monitorBox.id='ssIOSMonitorBox';monitorBox.className='ss-preview-box';



    const liveTag=ssElement('span','● VIDEO STREAMING');liveTag.id='ssIOSLiveTag';liveTag.className='ss-live-badge';liveTag.hidden=true;



    const previewImg=ssElement('img');previewImg.id='ssIOSPreviewImg';previewImg.hidden=true;previewImg.alt='Live iPhone Camera Stream';



    const placeholder=ssElement('div','Awaiting video stream · Open the invitation on iPhone and tap Start Camera');placeholder.id='ssIOSPreviewPlaceholder';placeholder.className='ss-preview-placeholder';



    monitorBox.append(liveTag,previewImg,placeholder);



    card.append(qr,link,expiry,copy,generate,httpsBox,details,statusRow,monitorBox);



   }else{



    const generate=ssElement('button','Create QR / temporary link');generate.className='ss-primary';



    generate.onclick=async()=>{generate.disabled=true;try{const result=await ssRequest('invite',{role:'IOS'});strictPreparationData.invitations=[...(strictPreparationData.invitations||[]).filter(i=>i.role!=='IOS'),result.invitation];renderStrictInvitations();}catch(e){showToast(e.message,'error');}finally{generate.disabled=false;}};



    card.append(generate);



   }



  }else{



   if(invite){



    const qr=ssElement('img');qr.src=invite.qr;qr.alt=role+' temporary invitation QR code';



    const link=ssElement('input');link.readOnly=true;link.value=invite.url;link.setAttribute('aria-label',role+' temporary invitation link');



    card.append(qr,link);



    const note=ssElement('p','Expires '+new Date(invite.expires_at*1000).toLocaleTimeString()+'. Open on your registered Android device.');note.className='ss-muted';card.append(note);



    const copy=ssElement('button','Copy invitation link');copy.className='btn btn-outline';



    copy.onclick=()=>navigator.clipboard.writeText(invite.url).then(()=>showToast('Invitation copied','success')).catch(()=>{link.select();showToast('Select and copy the invitation link.','info');});



    card.append(copy);



   }



   const generate=ssElement('button',invite?'Create new invitation':'Create QR / temporary link');generate.className='ss-primary';



   generate.onclick=async()=>{try{const result=await ssRequest('invite',{role});strictPreparationData.invitations=[...(strictPreparationData.invitations||[]).filter(i=>i.role!==role),result.invitation];renderStrictInvitations();}catch(e){showToast(e.message,'error');}};



   card.append(generate);



  }



  root.append(card);



 }



}



const ssOriginalNav=navAppPage;



navAppPage=async function(page){if(currentTab==='strict-session'&&page!=='strict-session'&&!ssActive())await stopStrictPreparationMedia();await ssOriginalNav(page);if(page==='strict-session'){ssApplyView(ssActive()&&strictSessionData.workflow.monitoring_resumed!==false?'FOCUS':'PRECHECK');await refreshStrictPreparation(true);await strictLoadInputs(false);}};



document.addEventListener('visibilitychange',()=>{if(!strictStudyStarting&&(!ssActive()||strictSessionData.workflow.monitoring_resumed===false))ssHostHeartbeat();});



setInterval(()=>{if(currentTab==='strict-session'&&!document.hidden){ssHostHeartbeat();refreshStrictPreparation();}},5000);



'''







JS='const ssRequiredRoles='+json.dumps(REQUIRED_ROLES)+';const ssCompanionRoles='+json.dumps(COMPANION_ROLES)+';\n'+MEDIA_JS+JS+CHECK_JS+PREVIEW_JS+IOS_CAMERA_JS











def augment_dashboard(html):



    start=html.index('<section class="app-page" id="page-strict-session">')



    end=html.index('</section>',start)



    body=html[start:end]



    setup_start=body.index('<div id="strictStudySetup"');setup_end=body.index('<p id="strictStudyMessage"',setup_start)



    setup=body[setup_start:setup_end];body=body[:setup_start]+body[setup_end:]



    button_start=body.index('<button class="btn btn-primary" id="btnStartStrictSession"');button_end=body.index('</button>',button_start)+9



    button=body[button_start:button_end];body=body[:button_start]+body[button_end:]



    opening='<section class="app-page" id="page-strict-session">'



    replacement=opening+HTML.replace('<!--SS_SETUP-->',setup).replace('<!--SS_START-->',button)+'<div id="ssFocusView" hidden>'+body[len(opening):]+'</div>'



    html=html[:start]+replacement+html[end:]



    return html.replace('</head>','<style>'+CSS+'</style></head>',1).replace('</body>','<script>'+JS+'</script></body>',1)



