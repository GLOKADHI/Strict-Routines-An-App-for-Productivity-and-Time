/* No persistent countdown here: the Windows companion owns the durable timer. */
(() => {
  const config = globalThis.STRICTROUTINES_SETUP || {};
  let identity;
  async function profileIdentity() {
    if (!identity) identity = (async () => {
      const saved = await chrome.storage.local.get('strictProfileIdentity');
      const value = saved.strictProfileIdentity || crypto.randomUUID();
      if (!saved.strictProfileIdentity) await chrome.storage.local.set({strictProfileIdentity:value});
      return value;
    })();
    return identity;
  }
  chrome.runtime.onMessageExternal.addListener((message, sender, reply) => {
    if (!config.monitor || sender.id !== config.monitor.helper_id ||
        message?.type !== 'STRICTROUTINES_OPERATIONAL' ||
        message.installation_id !== config.monitor.installation_id ||
        typeof message.nonce !== 'string' || message.nonce.length > 64) return false;
    profileIdentity().then(profile => reply({operational:true, target_id:chrome.runtime.id,
      installation_id:config.monitor.installation_id, target_profile:profile, nonce:message.nonce}));
    return true;
  });
  globalThis.strictMonitoringHeartbeat = async result => {
    if (result?.success) await chrome.storage.local.set({strictLastValidHeartbeat:Date.now()});
    if (!config.monitor) return;
    try {
      const response = await fetch('http://127.0.0.1:8765/api/browser_extensions/monitor_evidence', {
        method:'POST', credentials:'omit', cache:'no-store', signal:AbortSignal.timeout(4000),
        headers:{'Content-Type':'application/json','X-StrictRoutines-Monitor':config.monitor.token},
        body:JSON.stringify({role:'target', installation_id:config.monitor.installation_id,
          observed_at_ms:Date.now(), target_profile:await profileIdentity()})
      });
      const data = await response.json();
      await chrome.storage.local.set({strictMonitorConnection:{connected:response.ok && data.success === true,
        checked_at:Date.now(), state:data.state || 'STATE_UNKNOWN'}});
    } catch (_) {
      await chrome.storage.local.set({strictMonitorConnection:{connected:false, checked_at:Date.now(), state:'COMPANION_UNAVAILABLE'}});
    }
  };
})();
