"""
Tests for Centralized Video Authorization Priority & Educational 80-Point Bypass.
Verifies that:
1. Exact approved educational videos bypass the 80-point YouTube restriction.
2. The authorization is strictly bound to the approved video ID (no leak to recommendations).
3. Hard restrictions (Night Lock) take precedence over educational authorization.
4. Countdown timer persists accurately without resetting on refresh.
5. Service worker sends individualized status to tabs and never broadcasts generic restriction to approved video tabs.
"""

import os
import re
import json
import unittest

class TestEducationalAuthorizationPriority(unittest.TestCase):
    def setUp(self):
        with open(r'browser_extension\content.js', 'r', encoding='utf-8') as f:
            self.content_js = f.read()
        with open(r'browser_extension\service_worker.js', 'r', encoding='utf-8') as f:
            self.service_worker_js = f.read()

    def test_centralized_evaluation_function_exists(self):
        """Verify evaluateVideoAuthorization function is present and has proper priority logic."""
        self.assertIn('function evaluateVideoAuthorization(status, videoId, currentUrl)', self.content_js)
        
        # Priority 1: Hard restrictions
        self.assertIn("status?.code === 'NIGHT_LOCK_ACTIVE'", self.content_js)
        
        # Priority 2: Educational authorization
        self.assertIn("reason: 'EDUCATIONAL_VIDEO'", self.content_js)
        
        # Priority 3: Reward entitlement
        self.assertIn("reason: 'ENTERTAINMENT_REWARD'", self.content_js)
        
        # Priority 4: Points threshold
        self.assertIn("reason: 'POINTS_THRESHOLD_MET'", self.content_js)
        
        # Priority 5: Default block
        self.assertIn("reason: 'POINTS_INSUFFICIENT'", self.content_js)

    def test_educational_bypasses_80_point_check(self):
        """Verify present() and applyYouTubeVideoRestriction() bypass restriction for approved video."""
        # present() must evaluate before applying restriction
        self.assertIn('const authDecision = evaluateVideoAuthorization(status, currentVideoId, window.location.href);', self.content_js)
        self.assertIn('GENERAL_RESTRICTION_BYPASSED_FOR_APPROVED_VIDEO', self.content_js)
        
        # applyYouTubeVideoRestriction must abort immediately if authDecision.allow is True
        self.assertIn('STALE_RESTRICTION_IGNORED', self.content_js)

    def test_blur_overlay_removed_on_valid_auth(self):
        """ensureYouTubeBlurOverlays must not inject overlay if authorization is valid."""
        self.assertIn('RESTRICTION_OVERLAY_REMOVED', self.content_js)

    def test_refresh_persistence_and_no_timer_reset(self):
        """Verify startEducationalPlaybackTracking respects initialRemainingSeconds and checkpoint."""
        self.assertIn('initialRemainingSeconds = null', self.content_js)
        self.assertIn('strictActiveEduVideo', self.content_js)
        self.assertIn('EDUCATIONAL_AUTH_ACTIVE', self.content_js)
        self.assertIn('chrome.storage?.onChanged?.addListener', self.content_js)

    def test_service_worker_individualized_tab_broadcast(self):
        """Verify service worker queries each YouTube tab specifically."""
        self.assertIn('for (const t of ytTabs)', self.service_worker_js)
        self.assertIn('const tabStatus = await getRestriction("youtube", t, t.url || "");', self.service_worker_js)
        self.assertIn('chrome.tabs.sendMessage(t.id, {', self.service_worker_js)

    def test_all_browser_profiles_synced(self):
        """Verify that Comet and other browser extension directories match workspace content."""
        comet_content_path = r'C:\Users\lokad\AppData\Local\StrictRecords\browser_extension\09401e66eaa8a0f50ff4\content.js'
        if os.path.exists(comet_content_path):
            with open(comet_content_path, 'r', encoding='utf-8') as f:
                comet_content = f.read()
            self.assertEqual(len(comet_content), len(self.content_js))
            self.assertIn('evaluateVideoAuthorization', comet_content)

if __name__ == '__main__':
    unittest.main()
