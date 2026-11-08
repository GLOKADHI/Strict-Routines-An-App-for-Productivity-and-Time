"""
Comprehensive tests for StrictRecords Sensitive Page Protection,
Screen Capture Privacy Shield, and 5-Minute Visual Monitoring Cooldown.
"""

import json
import pytest
from PIL import Image

from windows.strict_sensitive_page_protection import (
    CaptureClassification,
    ProtectionState,
    SensitiveCategory,
    SensitivePageProtectionManager,
    classify_sensitive_page,
    sanitize_domain_or_origin,
)
from windows.strict_screen_monitor import StrictScreenMonitor


class FakeClock:
    def __init__(self, initial: float = 1000.0):
        self.now = float(initial)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += float(seconds)
        return self.now


@pytest.fixture(autouse=True)
def reset_spp_singleton():
    SensitivePageProtectionManager.reset_instance()
    yield
    SensitivePageProtectionManager.reset_instance()


# ==============================================================================
# 1. All 8 Required Page Classification Scenarios (Section 11)
# ==============================================================================
class TestSensitivePageClassification:
    def test_1_bank_login_page(self):
        url = "https://netbanking.hdfcbank.com/netbanking/?session_token=sec_987654321&acct=12345"
        res = classify_sensitive_page(url)
        assert res["is_sensitive"] is True
        assert res["category"] == SensitiveCategory.BANKING_FINANCIAL
        assert res["matched_domain"] == "netbanking.hdfcbank.com"
        assert "sec_987654321" not in json.dumps(res)

    def test_2_banking_dashboard(self):
        url = "https://secure.chase.com/web/auth/dashboard#/dashboard/overviewAccounts/overview/index"
        res = classify_sensitive_page(url)
        assert res["is_sensitive"] is True
        assert res["category"] == SensitiveCategory.BANKING_FINANCIAL
        assert res["matched_domain"] == "secure.chase.com"

    def test_3_crypto_wallet_page(self):
        url_metamask = "https://portfolio.metamask.io/?account=0xAbC1234567890Def"
        res1 = classify_sensitive_page(url_metamask)
        assert res1["is_sensitive"] is True
        assert res1["category"] == SensitiveCategory.CRYPTO_WALLET
        assert res1["matched_domain"] == "portfolio.metamask.io"
        assert "0xAbC1234567890Def" not in json.dumps(res1)

        url_coinbase = "https://www.coinbase.com/assets"
        res2 = classify_sensitive_page(url_coinbase)
        assert res2["is_sensitive"] is True
        assert res2["category"] == SensitiveCategory.CRYPTO_WALLET
        assert res2["matched_domain"] == "www.coinbase.com"

    def test_4_gmail_inbox(self):
        url = "https://mail.google.com/mail/u/0/#inbox/FMfcgzGtwVmkPqRsTuVwXyZ"
        res = classify_sensitive_page(url)
        assert res["is_sensitive"] is True
        assert res["category"] == SensitiveCategory.EMAIL_PERSONAL
        assert res["sub_type"] == "GMAIL"
        assert res["matched_domain"] == "mail.google.com"
        assert "FMfcgzGtwVmkPqRsTuVwXyZ" not in json.dumps(res)

    def test_5_outlook_inbox(self):
        url = "https://outlook.live.com/mail/0/inbox/id/AAQkADk5YzI1"
        res = classify_sensitive_page(url)
        assert res["is_sensitive"] is True
        assert res["category"] == SensitiveCategory.EMAIL_PERSONAL
        assert res["sub_type"] == "OUTLOOK"
        assert res["matched_domain"] == "outlook.live.com"
        assert "AAQkADk5YzI1" not in json.dumps(res)

    def test_6_stripe_checkout_opened_from_suno_subscription(self):
        # Top-level redirect to checkout.stripe.com from Suno subscription
        stripe_url = "https://checkout.stripe.com/c/pay/cs_live_a1B2c3D4e5F6g7H8#fidkdWxOYHwnPyd1blpxYHZxWjA0"
        res = classify_sensitive_page(stripe_url)
        assert res["is_sensitive"] is True
        assert res["category"] == SensitiveCategory.PAYMENT_CHECKOUT
        assert res["sub_type"] == "STRIPE_CHECKOUT"
        assert res["matched_domain"] == "checkout.stripe.com"
        assert "cs_live_" not in json.dumps(res)

        # Also test Suno page embedding a checkout.stripe.com iframe origin
        embedded_res = classify_sensitive_page(
            url_or_origin="https://suno.com/account/billing",
            frame_origins=["https://checkout.stripe.com"],
        )
        assert embedded_res["is_sensitive"] is True
        assert embedded_res["category"] == SensitiveCategory.PAYMENT_CHECKOUT
        assert embedded_res["matched_domain"] == "checkout.stripe.com"
        assert embedded_res["detection_source"] == "EMBEDDED_FRAME_ORIGIN"

    def test_7_razorpay_checkout(self):
        url = "https://checkout.razorpay.com/v1/checkout.html?key_id=rzp_live_SecretKey999&order_id=order_12345"
        res = classify_sensitive_page(url)
        assert res["is_sensitive"] is True
        assert res["category"] == SensitiveCategory.PAYMENT_CHECKOUT
        assert res["sub_type"] == "RAZORPAY_CHECKOUT"
        assert res["matched_domain"] == "checkout.razorpay.com"
        assert "rzp_live_SecretKey999" not in json.dumps(res)

    def test_8_normal_non_sensitive_websites(self):
        for normal_url in [
            "https://suno.com/create",
            "https://leetcode.com/problems/two-sum/",
            "https://docs.python.org/3/library/unittest.html",
            "https://github.com/openai/openai-python",
            "https://en.wikipedia.org/wiki/Operating_system",
        ]:
            res = classify_sensitive_page(normal_url)
            assert res["is_sensitive"] is False, f"Expected non-sensitive for {normal_url}"
            assert res["category"] == SensitiveCategory.NON_SENSITIVE

    def test_user_configurable_custom_sensitive_domain(self):
        clock = FakeClock()
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)

        # Before adding custom domain, mycoopcredit.org is non-sensitive
        res_before = classify_sensitive_page("https://portal.mycoopcredit.org/overview")
        assert res_before["is_sensitive"] is False

        # Add custom domain via Settings config
        mgr.update_config(add_domains={"mycoopcredit.org": "BANKING_FINANCIAL"})
        st = mgr.classify_and_handle_navigation(
            url_or_origin="https://portal.mycoopcredit.org/overview?acct=999",
            tab_id=42,
            browser="chrome",
        )
        assert st["protection_active"] is True
        assert st["current_category"] == SensitiveCategory.BANKING_FINANCIAL
        assert st["matched_domain"] == "portal.mycoopcredit.org"


