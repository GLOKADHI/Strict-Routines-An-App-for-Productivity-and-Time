/**
 * StrictRecords · Taufa AI Bridge — Popup Controller
 */

document.addEventListener("DOMContentLoaded", () => {
  const taufaStatusPill = document.getElementById("taufaStatusPill");
  const taufaStatusText = document.getElementById("taufaStatusText");
  const serverStatusPill = document.getElementById("serverStatusPill");
  const serverStatusText = document.getElementById("serverStatusText");
  const btnSyncCookies = document.getElementById("btnSyncCookies");
  const btnCopyCookies = document.getElementById("btnCopyCookies");
  const syncToast = document.getElementById("syncToast");

  const testCategory = document.getElementById("testCategory");
  const testDescription = document.getElementById("testDescription");
  const btnTestScore = document.getElementById("btnTestScore");
  const scoreResultBox = document.getElementById("scoreResultBox");
  const scoreDisplay = document.getElementById("scoreDisplay");
  const scoreDecisionPill = document.getElementById("scoreDecisionPill");
  const scoreQualityText = document.getElementById("scoreQualityText");
  const scoreReasonText = document.getElementById("scoreReasonText");

  let activeUserProfile = null;

  function showToast(msg, isSuccess = true) {
    syncToast.innerText = msg;
    syncToast.className = `toast ${isSuccess ? "toast-success" : "toast-error"}`;
    syncToast.style.display = "block";
    setTimeout(() => {
      syncToast.style.display = "none";
    }, 5500);
  }

  // 1. Check Taufa status from background
  function updateTaufaStatus() {
    chrome.runtime.sendMessage({ action: "GET_STATUS" }, (resp) => {
      if (resp && resp.taufa && resp.taufa.authenticated) {
        activeUserProfile = resp.taufa.user;
        const email = resp.taufa.user.email || resp.taufa.user.name || "Logged In";
        taufaStatusPill.className = "pill pill-green";
        taufaStatusPill.innerHTML = `<span class="dot dot-green"></span><span>${email}</span>`;
      } else if (resp && resp.hasCookies) {
        taufaStatusPill.className = "pill pill-green";
        taufaStatusPill.innerHTML = `<span class="dot dot-green"></span><span>Cookies Active (${resp.cookieCount})</span>`;
      } else {
        taufaStatusPill.className = "pill pill-red";
        taufaStatusPill.innerHTML = `<span class="dot dot-red"></span><span>Not Logged In</span>`;
      }
    });
  }
  updateTaufaStatus();

  // 2. Check StrictRecords Desktop server
  const serverPorts = [8765, 8000];
  let connectedServer = null;

  async function checkServer() {
    for (const port of serverPorts) {
      try {
        const r = await fetch(`http://127.0.0.1:${port}/api/taufa/status`, { method: "GET" });
        if (r.ok) {
          const json = await r.json();
          connectedServer = `http://127.0.0.1:${port}`;
          serverStatusPill.className = "pill pill-green";
          serverStatusPill.innerHTML = `<span class="dot dot-green"></span><span>Online (Port ${port})</span>`;
          return;
        }
      } catch (e) {
        // try next port
      }
    }
    serverStatusPill.className = "pill pill-red";
    serverStatusPill.innerHTML = `<span class="dot dot-red"></span><span>Offline</span>`;
  }
  checkServer();

  // 3. Sync Cookies Button Click
  btnSyncCookies.addEventListener("click", () => {
    btnSyncCookies.disabled = true;
    btnSyncCookies.innerText = "Capturing & Syncing...";

    chrome.runtime.sendMessage({ action: "SYNC_COOKIES", user: activeUserProfile }, (res) => {
      btnSyncCookies.disabled = false;
      btnSyncCookies.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
        </svg>
        Capture & Sync to StrictRecords
      `;

      if (res && res.success) {
        const countMsg = res.cookieCount ? ` (${res.cookieCount} cookies)` : "";
        showToast(`Success! Session cookies synced to StrictRecords${countMsg}.`, true);
        checkServer();
      } else {
        showToast(`${res?.error || "Check if StrictRecords server is running."}`, false);
      }
    });
  });

  // 4. Copy Cookies to Clipboard Click
  btnCopyCookies.addEventListener("click", () => {
    chrome.runtime.sendMessage({ action: "GET_COOKIE_HEADER" }, (res) => {
      if (res && res.cookieHeader) {
        navigator.clipboard.writeText(res.cookieHeader).then(() => {
          showToast("Copied full cookie header to clipboard!", true);
        }).catch(() => {
          showToast("Could not access clipboard.", false);
        });
      } else {
        showToast("No Taufa cookies found in browser.", false);
      }
    });
  });

  // 5. Test Live Scoring Click
  btnTestScore.addEventListener("click", () => {
    const category = testCategory.value;
    const desc = testDescription.value.trim();
    if (!desc) {
      showToast("Please enter an activity description.", false);
      return;
    }

    btnTestScore.disabled = true;
    btnTestScore.innerText = "Querying Taufa.ai...";
    scoreResultBox.style.display = "none";

    chrome.runtime.sendMessage({
      action: "ASK_TAUFA",
      category: category,
      description: desc,
      imageCount: 1
    }, (res) => {
      btnTestScore.disabled = false;
      btnTestScore.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="5 3 19 12 5 21 5 3"/>
        </svg>
        Ask Taufa.ai for Score
      `;

      if (res && res.success) {
        if (res.parsedResult) {
          displayParsedResult(res.parsedResult);
        } else {
          parseAndDisplayScore(res.raw);
        }
      } else {
        showToast(`Scoring Error: ${res?.error || "Could not query Taufa.ai"}`, false);
      }
    });
  });

  function displayParsedResult(result) {
    scoreResultBox.style.display = "block";
    const score = result.score !== undefined ? result.score : "?";
    scoreDisplay.innerText = `${score}/8`;
    scoreReasonText.innerText = (result.reason || "Evaluated by Taufa AI.").slice(0, 240);
    scoreQualityText.innerText = result.evidence_quality || "HIGH";

    const status = result.review_status || (score >= 6 ? "APPROVED" : (score === 0 ? "REJECTED" : "BELOW_THRESHOLD"));
    if (status === "APPROVED") {
      scoreDecisionPill.className = "pill pill-green";
      scoreDecisionPill.innerText = "APPROVED (QUALIFIES)";
    } else if (status === "REJECTED") {
      scoreDecisionPill.className = "pill pill-red";
      scoreDecisionPill.innerText = "REJECTED (0 PTS)";
    } else {
      scoreDecisionPill.className = "pill pill-gray";
      scoreDecisionPill.innerText = status;
    }
  }

  function parseAndDisplayScore(rawText) {
    scoreResultBox.style.display = "block";
    let score = null;
    let reason = rawText;
    let quality = "EVALUATED";
    let status = "APPROVED";

    let clean = rawText;
    if (clean.includes('0:"')) {
      const parts = [];
      clean.split("\n").forEach(line => {
        const l = line.trim();
        if (l.startsWith('0:"') && l.endsWith('"')) {
          try {
            parts.push(JSON.parse(l.slice(2)));
          } catch(e) {
            parts.push(l.slice(3, -1));
          }
        }
      });
      clean = parts.join("");
    }

    const match = clean.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/) || clean.match(/(\{[\s\S]*\})/);
    if (match) {
      try {
        const parsed = JSON.parse(match[1]);
        score = parsed.score;
        reason = parsed.reason || reason;
        quality = parsed.evidence_quality || quality;
        status = (score >= 6) ? "APPROVED" : (score === 0 ? "REJECTED" : "BELOW_THRESHOLD");
      } catch (e) {}
    }

    if (score === null) {
      const regexMatch = clean.match(/(?:score|points?)\s*[:=]?\s*(\d+)/i);
      if (regexMatch) {
        score = parseInt(regexMatch[1], 10);
        status = (score >= 6) ? "APPROVED" : (score === 0 ? "REJECTED" : "BELOW_THRESHOLD");
      } else {
        score = "?";
        status = "REVIEW_REQUIRED";
      }
    }

    displayParsedResult({
      score: score,
      reason: reason,
      evidence_quality: quality,
      review_status: status
    });
  }
});
