/**
 * StrictRecords – Global Browser Video Restriction Engine
 * ========================================================
 * Universally monitors and restricts unauthorized video playback across all supported
 * websites including Bing, Perplexity, search engines, external sites, social media platforms,
 * internal file videos exceeding 5 minutes, and embedded players.
 *
 * Requirements:
 * 1. Sun NXT exception: Never block or restrict video playback on sunnxt.com.
 * 2. Internal file videos: Allow clips <= 5 minutes (300s); block videos > 5 minutes.
 * 3. YouTube & 3rd party social media videos: Block playback under discipline policy while
 *    preserving educational video request flow for YouTube.
 * 4. Pure audio streaming platforms (Spotify, etc.) are NOT affected.
 * 5. Universal video player detection (HTML5 <video>, iframes, dynamic elements, shadow DOM).
 * 6. Targeted player blur & sleek overlay — NEVER blocks normal text browsing or reading.
 */

(() => {
  const STANDARD_RESTRICTION_MESSAGE =
    "Media playback is currently restricted by StrictRecords. SUN NXT is permitted daily from 8:25 PM to 9:35 PM. Other media requires an active authorized reward or schedule.";

  const getNowIst = () => {
    try {
      const now = new Date();
      const utcMs = now.getTime() + (now.getTimezoneOffset() * 60000);
      return new Date(utcMs + (5.5 * 3600000));
    } catch (_) {
      return new Date();
    }
  };

  const isSunNxtScheduledWindowActive = () => {
    try {
      const istDate = getNowIst();
      const curSec = istDate.getHours() * 3600 + istDate.getMinutes() * 60 + istDate.getSeconds();
      const startSec = 20 * 3600 + 25 * 60; // 20:25:00
      const endSec = 21 * 3600 + 35 * 60;   // 21:35:00
      return curSec >= startSec && curSec < endSec;
    } catch (_) {
      return false;
    }
  };

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

  // Sun NXT is permitted ONLY during 8:25 PM to 9:35 PM IST (Asia/Kolkata)
  if (isSunNxtHost()) {
    if (isSunNxtScheduledWindowActive()) {
      try {
        document.documentElement.classList.remove('strictrecords-playback-restricted', 'strictrecords-video-restricted', 'strictrecords-restricted');
        if (document.body) {
          document.body.classList.remove('strictrecords-playback-restricted', 'strictrecords-video-restricted', 'strictrecords-restricted');
        }
        document.querySelectorAll('.strictrecords-global-video-overlay').forEach(el => el.remove());
        document.querySelectorAll('.strictrecords-video-restricted').forEach(el => {
          el.classList.remove('strictrecords-video-restricted');
          if (el.tagName === 'VIDEO' || el.tagName === 'AUDIO') {
            try { el.playbackRate = 1; } catch (_) {}
          }
        });
      } catch (_) {}

      // Monitor 9:35 PM transition boundary: at 21:35:00 IST active playback must immediately pause
      const sunNxtTimer = setInterval(() => {
        if (!isSunNxtScheduledWindowActive()) {
          clearInterval(sunNxtTimer);
          try {
            if (document.pictureInPictureElement) document.exitPictureInPicture().catch(() => {});
            if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
          } catch (_) {}
          document.querySelectorAll('video, audio').forEach(el => {
            try { el.pause(); } catch (_) {}
            try { el.muted = true; } catch (_) {}
          });
          isRestricted = true;
          restrictionReason = STANDARD_RESTRICTION_MESSAGE;
          enforceAllVideoPlayers();
        }
      }, 1000);
      return;
    }
  }

  if (globalThis.__strictGlobalVideoEngineInitialized) return;
  globalThis.__strictGlobalVideoEngineInitialized = true;

  const ENGINE_OVERLAY_CLASS = 'strictrecords-global-video-overlay';
  const RESTRICTED_MEDIA_CLASS = 'strictrecords-video-restricted';
  const PROCESSED_ATTR = 'data-strict-processed';

  const isYouTubeHost = () => {
    try {
      const h = (window.location.hostname || '').toLowerCase();
      return h === 'youtu.be' || h.endsWith('youtube.com') || h.endsWith('youtube-nocookie.com');
    } catch (_) { return false; }
  };

  const isSocialVideoHost = () => {
    try {
      const h = (window.location.hostname || '').toLowerCase();
      return (
        h === 'facebook.com' || h.endsWith('.facebook.com') ||
        h === 'fb.watch' || h.endsWith('.fb.watch') ||
        h === 'fb.com' || h.endsWith('.fb.com') ||
        h === 'twitter.com' || h.endsWith('.twitter.com') ||
        h === 'x.com' || h.endsWith('.x.com') ||
        h === 'tiktok.com' || h.endsWith('.tiktok.com') ||
        h === 'reddit.com' || h.endsWith('.reddit.com') ||
        h === 'v.redd.it' || h.endsWith('.v.redd.it') ||
        h === 'redd.it' || h.endsWith('.redd.it') ||
        h === 'twitch.tv' || h.endsWith('.twitch.tv') ||
        h === 'vimeo.com' || h.endsWith('.vimeo.com') ||
        h === 'dailymotion.com' || h.endsWith('.dailymotion.com') ||
        h === 'threads.net' || h.endsWith('.threads.net')
      );
    } catch (_) { return false; }
  };

  function isInternalFileMedia(mediaOrUrl) {
    try {
      if (window.location.protocol !== 'file:') return false;
      let u = '';
      if (typeof mediaOrUrl === 'string') u = mediaOrUrl;
      else if (mediaOrUrl && mediaOrUrl.currentSrc) u = mediaOrUrl.currentSrc;
      else if (mediaOrUrl && mediaOrUrl.src) u = mediaOrUrl.src;
      if (u.startsWith('file:') || u.startsWith('blob:')) return true;
    } catch (_) {}
    return false;
  }

  function isInternalVideoBlocked(video) {
    if (!video) return false;
    const cur = video.currentTime;
    if (Number.isFinite(cur) && cur > 300) return true;
    const dur = video.duration;
    if (Number.isFinite(dur) && dur > 0) {
      return dur > 300;
    }
    // Duration not yet loaded; allow initial loading without prematurely blocking
    return false;
  }

  let isRestricted = false;
  let activeEduVideo = null;
  let currentScore = 0;
  let requiredScore = 80;
  let restrictionReason = STANDARD_RESTRICTION_MESSAGE;
  let checkInFlight = false;
  let lastCheckedTime = 0;

  // ─────────────────────────────────────────────────────────────────────────────
  // 1. STYLESHEET INJECTION
  // ─────────────────────────────────────────────────────────────────────────────
  function ensureStyles() {
    if (isSunNxtHost() && isSunNxtScheduledWindowActive()) return;
    if (document.getElementById('strictrecords-global-video-styles')) return;
    const style = document.createElement('style');
    style.id = 'strictrecords-global-video-styles';
    style.textContent = `
      .${RESTRICTED_MEDIA_CLASS} {
        filter: blur(28px) brightness(0.4) !important;
        -webkit-filter: blur(28px) brightness(0.4) !important;
        pointer-events: none !important;
        user-select: none !important;
      }

      .${ENGINE_OVERLAY_CLASS} {
        position: absolute !important;
        top: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: 100% !important;
        min-height: 140px !important;
        background: rgba(10, 12, 18, 0.94) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        z-index: 2147483645 !important;
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 18px 20px !important;
        box-sizing: border-box !important;
        border-radius: inherit !important;
        border: 1px solid rgba(168, 85, 247, 0.45) !important;
        box-shadow: 0 12px 36px rgba(0, 0, 0, 0.75), inset 0 0 20px rgba(168, 85, 247, 0.15) !important;
        color: #f8fafc !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
        text-align: center !important;
        user-select: none !important;
        pointer-events: auto !important;
        overflow: hidden !important;
      }

      .${ENGINE_OVERLAY_CLASS} * {
        box-sizing: border-box !important;
      }

      .${ENGINE_OVERLAY_CLASS} .srv-badge {
        display: inline-flex !important;
        align-items: center !important;
        gap: 6px !important;
        font-size: 11px !important;
        font-weight: 700 !important;
        letter-spacing: 0.08em !important;
        text-transform: uppercase !important;
        padding: 3px 10px !important;
        border-radius: 9999px !important;
        background: rgba(244, 63, 94, 0.2) !important;
        border: 1px solid rgba(244, 63, 94, 0.5) !important;
        color: #fda4af !important;
        margin-bottom: 8px !important;
      }

      .${ENGINE_OVERLAY_CLASS} .srv-title {
        font-size: 15px !important;
        font-weight: 700 !important;
        color: #ffffff !important;
        margin: 0 0 6px 0 !important;
        line-height: 1.3 !important;
      }

      .${ENGINE_OVERLAY_CLASS} .srv-desc {
        font-size: 12px !important;
        color: #94a3b8 !important;
        margin: 0 0 12px 0 !important;
        max-width: 380px !important;
        line-height: 1.45 !important;
      }

      .${ENGINE_OVERLAY_CLASS} .srv-actions {
        display: flex !important;
        gap: 8px !important;
        flex-wrap: wrap !important;
        justify-content: center !important;
      }

      .${ENGINE_OVERLAY_CLASS} .srv-btn {
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 6px !important;
        padding: 6px 14px !important;
        font-size: 12px !important;
        font-weight: 600 !important;
        border-radius: 6px !important;
        cursor: pointer !important;
        border: none !important;
        transition: all 0.15s ease-in-out !important;
        text-decoration: none !important;
      }

      .${ENGINE_OVERLAY_CLASS} .srv-btn-edu {
        background: #2563eb !important;
        color: #ffffff !important;
        border: 1px solid rgba(59, 130, 246, 0.6) !important;
      }
      .${ENGINE_OVERLAY_CLASS} .srv-btn-edu:hover {
        background: #1d4ed8 !important;
        transform: translateY(-1px) !important;
      }

      .${ENGINE_OVERLAY_CLASS} .srv-btn-dash {
        background: rgba(30, 41, 59, 0.85) !important;
        color: #cbd5e1 !important;
        border: 1px solid rgba(71, 85, 105, 0.7) !important;
      }
      .${ENGINE_OVERLAY_CLASS} .srv-btn-dash:hover {
        background: rgba(51, 65, 85, 0.95) !important;
        color: #ffffff !important;
        transform: translateY(-1px) !important;
      }

      /* Compact mode for small thumbnail video cards */
      .${ENGINE_OVERLAY_CLASS}.srv-compact {
        padding: 6px !important;
      }
      .${ENGINE_OVERLAY_CLASS}.srv-compact .srv-title {
        font-size: 12px !important;
        margin-bottom: 2px !important;
      }
      .${ENGINE_OVERLAY_CLASS}.srv-compact .srv-desc {
        display: none !important;
      }
      .${ENGINE_OVERLAY_CLASS}.srv-compact .srv-badge {
        font-size: 9px !important;
        padding: 2px 6px !important;
        margin-bottom: 4px !important;
      }
      .${ENGINE_OVERLAY_CLASS}.srv-compact .srv-btn {
        padding: 3px 8px !important;
        font-size: 10.5px !important;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // 2. KNOWN VIDEO PLAYER SELECTORS & IFRAME PATTERNS
  // ─────────────────────────────────────────────────────────────────────────────
  const VIDEO_CONTAINER_SELECTORS = [
    // Bing
    '.v_videocard', '.b_vidCard', 'div[data-vid]', '.mc_vtvc', '.ans_vid_card',
    'div[class*="videocard"]', 'div[class*="video_preview"]', '.vid_prev',
    'div.b_vlist2', 'div[data-src*="video"]', '.c_vid',
    // Perplexity
    'div[data-testid*="video"]', 'div.video-embed', 'div.video-player',
    'div[class*="video-embed"]', 'div[class*="VideoCard"]', 'div[class*="video_container"]',
    // Social media video wrappers
    'div[data-pagelet*="Video"]', 'div[role="dialog"] div[data-video-id]',
    'div[data-testid="videoComponent"]', 'div[data-testid="tweetPhoto"]',
    'div[class*="feed-video"]', 'div[class*="tiktok-"] video',
    // Generic & Framework Players
    '.video-js', '.plyr', '.jwplayer', '.flowplayer', '.player-container',
    '.video-container', '.video-wrapper', 'div[class*="video-player"]',
    'div[class*="PlayerContainer"]', 'div[class*="videoWrapper"]'
  ];

  const VIDEO_IFRAME_PATTERNS = [
    'youtube.com/embed/',
    'youtube-nocookie.com/embed/',
    'youtu.be/',
    'player.vimeo.com/video/',
    'vimeo.com/',
    'dailymotion.com/embed/',
    'dailymotion.com/',
    'wistia.net/embed/',
    'streamable.com/e/',
    'player.twitch.tv',
    'twitch.tv/',
    'facebook.com/plugins/video.php',
    'facebook.com/',
    'fb.watch/',
    'fb.com/',
    'tiktok.com/embed/',
    'tiktok.com/',
    'reddit.com/',
    'v.redd.it/',
    'twitter.com/',
    'x.com/',
    'threads.net/'
  ];

  function isVideoIframe(iframe) {
    if (!iframe) return false;
    const src = (iframe.src || iframe.getAttribute('data-src') || '').toLowerCase();
    if (!src) return false;
    return VIDEO_IFRAME_PATTERNS.some(pat => src.includes(pat));
  }

  function extractVideoId(urlStr) {
    if (!urlStr) return null;
    try {
      const u = new URL(urlStr, window.location.origin);
      if (u.hostname.includes('youtu.be')) {
        const seg = u.pathname.split('/').filter(Boolean);
        if (seg[0] && /^[A-Za-z0-9_-]{11}$/.test(seg[0])) return seg[0];
      }
      if (u.hostname.includes('youtube.com')) {
        const v = u.searchParams.get('v');
        if (v && /^[A-Za-z0-9_-]{11}$/.test(v)) return v;
        const segs = u.pathname.split('/').filter(Boolean);
        const idx = segs.findIndex(s => ['shorts', 'embed', 'live', 'v'].includes(s.toLowerCase()));
        if (idx !== -1 && segs[idx + 1] && /^[A-Za-z0-9_-]{11}$/.test(segs[idx + 1])) {
          return segs[idx + 1];
        }
      }
    } catch (_) {}
    const m = String(urlStr).match(/(?:v=|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})/);
    return m ? m[1] : null;
  }

  function isAllowedMedia(mediaOrUrl) {
    if (isSunNxtHost()) return isSunNxtScheduledWindowActive();
    if (!activeEduVideo || !activeEduVideo.videoId) return false;

    // 1. Check current page URL first
    const pageVid = extractVideoId(window.location.href);
    if (pageVid && pageVid === activeEduVideo.videoId) return true;

    // 2. Check media element attributes / source
    let urlStr = '';
    if (typeof mediaOrUrl === 'string') {
      urlStr = mediaOrUrl;
    } else if (mediaOrUrl && typeof mediaOrUrl.getAttribute === 'function') {
      urlStr = mediaOrUrl.getAttribute('data-video-url') ||
               mediaOrUrl.getAttribute('data-video-id') ||
               mediaOrUrl.src ||
               mediaOrUrl.currentSrc || '';
    }
    const vid = extractVideoId(urlStr);
    if (vid && vid === activeEduVideo.videoId) return true;

    // 3. If on YouTube host and active educational video matches page
    try {
      const h = (window.location.hostname || '').toLowerCase();
      if ((h.includes('youtube.com') || h.includes('youtu.be')) && pageVid === activeEduVideo.videoId) {
        return true;
      }
    } catch (_) {}

    return false;
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // 3. RESTRICTION STATE SYNCHRONIZATION
  // ─────────────────────────────────────────────────────────────────────────────
  async function checkVideoAccessState() {
    if (isSunNxtHost()) {
      if (isSunNxtScheduledWindowActive()) {
        isRestricted = false;
        document.documentElement.classList.remove('strictrecords-playback-restricted');
        liftAllVideoRestrictions();
        return;
      } else {
        isRestricted = true;
        restrictionReason = STANDARD_RESTRICTION_MESSAGE;
        enforceAllVideoPlayers();
        return;
      }
    }

    if (window.location.protocol === 'file:') {
      // Local file access: handled element-by-element via 5-minute duration rule
      enforceAllVideoPlayers();
      return;
    }

    if (isYouTubeHost()) return;
    if (checkInFlight) return;
    if (Date.now() - lastCheckedTime < 2500) return;
    checkInFlight = true;
    lastCheckedTime = Date.now();

    try {
      chrome.runtime.sendMessage({
        type: 'STRICTBOT_CHECK_GLOBAL_VIDEO_ACCESS',
        url: window.location.href,
        origin: window.location.origin
      }, (res) => {
        checkInFlight = false;
        if (chrome.runtime.lastError || !res) {
          return;
        }

        if (res.isSunNxt) {
          isRestricted = false;
          document.documentElement.classList.remove('strictrecords-playback-restricted');
          liftAllVideoRestrictions();
          return;
        }

        activeEduVideo = res.activeEduVideo || null;
        currentScore = res.currentScore ?? currentScore;
        requiredScore = res.requiredScore ?? requiredScore;
        restrictionReason = res.reason || '';

        // If this page hosts the approved educational video, do not restrict!
        if (activeEduVideo && activeEduVideo.videoId && isAllowedMedia(window.location.href)) {
          isRestricted = false;
          document.documentElement.classList.remove('strictrecords-playback-restricted');
          liftAllVideoRestrictions();
          return;
        }

        const newRestricted = res.restricted === true;
        if (newRestricted !== isRestricted) {
          isRestricted = newRestricted;
          if (isRestricted && !isYouTubeHost()) {
            document.documentElement.classList.add('strictrecords-playback-restricted');
            enforceAllVideoPlayers();
          } else {
            document.documentElement.classList.remove('strictrecords-playback-restricted');
            liftAllVideoRestrictions();
          }
        } else if (isRestricted && !isYouTubeHost()) {
          enforceAllVideoPlayers();
        }
      });
    } catch (_) {
      checkInFlight = false;
    }
  }

  // Listen for state change broadcasts
  chrome.runtime.onMessage.addListener((message) => {
    if (isSunNxtHost() || isYouTubeHost()) return;
    if (message?.type === 'STRICTBOT_VIDEO_RESTRICTION_STATE_CHANGED') {
      isRestricted = message.restricted === true;
      restrictionReason = message.reason || '';
      if (isRestricted) {
        document.documentElement.classList.add('strictrecords-playback-restricted');
        enforceAllVideoPlayers();
      } else {
        document.documentElement.classList.remove('strictrecords-playback-restricted');
        liftAllVideoRestrictions();
      }
    }
    if (message?.type === 'STRICTBOT_ACTIVE_EDU_VIDEO_CHANGED') {
      activeEduVideo = message.activeEduVideo || null;
      if (activeEduVideo && isAllowedMedia(window.location.href)) {
        isRestricted = false;
        document.documentElement.classList.remove('strictrecords-playback-restricted');
        liftAllVideoRestrictions();
      } else {
        checkVideoAccessState();
      }
    }
  });

  // ─────────────────────────────────────────────────────────────────────────────
  // 4. PLAYER ENFORCEMENT & OVERLAY CREATION
  // ─────────────────────────────────────────────────────────────────────────────
  function findOrCreateContainer(targetEl) {
    if (targetEl.parentElement && targetEl.parentElement.classList.contains(ENGINE_OVERLAY_CLASS)) {
      return targetEl.parentElement;
    }

    // Check if target has a known container parent
    for (const sel of VIDEO_CONTAINER_SELECTORS) {
      const match = targetEl.closest(sel);
      if (match) return match;
    }

    // Default to direct parent
    const parent = targetEl.parentElement;
    if (parent) {
      const style = window.getComputedStyle(parent);
      if (style.position === 'static') {
        parent.style.position = 'relative';
      }
      return parent;
    }
    return targetEl;
  }

  function createRestrictionOverlay(container, targetMedia, isInternal = false) {
    const isSmall = container.offsetWidth > 0 && container.offsetWidth < 320;
    const overlay = document.createElement('div');
    overlay.className = `${ENGINE_OVERLAY_CLASS}${isSmall ? ' srv-compact' : ''}`;
    overlay.setAttribute('role', 'alert');
    overlay.setAttribute('aria-label', isInternal ? 'StrictRecords Internal Video Restriction' : 'StrictRecords Video Restriction');

    if (isInternal) {
      overlay.innerHTML = `
        <div class="srv-badge">🔒 STRICTRECORDS – FILE LIMIT (5 MIN)</div>
        <div class="srv-title">Internal Video Exceeds 5 Minutes</div>
        <p class="srv-desc">
          Local and internal video files longer than 5 minutes (300 seconds) are restricted under StrictRecords discipline policy.<br>
          Only short reference or study clips (&le; 5 mins) are permitted.
        </p>
        <div class="srv-actions">
          <button type="button" class="srv-btn srv-btn-dash" title="Open StrictRecords Desktop Dashboard">
            Open Dashboard
          </button>
        </div>
      `;
    } else {
      overlay.innerHTML = `
        <div class="srv-badge">🔒 STRICTRECORDS – VIDEO RESTRICTED</div>
        <div class="srv-title">Video Playback Blocked</div>
        <p class="srv-desc">
          ${restrictionReason ? escapeHtml(restrictionReason) : 'This video is currently unavailable under your StrictRecords access policy.'}<br>
          Complete your required study session or activities to unlock video access.
        </p>
        <div class="srv-actions">
          <button type="button" class="srv-btn srv-btn-edu" title="Submit video for real-time Taufa AI educational validation">
            🎓 Request Educational Video
          </button>
          <button type="button" class="srv-btn srv-btn-dash" title="Open StrictRecords Desktop Dashboard">
            Open Dashboard
          </button>
        </div>
      `;
    }

    const eduBtn = overlay.querySelector('.srv-btn-edu');
    const dashBtn = overlay.querySelector('.srv-btn-dash');

    if (dashBtn) {
      dashBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        chrome.runtime.sendMessage({ type: 'STRICTBOT_OPEN_DASHBOARD' });
      });
    }

    if (eduBtn) {
      eduBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        triggerEducationalRequestFlow(targetMedia);
      });
    }

    // Intercept clicks and hovers to prevent triggering underlying player
    overlay.addEventListener('click', (e) => { e.stopPropagation(); });
    overlay.addEventListener('mouseenter', (e) => { e.stopPropagation(); });
    overlay.addEventListener('mouseover', (e) => { e.stopPropagation(); });

    return overlay;
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function triggerEducationalRequestFlow(targetMedia) {
    let videoUrl = window.location.href;
    if (targetMedia && targetMedia.tagName === 'IFRAME') {
      videoUrl = targetMedia.src || videoUrl;
    } else if (targetMedia && targetMedia.currentSrc) {
      videoUrl = targetMedia.currentSrc;
    }

    const vid = extractVideoId(videoUrl);
    const targetPromptUrl = vid ? `https://www.youtube.com/watch?v=${vid}` : videoUrl;

    const inputUrl = prompt(
      "🎓 StrictRecords Educational Video Validation\n\nEnter the YouTube video link to request real-time Taufa AI educational verification:",
      targetPromptUrl
    );

    if (!inputUrl) return;

    const cleanVid = extractVideoId(inputUrl);
    if (!cleanVid) {
      alert("Invalid YouTube link. Please provide a valid YouTube video link or ID.");
      return;
    }

    const notice = document.createElement('div');
    notice.style.cssText = `
      position: fixed;
      top: 20px;
      right: 20px;
      background: #0f172a;
      border: 1px solid #38bdf8;
      border-radius: 8px;
      padding: 14px 18px;
      color: #f8fafc;
      font-size: 13px;
      z-index: 2147483647;
      box-shadow: 0 10px 25px rgba(0,0,0,0.8);
      font-family: sans-serif;
    `;
    notice.innerHTML = `<strong>🔄 Submitting to Taufa AI…</strong><div style="font-size:11.5px;color:#94a3b8;margin-top:4px;">Evaluating video educational relevance in designated thread…</div>`;
    document.body.appendChild(notice);

    chrome.runtime.sendMessage({
      type: 'STRICTBOT_REQUEST_EDUCATIONAL_VIDEO',
      payload: {
        url: `https://www.youtube.com/watch?v=${cleanVid}`,
        video_id: cleanVid,
        title: document.title || 'Requested Video'
      }
    }, (resp) => {
      if (notice.parentElement) notice.remove();
      if (resp && resp.decision === 'EDUCATIONAL') {
        alert(`✅ Educational Video Approved!\n\n"${resp.title || cleanVid}" has been validated as educational study material.\nPlayback is now unlocked.`);
        checkVideoAccessState();
      } else {
        const reason = resp?.reason || resp?.error || 'Classified as non-educational entertainment.';
        alert(`❌ Access Denied\n\nTaufa AI evaluation result: ${resp?.decision || 'REJECTED'}\nReason: ${reason}`);
      }
    });
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // 5. ENFORCEMENT ON INDIVIDUAL ELEMENTS
  // ─────────────────────────────────────────────────────────────────────────────
  function enforceVideoElementInternal(video, isInternal) {
    if (!video) return;

    // 1. Halt playback immediately
    try {
      if (document.pictureInPictureElement === video) {
        document.exitPictureInPicture().catch(() => {});
      }
      if (document.fullscreenElement === video) {
        document.exitFullscreen().catch(() => {});
      }
    } catch (_) {}
    try {
      if (!video.paused) {
        video.pause();
      }
    } catch (_) {}
    video.muted = true;
    try { video.volume = 0; } catch (_) {}
    try { video.playbackRate = 0; } catch (_) {}
    try { video.autoplay = false; } catch (_) {}

    // 2. Apply visual blur
    video.classList.add(RESTRICTED_MEDIA_CLASS);

    // 3. Find container and mount overlay
    const container = findOrCreateContainer(video);
    if (!container.querySelector(`:scope > .${ENGINE_OVERLAY_CLASS}`)) {
      const overlay = createRestrictionOverlay(container, video, isInternal);
      container.appendChild(overlay);
    }
  }

  function enforceVideoElement(video) {
    if (!video || (isSunNxtHost() && isSunNxtScheduledWindowActive())) return;
    if (isAllowedMedia(video)) {
      liftVideoElement(video);
      return;
    }

    // Check internal file video condition
    if (isInternalFileMedia(video)) {
      if (!video.__strictInternalBound) {
        video.__strictInternalBound = true;
        const checkInternal = () => {
          if (isInternalVideoBlocked(video)) {
            enforceVideoElementInternal(video, true);
          } else if (Number.isFinite(video.duration) && video.duration > 0 && video.duration <= 300) {
            liftVideoElement(video);
          }
        };
        video.addEventListener('loadedmetadata', checkInternal);
        video.addEventListener('durationchange', checkInternal);
        video.addEventListener('timeupdate', () => {
          if (video.currentTime > 300) checkInternal();
        });
      }

      if (isInternalVideoBlocked(video)) {
        enforceVideoElementInternal(video, true);
      } else {
        liftVideoElement(video);
      }
      return;
    }

    // Social media hosts or standard restricted web videos
    const shouldBlock = isRestricted || isSocialVideoHost();
    if (!shouldBlock) return;

    enforceVideoElementInternal(video, false);
  }

  function enforceIframeElement(iframe) {
    if (!iframe || (isSunNxtHost() && isSunNxtScheduledWindowActive())) return;
    if (!isVideoIframe(iframe)) return;
    if (isAllowedMedia(iframe.src)) return;

    const shouldBlock = isRestricted || isSocialVideoHost();
    if (!shouldBlock) return;

    // Blur iframe
    iframe.classList.add(RESTRICTED_MEDIA_CLASS);

    // Mount click-intercepting overlay on parent
    const container = findOrCreateContainer(iframe);
    if (!container.querySelector(`:scope > .${ENGINE_OVERLAY_CLASS}`)) {
      const overlay = createRestrictionOverlay(container, iframe, false);
      container.appendChild(overlay);
    }
  }

  function liftVideoElement(video) {
    if (!video) return;
    video.classList.remove(RESTRICTED_MEDIA_CLASS);
    try { video.playbackRate = 1; } catch (_) {}

    const container = findOrCreateContainer(video);
    const overlay = container.querySelector(`:scope > .${ENGINE_OVERLAY_CLASS}`);
    if (overlay) overlay.remove();
  }

  function liftIframeElement(iframe) {
    if (!iframe) return;
    iframe.classList.remove(RESTRICTED_MEDIA_CLASS);

    const container = findOrCreateContainer(iframe);
    const overlay = container.querySelector(`:scope > .${ENGINE_OVERLAY_CLASS}`);
    if (overlay) overlay.remove();
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // 6. UNIVERSAL DOM SCANNER & TARGETED TRAVERSAL
  // ─────────────────────────────────────────────────────────────────────────────
  function scanNodeAndDescendants(root) {
    if (!root || isSunNxtHost()) return;
    // On YouTube watch/browse pages, content.js and yt_main_world.js are the primary controllers
    if (isYouTubeHost()) return;
    ensureStyles();

    // Direct video and iframe checks
    if (root.tagName === 'VIDEO') {
      enforceVideoElement(root);
    } else if (root.tagName === 'IFRAME') {
      enforceIframeElement(root);
    }

    // Direct targeted query selector scan
    if (root.querySelectorAll) {
      root.querySelectorAll('video').forEach(enforceVideoElement);
      root.querySelectorAll('iframe').forEach(enforceIframeElement);
    }
  }

  function enforceAllVideoPlayers() {
    if (isSunNxtHost() || isYouTubeHost()) return;
    ensureStyles();
    scanNodeAndDescendants(document.body || document.documentElement);
  }

  function liftAllVideoRestrictions() {
    if (isYouTubeHost()) return;
    document.querySelectorAll('.' + RESTRICTED_MEDIA_CLASS).forEach(el => {
      el.classList.remove(RESTRICTED_MEDIA_CLASS);
      if (el.tagName === 'VIDEO') {
        try { el.playbackRate = 1; } catch (_) {}
      }
    });
    document.querySelectorAll('.' + ENGINE_OVERLAY_CLASS).forEach(el => el.remove());
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // 7. EVENT INTERCEPTION & HOVER PREVIEW SUPPRESSION
  // ─────────────────────────────────────────────────────────────────────────────
  const PLAYBACK_EVENTS = ['play', 'playing', 'canplay', 'loadedmetadata'];
  PLAYBACK_EVENTS.forEach(evt => {
    window.addEventListener(evt, (e) => {
      if (isSunNxtHost() || isYouTubeHost()) return;
      const target = e.target;
      if (target && target.tagName === 'VIDEO') {
        if (isInternalFileMedia(target)) {
          if (isInternalVideoBlocked(target)) {
            e.preventDefault();
            e.stopImmediatePropagation();
            enforceVideoElement(target);
          }
          return;
        }
        if (!isAllowedMedia(target) && (isRestricted || isSocialVideoHost())) {
          e.preventDefault();
          e.stopImmediatePropagation();
          enforceVideoElement(target);
        }
      }
    }, true);
  });

  // Hover-to-preview suppression on Bing / Search Results
  window.addEventListener('mouseenter', (e) => {
    if (isSunNxtHost() || isYouTubeHost()) return;
    const card = e.target?.closest && e.target.closest(VIDEO_CONTAINER_SELECTORS.join(','));
    if (card) {
      const v = card.querySelector('video');
      if (v) enforceVideoElement(v);
    }
  }, true);

  window.addEventListener('mouseover', (e) => {
    if (isSunNxtHost() || isYouTubeHost()) return;
    const card = e.target?.closest && e.target.closest(VIDEO_CONTAINER_SELECTORS.join(','));
    if (card) {
      const v = card.querySelector('video');
      if (v) enforceVideoElement(v);
    }
  }, true);

  // ─────────────────────────────────────────────────────────────────────────────
  // 8. DYNAMIC MUTATION OBSERVER
  // ─────────────────────────────────────────────────────────────────────────────
  let mutationTimeout = null;
  const observer = new MutationObserver((mutations) => {
    if (isSunNxtHost()) return;
    const shouldCheck = isRestricted || isSocialVideoHost() || window.location.protocol === 'file:';
    if (!shouldCheck) return;

    let shouldScan = false;
    for (const mut of mutations) {
      for (const node of mut.addedNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) {
          if (node.classList && node.classList.contains(ENGINE_OVERLAY_CLASS)) continue;
          if (node.tagName === 'VIDEO' || node.tagName === 'IFRAME' || node.querySelector?.('video, iframe')) {
            shouldScan = true;
            break;
          }
        }
      }
      if (shouldScan) break;
    }

    if (shouldScan) {
      if (mutationTimeout) cancelAnimationFrame(mutationTimeout);
      mutationTimeout = requestAnimationFrame(() => {
        enforceAllVideoPlayers();
      });
    }
  });

  // ─────────────────────────────────────────────────────────────────────────────
  // 9. INITIALIZATION
  // ─────────────────────────────────────────────────────────────────────────────

  function renderInPageSensitiveCaptureRiskBanner(status) {
    try {
      let pill = document.getElementById('strictSensitiveCaptureRiskPill');
      const showRisk = status && (status.capture_risk_active || status.windows_capture_state_unknown);
      if (!showRisk) {
        if (pill) pill.remove();
        return;
      }
      if (!pill) {
        pill = document.createElement('div');
        pill.id = 'strictSensitiveCaptureRiskPill';
        pill.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:2147483646;max-width:380px;background:#0f172a;border:2px solid #ef4444;border-radius:10px;padding:12px 14px;color:#f8fafc;font-family:system-ui,-apple-system,sans-serif;font-size:12px;box-shadow:0 14px 30px rgba(0,0,0,0.75);pointer-events:none;';
        (document.body || document.documentElement).appendChild(pill);
      }
      const pages = Array.isArray(status.sensitive_pages_summary) ? status.sensitive_pages_summary : [];
      const caps = Array.isArray(status.capture_activity_summary) ? status.capture_activity_summary : [];
      pill.innerHTML = `
        <div style="font-weight:800;color:#f87171;font-size:12px;margin-bottom:4px;">⚠️ ${status.risk_alert_heading || 'SENSITIVE PAGE + SCREEN RECORDING DETECTED'}</div>
        <div style="color:#fecaca;font-size:11px;line-height:1.35;margin-bottom:6px;">${status.risk_alert_body || 'Close all active screen-recording and screen-sharing applications before continuing.'}</div>
        <div style="color:#cbd5e1;font-size:10.5px;">
          <div><strong>Pages:</strong> ${pages.map(p => `${p.label} (${p.status})`).join(', ') || 'Protected'}</div>
          <div><strong>Capture:</strong> ${caps.map(c => `${c.name} (${c.classification || c.status})`).join(', ') || 'Detected'}</div>
          <div><strong>StrictRecords visual capture:</strong> PAUSED (${status.cooldown_formatted || '05:00'})</div>
        </div>
      `;
    } catch (_) {}
  }

  window.addEventListener('message', (ev) => {
    if (ev?.data?.type === 'STRICTBOT_DISPLAY_MEDIA_STATE') {
      try {
        chrome.runtime?.sendMessage?.({
          type: 'STRICTBOT_BROWSER_CAPTURE_EVENT',
          is_active: Boolean(ev.data.is_active),
          capture_id: ev.data.capture_id || 'displayMedia',
          source_type: ev.data.source_type || 'SCREEN_SHARING',
          display_name: 'Browser screen sharing'
        }, (resp) => {
          void chrome.runtime?.lastError;
          if (resp) renderInPageSensitiveCaptureRiskBanner(resp);
        });
      } catch (_) {}
    }
  });

  try {
    chrome.runtime?.onMessage?.addListener((msg) => {
      if (msg?.type === 'STRICTBOT_SENSITIVE_PROTECTION_STATE_UPDATED' && msg.status) {
        renderInPageSensitiveCaptureRiskBanner(msg.status);
      }
    });
  } catch (_) {}

  function reportFrameOriginForSensitiveProtection() {
    try {
      const h = (window.location.hostname || '').toLowerCase();
      if (!h || h === '127.0.0.1' || h === 'localhost') return;
      if (
        h === 'checkout.stripe.com' || h === 'buy.stripe.com' || h === 'billing.stripe.com' ||
        h === 'pay.stripe.com' || h === 'checkout.razorpay.com' || h === 'api.razorpay.com' ||
        h === 'checkout.paddle.com' || h === 'checkout.adyen.com' ||
        h.startsWith('checkout.') || h.startsWith('pay.') || h.startsWith('payment.')
      ) {
        chrome.runtime?.sendMessage?.({
          type: 'STRICTBOT_SENSITIVE_FRAME_ORIGIN',
          origin: window.location.origin
        }, () => { void chrome.runtime?.lastError; });
      }
    } catch (_) {}
  }

  function init() {
    if (isSunNxtHost()) return;
    reportFrameOriginForSensitiveProtection();
    ensureStyles();
    checkVideoAccessState();

    if (document.documentElement) {
      observer.observe(document.documentElement, {
        childList: true,
        subtree: true
      });
    }

    // Periodic check
    setInterval(checkVideoAccessState, 15000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
