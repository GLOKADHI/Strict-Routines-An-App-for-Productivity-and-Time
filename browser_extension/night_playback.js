/* Reads actual HTMLMediaElement state only on YouTube watch pages in the saved monitoring window. */
(() => {
  if (window.top !== window || globalThis.__strictNightPlayback) return;
  globalThis.__strictNightPlayback = true;
  const documentId = crypto.randomUUID();
  let run = null, minuteTimer = null, tickerTimer = null, monitorTimer = null, shutdownTimer = null;
  let shutdownSent = false, last = '';

  function hms(sec) {
    if (!Number.isFinite(sec) || sec < 0) return '00:00:00';
    const s = Math.floor(sec);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rem = s % 60;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(rem).padStart(2, '0')}`;
  }

  function id() {
    try {
      const u = new URL(location.href);
      if (u.hostname === 'youtu.be') {
        const seg = u.pathname.split('/').filter(Boolean);
        if (seg[0] && /^[A-Za-z0-9_-]{11}$/.test(seg[0])) return seg[0];
      }
      const v = u.searchParams.get('v');
      if (v && /^[A-Za-z0-9_-]{11}$/.test(v)) return v;
      if (u.pathname.includes('/shorts/')) {
        const seg = u.pathname.split('/shorts/')[1]?.split('/')[0];
        if (seg && /^[A-Za-z0-9_-]{11}$/.test(seg)) return seg;
      }
      const segs = u.pathname.split('/').filter(Boolean);
      const idx = segs.findIndex(s => ['shorts', 'embed', 'live', 'v'].includes(s.toLowerCase()));
      if (idx !== -1 && segs[idx + 1] && /^[A-Za-z0-9_-]{11}$/.test(segs[idx + 1])) {
        return segs[idx + 1];
      }
    } catch (_) {}
    const m = String(location.href).match(/(?:v=|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})/);
    return m ? m[1] : null;
  }

  function playback() {
    const videoId = id();
    if (!videoId || !/^[A-Za-z0-9_-]{11}$/.test(videoId)) return null;

    const video = document.querySelector('video.html5-main-video') || document.querySelector('video');

    let pos = 0;
    if (video && Number.isFinite(video.currentTime) && video.currentTime >= 0) {
      pos = video.currentTime;
    } else {
      try {
        const u = new URL(location.href);
        const t = u.searchParams.get('t');
        if (t) {
          const match = t.match(/(\d+)s?$/);
          if (match) pos = parseFloat(match[1]);
        }
      } catch (_) {}
    }

    let dur = null;
    if (video && Number.isFinite(video.duration) && video.duration > 0) {
      dur = video.duration;
    } else {
      const durText = document.querySelector('.ytp-time-duration')?.textContent?.trim();
      if (durText) {
        const parts = durText.split(':').map(Number);
        if (parts.length === 2 && !parts.some(isNaN)) dur = parts[0] * 60 + parts[1];
        else if (parts.length === 3 && !parts.some(isNaN)) dur = parts[0] * 3600 + parts[1] * 60 + parts[2];
      }
    }

    const state = video ? (video.ended ? 'ENDED' : video.paused ? 'PAUSED' : 'PLAYING') : 'PAUSED';
    const speed = (video && Number.isFinite(video.playbackRate) && video.playbackRate > 0) ? video.playbackRate : 1;

    let title = (
      document.querySelector('h1.ytd-watch-metadata yt-formatted-string')?.textContent?.trim() ||
      document.querySelector('ytd-watch-metadata #title h1')?.textContent?.trim() ||
      document.querySelector('h1.ytd-watch-metadata')?.textContent?.trim() ||
      document.querySelector('h1.title')?.textContent?.trim() ||
      document.querySelector('meta[name="title"]')?.content?.trim() ||
      document.querySelector('meta[property="og:title"]')?.content?.trim() ||
      document.title.replace(/ - YouTube$/, '').replace(/^\(\d+\)\s*/, '').trim() ||
      'YouTube Video'
    );
    if (!title || title === 'YouTube') {
      title = document.title.replace(/ - YouTube$/, '').replace(/^\(\d+\)\s*/, '').trim() || 'YouTube Video';
    }

    const channel = (
      document.querySelector('#owner #channel-name a')?.textContent?.trim() ||
      document.querySelector('ytd-channel-name a')?.textContent?.trim() ||
      document.querySelector('#upload-info #channel-name a')?.textContent?.trim() ||
      document.querySelector('link[itemprop="name"]')?.getAttribute('content')?.trim() ||
      ''
    );

    return {
      video_id: videoId,
      document_id: documentId,
      position: pos,
      duration: dur,
      timeline: hms(pos),
      canonical_url: `https://www.youtube.com/watch?v=${videoId}`,
      video_url: location.href,
      state: state,
      speed: speed,
      title: title,
      channel: channel,
      timestamp: Date.now()
    };
  }

  const active = () => run && Date.now() >= run.monitor_ms && Date.now() <= run.shutdown_ms + 10000;

  async function checkShutdownTime() {
    if (run && Date.now() >= run.shutdown_ms && !shutdownSent) {
      shutdownSent = true;
      try {
        await chrome.runtime.sendMessage({ type: 'NIGHT_SHUTDOWN_SIGNAL', run_id: run.run_id });
      } catch (_) {}
    }
  }

  async function send(force = false) {
    checkShutdownTime();
    if (!active()) return;
    const value = playback();
    if (!value) return;
    const key = JSON.stringify(value);
    if (!force && key === last) return;
    last = key;
    try {
      await chrome.runtime.sendMessage({ type: 'NIGHT_CHECKPOINT', playback: value });
    } catch (_) {}
  }

  function scheduleFutureEvents() {
    if (!run) return;
    const now = Date.now();

    // 1. If currently before monitor_ms (11:15pm), schedule exact start
    if (now < run.monitor_ms) {
      clearTimeout(monitorTimer);
      const delay = run.monitor_ms - now;
      monitorTimer = setTimeout(() => {
        send(true);
        startMinuteSync();
      }, delay);
    } else if (now <= run.shutdown_ms + 10000) {
      startMinuteSync();
    }

    // 2. Schedule exact shutdown at shutdown_ms (11:22pm)
    if (now < run.shutdown_ms) {
      clearTimeout(shutdownTimer);
      const sDelay = run.shutdown_ms - now;
      shutdownTimer = setTimeout(() => {
        send(true);
        checkShutdownTime();
      }, sDelay);
    } else {
      checkShutdownTime();
    }
  }

  function startMinuteSync() {
    clearInterval(minuteTimer);
    minuteTimer = null;
    send(true);
    // Every minute (60 seconds) sync the video link and timeline (hh:mm:ss)
    minuteTimer = setInterval(() => {
      if (active()) {
        send(true);
      } else {
        checkShutdownTime();
      }
    }, 60000);
  }

  function setupTimers(enabled) {
    clearInterval(minuteTimer);
    clearInterval(tickerTimer);
    clearTimeout(monitorTimer);
    clearTimeout(shutdownTimer);
    minuteTimer = null;
    tickerTimer = null;

    if (run) {
      scheduleFutureEvents();
      // Safety periodic ticker every 15s to check boundaries and drift
      tickerTimer = setInterval(() => {
        const now = Date.now();
        if (now >= run.shutdown_ms) {
          checkShutdownTime();
        } else if (now >= run.monitor_ms && !minuteTimer) {
          startMinuteSync();
        }
      }, 15000);
    }
  }

  async function hello() {
    try {
      const reply = await chrome.runtime.sendMessage({ type: 'NIGHT_HELLO' });
      run = reply?.run || null;
      setupTimers(reply?.enabled || active());
    } catch (_) {}
  }

    let secondTimer = null;

  function syncPlaybackDetails() {
    try {
      const value = playback();
      if (!value) return;
      globalThis.__strictCurrentPlayback = value;
      try {
        chrome.storage.local.set({
          strictCurrentPlayback: value,
          strictLastPlaybackSync: Date.now()
        });
      } catch (_) {}
      if (active()) {
        send(false);
      }
    } catch (_) {}
  }

  function startSecondTicker() {
    if (secondTimer) return;
    syncPlaybackDetails();
    secondTimer = setInterval(syncPlaybackDetails, 1000);
  }

  chrome.runtime.onMessage.addListener((message, _, reply) => {
    if (message.type === 'NIGHT_MONITOR_CONTEXT') {
      run = message.run;
      setupTimers(message.enabled || active());
      reply({ success: true });
    }
    if (message.type === 'NIGHT_CAPTURE_NOW' || message.type === 'STRICTBOT_GET_PLAYBACK_DETAILS' || message.type === 'GET_PLAYBACK_DETAILS') {
      const value = playback();
      reply(value ? { success: true, playback: value, document_id: documentId, timestamp: Date.now() } : { success: false, error: 'Player state inaccessible' });
    }
  });

  for (const event of ['play', 'pause', 'seeked', 'ratechange', 'ended']) {
    document.addEventListener(event, () => {
      if (active()) send(true);
    }, true);
  }
  document.addEventListener('yt-navigate-finish', () => { last = ''; hello(); });
  window.addEventListener('popstate', hello);
  window.addEventListener('pageshow', hello);
  window.addEventListener('pagehide', () => {
    send(true);
    clearInterval(secondTimer);
    clearInterval(minuteTimer);
    clearInterval(tickerTimer);
    clearTimeout(monitorTimer);
    clearTimeout(shutdownTimer);
    secondTimer = null;
  });
  startSecondTicker();
  hello();
})();