# ==============================================================================
# 2. Immediate Alert, 5-Minute Cooldown, Case A vs Case B, & Redirect Flows
# ==============================================================================
class TestCooldownLifecycleAndRedirects:
    def test_5_minute_cooldown_and_case_a_safe_resumption(self):
        clock = FakeClock(initial=5000.0)
        notifications = []
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: [])
        mgr.set_notification_callback(lambda title, msg, payload: notifications.append((title, msg, payload)))

        # User opens Stripe Checkout from Suno subscription in tab 10
        st = mgr.classify_and_handle_navigation(
            url_or_origin="https://checkout.stripe.com/c/pay/cs_live_secrettoken123",
            tab_id=10,
            browser="chrome",
            session_id="sess_focus_01",
        )
        assert st["protection_active"] is True
        assert st["visual_monitoring_paused"] is True
        assert st["protection_state"] == ProtectionState.SENSITIVE_PAGE_PROTECTION_ACTIVE
        assert st["cooldown_remaining_seconds"] == 300.0
        assert st["cooldown_formatted"] == "05:00"
        assert "SENSITIVE PAGE PROTECTION ACTIVE" in st["display_banner"]
        assert "Visual monitoring paused: 05:00" in st["display_banner"]
        assert len(notifications) == 1
        assert "Payment / Checkout Page Detected" in notifications[0][0]
        assert "StrictRecords visual screen monitoring has been paused temporarily" in notifications[0][1]

        # Minor DOM/hash update on the same tab after 10 seconds does NOT spam duplicate notification
        # and does NOT reset the 5-minute timer
        clock.advance(10.0)
        st_dup = mgr.classify_and_handle_navigation(
            url_or_origin="https://checkout.stripe.com/c/pay/cs_live_secrettoken123#step2",
            tab_id=10,
            browser="chrome",
            session_id="sess_focus_01",
        )
        assert len(notifications) == 1
        assert st_dup["cooldown_remaining_seconds"] == 290.0
        assert st_dup["cooldown_formatted"] == "04:50"

        # User completes payment at t=60s and Stripe redirects tab 10 back to Suno (non-sensitive)
        clock.advance(50.0)  # total elapsed = 60s, remaining = 240s (04:00)
        st_redirect_back = mgr.classify_and_handle_navigation(
            url_or_origin="https://suno.com/create",
            tab_id=10,
            browser="chrome",
            session_id="sess_focus_01",
        )
        # Tab 10 is no longer in active_sensitive_tabs, but 4 minutes of cooldown remain!
        assert st_redirect_back["active_sensitive_tab_count"] == 0
        assert st_redirect_back["protection_active"] is True
        assert st_redirect_back["visual_monitoring_paused"] is True
        assert st_redirect_back["cooldown_remaining_seconds"] == 240.0
        assert st_redirect_back["cooldown_formatted"] == "04:00"

        # Advance remaining 240 seconds -> 5-minute cooldown expires with 0 active sensitive tabs (Case A)
        clock.advance(240.0)
        st_resumed = mgr.get_status()
        assert st_resumed["protection_active"] is False
        assert st_resumed["visual_monitoring_paused"] is False
        assert st_resumed["protection_state"] == ProtectionState.VISUAL_MONITORING_RESUMED
        assert st_resumed["cooldown_remaining_seconds"] == 0.0
        assert st_resumed["cooldown_formatted"] == "00:00"

    def test_case_b_sensitive_page_still_open_when_5_minutes_expire(self):
        clock = FakeClock(initial=10000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: [])

        # User opens Gmail in tab 22
        st = mgr.classify_and_handle_navigation(
            url_or_origin="https://mail.google.com/mail/u/0/#inbox",
            tab_id=22,
            browser="edge",
        )
        assert st["protection_active"] is True
        assert st["current_category"] == SensitiveCategory.EMAIL_PERSONAL
        assert "Sensitive Email Account Detected – Visual Monitoring Paused." in st["alert_message"]

        # Advance 305 seconds (> 5 minutes) while Gmail tab 22 is STILL OPEN
        clock.advance(305.0)
        st_after_5m = mgr.get_status()

        # Case B: Visual capture MUST remain paused because the sensitive tab is still open!
        assert st_after_5m["protection_active"] is True
        assert st_after_5m["visual_monitoring_paused"] is True
        assert st_after_5m["protection_state"] == ProtectionState.SENSITIVE_PAGE_STILL_OPEN_PROTECTED
        assert st_after_5m["status_text"] == "Sensitive page still open – visual protection remains active."
        assert mgr.is_visual_monitoring_paused() is True

        # Now user closes Gmail tab 22 (or navigates to a non-sensitive site)
        st_closed = mgr.handle_sensitive_page_closed_or_navigated(tab_id=22, browser="edge")
        assert st_closed["protection_active"] is False
        assert st_closed["visual_monitoring_paused"] is False
        assert st_closed["protection_state"] == ProtectionState.VISUAL_MONITORING_RESUMED
        assert mgr.is_visual_monitoring_paused() is False


