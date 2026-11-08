package com.strictrecords.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class YouTubeRewardTest {

    // ─────────────────────────────────────────────────────────────────────────
    // 1. Canonical Video ID Extraction
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun testExtractCanonicalVideoIdStandardWatch() {
        val url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assertEquals("dQw4w9WgXcQ", YouTubeRewardManager.extractCanonicalVideoId(url))
    }

    @Test
    fun testExtractCanonicalVideoIdWatchWithExtraParams() {
        val url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&feature=shared"
        assertEquals("dQw4w9WgXcQ", YouTubeRewardManager.extractCanonicalVideoId(url))
    }

    @Test
    fun testExtractCanonicalVideoIdShortLink() {
        val url = "https://youtu.be/dQw4w9WgXcQ"
        assertEquals("dQw4w9WgXcQ", YouTubeRewardManager.extractCanonicalVideoId(url))
    }

    @Test
    fun testExtractCanonicalVideoIdShortLinkWithTimestamp() {
        val url = "https://youtu.be/dQw4w9WgXcQ?t=15"
        assertEquals("dQw4w9WgXcQ", YouTubeRewardManager.extractCanonicalVideoId(url))
    }

    @Test
    fun testExtractCanonicalVideoIdEmbed() {
        val url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        assertEquals("dQw4w9WgXcQ", YouTubeRewardManager.extractCanonicalVideoId(url))
    }

    @Test
    fun testExtractCanonicalVideoIdShorts() {
        val url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        assertEquals("dQw4w9WgXcQ", YouTubeRewardManager.extractCanonicalVideoId(url))
    }

    @Test
    fun testExtractCanonicalVideoIdRaw11CharId() {
        val rawId = "dQw4w9WgXcQ"
        assertEquals("dQw4w9WgXcQ", YouTubeRewardManager.extractCanonicalVideoId(rawId))
    }

    @Test
    fun testExtractCanonicalVideoIdRejectsPlaylistWithoutVideo() {
        val url = "https://www.youtube.com/playlist?list=PL1234567890ABCDEF"
        assertNull(YouTubeRewardManager.extractCanonicalVideoId(url))
    }

    @Test
    fun testExtractCanonicalVideoIdRejectsChannels() {
        val url1 = "https://www.youtube.com/channel/UCuAXFkgsw1L7unroywLshAQ"
        val url2 = "https://www.youtube.com/@Veritasium"
        assertNull(YouTubeRewardManager.extractCanonicalVideoId(url1))
        assertNull(YouTubeRewardManager.extractCanonicalVideoId(url2))
    }

    @Test
    fun testExtractCanonicalVideoIdRejectsForeignDomains() {
        val url = "https://vimeo.com/12345678"
        assertNull(YouTubeRewardManager.extractCanonicalVideoId(url))
    }

    @Test
    fun testExtractCanonicalVideoIdRejectsNullAndEmpty() {
        assertNull(YouTubeRewardManager.extractCanonicalVideoId(null))
        assertNull(YouTubeRewardManager.extractCanonicalVideoId(""))
        assertNull(YouTubeRewardManager.extractCanonicalVideoId("   "))
        assertNull(YouTubeRewardManager.extractCanonicalVideoId("not_valid_id"))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 2. Viewing Allowance Math
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun testAllowanceMath5Minutes() {
        // 5m = 300s -> 300 + 60 = 360s (6 mins)
        assertEquals(1200, YouTubeRewardManager.calculateViewingAllowance(300))
    }

    @Test
    fun testAllowanceMath16Minutes() {
        // 16m = 960s -> 960 + 60 = 1020s (17 mins)
        assertEquals(1200, YouTubeRewardManager.calculateViewingAllowance(960))
    }

    @Test
    fun testAllowanceMath19Minutes() {
        // 19m = 1140s -> min(1140 + 60, 1200) = 1200s (20 mins cap)
        assertEquals(1200, YouTubeRewardManager.calculateViewingAllowance(1140))
    }

    @Test
    fun testAllowanceMath25MinutesCapsAt20() {
        // 25m = 1500s -> capped at 1200s (20 mins)
        assertEquals(1200, YouTubeRewardManager.calculateViewingAllowance(1500))
    }

    @Test
    fun testAllowanceMath60MinutesCapsAt20() {
        // 60m = 3600s -> capped at 1200s (20 mins)
        assertEquals(1200, YouTubeRewardManager.calculateViewingAllowance(3600))
    }

    @Test
    fun testAllowanceMathNullOrNegativeDefaultsTo20Minutes() {
        assertEquals(1200, YouTubeRewardManager.calculateViewingAllowance(null))
        assertEquals(1200, YouTubeRewardManager.calculateViewingAllowance(-10))
        assertEquals(1200, YouTubeRewardManager.calculateViewingAllowance(0))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 3. Timeliness Evaluation
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun testSubmissionTimelyBeforeCutoff() {
        val accepted = "2026-10-19T13:50:00+05:30"
        val scheduled = "2026-10-19T14:00:00+05:30"
        assertTrue(YouTubeRewardManager.isSubmissionTimely(accepted, scheduled))
    }

    @Test
    fun testSubmissionTimelyAtCutoffIsLate() {
        val accepted = "2026-10-19T14:00:00+05:30"
        val scheduled = "2026-10-19T14:00:00+05:30"
        assertFalse(YouTubeRewardManager.isSubmissionTimely(accepted, scheduled))
    }

    @Test
    fun testSubmissionTimelyDuringGracePeriodIsLate() {
        val accepted = "2026-10-19T14:05:00+05:30"
        val scheduled = "2026-10-19T14:00:00+05:30"
        assertFalse(YouTubeRewardManager.isSubmissionTimely(accepted, scheduled))
    }

    @Test
    fun testSubmissionTimelyNullOrMalformed() {
        assertFalse(YouTubeRewardManager.isSubmissionTimely(null, "2026-10-19T14:00:00+05:30"))
        assertFalse(YouTubeRewardManager.isSubmissionTimely("2026-10-19T13:50:00+05:30", null))
        assertFalse(YouTubeRewardManager.isSubmissionTimely("malformed", "2026-10-19T14:00:00+05:30"))
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 4. Reward JSON Parsing & Monotonic Session
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    fun testParseRewardJson() {
        val json = JSONObject().apply {
            put("reward_id", "rew_test_123")
            put("account_id", "user_lok")
            put("hourly_occurrence_id", "ACT-20261019-1400-HOURLY@v1")
            put("submission_id", "sub_test_01")
            put("validated_score", 8)
            put("is_timely", true)
            put("state", "AVAILABLE")
            put("allowed_seconds", 660)
        }

        val reward = YouTubeRewardManager.parseRewardJson(json)
        assertEquals("rew_test_123", reward.rewardId)
        assertEquals("user_lok", reward.accountId)
        assertEquals("ACT-20261019-1400-HOURLY@v1", reward.hourlyOccurrenceId)
        assertEquals(8, reward.validatedScore)
        assertTrue(reward.isTimely)
        assertEquals("AVAILABLE", reward.state)
        assertTrue(reward.isAvailable)
        assertFalse(reward.isActive)
        assertFalse(reward.isConsumed)
        assertEquals(1200, reward.allowedSeconds)
    }

    @Test
    fun testAndroidPlatformDisclosureIsHonest() {
        val disclosure = YouTubeRewardManager.ANDROID_PLATFORM_DISCLOSURE
        assertTrue("Disclosure must mention Android OS limitations", disclosure.contains("Android OS"))
        assertTrue("Disclosure must mention cannot terminate external processes", disclosure.contains("not permit") || disclosure.contains("terminate"))
        assertTrue("Disclosure must mention overlay/monitoring enforcement", disclosure.contains("enforces") || disclosure.contains("overlay"))
    }
}
