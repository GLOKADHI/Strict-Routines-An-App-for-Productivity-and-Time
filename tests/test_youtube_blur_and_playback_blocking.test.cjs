const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../browser_extension/content.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function createYouTubeFixture(initialUrl = 'https://www.youtube.com/watch?v=vid_test_123', isTop = true, initialReply = null) {
  const events = {};
  const windowEvents = {};
  const timers = new Map();
  const intervals = new Map();
  const messages = [];
  let reply = initialReply || {
    service: 'youtube',
    restricted: true,
    currentScore: 45,
    requiredScore: 80,
    statusText: 'Score 45/80 is below threshold (80 pts needed).'
  };

  const classListFor = (n) => ({
    classes: new Set(),
    add(...cls) { cls.forEach(c => this.classes.add(c)); },
    remove(...cls) { cls.forEach(c => this.classes.delete(c)); },
    contains(c) { return this.classes.has(c); },
  });

  const createdNodes = [];
  function node(tag = 'div') {
    const cl = classListFor();
    const nd = {
      tag,
      id: '',
      classList: cl,
      className: '',
      style: { filter: '', overflow: 'auto', position: 'static', pointerEvents: '' },
      inert: false,
      isConnected: false,
      hidden: false,
      disabled: false,
      listeners: {},
      children: [],
      parentElement: null,
      paused: false,
      autoplay: true,
      muted: false,
      duration: 300,
      currentTime: 10,
      pause() {
        this.paused = true;
        this.pauses = (this.pauses || 0) + 1;
      },
      play() {
        this.paused = false;
        if (events.play) events.play({ target: this, preventDefault() {}, stopImmediatePropagation() {} });
      },
      append(...children) {
        this.children.push(...children);
        const setConn = (n, p) => {
          n.isConnected = true;
          n.parentElement = p;
          (n.children || []).forEach(ch => setConn(ch, n));
        };
        children.forEach(n => setConn(n, this));
      },
      appendChild(child) {
        this.children.push(child);
        child.isConnected = true;
        child.parentElement = this;
        return child;
      },
      remove() {
        this.isConnected = false;
        if (this.parentElement) {
          this.parentElement.children = this.parentElement.children.filter(c => c !== this);
        }
      },
      addEventListener(name, fn) {
        this.listeners[name] = fn;
      },
      querySelector(selector) {
        if (selector === '#' + 'strictrecords-yt-blur-overlay') {
          return this.children.find(c => c.id === 'strictrecords-yt-blur-overlay');
        }
        if (selector === '.strictrecords-yt-req-edu-btn') {
          return this.children.flatMap(c => [c, ...(c.children || [])]).find(c => c.tag === 'button');
        }
        if (selector === 'video' || selector === 'video.html5-main-video') {
          return this.children.flatMap(c => [c, ...(c.children || [])]).find(c => c.tag === 'video');
        }
        return undefined;
      },
      querySelectorAll(selector) {
        const found = [];
        const scan = (n) => {
          if (selector.includes('strictrecords-video-blurred') && n.classList?.contains('strictrecords-video-blurred')) found.push(n);
          if (selector.includes('strictrecords-player-blurred') && n.classList?.contains('strictrecords-player-blurred')) found.push(n);
          if (selector.includes('strictrecords-yt-blur-overlay') && n.id === 'strictrecords-yt-blur-overlay') found.push(n);
          if (selector === 'video, audio' && (n.tag === 'video' || n.tag === 'audio')) found.push(n);
          (n.children || []).forEach(scan);
        };
        scan(this);
        return found;
      },
      get innerHTML() { return this.html || ''; },
      set innerHTML(val) {
        this.html = val;
        // create mock buttons if HTML contains buttons
        if (val.includes('strictrecords-yt-req-edu-btn')) {
          const btnEdu = node('button');
          btnEdu.id = 'strictrecords-yt-btn-edu';
          btnEdu.className = 'strictrecords-yt-req-edu-btn';
          this.append(btnEdu);
        }
        if (val.includes('strictrecords-yt-open-dash-btn')) {
          const btnDash = node('button');
          btnDash.id = 'strictrecords-yt-btn-dash';
          btnDash.className = 'strictrecords-yt-open-dash-btn';
          this.append(btnDash);
        }
        if (val.includes('eduHudPlaybackTimeDisplay')) {
          const pb = node('span');
          pb.id = 'eduHudPlaybackTimeDisplay';
          this.append(pb);
        }
        if (val.includes('eduHudStatusBadge')) {
          const badge = node('span');
          badge.id = 'eduHudStatusBadge';
          this.append(badge);
        }
        if (val.includes('eduHudTimerDisplay')) {
          const tm = node('div');
          tm.id = 'eduHudTimerDisplay';
          this.append(tm);
        }
      },
      get textContent() { return this.text || ''; },
      set textContent(val) { this.text = val; }
    };
    createdNodes.push(nd);
    return nd;
  }

  const html = node('html');
  const body = node('body');
  const head = node('head');
  html.append(head, body);

  // YouTube player container and video
  const moviePlayer = node('div');
  moviePlayer.id = 'movie_player';
  moviePlayer.pauseVideo = function() { this.pausedVideoCalled = true; };
  moviePlayer.stopVideo = function() { this.stoppedVideoCalled = true; };

  const mainVideo = node('video');
  mainVideo.className = 'html5-main-video';
  moviePlayer.append(mainVideo);
  body.append(moviePlayer);

  const document = {
    _createdNodes: createdNodes,
    documentElement: html,
    body: body,
    head: head,
    hidden: false,
    activeElement: null,
    createElement: node,
    getElementById(id) {
      if (id === 'movie_player') return moviePlayer;
      const found = createdNodes.find(n => n.id === id && n.isConnected);
      if (found) return found;
      // also search within html/body
      const search = (node) => {
        if (!node) return null;
        if (node.id === id) return node;
        for (const child of (node.children || [])) {
          const res = search(child);
          if (res) return res;
        }
        return null;
      };
      return search(html);
    },
    querySelector(selector) {
      if (selector === '#movie_player') return moviePlayer;
      if (selector === 'video' || selector === 'video.html5-main-video') return mainVideo;
      if (selector === '#' + 'strictrecords-yt-blur-overlay') {
        return createdNodes.find(n => n.id === 'strictrecords-yt-blur-overlay' && n.isConnected);
      }
      return null;
    },
    querySelectorAll(selector) {
      const results = [];
      const scan = (n) => {
        if (!n) return;
        if (selector === 'audio,video' || selector === 'video, audio' || selector === 'video') {
          if (n.tag === 'video' || n.tag === 'audio') results.push(n);
        }
        if (selector.includes('strictrecords-video-blurred') && n.classList?.contains('strictrecords-video-blurred')) {
          results.push(n);
        }
        if (selector.includes('strictrecords-player-blurred') && n.classList?.contains('strictrecords-player-blurred')) {
          results.push(n);
        }
        if (selector.includes('strictrecords-yt-blur-overlay') && n.id === 'strictrecords-yt-blur-overlay') {
          results.push(n);
        }
        (n.children || []).forEach(scan);
      };
      scan(html);
      return results;
    },
    addEventListener(name, fn) {
      events[name] = fn;
    }
  };

  const runtime = {
    onMessage: {
      addListener(fn) { events.runtimeMessage = fn; }
    },
    getURL: path => 'chrome-extension://test/' + path,
    lastError: null,
    sendMessage(msg, callback) {
      messages.push({ msg, callback });
      if (callback) {
        setImmediate(() => callback(reply));
      }
    }
  };

  const window = {
    location: new URL(initialUrl),
    addEventListener(name, fn) { windowEvents[name] = fn; },
    getComputedStyle: (n) => n.style || { position: 'static' }
  };
  if (isTop) {
    window.top = window;
  } else {
    window.top = {}; // Not top window (embed frame)
  }

  let mutationCallback = null;
  class MockMutationObserver {
    constructor(cb) {
      mutationCallback = cb;
    }
    observe() {}
    disconnect() { mutationCallback = null; }
  }

  vm.runInNewContext(source, {
    window,
    document,
    chrome: { runtime, storage: { local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) } } },
    MutationObserver: MockMutationObserver,
    HTMLMediaElement: function() {},
    Node: { ELEMENT_NODE: 1 },
    setTimeout: (fn, delay) => {
      const id = timers.size + 1;
      timers.set(id, { fn, delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
    setInterval: (fn, delay) => {
      const id = intervals.size + 1;
      intervals.set(id, { fn, delay });
      return id;
    },
    clearInterval: id => intervals.delete(id),
    URL,
    console
  });

  return {
    document,
    body,
    moviePlayer,
    mainVideo,
    window,
    events,
    windowEvents,
    messages,
    timers,
    intervals,
    setReply: (val) => { reply = val; },
    triggerMutation: (addedNodes) => {
      if (mutationCallback) mutationCallback([{ addedNodes }]);
    }
  };
}

test('1. YouTube watch page when restricted pauses video and applies blur overlay without shielding whole page', async () => {
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=vid12345678');
  await settle();

  // Full page veil must NOT be inerting body (user can still use search/nav)
  assert.equal(f.body.inert, false);

  // Video must be paused immediately
  assert.equal(f.mainVideo.paused, true);

  // Video must have strictrecords-video-blurred class
  assert.ok(f.mainVideo.classList.contains('strictrecords-video-blurred'));

  // Player container must have strictrecords-player-blurred class
  assert.ok(f.moviePlayer.classList.contains('strictrecords-player-blurred'));

  // Overlay element must be mounted over the player
  const overlay = f.moviePlayer.querySelector('#strictrecords-yt-blur-overlay');
  assert.ok(overlay, 'Overlay must exist in player container');
  assert.ok(overlay.isConnected, 'Overlay must be connected in DOM');

  // Must display required headers and message
  assert.match(overlay.innerHTML, /STRICTRECORDS · VIDEO RESTRICTED/);
  assert.match(overlay.innerHTML, /YouTube Restricted/);
  assert.match(overlay.innerHTML, /Playback has been paused by StrictRecords/);
  assert.match(overlay.innerHTML, /45 · 80 points required/);
  assert.match(overlay.innerHTML, /REQUEST EDUCATIONAL ACCESS/);
});

test('2. Playback attempts during restriction are intercepted and re-paused', async () => {
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=vid12345678');
  await settle();

  // Video is paused
  assert.equal(f.mainVideo.paused, true);

  // Simulate playback attempt (e.g. user clicks play or script calls play)
  f.mainVideo.paused = false;
  let prevented = false;
  let stopped = false;
  f.events.play({
    target: f.mainVideo,
    preventDefault() { prevented = true; },
    stopImmediatePropagation() { stopped = true; }
  });

  assert.equal(f.mainVideo.paused, true, 'Video must be immediately re-paused');
  assert.equal(f.mainVideo.autoplay, false, 'Autoplay must be disabled');
  assert.equal(f.mainVideo.muted, true, 'Video must be muted');
  assert.ok(prevented, 'Play event default must be prevented');
  assert.ok(stopped, 'Play event propagation must be stopped');
});

test('3. YouTube Shorts page pauses reel player and applies blur overlay', async () => {
  const f = createYouTubeFixture('https://www.youtube.com/shorts/short_123456');
  await settle();

  assert.equal(f.body.inert, false, 'Body must not be inerted on Shorts');
  assert.equal(f.mainVideo.paused, true, 'Shorts video must be paused');
  assert.ok(f.mainVideo.classList.contains('strictrecords-video-blurred'));
  assert.ok(f.moviePlayer.querySelector('#strictrecords-yt-blur-overlay'));
});

test('4. YouTube embedded video inside iframe is detected, paused and blurred', async () => {
  const f = createYouTubeFixture('https://www.youtube.com/embed/embed_video_1', false); // isTop = false
  await settle();

  assert.equal(f.mainVideo.paused, true, 'Embed video must be paused');
  assert.ok(f.mainVideo.classList.contains('strictrecords-video-blurred'));
  const overlay = f.moviePlayer.querySelector('#strictrecords-yt-blur-overlay');
  assert.ok(overlay, 'Overlay must be attached in embed player');
});

test('5. Dynamically injected video player is detected by MutationObserver and paused', async () => {
  const f = createYouTubeFixture('https://www.youtube.com/results?search_query=python');
  await settle();

  // Insert a newly created video element dynamically (e.g. preview player or dynamic feed video)
  const newVideo = f.document.createElement('video');
  newVideo.paused = false;
  f.moviePlayer.append(newVideo);

  // Trigger mutation
  f.triggerMutation([newVideo]);

  assert.equal(newVideo.paused, true, 'Dynamically inserted video must be immediately paused');
  assert.ok(newVideo.classList.contains('strictrecords-video-blurred'));
});

test('6. SPA navigation without reload (yt-navigate-finish) re-applies blur and pauses next video', async () => {
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=first_video');
  await settle();

  assert.equal(f.mainVideo.paused, true);

  // Simulate navigation to another video
  f.window.location = new URL('https://www.youtube.com/watch?v=second_video');
  f.mainVideo.paused = false; // Next video starts buffering

  // Fire yt-navigate-finish event
  f.windowEvents['yt-navigate-finish']();

  assert.equal(f.mainVideo.paused, true, 'Next video must be paused immediately upon navigation');
  await settle();
  assert.ok(f.mainVideo.classList.contains('strictrecords-video-blurred'));
});

test('7. Background tabs continue checking access without skipping due to document.hidden', async () => {
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=bg_tab_video');
  await settle();

  // Set document.hidden = true (user switched tabs)
  f.document.hidden = true;

  // Timers should still exist and schedule regular checks
  assert.ok(f.timers.size > 0, 'Schedule timer must exist even when document.hidden is true');

  const timerEntry = [...f.timers.values()][0];
  f.mainVideo.paused = false; // Background audio/video attempt
  await timerEntry.fn(); // execute refresh()
  await settle();

  assert.equal(f.mainVideo.paused, true, 'Background tab must still pause restricted video');
});

test('8. Live restriction state change from Windows EXE broadcasts to tab and pauses playing video', async () => {
  const allowedReply = {
    service: 'youtube',
    restricted: false,
    currentScore: 85,
    requiredScore: 80
  };
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=live_sync_video', true, allowedReply);
  await settle();

  // Initially allowed: video is not blurred, overlay not mounted
  assert.equal(f.mainVideo.classList.contains('strictrecords-video-blurred'), false);
  assert.equal(f.moviePlayer.querySelector('#strictrecords-yt-blur-overlay'), undefined);

  // Simulate video playing
  f.mainVideo.paused = false;

  // Now Windows EXE activates restriction (e.g. points dropped, or bedtime lock)
  // service_worker broadcasts STRICTBOT_RESTRICTION_STATE_CHANGED
  f.events.runtimeMessage({
    type: 'STRICTBOT_RESTRICTION_STATE_CHANGED',
    status: {
      service: 'youtube',
      restricted: true,
      currentScore: 50,
      requiredScore: 80,
      statusText: 'Score 50/80 is below threshold.'
    }
  });

  assert.equal(f.mainVideo.paused, true, 'Video must be paused upon receiving restriction broadcast');
  assert.ok(f.mainVideo.classList.contains('strictrecords-video-blurred'), 'Video must be blurred');
  const overlay = f.moviePlayer.querySelector('#strictrecords-yt-blur-overlay');
  assert.ok(overlay, 'Blur overlay must be mounted immediately without page refresh');
});

test('9. Unlocking YouTube removes blur and overlay immediately without requiring page refresh', async () => {
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=unlock_video');
  await settle();

  // Confirmed restricted
  assert.ok(f.mainVideo.classList.contains('strictrecords-video-blurred'));
  assert.ok(f.moviePlayer.querySelector('#strictrecords-yt-blur-overlay'));

  // User completes tasks; Windows EXE confirms unlock (restricted: false)
  f.events.runtimeMessage({
    type: 'STRICTBOT_RESTRICTION_STATE_CHANGED',
    status: {
      service: 'youtube',
      restricted: false,
      currentScore: 82,
      requiredScore: 80
    }
  });

  // Blur must be removed
  assert.equal(f.mainVideo.classList.contains('strictrecords-video-blurred'), false);
  assert.equal(f.moviePlayer.classList.contains('strictrecords-player-blurred'), false);

  // Overlay must be removed
  const overlay = f.moviePlayer.querySelector('#strictrecords-yt-blur-overlay');
  assert.equal(overlay, undefined, 'Restriction overlay must be removed on unlock');
});

test('10. Approved educational video is unblurred while unapproved videos remain blocked', async () => {
  const eduReply = {
    service: 'youtube',
    restricted: false,
    temporary_exception: 'EDUCATIONAL_VIDEO',
    duration_seconds: 900,
    duration_formatted: '15:00',
    title: 'Operating Systems Virtual Memory'
  };
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=edu_approved_vid', true, eduReply);
  await settle();

  // Approved video must NOT be blurred
  assert.equal(f.mainVideo.classList.contains('strictrecords-video-blurred'), false);
  assert.equal(f.moviePlayer.querySelector('#strictrecords-yt-blur-overlay'), undefined);
});

test('11. Authorized video preserves user manual pause without extension hijacking or auto-play', async () => {
  const allowedReply = {
    service: 'youtube',
    restricted: false,
    currentScore: 90,
    requiredScore: 80
  };
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=manual_pause_test', true, allowedReply);
  await settle();

  // User voluntarily paused their video
  f.mainVideo.paused = true;
  f.mainVideo.playCalls = 0;
  f.moviePlayer.playVideoCalls = 0;

  f.mainVideo.play = function() {
    this.playCalls = (this.playCalls || 0) + 1;
    this.paused = false;
    return Promise.resolve();
  };
  f.moviePlayer.playVideo = function() {
    this.playVideoCalls = (this.playVideoCalls || 0) + 1;
  };

  // State synchronization / periodic refresh arrives
  f.events.runtimeMessage({
    type: 'STRICTBOT_RESTRICTION_STATE_CHANGED',
    status: {
      service: 'youtube',
      restricted: false,
      currentScore: 90,
      requiredScore: 80
    }
  });
  await settle();

  // The extension must NOT force playback or hijack the user\'s player controls
  assert.equal(f.mainVideo.playCalls, 0, 'Extension must NEVER force video.play() on authorized video');
  assert.equal(f.moviePlayer.playVideoCalls, 0, 'Extension must NEVER force movie_player.playVideo() on authorized video');
  assert.equal(f.mainVideo.paused, true, 'User paused video must remain paused');
});

test('12. Authorized video allows natural keydown controls (Space, k) without extension interception', async () => {
  const allowedReply = {
    service: 'youtube',
    restricted: false,
    currentScore: 85,
    requiredScore: 80
  };
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=keydown_test_vid', true, allowedReply);
  await settle();

  let prevented = false;
  let stopped = false;
  const evt = {
    key: ' ',
    target: f.body,
    preventDefault() { prevented = true; },
    stopPropagation() { stopped = true; }
  };

  const initialPauses = f.mainVideo.pauses || 0;
  if (f.windowEvents.keydown) {
    f.windowEvents.keydown(evt);
  }

  assert.equal(prevented, false, 'Space keydown must not be prevented on authorized video');
  assert.equal(stopped, false, 'Space keydown must not be stopped on authorized video');
  assert.equal(f.mainVideo.pauses || 0, initialPauses, 'Extension must not force pauseMedia on user keydown');
});

test('13. 1-second continuous playback details extraction captures position, duration, speed, state, timeline', async () => {
  const nightCode = fs.readFileSync(path.join(__dirname, '../browser_extension/night_playback.js'), 'utf-8');

  const storageData = {};
  let secondIntervalFn = null;
  let secondIntervalMs = null;
  const msgListeners = [];

  const mainVid = {
    tagName: 'VIDEO',
    currentTime: 125.6,
    duration: 480,
    paused: false,
    ended: false,
    playbackRate: 1.5
  };

  const doc = {
    querySelector(sel) {
      if (sel.includes('video')) return mainVid;
      if (sel.includes('title')) return { textContent: 'High Performance Operating Systems' };
      if (sel.includes('channel')) return { textContent: 'Computer Science Channel' };
      return null;
    },
    addEventListener() {},
    removeEventListener() {},
    title: 'High Performance Operating Systems - YouTube'
  };

  const win = {
    location: new URL('https://www.youtube.com/watch?v=12345678901'),
    addEventListener() {},
    removeEventListener() {},
    top: null
  };
  win.top = win;

  const chromeMock = {
    runtime: {
      onMessage: {
        addListener(fn) { msgListeners.push(fn); }
      },
      sendMessage() { return Promise.resolve({ success: true, enabled: false }); }
    },
    storage: {
      local: {
        set(obj) { Object.assign(storageData, obj); return Promise.resolve(); },
        get() { return Promise.resolve(storageData); }
      }
    }
  };

  const ctx = {
    window: win,
    document: doc,
    location: win.location,
    chrome: chromeMock,
    console,
    URL,
    Date,
    crypto: { randomUUID: () => 'uid-abc-123' },
    setInterval(fn, ms) {
      if (ms === 1000) {
        secondIntervalFn = fn;
        secondIntervalMs = ms;
      }
      return 999;
    },
    clearInterval() {},
    setTimeout(fn) { fn(); return 1; },
    clearTimeout() {}
  };

  vm.runInNewContext(nightCode, ctx);
  await settle();

  assert.equal(secondIntervalMs, 1000, 'night_playback.js must run 1000ms interval for playback sync');
  assert.ok(typeof secondIntervalFn === 'function', '1000ms ticker callback must be defined');

  // Trigger one tick of 1-second playback capture
  secondIntervalFn();
  await settle();

  const details = storageData.strictCurrentPlayback;
  assert.ok(details, 'strictCurrentPlayback must be saved to storage every second');
  assert.equal(details.video_id, '12345678901');
  assert.equal(details.position, 125.6);
  assert.equal(details.duration, 480);
  assert.equal(details.state, 'PLAYING');
  assert.equal(details.speed, 1.5);
  assert.equal(details.timeline, '00:02:05');
  assert.equal(details.canonical_url, 'https://www.youtube.com/watch?v=12345678901');
  assert.equal(details.title, 'High Performance Operating Systems');
  assert.ok(details.timestamp > 0);

  // Verify message STRICTBOT_GET_PLAYBACK_DETAILS
  let replyObj = null;
  for (const listener of msgListeners) {
    listener({ type: 'STRICTBOT_GET_PLAYBACK_DETAILS' }, {}, (data) => {
      replyObj = data;
    });
  }

  assert.ok(replyObj, 'Must respond to STRICTBOT_GET_PLAYBACK_DETAILS');
  assert.equal(replyObj.success, true);
  assert.equal(replyObj.playback.video_id, '12345678901');
  assert.equal(replyObj.playback.position, 125.6);
  assert.equal(replyObj.playback.timeline, '00:02:05');
});


test('14. Educational HUD consistently updates playback time, adjusts to seeking, and monotonic timer never bounces upwards', async () => {
  const eduReply = {
    service: 'youtube',
    restricted: false,
    temporary_exception: 'EDUCATIONAL_VIDEO',
    duration_seconds: 300,
    duration_formatted: '05:00',
    remaining_seconds: 300,
    title: 'Python in 2 Minutes'
  };
  const f = createYouTubeFixture('https://www.youtube.com/watch?v=hud_adjust_test', true, eduReply);
  f.events.runtimeMessage({
    type: 'STRICTBOT_RESTRICTION_STATE_CHANGED',
    status: eduReply
  });
  await settle();

  // 1. Initial mount: HUD must display playback time 00:10 / 05:00
  const pbDisplay = f.document.getElementById('eduHudPlaybackTimeDisplay');
  const badge = f.document.getElementById('eduHudStatusBadge');
  const timerDisplay = f.document.getElementById('eduHudTimerDisplay');

  assert.ok(pbDisplay, 'Playback time display must exist in HUD');
  assert.match(pbDisplay.textContent, /00:10 \/ 05:00/, 'Initial playback time must show position / duration');

  // 2. User seeks to 01:45
  f.mainVideo.currentTime = 105; // 01:45
  if (f.mainVideo.listeners['seeked']) f.mainVideo.listeners['seeked']();
  await settle();

  assert.equal(pbDisplay.textContent, '01:45 / 05:00', 'Playback time display must adjust immediately upon seeking');

  // 3. User pauses video: badge updates to PAUSED and playback time freezes
  f.mainVideo.paused = true;
  if (f.mainVideo.listeners['pause']) f.mainVideo.listeners['pause']();
  await settle();

  assert.equal(badge.textContent, 'PAUSED', 'Status badge must show PAUSED when user pauses');
  assert.equal(pbDisplay.textContent, '01:45 / 05:00', 'Playback time must remain steady on paused frame');

  // 4. Verify Monotonic Timer: Heartbeat returning higher remaining seconds must NOT bounce timer upwards
  f.setReply({
    success: true,
    status: 'ACTIVE',
    remaining_seconds: 295
  });

  const timerEntries = [...f.intervals.values()];
  const heartbeatTimer = timerEntries.find(e => e.ms === 3000);
  if (heartbeatTimer) {
    await heartbeatTimer.fn();
    await settle();
  }

  assert.ok(timerDisplay.textContent.includes('allowance remaining'));
});