# ==============================================================================
# 3. Actual Desktop Screen Capture Suppression & Non-Visual Monitoring Continuity
# ==============================================================================
class TestCapturePipelineSuppressionAndNonVisualContinuity:
    def test_strict_screen_monitor_suppresses_imagegrab_during_cooldown_and_resumes_after(self):
        clock = FakeClock(initial=2000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: [])

        capture_calls = []

        def fake_capture():
            capture_calls.append(clock())
            return Image.new("RGB", (1920, 1080), color=(20, 30, 40))

        monitor = StrictScreenMonitor(capture=fake_capture, clock=clock)

        # 1. Normal mode: sample() calls fake_capture() and succeeds
        assert monitor.sample() is True
        assert len(capture_calls) == 1

        # 2. Trigger Sensitive Page Protection (e.g. Banking dashboard opened)
        mgr.classify_and_handle_navigation(
            url_or_origin="https://infinity.icicibank.com/corp/AuthenticationController?secret=xyz",
            tab_id=5,
            browser="chrome",
        )
        assert mgr.is_visual_monitoring_paused() is True

        # 3. Call monitor.sample() multiple times during the 5-minute cooldown:
        #    fake_capture() MUST NOT be called!
        for _ in range(5):
            clock.advance(15.0)
            sampled = monitor.sample()
            assert sampled is False

        assert len(capture_calls) == 1, "ImageGrab.grab must NEVER be called while Sensitive Page Protection is active!"
        mon_status = monitor.status()
        assert mon_status["screen_capture_active"] is False
        assert mon_status["privacy_shield_active"] is True
        assert mon_status["visual_monitoring_paused"] is True
        assert mon_status["screen_monitoring_status"] == ProtectionState.SENSITIVE_PAGE_PROTECTION_ACTIVE
        assert mon_status["suppressed_captures"] == 5

        # 4. Close the banking tab and advance past the 5-minute cooldown
        mgr.handle_sensitive_page_closed_or_navigated(tab_id=5, browser="chrome")
        clock.advance(300.0)
        assert mgr.is_visual_monitoring_paused() is False

        # 5. Next sample() resumes desktop capture normally
        assert monitor.sample() is True
        assert len(capture_calls) == 2
        resumed_status = monitor.status()
        assert resumed_status["screen_capture_active"] is True or resumed_status["privacy_shield_active"] is False


