// StrictRecords - Main World Playback Hook
// Runs in the webpage execution context (main world) to directly intercept HTMLMediaElement and Web Audio playback
(() => {
  function isSunNxt() {
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
  }

  // Sun NXT is explicitly permitted under user policy — never hook or restrict Sun NXT!
  if (isSunNxt()) {
    try {
      document.documentElement.classList.remove('strictrecords-playback-restricted', 'strictrecords-video-restricted', 'strictrecords-restricted', 'strictrecords-yt-restricted');
      if (document.body) {
        document.body.classList.remove('strictrecords-playback-restricted', 'strictrecords-video-restricted', 'strictrecords-restricted', 'strictrecords-yt-restricted');
      }
    } catch (_) {}
    return;
  }

  let playbackExplicitlyAllowed = false;

  function isPlaybackRestricted() {
    if (playbackExplicitlyAllowed) return false;
    if (isSunNxt()) return false;
    const el = document.documentElement;
    return !!(el && (
      el.classList.contains('strictrecords-yt-restricted') ||
      el.classList.contains('strictrecords-playback-restricted') ||
      el.classList.contains('strictrecords-restricted')
    ));
  }

  function isInternalFileMedia(media) {
    try {
      if (window.location.protocol !== 'file:') return false;
      const src = (media && (media.currentSrc || media.src || '')) || '';
      return src.startsWith('file:') || src.startsWith('blob:');
    } catch (_) { return false; }
  }

  function isInternalVideoBlocked(media) {
    if (!media) return false;
    const cur = media.currentTime;
    if (Number.isFinite(cur) && cur > 300) return true;
    const dur = media.duration;
    if (Number.isFinite(dur) && dur > 0) {
      return dur > 300;
    }
    // Duration not yet loaded; allow initial metadata loading without premature block
    return false;
  }

  function isPlaybackRestrictedForMedia(media) {
    if (playbackExplicitlyAllowed) return false;
    if (isSunNxt()) return false;

    // Distinguish Audio vs Video:
    // Pure <audio> playback (Spotify, Apple Music, JioSaavn, etc.) is governed by the music policy
    // and must NOT be broken by video-specific playback restrictions ('strictrecords-playback-restricted'
    // or 'strictrecords-yt-restricted'). Audio elements are only restricted when full site shield is active.
    if (media && media.tagName === 'AUDIO') {
      const el = document.documentElement;
      return !!(el && el.classList.contains('strictrecords-restricted'));
    }

    // Video elements:
    if (media && media.tagName === 'VIDEO' && isInternalFileMedia(media)) {
      return isInternalVideoBlocked(media);
    }

    return isPlaybackRestricted();
  }

  try {
    function observePlaybackOutcome(result) {
      // Mark expected autoplay/policy denials as handled, while returning the
      // original rejected promise so callers can still detect playback failure.
      if (result && typeof result.catch === 'function') {
        result.catch(() => {});
      }
      return result;
    }
    // 1. Intercept HTMLMediaElement.prototype.play
    const origPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function() {
      if (isSunNxt()) {
        return origPlay.apply(this, arguments);
      }

      // Dynamic duration monitoring for internal file videos
      if (this && this.tagName === 'VIDEO' && isInternalFileMedia(this)) {
        if (!this.__strictInternalHooked) {
          this.__strictInternalHooked = true;
          const checkDuration = () => {
            if (isInternalVideoBlocked(this)) {
              try { this.pause(); } catch (_) {}
              this.muted = true;
              try { this.volume = 0; } catch (_) {}
              try { this.playbackRate = 0; } catch (_) {}
            }
          };
          this.addEventListener('loadedmetadata', checkDuration, { once: true });
          this.addEventListener('durationchange', checkDuration);
          this.addEventListener('timeupdate', () => {
            if (this.currentTime > 300) checkDuration();
          });
        }
      }

      if (isPlaybackRestrictedForMedia(this)) {
        try { this.pause(); } catch (_) {}
        this.muted = true;
        try { this.volume = 0; } catch (_) {}
        try { this.playbackRate = 0; } catch (_) {}
        return observePlaybackOutcome(Promise.reject(new DOMException('Playback restricted by StrictRecords', 'NotAllowedError')));
      }
      return observePlaybackOutcome(origPlay.apply(this, arguments));
    };

    // 2. Intercept HTMLMediaElement.prototype.autoplay
    const origAutoplay = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'autoplay');
    if (origAutoplay && origAutoplay.set) {
      Object.defineProperty(HTMLMediaElement.prototype, 'autoplay', {
        set(val) {
          if (!isSunNxt() && isPlaybackRestrictedForMedia(this)) {
            return origAutoplay.set.call(this, false);
          }
          return origAutoplay.set.call(this, val);
        },
        get() {
          return origAutoplay.get.call(this);
        },
        configurable: true
      });
    }

    // 3. Intercept Web Audio AudioContext / webkitAudioContext
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (AudioCtx) {
      const origResume = AudioCtx.prototype.resume;
      if (origResume) {
        AudioCtx.prototype.resume = function() {
          if (!isSunNxt() && isPlaybackRestricted()) {
            try { this.suspend().catch(() => {}); } catch (_) {}
            return observePlaybackOutcome(Promise.reject(new DOMException('AudioContext playback restricted by StrictRecords', 'NotAllowedError')));
          }
          return observePlaybackOutcome(origResume.apply(this, arguments));
        };
      }
    }

    // 4. Intercept YouTube movie_player if present
    function hookYouTubePlayerApi() {
      try {
        const mp = document.getElementById('movie_player');
        if (mp && !mp.__strictHooked) {
          mp.__strictHooked = true;
          const origPlayVideo = mp.playVideo;
          if (typeof origPlayVideo === 'function') {
            mp.playVideo = function() {
              if (isPlaybackRestricted()) {
                try { mp.pauseVideo(); } catch (_) {}
                return;
              }
              return origPlayVideo.apply(this, arguments);
            };
          }
        }
      } catch (_) {}
    }

    hookYouTubePlayerApi();
    window.addEventListener('yt-navigate-finish', hookYouTubePlayerApi, true);
    window.addEventListener('load', hookYouTubePlayerApi, true);
    let hookAttempts = 0;
    const hookTimer = setInterval(() => {
      hookAttempts++;
      hookYouTubePlayerApi();
      const mp = document.getElementById('movie_player');
      if ((mp && mp.__strictHooked) || hookAttempts >= 10) {
        clearInterval(hookTimer);
      }
    }, 1000);

    // 5. Watch for restriction class additions and immediately silence active media
    const observer = new MutationObserver(() => {
      if (isSunNxt()) return;
      try {
        document.querySelectorAll('audio,video').forEach(m => {
          if (isPlaybackRestrictedForMedia(m)) {
            try {
              if (!m.paused) m.pause();
              m.muted = true;
              m.volume = 0;
              m.playbackRate = 0;
            } catch (_) {}
          }
        });
      } catch (_) {}
    });
    if (document.documentElement) {
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    }

    // 6. Direct Main-World Unpause and Player Unlock Listener
    window.addEventListener('message', (e) => {
      if (e.data && e.data.type === 'STRICTRECORDS_RESTRICT_PLAYBACK') {
        playbackExplicitlyAllowed = false;
      }
      if (e.data && e.data.type === 'STRICTRECORDS_UNLOCK_PLAYBACK') {
        try {
          playbackExplicitlyAllowed = true;
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
          // Lift media restriction and restore normal playbackRate without forcing playback
          document.querySelectorAll('video, audio').forEach(m => {
            try {
              m.muted = false;
              if (m.playbackRate === 0) m.playbackRate = 1;
              // CRITICAL: Do NOT call m.play()! User has full manual control over play/pause.
            } catch (_) {}
          });
          // CRITICAL: Do NOT call mp.playVideo() or click playBtn!
          // Unlocking only removes restrictions and restores natural user control.
        } catch (_) {}
      }
    });

    // 7. Browser Screen-Sharing / Display-Media Lifecycle Observer (Sensitive Page Protection)
    try {
      if (navigator.mediaDevices && typeof navigator.mediaDevices.getDisplayMedia === 'function') {
        const origGetDisplayMedia = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getDisplayMedia = async function (...args) {
          const stream = await origGetDisplayMedia(...args);
          const capId = 'displayMedia_' + Date.now();
          try {
            window.postMessage({
              type: 'STRICTBOT_DISPLAY_MEDIA_STATE',
              is_active: true,
              capture_id: capId,
              source_type: 'SCREEN_SHARING'
            }, '*');
            const tracks = stream && typeof stream.getTracks === 'function' ? stream.getTracks() : [];
            const checkEnded = () => {
              const anyLive = tracks.some(t => t.readyState === 'live');
              if (!anyLive) {
                window.postMessage({
                  type: 'STRICTBOT_DISPLAY_MEDIA_STATE',
                  is_active: false,
                  capture_id: capId,
                  source_type: 'SCREEN_SHARING'
                }, '*');
              }
            };
            tracks.forEach(t => {
              t.addEventListener('ended', checkEnded);
              t.addEventListener('mute', checkEnded);
            });
          } catch (_) {}
          return stream;
        };
      }
    } catch (_) {}
  } catch (_) {}
})();
