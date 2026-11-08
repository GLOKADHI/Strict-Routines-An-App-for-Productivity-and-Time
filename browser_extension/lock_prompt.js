document.addEventListener("DOMContentLoaded", async () => {
  const optManaged = document.getElementById("optManaged");
  const optStandard = document.getElementById("optStandard");
  const btnConfirm = document.getElementById("btnConfirm");
  const btnStandardOnly = document.getElementById("btnStandardOnly");
  const statusMsg = document.getElementById("statusMsg");
  const currentStatusBadge = document.getElementById("currentStatusBadge");
  const modeBadge = document.getElementById("modeBadge");

  let selectedMode = "standard"; // Applying management policies requires an explicit selection.

  function updateModeSelection(mode) {
    selectedMode = mode;
    if (mode === "managed") {
      optManaged.classList.add("selected");
      optManaged.setAttribute("aria-checked", "true"); optManaged.tabIndex=0;
      optStandard.setAttribute("aria-checked", "false"); optStandard.tabIndex=-1;
      optStandard.classList.remove("selected");
      btnConfirm.style.display = "flex";
      btnConfirm.innerHTML = '<span>🛡️</span> Apply Managed Removal Protection';
      btnStandardOnly.style.display = "none";
    } else {
      optStandard.classList.add("selected");
      optStandard.setAttribute("aria-checked", "true"); optStandard.tabIndex=0;
      optManaged.setAttribute("aria-checked", "false"); optManaged.tabIndex=-1;
      optManaged.classList.remove("selected");
      btnConfirm.style.display = "none";
      btnStandardOnly.style.display = "block";
    }
  }

  optManaged.addEventListener("click", () => updateModeSelection("managed"));
  optStandard.addEventListener("click", () => updateModeSelection("standard"));

  [optManaged,optStandard].forEach((element,index)=>element.addEventListener('keydown',event=>{
    if(['Enter',' ','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key)){
      event.preventDefault();const next=['Enter',' '].includes(event.key)?index:1-index;
      updateModeSelection(next===0?'managed':'standard');[optManaged,optStandard][next].focus();
    }
  }));
  const browser=globalThis.STRICTROUTINES_SETUP?.browser_key || 'Chromium';
  document.getElementById('browserInstructions').textContent='Browser: '+browser+'. Each browser profile needs its own confirmed companion and optional Status Helper.';
  updateModeSelection('standard');

  // Check current server status
  async function checkCurrentStatus() {
    try {
      const resp = await fetch("http://127.0.0.1:8765/api/browser_extensions/lock_status", {
        cache: "no-store",
        signal: AbortSignal.timeout(4000)
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data.installation_mode === "ADMINISTRATOR_MANAGED" && data.policy_enforced === true) {
          currentStatusBadge.textContent = "● Currently Protected";
          currentStatusBadge.style.color = "#22d69b";
          modeBadge.textContent = "ADMINISTRATOR MANAGED";
          modeBadge.className = "badge locked";
        } else if (data.maintenance_mode) {
          currentStatusBadge.textContent = `● Unlocked for Maintenance (${data.unlocked_remaining_seconds}s)`;
          currentStatusBadge.style.color = "#f59e0b";
          modeBadge.textContent = "MAINTENANCE MODE";
        } else {
          currentStatusBadge.textContent = "○ Standard (Unmanaged)";
          currentStatusBadge.style.color = "#94a3b8";
          modeBadge.textContent = "STANDARD INSTALLATION";
          modeBadge.className = "badge unmanaged";
        }
      } else {
        throw new Error("Policy status could not be read");
      }
    } catch (_) {
      currentStatusBadge.textContent = "Policy status unavailable — reopen settings to retry";
      currentStatusBadge.style.color = "#f87171";
    }
  }

  checkCurrentStatus();

  // Handle Managed Authorization
  btnConfirm.addEventListener("click", async () => {
    btnConfirm.disabled = true;
    btnConfirm.innerHTML = '<span>⏳</span> Applying Windows Policies...';
    statusMsg.className = "status-msg info";
    statusMsg.textContent = "Enforcing authorized browser management policies...";

    try {
      const res = await fetch('http://127.0.0.1:8765/api/browser_extensions/lock', {
        method:'POST',headers:{'Content-Type':'application/json','X-StrictRoutines-Setup':'1'},
        body:JSON.stringify({}),signal:AbortSignal.timeout(8000)
      });
      const data=await res.json();if(!res.ok || !data.success)throw new Error(data.error || 'Policy request failed.');
      const check=await fetch('http://127.0.0.1:8765/api/browser_extensions/lock_status',{cache:'no-store',signal:AbortSignal.timeout(5000)});
      const verified=await check.json();
      if(!check.ok || verified.installation_mode!=='ADMINISTRATOR_MANAGED' || verified.policy_enforced!==true)
        throw new Error('Policy was requested, but applied protection is not confirmed.');

      statusMsg.className = "status-msg success";
      statusMsg.textContent = "✅ Managed protection applied! Extension removal is protected during discipline sessions.";
      btnConfirm.innerHTML = '<span>🔒</span> Protected!';
      modeBadge.textContent = "ADMINISTRATOR MANAGED";
      modeBadge.className = "badge locked";
      currentStatusBadge.textContent = "● Currently Protected";
      currentStatusBadge.style.color = "#22d69b";

      setTimeout(() => {
        try {
          if (chrome.tabs && chrome.tabs.getCurrent) {
            chrome.tabs.getCurrent(tab => {
              if (tab && tab.id) chrome.tabs.remove(tab.id);
            });
          }
        } catch (_) {}
        window.close();
      }, 1200);

    } catch (err) {
      statusMsg.className = "status-msg error";
      statusMsg.textContent = "Could not apply management policy. Open StrictRoutines on this PC, then retry: " + err.message;
      btnConfirm.disabled = false;
      btnConfirm.innerHTML = '<span>🛡️</span> Retry Managed Protection';
    }
  });

  // Handle Standard Installation Selection
  btnStandardOnly.addEventListener("click", () => {
    statusMsg.className = "status-msg info";
    statusMsg.textContent = "Settings closed. Existing protection policy was not changed.";
    setTimeout(() => {
      try {
        if (chrome.tabs && chrome.tabs.getCurrent) {
          chrome.tabs.getCurrent(tab => {
            if (tab && tab.id) chrome.tabs.remove(tab.id);
          });
        }
      } catch (_) {}
      window.close();
    }, 600);
  });
});