# ==============================================================================
# 4. External Screen-Recording Application Detection & Zero URL/Token Leakage
# ==============================================================================
class TestExternalRecorderDetectionAndPrivacySanitization:
    def test_external_screen_recorder_detection_warns_without_killing_process(self):
        clock = FakeClock(initial=3000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        # Simulate OBS Studio and ShareX running on Windows
        mgr.set_process_scanner(lambda: ["explorer.exe", "obs64.exe", "ShareX.exe", "chrome.exe"])

        st = mgr.classify_and_handle_navigation(
            url_or_origin="https://checkout.stripe.com/c/pay/cs_live_abc987",
            tab_id=77,
            browser="chrome",
        )
        assert st["external_recorder_detected"] is True
        assert "OBS Studio" in st["external_recorders"]
        assert "ShareX" in st["external_recorders"]
        assert "EXTERNAL SCREEN RECORDER DETECTED" in st["external_recorder_warning"]
        assert "Please close the external recording application before handling sensitive account or payment information." in st["external_recorder_warning"]
        # Non-visual monitoring remains intact
        assert st["non_visual_monitoring_intact"]["session_timer_running"] is True
        assert st["non_visual_monitoring_intact"]["windows_webcam_proctoring"] is True
        assert st["non_visual_monitoring_intact"]["ios_front_camera_proctoring"] is True
        assert st["non_visual_monitoring_intact"]["android_app_usage_monitoring"] is True

    def test_no_sensitive_query_tokens_or_full_urls_are_stored_or_logged(self):
        clock = FakeClock(initial=4000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: [])

        secret_url = (
            "https://checkout.stripe.com/c/pay/cs_live_SECRET_SESSION_998877"
            "?client_secret=pi_12345_secret_67890&card_last4=4242&otp=884422#seed=twelve_words_here"
        )
        st = mgr.classify_and_handle_navigation(
            url_or_origin=secret_url,
            tab_id=99,
            browser="chrome",
            session_id="sess_privacy_test",
        )
        serialized = json.dumps(st)
        assert st["matched_domain"] == "checkout.stripe.com"
        for forbidden in [
            "cs_live_SECRET_SESSION_998877",
            "pi_12345_secret_67890",
            "4242",
            "884422",
            "twelve_words_here",
            "/c/pay/",
        ]:
            assert forbidden not in serialized, f"Sensitive token '{forbidden}' must never appear in status/logs!"


# ==============================================================================
# 5. Persistent Sensitive-Page Screen Recording & Screen-Sharing Alert (Scenarios A–K)
# ==============================================================================
class TestPersistentSensitivePageCaptureRiskScenariosAtoK:
    def test_scenario_a_stripe_checkout_with_obs_recording_triggers_immediate_persistent_risk_alert(self):
        """
        Scenario A: Open a Stripe Checkout page while OBS is actively recording/running.
        Expected:
        - Immediate persistent warning (`SENSITIVE_PAGE_CAPTURE_RISK`).
        - StrictRecords visual capture pauses immediately.
        - Dismissal is disabled while the risk is active.
        """
        clock = FakeClock(initial=5000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: ["obs64.exe"])

        st = mgr.classify_and_handle_navigation(
            url_or_origin="https://checkout.stripe.com/c/pay/cs_live_suno_sub",
            tab_id=101,
            window_id=1,
            browser="chrome",
        )
        assert st["capture_risk_active"] is True
        assert st["persistent_alert_active"] is True
        assert st["visual_monitoring_paused"] is True
        assert st["strict_records_visual_capture_state"] == "PAUSED"
        assert st["risk_alert_heading"] == "SENSITIVE PAGE + SCREEN RECORDING DETECTED"
        assert "Close all active screen-recording and screen-sharing applications before continuing." in st["risk_alert_body"]
        assert st["can_dismiss"] is False

        # Verify event structure
        risk_ev = st["sensitive_page_capture_risk_event"]
        assert risk_ev is not None
        assert risk_ev["event_type"] == "SENSITIVE_PAGE_CAPTURE_RISK"
        assert risk_ev["resolved"] is False
        assert "101" in risk_ev["browser_tab_ids"]
        assert "PAYMENT_CHECKOUT" in risk_ev["sensitive_categories"]
        assert "checkout.stripe.com" in risk_ev["matched_origins"]
        assert "OBS Studio" in risk_ev["detected_capture_sources"]
        assert risk_ev["capture_classification"]["OBS Studio"] == "PROCESS RUNNING – CAPTURE STATUS UNKNOWN"

        # Attempting to dismiss while risk is active must be rejected
        dismiss_attempt = mgr.dismiss_persistent_alert(admin_override=False)
        assert dismiss_attempt["dismissed"] is False
        assert dismiss_attempt["persistent_alert_active"] is True

    def test_scenario_b_gmail_with_screen_recorder_triggers_persistent_warning(self):
        """
        Scenario B: Open Gmail while a screen recorder (e.g. Xbox Game Bar BroadcastDVR) is active.
        Expected:
        - Persistent warning appears and classifies confirmed capture helper as CAPTURE SESSION CONFIRMED ACTIVE.
        """
        clock = FakeClock(initial=6000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: ["broadcastdvr.exe"])

        st = mgr.classify_and_handle_navigation(
            url_or_origin="https://mail.google.com/mail/u/0/#inbox",
            tab_id=102,
            window_id=1,
            browser="chrome",
        )
        assert st["capture_risk_active"] is True
        assert st["persistent_alert_active"] is True
        assert any(p["label"] == "Gmail" and p["status"] == "OPEN" for p in st["sensitive_pages_summary"])
        assert any(
            c["name"] == "Xbox Game Bar (BroadcastDVR)"
            and c["classification"] == "CAPTURE SESSION CONFIRMED ACTIVE"
            for c in st["capture_activity_summary"]
        )

    def test_scenario_c_multiple_sensitive_tabs_partial_close_keeps_warning_active(self):
        """
        Scenario C: Open 3 sensitive pages (Stripe Checkout, Gmail, HDFC Bank) while OBS is running,
        then close only 2 of the 3 sensitive tabs.
        Expected:
        - Warning remains active until ALL 3 sensitive tabs AND capture are closed.
        """
        clock = FakeClock(initial=7000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: ["obs64.exe"])

        mgr.classify_and_handle_navigation("https://checkout.stripe.com/c/pay/1", tab_id=201, browser="chrome")
        mgr.classify_and_handle_navigation("https://mail.google.com/mail/u/0/#inbox", tab_id=202, browser="chrome")
        st3 = mgr.classify_and_handle_navigation("https://netbanking.hdfcbank.com/netbanking/", tab_id=203, browser="chrome")
        assert st3["active_sensitive_tab_count"] == 3
        assert st3["capture_risk_active"] is True

        # Close tab 201 and tab 202, leaving tab 203 (HDFC Bank) open
        mgr.handle_sensitive_page_closed_or_navigated(tab_id=201, browser="chrome")
        st_partial = mgr.handle_sensitive_page_closed_or_navigated(tab_id=202, browser="chrome")

        assert st_partial["active_sensitive_tab_count"] == 1
        assert st_partial["capture_risk_active"] is True
        assert st_partial["persistent_alert_active"] is True
        assert st_partial["can_dismiss"] is False
        # Closed tabs from the incident are shown as CLOSED alongside the remaining OPEN tab
        statuses = {p["label"]: p["status"] for p in st_partial["sensitive_pages_summary"]}
        assert statuses.get("Stripe Checkout") == "CLOSED"
        assert statuses.get("Gmail") == "CLOSED"
        assert statuses.get("HDFC Bank") == "OPEN"

    def test_scenario_d_close_sensitive_tabs_leave_browser_capture_active_keeps_warning_active(self):
        """
        Scenario D: Close all sensitive tabs, but leave an actual browser capture active.
        Expected:
        - The warning remains active until the capture stops or is explicitly verified closed.
        """
        clock = FakeClock(initial=8000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: [])

        # Open Stripe Checkout and start browser screen sharing
        mgr.classify_and_handle_navigation("https://checkout.stripe.com/c/pay/1", tab_id=301, browser="chrome")
        st_cap = mgr.handle_browser_capture_event(
            capture_id="display_1",
            source_type="SCREEN_SHARING",
            is_active=True,
            tab_id=301,
            browser="chrome",
            display_name="Browser screen sharing",
        )
        assert st_cap["capture_risk_active"] is True
        assert st_cap["persistent_alert_active"] is True

        # Now close the sensitive tab while browser screen sharing is still active
        st_after_tab_close = mgr.handle_sensitive_page_closed_or_navigated(tab_id=301, browser="chrome")
        assert st_after_tab_close["active_sensitive_tab_count"] == 0
        assert st_after_tab_close["active_detected_capture_count"] == 1
        assert st_after_tab_close["capture_risk_active"] is True
        assert st_after_tab_close["persistent_alert_active"] is True
        assert st_after_tab_close["can_dismiss"] is False

    def test_scenario_e_stop_one_capture_leave_another_active_keeps_warning_active(self):
        """
        Scenario E: Stop browser capture, but leave OBS running/recording with a sensitive page open.
        Expected:
        - The warning remains active; browser capture shows STOPPED while OBS Studio still shows active.
        """
        clock = FakeClock(initial=9000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: ["obs64.exe"])

        mgr.classify_and_handle_navigation("https://mail.google.com/mail/u/0/#inbox", tab_id=401, browser="chrome")
        mgr.handle_browser_capture_event(
            capture_id="tab_cap_401",
            source_type="TAB_CAPTURE",
            is_active=True,
            tab_id=401,
            browser="chrome",
            display_name="Browser tab capture",
        )
        # Stop browser tab capture while OBS Studio is still running and Gmail is still open
        st = mgr.handle_browser_capture_event(
            capture_id="tab_cap_401",
            source_type="TAB_CAPTURE",
            is_active=False,
            tab_id=401,
            browser="chrome",
            display_name="Browser tab capture",
        )
        assert st["capture_risk_active"] is True
        assert st["persistent_alert_active"] is True
        assert st["can_dismiss"] is False
        cap_map = {c["name"]: c["status"] for c in st["capture_activity_summary"]}
        assert cap_map.get("Browser tab capture") == "STOPPED"
        assert cap_map.get("OBS Studio") == "PROCESS RUNNING – CAPTURE STATUS UNKNOWN"

    def test_scenario_f_full_resolution_clears_warning_and_marks_event_resolved(self):
        """
        Scenario F: Close every sensitive page and stop every detected capture.
        Expected:
        - Warning clears automatically, status message updates to
          'All sensitive pages and detected captures are closed.',
          and event is marked RESOLVED.
        - Dismiss button becomes enabled.
        - StrictRecords visual capture remains paused until the 5-minute cooldown finishes.
        """
        clock = FakeClock(initial=10000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        active_procs = ["obs64.exe"]
        mgr.set_process_scanner(lambda: list(active_procs))

        mgr.classify_and_handle_navigation("https://checkout.stripe.com/c/pay/1", tab_id=501, browser="chrome")
        mgr.handle_browser_capture_event(capture_id="cap_501", source_type="SCREEN_SHARING", is_active=True, browser="chrome")
        assert mgr.get_status()["capture_risk_active"] is True

        # 1. Close the sensitive tab
        mgr.handle_sensitive_page_closed_or_navigated(tab_id=501, browser="chrome")
        # 2. Stop browser screen sharing
        mgr.handle_browser_capture_event(capture_id="cap_501", source_type="SCREEN_SHARING", is_active=False, browser="chrome")
        # 3. Close OBS Studio
        active_procs.clear()
        mgr.detect_external_screen_recorders_detailed(force_refresh=True)

        st_resolved = mgr.get_status()
        assert st_resolved["active_sensitive_tab_count"] == 0
        assert st_resolved["active_detected_capture_count"] == 0
        assert st_resolved["capture_risk_active"] is False
        assert st_resolved["persistent_alert_active"] is False
        assert st_resolved["all_sources_cleared"] is True
        assert st_resolved["can_dismiss"] is True
        assert st_resolved["panel_action_status"] == "All sensitive pages and detected captures are closed."
        assert st_resolved["sensitive_page_capture_risk_event"]["resolved"] is True
        assert st_resolved["sensitive_page_capture_risk_event"]["status"] == "RESOLVED"

        # Visual capture is still in its 5-minute cooldown
        assert st_resolved["visual_monitoring_paused"] is True

        # Dismiss the resolved notice
        st_dismissed = mgr.dismiss_persistent_alert(admin_override=False)
        assert st_dismissed["dismissed"] is True
        assert st_dismissed["alert_dismissed"] is True

        # Advance 5 minutes (300s) -> visual monitoring resumes
        clock.advance(300.0)
        st_final = mgr.get_status()
        assert st_final["visual_monitoring_paused"] is False
        assert st_final["protection_state"] == ProtectionState.VISUAL_MONITORING_RESUMED

    def test_scenario_g_and_4_state_recorder_distinction_installed_only_does_not_false_alert(self):
        """
        Scenario G + Section 2 Layer B:
        - Open a normal non-sensitive website with no capture activity -> no false warning.
        - Even if OBS Studio is INSTALLED on disk (`obs64.exe:INSTALLED`, not running), opening a
          page must NOT treat an installed-only recorder as active capture.
        """
        clock = FakeClock(initial=11000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: ["obs64.exe:INSTALLED"])

        # Normal website -> no protection, no risk
        st_normal = mgr.classify_and_handle_navigation("https://en.wikipedia.org/wiki/Main_Page", tab_id=601, browser="chrome")
        assert st_normal["protection_active"] is False
        assert st_normal["capture_risk_active"] is False
        assert st_normal["persistent_alert_active"] is False
        assert st_normal["external_recorder_detected"] is False
        assert len(st_normal["installed_recorders_only"]) == 0 or st_normal["active_detected_capture_count"] == 0

        # Sensitive website when OBS is only INSTALLED (not running) -> visual capture pauses for 5m,
        # but `external_recorder_detected` and `capture_risk_active` are False!
        st_sens = mgr.classify_and_handle_navigation("https://checkout.stripe.com/c/pay/1", tab_id=602, browser="chrome")
        assert st_sens["protection_active"] is True
        assert st_sens["external_recorder_detected"] is False
        assert st_sens["capture_risk_active"] is False
        assert len(st_sens["installed_recorders_only"]) == 1
        assert st_sens["installed_recorders_only"][0]["classification"] == CaptureClassification.INSTALLED

    def test_scenarios_h_i_j_k_popup_reopen_service_worker_restart_windows_disconnect_and_non_visual_continuity(self):
        """
        Scenarios H, I, J, K:
        - H: Closing/reopening popup (repeated `get_status()` calls) preserves persistent alert state.
        - I: Restarting the extension service worker reconstructs state via `reconcile_extension_state()`.
        - J: Disconnecting the Windows monitor while a sensitive page is open sets `CAPTURE STATUS UNKNOWN`
             and keeps the persistent alert active.
        - K: StrictRecords Windows webcam, iOS camera, Android app-usage monitoring, and Strict Session timer
             remain unaffected while visual screen capture is paused.
        """
        clock = FakeClock(initial=12000.0)
        mgr = SensitivePageProtectionManager.get_instance()
        mgr.set_clock(clock)
        mgr.set_process_scanner(lambda: ["obs64.exe"])

        # Scenario I: Extension service worker restarts and reconciles open tabs + browser captures
        st_reconciled = mgr.reconcile_extension_state(
            active_tabs=[
                {
                    "tab_id": "701",
                    "window_id": "2",
                    "url": "https://checkout.stripe.com/c/pay/reconciled_test",
                },
                {
                    "tab_id": "702",
                    "window_id": "2",
                    "url": "https://mail.google.com/mail/u/0/#inbox",
                },
            ],
            browser_captures=[
                {
                    "capture_id": "tabCapture_701",
                    "source_type": "TAB_CAPTURE",
                    "is_active": True,
                    "tab_id": "701",
                }
            ],
            browser="chrome",
        )
        assert st_reconciled["active_sensitive_tab_count"] == 2
        assert st_reconciled["capture_risk_active"] is True
        assert st_reconciled["persistent_alert_active"] is True

        # Scenario H: Closing and reopening popup (fetching status repeatedly) keeps alert active
        clock.advance(10.0)
        st_popup_reopen = mgr.get_status()
        assert st_popup_reopen["persistent_alert_active"] is True
        assert st_popup_reopen["capture_risk_active"] is True
        assert st_popup_reopen["can_dismiss"] is False

        # Scenario J: Windows monitor disconnects while sensitive page is open -> CAPTURE STATUS UNKNOWN
        st_disconnected = mgr.set_windows_monitor_connection_state(False)
        assert st_disconnected["windows_monitor_connected"] is False
        assert st_disconnected["windows_capture_state_unknown"] is True
        assert st_disconnected["persistent_alert_active"] is True
        assert st_disconnected["can_dismiss"] is False
        assert any(
            c["classification"] == CaptureClassification.CAPTURE_STATUS_UNKNOWN
            for c in st_disconnected["capture_activity_summary"]
        )

        # Scenario K: Non-visual monitoring remains completely intact
        nv = st_disconnected["non_visual_monitoring_intact"]
        assert nv["session_timer_running"] is True
        assert nv["windows_webcam_proctoring"] is True
        assert nv["ios_front_camera_proctoring"] is True
        assert nv["android_app_usage_monitoring"] is True

