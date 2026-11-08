/**
 * StrictRecords – Sensitive Page Protection (Browser Extension Module)
 * =====================================================================
 * Identifies sensitive financial, banking, crypto-wallet, personal email, and
 * payment/checkout pages (including redirects like Suno -> checkout.stripe.com -> Suno
 * and embedded payment checkout frames) and immediately notifies the authoritative
 * Windows EXE controller to activate the 5-minute visual screen monitoring privacy shield.
 *
 * Strict Privacy & Security Guarantees:
 * - NEVER reads or inspects password inputs, credit card fields, CVVs, OTPs, seed phrases,
 *   private keys, or email message contents.
 * - NEVER stores or logs full sensitive URLs, query parameters, payment tokens, or session IDs.
 * - Transmits only sanitized hostname (`matched_domain`), `category`, `sub_type`, `tab_id`,
 *   `browser`, and `event_id` to the local Windows EXE (`http://127.0.0.1:8765`).
 */

(() => {
  const SPP_API_ROOT = "http://127.0.0.1:8765/api/sensitive_page_protection";

  const CATEGORIES = {
    BANKING_FINANCIAL: "BANKING_FINANCIAL",
    CRYPTO_WALLET: "CRYPTO_WALLET",
    EMAIL_PERSONAL: "EMAIL_PERSONAL",
    PAYMENT_CHECKOUT: "PAYMENT_CHECKOUT",
    NON_SENSITIVE: "NON_SENSITIVE",
  };

  const PAYMENT_DOMAINS = [
    "checkout.stripe.com",
    "buy.stripe.com",
    "billing.stripe.com",
    "invoice.stripe.com",
    "pay.stripe.com",
    "hooks.stripe.com",
    "checkout.razorpay.com",
    "api.razorpay.com",
    "pages.razorpay.com",
    "razorpay.com",
    "paytm.com",
    "securegw.paytm.in",
    "phonepe.com",
    "mercury.phonepe.com",
    "cashfree.com",
    "payments.cashfree.com",
    "payu.in",
    "secure.payu.in",
    "billdesk.com",
    "pgi.billdesk.com",
    "juspay.in",
    "api.juspay.in",
    "checkout.paddle.com",
    "buy.paddle.com",
    "paddle.com",
    "lemonsqueezy.com",
    "app.lemonsqueezy.com",
    "checkout.adyen.com",
    "adyen.com",
    "braintreepayments.com",
    "squareup.com",
    "checkout.square.site",
    "pay.google.com",
    "payments.google.com",
    "pay.amazon.com",
    "pay.amazon.in",
    "2checkout.com",
    "klarna.com",
    "pay.klarna.com",
    "afterpay.com",
    "affirm.com",
    "ccavenue.com",
    "instamojo.com",
    "gumroad.com",
  ];

  const BANKING_DOMAINS = [
    "hdfcbank.com",
    "netbanking.hdfcbank.com",
    "icicibank.com",
    "infinity.icicibank.com",
    "onlinesbi.sbi",
    "retail.onlinesbi.sbi",
    "sbi.co.in",
    "axisbank.com",
    "omni.axisbank.co.in",
    "kotak.com",
    "netbanking.kotak.com",
    "yesbank.in",
    "pnbindia.in",
    "netpnb.com",
    "bankofbaroda.in",
    "bobibanking.com",
    "canarabank.com",
    "indusind.com",
    "idfcfirstbank.com",
    "federalbank.co.in",
    "rblbank.com",
    "unionbankofindia.co.in",
    "chase.com",
    "secure.chase.com",
    "bankofamerica.com",
    "wellsfargo.com",
    "citi.com",
    "online.citi.com",
    "citibank.co.in",
    "hsbc.com",
    "hsbc.co.uk",
    "hsbc.co.in",
    "barclays.co.uk",
    "capitalone.com",
    "americanexpress.com",
    "discover.com",
    "usbank.com",
    "pnc.com",
    "td.com",
    "rbcroyalbank.com",
    "scotiabank.com",
    "bmo.com",
    "lloydsbank.com",
    "natwest.com",
    "santander.co.uk",
    "standardchartered.com",
    "dbs.com",
    "paypal.com",
    "wise.com",
    "revolut.com",
    "mercury.com",
    "brex.com",
    "dashboard.stripe.com",
    "dashboard.razorpay.com",
    "zerodha.com",
    "kite.zerodha.com",
    "groww.in",
    "upstox.com",
    "angelone.in",
    "robinhood.com",
    "fidelity.com",
    "schwab.com",
    "vanguard.com",
    "interactivebrokers.com",
    "venmo.com",
    "cash.app",
  ];

  const CRYPTO_DOMAINS = [
    "coinbase.com",
    "wallet.coinbase.com",
    "binance.com",
    "binance.us",
    "kraken.com",
    "pro.kraken.com",
    "metamask.io",
    "portfolio.metamask.io",
    "phantom.app",
    "trustwallet.com",
    "ledger.com",
    "live.ledger.com",
    "trezor.io",
    "suite.trezor.io",
    "uniswap.org",
    "app.uniswap.org",
    "opensea.io",
    "crypto.com",
    "kucoin.com",
    "bybit.com",
    "okx.com",
    "bitfinex.com",
    "gemini.com",
    "bitstamp.net",
    "wazirx.com",
    "coindcx.com",
    "coinswitch.co",
    "zebpay.com",
    "blockchain.com",
    "rabby.io",
    "rainbow.me",
    "1inch.io",
    "aave.com",
  ];

  const EMAIL_PERSONAL_DOMAINS = [
    "mail.google.com",
    "gmail.com",
    "inbox.google.com",
    "outlook.live.com",
    "outlook.office.com",
    "outlook.office365.com",
    "outlook.com",
    "hotmail.com",
    "mail.yahoo.com",
    "mail.proton.me",
    "proton.me",
    "protonmail.com",
    "mail.zoho.com",
    "mail.zoho.in",
    "icloud.com",
    "mail.aol.com",
    "fastmail.com",
    "tuta.com",
    "mail.tutanota.com",
    "myaccount.google.com",
    "accounts.google.com",
    "account.microsoft.com",
    "login.live.com",
    "appleid.apple.com",
    "account.apple.com",
    "vault.bitwarden.com",
    "my.1password.com",
    "lastpass.com",
    "app.dashlane.com",
  ];

  // Track active sensitive tabs locally: tabId -> { category, sub_type, matched_domain, lastSentAt }
  const activeSensitiveTabs = new Map();
  let customDomainsMap = {}; // domain -> category
  let lastDesktopStatus = null;

  function sanitizeHost(urlStr) {
    if (!urlStr || typeof urlStr !== "string") return "";
    try {
      const normalized = urlStr.includes("://") ? urlStr : `https://${urlStr}`;
      const u = new URL(normalized);
      if (!["http:", "https:"].includes(u.protocol)) return "";
      return (u.hostname || "").toLowerCase().replace(/\.$/, "");
    } catch (_) {
      return "";
    }
  }

  function extractHostAndPath(urlStr) {
    if (!urlStr || typeof urlStr !== "string") return { host: "", path: "" };
    try {
      const normalized = urlStr.includes("://") ? urlStr : `https://${urlStr}`;
      const u = new URL(normalized);
      if (!["http:", "https:"].includes(u.protocol)) return { host: "", path: "" };
      return {
        host: (u.hostname || "").toLowerCase().replace(/\.$/, ""),
        path: (u.pathname || "/").toLowerCase(),
      };
    } catch (_) {
      return { host: "", path: "" };
    }
  }

  function matchesDomainList(host, list) {
    if (!host) return false;
    for (const d of list) {
      if (host === d || host.endsWith(`.${d}`)) return true;
    }
    return false;
  }

  function classifySensitiveUrl(urlStr, isSubframe = false, topUrlStr = "") {
    const { host, path } = extractHostAndPath(urlStr);
    if (!host || host === "127.0.0.1" || host === "localhost") {
      return { is_sensitive: false, category: CATEGORIES.NON_SENSITIVE, sub_type: "NONE", matched_domain: host };
    }

    // 1. Custom user-configured domains
    for (const [cDom, cCat] of Object.entries(customDomainsMap || {})) {
      const cleanDom = cDom.toLowerCase().trim();
      if (cleanDom && (host === cleanDom || host.endsWith(`.${cleanDom}`))) {
        return {
          is_sensitive: true,
          category: cCat,
          sub_type: "CUSTOM_DOMAIN",
          matched_domain: host,
        };
      }
    }

    // 2. PayPal checkout vs PayPal account dashboard
    if (host === "paypal.com" || host.endsWith(".paypal.com")) {
      if (["/checkoutnow", "/cgi-bin/webscr", "/checkout", "/webapps/hermes", "/pay"].some(p => path.includes(p))) {
        return {
          is_sensitive: true,
          category: CATEGORIES.PAYMENT_CHECKOUT,
          sub_type: "PAYPAL_CHECKOUT",
          matched_domain: host,
        };
      }
      return {
        is_sensitive: true,
        category: CATEGORIES.BANKING_FINANCIAL,
        sub_type: "PAYPAL_ACCOUNT",
        matched_domain: host,
      };
    }

    // 3. Embedded js.stripe.com checkout frame when top-level page is on checkout/billing/subscription path
    if (host === "js.stripe.com" && isSubframe) {
      const topInfo = extractHostAndPath(topUrlStr);
      if (["checkout", "subscribe", "billing", "payment", "pay", "pricing", "upgrade"].some(k => topInfo.path.includes(k))) {
        return {
          is_sensitive: true,
          category: CATEGORIES.PAYMENT_CHECKOUT,
          sub_type: "STRIPE_CHECKOUT",
          matched_domain: host,
        };
      }
    }

    // 4. Payment & Checkout domains
    if (matchesDomainList(host, PAYMENT_DOMAINS)) {
      let subType = "PAYMENT_CHECKOUT";
      if (host.includes("stripe.com")) subType = "STRIPE_CHECKOUT";
      else if (host.includes("razorpay.com")) subType = "RAZORPAY_CHECKOUT";
      return {
        is_sensitive: true,
        category: CATEGORIES.PAYMENT_CHECKOUT,
        sub_type: subType,
        matched_domain: host,
      };
    }

    // 5. Email & Personal accounts
    if (matchesDomainList(host, EMAIL_PERSONAL_DOMAINS)) {
      let subType = "EMAIL_PERSONAL";
      if (host === "mail.google.com" || host === "gmail.com" || host.endsWith(".mail.google.com")) subType = "GMAIL";
      else if (host.includes("outlook.") || host.includes("hotmail.")) subType = "OUTLOOK";
      else if (host.includes("proton")) subType = "PROTONMAIL";
      else if (host.includes("yahoo.")) subType = "YAHOO_MAIL";
      return {
        is_sensitive: true,
        category: CATEGORIES.EMAIL_PERSONAL,
        sub_type: subType,
        matched_domain: host,
      };
    }

    // 6. Crypto & Wallet pages
    if (matchesDomainList(host, CRYPTO_DOMAINS)) {
      return {
        is_sensitive: true,
        category: CATEGORIES.CRYPTO_WALLET,
        sub_type: "CRYPTO_WALLET",
        matched_domain: host,
      };
    }

    // 7. Banking & Financial portals
    if (matchesDomainList(host, BANKING_DOMAINS)) {
      return {
        is_sensitive: true,
        category: CATEGORIES.BANKING_FINANCIAL,
        sub_type: "BANKING_FINANCIAL",
        matched_domain: host,
      };
    }

    // 8. Heuristic host/path patterns
    if (
      ["netbanking", "onlinebanking", "internetbanking", "ebanking", "homebanking", "creditcard", "cardportal"].some(k => host.includes(k)) ||
      ((host.includes("bank") || host.includes("creditunion")) &&
        ["/login", "/signin", "/auth", "/account", "/dashboard", "/summary", "/transfer", "/statements", "/netbanking", "/banking"].some(p => path.includes(p))) ||
      ["/netbanking", "/online-banking", "/internet-banking"].some(p => path.includes(p))
    ) {
      return {
        is_sensitive: true,
        category: CATEGORIES.BANKING_FINANCIAL,
        sub_type: "BANKING_HEURISTIC",
        matched_domain: host,
      };
    }

    if (
      ["cryptowallet", "web3wallet", "defiwallet", "coinwallet"].some(k => host.includes(k)) ||
      (["crypto", "wallet", "defi", "dex"].some(k => host.includes(k)) &&
        ["/wallet", "/swap", "/portfolio", "/send", "/withdraw", "/assets", "/vault"].some(p => path.includes(p)))
    ) {
      return {
        is_sensitive: true,
        category: CATEGORIES.CRYPTO_WALLET,
        sub_type: "CRYPTO_HEURISTIC",
        matched_domain: host,
      };
    }

    if (host.startsWith("webmail.") || host.startsWith("mail.") || path.includes("/webmail") || path.includes("/roundcube")) {
      return {
        is_sensitive: true,
        category: CATEGORIES.EMAIL_PERSONAL,
        sub_type: "WEBMAIL_HEURISTIC",
        matched_domain: host,
      };
    }

    if (
      host.startsWith("checkout.") ||
      host.startsWith("pay.") ||
      host.startsWith("payment.") ||
      host.startsWith("payments.") ||
      host.startsWith("billing.") ||
      ["/checkout/pay", "/payment/checkout", "/upi/pay", "/card-payment", "/subscription/checkout", "/billing/checkout"].some(p => path.includes(p))
    ) {
      return {
        is_sensitive: true,
        category: CATEGORIES.PAYMENT_CHECKOUT,
        sub_type: "CHECKOUT_HEURISTIC",
        matched_domain: host,
      };
    }

    return {
      is_sensitive: false,
      category: CATEGORIES.NON_SENSITIVE,
      sub_type: "NONE",
      matched_domain: host,
    };
  }

  // Active browser capture sessions keyed by captureId
  const activeBrowserCaptures = new Map();

  function getSensitiveDisplayLabel(category, subType, matchedDomain) {
    const dom = String(matchedDomain || "").toLowerCase();
    const sub = String(subType || "").toUpperCase();
    const cat = String(category || "").toUpperCase();
    if (sub === "STRIPE_CHECKOUT" || dom.includes("stripe.com")) return "Stripe Checkout";
    if (sub === "RAZORPAY_CHECKOUT" || dom.includes("razorpay.com")) return "Razorpay Checkout";
    if (sub === "PAYPAL_CHECKOUT") return "PayPal Checkout";
    if (sub === "PAYPAL_ACCOUNT" || dom.includes("paypal.com")) return "PayPal";
    if (sub === "GMAIL" || dom === "mail.google.com" || dom === "gmail.com") return "Gmail";
    if (sub === "OUTLOOK" || dom.includes("outlook.") || dom.includes("hotmail.")) return "Outlook";
    if (sub === "PROTONMAIL" || dom.includes("proton")) return "ProtonMail";
    if (sub === "YAHOO_MAIL" || dom.includes("yahoo.")) return "Yahoo Mail";
    if (dom.includes("coinbase.com")) return "Coinbase";
    if (dom.includes("binance.")) return "Binance";
    if (dom.includes("metamask.")) return "MetaMask Wallet";
    if (dom.includes("hdfcbank.com")) return "HDFC Bank";
    if (dom.includes("icicibank.com")) return "ICICI Bank";
    if (dom.includes("onlinesbi.sbi") || dom.includes("sbi.co.in")) return "SBI Online Banking";
    if (dom.includes("chase.com")) return "Chase Bank";
    if (cat === CATEGORIES.PAYMENT_CHECKOUT) return dom ? `Payment Checkout (${dom})` : "Payment Checkout";
    if (cat === CATEGORIES.BANKING_FINANCIAL) return dom ? `Banking Portal (${dom})` : "Banking Portal";
    if (cat === CATEGORIES.CRYPTO_WALLET) return dom ? `Crypto Wallet (${dom})` : "Crypto Wallet";
    if (cat === CATEGORIES.EMAIL_PERSONAL) return dom ? `Email / Account (${dom})` : "Personal Email Account";
    return dom || "Sensitive Page";
  }

  async function loadCustomDomainsFromStorage() {
    try {
      const saved = await chrome.storage.local.get([
        "strictSensitiveCustomDomains",
        "strictSensitiveProtectionStatus",
        "strictSensitiveLocalCaptures",
      ]);
      if (saved?.strictSensitiveCustomDomains && typeof saved.strictSensitiveCustomDomains === "object") {
        customDomainsMap = saved.strictSensitiveCustomDomains;
      }
      if (saved?.strictSensitiveProtectionStatus && !lastDesktopStatus) {
        lastDesktopStatus = saved.strictSensitiveProtectionStatus;
      }
      if (Array.isArray(saved?.strictSensitiveLocalCaptures)) {
        for (const cap of saved.strictSensitiveLocalCaptures) {
          if (cap && cap.capture_id && cap.is_active) {
            activeBrowserCaptures.set(cap.capture_id, cap);
          }
        }
      }
    } catch (_) {}
  }

  async function persistLocalTrackingState() {
    try {
      await chrome.storage.local.set({
        strictSensitiveLocalTabs: Array.from(activeSensitiveTabs.values()),
        strictSensitiveLocalCaptures: Array.from(activeBrowserCaptures.values()),
      });
    } catch (_) {}
  }

  function updateExtensionBadge(statusObj) {
    if (typeof chrome === "undefined" || !chrome.action?.setBadgeText) return;
    try {
      const s = statusObj || lastDesktopStatus;
      const hasLocalTabs = activeSensitiveTabs.size > 0;
      const hasLocalCaps = Array.from(activeBrowserCaptures.values()).some(c => c.is_active);
      const isRisk = Boolean(s?.capture_risk_active || (hasLocalTabs && hasLocalCaps));
      const isUnknown = Boolean(s?.windows_capture_state_unknown);
      const isProtected = Boolean(s?.protection_active || s?.visual_monitoring_paused || hasLocalTabs);

      if (isRisk) {
        chrome.action.setBadgeText({ text: "RISK" });
        chrome.action.setBadgeBackgroundColor({ color: "#ef4444" });
      } else if (isUnknown && isProtected) {
        chrome.action.setBadgeText({ text: "WARN" });
        chrome.action.setBadgeBackgroundColor({ color: "#f59e0b" });
      } else if (isProtected) {
        chrome.action.setBadgeText({ text: "5M" });
        chrome.action.setBadgeBackgroundColor({ color: "#0ea5e9" });
      } else {
        chrome.action.setBadgeText({ text: "" });
      }
    } catch (_) {}
  }

  function buildFallbackLocalStatus(windowsConnected = false) {
    const openTabs = Array.from(activeSensitiveTabs.values());
    const activeCaps = Array.from(activeBrowserCaptures.values()).filter(c => c.is_active);
    const prevRisk = Boolean(lastDesktopStatus?.capture_risk_active);
    const hasRisk = (openTabs.length > 0 && activeCaps.length > 0) || (prevRisk && (openTabs.length > 0 || activeCaps.length > 0));
    const unknownState = !windowsConnected && (openTabs.length > 0 || hasRisk || Boolean(lastDesktopStatus?.visual_monitoring_paused));
    const paused = openTabs.length > 0 || hasRisk || unknownState || Boolean(lastDesktopStatus?.visual_monitoring_paused);
    const allCleared = openTabs.length === 0 && activeCaps.length === 0 && !unknownState;

    const sensitiveSummary = openTabs.map(t => ({
      tab_id: String(t.tabId),
      window_id: String(t.windowId || "1"),
      category: t.category,
      sub_type: t.sub_type,
      label: t.display_label || getSensitiveDisplayLabel(t.category, t.sub_type, t.matched_domain),
      matched_domain: t.matched_domain,
      status: "OPEN",
      display_text: `${t.display_label || t.matched_domain} – OPEN`,
    }));

    const captureSummary = activeCaps.map(c => ({
      name: c.display_name || "Browser screen sharing",
      capture_id: c.capture_id,
      source_layer: "BROWSER_EXTENSION",
      classification: "ACTIVE",
      status: "ACTIVE",
      is_active: true,
    }));
    if (unknownState) {
      captureSummary.push({
        name: "Windows capture monitor",
        source_layer: "WINDOWS_MONITOR",
        classification: "CAPTURE STATUS UNKNOWN",
        status: "CAPTURE STATUS UNKNOWN",
        is_active: true,
      });
    }

    return {
      ...(lastDesktopStatus || {}),
      success: true,
      protection_active: paused,
      visual_monitoring_paused: paused,
      strict_records_visual_capture_paused: paused,
      strict_records_visual_capture_state: paused ? "PAUSED" : "ACTIVE",
      protection_state: paused ? "SENSITIVE_PAGE_PROTECTION_ACTIVE" : "INACTIVE",
      capture_risk_active: hasRisk || unknownState,
      persistent_alert_active: hasRisk || openTabs.length > 0 || unknownState,
      all_sources_cleared: allCleared,
      can_dismiss: allCleared,
      panel_header: "SENSITIVE PAGE PROTECTION",
      panel_status_badge: paused ? "PROTECTION ACTIVE" : "INACTIVE",
      risk_alert_heading: (hasRisk || unknownState) ? "SENSITIVE PAGE + SCREEN RECORDING DETECTED" : null,
      risk_alert_body: (hasRisk || unknownState)
        ? "Your browser currently contains a sensitive financial or personal page while a screen-capture activity is detected. Close all active screen-recording and screen-sharing applications before continuing."
        : null,
      panel_action_status: allCleared
        ? "All sensitive pages and detected captures are closed."
        : "Close all sensitive pages and stop detected recording or screen-sharing activity.",
      active_sensitive_tab_count: openTabs.length,
      sensitive_pages_summary: sensitiveSummary,
      active_detected_capture_count: captureSummary.filter(c => c.is_active).length,
      capture_activity_summary: captureSummary,
      windows_monitor_connected: windowsConnected,
      windows_capture_state_unknown: unknownState,
      cooldown_formatted: lastDesktopStatus?.cooldown_formatted || (paused ? "05:00" : "00:00"),
      custom_domains: customDomainsMap,
    };
  }

  async function broadcastStatusToTabs(statusObj) {
    if (typeof chrome === "undefined" || !chrome.tabs?.query) return;
    try {
      const tabs = await chrome.tabs.query({});
      for (const t of tabs) {
        if (t?.id) {
          chrome.tabs.sendMessage(t.id, {
            type: "STRICTBOT_SENSITIVE_PROTECTION_STATE_UPDATED",
            status: statusObj,
          }).catch(() => {});
        }
      }
    } catch (_) {}
  }

  async function reportSensitiveEventToDesktop(payload) {
    try {
      const browser = typeof detectBrowserKey === "function" ? await detectBrowserKey() : "browser";
      const body = {
        ...payload,
        browser: browser || "browser",
        timestamp: new Date().toISOString(),
      };
      const response = await fetch(`${SPP_API_ROOT}/event`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
        signal: AbortSignal.timeout(4000),
      });
      if (response.ok) {
        const data = await response.json();
        lastDesktopStatus = data;
        await chrome.storage.local.set({ strictSensitiveProtectionStatus: data });
        updateExtensionBadge(data);
        broadcastStatusToTabs(data).catch(() => {});
        return data;
      }
    } catch (_) {}

    // If Windows EXE is unreachable while a sensitive page or capture is active,
    // mark Windows capture status as CAPTURE STATUS UNKNOWN and preserve protection!
    const fallback = buildFallbackLocalStatus(false);
    lastDesktopStatus = fallback;
    try {
      await chrome.storage.local.set({ strictSensitiveProtectionStatus: fallback });
    } catch (_) {}
    updateExtensionBadge(fallback);
    broadcastStatusToTabs(fallback).catch(() => {});
    return fallback;
  }

  async function handleTabUrlInspection(
    tabId,
    urlStr,
    detectionSource = "TOP_LEVEL_URL",
    isSubframe = false,
    topUrlStr = "",
    windowId = 1
  ) {
    if (!Number.isInteger(tabId) || !urlStr || typeof urlStr !== "string") return;
    if (urlStr.startsWith("chrome://") || urlStr.startsWith("edge://") || urlStr.startsWith("about:") || urlStr.startsWith("chrome-extension://")) {
      return;
    }

    await loadCustomDomainsFromStorage();
    const res = classifySensitiveUrl(urlStr, isSubframe, topUrlStr);
    const prev = activeSensitiveTabs.get(tabId);

    if (res.is_sensitive) {
      const now = Date.now();
      if (
        prev &&
        prev.category === res.category &&
        prev.matched_domain === res.matched_domain &&
        now - (prev.lastSentAt || 0) < 5000
      ) {
        return;
      }

      const label = getSensitiveDisplayLabel(res.category, res.sub_type, res.matched_domain);
      activeSensitiveTabs.set(tabId, {
        tabId,
        windowId: windowId || prev?.windowId || 1,
        category: res.category,
        sub_type: res.sub_type,
        display_label: label,
        matched_domain: res.matched_domain,
        detection_source: detectionSource,
        detected_at: prev?.detected_at || new Date().toISOString(),
        status: "OPEN",
        lastSentAt: now,
      });
      await persistLocalTrackingState();

      await reportSensitiveEventToDesktop({
        event_type: "SENSITIVE_PAGE_DETECTED",
        event_id: `spp_${tabId}_${res.category}_${res.matched_domain}_${now}`,
        tab_id: String(tabId),
        window_id: String(windowId || prev?.windowId || 1),
        category: res.category,
        sub_type: res.sub_type,
        matched_domain: res.matched_domain,
        detection_source: detectionSource,
      });
    } else if (!isSubframe && prev) {
      activeSensitiveTabs.delete(tabId);
      await persistLocalTrackingState();
      await reportSensitiveEventToDesktop({
        event_type: "SENSITIVE_PAGE_CLOSED",
        tab_id: String(tabId),
        matched_domain: res.matched_domain || "",
        reason: "NAVIGATED_TO_NON_SENSITIVE_PAGE",
      });
    }
  }

  async function handleTabClosed(tabId) {
    if (!Number.isInteger(tabId)) return;
    if (activeSensitiveTabs.has(tabId)) {
      activeSensitiveTabs.delete(tabId);
      await persistLocalTrackingState();
      await reportSensitiveEventToDesktop({
        event_type: "SENSITIVE_PAGE_CLOSED",
        tab_id: String(tabId),
        reason: "TAB_CLOSED",
      });
    }
  }

  async function handleBrowserCaptureChange(captureId, sourceType, isActive, tabId = null, windowId = null, displayName = null) {
    const idStr = String(captureId || `${sourceType || "SCREEN_SHARING"}_${tabId || "global"}`);
    const src = String(sourceType || "SCREEN_SHARING").toUpperCase();
    const label = displayName || (src === "TAB_CAPTURE" ? "Browser tab capture" : src === "DESKTOP_CAPTURE" ? "Browser desktop capture" : "Browser screen sharing");

    if (isActive) {
      activeBrowserCaptures.set(idStr, {
        capture_id: idStr,
        source_type: src,
        display_name: label,
        is_active: true,
        tab_id: tabId != null ? String(tabId) : null,
        window_id: windowId != null ? String(windowId) : null,
        detected_at: new Date().toISOString(),
      });
    } else {
      activeBrowserCaptures.delete(idStr);
    }
    await persistLocalTrackingState();

    return await reportSensitiveEventToDesktop({
      event_type: isActive ? "BROWSER_CAPTURE_STARTED" : "BROWSER_CAPTURE_STOPPED",
      capture_id: idStr,
      source_type: src,
      is_active: Boolean(isActive),
      tab_id: tabId != null ? String(tabId) : null,
      window_id: windowId != null ? String(windowId) : null,
      display_name: label,
    });
  }

  async function reconcileStateOnStartup() {
    if (typeof chrome === "undefined" || !chrome.tabs?.query) return;
    try {
      await loadCustomDomainsFromStorage();
      const allTabs = await chrome.tabs.query({});
      activeSensitiveTabs.clear();
      const openSensitivePayload = [];

      for (const tab of allTabs) {
        const u = tab?.url || tab?.pendingUrl;
        if (!tab?.id || !u) continue;
        if (u.startsWith("chrome://") || u.startsWith("edge://") || u.startsWith("about:") || u.startsWith("chrome-extension://")) {
          continue;
        }
        const res = classifySensitiveUrl(u, false, u);
        if (res.is_sensitive) {
          const label = getSensitiveDisplayLabel(res.category, res.sub_type, res.matched_domain);
          const entry = {
            tabId: tab.id,
            windowId: tab.windowId || 1,
            category: res.category,
            sub_type: res.sub_type,
            display_label: label,
            matched_domain: res.matched_domain,
            detection_source: "RECONCILED_TAB",
            detected_at: new Date().toISOString(),
            status: "OPEN",
            lastSentAt: Date.now(),
          };
          activeSensitiveTabs.set(tab.id, entry);
          openSensitivePayload.push({
            tab_id: String(tab.id),
            window_id: String(tab.windowId || 1),
            category: res.category,
            sub_type: res.sub_type,
            matched_domain: res.matched_domain,
            detection_source: "RECONCILED_TAB",
          });
        }
      }

      // Check extension-observable tabCapture status if API is available
      if (chrome.tabCapture?.getCapturedTabs) {
        await new Promise(resolve => {
          try {
            chrome.tabCapture.getCapturedTabs(capturedList => {
              if (!chrome.runtime?.lastError && Array.isArray(capturedList)) {
                for (const info of capturedList) {
                  if (info && (info.status === "active" || info.status === "pending")) {
                    const cid = `tabCapture_${info.tabId}`;
                    activeBrowserCaptures.set(cid, {
                      capture_id: cid,
                      source_type: "TAB_CAPTURE",
                      display_name: "Browser tab capture",
                      is_active: true,
                      tab_id: String(info.tabId),
                    });
                  }
                }
              }
              resolve();
            });
          } catch (_) {
            resolve();
          }
        });
      }

      await persistLocalTrackingState();
      await reportSensitiveEventToDesktop({
        event_type: "RECONCILE_EXTENSION_STATE",
        active_tabs: openSensitivePayload,
        browser_captures: Array.from(activeBrowserCaptures.values()),
      });
    } catch (_) {}
  }

  // Register Chrome extension listeners for real-time detection
  if (typeof chrome !== "undefined") {
    if (chrome.runtime?.onStartup) {
      chrome.runtime.onStartup.addListener(() => {
        reconcileStateOnStartup().catch(() => {});
      });
    }
    if (chrome.runtime?.onInstalled) {
      chrome.runtime.onInstalled.addListener(() => {
        reconcileStateOnStartup().catch(() => {});
      });
    }
    // Also reconcile immediately when the service worker wakes up
    reconcileStateOnStartup().catch(() => {});

    if (chrome.tabCapture?.onStatusChanged) {
      chrome.tabCapture.onStatusChanged.addListener(info => {
        if (!info || info.tabId == null) return;
        const isActive = info.status === "active" || info.status === "pending";
        handleBrowserCaptureChange(
          `tabCapture_${info.tabId}`,
          "TAB_CAPTURE",
          isActive,
          info.tabId,
          null,
          "Browser tab capture"
        ).catch(() => {});
      });
    }

    if (chrome.tabs?.onCreated) {
      chrome.tabs.onCreated.addListener(tab => {
        const u = tab?.pendingUrl || tab?.url;
        if (tab?.id && u) {
          handleTabUrlInspection(tab.id, u, "TAB_CREATED", false, "", tab.windowId || 1).catch(() => {});
        }
      });
    }

    if (chrome.tabs?.onUpdated) {
      chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
        const u = changeInfo?.url || tab?.url || tab?.pendingUrl;
        if (u && (changeInfo.url || changeInfo.status === "loading" || changeInfo.status === "complete")) {
          handleTabUrlInspection(tabId, u, "TAB_UPDATED", false, "", tab?.windowId || 1).catch(() => {});
        }
      });
    }

    if (chrome.tabs?.onActivated) {
      chrome.tabs.onActivated.addListener(activeInfo => {
        if (!activeInfo?.tabId) return;
        chrome.tabs.get(activeInfo.tabId, tab => {
          if (chrome.runtime.lastError || !tab?.url) return;
          handleTabUrlInspection(activeInfo.tabId, tab.url, "TAB_ACTIVATED", false, "", tab.windowId || activeInfo.windowId || 1).catch(() => {});
        });
      });
    }

    if (chrome.tabs?.onRemoved) {
      chrome.tabs.onRemoved.addListener(tabId => {
        handleTabClosed(tabId).catch(() => {});
      });
    }

    if (chrome.webNavigation?.onCommitted) {
      chrome.webNavigation.onCommitted.addListener(details => {
        if (!details || !Number.isInteger(details.tabId) || !details.url) return;
        const isSubframe = details.frameId !== 0;
        const isRedirect = Array.isArray(details.transitionQualifiers) &&
          (details.transitionQualifiers.includes("server_redirect") || details.transitionQualifiers.includes("client_redirect"));
        const source = isSubframe
          ? "EMBEDDED_FRAME_ORIGIN"
          : isRedirect
            ? "REDIRECT_DESTINATION"
            : "WEB_NAVIGATION_COMMITTED";
        if (isSubframe) {
          chrome.tabs.get(details.tabId, tab => {
            const topUrl = (!chrome.runtime.lastError && tab?.url) ? tab.url : "";
            handleTabUrlInspection(details.tabId, details.url, source, true, topUrl, tab?.windowId || 1).catch(() => {});
          });
        } else {
          handleTabUrlInspection(details.tabId, details.url, source, false, "", 1).catch(() => {});
        }
      });
    }

    if (chrome.runtime?.onMessage) {
      chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
        if (message?.type === "STRICTBOT_SENSITIVE_FRAME_ORIGIN") {
          const tabId = sender?.tab?.id;
          const winId = sender?.tab?.windowId || 1;
          const frameOrigin = sanitizeHost(message.origin || sender?.origin || sender?.url || "");
          const topUrl = sender?.tab?.url || "";
          if (Number.isInteger(tabId) && frameOrigin) {
            handleTabUrlInspection(tabId, `https://${frameOrigin}/`, "EMBEDDED_FRAME_ORIGIN", sender?.frameId !== 0, topUrl, winId)
              .then(() => sendResponse({ success: true }))
              .catch(() => sendResponse({ success: false }));
            return true;
          }
          sendResponse({ success: false });
          return false;
        }

        if (message?.type === "STRICTBOT_BROWSER_CAPTURE_EVENT") {
          const tabId = message.tab_id ?? sender?.tab?.id ?? null;
          const winId = message.window_id ?? sender?.tab?.windowId ?? null;
          const capId = message.capture_id || `${message.source_type || "SCREEN_SHARING"}_${tabId || "global"}`;
          handleBrowserCaptureChange(
            capId,
            message.source_type || "SCREEN_SHARING",
            Boolean(message.is_active),
            tabId,
            winId,
            message.display_name || null
          )
            .then(d => sendResponse(d || { success: true }))
            .catch(err => sendResponse({ success: false, error: err.message }));
          return true;
        }

        if (message?.type === "STRICTBOT_DISMISS_SENSITIVE_ALERT") {
          reportSensitiveEventToDesktop({
            event_type: "DISMISS_ALERT",
            admin_override: Boolean(message.admin_override),
          })
            .then(d => sendResponse(d || { success: false }))
            .catch(err => sendResponse({ success: false, error: err.message }));
          return true;
        }

        if (message?.type === "STRICTBOT_GET_SENSITIVE_PROTECTION_STATUS") {
          (async () => {
            try {
              const r = await fetch(`${SPP_API_ROOT}/status`, {
                cache: "no-store",
                signal: AbortSignal.timeout(3000),
              });
              if (r.ok) {
                const d = await r.json();
                lastDesktopStatus = d;
                updateExtensionBadge(d);
                if (d.custom_domains) {
                  customDomainsMap = d.custom_domains;
                  await chrome.storage.local.set({
                    strictSensitiveCustomDomains: d.custom_domains,
                    strictSensitiveProtectionStatus: d,
                  });
                }
                return d;
              }
            } catch (_) {}
            const fallback = buildFallbackLocalStatus(false);
            lastDesktopStatus = fallback;
            updateExtensionBadge(fallback);
            return fallback;
          })().then(sendResponse).catch(err => sendResponse({ success: false, error: err.message }));
          return true;
        }
        return false;
      });
    }
  }

  // Sync custom domains and badge from desktop heartbeat when available
  globalThis.strictSensitiveProtectionSyncFromHeartbeat = async function (heartbeatData) {
    if (heartbeatData?.sensitive_page_protection) {
      const spp = heartbeatData.sensitive_page_protection;
      lastDesktopStatus = spp;
      updateExtensionBadge(spp);
      if (spp.custom_domains && typeof spp.custom_domains === "object") {
        customDomainsMap = spp.custom_domains;
      }
      try {
        await chrome.storage.local.set({
          strictSensitiveCustomDomains: customDomainsMap,
          strictSensitiveProtectionStatus: spp,
        });
      } catch (_) {}
    }
  };

  globalThis.strictClassifySensitiveUrl = classifySensitiveUrl;
  globalThis.strictReconcileSensitiveProtectionState = reconcileStateOnStartup;
})();
