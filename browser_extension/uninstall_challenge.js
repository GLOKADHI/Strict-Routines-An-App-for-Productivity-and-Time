document.addEventListener("DOMContentLoaded", async () => {
  const qTitle = document.getElementById("qTitle");
  const qBadge = document.getElementById("qBadge");
  const qDesc = document.getElementById("qDesc");
  const editorLang = document.getElementById("editorLang");
  const codeEditor = document.getElementById("codeEditor");
  const timerDisplay = document.getElementById("timerDisplay");
  const btnFullscreen = document.getElementById("btnFullscreen");
  const btnSubmit = document.getElementById("btnSubmit");
  const btnExit = document.getElementById("btnExit");
  const btnCopyPrompt = document.getElementById("btnCopyPrompt");
  const resultsBox = document.getElementById("resultsBox");
  const scoreBadge = document.getElementById("scoreBadge");
  const resultsMsg = document.getElementById("resultsMsg");
  const resultsDetails = document.getElementById("resultsDetails");
  const releaseBox = document.getElementById("releaseBox");
  const btnConfirmRelease = document.getElementById("btnConfirmRelease");
  const releaseStatus = document.getElementById("releaseStatus");
  const interruptedOverlay = document.getElementById("interruptedOverlay");
  const btnResume = document.getElementById("btnResume");

  let challengeData = null;
  let timerInterval = null;
  let deadlineEpoch = 0;
  let authToken = null;
  let isInterrupted = false;
  let isSolved = false;

  // 1. Fetch or initialize challenge
  async function loadChallenge() {
    try {
      const resp = await fetch("http://127.0.0.1:8765/api/browser_extensions/uninstall_challenge", {
        cache: "no-store",
        signal: AbortSignal.timeout(5000)
      });
      const data = await resp.json();
      if (!data.success) {
        qTitle.textContent = "Challenge Cooldown Active";
        qDesc.textContent = data.error || "Please wait before requesting another challenge.";
        btnSubmit.disabled = true;
        return;
      }

      challengeData = data;
      qTitle.textContent = data.title || "Coding Challenge";
      qBadge.textContent = data.category || "ALGORITHM";
      qBadge.className = "badge " + (data.category === "SQL" ? "sql" : data.category === "DEBUGGING" ? "debug" : "algo");
      qDesc.textContent = data.description || data.question || "";
      editorLang.textContent = (data.language || "python").toUpperCase();
      if (data.template && !codeEditor.value) {
        codeEditor.value = data.template;
      }

      const remainingSec = data.expires_in_sec || 600;
      deadlineEpoch = Date.now() + (remainingSec * 1000);
      startTimer();

      if (data.interrupted) {
        showInterruption();
      }
    } catch (err) {
      qTitle.textContent = "StrictRoutines Desktop Offline";
      qDesc.textContent = "Could not connect to StrictRoutines on 127.0.0.1:8765. Ensure the desktop application is running.";
      btnSubmit.disabled = true;
    }
  }

  // 2. Countdown Timer
  function startTimer() {
    if (timerInterval) clearInterval(timerInterval);
    updateTimerUI();
    timerInterval = setInterval(updateTimerUI, 1000);
  }

  function updateTimerUI() {
    const diff = Math.max(0, Math.floor((deadlineEpoch - Date.now()) / 1000));
    const mm = Math.floor(diff / 60);
    const ss = diff % 60;
    timerDisplay.textContent = `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;

    if (diff < 120) {
      timerDisplay.classList.add("warning");
    } else {
      timerDisplay.classList.remove("warning");
    }

    if (diff <= 0) {
      clearInterval(timerInterval);
      btnSubmit.disabled = true;
      resultsBox.style.display = "block";
      resultsBox.className = "fail";
      scoreBadge.className = "score-badge fail";
      scoreBadge.textContent = "EXPIRED";
      resultsMsg.textContent = "10-minute challenge deadline expired. Request a new challenge to attempt uninstall release.";
    }
  }

  // 3. Focus & Fullscreen Monitoring
  function showInterruption() {
    if (isSolved) return;
    isInterrupted = true;
    interruptedOverlay.style.display = "flex";
  }

  async function notifyInterruption(evType) {
    if (isSolved || !challengeData) return;
    showInterruption();
    try {
      await fetch("http://127.0.0.1:8765/api/browser_extensions/uninstall_challenge/event", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-StrictRoutines-Setup": "1"
        },
        body: JSON.stringify({
          challenge_id: challengeData.challenge_id,
          event: evType
        })
      });
    } catch (_) {}
  }

  window.addEventListener("blur", () => {
    notifyInterruption("focus_lost");
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      notifyInterruption("tab_hidden");
    }
  });

  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) {
      notifyInterruption("fullscreen_exit");
    }
  });

  // 4. Resume Button
  btnResume.addEventListener("click", async () => {
    try {
      if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen().catch(() => {});
      }
      if (challengeData) {
        await fetch("http://127.0.0.1:8765/api/browser_extensions/uninstall_challenge/resume", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-StrictRoutines-Setup": "1"
          },
          body: JSON.stringify({
            challenge_id: challengeData.challenge_id
          })
        });
      }
    } catch (_) {}
    interruptedOverlay.style.display = "none";
    isInterrupted = false;
  });

  // 5. Toggle Fullscreen
  btnFullscreen.addEventListener("click", async () => {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch (e) {
      console.warn("Fullscreen request failed", e);
    }
  });

  // 6. Submit Solution for Local Grading
  btnSubmit.addEventListener("click", async () => {
    if (!challengeData) return;
    btnSubmit.disabled = true;
    btnSubmit.innerHTML = '<span>⏳</span> Grading Locally...';
    resultsBox.style.display = "none";
    releaseBox.style.display = "none";

    const answerCode = codeEditor.value;

    try {
      const resp = await fetch("http://127.0.0.1:8765/api/browser_extensions/uninstall_challenge/verify", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-StrictRoutines-Setup": "1"
        },
        body: JSON.stringify({
          challenge_id: challengeData.challenge_id,
          code: answerCode,
          language: challengeData.language || "python"
        })
      });

      const res = await resp.json();
      resultsBox.style.display = "block";

      const score = res.score !== undefined ? res.score : (res.correct ? 100 : 0);
      scoreBadge.textContent = `${score} / 100`;

      if (score >= 95) {
        isSolved = true;
        authToken = res.auth_token;
        resultsBox.className = "success";
        scoreBadge.className = "score-badge pass";
        resultsMsg.textContent = res.message || "Qualifying score achieved!";
        releaseBox.style.display = "block";
      } else {
        resultsBox.className = "fail";
        scoreBadge.className = "score-badge fail";
        resultsMsg.textContent = res.message || res.error || "Did not meet passing score (95/100 required).";
      }

      if (res.grading && res.grading.details) {
        resultsDetails.textContent = typeof res.grading.details === "string"
          ? res.grading.details
          : JSON.stringify(res.grading.details, null, 2);
      } else if (res.error) {
        resultsDetails.textContent = res.error;
      } else {
        resultsDetails.textContent = "";
      }

    } catch (err) {
      resultsBox.style.display = "block";
      resultsBox.className = "fail";
      scoreBadge.className = "score-badge fail";
      scoreBadge.textContent = "ERROR";
      resultsMsg.textContent = "Grading request failed: " + err.message;
    } finally {
      btnSubmit.disabled = false;
      btnSubmit.innerHTML = '<span>⚡</span> Submit for Local Grading';
    }
  });

  // 7. Confirm & Remove Policy
  btnConfirmRelease.addEventListener("click", async () => {
    if (!challengeData || !authToken) return;
    btnConfirmRelease.disabled = true;
    btnConfirmRelease.innerHTML = '<span>⏳</span> Releasing Management Policy...';
    releaseStatus.style.display = "block";
    releaseStatus.style.color = "#38bdf8";
    releaseStatus.textContent = "Releasing Windows Chromium policies...";

    try {
      const resp = await fetch("http://127.0.0.1:8765/api/browser_extensions/uninstall_challenge/confirm", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-StrictRoutines-Setup": "1"
        },
        body: JSON.stringify({
          challenge_id: challengeData.challenge_id,
          auth_token: authToken
        })
      });

      const res = await resp.json();
      if (res.success && res.policy_removed) {
        releaseStatus.style.color = "#22d69b";
        releaseStatus.textContent = "✅ " + res.message;
        btnConfirmRelease.innerHTML = '<span>🔓</span> Released!';
        setTimeout(() => {
          try {
            window.open("chrome://extensions", "_blank");
          } catch (_) {}
        }, 1500);
      } else {
        releaseStatus.style.color = "#ef4444";
        releaseStatus.textContent = "Release failed: " + (res.error || "Policy could not be released.");
        btnConfirmRelease.disabled = false;
        btnConfirmRelease.innerHTML = '<span>🔓</span> Retry Policy Release';
      }
    } catch (err) {
      releaseStatus.style.color = "#ef4444";
      releaseStatus.textContent = "Error releasing policy: " + err.message;
      btnConfirmRelease.disabled = false;
      btnConfirmRelease.innerHTML = '<span>🔓</span> Retry Policy Release';
    }
  });

  // 8. Copy question & your code
  btnCopyPrompt.addEventListener("click", async () => {
    const textToCopy = `=== STRICTROUTINES CHALLENGE ===\nTitle: ${qTitle.textContent}\nCategory: ${qBadge.textContent}\nDescription:\n${qDesc.textContent}\n\n=== SUBMITTED CODE ===\n${codeEditor.value}\n`;
    try {
      await navigator.clipboard.writeText(textToCopy);
      btnCopyPrompt.textContent = "✅ Copied to Clipboard!";
      setTimeout(() => {
        btnCopyPrompt.textContent = "📋 Copy question & your code";
      }, 2000);
    } catch (_) {
      btnCopyPrompt.textContent = "Failed to copy";
    }
  });

  // 9. Exit Challenge
  btnExit.addEventListener("click", () => {
    if (confirm("Are you sure you want to exit the challenge? Progress will be saved under the active challenge window.")) {
      try {
        if (document.fullscreenElement) {
          document.exitFullscreen().catch(() => {});
        }
      } catch (_) {}
      window.close();
    }
  });

  // Load challenge immediately
  loadChallenge();
});
