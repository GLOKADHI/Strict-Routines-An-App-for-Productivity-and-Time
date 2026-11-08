importScripts('setup-config.js');
const ALARM = 'strict-status-report';
let work = Promise.resolve();
let identities;
async function identity() {
  if (!identities) identities = (async () => {
    const local = await chrome.storage.local.get('helperProfile');
    const session = await chrome.storage.session.get('browserSession');
    const helperProfile = local.helperProfile || crypto.randomUUID();
    const browserSession = session.browserSession || crypto.randomUUID();
    await chrome.storage.local.set({helperProfile});
    await chrome.storage.session.set({browserSession});
    return {helperProfile,browserSession};
  })();
  return identities;
}
async function report() {
  const config = globalThis.STRICTROUTINES_MONITOR;
  if (!config) return;
  const ids = await identity();
  const saved = await chrome.storage.local.get(['sequence','targetProfile','removedByChrome']);
  let state = 'STATE_UNKNOWN', operational = false;
  let targetProfile = saved.targetProfile || '';
  try {
    // Never enumerate unrelated extensions or send ExtensionInfo objects to Windows.
    const target = await chrome.management.get(config.target_id);
    if (target.id !== config.target_id) throw Error('Wrong target');
    state = target.enabled ? 'ENABLED' : 'DISABLED';
    if (target.enabled) {
      const nonce = crypto.randomUUID();
      const answer = await chrome.runtime.sendMessage(config.target_id, {type:'STRICTROUTINES_OPERATIONAL',
        nonce, installation_id:config.installation_id});
      operational = answer?.operational === true && answer.target_id === config.target_id &&
        answer.installation_id === config.installation_id && answer.nonce === nonce;
      if (operational) targetProfile = answer.target_profile;
    }
  } catch (_) {
    if (state === 'STATE_UNKNOWN' && saved.removedByChrome === true) state = 'UNINSTALLED';
  }
  const sequence = (saved.sequence || 0) + 1;
  // Persist ordering before sending; retries always obtain a new Chrome observation.
  await chrome.storage.local.set({sequence,targetProfile});
  const packet = {role:'observer', installation_id:config.installation_id, state, operational,
    helper_profile:ids.helperProfile, target_profile:targetProfile,
    browser_session:ids.browserSession, sequence, observed_at_ms:Date.now()};
  let status;
  try {
    const response = await fetch('http://127.0.0.1:8765/api/browser_extensions/monitor_evidence', {
      method:'POST', cache:'no-store', credentials:'omit', signal:AbortSignal.timeout(4000),
      headers:{'Content-Type':'application/json','X-StrictRoutines-Monitor':config.token}, body:JSON.stringify(packet)
    });
    status = await response.json();
    if (response.ok && status.success === true) await chrome.storage.local.set({lastValidHeartbeat:Date.now()});
  } catch (_) { status = {success:false,state:'COMPANION_UNAVAILABLE'}; }
  await chrome.storage.local.set({latestStatus:{...status,observed_state:state,checked_at:Date.now()}});
}
function scheduleReport(event) {
  work = work.catch(() => {}).then(async () => {
    if (event === 'uninstalled') await chrome.storage.local.set({removedByChrome:true});
    if (event === 'installed' || event === 'enabled') await chrome.storage.local.set({removedByChrome:false});
    await report();
  }).catch(() => {});
}
async function ensureAlarm() {
  if (!await chrome.alarms.get(ALARM)) await chrome.alarms.create(ALARM,{periodInMinutes:0.5});
}
// All wake-up listeners register synchronously before any storage/network work.
chrome.alarms.onAlarm.addListener(alarm => {if (alarm.name === ALARM) scheduleReport();});
chrome.runtime.onInstalled.addListener(() => {ensureAlarm().catch(() => {}); scheduleReport();});
chrome.runtime.onStartup.addListener(() => {ensureAlarm().catch(() => {}); scheduleReport();});
for (const [name,event] of [['onDisabled','disabled'],['onEnabled','enabled'],['onInstalled','installed']]) {
  chrome.management[name].addListener(info => {
    if (info.id === globalThis.STRICTROUTINES_MONITOR?.target_id) scheduleReport(event);
  });
}
chrome.management.onUninstalled.addListener(id => {
  if (id === globalThis.STRICTROUTINES_MONITOR?.target_id) scheduleReport('uninstalled');
});
ensureAlarm().catch(() => {});
scheduleReport();
