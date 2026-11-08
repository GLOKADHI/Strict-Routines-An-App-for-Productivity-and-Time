"""
STRICTRECORDS – SENSITIVE PAGE DETECTION, SCREEN CAPTURE PRIVACY SHIELD
& 5-MINUTE VISUAL MONITORING COOLDOWN

Authoritative Windows controller for Sensitive Page Protection:
1. Classifies sensitive pages across 4 categories:
   - BANKING_FINANCIAL (Online banking portals, bank dashboards, net banking login, credit card/investment portals)
   - CRYPTO_WALLET (Cryptocurrency exchanges, crypto wallets, Web3 asset dashboards)
   - EMAIL_PERSONAL (Gmail, Outlook, ProtonMail, Yahoo Mail, personal account management pages)
   - PAYMENT_CHECKOUT (Stripe Checkout, Razorpay, PayPal checkout, UPI gateways, subscription checkouts)
2. Supports user-configurable sensitive domains and external recorder process names.
3. Enforces strict privacy sanitization:
   - Never stores or logs full sensitive URLs, query parameters, payment tokens, session IDs,
     passwords, card numbers, OTPs, seed phrases, or email contents.
4. Immediately pauses all StrictRecords visual screen capture, OCR, and screen analysis for a
   5-minute (300-second) privacy cooldown using the authoritative Windows clock.
5. Keeps non-visual monitoring active (session timer, Windows webcam, iOS front camera, Android usage).
6. Handles Case A (no sensitive pages open after 5m -> VISUAL_MONITORING_RESUMED) and
   Case B (sensitive page still open after 5m -> visual protection remains active until closed/navigated away).
7. Performs best-effort Windows process check for external screen-recording apps (OBS, Xbox Game Bar,
   ShareX, Bandicam, Camtasia, Loom, Streamlabs, etc.) and warns the user without terminating processes.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False

logger = logging.getLogger("SensitivePageProtection")


class SensitiveCategory:
    BANKING_FINANCIAL = "BANKING_FINANCIAL"
    CRYPTO_WALLET = "CRYPTO_WALLET"
    EMAIL_PERSONAL = "EMAIL_PERSONAL"
    PAYMENT_CHECKOUT = "PAYMENT_CHECKOUT"
    NON_SENSITIVE = "NON_SENSITIVE"

    ALL_SENSITIVE = {
        BANKING_FINANCIAL,
        CRYPTO_WALLET,
        EMAIL_PERSONAL,
        PAYMENT_CHECKOUT,
    }


class ProtectionState:
    INACTIVE = "INACTIVE"
    SENSITIVE_PAGE_PROTECTION_ACTIVE = "SENSITIVE_PAGE_PROTECTION_ACTIVE"
    SENSITIVE_PAGE_CAPTURE_RISK = "SENSITIVE_PAGE_CAPTURE_RISK"
    SENSITIVE_PAGE_STILL_OPEN_PROTECTED = "SENSITIVE_PAGE_STILL_OPEN_PROTECTED"
    VISUAL_MONITORING_RESUMED = "VISUAL_MONITORING_RESUMED"


class CaptureClassification:
    INSTALLED = "INSTALLED"
    PROCESS_RUNNING_CAPTURE_UNKNOWN = "PROCESS RUNNING – CAPTURE STATUS UNKNOWN"
    PROCESS_RUNNING = "PROCESS RUNNING"
    CAPTURE_SESSION_CONFIRMED_ACTIVE = "CAPTURE SESSION CONFIRMED ACTIVE"
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    CAPTURE_STATUS_UNKNOWN = "CAPTURE STATUS UNKNOWN"


# Processes whose presence specifically indicates an active capture helper session
CONFIRMED_ACTIVE_RECORDER_PROCESSES: Set[str] = {
    "broadcastdvr.exe",
    "camrecorder.exe",
    "snagitcapture.exe",
    "screenclippinghost.exe",
}



# Built-in domain and pattern rules per category
BUILTIN_PAYMENT_DOMAINS: Set[str] = {
    "checkout.stripe.com",
    "buy.stripe.com",
    "billing.stripe.com",
    "js.stripe.com",
    "hooks.stripe.com",
    "invoice.stripe.com",
    "pay.stripe.com",
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
    "test.payu.in",
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
    "assets.braintreegateway.com",
    "squareup.com",
    "square.link",
    "checkout.square.site",
    "pay.google.com",
    "payments.google.com",
    "pay.amazon.com",
    "pay.amazon.in",
    "2checkout.com",
    "secure.2checkout.com",
    "dlocal.com",
    "worldpay.com",
    "klarna.com",
    "pay.klarna.com",
    "afterpay.com",
    "portal.afterpay.com",
    "affirm.com",
    "ccavenue.com",
    "secure.ccavenue.com",
    "instamojo.com",
    "gumroad.com",
    "app.gumroad.com",
}

BUILTIN_BANKING_DOMAINS: Set[str] = {
    # Indian Banks & Financial Portals
    "hdfcbank.com",
    "netbanking.hdfcbank.com",
    "icicibank.com",
    "infinity.icicibank.com",
    "onlinesbi.sbi",
    "retail.onlinesbi.sbi",
    "yonobusiness.sbi",
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
    "indusnet.co.in",
    "idfcfirstbank.com",
    "my.idfcfirstbank.com",
    "federalbank.co.in",
    "fednetbank.com",
    "rblbank.com",
    "unionbankofindia.co.in",
    "indianbank.in",
    "iobnet.co.in",
    # Global Banks & Credit Card Portals
    "chase.com",
    "secure.chase.com",
    "bankofamerica.com",
    "secure.bankofamerica.com",
    "wellsfargo.com",
    "connect.secure.wellsfargo.com",
    "citi.com",
    "online.citi.com",
    "citibank.co.in",
    "hsbc.com",
    "hsbc.co.uk",
    "hsbc.co.in",
    "barclays.co.uk",
    "bank.barclays.co.uk",
    "capitalone.com",
    "verified.capitalone.com",
    "americanexpress.com",
    "global.americanexpress.com",
    "discover.com",
    "portal.discover.com",
    "usbank.com",
    "pnc.com",
    "td.com",
    "easyweb.td.com",
    "rbcroyalbank.com",
    "scotiabank.com",
    "bmo.com",
    "lloydsbank.com",
    "natwest.com",
    "santander.co.uk",
    "standardchartered.com",
    "dbs.com",
    "internet-banking.dbs.com.sg",
    "anz.com",
    "commbank.com.au",
    "westpac.com.au",
    "nab.com.au",
    # Fintech, Brokerage & Payment Account Dashboards
    "paypal.com",
    "www.paypal.com",
    "wise.com",
    "revolut.com",
    "app.revolut.com",
    "mercury.com",
    "app.mercury.com",
    "brex.com",
    "dashboard.stripe.com",
    "dashboard.razorpay.com",
    "zerodha.com",
    "kite.zerodha.com",
    "console.zerodha.com",
    "groww.in",
    "upstox.com",
    "pro.upstox.com",
    "angelone.in",
    "robinhood.com",
    "fidelity.com",
    "digital.fidelity.com",
    "schwab.com",
    "client.schwab.com",
    "vanguard.com",
    "interactivebrokers.com",
    "venmo.com",
    "cash.app",
}

BUILTIN_CRYPTO_DOMAINS: Set[str] = {
    "coinbase.com",
    "www.coinbase.com",
    "advanced.coinbase.com",
    "wallet.coinbase.com",
    "binance.com",
    "www.binance.com",
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
    "exchange.gemini.com",
    "bitstamp.net",
    "gate.io",
    "htx.com",
    "mexc.com",
    "wazirx.com",
    "coindcx.com",
    "coinswitch.co",
    "zebpay.com",
    "blockchain.com",
    "login.blockchain.com",
    "rabby.io",
    "rainbow.me",
    "1inch.io",
    "app.1inch.io",
    "aave.com",
    "app.aave.com",
    "etherscan.io",
    "solscan.io",
}

BUILTIN_EMAIL_PERSONAL_DOMAINS: Set[str] = {
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
    "www.icloud.com",
    "mail.aol.com",
    "fastmail.com",
    "app.fastmail.com",
    "tuta.com",
    "mail.tutanota.com",
    "hey.com",
    "app.hey.com",
    "gmx.com",
    "mail.com",
    "yandex.com",
    "mail.yandex.com",
    # Personal Account Security & Credential Management Pages
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
}

# Known external screen-recording process names (lowercased) -> Display Name
BUILTIN_EXTERNAL_RECORDERS: Dict[str, str] = {
    "obs64.exe": "OBS Studio",
    "obs32.exe": "OBS Studio",
    "obs.exe": "OBS Studio",
    "streamlabs obs.exe": "Streamlabs Desktop",
    "streamlabs.exe": "Streamlabs Desktop",
    "gamebar.exe": "Xbox Game Bar",
    "gamebarftserver.exe": "Xbox Game Bar",
    "broadcastdvr.exe": "Xbox Game Bar (BroadcastDVR)",
    "sharex.exe": "ShareX",
    "bdcam.exe": "Bandicam",
    "bandicam.exe": "Bandicam",
    "camrecorder.exe": "Camtasia Recorder",
    "camtasiastudio.exe": "Camtasia Studio",
    "loom.exe": "Loom Screen Recorder",
    "snagit32.exe": "Snagit",
    "snagitcapture.exe": "Snagit Capture",
    "snagit.exe": "Snagit",
    "snippingtool.exe": "Windows Snipping Tool",
    "screenclippinghost.exe": "Windows Screen Clipping",
    "nvidia share.exe": "NVIDIA ShadowPlay",
    "xsplit.core.exe": "XSplit Broadcaster",
    "action.exe": "Mirillis Action!",
    "fraps.exe": "Fraps",
    "flashbackrecorder.exe": "FlashBack Screen Recorder",
    "icecreamscreenrecorder.exe": "Icecream Screen Recorder",
    "screenrec.exe": "ScreenRec",
}


def sanitize_domain_or_origin(url_or_domain: str) -> str:
    """
    Extracts ONLY the lowercase hostname (or sanitized domain) from any URL or origin string.
    Strictly discards query parameters, hash fragments, credentials, ports, and paths so
    sensitive URLs, payment tokens, session IDs, or account identifiers are NEVER stored.
    """
    if not url_or_domain or not isinstance(url_or_domain, str):
        return ""
    raw = url_or_domain.strip()
    if not raw:
        return ""
    try:
        if "://" not in raw:
            parsed = urlparse("https://" + raw)
        else:
            parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().strip(".")
        # Validate hostname characters only
        host = re.sub(r"[^a-z0-9.\-]", "", host)
        return host
    except Exception:
        return ""


def _extract_safe_host_and_path(url_or_domain: str) -> Tuple[str, str]:
    """
    Extracts `(host, lowercase_path)` in memory only for heuristic classification.
    Never returns or logs query strings or fragments.
    """
    if not url_or_domain or not isinstance(url_or_domain, str):
        return "", ""
    raw = url_or_domain.strip()
    if not raw:
        return "", ""
    try:
        if "://" not in raw:
            parsed = urlparse("https://" + raw)
        else:
            parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().strip(".")
        path = (parsed.path or "/").lower()
        return host, path
    except Exception:
        return "", ""


def _matches_domain_set(host: str, domain_set: Set[str]) -> Optional[str]:
    if not host:
        return None
    if host in domain_set:
        return host
    for d in domain_set:
        if host.endswith("." + d):
            return host
    return None


def classify_sensitive_page(
    url_or_origin: str,
    frame_origins: Optional[List[str]] = None,
    redirect_url: Optional[str] = None,
    custom_domains: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Classifies a page navigation, redirect destination, or accessible frame origin into one of:
      - BANKING_FINANCIAL
      - CRYPTO_WALLET
      - EMAIL_PERSONAL
      - PAYMENT_CHECKOUT
      - NON_SENSITIVE

    Returns only sanitized metadata:
      {
        "is_sensitive": bool,
        "category": str,
        "sub_type": str,          # e.g. "STRIPE_CHECKOUT", "GMAIL", "OUTLOOK", "RAZORPAY", "BANKING", "CRYPTO"
        "matched_domain": str,    # sanitized hostname only, e.g. "checkout.stripe.com"
        "detection_source": str,  # "TOP_LEVEL_URL", "REDIRECT_DESTINATION", "EMBEDDED_FRAME_ORIGIN", "CUSTOM_DOMAIN"
      }
    """
    candidates: List[Tuple[str, str]] = []
    if redirect_url:
        candidates.append((redirect_url, "REDIRECT_DESTINATION"))
    if url_or_origin:
        candidates.append((url_or_origin, "TOP_LEVEL_URL"))
    if frame_origins:
        for fo in frame_origins:
            if fo and isinstance(fo, str):
                candidates.append((fo, "EMBEDDED_FRAME_ORIGIN"))

    custom_map: Dict[str, str] = {}
    if custom_domains:
        for dom, cat in custom_domains.items():
            clean_dom = sanitize_domain_or_origin(dom)
            if clean_dom and cat in SensitiveCategory.ALL_SENSITIVE:
                custom_map[clean_dom] = cat

    for candidate_str, source in candidates:
        host, path = _extract_safe_host_and_path(candidate_str)
        if not host:
            continue

        # Ignore localhost / local desktop dashboard
        if host in ("127.0.0.1", "localhost", "0.0.0.0"):
            continue

        # 1. Check user-configured custom domains first
        if custom_map:
            for c_dom, c_cat in custom_map.items():
                if host == c_dom or host.endswith("." + c_dom):
                    return {
                        "is_sensitive": True,
                        "category": c_cat,
                        "sub_type": "CUSTOM_DOMAIN",
                        "matched_domain": host,
                        "detection_source": "CUSTOM_DOMAIN",
                    }

        # 2. Special PayPal distinction: PayPal checkout paths -> PAYMENT_CHECKOUT; PayPal dashboard -> BANKING_FINANCIAL
        if host == "paypal.com" or host.endswith(".paypal.com"):
            if any(p in path for p in ("/checkoutnow", "/cgi-bin/webscr", "/checkout", "/webapps/hermes", "/pay")):
                return {
                    "is_sensitive": True,
                    "category": SensitiveCategory.PAYMENT_CHECKOUT,
                    "sub_type": "PAYPAL_CHECKOUT",
                    "matched_domain": host,
                    "detection_source": source,
                }
            return {
                "is_sensitive": True,
                "category": SensitiveCategory.BANKING_FINANCIAL,
                "sub_type": "PAYPAL_ACCOUNT",
                "matched_domain": host,
                "detection_source": source,
            }

        # 3. Check Payment & Checkout domains
        # Note: js.stripe.com alone is sometimes loaded passively; require either checkout/buy/billing/pay.stripe.com
        # OR js.stripe.com when top-level path or frame context is a checkout/payment flow.
        if host == "js.stripe.com" and source == "EMBEDDED_FRAME_ORIGIN":
            top_host, top_path = _extract_safe_host_and_path(url_or_origin)
            if any(k in top_path for k in ("checkout", "subscribe", "billing", "payment", "pay", "pricing", "upgrade")):
                return {
                    "is_sensitive": True,
                    "category": SensitiveCategory.PAYMENT_CHECKOUT,
                    "sub_type": "STRIPE_CHECKOUT",
                    "matched_domain": host,
                    "detection_source": source,
                }
        elif _matches_domain_set(host, BUILTIN_PAYMENT_DOMAINS):
            sub_type = "PAYMENT_CHECKOUT"
            if "stripe.com" in host:
                sub_type = "STRIPE_CHECKOUT"
            elif "razorpay.com" in host:
                sub_type = "RAZORPAY_CHECKOUT"
            return {
                "is_sensitive": True,
                "category": SensitiveCategory.PAYMENT_CHECKOUT,
                "sub_type": sub_type,
                "matched_domain": host,
                "detection_source": source,
            }

        # 4. Check Email & Personal Accounts
        if _matches_domain_set(host, BUILTIN_EMAIL_PERSONAL_DOMAINS):
            # For icloud.com, only treat mail or account management or top-level icloud login as sensitive
            sub_type = "EMAIL_PERSONAL"
            if host in ("mail.google.com", "gmail.com", "inbox.google.com") or host.endswith(".mail.google.com"):
                sub_type = "GMAIL"
            elif "outlook." in host or "hotmail." in host:
                sub_type = "OUTLOOK"
            elif "proton" in host:
                sub_type = "PROTONMAIL"
            elif "yahoo." in host:
                sub_type = "YAHOO_MAIL"
            return {
                "is_sensitive": True,
                "category": SensitiveCategory.EMAIL_PERSONAL,
                "sub_type": sub_type,
                "matched_domain": host,
                "detection_source": source,
            }

        # 5. Check Crypto & Wallet pages
        if _matches_domain_set(host, BUILTIN_CRYPTO_DOMAINS):
            return {
                "is_sensitive": True,
                "category": SensitiveCategory.CRYPTO_WALLET,
                "sub_type": "CRYPTO_WALLET",
                "matched_domain": host,
                "detection_source": source,
            }

        # 6. Check Banking & Financial Account domains
        if _matches_domain_set(host, BUILTIN_BANKING_DOMAINS):
            return {
                "is_sensitive": True,
                "category": SensitiveCategory.BANKING_FINANCIAL,
                "sub_type": "BANKING_FINANCIAL",
                "matched_domain": host,
                "detection_source": source,
            }

        # 7. Heuristic Host & Path Classification (so detection does not rely solely on a fixed domain list)
        # 7a. Banking heuristics
        if any(
            kw in host
            for kw in (
                "netbanking",
                "onlinebanking",
                "internetbanking",
                "ebanking",
                "iban",
                "homebanking",
                "creditcard",
                "cardportal",
            )
        ) or (
            ("bank" in host or "creditunion" in host or "fcu" in host)
            and any(
                p in path
                for p in (
                    "/login",
                    "/signin",
                    "/auth",
                    "/account",
                    "/dashboard",
                    "/summary",
                    "/transfer",
                    "/statements",
                    "/netbanking",
                    "/banking",
                )
            )
        ) or any(p in path for p in ("/netbanking", "/online-banking", "/internet-banking")):
            return {
                "is_sensitive": True,
                "category": SensitiveCategory.BANKING_FINANCIAL,
                "sub_type": "BANKING_HEURISTIC",
                "matched_domain": host,
                "detection_source": source,
            }

        # 7b. Crypto wallet heuristics
        if any(
            kw in host
            for kw in (
                "cryptowallet",
                "web3wallet",
                "defiwallet",
                "coinwallet",
            )
        ) or (
            any(kw in host for kw in ("crypto", "wallet", "defi", "dex", "swap"))
            and any(p in path for p in ("/wallet", "/swap", "/portfolio", "/send", "/withdraw", "/assets", "/vault"))
        ):
            return {
                "is_sensitive": True,
                "category": SensitiveCategory.CRYPTO_WALLET,
                "sub_type": "CRYPTO_HEURISTIC",
                "matched_domain": host,
                "detection_source": source,
            }

        # 7c. Email/Webmail heuristics
        if (
            host.startswith("webmail.")
            or host.startswith("mail.")
            or "/webmail" in path
            or "/roundcube" in path
            or "/zimbra" in path
        ):
            return {
                "is_sensitive": True,
                "category": SensitiveCategory.EMAIL_PERSONAL,
                "sub_type": "WEBMAIL_HEURISTIC",
                "matched_domain": host,
                "detection_source": source,
            }

        # 7d. Payment & Checkout heuristics (e.g. UPI gateways, card checkout pages, subscription checkouts)
        if (
            host.startswith("checkout.")
            or host.startswith("pay.")
            or host.startswith("payment.")
            or host.startswith("payments.")
            or host.startswith("billing.")
            or any(
                p in path
                for p in (
                    "/checkout/pay",
                    "/payment/checkout",
                    "/upi/pay",
                    "/card-payment",
                    "/subscription/checkout",
                    "/billing/checkout",
                )
            )
        ):
            return {
                "is_sensitive": True,
                "category": SensitiveCategory.PAYMENT_CHECKOUT,
                "sub_type": "CHECKOUT_HEURISTIC",
                "matched_domain": host,
                "detection_source": source,
            }

    fallback_host = sanitize_domain_or_origin(url_or_origin)
    return {
        "is_sensitive": False,
        "category": SensitiveCategory.NON_SENSITIVE,
        "sub_type": "NONE",
        "matched_domain": fallback_host,
        "detection_source": "NONE",
    }


