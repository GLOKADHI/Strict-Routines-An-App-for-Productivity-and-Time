# StrictRecords — Taufa AI Extension Bridge

Lightweight Chrome Extension to capture authentic `taufa.ai` session cookies and bridge real-time AI scoring directly into StrictRecords for Hourly Attendance validation.

---

## 3-Step Setup Guide

### 1. Load the Extension in Chrome
1. Open Google Chrome.
2. Navigate to `chrome://extensions/` in the address bar.
3. Turn **ON** the **Developer mode** toggle in the top-right corner.
4. Click the **Load unpacked** button in the top-left.
5. Select this folder:
   `C:\Users\lokad\Music\Practical\StrictRecords\taufa_extension`
6. The **StrictRecords · Taufa AI Bridge** icon will appear in your Chrome toolbar.

### 2. Log in to Taufa.ai
1. Go to [https://taufa.ai/chat](https://taufa.ai/chat).
2. Sign in with your Taufa account.
3. You will see a small green pill in the bottom right corner confirming:
   `StrictRecords Bridge: <your-email>`

### 3. Sync Cookies with 1 Click
1. Click the **StrictRecords · Taufa AI Bridge** extension icon in your Chrome toolbar.
2. Click **Capture & Sync to StrictRecords**.
3. You will see:
   `Success! Session cookies synced to StrictRecords.`

---

## Features

- **Automated Cookie Harvesting**: Securely reads `authjs.session-token` / `__Secure-authjs.session-token` directly from `taufa.ai` and sends it to the local StrictRecords API (`127.0.0.1:8765/api/taufa/session`).
- **Real-Time Hourly Attendance Scoring**: StrictRecords automatically uses your active session to query `https://taufa.ai/api/chat` and score proofs from `0` to `8` points according to strict academic and discipline criteria.
- **In-Extension Test Evaluator**: Enter any category and activity description directly in the extension popup and click **Ask Taufa.ai for Score** to view the live evaluation immediately.
- **Copy Cookie String**: One-click clipboard copy of the full cookie header for diagnostics.
