/**
 * StrictRecords · Taufa AI Bridge — Background Service Worker
 * Manages cookie capture, synchronization to local StrictRecords server,
 * and direct/relay prompt execution against https://taufa.ai/api/chat.
 */

const LOCAL_SERVER_URLS = [
  "http://127.0.0.1:8765",
  "http://localhost:8765",
  "http://127.0.0.1:8000",
  "http://localhost:8000"
];

// Helper to get all taufa.ai cookies across domains and paths
async function getTaufaCookies() {
  try {
    const targets = [
      { domain: "taufa.ai" },
      { domain: ".taufa.ai" },
      { url: "https://taufa.ai" },
      { url: "https://taufa.ai/chat" },
      { url: "https://taufa.ai/api/auth/session" }
    ];

    const cookieMap = new Map();
    for (const t of targets) {
      try {
        const found = await chrome.cookies.getAll(t);
        for (const c of found) {
          cookieMap.set(c.name, c);
        }
      } catch (e) {
        // ignore individual search failure
      }
    }

    const merged = Array.from(cookieMap.values());
    const cookieHeader = merged.map(c => `${c.name}=${c.value}`).join("; ");
    return { cookies: merged, cookieHeader };
  } catch (err) {
    console.error("[TaufaBridge] Error retrieving cookies:", err);
    return { cookies: [], cookieHeader: "" };
  }
}

// Fetch user profile from Taufa.ai session endpoint
async function checkTaufaUserSession() {
  try {
    const res = await fetch("https://taufa.ai/api/auth/session", {
      credentials: "include",
      headers: { "Accept": "application/json" }
    });
    if (res.ok) {
      const data = await res.json();
      if (data && data.user) {
        return { authenticated: true, user: data.user, expires: data.expires };
      }
    }
  } catch (err) {
    console.debug("[TaufaBridge] Session check note:", err);
  }
  return { authenticated: false, user: null };
}

// Sync session cookies to StrictRecords local server
async function syncSessionToStrictRecords(userProfile = null) {
  const { cookies, cookieHeader } = await getTaufaCookies();
  if (!cookieHeader) {
    return {
      success: false,
      error: "No Taufa cookies found in browser. Please open https://taufa.ai/chat and log in first."
    };
  }

  let user = userProfile;
  if (!user) {
    const check = await checkTaufaUserSession();
    user = check.user;
  }

  const payload = {
    cookies: cookieHeader,
    cookies_list: cookies,
    user: user || {},
    source: "taufa_chrome_extension"
  };

  let lastErr = null;
  for (const baseUrl of LOCAL_SERVER_URLS) {
    try {
      const resp = await fetch(`${baseUrl}/api/taufa/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (resp.ok) {
        const json = await resp.json();
        console.log(`[TaufaBridge] Synced to ${baseUrl}:`, json);
        return { success: true, server: baseUrl, cookieCount: cookies.length, data: json };
      }
    } catch (err) {
      lastErr = err.message;
    }
  }

  return {
    success: false,
    error: `Could not reach StrictRecords server on port 8765 (${lastErr || "Connection refused"}). Make sure StrictRecords GUI is open.`
  };
}

// Proof scoring is performed by the app with actual original image bytes.
// A text description or image count must never stand in for an attachment.
async function askTaufaScore(category, description, imageCount = 0) {
  return {
    success: false,
    error: "Submit actual image proof in StrictRecords or the paired Android app. The extension connects your Taufa login; it cannot score an image count or a description without image files."
  };
}

// Listen for messages from popup or content script
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "GET_STATUS") {
    (async () => {
      const session = await checkTaufaUserSession();
      const { cookies, cookieHeader } = await getTaufaCookies();
      sendResponse({
        taufa: session,
        hasCookies: Boolean(cookieHeader),
        cookieCount: cookies.length
      });
    })();
    return true; // async reply
  }

  if (message.action === "SYNC_COOKIES") {
    (async () => {
      const res = await syncSessionToStrictRecords(message.user);
      sendResponse(res);
    })();
    return true;
  }

  if (message.action === "GET_COOKIE_HEADER") {
    (async () => {
      const { cookieHeader, cookies } = await getTaufaCookies();
      sendResponse({ cookieHeader, cookies });
    })();
    return true;
  }

  if (message.action === "ASK_TAUFA") {
    (async () => {
      const res = await askTaufaScore(message.category, message.description, message.imageCount || 0);
      sendResponse(res);
    })();
    return true;
  }
});

// Auto-sync when taufa auth cookies change
chrome.cookies.onChanged.addListener((changeInfo) => {
  const domain = changeInfo.cookie.domain || "";
  if (domain.includes("taufa.ai")) {
    const name = changeInfo.cookie.name;
    if (name.includes("session") || name.includes("authjs")) {
      console.log("[TaufaBridge] Session cookie changed:", name);
      chrome.storage.local.get(["autoSync"], (data) => {
        if (data.autoSync) {
          syncSessionToStrictRecords();
        }
      });
    }
  }
});