def format_mm_ss(seconds: float) -> str:
    rem = max(0, int(round(seconds)))
    mins = rem // 60
    secs = rem % 60
    return f"{mins:02d}:{secs:02d}"


def get_sensitive_page_display_label(
    category: str,
    sub_type: Optional[str] = None,
    matched_domain: Optional[str] = None,
) -> str:
    """
    Returns a clean, human-readable label for a detected sensitive page without exposing
    any path, query parameter, token, or user identifier (e.g. "Stripe Checkout", "Gmail").
    """
    dom = sanitize_domain_or_origin(matched_domain or "")
    sub = str(sub_type or "").upper().strip()
    cat = str(category or "").upper().strip()

    if sub == "STRIPE_CHECKOUT" or "stripe.com" in dom:
        return "Stripe Checkout"
    if sub == "RAZORPAY_CHECKOUT" or "razorpay.com" in dom:
        return "Razorpay Checkout"
    if sub == "PAYPAL_CHECKOUT":
        return "PayPal Checkout"
    if sub == "PAYPAL_ACCOUNT" or "paypal.com" in dom:
        return "PayPal"
    if sub == "GMAIL" or dom in ("mail.google.com", "gmail.com", "inbox.google.com") or dom.endswith(".mail.google.com"):
        return "Gmail"
    if sub == "OUTLOOK" or "outlook." in dom or "hotmail." in dom:
        return "Outlook"
    if sub == "PROTONMAIL" or "proton" in dom:
        return "ProtonMail"
    if sub == "YAHOO_MAIL" or "yahoo." in dom:
        return "Yahoo Mail"
    if "coinbase.com" in dom:
        return "Coinbase"
    if "binance." in dom:
        return "Binance"
    if "metamask." in dom:
        return "MetaMask Wallet"
    if "kraken.com" in dom:
        return "Kraken"
    if "phantom.app" in dom:
        return "Phantom Wallet"
    if "hdfcbank.com" in dom:
        return "HDFC Bank"
    if "icicibank.com" in dom:
        return "ICICI Bank"
    if "onlinesbi.sbi" in dom or "sbi.co.in" in dom:
        return "SBI Online Banking"
    if "axisbank" in dom:
        return "Axis Bank"
    if "kotak.com" in dom:
        return "Kotak Bank"
    if "chase.com" in dom:
        return "Chase Bank"
    if "bankofamerica.com" in dom:
        return "Bank of America"
    if "wellsfargo.com" in dom:
        return "Wells Fargo"
    if "citi.com" in dom or "citibank" in dom:
        return "Citibank"

    if dom and dom != "sensitive.local":
        if cat == SensitiveCategory.PAYMENT_CHECKOUT:
            return f"Payment Checkout ({dom})"
        if cat == SensitiveCategory.BANKING_FINANCIAL:
            return f"Banking Portal ({dom})"
        if cat == SensitiveCategory.CRYPTO_WALLET:
            return f"Crypto Wallet ({dom})"
        if cat == SensitiveCategory.EMAIL_PERSONAL:
            return f"Email / Account ({dom})"
        return dom

    if cat == SensitiveCategory.PAYMENT_CHECKOUT:
        return "Payment Checkout"
    if cat == SensitiveCategory.BANKING_FINANCIAL:
        return "Banking Portal"
    if cat == SensitiveCategory.CRYPTO_WALLET:
        return "Crypto Wallet"
    if cat == SensitiveCategory.EMAIL_PERSONAL:
        return "Personal Email Account"
    return "Sensitive Page"


