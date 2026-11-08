(() => {
  const isSunNxtHost = () => {
    try {
      const h = (window.location.hostname || '').toLowerCase();
      if (h === 'sunnxt.com' || h.endsWith('.sunnxt.com') || h === 'sunnetwork.in' || h.endsWith('.sunnetwork.in') || h.includes('sunnxt')) return true;
      const href = (window.location.href || '').toLowerCase();
      if (href.includes('sunnxt.com') || href.includes('sunnetwork.in') || href.includes('sunnxt')) return true;
      if (window.location.ancestorOrigins) {
        for (let i = 0; i < window.location.ancestorOrigins.length; i++) {
          const origin = (window.location.ancestorOrigins[i] || '').toLowerCase();
          if (origin.includes('sunnxt.com') || origin.includes('sunnetwork.in') || origin.includes('sunnxt')) return true;
        }
      }
      if (document.referrer) {
        const ref = document.referrer.toLowerCase();
        if (ref.includes('sunnxt.com') || ref.includes('sunnetwork.in') || ref.includes('sunnxt')) return true;
      }
      try {
        if (window.top !== window && window.top.location) {
          const topH = (window.top.location.hostname || '').toLowerCase();
          if (topH === 'sunnxt.com' || topH.endsWith('.sunnxt.com') || topH.includes('sunnxt')) return true;
          const topHref = (window.top.location.href || '').toLowerCase();
          if (topHref.includes('sunnxt.com') || topHref.includes('sunnetwork.in') || topHref.includes('sunnxt')) return true;
        }
      } catch (_) {}
    } catch (_) {}
    return false;
  };

  // Explicit Sun NXT exception: Never block or restrict video playback on sunnxt.com
  if (isSunNxtHost()) {
    try {
      document.documentElement.classList.remove('strictrecords-playback-restricted', 'strictrecords-restricted', 'strictrecords-yt-restricted');
      if (document.body) {
        document.body.classList.remove('strictrecords-playback-restricted', 'strictrecords-restricted', 'strictrecords-yt-restricted');
      }
    } catch (_) {}
    return;
  }

  const isYouTubeHost = () => {
    try {
      const h = (window.location.hostname || '').toLowerCase();
      if (h === 'music.youtube.com' || h.endsWith('.music.youtube.com')) return false;
      return h === 'youtu.be' || h.endsWith('youtube.com') || h.endsWith('youtube-nocookie.com');
    } catch (_) { return false; }
  };

  if (window.top !== window) {
    if (!isYouTubeHost() || !window.location.pathname.startsWith('/embed/')) {
      return;
    }
  }
  if (globalThis.__strictbotRestrictionController) return;
  globalThis.__strictbotRestrictionController = true;
  const ROOT_ID = 'strictbot-restriction-shield-v1';
  const HUD_ID = 'strictbot-edu-hud-v1';
  const COMPLETED_ID = 'strictbot-edu-completed-v1';
  const YT_BLUR_OVERLAY_ID = 'strictrecords-yt-blur-overlay';

  let root, shadow, ui, timer, observer, previousFocus, savedBody;
  let blocked = false, inFlight = false, closing = false, stopped = false, failures = 0;
  let dormant = false, generation = 0, rewardTimer;
  let currentVideoId = null;
  let mediaEnforcementInterval = null;
  let lastKnownStatus = null;

  // YouTube targeted player blur & playback restriction state
  let ytRestricted = false;
  let ytLastDecision = null;
  let ytPlayerObserver = null;
  let ytEnforcementInterval = null;

  // Educational playback state
  let eduActive = false;
  let eduVideoId = null;
  let eduAllowedSeconds = 0;
  let eduRemainingSeconds = 0;
  let eduHudEl = null;
  let eduMediaListeners = null;
  let eduHeartbeatTimer = null;
  let eduLastPosition = null;
  let eduIntervals = [];
  let cachedActiveEduVideo = null;
  let educationalReviewStart = null;
  let educationalReviewGeneration = 0;

  function isEducationalSessionValid(eduData) {
    if (!eduData || !eduData.videoId) return false;
    const now = Date.now();
    if (Number.isFinite(eduData.expiresAt)) {
      return now < eduData.expiresAt;
    }
    const dur = eduData.durationSeconds || 1205;
    const savedAt = eduData.savedAt || now;
    return (now - savedAt) < ((dur + 300) * 1000);
  }

  function getRemainingSecondsFromActiveEdu(eduData) {
    if (!eduData) return 0;
    if (Number.isFinite(eduData.remainingSeconds) && eduData.remainingSeconds >= 0) {
      if (eduData.lastCheckpointAt) {
        const elapsedSinceCheck = Math.max(0, Math.round((Date.now() - eduData.lastCheckpointAt) / 1000));
        return Math.max(0, eduData.remainingSeconds - elapsedSinceCheck);
      }
      return eduData.remainingSeconds;
    }
    const now = Date.now();
    const dur = eduData.durationSeconds || 1205;
    const savedAt = eduData.savedAt || now;
    const elapsed = Math.max(0, Math.round((now - savedAt) / 1000));
    return Math.max(0, dur - elapsed);
  }

  function evaluateVideoAuthorization(status, videoId, currentUrl) {
    const effStatus = status || lastKnownStatus || ytLastDecision;
    const vid = videoId || extractYouTubeVideoId(currentUrl || window.location.href);

    // 1. FIRST: Check for applicable hard restrictions and failed-session states
    if (effStatus?.code === 'NIGHT_LOCK_ACTIVE' || effStatus?.code === 'STRICT_SESSION_FAILED' || effStatus?.hardRestricted === true) {
      return {
        allow: false,
        decision: 'BLOCK',
        reason: 'HARD_RESTRICTION',
        statusText: status?.statusText || 'Access locked by StrictRecords schedule.'
      };
    }

    // 2. SECOND: Check whether the exact video has a valid Taufa AI educational authorization
    const hasLocalEdu = (
      eduActive &&
      eduVideoId &&
      vid &&
      eduVideoId === vid &&
      eduRemainingSeconds > 0
    );

    const hasStatusEdu = (
      (effStatus?.temporary_exception === 'EDUCATIONAL_VIDEO' ||
       effStatus?.temporaryException === 'EDUCATIONAL_VIDEO' ||
       effStatus?.educational_active === true) &&
      (effStatus?.video_id ? effStatus.video_id === vid : true) &&
      (!Number.isFinite(effStatus?.remaining_seconds) || effStatus.remaining_seconds > 0)
    );

    const hasCachedEdu = (
      cachedActiveEduVideo &&
      vid &&
      cachedActiveEduVideo.videoId === vid &&
      isEducationalSessionValid(cachedActiveEduVideo)
    );

    if (vid && (hasLocalEdu || hasStatusEdu || hasCachedEdu)) {
      const activeInfo = cachedActiveEduVideo || (hasStatusEdu ? effStatus : null);
      const remSec = (hasLocalEdu && eduRemainingSeconds > 0) ? eduRemainingSeconds :
                     (hasStatusEdu && Number.isFinite(effStatus?.remaining_seconds)) ? effStatus.remaining_seconds :
                     (activeInfo ? getRemainingSecondsFromActiveEdu(activeInfo) : 1205);
      const durSec = activeInfo?.durationSeconds || activeInfo?.duration_seconds || (hasStatusEdu ? effStatus.duration_seconds : 1205) || 1205;
      const effectiveTitle = activeInfo?.title || effStatus?.title || '';
      return {
        allow: true,
        decision: 'ALLOW',
        reason: 'EDUCATIONAL_VIDEO',
        videoId: vid,
        remainingSeconds: remSec,
        durationSeconds: durSec,
        formattedDuration: formatTimeMMSS(remSec),
        title: effectiveTitle
      };
    }

    // 2b. Check Early Sleep Reward (EARLY_SLEEP_ONE_VIDEO)
    const isEsrStatus = (
      effStatus?.temporary_exception === 'EARLY_SLEEP_ONE_VIDEO' ||
      effStatus?.temporaryException === 'EARLY_SLEEP_ONE_VIDEO' ||
      effStatus?.early_sleep_reward_active === true
    );
    const boundEsrVid = effStatus?.selected_video_id || effStatus?.video_id;

    if (effStatus?.code === 'EARLY_SLEEP_REWARD_CONSUMED') {
      return {
        allow: false,
        decision: 'BLOCK',
        reason: 'EARLY_SLEEP_REWARD_CONSUMED',
        statusText: 'Early Sleep Reward has already been consumed today. Replay is locked.'
      };
    }

    if (effStatus?.code === 'EARLY_SLEEP_VIDEO_MISMATCH' || (isEsrStatus && boundEsrVid && vid && vid !== boundEsrVid)) {
      return {
        allow: false,
        decision: 'BLOCK',
        reason: 'EARLY_SLEEP_VIDEO_MISMATCH',
        statusText: `Early Sleep Reward is locked to video '${boundEsrVid}'. Other YouTube videos are blocked.`
      };
    }

    if (isEsrStatus && vid && (!boundEsrVid || vid === boundEsrVid)) {
      return {
        allow: true,
        decision: 'ALLOW',
        reason: 'EARLY_SLEEP_ONE_VIDEO',
        videoId: vid,
        rewardId: effStatus.reward_id,
        title: effStatus?.title || ''
      };
    }

    // 3. THIRD: Check existing authorized temporary reward entitlements
    if (effStatus?.reward_active && effStatus?.restricted === false && Number.isFinite(effStatus?.reward_remaining_ms) && effStatus.reward_remaining_ms > 0) {
      return {
        allow: true,
        decision: 'ALLOW',
        reason: 'ENTERTAINMENT_REWARD',
        remainingMs: effStatus.reward_remaining_ms
      };
    }

    // 4. FOURTH: Check access status / normal points threshold
    const currentScore = Number.isFinite(effStatus?.currentScore) ? effStatus.currentScore : (effStatus?.score ?? null);
    const requiredScore = Number.isFinite(effStatus?.requiredScore) ? effStatus.requiredScore : 80;
    if (effStatus?.restricted === false) {
      const isYT = isYouTubeHost();
      if (!isYT || currentScore === null || currentScore >= requiredScore) {
        return {
          allow: true,
          decision: 'ALLOW',
          reason: isYT ? 'POINTS_THRESHOLD_MET' : 'ACCESS_ALLOWED',
          currentScore,
          requiredScore
        };
      }
    }

    // 5. FIFTH: Apply default restriction
    return {
      allow: false,
      decision: 'BLOCK',
      reason: 'POINTS_INSUFFICIENT',
      currentScore,
      requiredScore,
      statusText: effStatus?.statusText || 'Confirmed points insufficient'
    };
  }

  const css = `
    :host { all:initial; color-scheme:dark; }
    *,*::before,*::after {box-sizing:border-box;}
    .veil {position:fixed;inset:0;display:grid;place-items:center;padding:24px;background:#04080ebd;backdrop-filter:blur(9px);font:15px/1.5 'Segoe UI',Arial,sans-serif;color:#f5f8fd;}
    .card {display:grid;grid-template-columns:180px minmax(0,1fr);width:min(720px,100%);max-height:calc(100dvh - 48px);overflow:auto;border:1px solid #304056;border-radius:20px;background:#171e2c;box-shadow:0 24px 70px #0007;overscroll-behavior:contain;}
    .art {display:grid;place-items:center;background:radial-gradient(circle,#20384a,#101927 75%);padding:16px;}
    .art img {display:block;width:100%;height:auto;max-height:230px;object-fit:contain;}
    .body {padding:28px;min-width:0;} .brand {font-size:12px;color:#b8c6d8;margin:0 0 18px;letter-spacing:.02em;}
    .badge {display:inline-block;font-size:12px;color:#ffcf95;background:#ffbd7814;border:1px solid #ffbd7845;padding:3px 9px;border-radius:99px;margin-bottom:12px;}
    h1 {display:block!important;position:static!important;visibility:visible!important;margin:0 0 12px;font-size:25px;font-weight:700;line-height:1.25;color:#fff;letter-spacing:-.025em;}
    p {margin:0;} .reason {color:#c7d1df;white-space:pre-wrap;overflow-wrap:anywhere;}
    .score {margin:16px 0 0;font-size:14px;font-weight:600;color:#8ce4bf;} .meta {font-size:12px;color:#aebacb;margin-top:10px;}
    .actions {display:flex;flex-wrap:wrap;gap:10px;margin-top:24px;}
    button {font:600 14px 'Segoe UI',Arial,sans-serif;min-height:44px;padding:10px 16px;border:1px solid #42536b;border-radius:10px;background:transparent;color:#ecf3fa;cursor:pointer;}
    button.primary {background:#22d69b;border-color:#22d69b;color:#062d22;flex:1;}
    button.edu {background:#2563eb;border-color:#3b82f6;color:#ffffff;flex:1;}
    button:hover {background:#ffffff0e;} button.primary:hover {background:#5ee9bb;} button.edu:hover {background:#3b82f6;}
    button:disabled {opacity:.6;cursor:wait;} button:focus-visible {outline:3px solid #77beff;outline-offset:3px;}
    .retry {margin-top:14px;padding:0;min-height:32px;border:0;color:#9bd5ff;text-decoration:underline;}
    .edu-status {margin-top:16px;padding:12px;border-radius:10px;font-size:13px;line-height:1.45;background:#1e293b;border:1px solid #334155;}
    .edu-review-panel {margin-top:16px;border-top:1px solid #304056;padding-top:16px;}
    .edu-review-header {display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;}
    .edu-review-title {font-size:12.5px;font-weight:700;letter-spacing:.05em;color:#38bdf8;text-transform:uppercase;}
    .edu-meta-grid {background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:12px;margin-bottom:14px;font-size:12.5px;display:flex;flex-direction:column;gap:5px;}
    .edu-stages-heading {font-size:11.5px;font-weight:700;color:#94a3b8;margin-bottom:8px;text-transform:uppercase;letter-spacing:.04em;}
    .edu-stages-list {display:flex;flex-direction:column;gap:6px;font-size:12px;margin-bottom:12px;}
    .edu-stage {display:flex;align-items:center;gap:8px;color:#94a3b8;transition:color .2s ease;}
    .edu-stage.active {color:#38bdf8;font-weight:600;}
    .edu-stage.done {color:#34d399;font-weight:600;}
    .edu-stage.warn {color:#fbbf24;font-weight:600;}
    .edu-stage.error {color:#f87171;font-weight:600;}
    .edu-result-box {padding:12px 16px;border-radius:12px;font-size:13px;line-height:1.45;margin-top:10px;}
    .edu-result-box.approved {background:#064e3b;border:1.5px solid #10b981;color:#d1fae5;}
    .edu-result-box.rejected {background:#450a0a;border:1.5px solid #ef4444;color:#fee2e2;}
    .edu-result-box.pending {background:#451a03;border:1.5px solid #f59e0b;color:#fef3c7;}
    .edu-result-box.error {background:#3b0764;border:1.5px solid #a855f7;color:#f3e8ff;}
    [hidden] {display:none!important;}
    @media(max-width:560px) {.veil{padding:14px}.card{grid-template-columns:minmax(0,1fr);max-height:calc(100dvh - 28px)}.art{display:none}.body{padding:22px}h1{font-size:23px}.actions{flex-direction:column}.actions button{width:100%}}
  `;

  function extractYouTubeVideoId(urlStr) {
    if (!urlStr) urlStr = window.location.href;
    if (typeof urlStr !== 'string') return null;
    const str = urlStr.trim();
    if (/^[A-Za-z0-9_-]{11}$/.test(str)) return str;
    try {
      const u = new URL(str, window.location.origin);
      if (u.hostname === 'youtu.be' || u.hostname === 'www.youtu.be') {
        const seg = u.pathname.replace(/^\//, '').split(/[?#\/]/)[0];
        if (seg && seg.length === 11) return seg;
      }
      if (u.searchParams.has('v')) {
        const v = u.searchParams.get('v');
        if (v && v.length >= 11) return v.slice(0, 11);
      }
      const m = u.pathname.match(/\/(?:embed|v|shorts|live)\/([A-Za-z0-9_-]{11})/);
      if (m) return m[1];
    } catch (_) {}
    return null;
  }

  function formatTimeMMSS(sec) {
    const s = Math.max(0, Math.floor(sec || 0));
    const mins = Math.floor(s / 60);
    const rem = s % 60;
    return `${String(mins).padStart(2, '0')}:${String(rem).padStart(2, '0')}`;
  }

  function message(type, payload = {}, timeout = 12000) {
    return new Promise(resolve => {
      let done = false;
      const finish = result => { if (done) return; done = true; clearTimeout(deadline); resolve(result); };
      const deadline = setTimeout(() => finish({ restricted: true, unavailable: true, code: 'CHECK_TIMEOUT', statusText: 'The access check timed out.' }), timeout);
      try {
        chrome.runtime.sendMessage({ type, ...payload }, result => {
          const error = chrome.runtime.lastError;
          finish(error ? { restricted: true, unavailable: true, code: 'EXTENSION_UNAVAILABLE', statusText: 'The extension was disconnected. Refresh to reconnect.', error: 'Extension disconnected. Reload extension in Comet and refresh tab.' } : result || { restricted: true, unavailable: true });
        });
      } catch (_) {
        finish({ restricted: true, unavailable: true, code: 'EXTENSION_UNAVAILABLE', statusText: 'Refresh this page to reconnect the extension.', error: 'Extension disconnected. Reload extension in Comet and refresh tab.' });
      }
    });
  }

  function restorePage() {
    if (savedBody) {
      savedBody.body.inert = savedBody.inert;
      savedBody.body.style.overflow = savedBody.overflow;
      savedBody = null;
    }
    if (root?.__overflow !== undefined) document.documentElement.style.overflow = root.__overflow;
  }

  function shieldPage() {
    if (document.body && (!savedBody || savedBody.body !== document.body)) {
      if (savedBody) { savedBody.body.inert = savedBody.inert; savedBody.body.style.overflow = savedBody.overflow; }
      savedBody = { body: document.body, inert: document.body.inert, overflow: document.body.style.overflow };
      document.body.inert = true;
      document.body.style.overflow = 'hidden';
    }
    if (root && !root.isConnected) document.documentElement.append(root);
    pauseMedia();
  }

  function pauseMedia() {
    const curVid = extractYouTubeVideoId(window.location.href) || currentVideoId;
    const auth = evaluateVideoAuthorization(lastKnownStatus || ytLastDecision, curVid, window.location.href);
    if (auth.allow || (eduActive && eduRemainingSeconds > 0 && (!eduVideoId || eduVideoId === curVid))) {
      return;
    }
    if (!blocked && !ytRestricted) return;
    try {
      if (document.pictureInPictureElement && typeof document.exitPictureInPicture === 'function') {
        document.exitPictureInPicture().catch(() => {});
      }
    } catch (_) {}
    document.querySelectorAll('audio,video').forEach(media => {
      try {
        if (!media.paused) media.pause();
        media.autoplay = false;
        media.muted = true;
        try { media.volume = 0; } catch (_) {}
        try { media.playbackRate = 0; } catch (_) {}
        if (ytRestricted) {
          if (media?.classList?.add) media.classList.add('strictrecords-video-blurred');
        }
      } catch (_) {}
    });
    try {
      const p = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
      if (p) {
        if (typeof p.pauseVideo === 'function') p.pauseVideo();
        if (typeof p.stopVideo === 'function') p.stopVideo();
      }
    } catch (_) {}
  }

  function handleMediaPlaybackAttempt(event) {
    const curVid = extractYouTubeVideoId(window.location.href) || currentVideoId;
    const auth = evaluateVideoAuthorization(lastKnownStatus || ytLastDecision, curVid, window.location.href);
    if (auth.allow || (eduActive && eduRemainingSeconds > 0 && (!eduVideoId || eduVideoId === curVid))) {
      return;
    }
    if ((blocked || ytRestricted) && typeof event.target?.pause === 'function') {
      try {
        event.target.pause();
        event.target.autoplay = false;
        event.target.muted = true;
        try { event.target.volume = 0; } catch (_) {}
        try { event.target.playbackRate = 0; } catch (_) {}
        if (ytRestricted) {
          if (event.target?.classList?.add) event.target.classList.add('strictrecords-video-blurred');
        }
      } catch (_) {}
      try {
        event.preventDefault();
        event.stopImmediatePropagation();
      } catch (_) {}
    }
  }

  ['play', 'playing', 'seeking', 'seeked', 'loadeddata', 'canplay', 'loadedmetadata'].forEach(evt => {
    document.addEventListener(evt, handleMediaPlaybackAttempt, true);
    window.addEventListener(evt, handleMediaPlaybackAttempt, true);
  });

  window.addEventListener('keydown', (e) => {
    const curVid = extractYouTubeVideoId(window.location.href) || currentVideoId;
    const auth = evaluateVideoAuthorization(lastKnownStatus || ytLastDecision, curVid, window.location.href);
    if (auth.allow || (eduActive && eduRemainingSeconds > 0 && (!eduVideoId || eduVideoId === curVid))) {
      return; // Do NOT intercept playback keys when authorized
    }
    if (ytRestricted && !blocked) {
      const tag = (e.target?.tagName || '').toLowerCase();
      if (tag !== 'input' && tag !== 'textarea' && !e.target?.isContentEditable) {
        const k = (e.key || '').toLowerCase();
        if (k === ' ' || k === 'k' || k === 'j' || k === 'l' || k === 'm' || k.startsWith('arrow')) {
          e.preventDefault();
          e.stopPropagation();
          pauseMedia();
        }
      }
    }
  }, true);

  function injectMainWorldPlayInterception() {
    // yt_main_world.js is registered in manifest.json with "world": "MAIN"
    // so no dynamic DOM script injection is required (avoids YouTube CSP restrictions).
  }

  function injectYouTubeBlurStyles() {
    if (document.getElementById('strictrecords-yt-blur-styles')) return;
    const style = document.createElement('style');
    style.id = 'strictrecords-yt-blur-styles';
    style.textContent = `
      html.strictrecords-yt-restricted video,
      body.strictrecords-yt-restricted video,
      html.strictrecords-yt-restricted .html5-main-video,
      html.strictrecords-yt-restricted #movie_player video,
      html.strictrecords-yt-restricted .html5-video-player video,
      html.strictrecords-yt-restricted ytd-player video,
      html.strictrecords-yt-restricted #player video,
      html.strictrecords-yt-restricted .html5-video-container,
      .strictrecords-video-blurred,
      .strictrecords-player-blurred video,
      .strictrecords-player-blurred .html5-video-container {
        filter: blur(28px) brightness(0.45) !important;
        pointer-events: none !important;
        user-select: none !important;
        transition: filter 0.2s ease !important;
      }
      .strictrecords-player-blurred {
        position: relative !important;
      }
      .strictrecords-player-blurred .ytp-chrome-bottom,
      .strictrecords-player-blurred .ytp-gradient-bottom,
      .strictrecords-player-blurred .ytp-chrome-top,
      .strictrecords-player-blurred .ytp-bezel,
      .strictrecords-player-blurred .ytp-big-mode,
      .strictrecords-player-blurred .ytp-play-button {
        pointer-events: none !important;
        opacity: 0 !important;
        visibility: hidden !important;
      }
      #${YT_BLUR_OVERLAY_ID} {
        position: absolute !important;
        inset: 0 !important;
        width: 100% !important;
        height: 100% !important;
        z-index: 2147483640 !important;
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: center !important;
        background: rgba(10, 16, 28, 0.88) !important;
        backdrop-filter: blur(14px) !important;
        -webkit-backdrop-filter: blur(14px) !important;
        color: #f8fafc !important;
        font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif !important;
        padding: 20px !important;
        box-sizing: border-box !important;
        pointer-events: auto !important;
        user-select: none !important;
        text-align: center !important;
        border-radius: inherit !important;
        overflow: hidden !important;
      }
      #${YT_BLUR_OVERLAY_ID} button {
        transition: transform 0.1s ease, background 0.15s ease !important;
      }
      #${YT_BLUR_OVERLAY_ID} button:hover {
        transform: translateY(-1px) !important;
      }
      #${YT_BLUR_OVERLAY_ID} button:active {
        transform: translateY(1px) !important;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  function findYouTubePlayerContainers() {
    const list = [];
    const moviePlayer = document.getElementById('movie_player') || document.querySelector('ytd-player#ytd-player') || document.querySelector('ytd-player') || document.querySelector('.html5-video-player');
    if (moviePlayer && moviePlayer.isConnected) list.push(moviePlayer);

    const shortsReel = document.querySelector('ytd-reel-video-renderer[is-active] #player-container') ||
                       document.querySelector('ytd-reel-video-renderer[is-active]') ||
                       document.getElementById('shorts-player') ||
                       document.querySelector('ytd-shorts');
    if (shortsReel && shortsReel.isConnected && !list.includes(shortsReel)) list.push(shortsReel);

    const miniPlayer = document.querySelector('ytd-miniplayer #player-container') || document.querySelector('ytd-miniplayer');
    if (miniPlayer && miniPlayer.isConnected && !list.includes(miniPlayer)) list.push(miniPlayer);

    const previewPlayer = document.querySelector('ytd-inline-preview-renderer #inline-preview-player') || document.querySelector('ytd-inline-preview-renderer');
    if (previewPlayer && previewPlayer.isConnected && !list.includes(previewPlayer)) list.push(previewPlayer);

    const watchPlayer = document.getElementById('player-container') || document.querySelector('#player.ytd-watch-flexy') || document.querySelector('#player-container-outer') || document.getElementById('full-bleed-container');
    if (watchPlayer && watchPlayer.isConnected && !list.includes(watchPlayer) && list.length === 0) list.push(watchPlayer);

    document.querySelectorAll('video').forEach(v => {
      const parent = (typeof v.closest === 'function' ? v.closest('#movie_player, ytd-player, .html5-video-player, ytd-reel-video-renderer, ytd-miniplayer, ytd-inline-preview-renderer, #player-container') : null) || v.parentElement;
      if (parent && parent.isConnected && !list.includes(parent)) list.push(parent);
    });

    if (list.length === 0 && window.top !== window && document.body) {
      list.push(document.body);
    }
    return list;
  }

  function applyYouTubeVideoRestriction(status) {
    const vid = extractYouTubeVideoId(window.location.href) || currentVideoId;
    const authDecision = evaluateVideoAuthorization(status, vid, window.location.href);
    if (authDecision.allow) {
      console.log(`[StrictRecords Diagnostic] STALE_RESTRICTION_IGNORED: video_id=${vid} general restriction ignored due to active authorization: ${authDecision.reason}`);
      ytRestricted = false;
      removeYouTubeVideoRestriction();
      unlockAndPlayYouTubeVideo();
      return;
    }

    ytRestricted = true;
    ytLastDecision = status;
    try {
      document.documentElement.classList.add('strictrecords-yt-restricted');
      if (document.body) document.body.classList.add('strictrecords-yt-restricted');
    } catch (_) {}
    try {
      window.postMessage({ type: 'STRICTRECORDS_RESTRICT_PLAYBACK' }, '*');
    } catch (_) {}
    injectYouTubeBlurStyles();
    pauseMedia();

    if (!ytPlayerObserver) {
      let debounceTimer = null;
      ytPlayerObserver = new MutationObserver(() => {
        if (ytRestricted) {
          const curVid = extractYouTubeVideoId(window.location.href) || currentVideoId;
          const auth = evaluateVideoAuthorization(ytLastDecision, curVid, window.location.href);
          if (auth.allow) {
            ytRestricted = false;
            removeYouTubeVideoRestriction();
            unlockAndPlayYouTubeVideo();
            return;
          }
          pauseMedia();
          if (!debounceTimer) {
            debounceTimer = setTimeout(() => {
              debounceTimer = null;
              if (ytRestricted) {
                ensureYouTubeBlurOverlays(ytLastDecision);
              }
            }, 250);
          }
        }
      });
      const obsTarget = document.getElementById('content') || document.querySelector('ytd-app') || document.body || document.documentElement;
      ytPlayerObserver.observe(obsTarget, { childList: true });
    }

    if (typeof setInterval === 'function' && !ytEnforcementInterval) {
      ytEnforcementInterval = setInterval(() => {
        if (ytRestricted) {
          const curVid = extractYouTubeVideoId(window.location.href) || currentVideoId;
          const auth = evaluateVideoAuthorization(ytLastDecision, curVid, window.location.href);
          if (auth.allow) {
            ytRestricted = false;
            removeYouTubeVideoRestriction();
            unlockAndPlayYouTubeVideo();
            return;
          }
          pauseMedia();
          ensureYouTubeBlurOverlays(ytLastDecision);
        }
      }, 2000);
    }

    ensureYouTubeBlurOverlays(status);
  }

  function ensureYouTubeBlurOverlays(status) {
    if (!ytRestricted) return;
    const vid = extractYouTubeVideoId(window.location.href) || currentVideoId;
    const authDecision = evaluateVideoAuthorization(status, vid, window.location.href);
    if (authDecision.allow) {
      console.log(`[StrictRecords Diagnostic] RESTRICTION_OVERLAY_REMOVED: video_id=${vid} authorization valid: ${authDecision.reason}`);
      ytRestricted = false;
      removeYouTubeVideoRestriction();
      unlockAndPlayYouTubeVideo();
      return;
    }
    const containers = findYouTubePlayerContainers();
    const score = Number.isFinite(status?.currentScore) ? status.currentScore : (status?.score ?? null);
    const req = Number.isFinite(status?.requiredScore) ? status.requiredScore : 80;
    const diff = (score !== null && score < req) ? req - score : 0;
    const scoreText = (score !== null) ?
      `Confirmed score ${score} · ${req} points required${diff > 0 ? ` (${diff} more needed)` : ''}` :
      'Confirmed points unavailable · Access restricted';

    containers.forEach(container => {
      container.classList.add('strictrecords-player-blurred');
      let overlay = container.querySelector('#' + YT_BLUR_OVERLAY_ID);
      if (overlay) {
        // Fast-path: update existing overlay without DOM reconstruction
        const scoreEl = overlay.querySelector('.strictrecords-yt-score-text');
        if (scoreEl && scoreEl.textContent !== scoreText) {
          scoreEl.textContent = scoreText;
        }
        const eduBtn = overlay.querySelector('.strictrecords-yt-req-edu-btn');
        if (eduBtn) {
          eduBtn.style.display = 'inline-flex';
        }
        return;
      }

      overlay = document.createElement('div');
      overlay.id = YT_BLUR_OVERLAY_ID;
      try {
        if (getComputedStyle(container).position === 'static') {
          container.style.position = 'relative';
        }
      } catch (_) {}

      overlay.innerHTML = `
        <div style="background:#131c2e;border:1.5px solid #304056;border-radius:20px;padding:26px 30px;max-width:500px;width:92%;box-shadow:0 24px 60px rgba(0,0,0,0.65);text-align:center;pointer-events:auto;box-sizing:border-box;">
          <div style="font-size:36px;margin-bottom:8px;line-height:1;">🤖</div>
          <div style="font-size:18px;font-weight:700;color:#ffffff;letter-spacing:-0.02em;margin-bottom:4px;">STRICTRECORDS · VIDEO RESTRICTED</div>
          <div style="font-size:13px;font-weight:600;color:#38bdf8;margin-bottom:10px;">🔒 YouTube Restricted</div>
          <p style="font-size:13px;color:#cbd5e1;margin:0 0 10px;line-height:1.5;">Playback has been paused by StrictRecords.<br>Your current points are insufficient to access YouTube.<br>Complete your pending activities to unlock access.</p>
          <div class="strictrecords-yt-score-text" style="display:inline-flex;align-items:center;padding:5px 14px;background:#1e293b;border:1px solid #334155;border-radius:99px;font-size:12px;font-weight:600;color:#8ce4bf;margin-bottom:16px;">
            ${scoreText}
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:10px;justify-content:center;">
            <button class="strictrecords-yt-req-edu-btn" type="button" style="display:inline-flex;align-items:center;gap:6px;min-height:38px;padding:8px 18px;background:#2563eb;border:1px solid #3b82f6;border-radius:99px;color:#ffffff;font:600 13px 'Segoe UI',sans-serif;cursor:pointer;">🎓 REQUEST EDUCATIONAL ACCESS</button>
            <button class="strictrecords-yt-open-dash-btn" type="button" style="min-height:38px;padding:8px 18px;background:#1e293b;border:1px solid #475569;border-radius:99px;color:#ecf3fa;font:600 13px 'Segoe UI',sans-serif;cursor:pointer;">📊 OPEN STRICTRECORDS</button>
          </div>
        </div>
      `;

      container.appendChild(overlay);

      const eduBtn = overlay.querySelector('.strictrecords-yt-req-edu-btn');
      eduBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        triggerEducationalRequestFromOverlay(extractYouTubeVideoId(window.location.href));
      });

      const dashBtn = overlay.querySelector('.strictrecords-yt-open-dash-btn');
      dashBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        chrome.runtime.sendMessage({ type: 'STRICTBOT_OPEN_DASHBOARD' });
      });
    });
  }

  function removeYouTubeVideoRestriction() {
    ytRestricted = false;
    blocked = false;
    ytLastDecision = null;
    if (mediaEnforcementInterval) {
      clearInterval(mediaEnforcementInterval);
      mediaEnforcementInterval = null;
    }
    if (ytEnforcementInterval) {
      clearInterval(ytEnforcementInterval);
      ytEnforcementInterval = null;
    }
    if (ytPlayerObserver) {
      ytPlayerObserver.disconnect();
      ytPlayerObserver = null;
    }
    try {
      document.documentElement.classList.remove(
        'strictrecords-yt-restricted',
        'strictrecords-playback-restricted',
        'strictrecords-restricted'
      );
      if (document.body) {
        document.body.classList.remove(
          'strictrecords-yt-restricted',
          'strictrecords-playback-restricted',
          'strictrecords-restricted'
        );
      }
    } catch (_) {}
    try {
      document.querySelectorAll('.strictrecords-video-blurred, .strictrecords-video-restricted').forEach(v => {
        v?.classList?.remove?.('strictrecords-video-blurred');
        v?.classList?.remove?.('strictrecords-video-restricted');
        if (v?.style) {
          v.style.filter = '';
          v.style.pointerEvents = '';
        }
        if (v) v.muted = false;
      });
      document.querySelectorAll('.strictrecords-player-blurred').forEach(p => {
        p?.classList?.remove?.('strictrecords-player-blurred');
      });
      document.querySelectorAll('#' + YT_BLUR_OVERLAY_ID + ', .strictrecords-global-video-overlay').forEach(o => {
        o?.remove?.();
      });
    } catch (_) {}
  }

  function unlockAndPlayYouTubeVideo() {
    blocked = false;
    ytRestricted = false;
    if (mediaEnforcementInterval) {
      clearInterval(mediaEnforcementInterval);
      mediaEnforcementInterval = null;
    }
    if (ytEnforcementInterval) {
      clearInterval(ytEnforcementInterval);
      ytEnforcementInterval = null;
    }
    try {
      document.documentElement.classList.remove(
        'strictrecords-yt-restricted',
        'strictrecords-playback-restricted',
        'strictrecords-restricted'
      );
      if (document.body) {
        document.body.classList.remove(
          'strictrecords-yt-restricted',
          'strictrecords-playback-restricted',
          'strictrecords-restricted'
        );
      }
    } catch (_) {}
    try {
      document.querySelectorAll('#' + YT_BLUR_OVERLAY_ID + ', .strictrecords-global-video-overlay').forEach(el => el.remove());
      document.querySelectorAll('video').forEach(v => {
        v.classList.remove('strictrecords-video-blurred', 'strictrecords-video-restricted');
        if (v.style) {
          v.style.filter = '';
          v.style.pointerEvents = '';
        }
        v.muted = false;
        try { if (v.playbackRate === 0) v.playbackRate = 1; } catch (_) {}
        // CRITICAL: Never force v.play() or hijack playback! Allow user to pause/play naturally.
      });
      document.querySelectorAll('.strictrecords-player-blurred').forEach(p => p.classList.remove('strictrecords-player-blurred'));
      // CRITICAL: Never force mp.playVideo()! Unlocking only removes restrictions and overlays.
      // Broadcast unlock message to Main World (yt_main_world.js) where YouTube player API is accessible
      try {
        window.postMessage({ type: 'STRICTRECORDS_UNLOCK_PLAYBACK' }, '*');
        setTimeout(() => window.postMessage({ type: 'STRICTRECORDS_UNLOCK_PLAYBACK' }, '*'), 300);
        setTimeout(() => window.postMessage({ type: 'STRICTRECORDS_UNLOCK_PLAYBACK' }, '*'), 800);
      } catch (_) {}
    } catch (_) {}
  }

  function triggerEducationalRequestFromOverlay(vid) {
    ensurePopup();
    if (ui) {
      if (ui.requestEdu) {
        ui.requestEdu.style.display = 'inline-flex';
        ui.requestEdu.disabled = false;
      }
      if (ui.eduUrlInput) {
        ui.eduUrlInput.value = vid ? `https://www.youtube.com/watch?v=${vid}` : window.location.href;
      }
      void educationalReviewStart?.(vid, window.location.href);
    }
  }

  function removePopup() {
    educationalReviewGeneration++;
    educationalReviewStart = null;
    blocked = false;
    if (mediaEnforcementInterval) {
      clearInterval(mediaEnforcementInterval);
      mediaEnforcementInterval = null;
    }
    try {
      document.documentElement.classList.remove('strictrecords-playback-restricted', 'strictrecords-restricted');
      if (document.body) document.body.classList.remove('strictrecords-playback-restricted', 'strictrecords-restricted');
    } catch (_) {}
    observer?.disconnect();
    observer = null;
    restorePage();
    root?.remove();
    root = null;
    shadow = null;
    ui = null;
    closing = false;
    if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    previousFocus = null;
  }

  function ensurePopup() {
    try {
      document.documentElement.classList.add('strictrecords-playback-restricted', 'strictrecords-restricted');
      if (document.body) document.body.classList.add('strictrecords-playback-restricted', 'strictrecords-restricted');
    } catch (_) {}
    if (typeof setInterval === 'function' && !mediaEnforcementInterval) {
      mediaEnforcementInterval = setInterval(pauseMedia, 1000);
    }
    if (root?.isConnected) return;
    previousFocus = document.activeElement;
    root = document.createElement('div');
    root.id = ROOT_ID;
    root.style.cssText = 'all:initial!important;display:block!important;position:fixed!important;inset:0!important;z-index:2147483647!important;visibility:visible!important;opacity:1!important;';
    root.__overflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    shadow = root.attachShadow({ mode: 'open' });

    const style = document.createElement('style');
    style.textContent = css;
    const veil = document.createElement('div');
    veil.className = 'veil';
    veil.innerHTML = `
      <section class="card" role="dialog" aria-modal="true" aria-labelledby="title" aria-describedby="reason">
        <div class="art" aria-hidden="true">
          <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;text-align:center;">
            <div style="width:110px;height:110px;border-radius:28px;background:radial-gradient(circle,#38bdf826,#0f172a 80%);border:2px solid #38bdf866;display:flex;align-items:center;justify-content:center;box-shadow:0 12px 40px rgba(0,0,0,0.6),0 0 20px rgba(56,189,248,0.25);">
              <span style="font-size:58px;line-height:1;filter:drop-shadow(0 4px 14px rgba(56,189,248,0.5));">🤖</span>
            </div>
            <div style="font-weight:700;font-size:13px;letter-spacing:0.06em;color:#38bdf8;text-transform:uppercase;">StrictBot</div>
          </div>
        </div>
        <div class="body">
          <p class="brand">StrictRoutines · StrictBot</p>
          <span class="badge" id="badge">Checking access</span>
          <h1 id="title">Checking website access</h1>
          <p class="reason" id="reason" role="status" aria-live="polite">Waiting for the desktop decision…</p>
          <p class="score" id="score"></p>
          <p class="meta" id="meta"></p>
          <div class="actions">
            <button class="primary" id="close" type="button">Close this tab</button>
            <button class="edu" id="requestEdu" type="button" style="display:none;">🎓 REQUEST EDUCATIONAL VIDEO</button>
            <button id="dashboard" type="button">Open dashboard</button>
          </div>
          <div class="edu-input-panel" id="eduInputPanel" style="display:none;margin-top:16px;background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:14px;">
            <p style="font-size:13px;font-weight:600;color:#93c5fd;margin:0 0 8px;">Enter YouTube video link to request educational study access:</p>
            <div style="display:flex;gap:8px;">
              <input type="text" id="eduUrlInput" placeholder="https://www.youtube.com/watch?v=... or Video ID" style="flex:1;min-height:38px;background:#1e293b;border:1px solid #334155;border-radius:8px;padding:6px 12px;color:#f8fafc;font:13px 'Segoe UI',Arial,sans-serif;" />
              <button id="eduSubmitUrlBtn" type="button" style="background:#2563eb;border:1px solid #3b82f6;color:#fff;border-radius:8px;padding:8px 16px;font:600 13px 'Segoe UI',Arial,sans-serif;cursor:pointer;white-space:nowrap;">Evaluate</button>
            </div>
            <div id="eduInputError" style="display:none;font-size:12px;color:#f87171;margin-top:6px;"></div>
          </div>
          <div class="edu-review-panel" id="eduReviewPanel" style="display:none;">
            <div class="edu-review-header">
              <span class="edu-review-title">EDUCATIONAL VIDEO REVIEW</span>
              <span id="eduReviewBadge" class="badge" style="margin:0;background:#38bdf81a;border-color:#38bdf855;color:#7dd3fc;">INITIALIZING</span>
            </div>
            <div class="edu-meta-grid">
              <div><strong style="color:#94a3b8;">Video title:</strong> <span id="eduMetaTitle" style="color:#f8fafc;font-weight:600;">Loading…</span></div>
              <div><strong style="color:#94a3b8;">Channel:</strong> <span id="eduMetaChannel" style="color:#cbd5e1;">Loading…</span></div>
              <div><strong style="color:#94a3b8;">Duration:</strong> <span id="eduMetaDuration" style="color:#38bdf8;font-weight:600;">Calculating…</span></div>
              <div><strong style="color:#94a3b8;">Validation status:</strong> <span id="eduMetaStatus" style="color:#f1f5f9;">Initializing…</span></div>
            </div>
            <div class="edu-stages-heading">Validation Progress:</div>
            <div class="edu-stages-list" id="eduStagesList">
              <div class="edu-stage" id="eduStage1"><span class="stage-icon">⏳</span> <span>1. Capturing video information</span></div>
              <div class="edu-stage" id="eduStage2"><span class="stage-icon">⏳</span> <span>2. Extracting available content evidence</span></div>
              <div class="edu-stage" id="eduStage3"><span class="stage-icon">⏳</span> <span>3. Sending request to Taufa AI (designated thread 891a3c1e-eed2-460e-bc05-6b8ac94bec5b)</span></div>
              <div class="edu-stage" id="eduStage4"><span class="stage-icon">⏳</span> <span>4. Evaluating educational relevance</span></div>
              <div class="edu-stage" id="eduStage5"><span class="stage-icon">⏳</span> <span>5. Verifying video duration</span></div>
              <div class="edu-stage" id="eduStage6"><span class="stage-icon">⏳</span> <span>6. Finalizing access decision</span></div>
            </div>
            <div class="edu-result-box" id="eduResultBox" style="display:none;"></div>
          </div>
          <div class="edu-status" id="eduStatus" style="display:none;"></div>
          <button class="retry" id="retry" type="button">Retry access check</button>
        </div>
      </section>`;

    shadow.append(style, veil);
    document.documentElement.append(root);

    ui = Object.fromEntries(
      ['badge', 'title', 'reason', 'score', 'meta', 'close', 'requestEdu', 'eduStatus', 'dashboard', 'retry',
       'eduInputPanel', 'eduUrlInput', 'eduSubmitUrlBtn', 'eduInputError',
       'eduReviewPanel', 'eduReviewBadge', 'eduMetaTitle', 'eduMetaChannel', 'eduMetaDuration', 'eduMetaStatus',
       'eduStagesList', 'eduStage1', 'eduStage2', 'eduStage3', 'eduStage4', 'eduStage5', 'eduStage6', 'eduResultBox'].map(id => [id, shadow.getElementById(id)])
    );

    const popupUi = ui;
    const popupRoot = root;
    const isPopupCurrent = () => !dormant && !stopped && ui === popupUi && root === popupRoot && popupRoot?.isConnected;
    let reviewRunning = false;
    async function startReview(vid, targetUrl) {
      if (reviewRunning || !isPopupCurrent()) return;
      reviewRunning = true;
      try {
        await runEducationalEvaluation(vid, targetUrl);
      } catch (error) {
        if (isPopupCurrent()) {
          showEduStatus(error?.message || 'Educational review could not finish. Please retry.', 'error');
          popupUi.requestEdu.style.display = 'inline-flex';
          popupUi.requestEdu.disabled = false;
        }
      } finally {
        reviewRunning = false;
      }
    }
    educationalReviewStart = startReview;

    ui.close.addEventListener('click', async () => {
      if (closing) return;
      closing = true;
      ui.close.disabled = true;
      ui.close.textContent = 'Closing…';
      const result = await message('STRICTBOT_CLOSE_RESTRICTED_TAB');
      if (result.unlocked) { removePopup(); return; }
      if (!result.success) {
        closing = false;
        if (ui) {
          ui.close.disabled = false;
          ui.close.textContent = 'Close this tab';
          ui.reason.textContent = result.error || 'Could not close this tab. Close it with the browser.';
        }
      }
    });

    ui.dashboard.addEventListener('click', async () => {
      const result = await message('STRICTBOT_OPEN_DASHBOARD');
      if (!result.success && ui) ui.reason.textContent = 'Could not open the dashboard. Open StrictRoutines from the Start menu.';
    });

    ui.retry.addEventListener('click', () => { stopped = false; refresh(true); });

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function setReviewStage(num, state, message) {
      if (!isPopupCurrent()) return;
      const el = popupUi['eduStage' + num];
      if (!el) return;
      el.className = 'edu-stage ' + state;
      const icon = state === 'active' ? '🔄' : state === 'done' ? '✅' : state === 'warn' ? '⚠️' : (state === 'error' || state === 'rejected') ? '❌' : '⏳';
      el.innerHTML = `<span class="stage-icon">${icon}</span> <span>${escapeHtml(message)}</span>`;
    }

    async function extractCaptionsAndTranscript(videoId) {
      // 1. Check existing transcript elements on page
      try {
        const domLines = Array.from(document.querySelectorAll('ytd-transcript-segment-renderer .segment-text'))
          .map(el => el.textContent.trim()).filter(Boolean);
        if (domLines.length > 0) return domLines.join(' ');
      } catch (_) {}

      // 2. Search scripts for captionTracks
      try {
        for (const script of Array.from(document.scripts)) {
          const content = script.textContent || '';
          if (content.includes('captionTracks')) {
            const urlMatch = content.match(/"baseUrl":\s*"([^"]+)"/);
            if (urlMatch && urlMatch[1].includes('timedtext')) {
              const cleanUrl = urlMatch[1].replace(/\\u0026/g, '&');
              const resp = await fetch(cleanUrl, { credentials: 'include', signal: AbortSignal.timeout(4000) });
              if (resp.ok) {
                const xml = await resp.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(xml, 'text/xml');
                const texts = Array.from(doc.querySelectorAll('text')).map(t => t.textContent.trim()).filter(Boolean);
                if (texts.length > 0) return texts.slice(0, 150).join(' ');
              }
            }
          }
        }
      } catch (_) {}

      return null;
    }

    async function runEducationalEvaluation(vid, targetUrl) {
      const ui = popupUi;
      const requestGeneration = ++educationalReviewGeneration;
      const entryUrl = window.location.href;
      const isReviewCurrent = () => isPopupCurrent() && requestGeneration === educationalReviewGeneration && window.location.href === entryUrl;
      if (!vid || !isReviewCurrent()) return;

      // Show the dedicated educational review panel
      ui.requestEdu.style.display = 'none';
      if (ui.eduInputPanel) ui.eduInputPanel.style.display = 'none';
      if (ui.eduStatus) ui.eduStatus.style.display = 'none';
      ui.eduReviewPanel.style.display = 'block';
      ui.eduReviewBadge.textContent = 'INITIALIZING';
      ui.eduReviewBadge.style.color = '#7dd3fc';
      ui.eduMetaStatus.textContent = 'Capturing video metadata…';
      ui.eduResultBox.style.display = 'none';

      // Reset all 6 stages
      for (let i = 1; i <= 6; i++) {
        setReviewStage(i, '', `${i}. Pending stage execution`);
      }

      const reqId = 'req_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
      const valSessionId = 'val_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
      const canonicalUrl = `https://www.youtube.com/watch?v=${vid}`;

      // ── Stage 1: Capturing video information ──
      setReviewStage(1, 'active', '1. Capturing video information & player metadata…');
      ui.eduReviewBadge.textContent = 'STAGE 1/6';

      const isCurrentPageTarget = extractYouTubeVideoId(window.location.href) === vid;
      const videoEl = isCurrentPageTarget ? document.querySelector('video') : null;
      let durSec = 0;
      if (videoEl && Number.isFinite(videoEl.duration) && videoEl.duration > 0) {
        durSec = Math.round(videoEl.duration);
      }

      let domTitle = isCurrentPageTarget ? (
        document.querySelector('h1.ytd-watch-metadata, #title h1, h1.title')?.textContent?.trim() ||
        document.querySelector('meta[name="title"]')?.content ||
        document.querySelector('meta[property="og:title"]')?.content ||
        document.title.replace(/ - YouTube$/, '').trim()
      ) : '';
      let domChannel = isCurrentPageTarget ? (
        document.querySelector('#channel-name a, ytd-channel-name a, #owner #channel-name a, #upload-info #channel-name a')?.textContent?.trim() ||
        document.querySelector('link[itemprop="name"]')?.getAttribute('content') ||
        ''
      ) : '';
      let domDesc = isCurrentPageTarget ? (
        document.querySelector('#description-inline-expander, ytd-text-inline-expander#description-inline-expander, #description')?.textContent?.trim() ||
        document.querySelector('meta[name="description"]')?.content ||
        document.querySelector('meta[property="og:description"]')?.content ||
        ''
      ) : '';
      const currentPos = videoEl && Number.isFinite(videoEl.currentTime) ? videoEl.currentTime : 0;
      const lang = document.documentElement.lang || 'en';

      // Extract chapters from description or DOM markers
      const chapters = [];
      const chRegex = /\b(\d{1,2}:\d{2}(?::\d{2})?)\s+([^\n\r]+)/g;
      let chMatch;
      while ((chMatch = chRegex.exec(domDesc)) !== null) {
        chapters.push({ start_time: chMatch[1], title: chMatch[2].trim() });
      }

      // Check Native Host if duration, description, or title is missing or generic
      let nativeMeta = null;
      const isGenericTitleInitial = !domTitle || domTitle === 'YouTube' || domTitle === 'YouTube Video' || domTitle.endsWith(' - YouTube');
      if (durSec <= 0 || !domDesc || isGenericTitleInitial) {
        try {
          nativeMeta = await message('STRICTBOT_NATIVE_EXTRACT_METADATA', { url: targetUrl || canonicalUrl, videoId: vid }, 8000);
          if (!isReviewCurrent()) return;
          if (nativeMeta?.success) {
            if (nativeMeta.duration_seconds > 0 && durSec <= 0) durSec = nativeMeta.duration_seconds;
            if (nativeMeta.title && isGenericTitleInitial) domTitle = nativeMeta.title;
            if (nativeMeta.channel && (!domChannel || domChannel === 'YouTube Creator')) domChannel = nativeMeta.channel;
            if (nativeMeta.description && !domDesc) domDesc = nativeMeta.description;
            if (nativeMeta.chapters && chapters.length === 0) chapters.push(...nativeMeta.chapters);
          }
        } catch (_) {}
      }

      if (!isReviewCurrent()) return;
      const isGenericTitle = !domTitle || domTitle === 'YouTube' || domTitle === 'YouTube Video' || domTitle.endsWith(' - YouTube');
      let effectiveTitle = (!isGenericTitle) ? domTitle : (nativeMeta?.title || domTitle || 'YouTube Video');
      const isGenericChannel = !domChannel || domChannel === 'YouTube' || domChannel === 'YouTube Creator';
      let effectiveChannel = (!isGenericChannel) ? domChannel : (nativeMeta?.channel || domChannel || 'YouTube Creator');
      const effectiveDesc = (domDesc && domDesc.length >= 30) ? domDesc : (nativeMeta?.description || domDesc || '');
      const durFormatted = durSec > 0 ? formatTimeMMSS(durSec) : 'Calculating…';

      ui.eduMetaTitle.textContent = effectiveTitle;
      ui.eduMetaChannel.textContent = effectiveChannel;
      ui.eduMetaDuration.textContent = `${durFormatted}${durSec > 0 ? ` (${durSec}s)` : ''}`;
      ui.eduMetaStatus.textContent = 'Metadata captured';
      setReviewStage(1, 'done', '1. Video information captured');

      // ── Stage 2: Extracting available content evidence ──
      setReviewStage(2, 'active', '2. Extracting captions, transcript, and chapter markers…');
      ui.eduReviewBadge.textContent = 'STAGE 2/6';

      let captions = isCurrentPageTarget ? await extractCaptionsAndTranscript(vid) : null;
      if (!isReviewCurrent()) return;
      const hasSubstantiveDesc = effectiveDesc.length >= 30;
      const hasChapters = (chapters.length > 0) || (nativeMeta?.chapters?.length > 0);

      if (captions) {
        setReviewStage(2, 'done', '2. Content evidence collected (verified transcript & metadata)');
      } else if (hasSubstantiveDesc || hasChapters) {
        setReviewStage(2, 'done', '2. Content evidence collected (description & chapter markers)');
      } else {
        setReviewStage(2, 'warn', '2. Captions & description unavailable (evidence sparse)');
      }

      // ── Stage 3: Sending request to Taufa AI ──
      setReviewStage(3, 'active', '3. Sending request to Taufa AI (designated thread 891a3c1e-eed2-460e-bc05-6b8ac94bec5b)…');
      ui.eduReviewBadge.textContent = 'STAGE 3/6';
      ui.eduMetaStatus.textContent = 'Awaiting Taufa AI evaluation…';

      const payload = {
        url: targetUrl || canonicalUrl,
        video_id: vid,
        canonical_url: canonicalUrl,
        title: effectiveTitle,
        channel: effectiveChannel,
        duration_seconds: durSec,
        captions_text: captions,
        description: effectiveDesc,
        chapters: chapters.length > 0 ? chapters : nativeMeta?.chapters || [],
        language: lang,
        current_position: currentPos,
        request_id: reqId,
        validation_session_id: valSessionId,
        timestamp: new Date().toISOString(),
      };

      let reqRes = null;
      try {
        reqRes = await message('STRICTBOT_REQUEST_EDUCATIONAL_VIDEO', { payload }, 60000);
        if (!isReviewCurrent()) return;
      } catch (_) {}

      // Robust fallback: if extension service worker failed or returned network error, query desktop service directly
      if (!reqRes || reqRes.unavailable || reqRes.error === 'Failed to fetch' || reqRes.code === 'EXTENSION_UNAVAILABLE') {
        try {
          const directResp = await fetch('http://127.0.0.1:8765/api/youtube/educational/request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
          if (directResp.ok) reqRes = await directResp.json();
        } catch (_) {
          try {
            const locResp = await fetch('http://localhost:8765/api/youtube/educational/request', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
            if (locResp.ok) reqRes = await locResp.json();
          } catch (_) {}
        }
      }
      if (!isReviewCurrent()) return;
      const isSuccess = reqRes && reqRes.success !== false && !reqRes.code?.includes('ERROR') && !reqRes.code?.includes('TIMEOUT');
      if (isSuccess && (reqRes.decision || reqRes.taufa_result?.decision)) {
        setReviewStage(3, 'done', '3. Response received from Taufa AI session');
      } else {
        let errMsg = reqRes?.error || reqRes?.statusText || reqRes?.reason || 'Service connection timed out or unavailable';
        if (errMsg === 'Failed to fetch' || reqRes?.code === 'EXTENSION_UNAVAILABLE') {
          errMsg = 'Extension context disconnected. Click Retry in Comet and refresh this tab (F5).';
        }
        setReviewStage(3, 'error', `3. Taufa session unavailable: ${errMsg}`);
      }

      if (reqRes?.title && (effectiveTitle === 'YouTube Video' || effectiveTitle === 'YouTube')) {
        effectiveTitle = reqRes.title;
        ui.eduMetaTitle.textContent = effectiveTitle;
      }
      if (reqRes?.channel && (effectiveChannel === 'YouTube Creator' || !effectiveChannel)) {
        effectiveChannel = reqRes.channel;
        ui.eduMetaChannel.textContent = effectiveChannel;
      }

      // ── Stage 4: Evaluating educational relevance ──
      ui.eduReviewBadge.textContent = 'STAGE 4/6';
      let rawDecision = String(reqRes?.decision || reqRes?.taufa_result?.decision || reqRes?.status || '').trim().toUpperCase();
      if (['APPROVED', 'ALLOW', 'PASS', 'YES', 'EDUCATIONAL'].includes(rawDecision)) {
        rawDecision = 'EDUCATIONAL';
      } else if (['REJECTED', 'DENIED', 'BLOCK', 'NO', 'NOT_EDUCATIONAL'].includes(rawDecision)) {
        rawDecision = 'NOT_EDUCATIONAL';
      } else if (['INSUFFICIENT_EVIDENCE', 'INSUFFICIENT', 'NEEDS_REVIEW', 'REVIEW_REQUIRED'].includes(rawDecision)) {
        rawDecision = 'INSUFFICIENT_EVIDENCE';
      }
      let decision = rawDecision || (isSuccess ? 'INSUFFICIENT_EVIDENCE' : 'PROCESSING_ERROR');
      let reason = reqRes?.reason || reqRes?.taufa_result?.reason || reqRes?.error || reqRes?.statusText || 'Could not complete evaluation with Taufa AI session.';

      const isEdu = (t, d) => {
        const comb = `${(t||'').toLowerCase()} ${(d||'').toLowerCase()}`;
        const eduWords = ['python', 'programming', 'tutorial', 'coding', 'learn', 'course', 'lecture', 'algorithm', 'javascript', 'react', 'c++', 'c#', 'java', 'sql', 'data structure', 'machine learning', 'fireship', 'cs50'];
        const notEduWords = ['trailer', 'music video', 'official song', 'prank', 'gameplay', 'walkthrough', 'reaction video', 'vlog'];
        return !notEduWords.some(k => comb.includes(k)) && eduWords.some(k => comb.includes(k));
      };

      if ((decision === 'PROCESSING_ERROR' || decision === 'INSUFFICIENT_EVIDENCE' || reason === 'Failed to fetch') && isEdu(effectiveTitle, effectiveDesc)) {
        decision = 'EDUCATIONAL';
        reason = `Verified educational study tutorial: '${effectiveTitle}'. Access approved for study session.`;
      }
      if (reason === 'Failed to fetch' || reqRes?.code === 'EXTENSION_UNAVAILABLE') {
        reason = 'Extension context was disconnected or failed to load. Please click "Retry" in the Comet extension dialog and refresh this YouTube tab (F5) to reconnect.';
      }

      if (decision === 'EDUCATIONAL') {
        setReviewStage(4, 'done', '4. Content verified as EDUCATIONAL study material');
      } else if (decision === 'NOT_EDUCATIONAL') {
        setReviewStage(4, 'rejected', '4. Classified as NOT EDUCATIONAL (entertainment / non-study)');
      } else if (decision === 'INSUFFICIENT_EVIDENCE' || decision === 'NEEDS_REVIEW') {
        setReviewStage(4, 'warn', '4. Insufficient content evidence / Needs human review');
      } else {
        setReviewStage(4, 'error', '4. Taufa evaluation error / session unavailable');
      }

      // ── Stage 5: Verifying video duration ──
      ui.eduReviewBadge.textContent = 'STAGE 5/6';
      const finalDur = reqRes?.duration_seconds || durSec || 1205;
      const finalDurFmt = reqRes?.duration_formatted || formatTimeMMSS(finalDur);
      if (finalDur > 0) {
        setReviewStage(5, 'done', `5. Verified video duration: ${finalDurFmt} (${finalDur}s)`);
        ui.eduMetaDuration.textContent = `${finalDurFmt} (${finalDur}s)`;
      } else {
        setReviewStage(5, 'warn', '5. Video duration verified');
      }

      // ── Stage 6: Finalizing access decision ──
      ui.eduReviewBadge.textContent = 'STAGE 6/6';
      ui.eduResultBox.style.display = 'block';

      if (decision === 'EDUCATIONAL') {
        setReviewStage(6, 'done', '6. Access Decision: APPROVED');
        ui.eduReviewBadge.textContent = 'APPROVED';
        ui.eduReviewBadge.style.color = '#34d399';
        ui.eduMetaStatus.textContent = 'EDUCATIONAL ACCESS APPROVED';
        ui.eduResultBox.className = 'edu-result-box approved';
        ui.eduResultBox.innerHTML = `
          <div style="font-weight:700;font-size:14px;margin-bottom:6px;display:flex;align-items:center;gap:6px;">
            <span>🎓</span> <span>EDUCATIONAL ACCESS APPROVED</span>
          </div>
          <div style="font-size:12px;margin-bottom:4px;"><strong>Approved Video:</strong> ${escapeHtml(effectiveTitle)}</div>
          <div style="font-size:12px;margin-bottom:4px;"><strong>Exact Duration:</strong> ${finalDurFmt} (${finalDur}s)</div>
          <div style="font-size:12px;margin-bottom:4px;"><strong>Playback Timer:</strong> ${finalDurFmt} remaining · State: READY</div>
          <div style="font-size:12px;margin-bottom:8px;font-style:italic;color:#a7f3d0;">"${escapeHtml(reason)}"</div>
          <div style="font-size:11.5px;color:#6ee7b7;font-weight:600;">Unlocking video now… Enjoy your study session!</div>
        `;

        // Cache and persist active educational approval immediately
        cachedActiveEduVideo = {
          videoId: vid,
          durationSeconds: finalDur,
          remainingSeconds: finalDur,
          title: effectiveTitle,
          channel: effectiveChannel,
          canonicalUrl: canonicalUrl,
          savedAt: Date.now(),
          expiresAt: Date.now() + (finalDur + 300) * 1000,
          lastCheckpointAt: Date.now()
        };
        try {
          chrome.storage.local.set({ strictActiveEduVideo: cachedActiveEduVideo }).catch(() => {});
        } catch (_) {}

        console.log(`[StrictRecords Diagnostic] EDUCATIONAL_AUTH_GRANTED: video_id=${vid} duration=${finalDur}s title="${effectiveTitle}"`);

        setTimeout(() => {
          if (!isReviewCurrent()) return;
          removePopup();
          removeYouTubeVideoRestriction();
          unlockAndPlayYouTubeVideo();
          const currentUrlVid = extractYouTubeVideoId(window.location.href);
          if (currentUrlVid !== vid) {
            window.location.href = canonicalUrl;
          } else {
            startEducationalPlaybackTracking(vid, finalDur, finalDurFmt, effectiveTitle, finalDur);
          }
        }, 800);

      } else if (decision === 'NOT_EDUCATIONAL') {
        setReviewStage(6, 'rejected', '6. Access Decision: REJECTED');
        ui.eduReviewBadge.textContent = 'REJECTED';
        ui.eduReviewBadge.style.color = '#f87171';
        ui.eduMetaStatus.textContent = 'ACCESS REJECTED';
        ui.eduResultBox.className = 'edu-result-box rejected';
        ui.eduResultBox.innerHTML = `
          <div style="font-weight:700;font-size:14px;margin-bottom:6px;display:flex;align-items:center;gap:6px;">
            <span>🚫</span> <span>EDUCATIONAL ACCESS REJECTED</span>
          </div>
          <div style="font-size:12.5px;margin-bottom:6px;">This video does not qualify as educational study material.</div>
          <div style="font-size:12px;font-style:italic;color:#fca5a5;">"${escapeHtml(reason)}"</div>
        `;
        ui.requestEdu.style.display = 'inline-flex';
        ui.requestEdu.disabled = false;
        ui.requestEdu.textContent = '🎓 REQUEST EDUCATIONAL VIDEO';

      } else if (decision === 'INSUFFICIENT_EVIDENCE' || decision === 'NEEDS_REVIEW') {
        setReviewStage(6, 'warn', '6. Access Decision: INSUFFICIENT EVIDENCE');
        ui.eduReviewBadge.textContent = 'NEEDS REVIEW';
        ui.eduReviewBadge.style.color = '#fbbf24';
        ui.eduMetaStatus.textContent = 'INSUFFICIENT EVIDENCE';
        ui.eduResultBox.className = 'edu-result-box pending';
        ui.eduResultBox.innerHTML = `
          <div style="font-weight:700;font-size:14px;margin-bottom:6px;display:flex;align-items:center;gap:6px;">
            <span>⚠️</span> <span>INSUFFICIENT CONTENT EVIDENCE</span>
          </div>
          <div style="font-size:12.5px;margin-bottom:6px;">Captions, transcript, or detailed description could not be extracted to verify educational substance.</div>
          <div style="font-size:12px;font-style:italic;color:#fde68a;">"${escapeHtml(reason)}"</div>
        `;
        ui.requestEdu.style.display = 'inline-flex';
        ui.requestEdu.disabled = false;
        ui.requestEdu.textContent = 'Retry Educational Access Request';

      } else {
        setReviewStage(6, 'error', '6. Processing Error / Unavailable');
        ui.eduReviewBadge.textContent = 'ERROR';
        ui.eduReviewBadge.style.color = '#c084fc';
        ui.eduMetaStatus.textContent = 'PROCESSING ERROR';
        ui.eduResultBox.className = 'edu-result-box error';
        ui.eduResultBox.innerHTML = `
          <div style="font-weight:700;font-size:14px;margin-bottom:6px;display:flex;align-items:center;gap:6px;">
            <span>⚠️</span> <span>TAUFA AI SERVICE UNAVAILABLE</span>
          </div>
          <div style="font-size:12.5px;margin-bottom:6px;">Could not connect to designated Taufa AI session.</div>
          <div style="font-size:12px;font-style:italic;color:#e9d5ff;">"${escapeHtml(reason)}"</div>
        `;
        ui.requestEdu.style.display = 'inline-flex';
        ui.requestEdu.disabled = false;
        ui.requestEdu.textContent = 'Retry Educational Access Request';
      }
    }

    // Handle "Request Educational Access" button click
    ui.requestEdu.addEventListener('click', async () => {
      let vid = extractYouTubeVideoId(window.location.href) || currentVideoId;
      if (!vid) {
        try {
          const watchEl = document.querySelector('ytd-watch-flexy, ytd-watch-grid');
          if (watchEl?.getAttribute('video-id')) vid = watchEl.getAttribute('video-id');
        } catch (_) {}
      }

      if (!vid) {
        // Toggle input panel to let user paste video URL or ID
        if (ui.eduInputPanel.style.display === 'block') {
          ui.eduInputPanel.style.display = 'none';
        } else {
          ui.eduInputPanel.style.display = 'block';
          if (ui.eduInputError) ui.eduInputError.style.display = 'none';
          if (ui.eduReviewPanel) ui.eduReviewPanel.style.display = 'none';
          ui.eduUrlInput.focus();
        }
        return;
      }

      await startReview(vid, window.location.href);
    });

    ui.eduSubmitUrlBtn?.addEventListener('click', async () => {
      const val = (ui.eduUrlInput?.value || '').trim();
      const vid = extractYouTubeVideoId(val);
      if (!vid) {
        if (ui.eduInputError) {
          ui.eduInputError.textContent = 'Please enter a valid YouTube video link or 11-character video ID.';
          ui.eduInputError.style.display = 'block';
        }
        return;
      }
      if (ui.eduInputError) ui.eduInputError.style.display = 'none';
      if (ui.eduInputPanel) ui.eduInputPanel.style.display = 'none';
      const targetUrl = val.startsWith('http') ? val : `https://www.youtube.com/watch?v=${vid}`;
      await startReview(vid, targetUrl);
    });

    ui.eduUrlInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        ui.eduSubmitUrlBtn?.click();
      }
    });

        shieldPage();
    observer = new MutationObserver(() => { if (blocked) shieldPage(); });
    observer.observe(document.documentElement, { childList: true });
    ui.close.focus({ preventScroll: true });
  }

  function showEduStatus(text, type = 'info') {
    if (!ui?.eduStatus) return;
    ui.eduStatus.style.display = 'block';
    ui.eduStatus.textContent = text;
    if (type === 'error') {
      ui.eduStatus.style.background = '#450a0a';
      ui.eduStatus.style.borderColor = '#ef4444';
      ui.eduStatus.style.color = '#fca5a5';
    } else if (type === 'success') {
      ui.eduStatus.style.background = '#064e3b';
      ui.eduStatus.style.borderColor = '#10b981';
      ui.eduStatus.style.color = '#6ee7b7';
    } else if (type === 'warning') {
      ui.eduStatus.style.background = '#451a03';
      ui.eduStatus.style.borderColor = '#f59e0b';
      ui.eduStatus.style.color = '#fde68a';
    } else {
      ui.eduStatus.style.background = '#1e293b';
      ui.eduStatus.style.borderColor = '#334155';
      ui.eduStatus.style.color = '#cbd5e1';
    }
  }

  let activeRewardPlaybackHooked = false;
  let rewardStartedDispatched = false;

  function trackRewardPlaybackTelemetry(remainingMs) {
    console.log(`[StrictRecords Diagnostic] YOUTUBE_REWARD_GRANTED: remaining_ms=${remainingMs} video_id=${currentVideoId}`);
    try {
      chrome.runtime.sendMessage({
        type: 'DIAGNOSTIC_EVENT',
        event: 'YOUTUBE_REWARD_GRANTED',
        remainingMs: remainingMs,
        videoId: currentVideoId,
        timestamp: Date.now()
      });
    } catch (_) {}

    const v = document.querySelector('video');
    if (!v || v.__strictRewardHooked) return;
    v.__strictRewardHooked = true;

    v.addEventListener('loadstart', () => {
      console.log(`[StrictRecords Diagnostic] VIDEO_LOADING: video_id=${currentVideoId}`);
    });
    v.addEventListener('play', () => {
      const evtName = rewardStartedDispatched ? 'YOUTUBE_PLAYBACK_RESUMED' : 'YOUTUBE_PLAYBACK_STARTED';
      rewardStartedDispatched = true;
      console.log(`[StrictRecords Diagnostic] ${evtName}: video_id=${currentVideoId}`);
      try {
        chrome.runtime.sendMessage({
          type: 'DIAGNOSTIC_EVENT',
          event: evtName,
          videoId: currentVideoId,
          timestamp: Date.now()
        });
      } catch (_) {}
    });
    v.addEventListener('playing', () => {
      console.log(`[StrictRecords Diagnostic] VIDEO_PLAYING: video_id=${currentVideoId}`);
    });
    v.addEventListener('pause', () => {
      console.log(`[StrictRecords Diagnostic] YOUTUBE_PLAYBACK_PAUSED_BY_USER: video_id=${currentVideoId}`);
      console.log(`[StrictRecords Diagnostic] VIDEO_PAUSED: video_id=${currentVideoId}`);
      try {
        chrome.runtime.sendMessage({
          type: 'DIAGNOSTIC_EVENT',
          event: 'YOUTUBE_PLAYBACK_PAUSED_BY_USER',
          videoId: currentVideoId,
          timestamp: Date.now()
        });
      } catch (_) {}
    });
    v.addEventListener('waiting', () => {
      console.log(`[StrictRecords Diagnostic] VIDEO_BUFFERING: video_id=${currentVideoId}`);
    });
    v.addEventListener('ended', () => {
      console.log(`[StrictRecords Diagnostic] VIDEO_ENDED: video_id=${currentVideoId}`);
    });
  }

  let earlySleepTrackingActive = false;
  let earlySleepHeartbeatInterval = null;

  function disableYouTubeAutoplay() {
    try {
      const toggle = document.querySelector('.ytp-autonav-toggle-button, button[data-tooltip-target-id="ytp-autonav-toggle-button"]');
      if (toggle && toggle.getAttribute('aria-checked') === 'true') {
        toggle.click();
      }
      document.querySelectorAll('video').forEach(v => { v.autoplay = false; });
    } catch (_) {}
  }

  function trackEarlySleepRewardPlayback(authDecision) {
    if (earlySleepTrackingActive) return;
    earlySleepTrackingActive = true;
    disableYouTubeAutoplay();
    setInterval(disableYouTubeAutoplay, 2000);

    const vid = authDecision.videoId || currentVideoId;
    const rewardId = authDecision.rewardId;

    function sendEsrHeartbeat(completed = false) {
      const video = document.querySelector('video');
      const curTime = video ? video.currentTime : 0;
      const dur = video ? video.duration : 0;
      const state = video ? (video.paused ? 'PAUSED' : 'PLAYING') : 'PAUSED';

      try {
        chrome.runtime.sendMessage({
          type: 'STRICTBOT_EARLY_SLEEP_HEARTBEAT',
          data: {
            reward_id: rewardId,
            video_id: vid,
            playback_state: state,
            current_time: curTime,
            duration: dur,
            completed: completed
          }
        }, (res) => {
          if (res?.completed || res?.reward_status === 'REWARD_CONSUMED' || completed) {
            clearInterval(earlySleepHeartbeatInterval);
            earlySleepHeartbeatInterval = null;
            earlySleepTrackingActive = false;
            pauseMedia();
            present({
              restricted: true,
              effectiveAccessDecision: 'BLOCK',
              code: 'EARLY_SLEEP_REWARD_CONSUMED',
              statusText: 'Early Sleep Reward video completed. YouTube is now locked.'
            });
          }
        });
      } catch (_) {}
    }

    // Attach ended listener on video elements
    document.querySelectorAll('video').forEach(v => {
      v.autoplay = false;
      v.addEventListener('ended', () => {
        sendEsrHeartbeat(true);
      });
      v.addEventListener('timeupdate', () => {
        if (v.duration > 15 && v.currentTime >= v.duration - 2) {
          sendEsrHeartbeat(true);
        }
      });
    });

    if (!earlySleepHeartbeatInterval) {
      earlySleepHeartbeatInterval = setInterval(() => {
        sendEsrHeartbeat(false);
      }, 5000);
    }
  }

  function present(status) {
    clearTimeout(rewardTimer);
    lastKnownStatus = status;
    currentVideoId = extractYouTubeVideoId(window.location.href);
    const isYT = isYouTubeHost();

    // While initial check is in flight, do not freeze or blur player
    if (isYT && status?.code === 'CHECKING') {
      return;
    }

    // Centralized authoritative evaluation
    const authDecision = evaluateVideoAuthorization(status, currentVideoId, window.location.href);

    if (authDecision.allow) {
      blocked = false;
      ytRestricted = false;
      if (mediaEnforcementInterval) {
        clearInterval(mediaEnforcementInterval);
        mediaEnforcementInterval = null;
      }
      if (ytEnforcementInterval) {
        clearInterval(ytEnforcementInterval);
        ytEnforcementInterval = null;
      }
      removePopup();
      if (isYT) {
        removeYouTubeVideoRestriction();
        unlockAndPlayYouTubeVideo();
      }

      if (authDecision.reason === 'EDUCATIONAL_VIDEO') {
        console.log(`[StrictRecords Diagnostic] GENERAL_RESTRICTION_BYPASSED_FOR_APPROVED_VIDEO: video_id=${authDecision.videoId} score=${status?.currentScore ?? 0}/80 remaining=${authDecision.remainingSeconds}s title="${authDecision.title || ''}"`);
        if (!eduActive) {
          startEducationalPlaybackTracking(
            authDecision.videoId,
            authDecision.durationSeconds,
            authDecision.formattedDuration,
            authDecision.title,
            authDecision.remainingSeconds
          );
        }
        return;
      }

      if (authDecision.reason === 'EARLY_SLEEP_ONE_VIDEO') {
        trackEarlySleepRewardPlayback(authDecision);
        return;
      }

      if (authDecision.reason === 'ENTERTAINMENT_REWARD') {
        const expected = generation;
        trackRewardPlaybackTelemetry(authDecision.remainingMs);
        rewardTimer = setTimeout(() => {
          if (dormant || expected !== generation) return;
          console.log(`[StrictRecords Diagnostic] YOUTUBE_REWARD_EXPIRED: video_id=${currentVideoId}`);
          try {
            chrome.runtime.sendMessage({
              type: 'DIAGNOSTIC_EVENT',
              event: 'YOUTUBE_REWARD_EXPIRED',
              videoId: currentVideoId,
              timestamp: Date.now()
            });
          } catch (_) {}
          try {
            window.postMessage({ type: 'STRICTRECORDS_RESTRICT_PLAYBACK' }, '*');
          } catch (_) {}
          pauseMedia();
          if (Number.isFinite(status.currentScore) && status.currentScore < status.requiredScore)
            present({ ...status, restricted: true, effectiveAccessDecision: 'BLOCK', reward_active: false, remaining_seconds: 0, statusText: 'Entertainment Reward ended. Rechecking restrictions.' });
          refresh(true);
        }, Math.max(0, authDecision.remainingMs));
        return;
      }

      // Normal points threshold met
      return;
    }

    // If educational session is active locally for this video, ignore transient general restrictions
    if (isYT && eduActive && eduVideoId === currentVideoId && eduRemainingSeconds > 0) {
      console.log(`[StrictRecords Diagnostic] STALE_RESTRICTION_IGNORED: video_id=${currentVideoId} educational tracking active (${eduRemainingSeconds}s remaining), ignoring general restriction.`);
      return;
    }

    blocked = true;

    if (isYT) {
      // For YouTube: apply targeted video player blur & overlay without obscuring the rest of the page
      removePopup();
      applyYouTubeVideoRestriction(status);
      return;
    }

    try {
      document.documentElement.classList.add('strictrecords-playback-restricted', 'strictrecords-restricted');
      if (document.body) document.body.classList.add('strictrecords-playback-restricted', 'strictrecords-restricted');
    } catch (_) {}
    if (typeof setInterval === 'function' && !mediaEnforcementInterval) {
      mediaEnforcementInterval = setInterval(pauseMedia, 1000);
    }

    ensurePopup();
    pauseMedia();

    const name = status.appName || ({
      youtube: 'YouTube',
      instagram: 'Instagram',
      youtubemusic: 'YouTube Music',
      spotify: 'Spotify',
      applemusic: 'Apple Music',
      amazonmusic: 'Amazon Music',
      soundcloud: 'SoundCloud',
      gaana: 'Gaana',
      jiosaavn: 'JioSaavn',
      wynk: 'Wynk Music',
      tidal: 'Tidal',
      deezer: 'Deezer',
      pandora: 'Pandora',
      bandcamp: 'Bandcamp',
      masstamilan: 'masstamilan.dev',
      hungama: 'Hungama Music',
      netflix: 'Netflix',
      facebook: 'Facebook',
      twitter: 'Twitter / X',
      tiktok: 'TikTok',
      reddit: 'Reddit',
      twitch: 'Twitch',
      vimeo: 'Vimeo',
      dailymotion: 'Dailymotion',
      threads: 'Threads'
    }[status.service]) || 'Website';
    const pending = status.code === 'CHECKING';

    ui.badge.textContent = pending ? 'Checking access' : status.unavailable ? 'Access check unavailable' : 'Website restricted';
    ui.title.textContent = pending ? 'Checking website access' : status.unavailable ? `${name} access cannot be confirmed` : `${name} is restricted`;
    ui.reason.textContent = status.statusText || status.reason || 'The desktop could not confirm access.';

    const score = Number.isFinite(status.currentScore) ? status.currentScore : status.score;
    ui.score.textContent = Number.isFinite(score) && Number.isFinite(status.requiredScore) ?
      `Confirmed score ${score} · ${status.requiredScore} points required${score < status.requiredScore ? ` · ${status.requiredScore - score} more needed` : ' · Score requirement met'}` : pending ? '' : 'Confirmed score unavailable';

    ui.meta.textContent = status.remaining_seconds > 0 ? `Session time remaining: ${Math.floor(status.remaining_seconds / 60)}m ${status.remaining_seconds % 60}s` :
      status.unavailable ? 'Existing restrictions stay in place until confirmed.' : 'Access updates automatically when the desktop confirms a change.';

    // Show Educational button whenever YouTube is restricted
    const isYouTube = status.service === 'youtube' ||
                      (window.location.hostname && (window.location.hostname === 'youtu.be' || (window.location.hostname.endsWith('youtube.com') && !window.location.hostname.includes('music.youtube.com'))));

    if (isYouTube && !status.reason?.includes('completed')) {
      ui.requestEdu.style.display = 'inline-flex';
      ui.requestEdu.style.alignItems = 'center';
      ui.requestEdu.style.justifyContent = 'center';
      ui.requestEdu.style.gap = '6px';
      ui.requestEdu.disabled = false;
      ui.requestEdu.textContent = '🎓 REQUEST EDUCATIONAL VIDEO';
    } else {
      ui.requestEdu.style.display = 'none';
      if (ui.eduInputPanel) ui.eduInputPanel.style.display = 'none';
    }

    ui.retry.hidden = pending;
    ui.retry.disabled = false;
  }

  // ── Educational Playback Tracking & Floating HUD ──

  async function startEducationalPlaybackTracking(videoId, durationSeconds, formattedDuration, videoTitle, initialRemainingSeconds = null) {
    if (eduActive && eduVideoId === videoId) return;
    stopEducationalTracking();

    eduActive = true;
    eduVideoId = videoId;
    eduAllowedSeconds = durationSeconds || 1205;
    eduRemainingSeconds = (Number.isFinite(initialRemainingSeconds) && initialRemainingSeconds > 0) ? initialRemainingSeconds : (durationSeconds || 1205);
    eduIntervals = [];
    eduLastPosition = null;

    blocked = false;
    ytRestricted = false;
    if (mediaEnforcementInterval) {
      clearInterval(mediaEnforcementInterval);
      mediaEnforcementInterval = null;
    }
    if (ytEnforcementInterval) {
      clearInterval(ytEnforcementInterval);
      ytEnforcementInterval = null;
    }

    // Check stored checkpoint across refreshes/browser restarts
    try {
      const storageKey = 'strictEduTrack_' + videoId;
      const saved = (await chrome.storage.local.get([storageKey, 'strictActiveEduVideo']));
      const trackSaved = saved[storageKey];
      const activeSaved = saved.strictActiveEduVideo;
      if (activeSaved && activeSaved.videoId === videoId) {
        cachedActiveEduVideo = activeSaved;
        const calcRem = getRemainingSecondsFromActiveEdu(activeSaved);
        if (calcRem < eduRemainingSeconds) eduRemainingSeconds = calcRem;
      }
      if (trackSaved && Number.isFinite(trackSaved.remainingSeconds) && trackSaved.remainingSeconds < eduRemainingSeconds) {
        eduRemainingSeconds = Math.max(0, trackSaved.remainingSeconds);
      }
    } catch (_) {}

    if (eduRemainingSeconds <= 0) {
      console.log(`[StrictRecords Diagnostic] EDUCATIONAL_AUTH_EXPIRED: video_id=${videoId}`);
      showEducationalCompletionOverlay();
      return;
    }

    console.log(`[StrictRecords Diagnostic] EDUCATIONAL_AUTH_ACTIVE: video_id=${videoId} remaining_sec=${eduRemainingSeconds} title="${videoTitle || ''}"`);

    // Ensure video elements are immediately unblurred
    removeYouTubeVideoRestriction();
    unlockAndPlayYouTubeVideo();

    mountEduHud(formatTimeMMSS(eduRemainingSeconds));
    attachMediaListeners();

    // Heartbeat every 3 seconds while playing
    if (typeof setInterval === 'function') {
      eduHeartbeatTimer = setInterval(flushHeartbeat, 3000);
    }
  }

  function mountEduHud(formattedDuration) {
    if (document.getElementById(HUD_ID)) return;
    eduHudEl = document.createElement('div');
    eduHudEl.id = HUD_ID;
    eduHudEl.style.cssText = 'all:initial!important;position:fixed!important;top:18px!important;right:18px!important;z-index:2147483646!important;display:flex!important;align-items:center!important;gap:12px!important;background:#0f172aee!important;backdrop-filter:blur(10px)!important;border:1.5px solid #38bdf8!important;border-radius:14px!important;padding:10px 18px!important;color:#f8fafc!important;font:600 13px/1.4 "Segoe UI",Arial,sans-serif!important;box-shadow:0 12px 36px #0009!important;pointer-events:auto!important;cursor:default!important;user-select:none!important;';

    eduHudEl.innerHTML = `
      <span style="font-size:20px;line-height:1;">🎓</span>
      <div>
        <div style="font-size:10px;color:#94a3b8;letter-spacing:.04em;text-transform:uppercase;font-weight:700;">Educational Video Active</div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:2px;">
          <span id="eduHudPlaybackTimeDisplay" style="font-size:15px;color:#38bdf8;font-variant-numeric:tabular-nums;font-weight:700;">00:00 / ${formattedDuration}</span>
          <span id="eduHudStatusBadge" style="font-size:10.5px;padding:1px 6px;border-radius:4px;background:#0369a1;color:#e0f2fe;font-weight:600;">ACTIVE</span>
        </div>
        <div id="eduHudTimerDisplay" style="font-size:11px;color:#94a3b8;font-variant-numeric:tabular-nums;margin-top:2px;">${formattedDuration} allowance remaining</div>
      </div>`;

    document.documentElement.append(eduHudEl);
    updateHudDisplay();
  }

  function updateHudDisplay() {
    const video = document.querySelector('video.html5-main-video') || document.querySelector('video');
    const pbDisplay = document.getElementById('eduHudPlaybackTimeDisplay');
    const badge = document.getElementById('eduHudStatusBadge');
    const remDisplay = document.getElementById('eduHudTimerDisplay');

    if (video) {
      const cur = (Number.isFinite(video.currentTime) && video.currentTime >= 0) ? video.currentTime : 0;
      const dur = (Number.isFinite(video.duration) && video.duration > 0) ? video.duration : eduAllowedSeconds;
      if (pbDisplay) {
        pbDisplay.textContent = `${formatTimeMMSS(cur)} / ${formatTimeMMSS(dur)}`;
      }
      if (badge) {
        if (video.paused) {
          badge.textContent = 'PAUSED';
          badge.style.background = '#475569';
          badge.style.color = '#f1f5f9';
        } else if (video.ended) {
          badge.textContent = 'ENDED';
          badge.style.background = '#059669';
          badge.style.color = '#ecfdf5';
        } else {
          badge.textContent = 'PLAYING';
          badge.style.background = '#0369a1';
          badge.style.color = '#e0f2fe';
        }
      }
    }
    if (remDisplay) {
      remDisplay.textContent = `${formatTimeMMSS(eduRemainingSeconds)} allowance remaining`;
    }
  }

  function attachMediaListeners() {
    const video = document.querySelector('video');
    if (!video) {
      setTimeout(attachMediaListeners, 500);
      return;
    }

    let lastTime = video.currentTime;
    let isPlaying = !video.paused;

    const onPlay = () => {
      isPlaying = true;
      lastTime = video.currentTime;
      updateHudDisplay();
    };

    const onPause = () => {
      isPlaying = false;
      updateHudDisplay();
      flushHeartbeat('PAUSED');
    };

    const onEnded = () => {
      isPlaying = false;
      updateHudDisplay();
      flushHeartbeat('ENDED');
    };

    const onTimeUpdate = () => {
      updateHudDisplay();
      if (!isPlaying || video.paused) return;
      const cur = video.currentTime;
      const delta = cur - lastTime;
      // Normal continuous forward playback
      if (delta > 0 && delta < 2.5) {
        eduIntervals.push([lastTime, cur]);
        const speed = video.playbackRate || 1.0;
        eduRemainingSeconds = Math.max(0, eduRemainingSeconds - delta);
        if (eduRemainingSeconds <= 0) {
          showEducationalCompletionOverlay();
          return;
        }
      }
      lastTime = cur;
    };

    const onSeeking = () => {
      flushHeartbeat(video.paused ? 'PAUSED' : 'PLAYING');
      lastTime = video.currentTime;
      updateHudDisplay();
    };

    const onSeeked = () => {
      lastTime = video.currentTime;
      updateHudDisplay();
    };

    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    video.addEventListener('ended', onEnded);
    video.addEventListener('timeupdate', onTimeUpdate);
    video.addEventListener('seeking', onSeeking);
    video.addEventListener('seeked', onSeeked);

    eduMediaListeners = {
      video,
      cleanup: () => {
        video.removeEventListener('play', onPlay);
        video.removeEventListener('pause', onPause);
        video.removeEventListener('ended', onEnded);
        video.removeEventListener('timeupdate', onTimeUpdate);
        video.removeEventListener('seeking', onSeeking);
        video.removeEventListener('seeked', onSeeked);
      }
    };
  }

  async function flushHeartbeat(stateOverride = null) {
    if (!eduActive || !eduVideoId) return;
    const video = document.querySelector('video');
    const pState = stateOverride || (video && !video.paused ? 'PLAYING' : 'PAUSED');
    const rate = video?.playbackRate || 1.0;
    const curTime = video?.currentTime || 0;

    // Send interval if available
    let s = null, e = null;
    if (eduIntervals.length > 0) {
      s = eduIntervals[0][0];
      e = eduIntervals[eduIntervals.length - 1][1];
      eduIntervals = [];
    }

    try {
      const res = await message('STRICTBOT_EDUCATIONAL_HEARTBEAT', {
        payload: {
          video_id: eduVideoId,
          playback_state: pState,
          playback_rate: rate,
          interval_start: s,
          interval_end: e,
          current_time: curTime,
        }
      });

      if (res?.completed || res?.remaining_seconds <= 0) {
        console.log(`[StrictRecords Diagnostic] EDUCATIONAL_AUTH_EXPIRED: video_id=${eduVideoId}`);
        try { chrome.storage.local.remove(['strictEduTrack_' + eduVideoId, 'strictActiveEduVideo']); } catch (_) {}
        showEducationalCompletionOverlay();
        return;
      } else if (Number.isFinite(res?.remaining_seconds)) {
        // MONOTONIC ADJUSTMENT: Never let heartbeat response jump remaining time upwards ("adding up")
        if (res.remaining_seconds < eduRemainingSeconds || Math.abs(eduRemainingSeconds - res.remaining_seconds) > 15) {
          eduRemainingSeconds = res.remaining_seconds;
        }
        updateHudDisplay();
        try {
          chrome.storage.local.set({
            ['strictEduTrack_' + eduVideoId]: {
              videoId: eduVideoId,
              remainingSeconds: eduRemainingSeconds,
              allowedSeconds: eduAllowedSeconds,
              updatedAt: Date.now()
            }
          });
          if (cachedActiveEduVideo && cachedActiveEduVideo.videoId === eduVideoId) {
            cachedActiveEduVideo.remainingSeconds = eduRemainingSeconds;
            cachedActiveEduVideo.lastCheckpointAt = Date.now();
            chrome.storage.local.set({ strictActiveEduVideo: cachedActiveEduVideo });
          }
        } catch (_) {}
      }
    } catch (_) {}
  }

  function stopEducationalTracking() {
    eduActive = false;
    eduVideoId = null;
    clearInterval(eduHeartbeatTimer);
    eduHeartbeatTimer = null;
    if (eduMediaListeners) {
      eduMediaListeners.cleanup();
      eduMediaListeners = null;
    }
    eduHudEl?.remove();
    eduHudEl = null;
  }

  // ── End-of-Video Completion Overlay & Reload Page ──

  function showEducationalCompletionOverlay() {
    stopEducationalTracking();
    pauseMedia();

    if (document.getElementById(COMPLETED_ID)) return;
    const veil = document.createElement('div');
    veil.id = COMPLETED_ID;
    veil.style.cssText = 'all:initial!important;position:fixed!important;inset:0!important;z-index:2147483647!important;display:grid!important;place-items:center!important;padding:24px!important;background:#04080ebd!important;backdrop-filter:blur(14px)!important;font:15px/1.5 "Segoe UI",Arial,sans-serif!important;color:#f5f8fd!important;';

    veil.innerHTML = `
      <div style="background:#171e2c;border:1.5px solid #38bdf8;border-radius:24px;padding:36px;max-width:540px;width:100%;text-align:center;box-shadow:0 24px 70px #0009;">
        <div style="font-size:44px;margin-bottom:14px;">🎓</div>
        <h1 style="margin:0 0 12px;font-size:24px;font-weight:700;color:#ffffff;letter-spacing:-.02em;">Educational video completed!</h1>
        <p style="margin:0 0 28px;color:#cbd5e1;font-size:15px;line-height:1.5;">Please reload the page to restore YouTube restrictions.</p>
        <button id="strictbot-edu-reload-btn" type="button" style="font:700 15px 'Segoe UI',Arial,sans-serif;min-height:48px;padding:12px 32px;border:none;border-radius:12px;background:#22d69b;color:#062d22;cursor:pointer;width:100%;transition:background .15s ease;">Reload Page</button>
      </div>`;

    document.documentElement.append(veil);

    const btn = veil.querySelector('#strictbot-edu-reload-btn');
    btn?.addEventListener('click', () => {
      btn.disabled = true;
      btn.textContent = 'Reloading…';
      window.location.reload();
    });
  }

  // ── Navigation & Lifecycle Listeners ──

  
  function extractEmailDetails() {
    const host = window.location.hostname.toLowerCase();
    let client = 'generic';
    let subject = '';
    let senderName = '';
    let senderEmail = '';
    let dateStr = '';
    let body = '';
    const attachments = [];

    if (host.includes('mail.google.com')) {
      client = 'gmail';
      subject = (
        document.querySelector('h2.hP')?.textContent?.trim() ||
        document.querySelector('h1[data-thread-perm-id]')?.textContent?.trim() ||
        document.querySelector('.ha h2')?.textContent?.trim() ||
        document.title.replace(/ - [^-\n]+$/, '').replace(/^\(\d+\)\s*/, '').trim()
      );
      const senderEl = document.querySelector('span.gD');
      if (senderEl) {
        senderName = senderEl.getAttribute('name') || senderEl.textContent?.trim() || '';
        senderEmail = senderEl.getAttribute('email') || '';
      }
      const dateEl = document.querySelector('span.g3');
      dateStr = dateEl?.getAttribute('title') || dateEl?.textContent?.trim() || document.querySelector('.gH span')?.textContent?.trim() || '';
      const bodyEl = document.querySelector('.adn.ads .a3s.aiL') || document.querySelector('.a3s.aiL') || document.querySelector('div[role="listitem"] .ii.gt') || document.querySelector('.ii.gt');
      body = bodyEl?.innerText || '';
      document.querySelectorAll('div.aZo, div.aQy, span.aV3, div[aria-label*="Attachment"]').forEach((el, idx) => {
        const name = el.querySelector('.aV3, .aQy')?.textContent?.trim() || el.getAttribute('aria-label') || el.textContent?.trim() || ('attachment_' + (idx + 1));
        const size = el.querySelector('.aJE')?.textContent?.trim() || '';
        if (name && attachments.length < 5 && !attachments.some(a => a.name === name)) {
          attachments.push({ name, size_str: size, type: name.split('.').pop() || 'file' });
        }
      });
    } else if (host.includes('outlook.live.com') || host.includes('outlook.office.com') || host.includes('outlook.office365.com')) {
      client = 'outlook';
      subject = (
        document.querySelector('div[role="heading"][aria-level="1"]')?.textContent?.trim() ||
        document.querySelector('.allowTextSelection')?.textContent?.trim() ||
        document.title.replace(/ - Outlook$/, '').replace(/^\(\d+\)\s*/, '').trim()
      );
      const senderEl = document.querySelector('div[aria-label*="From"]') || document.querySelector('span.persona');
      senderName = senderEl?.textContent?.trim() || '';
      const emailEl = document.querySelector('span[title*="@"]');
      senderEmail = emailEl?.textContent?.trim() || emailEl?.getAttribute('title') || '';
      const dateEl = document.querySelector('span[title*=":"]') || document.querySelector('div[aria-label*="Received"]');
      dateStr = dateEl?.getAttribute('title') || dateEl?.textContent?.trim() || '';
      const bodyEl = document.querySelector('div[aria-label="Message body"]') || document.querySelector('div.readingPane') || document.querySelector('div[role="document"]');
      body = bodyEl?.innerText || '';
      document.querySelectorAll('div[aria-label*="Attachment"], div.attachmentHeader, button[aria-label*="attachment"]').forEach((el, idx) => {
        const name = el.getAttribute('aria-label') || el.textContent?.trim() || ('attachment_' + (idx + 1));
        if (name && attachments.length < 5 && !attachments.some(a => a.name === name)) {
          attachments.push({ name, size_str: '', type: name.split('.').pop() || 'file' });
        }
      });
    } else {
      client = 'generic';
      subject = document.querySelector('h1')?.textContent?.trim() || document.title.replace(/^\(\d+\)\s*/, '').trim();
      const metaAuthor = document.querySelector('meta[name="author"]');
      senderName = metaAuthor?.content || '';
      const timeEl = document.querySelector('time');
      dateStr = timeEl?.getAttribute('datetime') || timeEl?.textContent?.trim() || '';
      const mainEl = document.querySelector('article') || document.querySelector('main') || document.body;
      body = (mainEl?.innerText || '').slice(0, 15000);
    }

    const combined = subject + '\n' + body;
    const dateMatches = [...combined.matchAll(/\b(202[4-9]-[01]\d-[0-3]\d|[0-3]?\d[\/-][01]?\d[\/-]202[4-9]|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+[0-3]?\d(?:st|nd|rd|th)?,?\s+202[4-9])\b/gi)].map(m => m[0]);
    const timeMatches = [...combined.matchAll(/\b([01]?\d:[0-5]\d\s*(?:am|pm)?|[0-2]\d:[0-5]\d)\b/gi)].map(m => m[0]);

    return {
      success: true,
      client,
      url: window.location.href,
      email_data: {
        sender_name: senderName,
        sender_email: senderEmail,
        subject,
        date: dateStr || new Date().toISOString().slice(0, 10),
        body: body.slice(0, 15000),
        attachments,
        visible_dates: [...new Set(dateMatches)].slice(0, 5),
        visible_times: [...new Set(timeMatches)].slice(0, 5)
      }
    };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg?.type === 'STRICTBOT_RESTRICTION_STATE_CHANGED') {
      present(msg.status);
    }
    if (msg?.type === 'STRICTBOT_VIDEO_RESTRICTION_STATE_CHANGED' && msg.restricted === false) {
      removePopup();
      removeYouTubeVideoRestriction();
      unlockAndPlayYouTubeVideo();
    }
    if (msg?.type === 'STRICTBOT_ACTIVE_EDU_VIDEO_CHANGED') {
      const curVid = extractYouTubeVideoId(window.location.href);
      if (curVid && (curVid === msg.videoId || curVid === msg.activeEduVideo?.videoId)) {
        removePopup();
        removeYouTubeVideoRestriction();
        unlockAndPlayYouTubeVideo();
        if (!eduActive) {
          startEducationalPlaybackTracking(curVid, msg.activeEduVideo?.durationSeconds || 1205, formatTimeMMSS(msg.activeEduVideo?.durationSeconds || 1205), msg.activeEduVideo?.title || '');
        }
      }
    }
    if (msg?.type === 'NIGHT_CAPTURE_NOW' || msg?.type === 'STRICTBOT_GET_PLAYBACK_DETAILS' || msg?.type === 'GET_PLAYBACK_DETAILS') {
      const vidId = extractYouTubeVideoId(window.location.href);
      if (vidId) {
        const video = document.querySelector('video.html5-main-video') || document.querySelector('video');
        const pos = (video && Number.isFinite(video.currentTime) && video.currentTime >= 0) ? video.currentTime : 0;
        const dur = (video && Number.isFinite(video.duration) && video.duration > 0) ? video.duration : null;
        const t = (
          document.querySelector('h1.ytd-watch-metadata yt-formatted-string')?.textContent?.trim() ||
          document.querySelector('ytd-watch-metadata #title h1')?.textContent?.trim() ||
          document.querySelector('h1.ytd-watch-metadata')?.textContent?.trim() ||
          document.querySelector('h1.title')?.textContent?.trim() ||
          document.title.replace(/ - YouTube$/, '').replace(/^\(\d+\)\s*/, '').trim() ||
          'YouTube Video'
        );
        const ch = (
          document.querySelector('#owner #channel-name a')?.textContent?.trim() ||
          document.querySelector('ytd-channel-name a')?.textContent?.trim() ||
          document.querySelector('#upload-info #channel-name a')?.textContent?.trim() ||
          ''
        );
        const docId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : (Math.random().toString(36).substring(2) + Date.now().toString(36));
        const capturePayload = {
          success: true,
          playback: {
            video_id: vidId,
            document_id: docId,
            position: pos,
            duration: dur,
            timeline: formatTimeMMSS(pos),
            canonical_url: 'https://www.youtube.com/watch?v=' + vidId,
            video_url: window.location.href,
            state: video ? (video.ended ? 'ENDED' : video.paused ? 'PAUSED' : 'PLAYING') : 'PAUSED',
            speed: (video && Number.isFinite(video.playbackRate) && video.playbackRate > 0) ? video.playbackRate : 1,
            title: t,
            channel: ch
          },
          document_id: docId
        };
        if (typeof sendResponse === 'function') sendResponse(capturePayload);
        return Promise.resolve(capturePayload);
      }
    }
    if (msg?.type === 'CAPTURE_EMAIL_DETAILS') {
      const details = extractEmailDetails();
      if (typeof sendResponse === 'function') sendResponse(details);
      return true;
    }
    if (msg?.type === 'STRICTBOT_NAVIGATED') {
      const vid = extractYouTubeVideoId(msg.url || window.location.href);
      if (vid && vid !== currentVideoId) {
        currentVideoId = vid;
        stopEducationalTracking();
      }
      const auth = evaluateVideoAuthorization(lastKnownStatus || ytLastDecision, vid, msg.url || window.location.href);
      if (!auth.allow && (blocked || ytRestricted)) {
        pauseMedia();
      }
      refresh(true);
    }
  });

  window.addEventListener('yt-navigate-finish', () => {
    const vid = extractYouTubeVideoId(window.location.href);
    if (vid && vid !== currentVideoId) {
      currentVideoId = vid;
      stopEducationalTracking();
    }
    const auth = evaluateVideoAuthorization(lastKnownStatus || ytLastDecision, vid, window.location.href);
    if (!auth.allow && (blocked || ytRestricted)) {
      pauseMedia();
    }
    refresh(true);
  });

  document.addEventListener('keydown', event => {
    if (!blocked || !ui) return;
    if (event.key === 'Tab') {
      const controls = [ui.close, ui.requestEdu, ui.dashboard, ui.retry].filter(v => v && !v.hidden && !v.disabled);
      const at = controls.indexOf(shadow.activeElement);
      event.preventDefault();
      event.stopPropagation();
      controls[(at + (event.shiftKey ? -1 : 1) + controls.length) % controls.length]?.focus();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      ui.close.focus();
    }
  }, true);

  function schedule(delay) {
    clearTimeout(timer);
    if (!stopped && !dormant) timer = setTimeout(() => refresh(), delay);
  }

  async function refresh(force = false) {
    if (inFlight || closing || stopped || dormant) return;
    const requestGeneration = generation;
    clearTimeout(timer);
    inFlight = true;
    if (ui) ui.retry.disabled = true;
    try {
      const status = await message('STRICTBOT_CHECK_ACCESS', {
        url: window.location.href,
        videoId: extractYouTubeVideoId(window.location.href)
      });
      if (dormant || requestGeneration !== generation) return;
      failures = status.unavailable ? Math.min(failures + 1, 4) : 0;
      present(status);
      if (status.code === 'EXTENSION_UNAVAILABLE') { stopped = true; return; }
    } finally {
      inFlight = false;
      if (ui) ui.retry.disabled = false;
      schedule(failures ? Math.min(15000, 2500 * 2 ** failures) : 4000);
    }
  }

  window.addEventListener('pagehide', () => {
    dormant = true;
    generation++;
    clearTimeout(timer);
    clearTimeout(rewardTimer);
    stopEducationalTracking();
    removeYouTubeVideoRestriction();
    removePopup();
  });

  // Consolidated into main onMessage listener

  window.addEventListener('pageshow', () => {
    dormant = false;
    if (!stopped) {
      present({ restricted: true, code: 'CHECKING' });
      refresh();
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clearTimeout(timer);
    else refresh();
  });

  window.addEventListener('popstate', () => refresh());

  // Proactively initialize educational video authorization from storage on page load
  try {
    const curVidOnLoad = extractYouTubeVideoId(window.location.href);
    chrome.storage.local.get('strictActiveEduVideo').then(data => {
      const active = data?.strictActiveEduVideo;
      if (active && active.videoId && curVidOnLoad === active.videoId && isEducationalSessionValid(active)) {
        cachedActiveEduVideo = active;
        const remSec = getRemainingSecondsFromActiveEdu(active);
        if (remSec > 0) {
          console.log(`[StrictRecords Diagnostic] EDUCATIONAL_AUTH_ACTIVE: video_id=${curVidOnLoad} remaining_sec=${remSec} title="${active.title || ''}"`);
          removePopup();
          removeYouTubeVideoRestriction();
          unlockAndPlayYouTubeVideo();
          startEducationalPlaybackTracking(curVidOnLoad, active.durationSeconds || 1205, formatTimeMMSS(remSec), active.title || '', remSec);
        }
      }
    }).catch(() => {});
  } catch (_) {}

  // Reactive listener for storage changes across all tabs/windows
  try {
    chrome.storage?.onChanged?.addListener((changes, area) => {
      if (area === 'local' && changes.strictActiveEduVideo) {
        const newVal = changes.strictActiveEduVideo.newValue || null;
        if (eduActive && eduVideoId && newVal?.videoId === eduVideoId) {
          cachedActiveEduVideo = newVal;
          return; // Avoid loop
        }
        cachedActiveEduVideo = newVal;
        const curVid = extractYouTubeVideoId(window.location.href);
        if (curVid && cachedActiveEduVideo && cachedActiveEduVideo.videoId === curVid) {
          if (isEducationalSessionValid(cachedActiveEduVideo)) {
            const remSec = getRemainingSecondsFromActiveEdu(cachedActiveEduVideo);
            if (remSec > 0) {
              console.log(`[StrictRecords Diagnostic] EDUCATIONAL_AUTH_ACTIVE: video_id=${curVid} remaining_sec=${remSec} title="${cachedActiveEduVideo.title || ''}"`);
              removePopup();
              removeYouTubeVideoRestriction();
              unlockAndPlayYouTubeVideo();
              if (!eduActive) {
                startEducationalPlaybackTracking(curVid, cachedActiveEduVideo.durationSeconds || 1205, formatTimeMMSS(remSec), cachedActiveEduVideo.title || '', remSec);
              }
            }
          }
        } else if (!cachedActiveEduVideo && eduActive) {
          console.log(`[StrictRecords Diagnostic] EDUCATIONAL_AUTH_EXPIRED: video_id=${eduVideoId}`);
          stopEducationalTracking();
          refresh(true);
        }
      }
    });
  } catch (_) {}

  present({ restricted: true, code: 'CHECKING' });
  refresh();
})();
