/**
 * StrictRecords Browser Companion — Scheduler Prebooking Script
 * Handles email extraction, ChatGPT validation, batch exam scheduling,
 * conflict checking, and authoritative prebooking.
 */
(() => {
  const get = id => document.getElementById(id);
  const text = (id, val) => { const el = get(id); if (el) el.textContent = val; };
  const badge = (id, val, tone = 'neutral') => {
    const el = get(id);
    if (el) {
      el.className = 'badge ' + tone;
      el.textContent = val;
    }
  };

  let currentCategory = 'EXAM_PREBOOK';
  let capturedEmail = null;
  let customAttachments = [];
  let currentValidation = null;
  let examTests = [];

  // Generate or retrieve idempotency key
  function generateIdempotencyKey() {
    return 'idemp_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9);
  }
  let currentIdempotencyKey = generateIdempotencyKey();

  async function api(path, body) {
    const response = await fetch('http://127.0.0.1:8765' + path, {
      cache: 'no-store',
      signal: AbortSignal.timeout(15000),
      ...(body ? {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-StrictRoutines-Setup': '1'
        },
        body: JSON.stringify(body)
      } : {})
    });
    const data = await response.json();
    if (!response.ok || data.success === false) {
      throw Error(data.error || 'Server request failed.');
    }
    return data;
  }

  // ── Category Switching ──
  function setCategory(cat) {
    currentCategory = cat;
    ['catBtnMovie', 'catBtnExam', 'catBtnTravel'].forEach(btnId => {
      const btn = get(btnId);
      if (btn) {
        if (btn.dataset.cat === cat) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      }
    });

    if (get('formMovie')) get('formMovie').hidden = (cat !== 'MOVIE_PREBOOK');
    if (get('formExam')) get('formExam').hidden = (cat !== 'EXAM_PREBOOK');
    if (get('formTravel')) get('formTravel').hidden = (cat !== 'TRAVEL_PREBOOK');

    updateReviewSummary();
  }

  // ── Exam Batch Test Management (Up to 4 tests) ──
  function initExamTests() {
    if (examTests.length === 0) {
      examTests = [{
        test_name: 'Core Assessment',
        subject: 'General',
        exam_date: new Date().toISOString().slice(0, 10),
        start_time: '10:00',
        end_time: '12:00',
        prep_buffer_minutes: 45,
        completion_buffer_minutes: 30
      }];
    }
    renderExamTests();
  }

  function renderExamTests() {
    const container = get('examTestsContainer');
    if (!container) return;
    container.innerHTML = '';

    examTests.forEach((t, idx) => {
      const card = document.createElement('div');
      card.className = 'test-card';
      card.innerHTML = `
        <div class="test-card-header">
          <span>Test #${idx + 1}: ${escapeHtml(t.test_name || 'Test')}</span>
          ${examTests.length > 1 ? `<button type="button" class="outline danger btn-remove-test" data-idx="${idx}" style="min-height:24px;padding:2px 8px;font-size:11px;">Remove</button>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">
          <div>
            <label style="font-size:11px;margin-top:2px;">Test Name</label>
            <input type="text" class="t-name" data-idx="${idx}" value="${escapeHtml(t.test_name || '')}" style="min-height:30px;padding:4px 6px;font-size:11.5px;" />
          </div>
          <div>
            <label style="font-size:11px;margin-top:2px;">Subject</label>
            <input type="text" class="t-subject" data-idx="${idx}" value="${escapeHtml(t.subject || '')}" style="min-height:30px;padding:4px 6px;font-size:11.5px;" />
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;">
          <div>
            <label style="font-size:11px;margin-top:2px;">Date</label>
            <input type="date" class="t-date" data-idx="${idx}" value="${t.exam_date || ''}" style="min-height:30px;padding:4px 6px;font-size:11.5px;" />
          </div>
          <div>
            <label style="font-size:11px;margin-top:2px;">Time In</label>
            <input type="time" class="t-start" data-idx="${idx}" value="${t.start_time || '10:00'}" style="min-height:30px;padding:4px 6px;font-size:11.5px;" />
          </div>
          <div>
            <label style="font-size:11px;margin-top:2px;">Time Out</label>
            <input type="time" class="t-end" data-idx="${idx}" value="${t.end_time || '12:00'}" style="min-height:30px;padding:4px 6px;font-size:11.5px;" />
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">
          <div>
            <label style="font-size:11px;margin-top:2px;">Prep Buffer (mins)</label>
            <input type="number" class="t-prep" data-idx="${idx}" value="${t.prep_buffer_minutes || 45}" style="min-height:30px;padding:4px 6px;font-size:11.5px;" />
          </div>
          <div>
            <label style="font-size:11px;margin-top:2px;">Completion Buffer (mins)</label>
            <input type="number" class="t-comp" data-idx="${idx}" value="${t.completion_buffer_minutes || 30}" style="min-height:30px;padding:4px 6px;font-size:11.5px;" />
          </div>
        </div>
      `;
      container.appendChild(card);
    });

    // Wire input changes
    container.querySelectorAll('.t-name').forEach(inp => {
      inp.addEventListener('input', e => { examTests[Number(e.target.dataset.idx)].test_name = e.target.value; updateReviewSummary(); });
    });
    container.querySelectorAll('.t-subject').forEach(inp => {
      inp.addEventListener('input', e => { examTests[Number(e.target.dataset.idx)].subject = e.target.value; updateReviewSummary(); });
    });
    container.querySelectorAll('.t-date').forEach(inp => {
      inp.addEventListener('change', e => { examTests[Number(e.target.dataset.idx)].exam_date = e.target.value; updateReviewSummary(); });
    });
    container.querySelectorAll('.t-start').forEach(inp => {
      inp.addEventListener('change', e => { examTests[Number(e.target.dataset.idx)].start_time = e.target.value; updateReviewSummary(); });
    });
    container.querySelectorAll('.t-end').forEach(inp => {
      inp.addEventListener('change', e => { examTests[Number(e.target.dataset.idx)].end_time = e.target.value; updateReviewSummary(); });
    });
    container.querySelectorAll('.t-prep').forEach(inp => {
      inp.addEventListener('input', e => { examTests[Number(e.target.dataset.idx)].prep_buffer_minutes = Number(e.target.value); updateReviewSummary(); });
    });
    container.querySelectorAll('.t-comp').forEach(inp => {
      inp.addEventListener('input', e => { examTests[Number(e.target.dataset.idx)].completion_buffer_minutes = Number(e.target.value); updateReviewSummary(); });
    });
    container.querySelectorAll('.btn-remove-test').forEach(btn => {
      btn.addEventListener('click', e => {
        const idx = Number(e.target.dataset.idx);
        examTests.splice(idx, 1);
        renderExamTests();
        updateReviewSummary();
      });
    });

    const addBtn = get('btnAddExamTest');
    if (addBtn) {
      addBtn.disabled = (examTests.length >= 4);
      addBtn.textContent = examTests.length >= 4 ? 'Maximum 4 Tests Reached' : '+ Add Another Test (up to 4)';
    }
  }

  function addExamTest() {
    if (examTests.length >= 4) return;
    const prev = examTests[examTests.length - 1];
    examTests.push({
      test_name: `Test #${examTests.length + 1}`,
      subject: prev ? prev.subject : 'General',
      exam_date: prev ? prev.exam_date : new Date().toISOString().slice(0, 10),
      start_time: '14:00',
      end_time: '16:00',
      prep_buffer_minutes: 30,
      completion_buffer_minutes: 30
    });
    renderExamTests();
    updateReviewSummary();
  }

  // ── Capture Opened Email ──
  async function captureOpenedEmail() {
    const btn = get('btnCaptureEmailDetails');
    const statusBox = get('schedulerStatusMsg');
    if (btn) btn.disabled = true;
    text('schedulerStatusMsg', 'Reading active webmail tab…');

    try {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!activeTab?.id) {
        throw Error('No active browser tab found. Please switch to your open email.');
      }

      let resp = null;
      try {
        resp = await chrome.tabs.sendMessage(activeTab.id, { type: 'CAPTURE_EMAIL_DETAILS' });
      } catch (_) {
        // Fallback: request service worker to query
        resp = await new Promise((resolve, reject) => {
          chrome.runtime.sendMessage({ type: 'CAPTURE_EMAIL_DETAILS' }, res => {
            if (chrome.runtime.lastError) reject(Error('Could not communicate with active tab. Ensure you have Gmail or Outlook open.'));
            else resolve(res);
          });
        });
      }

      if (!resp || !resp.success || !resp.email_data) {
        throw Error(resp?.error || 'Could not find an opened email in the active tab. Open an email message first.');
      }

      capturedEmail = resp.email_data;
      displayEmailSummary(resp.email_data, resp.client);

      // Trigger automatic AI validation
      await validateWithBackend(resp.email_data);
    } catch (err) {
      badge('aiValidationBadge', 'ERROR', 'err');
      text('schedulerStatusMsg', err.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function displayEmailSummary(data, client) {
    const card = get('emailSummaryCard');
    if (!card) return;
    card.hidden = false;

    text('emailSummarySender', `${data.sender_name || 'Unknown Sender'} ${data.sender_email ? '<' + data.sender_email + '>' : ''}`);
    text('emailSummarySubject', data.subject || 'No Subject');
    text('emailSummaryDate', data.date || 'Unknown Date');

    const allAtts = [...(data.attachments || []), ...customAttachments];
    text('emailSummaryAttCount', allAtts.length > 0 ? `${allAtts.length} attachment(s): ${allAtts.map(a => a.name).join(', ')}` : 'None');

    const preview = data.body ? (data.body.slice(0, 200) + '…') : 'No body text extracted.';
    text('emailSummaryBodyPreview', preview);
    text('emailClientBadge', client ? client.toUpperCase() : 'WEBMAIL');
  }

  // ── Validation Workflow ──
  async function validateWithBackend(emailPayload) {
    badge('aiValidationBadge', 'VALIDATING', 'warn');
    text('schedulerStatusMsg', 'ChatGPT is analyzing email contents and schedules…');
    get('aiExplanationBox').textContent = 'Evaluating date/time consistency, contradictions, and duration…';

    try {
      const payload = {
        email_data: emailPayload || capturedEmail || {
          subject: get('examName')?.value || 'Scheduled Event',
          body: 'Manual booking entry',
          date: new Date().toISOString().slice(0, 10),
          attachments: customAttachments
        },
        category_hint: currentCategory
      };

      const result = await api('/api/schedule/validate-email', payload);
      currentValidation = result;

      // Status Badge
      const st = result.validation_status || 'EXTRACTED';
      let tone = 'ok';
      if (st === 'NEEDS_REVIEW') tone = 'warn';
      else if (st === 'INCONSISTENT' || st === 'INCOMPLETE') tone = 'warn';
      else if (st === 'REJECTED') tone = 'err';
      badge('aiValidationBadge', st, tone);

      // Populate Form Fields
      applyExtractedDetails(result);

      // AI Explanation
      const contradictions = result.contradictions?.length ? `\n⚠️ Contradictions:\n• ` + result.contradictions.join('\n• ') : '';
      const missing = result.missing_fields?.length ? `\nℹ️ Missing Information:\n• ` + result.missing_fields.join('\n• ') : '';
      const recs = result.recommendations?.length ? `\n💡 Recommendations:\n• ` + result.recommendations.join('\n• ') : '';
      get('aiExplanationBox').textContent = (result.summary || 'Validation completed.') + contradictions + missing + recs;

      text('schedulerStatusMsg', `Validation complete (${result.provider || 'AI'}). Check proposed schedule below.`);
      updateReviewSummary();
    } catch (err) {
      badge('aiValidationBadge', 'EXTRACTED', 'neutral');
      text('schedulerStatusMsg', 'Local fallback active: ' + err.message);
      get('aiExplanationBox').textContent = 'Validation service encountered an issue. You may review and edit details manually.';
    }
  }

  function applyExtractedDetails(result) {
    if (result.extracted_category) {
      setCategory(result.extracted_category);
    }

    const slots = result.parsed_slots || [];

    if (currentCategory === 'MOVIE_PREBOOK') {
      if (result.event_name && get('movieName')) get('movieName').value = result.event_name;
      if (result.organization_or_venue && get('movieVenue')) get('movieVenue').value = result.organization_or_venue;
      if (slots.length > 0) {
        if (slots[0].date && get('movieDate')) get('movieDate').value = slots[0].date;
        if (slots[0].start_time && get('movieStart')) get('movieStart').value = slots[0].start_time;
        if (slots[0].end_time && get('movieEnd')) get('movieEnd').value = slots[0].end_time;
        if (slots[0].prep_buffer_minutes && get('moviePrep')) get('moviePrep').value = slots[0].prep_buffer_minutes;
        if (slots[0].completion_buffer_minutes && get('movieComp')) get('movieComp').value = slots[0].completion_buffer_minutes;
      }
    } else if (currentCategory === 'EXAM_PREBOOK') {
      if (result.organization_or_venue && get('examInstitution')) get('examInstitution').value = result.organization_or_venue;
      if (result.event_name && get('examName')) get('examName').value = result.event_name;

      if (slots.length > 0) {
        examTests = slots.slice(0, 4).map((s, idx) => ({
          test_name: s.slot_name || `Test #${idx + 1}`,
          subject: s.subject || 'General',
          exam_date: s.date || new Date().toISOString().slice(0, 10),
          start_time: s.start_time || '10:00',
          end_time: s.end_time || '12:00',
          prep_buffer_minutes: s.prep_buffer_minutes || 45,
          completion_buffer_minutes: s.completion_buffer_minutes || 30
        }));
        renderExamTests();
      }
    } else if (currentCategory === 'TRAVEL_PREBOOK') {
      const td = result.travel_details || {};
      if (td.travel_type && get('travelType')) get('travelType').value = td.travel_type;
      if (result.event_name && get('travelPurpose')) get('travelPurpose').value = result.event_name;
      if (td.origin && get('travelOrigin')) get('travelOrigin').value = td.origin;
      if (td.destination && get('travelDestination')) get('travelDestination').value = td.destination;
      if (td.departure_date && get('travelDepDate')) get('travelDepDate').value = td.departure_date;
      if (td.departure_time && get('travelDepTime')) get('travelDepTime').value = td.departure_time;
      if (td.return_date && get('travelRetDate')) get('travelRetDate').value = td.return_date;
      if (td.return_time && get('travelRetTime')) get('travelRetTime').value = td.return_time;
    }
  }

  // ── Conflict Check ──
  async function runConflictCheck() {
    text('conflictStatusText', 'Checking schedule conflicts against existing StrictRecords entries…');
    try {
      let slotsToCheck = [];
      if (currentCategory === 'EXAM_PREBOOK') {
        slotsToCheck = examTests.map(t => ({
          slot_name: t.test_name,
          date: t.exam_date,
          start_time: t.start_time,
          end_time: t.end_time,
          prep_buffer_minutes: t.prep_buffer_minutes,
          completion_buffer_minutes: t.completion_buffer_minutes
        }));
      } else if (currentCategory === 'MOVIE_PREBOOK') {
        slotsToCheck = [{
          slot_name: get('movieName')?.value || 'Movie',
          date: get('movieDate')?.value,
          start_time: get('movieStart')?.value,
          end_time: get('movieEnd')?.value,
          prep_buffer_minutes: Number(get('moviePrep')?.value || 30),
          completion_buffer_minutes: Number(get('movieComp')?.value || 30)
        }];
      } else if (currentCategory === 'TRAVEL_PREBOOK') {
        slotsToCheck = [{
          slot_name: get('travelPurpose')?.value || 'Travel',
          date: get('travelDepDate')?.value,
          start_time: get('travelDepTime')?.value,
          end_time: '23:59',
          prep_buffer_minutes: Number(get('travelDepBuffer')?.value || 90),
          completion_buffer_minutes: Number(get('travelArrBuffer')?.value || 60)
        }];
      }

      const res = await api('/api/schedule/conflict-check', {
        category: currentCategory,
        slots: slotsToCheck
      });

      if (res.valid && (!res.conflicts || res.conflicts.length === 0)) {
        text('conflictStatusText', '✅ No schedule conflicts detected. Ready to confirm.');
        get('conflictStatusText').style.color = '#34d399';
      } else {
        text('conflictStatusText', '⚠️ Conflicts detected:\n• ' + res.conflicts.join('\n• '));
        get('conflictStatusText').style.color = '#fbbf24';
      }
    } catch (e) {
      text('conflictStatusText', 'Conflict check error: ' + e.message);
      get('conflictStatusText').style.color = '#f87171';
    }
  }

  // ── Confirm and Prebook ──
  async function confirmPrebook() {
    const btn = get('btnConfirmPrebook');
    if (btn) btn.disabled = true;
    text('schedulerStatusMsg', 'Creating authoritative prebooking in StrictRecords ecosystem…');

    try {
      let bookingDetails = {};
      const allAtts = [...(capturedEmail?.attachments || []), ...customAttachments];

      if (currentCategory === 'EXAM_PREBOOK') {
        const isPeriodic = get('chkPeriodicExam')?.checked;
        bookingDetails = {
          institution: get('examInstitution')?.value || 'Institution',
          exam_name: get('examName')?.value || 'Examination',
          tests: examTests,
          supporting_documents: allAtts,
          periodic_schedule: isPeriodic ? {
            enabled: true,
            recurrence: get('periodicRecurrence')?.value || 'DAILY',
            start_date: get('periodicStartDate')?.value,
            end_date: get('periodicEndDate')?.value
          } : { enabled: false }
        };
      } else if (currentCategory === 'MOVIE_PREBOOK') {
        bookingDetails = {
          title: get('movieName')?.value || 'Movie Screening',
          cinema_venue: get('movieVenue')?.value || 'Cinema',
          date: get('movieDate')?.value,
          start_time: get('movieStart')?.value,
          end_time: get('movieEnd')?.value,
          prep_buffer_minutes: Number(get('moviePrep')?.value || 30),
          completion_buffer_minutes: Number(get('movieComp')?.value || 30),
          supporting_documents: allAtts
        };
      } else if (currentCategory === 'TRAVEL_PREBOOK') {
        bookingDetails = {
          title: `Travel: ${get('travelOrigin')?.value || 'Origin'} to ${get('travelDestination')?.value || 'Destination'}`,
          travel_type: get('travelType')?.value || 'FLIGHT',
          purpose: get('travelPurpose')?.value || 'Travel',
          origin: get('travelOrigin')?.value || 'Origin',
          destination: get('travelDestination')?.value || 'Destination',
          date: get('travelDepDate')?.value,
          start_time: get('travelDepTime')?.value,
          end_time: get('travelRetTime')?.value || '23:00',
          return_date: get('travelRetDate')?.value,
          prep_buffer_minutes: Number(get('travelDepBuffer')?.value || 90),
          completion_buffer_minutes: Number(get('travelArrBuffer')?.value || 60),
          supporting_documents: allAtts
        };
      }

      const res = await api('/api/schedule/confirm-email-prebook', {
        idempotency_key: currentIdempotencyKey,
        category: currentCategory,
        booking_details: bookingDetails
      });

      badge('aiValidationBadge', 'APPROVED', 'ok');
      text('schedulerStatusMsg', `🎉 ${res.message || 'Booking confirmed successfully!'}`);

      // Refresh fresh idempotency key for next booking
      currentIdempotencyKey = generateIdempotencyKey();

      // Refresh list
      loadConfirmedPrebooks();
    } catch (err) {
      text('schedulerStatusMsg', 'Confirmation failed: ' + err.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ── Load Confirmed Prebooks List ──
  async function loadConfirmedPrebooks() {
    const listEl = get('confirmedPrebooksList');
    if (!listEl) return;
    try {
      const res = await api('/api/schedule/email-prebooks');
      const exams = res.exams || [];
      const events = res.events || [];

      if (exams.length === 0 && events.length === 0) {
        listEl.innerHTML = '<p class="note">No active prebookings found.</p>';
        return;
      }

      const items = [];
      exams.forEach(ex => {
        items.push(`
          <div class="site" style="padding:8px 10px;font-size:11.5px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <strong style="color:#93c5fd;">📝 ${escapeHtml(ex.exam_name || 'Exam')}</strong>
              <span class="badge ok">${escapeHtml(ex.status || 'SCHEDULED')}</span>
            </div>
            <div style="color:var(--muted);margin-top:3px;">
              Date: <strong>${ex.exam_date}</strong> | Time: ${ex.official_start_time} - ${ex.official_end_time}
            </div>
            <div style="font-size:11px;color:#34d399;margin-top:2px;">
              🛡️ Excused attendance active · Full points preserved
            </div>
          </div>
        `);
      });

      events.forEach(ev => {
        const isMovie = ev.category === 'MOVIE_PREBOOK';
        const icon = isMovie ? '🎬' : '✈️';
        items.push(`
          <div class="site" style="padding:8px 10px;font-size:11.5px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <strong style="color:#93c5fd;">${icon} ${escapeHtml(ev.title || 'Event')}</strong>
              <span class="badge ok">${escapeHtml(ev.status || 'CONFIRMED')}</span>
            </div>
            <div style="color:var(--muted);margin-top:3px;">
              Date: <strong>${ev.event_date}</strong> | Time: ${ev.start_time} - ${ev.end_time} | Venue: ${escapeHtml(ev.venue_or_platform || ev.organization || '')}
            </div>
          </div>
        `);
      });

      listEl.innerHTML = items.join('');
    } catch (_) {
      listEl.innerHTML = '<p class="note">Could not refresh prebookings.</p>';
    }
  }

  // ── Update Review Summary ──
  function updateReviewSummary() {
    const rev = get('reviewSummaryBox');
    if (!rev) return;

    let summaryHtml = '';
    if (currentCategory === 'EXAM_PREBOOK') {
      const testsHtml = examTests.map((t, idx) => `
        <div style="margin-left:10px;margin-bottom:3px;font-size:11px;">
          • <strong>${escapeHtml(t.test_name)}</strong>: ${t.exam_date} (${t.start_time} - ${t.end_time}) [Buffers: +${t.prep_buffer_minutes}m / +${t.completion_buffer_minutes}m]
        </div>
      `).join('');
      summaryHtml = `
        <div><strong>Category:</strong> Exam Prebook (${examTests.length} test${examTests.length > 1 ? 's' : ''})</div>
        <div><strong>Institution:</strong> ${escapeHtml(get('examInstitution')?.value || 'Pending')}</div>
        <div style="margin-top:4px;"><strong>Tests:</strong></div>
        ${testsHtml}
        <div style="font-size:11px;color:#34d399;margin-top:4px;">Full points scheduled · Attendance auto-excused · NeoExamBrowser enforced</div>
      `;
    } else if (currentCategory === 'MOVIE_PREBOOK') {
      summaryHtml = `
        <div><strong>Category:</strong> Movie Prebook</div>
        <div><strong>Movie:</strong> ${escapeHtml(get('movieName')?.value || 'Pending')}</div>
        <div><strong>Cinema:</strong> ${escapeHtml(get('movieVenue')?.value || 'Pending')}</div>
        <div><strong>Date & Time:</strong> ${get('movieDate')?.value || 'Pending'} (${get('movieStart')?.value || '--:--'} - ${get('movieEnd')?.value || '--:--'})</div>
        <div><strong>Buffers:</strong> Prep ${get('moviePrep')?.value || 30}m, Completion ${get('movieComp')?.value || 30}m</div>
      `;
    } else if (currentCategory === 'TRAVEL_PREBOOK') {
      summaryHtml = `
        <div><strong>Category:</strong> Travel Prebook (${get('travelType')?.value || 'FLIGHT'})</div>
        <div><strong>Route:</strong> ${escapeHtml(get('travelOrigin')?.value || 'Origin')} ➔ ${escapeHtml(get('travelDestination')?.value || 'Destination')}</div>
        <div><strong>Departure:</strong> ${get('travelDepDate')?.value || 'Pending'} at ${get('travelDepTime')?.value || '--:--'}</div>
        ${get('travelRetDate')?.value ? `<div><strong>Return:</strong> ${get('travelRetDate')?.value} at ${get('travelRetTime')?.value || '--:--'}</div>` : ''}
        <div style="font-size:11px;color:#38bdf8;margin-top:4px;">Physical presence checks suppressed during travel window</div>
      `;
    }

    rev.innerHTML = summaryHtml;
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[m]);
  }

  // ── Setup & Initialization ──
  function setupScheduler() {
    initExamTests();

    get('btnCaptureEmailDetails')?.addEventListener('click', captureOpenedEmail);

    get('catBtnMovie')?.addEventListener('click', () => setCategory('MOVIE_PREBOOK'));
    get('catBtnExam')?.addEventListener('click', () => setCategory('EXAM_PREBOOK'));
    get('catBtnTravel')?.addEventListener('click', () => setCategory('TRAVEL_PREBOOK'));

    get('btnAddExamTest')?.addEventListener('click', addExamTest);

    get('chkPeriodicExam')?.addEventListener('change', e => {
      const panel = get('periodicOptionsPanel');
      if (panel) panel.hidden = !e.target.checked;
    });

    get('btnAddAttachments')?.addEventListener('click', () => {
      get('emailAttachmentInput')?.click();
    });

    get('emailAttachmentInput')?.addEventListener('change', e => {
      const files = Array.from(e.target.files || []);
      files.slice(0, 5).forEach(f => {
        if (!customAttachments.some(a => a.name === f.name)) {
          customAttachments.push({
            name: f.name,
            size_str: `${Math.round(f.size / 1024)} KB`,
            type: f.name.split('.').pop() || 'file'
          });
        }
      });
      displayEmailSummary(capturedEmail || { sender_name: 'Manual', subject: 'User Attachments', date: new Date().toISOString().slice(0, 10), body: '' });
      updateReviewSummary();
    });

    get('btnCheckConflicts')?.addEventListener('click', runConflictCheck);
    get('btnRevalidate')?.addEventListener('click', () => validateWithBackend(null));
    get('btnConfirmPrebook')?.addEventListener('click', confirmPrebook);

    get('btnCancelPrebook')?.addEventListener('click', () => {
      if (confirm('Discard proposed booking?')) {
        capturedEmail = null;
        customAttachments = [];
        get('emailSummaryCard').hidden = true;
        text('schedulerStatusMsg', 'Booking discarded.');
        badge('aiValidationBadge', 'EXTRACTED', 'neutral');
        initExamTests();
        updateReviewSummary();
      }
    });

    // Wire live field inputs
    ['movieName', 'movieVenue', 'movieDate', 'movieStart', 'movieEnd', 'moviePrep', 'movieComp',
     'examInstitution', 'examName',
     'travelType', 'travelPurpose', 'travelOrigin', 'travelDestination', 'travelDepDate', 'travelDepTime', 'travelRetDate', 'travelRetTime'].forEach(id => {
      get(id)?.addEventListener('input', updateReviewSummary);
      get(id)?.addEventListener('change', updateReviewSummary);
    });

    loadConfirmedPrebooks();
    updateReviewSummary();
  }

  document.addEventListener('strict-scheduler-open', () => {
    loadConfirmedPrebooks();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupScheduler);
  } else {
    setupScheduler();
  }
})();