class SensitivePageProtectionManager:
    """
    Authoritative Windows controller for Sensitive Page Protection, Persistent
    Sensitive-Page Screen-Recording / Screen-Sharing Risk Alerts, and the 5-minute
    visual screen monitoring privacy cooldown.
    """

    COOLDOWN_SECONDS: float = 300.0  # 5 minutes (300 seconds)
    DEBOUNCE_SECONDS: float = 5.0    # Ignore rapid duplicate events on the same tab+domain

    RISK_ALERT_HEADING: str = "SENSITIVE PAGE + SCREEN RECORDING DETECTED"
    RISK_ALERT_BODY: str = (
        "Your browser currently contains a sensitive financial or personal page while a "
        "screen-capture activity is detected. Close all active screen-recording and "
        "screen-sharing applications before continuing."
    )
    PANEL_HEADER: str = "SENSITIVE PAGE PROTECTION"
    PANEL_ACTION_REQUIRED_STATUS: str = (
        "Close all sensitive pages and stop detected recording or screen-sharing activity."
    )
    PANEL_RESOLVED_STATUS: str = "All sensitive pages and detected captures are closed."

    _instance: Optional[SensitivePageProtectionManager] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> SensitivePageProtectionManager:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.reset()
                cls._instance = None

    def __init__(
        self,
        clock_fn: Optional[Callable[[], float]] = None,
        process_scanner_fn: Optional[Callable[[], List[Any]]] = None,
        notification_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        cooldown_seconds: float = 300.0,
    ):
        self._lock = threading.RLock()
        self._clock: Callable[[], float] = clock_fn or time.monotonic
        self._process_scanner_fn = process_scanner_fn
        self._notification_callback = notification_callback
        self._cooldown_duration: float = float(cooldown_seconds)

        # Authoritative cooldown & protection state
        self._protection_state: str = ProtectionState.INACTIVE
        self._visual_monitoring_paused: bool = False
        self._cooldown_started_mono: Optional[float] = None
        self._cooldown_expires_mono: Optional[float] = None
        self._last_activated_wall: Optional[str] = None
        self._last_resumed_wall: Optional[str] = None

        # Active sensitive tabs keyed by "{browser}:{tab_id}"
        self._active_sensitive_tabs: Dict[str, Dict[str, Any]] = {}
        # Tabs that were part of the current/recent incident and have since closed
        self._closed_incident_tabs: Dict[str, Dict[str, Any]] = {}

        # Browser-level capture sessions (tabCapture, desktopCapture, getDisplayMedia)
        # keyed by "{browser}:{capture_id}"
        self._browser_capture_sessions: Dict[str, Dict[str, Any]] = {}

        # Current/most recent active sensitive detection metadata
        self._current_category: Optional[str] = None
        self._current_sub_type: Optional[str] = None
        self._current_matched_domain: Optional[str] = None
        self._current_alert_title: Optional[str] = None
        self._current_alert_message: Optional[str] = None

        # Deduplication & idempotency tracking
        self._processed_event_ids: Set[str] = set()
        self._notified_tab_signatures: Dict[str, float] = {}  # tab_key|category|domain -> last_mono
        self._notified_risk_signatures: Dict[str, float] = {}

        # External screen recorder state (4-state classification)
        self._detected_external_recorders: List[str] = []
        self._detailed_external_recorders: List[Dict[str, Any]] = []
        self._installed_only_recorders: List[Dict[str, Any]] = []
        self._tracked_incident_recorders: Dict[str, Dict[str, Any]] = {}
        self._external_recorder_warning: Optional[str] = None
        self._last_recorder_scan_mono: float = 0.0

        # Windows monitor connection & unknown capture state tracking
        self._windows_monitor_connected: bool = True
        self._windows_capture_state_unknown: bool = False

        # Persistent SENSITIVE_PAGE_CAPTURE_RISK incident tracking
        self._risk_incident_active: bool = False
        self._active_risk_event: Optional[Dict[str, Any]] = None
        self._last_resolved_risk_event: Optional[Dict[str, Any]] = None
        self._alert_dismissed_after_clear: bool = False

        # Configurable sensitive domains & custom recorder process names
        self._custom_domains: Dict[str, str] = {}  # domain -> category
        self._custom_recorders: Dict[str, str] = {}  # exe_lower -> display_name

        # Sanitized event & notification history (bounded in memory)
        self._event_history: List[Dict[str, Any]] = []
        self._notifications: List[Dict[str, Any]] = []

        # Attempt to load persisted custom domains from DB
        self._load_persisted_config()

    def reset(self) -> None:
        with self._lock:
            self._protection_state = ProtectionState.INACTIVE
            self._visual_monitoring_paused = False
            self._cooldown_started_mono = None
            self._cooldown_expires_mono = None
            self._last_activated_wall = None
            self._last_resumed_wall = None
            self._active_sensitive_tabs.clear()
            self._closed_incident_tabs.clear()
            self._browser_capture_sessions.clear()
            self._current_category = None
            self._current_sub_type = None
            self._current_matched_domain = None
            self._current_alert_title = None
            self._current_alert_message = None
            self._processed_event_ids.clear()
            self._notified_tab_signatures.clear()
            self._notified_risk_signatures.clear()
            self._detected_external_recorders.clear()
            self._detailed_external_recorders.clear()
            self._installed_only_recorders.clear()
            self._tracked_incident_recorders.clear()
            self._external_recorder_warning = None
            self._last_recorder_scan_mono = 0.0
            self._windows_monitor_connected = True
            self._windows_capture_state_unknown = False
            self._risk_incident_active = False
            self._active_risk_event = None
            self._last_resolved_risk_event = None
            self._alert_dismissed_after_clear = False
            self._event_history.clear()
            self._notifications.clear()

    def set_clock(self, clock_fn: Callable[[], float]) -> None:
        with self._lock:
            self._clock = clock_fn

    def set_process_scanner(self, scanner_fn: Optional[Callable[[], List[Any]]]) -> None:
        with self._lock:
            self._process_scanner_fn = scanner_fn
            self._last_recorder_scan_mono = 0.0

    def set_notification_callback(self, callback: Optional[Callable[[str, str, Dict[str, Any]], None]]) -> None:
        with self._lock:
            self._notification_callback = callback

    def set_windows_monitor_connection_state(self, connected: bool) -> Dict[str, Any]:
        """
        Records whether the browser extension and Windows capture monitor have a healthy
        connection. If the Windows monitor disconnects while a sensitive page is open or
        a risk incident is active, the capture state is marked `CAPTURE STATUS UNKNOWN`
        and the persistent alert + visual capture pause remain active.
        """
        with self._lock:
            self._windows_monitor_connected = bool(connected)
            if not connected:
                if len(self._active_sensitive_tabs) > 0 or self._risk_incident_active or self._visual_monitoring_paused:
                    self._windows_capture_state_unknown = True
                    self._visual_monitoring_paused = True
                    if len(self._active_sensitive_tabs) > 0:
                        self._risk_incident_active = True
            else:
                self._windows_capture_state_unknown = False
            self._evaluate_state_locked()
            return self.get_status()

    # ------------------------------------------------------------------
    # Persistence & Configuration
    # ------------------------------------------------------------------
    def _load_persisted_config(self) -> None:
        try:
            from core.database import StrictRecordsDB
            db = StrictRecordsDB()
            conn = db._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sensitive_page_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    browser TEXT,
                    tab_id TEXT,
                    category TEXT,
                    matched_domain TEXT,
                    detection_timestamp TEXT,
                    protection_state TEXT,
                    external_recorders_json TEXT
                )
                """
            )
            conn.commit()
            raw_cfg = db.get_setting("sensitive_page_protection_config")
            if raw_cfg:
                data = json.loads(raw_cfg)
                if isinstance(data.get("custom_domains"), dict):
                    for d, c in data["custom_domains"].items():
                        clean_d = sanitize_domain_or_origin(d)
                        if clean_d and c in SensitiveCategory.ALL_SENSITIVE:
                            self._custom_domains[clean_d] = c
                if isinstance(data.get("custom_recorders"), dict):
                    for exe, label in data["custom_recorders"].items():
                        if exe and label:
                            self._custom_recorders[str(exe).lower().strip()] = str(label).strip()
        except Exception as exc:
            logger.debug(f"Could not load persisted sensitive page config: {exc}")

    def _save_persisted_config(self) -> None:
        try:
            from core.database import StrictRecordsDB
            db = StrictRecordsDB()
            payload = {
                "custom_domains": dict(self._custom_domains),
                "custom_recorders": dict(self._custom_recorders),
            }
            db.set_setting("sensitive_page_protection_config", json.dumps(payload))
        except Exception as exc:
            logger.debug(f"Could not save sensitive page config: {exc}")

    def _persist_sanitized_event(self, record: Dict[str, Any]) -> None:
        try:
            from core.database import StrictRecordsDB
            db = StrictRecordsDB()
            conn = db._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sensitive_page_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    browser TEXT,
                    tab_id TEXT,
                    category TEXT,
                    matched_domain TEXT,
                    detection_timestamp TEXT,
                    protection_state TEXT,
                    external_recorders_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO sensitive_page_events
                (event_id, session_id, browser, tab_id, category, matched_domain,
                 detection_timestamp, protection_state, external_recorders_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.get("event_id", "")),
                    str(record.get("session_id") or ""),
                    str(record.get("browser") or "browser"),
                    str(record.get("tab_id") or ""),
                    str(record.get("category") or ""),
                    sanitize_domain_or_origin(str(record.get("matched_domain") or "")),
                    str(record.get("detection_timestamp") or ""),
                    str(record.get("protection_state") or ""),
                    json.dumps(record.get("external_recorders") or []),
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.debug(f"Could not persist sensitive page event: {exc}")

    def update_config(
        self,
        add_domains: Optional[Dict[str, str]] = None,
        remove_domains: Optional[List[str]] = None,
        add_recorders: Optional[Dict[str, str]] = None,
        remove_recorders: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if add_domains:
                for dom, cat in add_domains.items():
                    clean_dom = sanitize_domain_or_origin(dom)
                    cat_upper = str(cat or "").upper().strip()
                    if clean_dom and cat_upper in SensitiveCategory.ALL_SENSITIVE:
                        self._custom_domains[clean_dom] = cat_upper
            if remove_domains:
                for dom in remove_domains:
                    clean_dom = sanitize_domain_or_origin(dom)
                    self._custom_domains.pop(clean_dom, None)
            if add_recorders:
                for exe, label in add_recorders.items():
                    exe_clean = str(exe or "").lower().strip()
                    if exe_clean and not exe_clean.endswith(".exe"):
                        exe_clean += ".exe"
                    if exe_clean:
                        self._custom_recorders[exe_clean] = str(label or exe_clean).strip()
            if remove_recorders:
                for exe in remove_recorders:
                    exe_clean = str(exe or "").lower().strip()
                    self._custom_recorders.pop(exe_clean, None)
                    if not exe_clean.endswith(".exe"):
                        self._custom_recorders.pop(exe_clean + ".exe", None)
            self._save_persisted_config()
            return self.get_config()

    def get_config(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "cooldown_seconds": self._cooldown_duration,
                "custom_domains": dict(self._custom_domains),
                "custom_recorders": dict(self._custom_recorders),
                "categories": list(SensitiveCategory.ALL_SENSITIVE),
            }

    # ------------------------------------------------------------------
    # External Screen-Recording Application Detection (4-State Classification, Non-Destructive)
    # ------------------------------------------------------------------
    def detect_external_screen_recorders_detailed(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Best-effort Windows process check for known external screen-recording applications.
        Distinguishes carefully between:
          1. INSTALLED ("INSTALLED" — application is installed on disk only, NOT running; is_active=False)
          2. PROCESS_RUNNING_CAPTURE_UNKNOWN ("PROCESS RUNNING – CAPTURE STATUS UNKNOWN" — e.g. obs64.exe
             is running, which poses a capture risk when a sensitive page is open, but process presence alone
             is not described as proof of active recording)
          3. CAPTURE_SESSION_CONFIRMED_ACTIVE ("CAPTURE SESSION CONFIRMED ACTIVE" — confirmed active capture
             session, e.g. dedicated capture helper process, active recording flag, or open video output handle)
          4. CAPTURE_STATUS_UNKNOWN ("CAPTURE STATUS UNKNOWN" — Windows monitor disconnected)

        NEVER terminates or modifies any external process.
        """
        with self._lock:
            now_mono = self._clock()
            if not force_refresh and (now_mono - self._last_recorder_scan_mono) < 2.0 and self._last_recorder_scan_mono > 0:
                return [dict(item) for item in self._detailed_external_recorders]

            self._last_recorder_scan_mono = now_mono
            now_iso = datetime.now().isoformat()
            recorder_map = dict(BUILTIN_EXTERNAL_RECORDERS)
            recorder_map.update(self._custom_recorders)

            active_by_label: Dict[str, Dict[str, Any]] = {}
            installed_only_list: List[Dict[str, Any]] = []

            def _record_entry(
                exe_name: str,
                capture_confirmed: bool = False,
                installed_only: bool = False,
                explicit_classification: Optional[str] = None,
            ) -> None:
                low = str(exe_name or "").lower().strip()
                if not low:
                    return
                label = recorder_map.get(low)
                if not label:
                    return
                if installed_only:
                    installed_only_list.append({
                        "name": label,
                        "process_name": low,
                        "classification": CaptureClassification.INSTALLED,
                        "status": CaptureClassification.INSTALLED,
                        "is_active": False,
                        "capture_confirmed": False,
                        "source_layer": "WINDOWS_PROCESS",
                    })
                    return

                is_confirmed = bool(capture_confirmed or (low in CONFIRMED_ACTIVE_RECORDER_PROCESSES))
                if explicit_classification:
                    classification = explicit_classification
                elif is_confirmed:
                    classification = CaptureClassification.CAPTURE_SESSION_CONFIRMED_ACTIVE
                else:
                    classification = CaptureClassification.PROCESS_RUNNING_CAPTURE_UNKNOWN

                existing = active_by_label.get(label)
                # Upgrade to CAPTURE_SESSION_CONFIRMED_ACTIVE if any matching process is confirmed active
                if existing is None or (is_confirmed and not existing.get("capture_confirmed")):
                    active_by_label[label] = {
                        "name": label,
                        "process_name": low,
                        "classification": classification,
                        "status": classification,
                        "is_active": True,
                        "capture_confirmed": is_confirmed,
                        "source_layer": "WINDOWS_PROCESS",
                        "detected_at": now_iso,
                    }

            try:
                if self._process_scanner_fn is not None:
                    proc_items = self._process_scanner_fn() or []
                    for item in proc_items:
                        if isinstance(item, dict):
                            pname = str(item.get("name") or item.get("process_name") or "").strip()
                            installed_only = bool(
                                item.get("installed_only")
                                or str(item.get("classification") or "").upper() == CaptureClassification.INSTALLED
                            )
                            confirmed = bool(
                                item.get("capture_confirmed")
                                or item.get("active_capture")
                                or str(item.get("classification") or "").upper()
                                in (CaptureClassification.CAPTURE_SESSION_CONFIRMED_ACTIVE, CaptureClassification.ACTIVE)
                            )
                            _record_entry(
                                pname,
                                capture_confirmed=confirmed,
                                installed_only=installed_only,
                            )
                        else:
                            raw_str = str(item or "").strip()
                            low_str = raw_str.lower()
                            if low_str.endswith(":installed"):
                                _record_entry(low_str.rsplit(":", 1)[0], installed_only=True)
                            elif low_str.endswith(":active") or low_str.endswith(":confirmed"):
                                _record_entry(low_str.rsplit(":", 1)[0], capture_confirmed=True)
                            elif low_str.endswith(":running"):
                                _record_entry(low_str.rsplit(":", 1)[0], capture_confirmed=False)
                            else:
                                _record_entry(low_str, capture_confirmed=(low_str in CONFIRMED_ACTIVE_RECORDER_PROCESSES))
                elif HAS_PSUTIL and psutil is not None:
                    for proc in psutil.process_iter(["name", "cmdline"]):
                        try:
                            pname = (proc.info.get("name") or "").lower().strip()
                            if pname not in recorder_map:
                                continue
                            confirmed = pname in CONFIRMED_ACTIVE_RECORDER_PROCESSES
                            if not confirmed:
                                cmdline = [str(c).lower() for c in (proc.info.get("cmdline") or [])]
                                if any(
                                    flag in arg
                                    for arg in cmdline
                                    for flag in ("--startrecording", "--startstreaming", "--record")
                                ):
                                    confirmed = True
                            _record_entry(pname, capture_confirmed=confirmed)
                        except Exception:
                            continue
            except Exception as exc:
                logger.debug(f"Process scan for external recorders encountered error: {exc}")

            detailed_list = list(active_by_label.values())
            detected_labels = [item["name"] for item in detailed_list]

            # Update tracked incident recorders so any recorder that was active during an incident
            # and subsequently closed is shown as STOPPED until the incident clears
            for item in detailed_list:
                self._tracked_incident_recorders[item["name"]] = dict(item)
            for tracked_name, tracked_entry in list(self._tracked_incident_recorders.items()):
                if tracked_name not in active_by_label:
                    tracked_entry["is_active"] = False
                    tracked_entry["classification"] = CaptureClassification.STOPPED
                    tracked_entry["status"] = CaptureClassification.STOPPED
                    tracked_entry["stopped_at"] = now_iso

            self._detailed_external_recorders = detailed_list
            self._installed_only_recorders = installed_only_list
            self._detected_external_recorders = detected_labels

            if detailed_list:
                parts = [f"{item['name']} ({item['classification']})" for item in detailed_list]
                names_str = ", ".join(detected_labels)
                self._external_recorder_warning = (
                    f"EXTERNAL SCREEN RECORDER DETECTED ({names_str}) [{'; '.join(parts)}]: "
                    "Please close the external recording application before handling sensitive account or payment information."
                )
            else:
                self._external_recorder_warning = None

            return [dict(item) for item in detailed_list]

    def detect_external_screen_recorders(self, force_refresh: bool = False) -> List[str]:
        """
        Returns the list of active external recorder display names (excluding INSTALLED-only entries).
        NEVER terminates or modifies any external process.
        """
        with self._lock:
            self.detect_external_screen_recorders_detailed(force_refresh=force_refresh)
            return list(self._detected_external_recorders)

    # ------------------------------------------------------------------
    # Browser-Level Screen Sharing & Tab Capture Tracking
    # ------------------------------------------------------------------
    def handle_browser_capture_event(
        self,
        capture_id: str = "default",
        source_type: str = "SCREEN_SHARING",
        is_active: bool = True,
        tab_id: Any = None,
        window_id: Any = None,
        browser: str = "browser",
        display_name: Optional[str] = None,
        event_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Records start or stop of a browser-level capture source:
          - Extension-owned tab capture (`TAB_CAPTURE`)
          - Desktop/window capture (`DESKTOP_CAPTURE`)
          - Web page `navigator.mediaDevices.getDisplayMedia` stream (`SCREEN_SHARING`)
        """
        with self._lock:
            now_mono = self._clock()
            now_iso = timestamp or datetime.now().isoformat()
            browser_clean = str(browser or "browser").lower().strip()
            cap_id_clean = str(capture_id or f"{source_type}_{tab_id or 'global'}").strip()
            cap_key = f"{browser_clean}:{cap_id_clean}"

            src_upper = str(source_type or "SCREEN_SHARING").upper().strip()
            if not display_name:
                if src_upper == "TAB_CAPTURE":
                    display_name = "Browser tab capture"
                elif src_upper == "DESKTOP_CAPTURE":
                    display_name = "Browser desktop capture"
                else:
                    display_name = "Browser screen sharing"

            existing = self._browser_capture_sessions.get(cap_key)
            if is_active:
                self._browser_capture_sessions[cap_key] = {
                    "capture_key": cap_key,
                    "capture_id": cap_id_clean,
                    "browser": browser_clean,
                    "tab_id": str(tab_id) if tab_id is not None else None,
                    "window_id": str(window_id) if window_id is not None else None,
                    "source_type": src_upper,
                    "name": display_name,
                    "classification": CaptureClassification.ACTIVE,
                    "status": CaptureClassification.ACTIVE,
                    "is_active": True,
                    "capture_confirmed": True,
                    "source_layer": "BROWSER_EXTENSION",
                    "detected_at": existing.get("detected_at", now_iso) if existing else now_iso,
                    "last_seen_mono": now_mono,
                }
                # If a sensitive page is already open (or a risk incident is already active),
                # immediately ensure visual capture is paused and risk incident is active
                if len(self._active_sensitive_tabs) > 0 or self._risk_incident_active:
                    self._risk_incident_active = True
                    self._alert_dismissed_after_clear = False
                    if not self._visual_monitoring_paused:
                        self._visual_monitoring_paused = True
                        self._cooldown_started_mono = now_mono
                        self._cooldown_expires_mono = now_mono + self._cooldown_duration
                        self._last_activated_wall = now_iso
            else:
                if existing is not None:
                    existing["is_active"] = False
                    existing["classification"] = CaptureClassification.STOPPED
                    existing["status"] = CaptureClassification.STOPPED
                    existing["stopped_at"] = now_iso
                else:
                    # Also check if any active capture matches tab_id or source_type when capture_id was generic
                    for k, sess in self._browser_capture_sessions.items():
                        if sess.get("is_active") and (
                            (tab_id is not None and str(sess.get("tab_id")) == str(tab_id))
                            or sess.get("source_type") == src_upper
                        ):
                            sess["is_active"] = False
                            sess["classification"] = CaptureClassification.STOPPED
                            sess["status"] = CaptureClassification.STOPPED
                            sess["stopped_at"] = now_iso

            self._evaluate_state_locked()
            return self.get_status()

    # ------------------------------------------------------------------
    # Extension Service Worker Restart & State Reconciliation
    # ------------------------------------------------------------------
    def reconcile_extension_state(
        self,
        active_tabs: Optional[List[Dict[str, Any]]] = None,
        browser_captures: Optional[List[Dict[str, Any]]] = None,
        browser: str = "browser",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Reconciles open browser tabs and active browser capture sessions after an extension
        service worker restart or reconnect.
        - Re-classifies or restores all currently open sensitive tabs.
        - Removes stale tabs for that browser that are no longer open.
        - Reconciles active browser captures.
        - Clears `CAPTURE STATUS UNKNOWN` once Windows and the extension have reconciled.
        """
        with self._lock:
            now_mono = self._clock()
            now_iso = datetime.now().isoformat()
            browser_clean = str(browser or "browser").lower().strip()
            self._windows_monitor_connected = True
            self._windows_capture_state_unknown = False

            seen_tab_keys: Set[str] = set()
            for tab_info in (active_tabs or []):
                if not isinstance(tab_info, dict):
                    continue
                t_id = str(tab_info.get("tab_id") if tab_info.get("tab_id") is not None else "active").strip()
                w_id = tab_info.get("window_id")
                t_browser = str(tab_info.get("browser") or browser_clean).lower().strip()
                raw_domain_or_url = str(
                    tab_info.get("matched_domain")
                    or tab_info.get("matched_origin")
                    or tab_info.get("url")
                    or ""
                ).strip()
                cat = str(tab_info.get("category") or "").upper().strip()
                sub = tab_info.get("sub_type")

                if cat not in SensitiveCategory.ALL_SENSITIVE:
                    classified = classify_sensitive_page(
                        raw_domain_or_url,
                        frame_origins=tab_info.get("frame_origins"),
                        custom_domains=self._custom_domains,
                    )
                    if not classified["is_sensitive"]:
                        continue
                    cat = classified["category"]
                    sub = classified.get("sub_type")
                    clean_dom = classified["matched_domain"]
                else:
                    clean_dom = sanitize_domain_or_origin(raw_domain_or_url) or "sensitive.local"

                t_key = f"{t_browser}:{t_id}"
                seen_tab_keys.add(t_key)
                existing = self._active_sensitive_tabs.get(t_key)
                label = get_sensitive_page_display_label(cat, sub, clean_dom)
                self._active_sensitive_tabs[t_key] = {
                    "tab_key": t_key,
                    "browser": t_browser,
                    "tab_id": t_id,
                    "window_id": str(w_id) if w_id is not None else (existing.get("window_id") if existing else "1"),
                    "category": cat,
                    "sub_type": sub or cat,
                    "display_label": label,
                    "matched_domain": clean_dom,
                    "matched_origin": clean_dom,
                    "detection_source": tab_info.get("detection_source", "RECONCILED_TAB"),
                    "detected_at": existing.get("detected_at", now_iso) if existing else (tab_info.get("detected_at") or now_iso),
                    "status": "OPEN",
                    "last_seen_mono": now_mono,
                }
                self._closed_incident_tabs.pop(t_key, None)

                if not self._visual_monitoring_paused:
                    self._visual_monitoring_paused = True
                    self._protection_state = ProtectionState.SENSITIVE_PAGE_PROTECTION_ACTIVE
                    self._cooldown_started_mono = now_mono
                    self._cooldown_expires_mono = now_mono + self._cooldown_duration
                    self._last_activated_wall = now_iso
                self._current_category = cat
                self._current_sub_type = sub or cat
                self._current_matched_domain = clean_dom

            # Mark any previously tracked tabs for this browser that are no longer open as closed
            if active_tabs is not None:
                for old_key in list(self._active_sensitive_tabs.keys()):
                    if old_key.startswith(f"{browser_clean}:") and old_key not in seen_tab_keys:
                        removed = self._active_sensitive_tabs.pop(old_key, None)
                        if removed and self._risk_incident_active:
                            closed_copy = dict(removed)
                            closed_copy["status"] = "CLOSED"
                            closed_copy["closed_at"] = now_iso
                            self._closed_incident_tabs[old_key] = closed_copy

            # Reconcile browser captures if provided
            if browser_captures is not None:
                seen_cap_keys: Set[str] = set()
                for cap in browser_captures:
                    if not isinstance(cap, dict):
                        continue
                    c_id = str(cap.get("capture_id") or cap.get("id") or "default").strip()
                    c_active = bool(cap.get("is_active", True))
                    c_key = f"{browser_clean}:{c_id}"
                    seen_cap_keys.add(c_key)
                    self.handle_browser_capture_event(
                        capture_id=c_id,
                        source_type=str(cap.get("source_type") or "SCREEN_SHARING"),
                        is_active=c_active,
                        tab_id=cap.get("tab_id"),
                        window_id=cap.get("window_id"),
                        browser=browser_clean,
                        display_name=cap.get("display_name") or cap.get("name"),
                    )
                for old_cap_key, sess in list(self._browser_capture_sessions.items()):
                    if old_cap_key.startswith(f"{browser_clean}:") and old_cap_key not in seen_cap_keys:
                        sess["is_active"] = False
                        sess["classification"] = CaptureClassification.STOPPED
                        sess["status"] = CaptureClassification.STOPPED
                        sess["stopped_at"] = now_iso

            self.detect_external_screen_recorders_detailed(force_refresh=True)
            self._evaluate_state_locked()
            return self.get_status()

    # ------------------------------------------------------------------
    # Persistent Alert Dismissal Gate
    # ------------------------------------------------------------------
    def dismiss_persistent_alert(self, admin_override: bool = False, reason: str = "") -> Dict[str, Any]:
        """
        Attempts to dismiss the persistent Sensitive Page Protection / Capture Risk notice.
        - Rejects ordinary dismissal (`dismissed=False`) whenever any sensitive page remains open,
          any detected screen-recording/screen-sharing activity remains active, or Windows capture
          status is UNKNOWN.
        - Allows dismissal once all sensitive pages and detected captures are closed
          (`all_sources_cleared == True`), or under an explicit audited `admin_override=True`.
        """
        with self._lock:
            self._evaluate_state_locked()
            active_tab_count = len(self._active_sensitive_tabs)
            active_cap_count = self._count_active_detected_captures_locked()

            if not admin_override and (active_tab_count > 0 or active_cap_count > 0 or self._windows_capture_state_unknown):
                status = self.get_status()
                status["dismissed"] = False
                status["dismiss_rejected_reason"] = (
                    "Cannot dismiss alert while a sensitive page or detected screen-capture activity remains active."
                )
                return status

            self._alert_dismissed_after_clear = True
            if admin_override:
                now_iso = datetime.now().isoformat()
                override_ev = {
                    "event_id": str(uuid.uuid4()),
                    "event_type": "SENSITIVE_ALERT_ADMIN_OVERRIDE",
                    "timestamp": now_iso,
                    "reason": str(reason or "Explicit administrator override"),
                    "active_sensitive_tab_count": active_tab_count,
                    "active_detected_capture_count": active_cap_count,
                }
                self._event_history.insert(0, override_ev)

            status = self.get_status()
            status["dismissed"] = True
            return status

    # ------------------------------------------------------------------
    # Category-Specific Alert Messaging
    # ------------------------------------------------------------------
    @staticmethod
    def build_category_alert(
        category: str,
        sub_type: Optional[str] = None,
        external_recorders: Optional[List[str]] = None,
    ) -> Tuple[str, str]:
        """
        Returns (alert_title, alert_message) tailored to the detected sensitive category.
        """
        cat = str(category or "").upper()
        sub = str(sub_type or "").upper()

        if cat == SensitiveCategory.PAYMENT_CHECKOUT:
            title = "SENSITIVE PAGE DETECTED — Payment / Checkout Page Detected"
            msg = (
                "A sensitive payment page has been opened. "
                "StrictRecords visual screen monitoring has been paused temporarily to protect your financial information. "
                "Please close external screen-recording applications before continuing."
            )
        elif cat == SensitiveCategory.EMAIL_PERSONAL:
            if sub == "GMAIL":
                title = "SENSITIVE PAGE DETECTED — Sensitive Email Account Detected (Gmail)"
            elif sub == "OUTLOOK":
                title = "SENSITIVE PAGE DETECTED — Sensitive Email Account Detected (Outlook)"
            else:
                title = "SENSITIVE PAGE DETECTED — Sensitive Email Account Detected"
            msg = (
                "Sensitive Email Account Detected – Visual Monitoring Paused. "
                "StrictRecords visual screen monitoring has been paused for 5 minutes to protect your personal email and account credentials. "
                "Please close external screen-recording applications before continuing."
            )
        elif cat == SensitiveCategory.BANKING_FINANCIAL:
            title = "SENSITIVE PAGE DETECTED — Banking Page Detected"
            msg = (
                "Banking Page Detected – Sensitive Page Protection Active. "
                "StrictRecords visual screen monitoring has been paused temporarily to protect your banking and financial information. "
                "Please close external screen-recording applications before continuing."
            )
        elif cat == SensitiveCategory.CRYPTO_WALLET:
            title = "SENSITIVE PAGE DETECTED — Crypto Wallet Page Detected"
            msg = (
                "Crypto Wallet Page Detected – Sensitive Page Protection Active. "
                "StrictRecords visual screen monitoring has been paused temporarily to protect your wallet and digital asset credentials. "
                "Please close external screen-recording applications before continuing."
            )
        else:
            title = "SENSITIVE PAGE DETECTED"
            msg = (
                "A sensitive page has been opened. StrictRecords visual screen monitoring has been paused temporarily. "
                "Please close external screen-recording applications before continuing."
            )

        if external_recorders:
            rec_list = ", ".join(external_recorders)
            msg += (
                f" [EXTERNAL SCREEN RECORDER DETECTED: {rec_list} — "
                "Please close the external recording application before handling sensitive account or payment information.]"
            )
        return title, msg

    # ------------------------------------------------------------------
    # State Evaluation, Multi-Source Risk Incident & 5-Minute Cooldown Lifecycle
    # ------------------------------------------------------------------
    def _count_active_detected_captures_locked(self) -> int:
        active_win = sum(1 for r in self._detailed_external_recorders if r.get("is_active"))
        active_browser = sum(1 for c in self._browser_capture_sessions.values() if c.get("is_active"))
        unknown_penalty = 1 if self._windows_capture_state_unknown else 0
        return active_win + active_browser + unknown_penalty

    def _sync_risk_incident_locked(self) -> None:
        """
        Maintains the `SENSITIVE_PAGE_CAPTURE_RISK` lifecycle across multiple sensitive tabs
        and multiple capture sources (Windows processes + browser tab/screen capture).
        - Starts a risk incident when `active_sensitive_tab_count > 0` AND `active_detected_capture_count > 0`.
        - Once started, keeps the incident active while `active_sensitive_tab_count > 0` OR
          `active_detected_capture_count > 0` (so closing sensitive tabs while capture continues [11D],
          or stopping one capture while another capture/tab remains open [11E], keeps the warning active).
        - Marks the incident resolved ONLY when `active_sensitive_tab_count == 0` AND
          `active_detected_capture_count == 0` AND `not self._windows_capture_state_unknown`.
        """
        now_mono = self._clock()
        now_iso = datetime.now().isoformat()

        active_tabs = list(self._active_sensitive_tabs.values())
        active_tab_count = len(active_tabs)
        active_win_recorders = [r for r in self._detailed_external_recorders if r.get("is_active")]
        active_browser_caps = [c for c in self._browser_capture_sessions.values() if c.get("is_active")]
        active_cap_count = len(active_win_recorders) + len(active_browser_caps) + (1 if self._windows_capture_state_unknown else 0)

        # Trigger condition: sensitive page open + detected capture activity
        if active_tab_count > 0 and active_cap_count > 0:
            self._risk_incident_active = True
            self._alert_dismissed_after_clear = False

        if self._risk_incident_active:
            if active_tab_count > 0 or active_cap_count > 0 or self._windows_capture_state_unknown:
                # Risk incident remains active! Keep StrictRecords visual capture paused.
                if not self._visual_monitoring_paused:
                    self._visual_monitoring_paused = True
                    if self._cooldown_expires_mono is None:
                        self._cooldown_started_mono = now_mono
                        self._cooldown_expires_mono = now_mono + self._cooldown_duration
                        self._last_activated_wall = now_iso

                tab_ids = [str(t.get("tab_id")) for t in active_tabs]
                categories = sorted({str(t.get("category")) for t in active_tabs if t.get("category")})
                origins = sorted({str(t.get("matched_domain")) for t in active_tabs if t.get("matched_domain")})
                capture_sources = (
                    [r["name"] for r in active_win_recorders]
                    + [c["name"] for c in active_browser_caps]
                    + (["Windows capture monitor"] if self._windows_capture_state_unknown else [])
                )
                classifications = {
                    r["name"]: r["classification"] for r in active_win_recorders
                }
                for c in active_browser_caps:
                    classifications[c["name"]] = c["classification"]
                if self._windows_capture_state_unknown:
                    classifications["Windows capture monitor"] = CaptureClassification.CAPTURE_STATUS_UNKNOWN

                if self._active_risk_event is None:
                    risk_ev_id = str(uuid.uuid4())
                    self._active_risk_event = {
                        "event_id": risk_ev_id,
                        "event_type": "SENSITIVE_PAGE_CAPTURE_RISK",
                        "status": "ACTIVE",
                        "resolved": False,
                        "heading": self.RISK_ALERT_HEADING,
                        "message": self.RISK_ALERT_BODY,
                        "browser_tab_ids": tab_ids,
                        "sensitive_categories": categories,
                        "matched_origins": origins,
                        "detected_capture_sources": capture_sources,
                        "capture_classification": classifications,
                        "detection_timestamp": now_iso,
                        "updated_timestamp": now_iso,
                        "current_protection_state": self._protection_state,
                    }
                    self._event_history.insert(0, dict(self._active_risk_event))
                    if len(self._event_history) > 100:
                        self._event_history = self._event_history[:100]
                else:
                    # Merge/update active risk event metadata
                    self._active_risk_event["status"] = "ACTIVE"
                    self._active_risk_event["resolved"] = False
                    if tab_ids:
                        self._active_risk_event["browser_tab_ids"] = tab_ids
                    if categories:
                        self._active_risk_event["sensitive_categories"] = categories
                    if origins:
                        self._active_risk_event["matched_origins"] = origins
                    if capture_sources:
                        self._active_risk_event["detected_capture_sources"] = capture_sources
                    self._active_risk_event["capture_classification"] = classifications
                    self._active_risk_event["updated_timestamp"] = now_iso
                    self._active_risk_event["current_protection_state"] = self._protection_state
            else:
                # Both activeSensitiveTabCount == 0 AND activeDetectedCaptureCount == 0!
                # Resolve the risk incident automatically.
                self._risk_incident_active = False
                if self._active_risk_event is not None:
                    self._active_risk_event["status"] = "RESOLVED"
                    self._active_risk_event["resolved"] = True
                    self._active_risk_event["resolved_timestamp"] = now_iso
                    self._active_risk_event["resolution_message"] = self.PANEL_RESOLVED_STATUS
                    self._last_resolved_risk_event = dict(self._active_risk_event)
                    self._active_risk_event = None
                self._closed_incident_tabs.clear()
                self._tracked_incident_recorders.clear()
                for cap_sess in list(self._browser_capture_sessions.values()):
                    if not cap_sess.get("is_active"):
                        self._browser_capture_sessions.pop(cap_sess.get("capture_key", ""), None)

    def _evaluate_state_locked(self) -> None:
        """
        Updates the authoritative protection state based on:
          1. Active sensitive tabs (`_active_sensitive_tabs`)
          2. Active screen-recording / screen-sharing sources (`_detailed_external_recorders` + `_browser_capture_sessions`)
          3. The 5-minute monotonic privacy cooldown timer.
        """
        # Refresh external recorder detection if protection or risk incident is active
        if self._visual_monitoring_paused or self._risk_incident_active or len(self._active_sensitive_tabs) > 0:
            self.detect_external_screen_recorders_detailed(force_refresh=False)

        self._sync_risk_incident_locked()

        if not self._visual_monitoring_paused:
            return

        now_mono = self._clock()
        expires_mono = self._cooldown_expires_mono or now_mono
        remaining = max(0.0, expires_mono - now_mono)
        active_cap_count = self._count_active_detected_captures_locked()

        if remaining > 0.0:
            self._protection_state = ProtectionState.SENSITIVE_PAGE_PROTECTION_ACTIVE
            self._visual_monitoring_paused = True
            return

        # Cooldown timer (5 minutes) has reached 00:00
        # Never resume visual capture if any sensitive tab remains open OR if a risk incident still has active capture!
        if len(self._active_sensitive_tabs) == 0 and not (self._risk_incident_active and active_cap_count > 0) and not self._windows_capture_state_unknown:
            # Case A — No sensitive pages or active incident captures remain: safely resume visual screen monitoring
            self._visual_monitoring_paused = False
            self._protection_state = ProtectionState.VISUAL_MONITORING_RESUMED
            self._cooldown_started_mono = None
            self._cooldown_expires_mono = None
            self._last_resumed_wall = datetime.now().isoformat()
            logger.info("[SensitivePageProtection] 5-minute cooldown completed and all sensitive tabs/captures closed -> VISUAL_MONITORING_RESUMED")
        else:
            # Case B — Sensitive page or active capture risk still present when 5 minutes expire: keep visual capture paused!
            self._visual_monitoring_paused = True
            self._protection_state = ProtectionState.SENSITIVE_PAGE_STILL_OPEN_PROTECTED

    def is_visual_monitoring_paused(self) -> bool:
        """
        Authoritative check called by StrictScreenMonitor, OCR pipelines, and screenshot savers.
        Returns True whenever Sensitive Page Protection is active or a sensitive page/capture risk remains open.
        """
        with self._lock:
            self._evaluate_state_locked()
            return bool(self._visual_monitoring_paused)

    # ------------------------------------------------------------------
    # Event Handlers (from Browser Extension or Direct Classification)
    # ------------------------------------------------------------------
    def classify_and_handle_navigation(
        self,
        url_or_origin: str,
        tab_id: Any = "active",
        browser: str = "browser",
        session_id: Optional[str] = None,
        frame_origins: Optional[List[str]] = None,
        redirect_url: Optional[str] = None,
        event_id: Optional[str] = None,
        window_id: Any = None,
    ) -> Dict[str, Any]:
        """
        Classifies a navigation or redirect event and updates the authoritative protection state.
        If a previously sensitive tab navigates to a normal page (e.g. Stripe Checkout -> Suno),
        marks that tab as no longer on a sensitive page while preserving the remaining 5-minute cooldown.
        """
        with self._lock:
            classification = classify_sensitive_page(
                url_or_origin=url_or_origin,
                frame_origins=frame_origins,
                redirect_url=redirect_url,
                custom_domains=self._custom_domains,
            )
            if classification["is_sensitive"]:
                return self.handle_sensitive_page_detected(
                    category=classification["category"],
                    matched_domain=classification["matched_domain"],
                    tab_id=tab_id,
                    window_id=window_id,
                    browser=browser,
                    session_id=session_id,
                    sub_type=classification.get("sub_type"),
                    detection_source=classification.get("detection_source", "TOP_LEVEL_URL"),
                    event_id=event_id,
                )
            else:
                return self.handle_sensitive_page_closed_or_navigated(
                    tab_id=tab_id,
                    browser=browser,
                    new_non_sensitive_domain=classification.get("matched_domain") or "",
                    reason="NAVIGATED_TO_NON_SENSITIVE_PAGE",
                )

    def handle_sensitive_page_detected(
        self,
        category: str,
        matched_domain: str,
        tab_id: Any = "active",
        browser: str = "browser",
        session_id: Optional[str] = None,
        sub_type: Optional[str] = None,
        detection_source: str = "TOP_LEVEL_URL",
        event_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        window_id: Any = None,
    ) -> Dict[str, Any]:
        """
        Handles an incoming SENSITIVE_PAGE_DETECTED event from the browser extension.
        - Sanitizes `matched_domain` so full URLs/tokens are NEVER stored.
        - Starts the 5-minute (300s) visual screen monitoring cooldown.
        - Checks for external screen-recording apps and browser screen-sharing/tab capture.
        - Emits a deduplicated Windows notification and activates `SENSITIVE_PAGE_CAPTURE_RISK` if capture is present.
        """
        with self._lock:
            now_mono = self._clock()
            now_iso = timestamp or datetime.now().isoformat()

            clean_domain = sanitize_domain_or_origin(matched_domain) or "sensitive.local"
            cat_upper = str(category or SensitiveCategory.PAYMENT_CHECKOUT).upper().strip()
            if cat_upper not in SensitiveCategory.ALL_SENSITIVE:
                recheck = classify_sensitive_page(clean_domain, custom_domains=self._custom_domains)
                cat_upper = recheck["category"] if recheck["is_sensitive"] else SensitiveCategory.PAYMENT_CHECKOUT
                if not sub_type:
                    sub_type = recheck.get("sub_type")
            elif not sub_type:
                recheck = classify_sensitive_page(clean_domain, custom_domains=self._custom_domains)
                if recheck["is_sensitive"] and recheck.get("sub_type"):
                    sub_type = recheck.get("sub_type")

            browser_clean = str(browser or "browser").lower().strip()
            tab_str = str(tab_id if tab_id is not None else "active").strip()
            win_str = str(window_id if window_id is not None else "1").strip()
            tab_key = f"{browser_clean}:{tab_str}"
            ev_id = str(event_id or uuid.uuid4())

            # Idempotency check
            if ev_id in self._processed_event_ids:
                self._evaluate_state_locked()
                return self.get_status()
            self._processed_event_ids.add(ev_id)

            # Check if this exact (tab_key, category, matched_domain) is already active
            existing_tab = self._active_sensitive_tabs.get(tab_key)
            is_same_active_page = bool(
                existing_tab
                and existing_tab.get("category") == cat_upper
                and existing_tab.get("matched_domain") == clean_domain
            )

            display_label = get_sensitive_page_display_label(cat_upper, sub_type, clean_domain)

            # Update active sensitive tab entry
            self._active_sensitive_tabs[tab_key] = {
                "tab_key": tab_key,
                "browser": browser_clean,
                "tab_id": tab_str,
                "window_id": win_str,
                "category": cat_upper,
                "sub_type": sub_type or cat_upper,
                "display_label": display_label,
                "matched_domain": clean_domain,
                "matched_origin": clean_domain,
                "detection_source": detection_source,
                "detected_at": existing_tab.get("detected_at", now_iso) if is_same_active_page else now_iso,
                "status": "OPEN",
                "last_seen_mono": now_mono,
            }
            self._closed_incident_tabs.pop(tab_key, None)
            self._alert_dismissed_after_clear = False

            # Activate visual monitoring pause and start/refresh 5-minute cooldown
            # Note: Do NOT repeatedly restart the 5-minute timer for minor DOM updates on the exact same page
            was_paused = self._visual_monitoring_paused
            self._visual_monitoring_paused = True
            self._protection_state = ProtectionState.SENSITIVE_PAGE_PROTECTION_ACTIVE

            if not was_paused or not is_same_active_page or self._cooldown_expires_mono is None:
                self._cooldown_started_mono = now_mono
                self._cooldown_expires_mono = now_mono + self._cooldown_duration
                self._last_activated_wall = now_iso

            # Scan for external screen-recording processes (4-state detailed classification)
            self.detect_external_screen_recorders_detailed(force_refresh=True)
            ext_recorders = list(self._detected_external_recorders)

            # Sync multi-source capture risk incident
            self._sync_risk_incident_locked()

            # Build category-specific alert title & message
            alert_title, alert_message = self.build_category_alert(
                category=cat_upper,
                sub_type=sub_type,
                external_recorders=ext_recorders,
            )
            if self._risk_incident_active:
                alert_title = f"{self.RISK_ALERT_HEADING} — {alert_title}"
                alert_message = f"{self.RISK_ALERT_BODY} {alert_message}"

            self._current_category = cat_upper
            self._current_sub_type = sub_type or cat_upper
            self._current_matched_domain = clean_domain
            self._current_alert_title = alert_title
            self._current_alert_message = alert_message

            # Deduplicate notifications so the same continuously active sensitive page does not spam alerts
            sig = f"{tab_key}|{cat_upper}|{clean_domain}"
            last_notif_mono = self._notified_tab_signatures.get(sig)
            should_notify = (not is_same_active_page) or (last_notif_mono is None)

            if should_notify:
                self._notified_tab_signatures[sig] = now_mono
                notif_payload = {
                    "event_id": ev_id,
                    "event_type": "SENSITIVE_PAGE_CAPTURE_RISK" if self._risk_incident_active else "SENSITIVE_PAGE_DETECTED",
                    "title": alert_title,
                    "message": alert_message,
                    "category": cat_upper,
                    "sub_type": sub_type or cat_upper,
                    "display_label": display_label,
                    "matched_domain": clean_domain,
                    "tab_id": tab_str,
                    "window_id": win_str,
                    "browser": browser_clean,
                    "session_id": session_id,
                    "timestamp": now_iso,
                    "cooldown_seconds": int(self._cooldown_duration),
                    "external_recorders": list(ext_recorders),
                    "external_recorder_warning": self._external_recorder_warning,
                    "capture_risk_active": bool(self._risk_incident_active),
                }
                self._notifications.insert(0, notif_payload)
                if len(self._notifications) > 50:
                    self._notifications = self._notifications[:50]

                event_record = {
                    "event_id": ev_id,
                    "session_id": session_id,
                    "browser": browser_clean,
                    "tab_id": tab_str,
                    "window_id": win_str,
                    "category": cat_upper,
                    "display_label": display_label,
                    "matched_domain": clean_domain,
                    "detection_timestamp": now_iso,
                    "protection_state": self._protection_state,
                    "external_recorders": list(ext_recorders),
                    "capture_risk_active": bool(self._risk_incident_active),
                }
                self._event_history.insert(0, event_record)
                if len(self._event_history) > 100:
                    self._event_history = self._event_history[:100]
                self._persist_sanitized_event(event_record)

                logger.info(
                    f"[SensitivePageProtection] SENSITIVE_PAGE_DETECTED category={cat_upper} "
                    f"domain={clean_domain} tab={tab_key} cooldown=300s ext_recorders={ext_recorders} "
                    f"risk_incident={self._risk_incident_active}"
                )

                if self._notification_callback is not None:
                    try:
                        self._notification_callback(alert_title, alert_message, notif_payload)
                    except Exception as cb_err:
                        logger.debug(f"Notification callback failed: {cb_err}")

            self._evaluate_state_locked()
            status = self.get_status()
            status["notification_emitted"] = should_notify
            status["event_id"] = ev_id
            return status

    def handle_sensitive_page_closed_or_navigated(
        self,
        tab_id: Any = "active",
        browser: str = "browser",
        new_non_sensitive_domain: str = "",
        reason: str = "TAB_CLOSED_OR_NAVIGATED",
    ) -> Dict[str, Any]:
        """
        Called when a sensitive tab is closed or navigates away to a non-sensitive domain
        (for example, returning from `checkout.stripe.com` to `suno.com`).
        - Removes the tab from `_active_sensitive_tabs`.
        - Clears its notification signature so a future visit to a sensitive page in that tab will alert.
        - Evaluates multi-source capture risk & cooldown:
          * If other sensitive tabs or detected capture activities remain open, the persistent warning stays active.
          * If the 5-minute cooldown is still running, visual protection remains active until the 5 minutes expire.
          * If the 5-minute cooldown has already expired and all sensitive tabs + captures are closed,
            immediately resumes visual monitoring (`VISUAL_MONITORING_RESUMED`).
        """
        with self._lock:
            now_iso = datetime.now().isoformat()
            browser_clean = str(browser or "browser").lower().strip()
            tab_str = str(tab_id if tab_id is not None else "active").strip()
            tab_key = f"{browser_clean}:{tab_str}"

            removed = self._active_sensitive_tabs.pop(tab_key, None)
            if removed is None:
                for k in list(self._active_sensitive_tabs.keys()):
                    if k.endswith(f":{tab_str}"):
                        removed = self._active_sensitive_tabs.pop(k, None)
                        tab_key = k
                        for sig_k in list(self._notified_tab_signatures.keys()):
                            if sig_k.startswith(f"{k}|"):
                                self._notified_tab_signatures.pop(sig_k, None)

            if removed is not None and self._risk_incident_active:
                closed_copy = dict(removed)
                closed_copy["status"] = "CLOSED"
                closed_copy["closed_at"] = now_iso
                closed_copy["close_reason"] = reason
                self._closed_incident_tabs[tab_key] = closed_copy

            for sig_k in list(self._notified_tab_signatures.keys()):
                if sig_k.startswith(f"{tab_key}|"):
                    self._notified_tab_signatures.pop(sig_k, None)

            self._evaluate_state_locked()
            return self.get_status()

    # ------------------------------------------------------------------
    # Authoritative Status Snapshot for Windows UI, Screen Monitor & Extension
    # ------------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            self._evaluate_state_locked()
            now_mono = self._clock()

            remaining_sec = 0.0
            if self._visual_monitoring_paused and self._cooldown_expires_mono is not None:
                remaining_sec = max(0.0, self._cooldown_expires_mono - now_mono)

            cooldown_mm_ss = format_mm_ss(remaining_sec)
            active_tabs_list = list(self._active_sensitive_tabs.values())
            active_tab_count = len(active_tabs_list)

            # Build individual Sensitive Pages summary (OPEN tabs + any CLOSED tabs from an active incident)
            sensitive_pages_summary: List[Dict[str, Any]] = []
            for t in active_tabs_list:
                label = t.get("display_label") or get_sensitive_page_display_label(
                    t.get("category", ""), t.get("sub_type"), t.get("matched_domain")
                )
                sensitive_pages_summary.append({
                    "tab_id": t.get("tab_id"),
                    "window_id": t.get("window_id", "1"),
                    "browser": t.get("browser"),
                    "category": t.get("category"),
                    "sub_type": t.get("sub_type"),
                    "label": label,
                    "matched_domain": t.get("matched_domain"),
                    "status": "OPEN",
                    "display_text": f"{label} – OPEN",
                })
            if self._risk_incident_active:
                for ct in self._closed_incident_tabs.values():
                    label = ct.get("display_label") or get_sensitive_page_display_label(
                        ct.get("category", ""), ct.get("sub_type"), ct.get("matched_domain")
                    )
                    sensitive_pages_summary.append({
                        "tab_id": ct.get("tab_id"),
                        "window_id": ct.get("window_id", "1"),
                        "browser": ct.get("browser"),
                        "category": ct.get("category"),
                        "sub_type": ct.get("sub_type"),
                        "label": label,
                        "matched_domain": ct.get("matched_domain"),
                        "status": "CLOSED",
                        "display_text": f"{label} – CLOSED",
                    })

            # Build individual Capture Activity summary (Windows processes + browser captures + unknown state)
            capture_activity_summary: List[Dict[str, Any]] = []
            seen_recorder_names: Set[str] = set()
            for r in self._detailed_external_recorders:
                seen_recorder_names.add(r["name"])
                capture_activity_summary.append({
                    "name": r["name"],
                    "source_layer": "WINDOWS_PROCESS",
                    "classification": r["classification"],
                    "status": r["classification"],
                    "is_active": bool(r.get("is_active", True)),
                    "capture_confirmed": bool(r.get("capture_confirmed", False)),
                    "display_text": f"{r['name']} – {r['classification']}",
                })
            if self._risk_incident_active:
                for t_name, t_rec in self._tracked_incident_recorders.items():
                    if t_name not in seen_recorder_names and not t_rec.get("is_active"):
                        capture_activity_summary.append({
                            "name": t_name,
                            "source_layer": "WINDOWS_PROCESS",
                            "classification": CaptureClassification.STOPPED,
                            "status": CaptureClassification.STOPPED,
                            "is_active": False,
                            "capture_confirmed": False,
                            "display_text": f"{t_name} – {CaptureClassification.STOPPED}",
                        })

            for cap in self._browser_capture_sessions.values():
                c_status = cap.get("classification") or (
                    CaptureClassification.ACTIVE if cap.get("is_active") else CaptureClassification.STOPPED
                )
                capture_activity_summary.append({
                    "name": cap.get("name", "Browser screen sharing"),
                    "capture_id": cap.get("capture_id"),
                    "tab_id": cap.get("tab_id"),
                    "window_id": cap.get("window_id"),
                    "source_layer": "BROWSER_EXTENSION",
                    "source_type": cap.get("source_type", "SCREEN_SHARING"),
                    "classification": c_status,
                    "status": c_status,
                    "is_active": bool(cap.get("is_active")),
                    "capture_confirmed": bool(cap.get("is_active")),
                    "display_text": f"{cap.get('name', 'Browser screen sharing')} – {c_status}",
                })

            if self._windows_capture_state_unknown:
                capture_activity_summary.append({
                    "name": "Windows capture monitor",
                    "source_layer": "WINDOWS_MONITOR",
                    "classification": CaptureClassification.CAPTURE_STATUS_UNKNOWN,
                    "status": CaptureClassification.CAPTURE_STATUS_UNKNOWN,
                    "is_active": True,
                    "capture_confirmed": False,
                    "display_text": f"Windows capture monitor – {CaptureClassification.CAPTURE_STATUS_UNKNOWN}",
                })

            active_detected_capture_count = self._count_active_detected_captures_locked()
            all_sources_cleared = (
                active_tab_count == 0
                and active_detected_capture_count == 0
                and not self._windows_capture_state_unknown
            )

            # Persistent alert visibility:
            # - Active whenever a risk incident is active (`self._risk_incident_active`), OR a sensitive tab is open,
            #   OR Windows capture status is unknown during protection.
            persistent_alert_active = bool(
                self._risk_incident_active
                or active_tab_count > 0
                or self._windows_capture_state_unknown
            )
            can_dismiss = bool(all_sources_cleared)

            if self._risk_incident_active:
                panel_action_status = self.PANEL_ACTION_REQUIRED_STATUS
            elif all_sources_cleared and (self._last_resolved_risk_event is not None or self._visual_monitoring_paused):
                panel_action_status = self.PANEL_RESOLVED_STATUS
            elif active_tab_count > 0:
                panel_action_status = "Sensitive page open – StrictRecords visual screen capture is paused."
            else:
                panel_action_status = "Visual screen monitoring is active."

            if self._risk_incident_active:
                display_banner = (
                    f"{self.RISK_ALERT_HEADING} — StrictRecords visual capture: PAUSED ({cooldown_mm_ss})"
                )
                status_text = self.PANEL_ACTION_REQUIRED_STATUS
            elif self._protection_state == ProtectionState.SENSITIVE_PAGE_STILL_OPEN_PROTECTED:
                display_banner = "SENSITIVE PAGE PROTECTION ACTIVE — Sensitive page still open – visual protection remains active."
                status_text = "Sensitive page still open – visual protection remains active."
            elif self._protection_state == ProtectionState.SENSITIVE_PAGE_PROTECTION_ACTIVE:
                display_banner = f"SENSITIVE PAGE PROTECTION ACTIVE — Visual monitoring paused: {cooldown_mm_ss}"
                status_text = f"Visual monitoring paused: {cooldown_mm_ss}"
            elif self._protection_state == ProtectionState.VISUAL_MONITORING_RESUMED:
                display_banner = "VISUAL_MONITORING_RESUMED"
                status_text = "Visual screen monitoring is active."
            else:
                display_banner = "INACTIVE"
                status_text = "Visual screen monitoring is active."

            return {
                "success": True,
                "protection_active": bool(self._visual_monitoring_paused),
                "visual_monitoring_paused": bool(self._visual_monitoring_paused),
                "strict_records_visual_capture_paused": bool(self._visual_monitoring_paused),
                "strict_records_visual_capture_state": "PAUSED" if self._visual_monitoring_paused else "ACTIVE",
                "protection_state": self._protection_state,
                "capture_risk_active": bool(self._risk_incident_active),
                "persistent_alert_active": persistent_alert_active,
                "all_sources_cleared": all_sources_cleared,
                "can_dismiss": can_dismiss,
                "alert_dismissed": bool(self._alert_dismissed_after_clear and all_sources_cleared),
                "panel_header": self.PANEL_HEADER,
                "panel_status_badge": "PROTECTION ACTIVE" if (self._visual_monitoring_paused or persistent_alert_active) else "INACTIVE",
                "risk_alert_heading": self.RISK_ALERT_HEADING if self._risk_incident_active else None,
                "risk_alert_body": self.RISK_ALERT_BODY if self._risk_incident_active else None,
                "panel_action_status": panel_action_status,
                "current_category": self._current_category if self._visual_monitoring_paused else None,
                "current_sub_type": self._current_sub_type if self._visual_monitoring_paused else None,
                "matched_domain": self._current_matched_domain if self._visual_monitoring_paused else None,
                "cooldown_total_seconds": int(self._cooldown_duration),
                "cooldown_remaining_seconds": round(remaining_sec, 1),
                "cooldown_formatted": cooldown_mm_ss,
                "display_banner": display_banner,
                "status_text": status_text,
                "alert_title": (
                    self.RISK_ALERT_HEADING
                    if self._risk_incident_active
                    else (self._current_alert_title if self._visual_monitoring_paused else None)
                ),
                "alert_message": (
                    self.RISK_ALERT_BODY
                    if self._risk_incident_active
                    else (self._current_alert_message if self._visual_monitoring_paused else None)
                ),
                "active_sensitive_tab_count": active_tab_count,
                "active_sensitive_tabs": [
                    {
                        "browser": t.get("browser"),
                        "tab_id": t.get("tab_id"),
                        "window_id": t.get("window_id", "1"),
                        "category": t.get("category"),
                        "sub_type": t.get("sub_type"),
                        "display_label": t.get("display_label") or get_sensitive_page_display_label(
                            t.get("category", ""), t.get("sub_type"), t.get("matched_domain")
                        ),
                        "matched_domain": t.get("matched_domain"),
                        "matched_origin": t.get("matched_origin") or t.get("matched_domain"),
                        "detected_at": t.get("detected_at"),
                        "status": t.get("status", "OPEN"),
                    }
                    for t in active_tabs_list
                ],
                "sensitive_pages_summary": sensitive_pages_summary,
                "active_detected_capture_count": active_detected_capture_count,
                "capture_activity_summary": capture_activity_summary,
                "external_recorder_detected": len(self._detected_external_recorders) > 0,
                "external_recorders": list(self._detected_external_recorders),
                "external_recorders_detailed": [dict(r) for r in self._detailed_external_recorders],
                "installed_recorders_only": [dict(r) for r in self._installed_only_recorders],
                "external_recorder_warning": self._external_recorder_warning,
                "browser_captures": [dict(c) for c in self._browser_capture_sessions.values()],
                "windows_monitor_connected": self._windows_monitor_connected,
                "windows_capture_state_unknown": self._windows_capture_state_unknown,
                "sensitive_page_capture_risk_event": (
                    dict(self._active_risk_event)
                    if self._active_risk_event is not None
                    else (dict(self._last_resolved_risk_event) if self._last_resolved_risk_event is not None else None)
                ),
                "non_visual_monitoring_intact": {
                    "session_timer_running": True,
                    "windows_webcam_proctoring": True,
                    "ios_front_camera_proctoring": True,
                    "android_app_usage_monitoring": True,
                },
                "custom_domains": dict(self._custom_domains),
                "recent_events": list(self._event_history[:20]),
                "recent_notifications": list(self._notifications[:10]),
                "last_activated_at": self._last_activated_wall,
                "last_resumed_at": self._last_resumed_wall,
            }

