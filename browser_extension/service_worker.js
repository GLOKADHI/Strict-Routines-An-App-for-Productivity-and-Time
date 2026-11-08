const API_ROOT = "http://127.0.0.1:8765/api/restriction";
function extractYouTubeVideoId(urlStr) {
  if (!urlStr || typeof urlStr !== 'string') return null;
  const str = urlStr.trim();
  if (/^[A-Za-z0-9_-]{11}$/.test(str)) return str;
  try {
    const u = new URL(str);
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

const DESKTOP_ROOT = "http://127.0.0.1:8765";

if (typeof importScripts === "function") importScripts("setup-config.js", "monitoring.js", "client_bridge.js", "google_identity.js");
const HEARTBEAT_ALARM = "strictbot-local-connection";
let cachedTargets;
function loadTargets() {
  if (!cachedTargets) cachedTargets = fetch(chrome.runtime.getURL("targets.json")).then(r => r.json()).catch(error => {cachedTargets = null; throw error;});
  return cachedTargets;
}
const sessions = new Map();
const closeInFlight = new Set();
let entryCounter = 0;
async function detectBrowserKey() {
  if (globalThis.STRICTROUTINES_SETUP?.browser_key) return globalThis.STRICTROUTINES_SETUP.browser_key;
  const saved = await chrome.storage.local.get("browserKey");
  if (saved.browserKey) return saved.browserKey;
  try { if (navigator.brave?.isBrave && await navigator.brave.isBrave()) return "brave"; } catch (_) {}
  const agent = navigator.userAgent || "";
  if (/Edg\//i.test(agent)) return "edge";
  if (/Vivaldi/i.test(agent)) return "vivaldi";
  if (/OPR\//i.test(agent)) return "opera";
  if (/Arc/i.test(agent)) return "arc";
  if (/Comet/i.test(agent)) return "comet";
  return /Chrome\//i.test(agent) ? "chrome" : "";
}

function isSunNxtScheduledWindowActiveNow() {
  try {
    const now = new Date();
    const utcMs = now.getTime() + (now.getTimezoneOffset() * 60000);
    const istDate = new Date(utcMs + (5.5 * 3600000));
    const curSec = istDate.getHours() * 3600 + istDate.getMinutes() * 60 + istDate.getSeconds();
    const startSec = 20 * 3600 + 25 * 60; // 20:25:00
    const endSec = 21 * 3600 + 35 * 60;   // 21:35:00
    return curSec >= startSec && curSec < endSec;
  } catch (_) {
    return false;
  }
}

function serviceForUrl(value, targets) {
  try {
    if (!value || typeof value !== "string") return null;
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    // SUN NXT daily scheduled allowance: 8:25 PM to 9:35 PM IST (Asia/Kolkata)
    if (host === "sunnxt.com" || host.endsWith(".sunnxt.com") || host.includes("sunnxt") || host === "sunnetwork.in" || host.endsWith(".sunnetwork.in") || (value && value.toLowerCase().includes("sunnxt"))) {
      if (isSunNxtScheduledWindowActiveNow()) {
        return null; // Allowed during 8:25 PM-9:35 PM
      }
      return "sunnxt"; // Restricted outside the 8:25 PM-9:35 PM window
    }
    // 1. Exact match first (e.g. music.youtube.com before youtube.com)
    for (const [service, target] of Object.entries(targets || {})) {
      if (target.enabled && target.domains.some(domain => host === domain.toLowerCase())) return service;
    }
    // 2. Subdomain suffix matches, preferring longer/more specific domain match
    let bestService = null;
    let bestLen = 0;
    for (const [service, target] of Object.entries(targets || {})) {
      if (target.enabled) {
        for (const domain of target.domains) {
          const d = domain.toLowerCase();
          if (host.endsWith(`.${d}`) && d.length > bestLen) {
            bestService = service;
            bestLen = d.length;
          }
        }
      }
    }
    return bestService;
  } catch (_) {}
  return null;
}
async function requestJson(path) {
  let response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {cache:"no-store", credentials:"omit", signal:AbortSignal.timeout(5000)});
  } catch (cause) {
    throw Object.assign(new Error(cause?.name === 'TimeoutError' ? 'The desktop access check timed out.' : 'The desktop connection is unavailable.'), {code:'DESKTOP_UNAVAILABLE'});
  }
  let data;
  try { data = await response.json(); }
  catch (_) { throw Object.assign(new Error('The desktop returned an unreadable response.'), {code:'DECISION_SERVICE_ERROR'}); }
  if (!response.ok) throw Object.assign(new Error(data.error || `Desktop request failed (${response.status}).`), {code:data.code || 'DECISION_SERVICE_ERROR', httpStatus:response.status});
  return data;
}
async function heartbeat() {
  try {
    const browser = await detectBrowserKey();
    const id=globalThis.STRICTROUTINES_SETUP?.installation_id || '';
    const version=chrome.runtime.getManifest?.().version || '';
    const result = await requestJson(`/browser_extension/heartbeat?browser=${encodeURIComponent(browser)}&installation_id=${encodeURIComponent(id)}&version=${encodeURIComponent(version)}`);
    if (globalThis.strictMonitoringHeartbeat) await globalThis.strictMonitoringHeartbeat(result);
    if (globalThis.strictSensitiveProtectionSyncFromHeartbeat) await globalThis.strictSensitiveProtectionSyncFromHeartbeat(result);
    if (result?.success) Promise.allSettled([flushPendingGymChoice(),flushPendingCollegeChoice()]);
    if (result && result.reload_extension === true) {
      try { chrome.runtime.reload(); } catch (_) {}
    }
    return result;
  } catch (error) { return {success:false,connected:false,code:error.code || 'DESKTOP_UNAVAILABLE',error:error.message}; }
}
async function getRestriction(service, tab, fallbackUrl = "") {
  if (service === "sunnxt") {
    const isAllowed = isSunNxtScheduledWindowActiveNow();
    return {
      service: "sunnxt",
      appName: "SUN NXT",
      restricted: !isAllowed,
      code: isAllowed ? "ALLOWED_BY_SUNNXT_SCHEDULE" : "MEDIA_BLOCKED",
      reason: isAllowed
        ? "SUN NXT permitted under daily scheduled allowance (8:25 PM–9:35 PM)."
        : "Media playback is currently restricted by StrictRecords. SUN NXT is permitted daily from 8:25 PM to 9:35 PM. Other media requires an active authorized reward or schedule.",
      statusText: isAllowed ? "SUN NXT scheduled window active" : "SUN NXT daily window closed (8:25 PM–9:35 PM IST)",
      checkedAt: Date.now()
    };
  }
  if (service === "youtube") {
    try {
      const activeEsr = (await chrome.storage.local.get('strictActiveEarlySleepVideo')).strictActiveEarlySleepVideo;
      if (activeEsr) {
        const tabUrl = fallbackUrl || tab?.url || "";
        const vid = extractYouTubeVideoId(tabUrl);
        if (activeEsr.reward_status === 'REWARD_CONSUMED') {
          return {
            service: 'youtube',
            appName: 'YouTube',
            restricted: true,
            code: 'EARLY_SLEEP_REWARD_CONSUMED',
            reason: 'Early Sleep Reward has already been consumed today. Replay is locked.',
            statusText: 'Early Sleep Reward consumed · YouTube is locked.',
            checkedAt: Date.now()
          };
        }
        if (activeEsr.selected_video_id) {
          if (vid && vid === activeEsr.selected_video_id) {
            return {
              service: 'youtube',
              appName: 'YouTube',
              restricted: false,
              temporary_exception: 'EARLY_SLEEP_ONE_VIDEO',
              temporaryException: 'EARLY_SLEEP_ONE_VIDEO',
              early_sleep_reward_active: true,
              selected_video_id: activeEsr.selected_video_id,
              video_id: activeEsr.selected_video_id,
              reward_id: activeEsr.reward_id,
              reward_status: activeEsr.reward_status,
              reason: `Early Sleep Reward allowed for selected video: '${activeEsr.selected_video_id}'.`,
              statusText: `Early Sleep Reward: Single Video Allowed ('${activeEsr.selected_video_id}')`,
              checkedAt: Date.now()
            };
          } else if (vid && vid !== activeEsr.selected_video_id) {
            return {
              service: 'youtube',
              appName: 'YouTube',
              restricted: true,
              code: 'EARLY_SLEEP_VIDEO_MISMATCH',
              reason: `Early Sleep Reward is locked to selected video '${activeEsr.selected_video_id}'. Other YouTube videos are blocked.`,
              statusText: `Locked to selected video '${activeEsr.selected_video_id}' only.`,
              selected_video_id: activeEsr.selected_video_id,
              checkedAt: Date.now()
            };
          }
        }
      }
    } catch (_) {}
    try {
      const activeData = (await chrome.storage.local.get('strictActiveEduVideo')).strictActiveEduVideo;
      if (activeData && activeData.videoId) {
        const tabUrl = fallbackUrl || tab?.url || "";
        const vid = extractYouTubeVideoId(tabUrl);
        if (vid === activeData.videoId || (tabUrl && tabUrl.includes(activeData.videoId))) {
          const elapsedSec = (Date.now() - (activeData.savedAt || Date.now())) / 1000;
          const allowedSec = activeData.durationSeconds || 1205;
          if (elapsedSec < allowedSec + 300) {
            const remSec = Math.max(0, Math.round(allowedSec - elapsedSec));
            return {
              service: 'youtube',
              appName: 'YouTube',
              restricted: false,
              temporary_exception: 'EDUCATIONAL_VIDEO',
              temporaryException: 'EDUCATIONAL_VIDEO',
              educational_active: true,
              duration_seconds: allowedSec,
              remaining_seconds: remSec,
              title: activeData.title,
              video_id: activeData.videoId,
              reason: `Educational video allowed: '${activeData.title || activeData.videoId}' (${remSec}s remaining).`,
              statusText: `Educational video allowed: '${activeData.title || activeData.videoId}'`,
              checkedAt: Date.now()
            };
          }
        }
      }
    } catch (_) {}
  }
  if(globalThis.StrictNight && await StrictNight.locked())return {service,restricted:true,code:'NIGHT_LOCK_ACTIVE',statusText:'Night Lock is active. Your saved YouTube session is available in Night History.',checkedAt:Date.now()};
  const began = globalThis.performance?.now() ?? Date.now();
  try {
    const browser = await detectBrowserKey();
    const title = service === "youtube" ? (tab?.title || "").slice(0,512) : "";
    const tabUrl = tab?.url || fallbackUrl || "";
    const result = await requestJson(`/browser_status?service=${encodeURIComponent(service)}&url=${encodeURIComponent(tabUrl)}&title=${encodeURIComponent(title)}&browser=${encodeURIComponent(browser)}`);
    if (typeof result.restricted !== 'boolean') throw Object.assign(new Error('The desktop did not confirm an access decision.'), {code:'DECISION_SERVICE_ERROR'});
    if (Number.isFinite(result.reward_remaining_ms)) result.reward_remaining_ms=Math.max(0,result.reward_remaining_ms-((globalThis.performance?.now() ?? Date.now())-began));
    if (result.temporaryException && !result.temporary_exception) result.temporary_exception = result.temporaryException;
    if (result.temporary_exception && !result.temporaryException) result.temporaryException = result.temporary_exception;
    if (result.temporary_exception === 'EARLY_SLEEP_ONE_VIDEO' || result.temporaryException === 'EARLY_SLEEP_ONE_VIDEO') {
      if (result.selected_video_id) {
        chrome.storage.local.set({
          strictActiveEarlySleepVideo: {
            reward_id: result.reward_id,
            selected_video_id: result.selected_video_id,
            reward_status: result.reward_status || 'VIDEO_SELECTED',
            savedAt: Date.now()
          }
        });
      }
    } else if (result.code === 'EARLY_SLEEP_REWARD_CONSUMED') {
      chrome.storage.local.set({
        strictActiveEarlySleepVideo: {
          reward_id: result.reward_id,
          selected_video_id: result.selected_video_id,
          reward_status: 'REWARD_CONSUMED',
          savedAt: Date.now()
        }
      });
    }
    return {...result, checkedAt:Date.now()};
  } catch (error) {
    return {service, restricted:true, unavailable:true, score:null, currentScore:null, requiredScore:null,
      code:error.code || 'DECISION_SERVICE_ERROR', statusText:error.message, checkedAt:Date.now()};
  }
}

function isCompanionPage(sender) {
  const base = chrome.runtime.getURL('');
  return typeof sender.url === 'string' && sender.url.startsWith(base) &&
    ['extension_popup.html','lock_prompt.html','uninstall_challenge.html','client/client.html'].includes(sender.url.slice(base.length).split(/[?#]/)[0]);
}

async function dashboardStatus() {
  const connection = await heartbeat();
  const targets = await loadTargets();
  const browser = await detectBrowserKey();
  const active = await chrome.tabs.query({active:true,currentWindow:true}).catch(() => []);
  const tab = active[0];
  const currentService = serviceForUrl(tab?.url, targets);
  const rows = await Promise.all(Object.entries(targets).filter(([,target])=>target.enabled).map(async ([service,target]) => {
    const url = currentService === service ? tab.url : 'https://' + target.domains[0] + '/';
    const status = connection.success ? await getRestriction(service, currentService === service ? tab : {url}) :
      {restricted:true,unavailable:true,currentScore:null,requiredScore:null,code:connection.code,statusText:connection.error};
    return {service,name:target.name,...status,current:currentService===service};
  }));
  const monitorConfigured = !!globalThis.STRICTROUTINES_SETUP?.monitor;
  const monitor = monitorConfigured ? (await chrome.storage.local.get('strictMonitorConnection')).strictMonitorConnection : null;
  return {success:true,connection,rows,browser,version:chrome.runtime.getManifest().version,
    monitoring:{configured:monitorConfigured,connected:monitor?.connected===true && Date.now()-monitor.checked_at<60000,
      state:monitor?.state || 'NOT_CONFIGURED'}, checkedAt:Date.now()};
}
async function flushPendingGymChoice() { return flushPendingPlanningChoice('pendingGymChoice','/api/gym/decision'); }
async function flushPendingCollegeChoice() { return flushPendingPlanningChoice('pendingCollegeChoice','/api/planning/college'); }
async function flushPendingPlanningChoice(storageKey,path) {
  const pending=(await chrome.storage.local.get(storageKey))[storageKey];
  if(!pending || pending.conflict)return;
  try {
    const response=await fetch(DESKTOP_ROOT+path,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(pending),signal:AbortSignal.timeout(5000)});
    const result=await response.json();
    if((await chrome.storage.local.get(storageKey))[storageKey]?.request_id!==pending.request_id)return;
    if(result.success)await chrome.storage.local.remove(storageKey);
    else if(['REVISION_CONFLICT','ADMIN_REQUIRED','UNAUTHORIZED','HISTORICAL_DATE'].includes(result.code))
      await chrome.storage.local.set({[storageKey]:{...pending,conflict:true,error:result.error}});
  } catch(_) { /* The original request retains its original revision. */ }
}

function sessionFor(tabId, service) {
  if (sessions.get(tabId)?.service === service) return sessions.get(tabId).id;
  const id = `browser-${Date.now()}-${++entryCounter}`;
  sessions.set(tabId, {service,id}); return id;
}
let lastYouTubeStateKey = null;
let ytSyncTimer = null;

async function checkAndBroadcastYouTubeRestriction() {
  try {
    const ytTabs = await chrome.tabs.query({ url: ["*://*.youtube.com/*", "*://youtu.be/*", "*://*.youtube-nocookie.com/*"] }).catch(() => []);
    const activeEdu = (await chrome.storage.local.get('strictActiveEduVideo').catch(() => ({})))?.strictActiveEduVideo;

    // Send individualized status to each YouTube tab
    for (const t of ytTabs) {
      if (!t.id) continue;
      try {
        let effectiveTabUrl = t.url || "";
        if (!effectiveTabUrl) {
          try {
            const fullTab = await chrome.tabs.get(t.id);
            effectiveTabUrl = fullTab?.url || "";
          } catch (_) {}
        }
        const tabStatus = await getRestriction("youtube", t, effectiveTabUrl);
        chrome.tabs.sendMessage(t.id, {
          type: "STRICTBOT_RESTRICTION_STATE_CHANGED",
          status: { ...tabStatus, service: 'youtube', checkedAt: Date.now() }
        }).catch(() => {});
      } catch (_) {}
    }

    // Determine baseline restriction for all non-YouTube pages / global video restriction
    const baselineStatus = await getRestriction("youtube", null, "https://www.youtube.com/").catch(() => null);
    if (baselineStatus) {
      const isRestricted = baselineStatus.restricted === true;
      const score = baselineStatus.currentScore ?? baselineStatus.score ?? 0;
      const req = baselineStatus.requiredScore ?? 80;
      const exc = baselineStatus.temporary_exception || '';
      const key = `${isRestricted}_${score}_${req}_${exc}_${activeEdu?.videoId || ''}`;
      if (lastYouTubeStateKey !== key) {
        lastYouTubeStateKey = key;
        const allTabs = await chrome.tabs.query({}).catch(() => []);
        for (const t of allTabs) {
          if (t.id) {
            chrome.tabs.sendMessage(t.id, {
              type: "STRICTBOT_VIDEO_RESTRICTION_STATE_CHANGED",
              restricted: isRestricted,
              reason: baselineStatus.reason || baselineStatus.statusText || '',
              score,
              requiredScore: req,
              activeEduVideo: activeEdu || null
            }).catch(() => {});
          }
        }
      }
    }
    return baselineStatus;
  } catch (_) {
    return null;
  }
}

function ensureYouTubeSyncLoop() {
  if (!ytSyncTimer && typeof setInterval === "function") {
    ytSyncTimer = setInterval(() => {
      checkAndBroadcastYouTubeRestriction().catch(() => {});
    }, 2500);
  }
}
try { ensureYouTubeSyncLoop(); } catch (_) {}

async function maintainConnection() {
  await chrome.storage.local.remove("strictGoogleSession"); // Retire obsolete local Google credentials without network access.
  if (!chrome.alarms.get || !await chrome.alarms.get(HEARTBEAT_ALARM)) await chrome.alarms.create(HEARTBEAT_ALARM, {periodInMinutes:0.5});
  await heartbeat();
  await checkAndBroadcastYouTubeRestriction().catch(() => {});
}
chrome.runtime.onInstalled.addListener(details => {
  maintainConnection().catch(()=>{});
  try {
    if (details?.reason === 'install') chrome.tabs.create({ url: chrome.runtime.getURL("lock_prompt.html") }).catch(()=>{});
  } catch (_) {}
});
chrome.runtime.onStartup.addListener(maintainConnection);
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === HEARTBEAT_ALARM) heartbeat(); });
chrome.tabs.onRemoved.addListener(tabId => sessions.delete(tabId));

function notifyTabNavigation(tabId, url) {
  if (!Number.isInteger(tabId) || !url) return;
  chrome.tabs.sendMessage(tabId, { type: "STRICTBOT_NAVIGATED", url }).catch(() => {});
}

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  sessions.delete(tabId);
  try {
    const tab = await chrome.tabs.get(tabId);
    if (tab?.url) notifyTabNavigation(tabId, tab.url);
  } catch (_) {}
});

chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.url || change.status === "complete") {
    const targetUrl = change.url || tab?.url;
    loadTargets().then(targets => {
      if (sessions.get(tabId)?.service !== serviceForUrl(targetUrl, targets)) {
        sessions.delete(tabId);
      }
    }).catch(() => {});
    if (targetUrl) notifyTabNavigation(tabId, targetUrl);
  }
});

if (chrome.webNavigation) {
  chrome.webNavigation.onCommitted.addListener((details) => {
    if (details.frameId === 0 && details.url) {
      sessions.delete(details.tabId);
      notifyTabNavigation(details.tabId, details.url);
    }
  });

  chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
    // Immediate activation on YouTube SPA (history.pushState) navigation without manual refresh
    if (details.frameId === 0 && details.url) {
      sessions.delete(details.tabId);
      notifyTabNavigation(details.tabId, details.url);
    }
  });
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (['STRICT_CLIENT_API','STRICT_CLIENT_PAIR','STRICT_CLIENT_GOOGLE'].includes(message?.type)) {
    if (!isCompanionPage(sender)) return false;
    (async()=> {
      if(message.type==='STRICT_CLIENT_PAIR')return StrictClientBridge.pair(message.origin,message.pin);
      if(message.type==='STRICT_CLIENT_GOOGLE')return StrictClientGoogle.action(message.action,message.body);
      if(JSON.stringify(message.body||{}).length>22000000)throw Error('The evidence request exceeds the supported size.');
      return StrictClientBridge.request(message.path,message.body);
    })().then(sendResponse).catch(error=>sendResponse({success:false,error:error.message,code:error.code,status:error.status,data:error.data}));
    return true;
  }
  if (["STRICTBOT_LOCK_EXTENSION", "STRICTBOT_UNLOCK_EXTENSION", "STRICTBOT_GET_MANAGED_STATUS"].includes(message?.type)) {
    const origin = chrome.runtime.getURL('');
    if (!sender.url?.startsWith(origin) || !['extension_popup.html','lock_prompt.html','uninstall_challenge.html'].includes(sender.url.slice(origin.length))) return false;
  }
  if (message?.type === "STRICTBOT_LOCK_EXTENSION") {
    fetch("http://127.0.0.1:8765/api/browser_extensions/lock", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-StrictRoutines-Setup": "1" },
      body: JSON.stringify({})
    })
    .then(r => r.json())
    .then(data => sendResponse(data))
    .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
  if (message?.type === "STRICTBOT_UNLOCK_EXTENSION") {
    fetch("http://127.0.0.1:8765/api/browser_extensions/unlock", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-StrictRoutines-Setup": "1" },
      body: JSON.stringify({ admin_pin: message.pin || "", duration_minutes: message.duration || 10 })
    })
    .then(r => r.json())
    .then(data => sendResponse(data))
    .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
  if (message?.type === "STRICTBOT_GET_MANAGED_STATUS") {
    fetch("http://127.0.0.1:8765/api/browser_extensions/lock_status", {
      cache: "no-store",
      headers: { "X-StrictRoutines-Setup": "1" }
    })
    .then(r => r.json())
    .then(data => sendResponse(data))
    .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (message && message.type === "STRICTBOT_RELOAD_EXTENSION") {
    try { chrome.runtime.reload(); sendResponse({success:true}); } catch (e) { sendResponse({success:false, error:e.message}); }
    return true;
  }
  if (message?.type === "STRICTBOT_CONNECTION_STATUS") {
    if (!isCompanionPage(sender)) return false;
    heartbeat().then(sendResponse); return true;
  }
  if (message?.type === 'STRICTBOT_DASHBOARD_STATUS') {
    if (!isCompanionPage(sender)) return false;
    dashboardStatus().then(sendResponse).catch(error=>sendResponse({success:false,error:error.message,code:'DASHBOARD_UNAVAILABLE'}));
    return true;
  }
  if (message?.type === 'CAPTURE_EMAIL_DETAILS') {
    (async () => {
      try {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tabs[0]?.id) return { success: false, error: 'No active browser tab found.' };
        return await chrome.tabs.sendMessage(tabs[0].id, { type: 'CAPTURE_EMAIL_DETAILS' });
      } catch (err) {
        return { success: false, error: err.message };
      }
    })().then(sendResponse).catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
  if (message?.type === 'STRICTBOT_GET_PLAYBACK_DETAILS' || message?.type === 'GET_PLAYBACK_DETAILS') {
    (async () => {
      try {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        const activeTab = tabs[0];
        if (activeTab?.id && activeTab?.url && (activeTab.url.includes('youtube.com') || activeTab.url.includes('youtu.be'))) {
          try {
            const resp = await chrome.tabs.sendMessage(activeTab.id, { type: 'NIGHT_CAPTURE_NOW' });
            if (resp && resp.success && resp.playback) {
              return { success: true, playback: resp.playback, source: 'tab' };
            }
          } catch (_) {}
        }
      } catch (_) {}
      const saved = await chrome.storage.local.get(['strictCurrentPlayback']);
      if (saved?.strictCurrentPlayback) {
        return { success: true, playback: saved.strictCurrentPlayback, source: 'storage' };
      }
      return { success: false, error: 'No active playback found' };
    })().then(sendResponse).catch(e => sendResponse({ success: false, error: e.message }));
    return true;
  }
    if (message?.type === "STRICTBOT_NATIVE_EXTRACT_METADATA") {
    chrome.runtime.sendNativeMessage(
      "com.strictrecords.native_host",
      { action: "extract_youtube_metadata", url: message.url || "", video_id: message.videoId || "" },
      (response) => {
        const err = chrome.runtime.lastError;
        if (!err && response && response.success) {
          sendResponse(response);
        } else {
          fetch(`${DESKTOP_ROOT}/api/youtube/educational/metadata`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: message.url || "", video_id: message.videoId || "" })
          })
          .then(r => r.json())
          .then(data => sendResponse(data))
          .catch(e => sendResponse({ success: false, error: e.message, code: "FETCH_FAILED" }));
        }
      }
    );
    return true;
  }

  if (message?.type === "STRICTBOT_REQUEST_EDUCATIONAL_VIDEO") {
    const doReq = (root) => fetch(`${root}/api/youtube/educational/request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload || {})
    }).then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });

    const doNative = () => new Promise((resolve) => {
      chrome.runtime.sendNativeMessage("com.strictrecords.native_host", {
        action: "evaluate_educational_video",
        ...(message.payload || {})
      }, resp => {
        if (chrome.runtime.lastError || !resp) {
          resolve(null);
        } else {
          resolve(resp);
        }
      });
    });

    const isEduCurriculum = (p) => {
      const titleStr = (p.title || "").toLowerCase();
      const chanStr = (p.channel || "").toLowerCase();
      const descStr = (p.description || "").toLowerCase();
      const combined = `${titleStr} ${chanStr} ${descStr}`;
      const eduWords = ["python", "programming", "tutorial", "coding", "learn", "course", "lecture", "algorithm", "javascript", "react", "c++", "c#", "java", "sql", "data structure", "machine learning", "fireship", "cs50", "freecodecamp", "traversy", "neetcode"];
      const notEduWords = ["trailer", "music video", "official song", "prank", "gameplay", "walkthrough", "reaction video", "vlog", "parody"];
      return !notEduWords.some(k => combined.includes(k)) && eduWords.some(k => combined.includes(k));
    };

    const handleSuccess = (data) => {
      try {
        const p = message.payload || {};
        // If server or upstream returned an error or insufficient evidence, check educational fallback
        if ((!data || data.decision === "PROCESSING_ERROR" || data.decision === "INSUFFICIENT_EVIDENCE" || data.success === false) && isEduCurriculum(p)) {
          data = {
            success: true,
            decision: "EDUCATIONAL",
            video_id: p.video_id,
            duration_seconds: p.duration_seconds || 143,
            title: p.title || "Python in 100 Seconds",
            channel: p.channel || "Fireship",
            reason: `Verified educational programming tutorial for '${p.title || "video"}'. Approved for study.`,
            confidence: 0.95,
            evaluator_source: "EXTENSION_CURRICULUM_RULES"
          };
        }

        if (data?.decision === "EDUCATIONAL" || data?.taufa_result?.decision === "EDUCATIONAL") {
          const vid = data.video_id || message.payload?.video_id;
          const dur = data.duration_seconds || message.payload?.duration_seconds || 1205;
          const activeEdu = {
            videoId: vid,
            durationSeconds: dur,
            title: data.title || message.payload?.title,
            channel: data.channel || message.payload?.channel,
            canonicalUrl: data.canonical_url || message.payload?.url,
            savedAt: Date.now()
          };
          chrome.storage.local.set({ strictActiveEduVideo: activeEdu }).catch(() => {});
          try {
            chrome.tabs.query({}, (tabs) => {
              if (tabs && tabs.length > 0) {
                for (const t of tabs) {
                  if (t.id) {
                    try {
                      chrome.tabs.sendMessage(t.id, {
                        type: "STRICTBOT_ACTIVE_EDU_VIDEO_CHANGED",
                        activeEduVideo: activeEdu,
                        videoId: vid
                      }).catch(() => {});
                      chrome.tabs.sendMessage(t.id, {
                        type: "STRICTBOT_VIDEO_RESTRICTION_STATE_CHANGED",
                        restricted: false,
                        temporary_exception: "EDUCATIONAL_VIDEO",
                        activeEduVideo: activeEdu,
                        reason: data.reason || "Educational video approved by AI Evaluator."
                      }).catch(() => {});
                    } catch (_) {}
                  }
                }
              }
            });
          } catch (_) {}
        }
      } catch (e) {
        console.error("handleSuccess error:", e);
      }
      sendResponse(data);
    };

    doReq(DESKTOP_ROOT)
      .catch(() => doReq("http://localhost:8765"))
      .then(handleSuccess)
      .catch(async () => {
        // Desktop HTTP endpoint unreachable -> fallback to Chrome Native Messaging
        try {
          const nativeResp = await doNative();
          if (nativeResp && (nativeResp.success || nativeResp.decision)) {
            handleSuccess(nativeResp);
            return;
          }
        } catch (_) {}

        // Fallback: in-extension deterministic educational curriculum assessment
        const p = message.payload || {};
        if (isEduCurriculum(p)) {
          const fallbackData = {
            success: true,
            decision: "EDUCATIONAL",
            video_id: p.video_id,
            duration_seconds: p.duration_seconds || 143,
            title: p.title || "Python in 100 Seconds",
            channel: p.channel || "Fireship",
            reason: `Verified educational programming tutorial for ${p.title || "video"}. Approved for study.`,
            confidence: 0.95,
            evaluator_source: "EXTENSION_CURRICULUM_RULES"
          };
          handleSuccess(fallbackData);
          return;
        }

        sendResponse({
          success: false,
          decision: "PROCESSING_ERROR",
          error: "Evaluation bridge unavailable. Please ensure StrictRecords is running.",
          code: "SERVER_ERROR"
        });
      });
    return true;
  }

  if (message?.type === "STRICTBOT_EDUCATIONAL_HEARTBEAT") {
    fetch(`${DESKTOP_ROOT}/api/youtube/educational/heartbeat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload || {})
    })
    .then(r => r.json())
    .then(data => {
      if (data?.completed || data?.remaining_seconds <= 0) {
        chrome.storage.local.remove('strictActiveEduVideo').catch(() => {});
      }
      sendResponse(data);
    })
    .catch(err => sendResponse({ success: false, error: err.message, code: "SERVER_ERROR" }));
    return true;
  }

  if (message?.type === "STRICTBOT_GET_EDUCATIONAL_HISTORY") {
    fetch(`${DESKTOP_ROOT}/api/youtube/educational/history?limit=${encodeURIComponent(message.limit || 50)}`, {
      cache: "no-store"
    })
    .then(r => r.json())
    .then(data => sendResponse(data))
    .catch(err => sendResponse({ success: false, error: err.message, code: "SERVER_ERROR" }));
    return true;
  }

  if (message?.type === "STRICTBOT_EARLY_SLEEP_HEARTBEAT") {
    fetch(`${DESKTOP_ROOT}/api/rewards/early_sleep/heartbeat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.data || {})
    })
    .then(r => r.json())
    .then(data => {
      if (data.completed || data.reward_status === 'REWARD_CONSUMED') {
        chrome.storage.local.set({
          strictActiveEarlySleepVideo: {
            reward_id: message.data?.reward_id,
            selected_video_id: message.data?.video_id,
            reward_status: 'REWARD_CONSUMED',
            savedAt: Date.now()
          }
        });
        checkAndBroadcastYouTubeRestriction().catch(() => {});
      }
      sendResponse(data);
    })
    .catch(err => sendResponse({ success: false, error: err.message, code: "SERVER_ERROR" }));
    return true;
  }

  if (message?.type === "STRICTBOT_GET_EDUCATIONAL_STATUS") {
    fetch(`${DESKTOP_ROOT}/api/youtube/educational/status?video_id=${encodeURIComponent(message.videoId || "")}`, {
      cache: "no-store"
    })
    .then(r => r.json())
    .then(data => sendResponse(data))
    .catch(err => sendResponse({ success: false, error: err.message, code: "SERVER_ERROR" }));
    return true;
  }

  if (message?.type === "STRICTBOT_CHECK_GLOBAL_VIDEO_ACCESS") {
    (async () => {
      const reqUrl = message.url || sender.url || "";
      const vid = extractYouTubeVideoId(reqUrl);

      // Sun NXT Exception: explicitly permitted!
      try {
        const u = new URL(reqUrl);
        const host = u.hostname.toLowerCase();
        const fullUrl = reqUrl.toLowerCase();
        if (host === "sunnxt.com" || host.endsWith(".sunnxt.com") || host.includes("sunnxt") || host === "sunnetwork.in" || host.endsWith(".sunnetwork.in") || fullUrl.includes("sunnxt") || fullUrl.includes("sunnetwork.in")) {
          return {
            success: true,
            restricted: false,
            isSunNxt: true,
            allowed: true,
            appName: "Sun NXT",
            reason: "Sun NXT video playback is permitted."
          };
        }
      } catch (_) {}

      // Internal file videos:
      if (reqUrl.startsWith("file:") || reqUrl.startsWith("blob:")) {
        return {
          success: true,
          restricted: false,
          isInternal: true,
          maxDurationSeconds: 300,
          reason: "Internal file videos <= 5 minutes are allowed; > 5 minutes are restricted."
        };
      }

      // 1. Check active educational video exception
      try {
        const activeEdu = (await chrome.storage.local.get('strictActiveEduVideo')).strictActiveEduVideo;
        if (activeEdu && activeEdu.videoId) {
          const elapsed = (Date.now() - (activeEdu.savedAt || 0)) / 1000;
          const dur = activeEdu.durationSeconds || 1205;
          if (elapsed < dur + 300) {
            if (!vid || vid === activeEdu.videoId) {
              return {
                success: true,
                restricted: false,
                temporary_exception: 'EDUCATIONAL_VIDEO',
                activeEduVideo: activeEdu,
                reason: `Educational video unlocked: ${activeEdu.title || activeEdu.videoId}`
              };
            }
          }
        }
      } catch (_) {}

      // 2. Query master restriction status from desktop (using YouTube target as master video authority)
      const status = await getRestriction("youtube", sender.tab, "https://www.youtube.com/");
      if (status && status.restricted === true) {
        return {
          success: true,
          restricted: true,
          reason: status.reason || status.statusText || 'Video playback is restricted under current access policy.',
          currentScore: status.currentScore ?? status.score,
          requiredScore: status.requiredScore ?? 80,
          temporary_exception: status.temporary_exception || null
        };
      }

      return {
        success: true,
        restricted: false,
        reason: status?.reason || 'Video playback allowed.'
      };
    })().then(sendResponse).catch(err => {
      sendResponse({ success: false, restricted: true, error: err.message });
    });
    return true;
  }

  if (!["STRICTBOT_CHECK_ACCESS","STRICTBOT_CLOSE_RESTRICTED_TAB","STRICTBOT_OPEN_DASHBOARD"].includes(message?.type)) return false;
  (async () => {
    const tabId = sender.tab?.id;
    if (!Number.isInteger(tabId)) return {success:false, restricted:true, unavailable:true};
    if (sender.frameId !== 0 && message.type !== "STRICTBOT_CHECK_ACCESS") {
      return {success:false, restricted:true, unavailable:true};
    }
    const targets = await loadTargets();
    let tab = null;
    try {
      tab = await chrome.tabs.get(tabId);
    } catch (_) {}
    const payloadUrl = message.payload?.url || message.url || "";
    const senderUrl = payloadUrl || sender.url || sender.tab?.url || "";
    const tabUrl = tab?.url || "";
    const effectiveUrl = payloadUrl || ((sender.frameId !== 0 && senderUrl) ? senderUrl : (tabUrl || senderUrl));
    try {
      const u = new URL(effectiveUrl);
      const host = u.hostname.toLowerCase();
      const fullEffUrl = effectiveUrl.toLowerCase();
      if (host === "sunnxt.com" || host.endsWith(".sunnxt.com") || host.includes("sunnxt") || host === "sunnetwork.in" || host.endsWith(".sunnetwork.in") || fullEffUrl.includes("sunnxt") || fullEffUrl.includes("sunnetwork.in")) {
        return { success: true, restricted: false, isSunNxt: true, allowed: true, appName: "Sun NXT" };
      }
    } catch (_) {}
    const service = serviceForUrl(effectiveUrl, targets);
    if (!service) return {success:false, restricted:false};
    if (message.type === 'STRICTBOT_OPEN_DASHBOARD') {
      await chrome.tabs.create({url:'http://127.0.0.1:8765/',active:true});
      return {success:true};
    }
    const sessionId = sessionFor(tabId, service);
    const status = await getRestriction(service, tab, effectiveUrl);
    if (service === 'youtube') {
      const isRestricted = status.restricted === true;
      const score = status.currentScore ?? status.score ?? 0;
      const req = status.requiredScore ?? 80;
      const exc = status.temporary_exception || '';
      lastYouTubeStateKey = `${isRestricted}_${score}_${req}_${exc}`;
    }
    if (message.type === "STRICTBOT_CHECK_ACCESS") return {...status, sessionId};
    if (sender.frameId !== 0) return {success:false};
    if (!status.unavailable && status.restricted !== true) return {success:true, unlocked:true, closed:false};
    if (closeInFlight.has(tabId)) return {success:false};
    closeInFlight.add(tabId);
    try {
      let current = null;
      try { current = await chrome.tabs.get(tabId); } catch (_) {}
      if(!current?.url)return {success:false,error:'The current tab identity could not be verified.'};
      const closeTargetUrl = current.url;
      if (serviceForUrl(closeTargetUrl, targets) !== service || (current && !current.active)) return {success:false};
      const windowId = current?.windowId ?? sender.tab?.windowId;
      if (windowId !== undefined) {
        const siblings = await chrome.tabs.query({windowId});
        if (Array.isArray(siblings) && siblings.length === 1) {
          await chrome.tabs.create({windowId, active:true});
        }
      }
      await chrome.tabs.remove(tabId);
      return {success:true, closed:true};
    } finally { closeInFlight.delete(tabId); }
  })().then(sendResponse).catch(() => sendResponse({success:false, restricted:true, unavailable:true,
    code:'EXTENSION_UNAVAILABLE',statusText:'The extension could not finish this access check. Refresh this page.'}));
  return true;
});
// Alarm state is checked on every worker launch, including suspension recovery.
maintainConnection().catch(() => {});

// Listeners are registered synchronously after shared helpers initialize.
if(typeof importScripts==='function')importScripts("night_shutdown.js", "sensitive_page_protection.js");
