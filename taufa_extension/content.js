/**
 * StrictRecords · Taufa AI Bridge — Content Script
 * Injected on https://taufa.ai/*
 * Surfaces a lightweight bridge badge and handles same-origin fetch requests.
 */

(function () {
  if (window.__sr_taufa_bridge_installed) return;
  window.__sr_taufa_bridge_installed = true;
  console.log("[StrictRecords Bridge] Active on Taufa.ai");

  // Ping background to verify cookies whenever user navigates on taufa.ai
  chrome.runtime.sendMessage({ action: "GET_STATUS" }, (resp) => {
    if (resp && resp.taufa && resp.taufa.authenticated) {
      renderActiveBadge(resp.taufa.user);
    }
  });

  // Handle same-origin fetch from extension popup/background
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === "EXECUTE_TAUFA_CHAT") {
      (async () => {
        try {
          const resp = await fetch("/api/chat", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Accept": "*/*"
            },
            body: JSON.stringify(msg.payload)
          });
          if (!resp.ok) {
            const errText = await resp.text();
            sendResponse({ success: false, status: resp.status, error: `HTTP ${resp.status}: ${errText.slice(0, 100)}` });
            return;
          }
          const text = await resp.text();
          sendResponse({ success: true, raw: text });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
      })();
      return true; // async reply
    }
  });

  function renderActiveBadge(user) {
    if (document.getElementById("sr-taufa-bridge-badge")) return;
    const badge = document.createElement("div");
    badge.id = "sr-taufa-bridge-badge";
    badge.style.position = "fixed";
    badge.style.bottom = "16px";
    badge.style.right = "16px";
    badge.style.zIndex = "999999";
    badge.style.backgroundColor = "#101722";
    badge.style.border = "1px solid #00d69b";
    badge.style.borderRadius = "20px";
    badge.style.padding = "6px 14px";
    badge.style.fontSize = "12px";
    badge.style.fontFamily = "system-ui, -apple-system, sans-serif";
    badge.style.color = "#f5f7fb";
    badge.style.display = "flex";
    badge.style.alignItems = "center";
    badge.style.gap = "8px";
    badge.style.boxShadow = "0 4px 12px rgba(0,0,0,0.4)";
    badge.style.cursor = "pointer";
    badge.style.transition = "transform 0.2s, opacity 0.2s";

    const dot = document.createElement("span");
    dot.style.width = "8px";
    dot.style.height = "8px";
    dot.style.backgroundColor = "#00d69b";
    dot.style.borderRadius = "50%";
    dot.style.display = "inline-block";
    dot.style.boxShadow = "0 0 8px #00d69b";

    const label = document.createElement("span");
    label.innerText = `StrictRecords Bridge: ${user?.email || "Connected"}`;

    badge.appendChild(dot);
    badge.appendChild(label);

    badge.addEventListener("click", () => {
      badge.style.transform = "scale(0.96)";
      label.innerText = "Syncing session...";
      chrome.runtime.sendMessage({ action: "SYNC_COOKIES", user }, (res) => {
        if (res && res.success) {
          label.innerText = "Synced to StrictRecords!";
          setTimeout(() => {
            label.innerText = `StrictRecords Bridge: ${user?.email || "Connected"}`;
            badge.style.transform = "scale(1)";
          }, 2000);
        } else {
          label.innerText = "Sync failed (Check app)";
          setTimeout(() => {
            label.innerText = `StrictRecords Bridge: ${user?.email || "Connected"}`;
            badge.style.transform = "scale(1)";
          }, 2500);
        }
      });
    });

    document.body.appendChild(badge);
  }
})();
