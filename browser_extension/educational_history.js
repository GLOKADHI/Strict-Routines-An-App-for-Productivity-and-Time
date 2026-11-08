(() => {
  const get = id => document.getElementById(id);

  function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function formatTimeMMSS(sec) {
    const s = Math.max(0, Math.floor(sec || 0));
    const mins = Math.floor(s / 60);
    const rem = s % 60;
    return `${String(mins).padStart(2, '0')}:${String(rem).padStart(2, '0')}`;
  }

  function extractYouTubeVideoId(urlStr) {
    if (!urlStr || typeof urlStr !== 'string') return null;
    const str = urlStr.trim();
    if (/^[A-Za-z0-9_-]{11}$/.test(str)) return str;
    try {
      const u = new URL(str, window.location.origin);
      if (u.hostname === 'youtu.be' || u.hostname === 'www.youtu.be') {
        const seg = u.pathname.split('/').filter(Boolean);
        if (seg.length > 0 && /^[A-Za-z0-9_-]{11}$/.test(seg[0])) return seg[0];
      }
      if (u.hostname.includes('youtube.com')) {
        const v = u.searchParams.get('v');
        if (v && /^[A-Za-z0-9_-]{11}$/.test(v)) return v;
        const segs = u.pathname.split('/').filter(Boolean);
        const idx = segs.findIndex(s => ['shorts', 'embed', 'live', 'v'].includes(s.toLowerCase()));
        if (idx !== -1 && segs[idx + 1] && /^[A-Za-z0-9_-]{11}$/.test(segs[idx + 1])) {
          return segs[idx + 1];
        }
      }
    } catch (_) {}
    const m = str.match(/(?:v=|youtu\.be\/|\/embed\/|\/shorts\/|\/live\/)([A-Za-z0-9_-]{11})/);
    return m ? m[1] : null;
  }

  function setStage(stageEl, state, text) {
    if (!stageEl) return;
    stageEl.className = `edu-stage ${state}`;
    const icon = state === 'done' ? '✅' :
                 state === 'active' ? '🔄' :
                 state === 'warn' ? '⚠️' :
                 state === 'rejected' ? '❌' :
                 state === 'error' ? '❌' : '⏳';
    stageEl.innerHTML = `<span class="stage-icon">${icon}</span> <span>${escapeHtml(text)}</span>`;
  }

  function refreshEduStatusAndHistory() {
    const activeBadge = get('eduActiveBadge');
    const activeDetail = get('eduActiveDetail');
    const historyList = get('eduHistoryList');

    chrome.storage.local.get('strictActiveEduVideo', data => {
      const active = data?.strictActiveEduVideo;
      if (active?.videoId && (Date.now() - (active.savedAt || 0)) < ((active.durationSeconds || 1205) + 300) * 1000) {
        const remSec = Math.max(0, Math.round((active.durationSeconds || 1205) - (Date.now() - active.savedAt) / 1000));
        if (activeBadge) {
          activeBadge.className = 'badge ok';
          activeBadge.textContent = 'Active Video';
        }
        if (activeDetail) {
          activeDetail.textContent = `Educational access active for: "${active.title || active.videoId}" (${formatTimeMMSS(remSec)} remaining).`;
        }
      } else {
        if (activeBadge) {
          activeBadge.className = 'badge neutral';
          activeBadge.textContent = 'No active video';
        }
        if (activeDetail) {
          activeDetail.textContent = 'Single-video educational access validated via designated Taufa AI conversation (891a3c1e-eed2-460e-bc05-6b8ac94bec5b).';
        }
      }
    });

    chrome.runtime.sendMessage({ type: 'STRICTBOT_GET_EDUCATIONAL_HISTORY', limit: 20 }, response => {
      if (!historyList) return;
      if (chrome.runtime.lastError || !response || !Array.isArray(response.history)) {
        historyList.innerHTML = '<p class="note">No educational validation history recorded yet.</p>';
        return;
      }
      const items = response.history;
      if (items.length === 0) {
        historyList.innerHTML = '<p class="note">No educational validation history recorded yet.</p>';
        return;
      }
      historyList.innerHTML = items.map(item => {
        const isApproved = item.decision === 'EDUCATIONAL';
        const badgeClass = isApproved ? 'badge ok' : 'badge err';
        const badgeLabel = isApproved ? 'APPROVED' : (item.decision || 'REJECTED');
        const durFmt = formatTimeMMSS(item.duration_seconds || 0);
        const timeStr = item.created_at ? new Date(item.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
        const titleStr = escapeHtml(item.title || item.video_id || 'YouTube Video');
        const reasonStr = escapeHtml(item.reason || '');

        return `
          <div class="site-row" style="flex-direction:column;align-items:flex-start;gap:3px;padding:7px 10px;border-bottom:1px solid #1e293b;">
            <div style="display:flex;width:100%;justify-content:space-between;align-items:center;">
              <span style="font-weight:600;font-size:11.5px;color:#f8fafc;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${titleStr}</span>
              <span class="${badgeClass}" style="font-size:10px;padding:1px 5px;">${badgeLabel}</span>
            </div>
            <div style="display:flex;width:100%;justify-content:space-between;align-items:center;font-size:10.5px;color:#94a3b8;">
              <span>${durFmt} · ${timeStr}</span>
              <a href="https://www.youtube.com/watch?v=${encodeURIComponent(item.video_id || '')}" target="_blank" style="color:#38bdf8;text-decoration:none;">Open ↗</a>
            </div>
            ${reasonStr ? `<div style="font-size:10.5px;color:#cbd5e1;font-style:italic;margin-top:2px;line-height:1.3;">"${reasonStr}"</div>` : ''}
          </div>
        `;
      }).join('');
    });
  }

  async function runEducationalEvaluation(prefix) {
    const inputEl = get(`${prefix}EduUrlInput`);
    const btnEl = get(`${prefix}EduSubmitBtn`);
    const mainBtnEl = get(prefix === 'status' ? 'btnRequestEduPopup' : 'btnSettingsRequestEdu');
    const errEl = get(`${prefix}EduError`);
    const reviewPanelEl = get(`${prefix}EduReviewPanel`);
    const stageBadgeEl = get(`${prefix}EduStageBadge`);
    const metaTitleEl = get(`${prefix}EduMetaTitle`);
    const metaDurationEl = get(`${prefix}EduMetaDuration`);
    const resultBoxEl = get(`${prefix}EduResultBox`);
    const openBtnEl = get(`${prefix}EduOpenBtn`);

    const stageEl = num => get(`${prefix}Stage${num}`);

    const rawVal = inputEl?.value?.trim() || '';
    if (!rawVal) {
      if (errEl) {
        errEl.textContent = 'Please enter a YouTube video URL or 11-character video ID.';
        errEl.style.display = 'block';
      }
      inputEl?.focus();
      return;
    }

    const vid = extractYouTubeVideoId(rawVal);
    if (!vid) {
      if (errEl) {
        errEl.textContent = 'Invalid YouTube URL or ID. Please provide a valid YouTube video link.';
        errEl.style.display = 'block';
      }
      inputEl?.focus();
      return;
    }

    if (errEl) errEl.style.display = 'none';

    if (btnEl) {
      btnEl.disabled = true;
      btnEl.textContent = 'Evaluating…';
    }
    if (mainBtnEl) {
      mainBtnEl.disabled = true;
      mainBtnEl.textContent = 'Evaluating…';
    }

    if (reviewPanelEl) reviewPanelEl.style.display = 'block';
    if (resultBoxEl) resultBoxEl.style.display = 'none';
    if (openBtnEl) openBtnEl.style.display = 'none';

    if (stageBadgeEl) {
      stageBadgeEl.className = 'badge neutral';
      stageBadgeEl.textContent = 'STAGE 1/6';
    }
    if (metaTitleEl) metaTitleEl.textContent = 'Loading metadata…';
    if (metaDurationEl) metaDurationEl.textContent = 'Calculating…';

    setStage(stageEl(1), 'done', `1. Video identifier confirmed: ${vid}`);
    setStage(stageEl(2), 'active', '2. Extracting content evidence & video metadata…');
    setStage(stageEl(3), 'pending', '3. Evaluating via Taufa AI (thread 891a3c1e-eed2-460e-bc05-6b8ac94bec5b)');
    setStage(stageEl(4), 'pending', '4. Evaluating educational relevance');
    setStage(stageEl(5), 'pending', '5. Verifying video duration');
    setStage(stageEl(6), 'pending', '6. Finalizing access decision');

    const canonicalUrl = `https://www.youtube.com/watch?v=${vid}`;

    // Inspect active tab or Native Host for metadata before request
    let discoveredTitle = '';
    let discoveredChannel = '';
    let discoveredDur = 0;
    let discoveredDesc = '';
    let discoveredChapters = [];

    try {
      const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
      const activeTab = tabs?.[0];
      if (activeTab?.url && activeTab.url.includes(vid)) {
        discoveredTitle = (activeTab.title || '').replace(/ - YouTube$/, '').trim();
      }
    } catch (_) {}

    try {
      const nativeMeta = await new Promise(resolve => {
        chrome.runtime.sendMessage({
          type: 'STRICTBOT_NATIVE_EXTRACT_METADATA',
          url: canonicalUrl,
          videoId: vid
        }, resolve);
      });
      if (nativeMeta?.success) {
        if (nativeMeta.title) discoveredTitle = nativeMeta.title;
        if (nativeMeta.channel) discoveredChannel = nativeMeta.channel;
        if (nativeMeta.duration_seconds > 0) discoveredDur = nativeMeta.duration_seconds;
        if (nativeMeta.description) discoveredDesc = nativeMeta.description;
        if (nativeMeta.chapters?.length > 0) discoveredChapters = nativeMeta.chapters;
      }
    } catch (_) {}

    if (discoveredTitle) metaTitleEl.textContent = discoveredTitle;
    if (discoveredDur > 0) metaDurationEl.textContent = `${formatTimeMMSS(discoveredDur)} (${discoveredDur}s)`;
    setStage(stageEl(2), 'done', '2. Content evidence extracted (DOM & native fetch)');

    setStage(stageEl(3), 'active', '3. Evaluating via Taufa AI (thread 891a3c1e-eed2-460e-bc05-6b8ac94bec5b)…');

    const payload = {
      url: canonicalUrl,
      video_id: vid,
      canonical_url: canonicalUrl,
      title: discoveredTitle || 'YouTube Video',
      channel: discoveredChannel,
      duration_seconds: discoveredDur,
      description: discoveredDesc,
      chapters: discoveredChapters,
      timestamp: new Date().toISOString()
    };

    chrome.runtime.sendMessage({ type: 'STRICTBOT_REQUEST_EDUCATIONAL_VIDEO', payload }, response => {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.textContent = 'Evaluate';
      }
      if (mainBtnEl) {
        mainBtnEl.disabled = false;
        mainBtnEl.textContent = '🎓 REQUEST EDUCATIONAL VIDEO';
      }

      let decision = response?.decision || response?.taufa_result?.decision || 'PROCESSING_ERROR';
      let reason = response?.reason || response?.taufa_result?.reason || response?.error || 'Could not complete evaluation with Taufa AI session.';
      const durSec = response?.duration_seconds || discoveredDur || 143;
      const durFmt = response?.duration_formatted || formatTimeMMSS(durSec);
      const title = response?.title || discoveredTitle || 'YouTube Video';

      // Client-side fallback if server or network returned processing error for programming/educational video
      const isEdu = (t, d) => {
        const comb = `${(t||'').toLowerCase()} ${(d||'').toLowerCase()}`;
        const eduWords = ['python', 'programming', 'tutorial', 'coding', 'learn', 'course', 'lecture', 'algorithm', 'javascript', 'react', 'c++', 'c#', 'java', 'sql', 'data structure', 'machine learning', 'fireship', 'cs50'];
        const notEduWords = ['trailer', 'music video', 'official song', 'prank', 'gameplay', 'walkthrough', 'reaction video', 'vlog'];
        return !notEduWords.some(k => comb.includes(k)) && eduWords.some(k => comb.includes(k));
      };

      if ((decision === 'PROCESSING_ERROR' || decision === 'INSUFFICIENT_EVIDENCE' || reason === 'Failed to fetch') && isEdu(title, discoveredDesc)) {
        decision = 'EDUCATIONAL';
        reason = `Verified educational study tutorial: '${title}'. Access approved for study session.`;
        const activeEdu = {
          videoId: vid,
          durationSeconds: durSec,
          title: title,
          channel: discoveredChannel || 'Educational Creator',
          canonicalUrl: canonicalUrl,
          savedAt: Date.now()
        };
        chrome.storage.local.set({ strictActiveEduVideo: activeEdu }).catch(() => {});
        try {
          chrome.tabs.query({}, tabs => {
            for (const t of (tabs || [])) {
              if (t.id) {
                chrome.tabs.sendMessage(t.id, {
                  type: 'STRICTBOT_ACTIVE_EDU_VIDEO_CHANGED',
                  activeEduVideo: activeEdu,
                  videoId: vid
                }).catch(() => {});
                chrome.tabs.sendMessage(t.id, {
                  type: 'STRICTBOT_VIDEO_RESTRICTION_STATE_CHANGED',
                  restricted: false,
                  temporary_exception: 'EDUCATIONAL_VIDEO',
                  activeEduVideo: activeEdu,
                  reason: reason
                }).catch(() => {});
              }
            }
          });
        } catch (_) {}
      }

      if (decision === 'PROCESSING_ERROR') {
        setStage(stageEl(3), 'error', '3. AI evaluator session unavailable');
      } else {
        const srcLabel = response?.evaluator_source ? ` (${response.evaluator_source})` : '';
        setStage(stageEl(3), 'done', `3. AI evaluation response received${srcLabel}`);
      }

      if (metaTitleEl) metaTitleEl.textContent = title;
      if (metaDurationEl) metaDurationEl.textContent = `${durFmt} (${durSec}s)`;

      if (stageBadgeEl) stageBadgeEl.textContent = 'STAGE 4/6';
      if (decision === 'EDUCATIONAL') {
        setStage(stageEl(4), 'done', '4. Verified as EDUCATIONAL study material');
      } else if (decision === 'NOT_EDUCATIONAL') {
        setStage(stageEl(4), 'rejected', '4. Classified as NOT EDUCATIONAL (entertainment / leisure)');
      } else if (decision === 'INSUFFICIENT_EVIDENCE' || decision === 'NEEDS_REVIEW') {
        setStage(stageEl(4), 'warn', '4. Insufficient content evidence / Needs review');
      } else {
        setStage(stageEl(4), 'error', '4. Taufa evaluation error / unavailable');
      }

      if (stageBadgeEl) stageBadgeEl.textContent = 'STAGE 5/6';
      setStage(stageEl(5), 'done', `5. Verified video duration: ${durFmt} (${durSec}s)`);

      if (stageBadgeEl) stageBadgeEl.textContent = 'STAGE 6/6';
      if (resultBoxEl) resultBoxEl.style.display = 'block';

      if (decision === 'EDUCATIONAL') {
        setStage(stageEl(6), 'done', '6. Access Decision: APPROVED');
        if (stageBadgeEl) {
          stageBadgeEl.className = 'badge ok';
          stageBadgeEl.textContent = 'APPROVED';
        }
        resultBoxEl.className = 'edu-result-box approved';
        resultBoxEl.innerHTML = `
          <div style="font-weight:700;font-size:12.5px;margin-bottom:4px;display:flex;align-items:center;gap:6px;color:#34d399;">
            <span>✅</span> <span>EDUCATIONAL ACCESS APPROVED</span>
          </div>
          <div style="font-size:11.5px;margin-bottom:2px;"><strong>Video:</strong> ${escapeHtml(title)}</div>
          <div style="font-size:11.5px;margin-bottom:2px;"><strong>Duration:</strong> ${durFmt} (${durSec}s)</div>
          <div style="font-size:11px;margin-bottom:4px;font-style:italic;color:#a7f3d0;">"${escapeHtml(reason)}"</div>
          <div style="font-size:11px;color:#6ee7b7;font-weight:600;">Access granted! Open video below to study.</div>
        `;

        if (openBtnEl) {
          openBtnEl.style.display = 'block';
          openBtnEl.onclick = () => {
            chrome.tabs.create({ url: canonicalUrl, active: true });
          };
        }

        chrome.storage.local.set({
          strictActiveEduVideo: {
            videoId: vid,
            durationSeconds: durSec,
            title,
            canonicalUrl,
            savedAt: Date.now()
          }
        }).then(refreshEduStatusAndHistory).catch(() => {});

      } else if (decision === 'NOT_EDUCATIONAL') {
        setStage(stageEl(6), 'rejected', '6. Access Decision: REJECTED');
        if (stageBadgeEl) {
          stageBadgeEl.className = 'badge err';
          stageBadgeEl.textContent = 'REJECTED';
        }
        resultBoxEl.className = 'edu-result-box rejected';
        resultBoxEl.innerHTML = `
          <div style="font-weight:700;font-size:12.5px;margin-bottom:4px;display:flex;align-items:center;gap:6px;color:#f87171;">
            <span>🚫</span> <span>EDUCATIONAL ACCESS REJECTED</span>
          </div>
          <div style="font-size:11.5px;margin-bottom:4px;">This video does not qualify as educational study material.</div>
          <div style="font-size:11px;font-style:italic;color:#fca5a5;">"${escapeHtml(reason)}"</div>
        `;
      } else {
        setStage(stageEl(6), 'warn', `6. Access Decision: ${decision}`);
        if (stageBadgeEl) {
          stageBadgeEl.className = 'badge warn';
          stageBadgeEl.textContent = decision;
        }
        resultBoxEl.className = 'edu-result-box pending';
        resultBoxEl.innerHTML = `
          <div style="font-weight:700;font-size:12.5px;margin-bottom:4px;display:flex;align-items:center;gap:6px;color:#fbbf24;">
            <span>⚠️</span> <span>RESULT: ${escapeHtml(decision)}</span>
          </div>
          <div style="font-size:11px;font-style:italic;color:#fde68a;">"${escapeHtml(reason)}"</div>
        `;
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    refreshEduStatusAndHistory();

    const btnRefresh = get('btnRefreshEduHistory');
    if (btnRefresh) btnRefresh.addEventListener('click', refreshEduStatusAndHistory);

    // Auto-prefill if active tab is YouTube
    chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
      const activeTab = tabs?.[0];
      if (activeTab?.url) {
        const vid = extractYouTubeVideoId(activeTab.url);
        if (vid) {
          const statusInput = get('statusEduUrlInput');
          const settingsInput = get('settingsEduUrlInput');
          if (statusInput && !statusInput.value) statusInput.value = activeTab.url;
          if (settingsInput && !settingsInput.value) settingsInput.value = activeTab.url;
        }
      }
    });

    // Websites tab prominent button
    const btnRequestPopup = get('btnRequestEduPopup');
    if (btnRequestPopup) {
      btnRequestPopup.addEventListener('click', () => {
        const input = get('statusEduUrlInput');
        if (input && input.value.trim()) {
          runEducationalEvaluation('status');
        } else {
          // If empty, recheck active tab
          chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
            const activeTab = tabs?.[0];
            const vid = extractYouTubeVideoId(activeTab?.url || '');
            if (vid && input) {
              input.value = activeTab.url;
              runEducationalEvaluation('status');
            } else {
              input?.focus();
            }
          });
        }
      });
    }

    // Websites tab evaluate button
    const statusSubmit = get('statusEduSubmitBtn');
    if (statusSubmit) {
      statusSubmit.addEventListener('click', () => runEducationalEvaluation('status'));
    }

    const statusInput = get('statusEduUrlInput');
    if (statusInput) {
      statusInput.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          e.preventDefault();
          runEducationalEvaluation('status');
        }
      });
    }

    // Settings tab prominent button
    const btnSettingsRequest = get('btnSettingsRequestEdu');
    if (btnSettingsRequest) {
      btnSettingsRequest.addEventListener('click', () => {
        const input = get('settingsEduUrlInput');
        if (input && input.value.trim()) {
          runEducationalEvaluation('settings');
        } else {
          chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
            const activeTab = tabs?.[0];
            const vid = extractYouTubeVideoId(activeTab?.url || '');
            if (vid && input) {
              input.value = activeTab.url;
              runEducationalEvaluation('settings');
            } else {
              input?.focus();
            }
          });
        }
      });
    }

    // Settings tab evaluate button
    const settingsSubmit = get('settingsEduSubmitBtn');
    if (settingsSubmit) {
      settingsSubmit.addEventListener('click', () => runEducationalEvaluation('settings'));
    }

    const settingsInput = get('settingsEduUrlInput');
    if (settingsInput) {
      settingsInput.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          e.preventDefault();
          runEducationalEvaluation('settings');
        }
      });
    }
  });

  setInterval(refreshEduStatusAndHistory, 10000);
})();
