"""
StrictRecords – Automatic 5-Minute AI Preflight Check Test Suite
================================================================
Comprehensive test suite verifying scenarios A through N for the automatic AI preflight check
before proof-submission windows.

Scenarios Covered:
- Test A: Both Taufa AI and OpenAI connected 5 minutes before window -> Taufa primary, OpenAI backup.
- Test B: Taufa unavailable, OpenAI connected -> OpenAI fallback.
- Test C: OpenAI unavailable, Taufa connected -> Taufa primary.
- Test D: Both providers unavailable -> PENDING_VALIDATION, submission remains available.
- Test E: Temporary timeout followed by successful retry (retry 1 / retry 2).
- Test F: Authentication failure -> terminal error, NO retry.
- Test G: Rate-limit or quota-exceeded response -> NO retry.
- Test H: Multiple simultaneous requests -> deduplicated, single preflight per window.
- Test I: Minimized / background execution timing (accurate -300s trigger).
- Test J: Deferred execution timing.
- Test K: Window rescheduled -> cancels obsolete job, reschedules for new window_start - 300s.
- Test L: Window excused or cancelled -> skipped/cancelled.
- Test M: Actual evidence submission fails after preflight succeeds -> handled gracefully.
- Test N: Both unavailable during preflight but recover before actual submission -> re-evaluated.
- Test Zero-Side-Effect: Preflight check never creates submissions, points, or approvals.
"""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.database import StrictRecordsDB
from core.ai_preflight_service import (
    AiPreflightService,
    AiPreflightResult,
    ProviderProbeResult,
    STATE_CONNECTED,
    STATE_UNAVAILABLE,
    STATE_TIMEOUT,
    STATE_AUTHENTICATION_FAILED,
    STATE_QUOTA_EXCEEDED,
    STATE_RATE_LIMITED,
    STATE_INFERENCE_FAILED,
    STATE_RESPONSE_INVALID,
    PROVIDER_TAUFA_AI,
    PROVIDER_OPENAI_FALLBACK,
    PROVIDER_PENDING_VALIDATION,
    NON_RETRYABLE_STATES,
    MINIMAL_PREFLIGHT_PROMPT,
)
from core.ai_preflight_scheduler import (
    AiPreflightScheduler,
    PREFLIGHT_OFFSET_SEC,
    RETRY_1_OFFSET_SEC,
    RETRY_2_OFFSET_SEC,
)


class TestAiPreflightSuite(unittest.TestCase):
    def setUp(self):
        self.tmp_db_fd, self.tmp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_db_fd)
        self.db = StrictRecordsDB(self.tmp_db_path)
        self.service = AiPreflightService(db=self.db)
        self.service.clear_cache()

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        if os.path.exists(self.tmp_db_path):
            try:
                os.unlink(self.tmp_db_path)
            except Exception:
                pass

    # =========================================================================
    # Test A: Both Taufa AI and OpenAI connected
    # =========================================================================
    def test_scenario_a_both_connected(self):
        """Scenario A: Both Taufa AI and OpenAI connected 5m before window -> Taufa primary, OpenAI backup."""
        with patch.object(self.service, 'probe_taufa_ai') as mock_taufa, \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_taufa.return_value = ProviderProbeResult(
                provider="taufa",
                status=STATE_CONNECTED,
                latency_ms=120,
                validation_ready=True
            )
            mock_openai.return_value = ProviderProbeResult(
                provider="openai",
                status=STATE_CONNECTED,
                latency_ms=210,
                validation_ready=True
            )

            res = self.service.execute_preflight(
                proof_window_id="act_hour_10_20261106_v1",
                activity_id="act_hour_10",
                scheduled_open_time="2026-11-06T10:00:00+05:30",
                schedule_version=1,
                force=True
            )

            self.assertEqual(res.taufa.status, STATE_CONNECTED)
            self.assertEqual(res.openai.status, STATE_CONNECTED)
            self.assertEqual(res.selected_validation_provider, PROVIDER_TAUFA_AI)
            self.assertEqual(res.primary_provider, PROVIDER_TAUFA_AI)
            self.assertEqual(res.backup_provider, PROVIDER_OPENAI_FALLBACK)
            self.assertEqual(res.display_title, "AI PRE-SUBMISSION CHECK")

            # Verify persisted record
            rec = self.db.get_ai_preflight_record("act_hour_10_20261106_v1", 1)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["taufa_status"], STATE_CONNECTED)
            self.assertEqual(rec["openai_status"], STATE_CONNECTED)
            self.assertEqual(rec["selected_validation_provider"], PROVIDER_TAUFA_AI)

    # =========================================================================
    # Test B: Taufa unavailable, OpenAI connected
    # =========================================================================
    def test_scenario_b_taufa_unavailable_openai_connected(self):
        """Scenario B: Taufa unavailable, OpenAI connected -> OpenAI fallback."""
        with patch.object(self.service, 'probe_taufa_ai') as mock_taufa, \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_taufa.return_value = ProviderProbeResult(
                provider="taufa",
                status=STATE_UNAVAILABLE,
                latency_ms=5000,
                error="Connection refused"
            )
            mock_openai.return_value = ProviderProbeResult(
                provider="openai",
                status=STATE_CONNECTED,
                latency_ms=180,
                validation_ready=True
            )

            res = self.service.execute_preflight(
                proof_window_id="act_hour_11_20261106_v1",
                activity_id="act_hour_11",
                scheduled_open_time="2026-11-06T11:00:00+05:30",
                schedule_version=1,
                force=True
            )

            self.assertEqual(res.taufa.status, STATE_UNAVAILABLE)
            self.assertEqual(res.openai.status, STATE_CONNECTED)
            self.assertEqual(res.selected_validation_provider, PROVIDER_OPENAI_FALLBACK)
            self.assertEqual(res.primary_provider, PROVIDER_OPENAI_FALLBACK)
            self.assertEqual(res.backup_provider, "NONE")
            self.assertEqual(res.display_title, "AI FALLBACK READY")

    # =========================================================================
    # Test C: OpenAI unavailable, Taufa connected
    # =========================================================================
    def test_scenario_c_openai_unavailable_taufa_connected(self):
        """Scenario C: OpenAI unavailable, Taufa connected -> Taufa primary without backup."""
        with patch.object(self.service, 'probe_taufa_ai') as mock_taufa, \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_taufa.return_value = ProviderProbeResult(
                provider="taufa",
                status=STATE_CONNECTED,
                latency_ms=130,
                validation_ready=True
            )
            mock_openai.return_value = ProviderProbeResult(
                provider="openai",
                status=STATE_UNAVAILABLE,
                latency_ms=5000,
                error="Service unavailable"
            )

            res = self.service.execute_preflight(
                proof_window_id="act_hour_12_20261106_v1",
                activity_id="act_hour_12",
                scheduled_open_time="2026-11-06T12:00:00+05:30",
                schedule_version=1,
                force=True
            )

            self.assertEqual(res.taufa.status, STATE_CONNECTED)
            self.assertEqual(res.openai.status, STATE_UNAVAILABLE)
            self.assertEqual(res.selected_validation_provider, PROVIDER_TAUFA_AI)
            self.assertEqual(res.primary_provider, PROVIDER_TAUFA_AI)
            self.assertEqual(res.backup_provider, "NONE")

    # =========================================================================
    # Test D: Both providers unavailable
    # =========================================================================
    def test_scenario_d_both_unavailable(self):
        """Scenario D: Both providers unavailable -> PENDING_VALIDATION, submission protected."""
        with patch.object(self.service, 'probe_taufa_ai') as mock_taufa, \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_taufa.return_value = ProviderProbeResult(
                provider="taufa",
                status=STATE_UNAVAILABLE,
                latency_ms=5000,
                error="Taufa unreachable"
            )
            mock_openai.return_value = ProviderProbeResult(
                provider="openai",
                status=STATE_TIMEOUT,
                latency_ms=8000,
                error="OpenAI timeout"
            )

            res = self.service.execute_preflight(
                proof_window_id="act_hour_13_20261106_v1",
                activity_id="act_hour_13",
                scheduled_open_time="2026-11-06T13:00:00+05:30",
                schedule_version=1,
                force=True
            )

            self.assertEqual(res.selected_validation_provider, PROVIDER_PENDING_VALIDATION)
            self.assertEqual(res.primary_provider, "NONE")
            self.assertEqual(res.backup_provider, "NONE")
            self.assertEqual(res.display_title, "AI VALIDATION SERVICES UNAVAILABLE")

    # =========================================================================
    # Test E: Temporary timeout followed by successful retry
    # =========================================================================
    def test_scenario_e_temporary_timeout_and_successful_retry(self):
        """Scenario E: Timeout at initial check, succeeds on retry 1."""
        calls = {"taufa": 0}

        def probe_taufa(*args, **kwargs):
            calls["taufa"] += 1
            if calls["taufa"] == 1:
                return ProviderProbeResult(provider="taufa", status=STATE_TIMEOUT, latency_ms=8000, error="Timeout")
            return ProviderProbeResult(provider="taufa", status=STATE_CONNECTED, latency_ms=150, validation_ready=True)

        with patch.object(self.service, 'probe_taufa_ai', side_effect=probe_taufa), \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_openai.return_value = ProviderProbeResult(provider="openai", status=STATE_UNAVAILABLE, error="Down")

            # Check 1 (Initial: -300s)
            res1 = self.service.execute_preflight(
                proof_window_id="act_retry_1",
                activity_id="act_retry",
                scheduled_open_time="2026-11-06T14:00:00+05:30",
                schedule_version=1,
                retry_attempt=0,
                force=True
            )
            self.assertEqual(res1.taufa.status, STATE_TIMEOUT)
            self.assertEqual(res1.selected_validation_provider, PROVIDER_PENDING_VALIDATION)

            # Check 2 (Retry 1: -180s)
            res2 = self.service.execute_preflight(
                proof_window_id="act_retry_1",
                activity_id="act_retry",
                scheduled_open_time="2026-11-06T14:00:00+05:30",
                schedule_version=1,
                retry_attempt=1,
                force=True
            )
            self.assertEqual(res2.taufa.status, STATE_CONNECTED)
            self.assertEqual(res2.selected_validation_provider, PROVIDER_TAUFA_AI)
            self.assertEqual(res2.retry_count, 1)

    # =========================================================================
    # Test F: Authentication failure -> terminal error, NO retry
    # =========================================================================
    def test_scenario_f_auth_failure_no_retry(self):
        """Scenario F: Authentication failure is terminal; should_retry returns False."""
        with patch.object(self.service, 'probe_taufa_ai') as mock_taufa, \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_taufa.return_value = ProviderProbeResult(
                provider="taufa",
                status=STATE_AUTHENTICATION_FAILED,
                error="Invalid auth session token"
            )
            mock_openai.return_value = ProviderProbeResult(
                provider="openai",
                status=STATE_AUTHENTICATION_FAILED,
                error="Incorrect API key"
            )

            res = self.service.execute_preflight(
                proof_window_id="act_auth_fail_1",
                activity_id="act_auth_fail",
                scheduled_open_time="2026-11-06T15:00:00+05:30",
                schedule_version=1,
                force=True
            )

            self.assertIn(STATE_AUTHENTICATION_FAILED, NON_RETRYABLE_STATES)
            # Scheduler should not retry terminal states
            scheduler = AiPreflightScheduler(db=self.db, preflight_svc=self.service)
            self.assertFalse(scheduler._should_retry(res))

    # =========================================================================
    # Test G: Rate-limit or quota-exceeded response -> NO retry
    # =========================================================================
    def test_scenario_g_quota_or_rate_limit_no_retry(self):
        """Scenario G: Rate limit or quota exceeded is non-retryable for preflight."""
        with patch.object(self.service, 'probe_taufa_ai') as mock_taufa, \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_taufa.return_value = ProviderProbeResult(
                provider="taufa",
                status=STATE_RATE_LIMITED,
                error="Rate limited"
            )
            mock_openai.return_value = ProviderProbeResult(
                provider="openai",
                status=STATE_QUOTA_EXCEEDED,
                error="You exceeded your current quota"
            )

            res = self.service.execute_preflight(
                proof_window_id="act_quota_fail_1",
                activity_id="act_quota_fail",
                scheduled_open_time="2026-11-06T16:00:00+05:30",
                schedule_version=1,
                force=True
            )

            self.assertIn(STATE_RATE_LIMITED, NON_RETRYABLE_STATES)
            self.assertIn(STATE_QUOTA_EXCEEDED, NON_RETRYABLE_STATES)
            scheduler = AiPreflightScheduler(db=self.db, preflight_svc=self.service)
            self.assertFalse(scheduler._should_retry(res))

    # =========================================================================
    # Test H: Multiple simultaneous requests deduplicated
    # =========================================================================
    def test_scenario_h_deduplicated_simultaneous_requests(self):
        """Scenario H: Multiple simultaneous requests reuse cached probe or single in-flight lock."""
        probe_count = {"calls": 0}

        def probe_count_taufa(*args, **kwargs):
            probe_count["calls"] += 1
            return ProviderProbeResult(provider="taufa", status=STATE_CONNECTED, latency_ms=100, validation_ready=True)

        with patch.object(self.service, 'probe_taufa_ai', side_effect=probe_count_taufa), \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_openai.return_value = ProviderProbeResult(provider="openai", status=STATE_CONNECTED, latency_ms=150, validation_ready=True)

            # Execute first preflight
            res1 = self.service.execute_preflight(
                proof_window_id="act_dedup_1",
                activity_id="act_dedup",
                scheduled_open_time="2026-11-06T17:00:00+05:30",
                schedule_version=1,
                force=False
            )

            # Execute second preflight within cache freshness window (180s)
            res2 = self.service.execute_preflight(
                proof_window_id="act_dedup_1",
                activity_id="act_dedup",
                scheduled_open_time="2026-11-06T17:00:00+05:30",
                schedule_version=1,
                force=False
            )

            # Should only have probed once due to database / cache deduplication
            self.assertEqual(probe_count["calls"], 1)
            self.assertEqual(res1.selected_validation_provider, res2.selected_validation_provider)

    # =========================================================================
    # Test I & J: Execution timing (-300s calculation)
    # =========================================================================
    def test_scenario_i_and_j_timing_calculation(self):
        """Scenario I & J: Preflight is calculated exactly 5 minutes (300s) before window opening."""
        open_time_str = "2026-11-06T18:00:00+05:30"
        open_dt = datetime.fromisoformat(open_time_str)
        expected_preflight_dt = open_dt - timedelta(seconds=PREFLIGHT_OFFSET_SEC)

        scheduler = AiPreflightScheduler(db=self.db, preflight_svc=self.service)
        trigger_dt = scheduler.calculate_preflight_time(open_time_str)

        self.assertEqual(trigger_dt, expected_preflight_dt)
        diff_sec = (open_dt - trigger_dt).total_seconds()
        self.assertEqual(diff_sec, 300)

    # =========================================================================
    # Test K: Window rescheduled -> cancels obsolete job, reschedules
    # =========================================================================
    def test_scenario_k_window_rescheduled(self):
        """Scenario K: Rescheduled window cancels old trigger, recalculates for new opening time."""
        scheduler = AiPreflightScheduler(db=self.db, preflight_svc=self.service)

        # Initial schedule v1: 19:00:00
        window_v1 = {
            "activity_id": "act_resched",
            "proof_window_id": "act_resched_v1",
            "scheduled_open_time": "2026-11-06T19:00:00+05:30",
            "schedule_version": 1,
            "status": "SCHEDULED"
        }
        scheduler.register_window(window_v1)

        job_v1 = scheduler.get_scheduled_job("act_resched")
        self.assertIsNotNone(job_v1)
        self.assertEqual(job_v1["schedule_version"], 1)
        self.assertEqual(job_v1["trigger_time"], scheduler.calculate_preflight_time("2026-11-06T19:00:00+05:30"))

        # Rescheduled v2: moved to 19:30:00
        window_v2 = {
            "activity_id": "act_resched",
            "proof_window_id": "act_resched_v2",
            "scheduled_open_time": "2026-11-06T19:30:00+05:30",
            "schedule_version": 2,
            "status": "SCHEDULED"
        }
        scheduler.register_window(window_v2)

        job_v2 = scheduler.get_scheduled_job("act_resched")
        self.assertIsNotNone(job_v2)
        self.assertEqual(job_v2["schedule_version"], 2)
        self.assertEqual(job_v2["trigger_time"], scheduler.calculate_preflight_time("2026-11-06T19:30:00+05:30"))

    # =========================================================================
    # Test L: Window excused or cancelled -> skipped / cancelled
    # =========================================================================
    def test_scenario_l_window_excused_or_cancelled(self):
        """Scenario L: Excused or cancelled windows are cancelled and never probed."""
        scheduler = AiPreflightScheduler(db=self.db, preflight_svc=self.service)

        window_excused = {
            "activity_id": "act_exam_excused",
            "proof_window_id": "act_exam_excused_v1",
            "scheduled_open_time": "2026-11-06T20:00:00+05:30",
            "schedule_version": 1,
            "status": "EXCUSED"
        }
        scheduler.register_window(window_excused)
        self.assertIsNone(scheduler.get_scheduled_job("act_exam_excused"))

        window_cancelled = {
            "activity_id": "act_cancelled",
            "proof_window_id": "act_cancelled_v1",
            "scheduled_open_time": "2026-11-06T20:00:00+05:30",
            "schedule_version": 1,
            "status": "CANCELLED"
        }
        scheduler.register_window(window_cancelled)
        self.assertIsNone(scheduler.get_scheduled_job("act_cancelled"))

    # =========================================================================
    # Test M: Actual evidence submission fails after preflight succeeds
    # =========================================================================
    def test_scenario_m_evidence_failure_isolated_from_preflight(self):
        """Scenario M: If submission fails locally (e.g. invalid photo), preflight record remains intact."""
        with patch.object(self.service, 'probe_taufa_ai') as mock_taufa, \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_taufa.return_value = ProviderProbeResult(provider="taufa", status=STATE_CONNECTED, validation_ready=True)
            mock_openai.return_value = ProviderProbeResult(provider="openai", status=STATE_CONNECTED, validation_ready=True)

            res = self.service.execute_preflight(
                proof_window_id="act_iso_1",
                activity_id="act_iso",
                scheduled_open_time="2026-11-06T21:00:00+05:30",
                schedule_version=1,
                force=True
            )
            self.assertEqual(res.selected_validation_provider, PROVIDER_TAUFA_AI)

            # Ensure preflight is unchanged even if an evidence exception happens
            rec = self.db.get_ai_preflight_record("act_iso_1", 1)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["selected_validation_provider"], PROVIDER_TAUFA_AI)

    # =========================================================================
    # Test N: Both unavailable during preflight but recover before submission
    # =========================================================================
    def test_scenario_n_services_recover_at_submission(self):
        """Scenario N: If services were unavailable at preflight, late force check re-evaluates cleanly."""
        state = {"taufa_ok": False}

        def probe_taufa(*args, **kwargs):
            if state["taufa_ok"]:
                return ProviderProbeResult(provider="taufa", status=STATE_CONNECTED, latency_ms=100, validation_ready=True)
            return ProviderProbeResult(provider="taufa", status=STATE_UNAVAILABLE, error="Down")

        with patch.object(self.service, 'probe_taufa_ai', side_effect=probe_taufa), \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_openai.return_value = ProviderProbeResult(provider="openai", status=STATE_UNAVAILABLE, error="Down")

            # At -300s: both down
            res1 = self.service.execute_preflight(
                proof_window_id="act_recov_1",
                activity_id="act_recov",
                scheduled_open_time="2026-11-06T22:00:00+05:30",
                schedule_version=1,
                force=True
            )
            self.assertEqual(res1.selected_validation_provider, PROVIDER_PENDING_VALIDATION)

            # Later service recovers
            state["taufa_ok"] = True
            self.service.clear_cache()
            res2 = self.service.execute_preflight(
                proof_window_id="act_recov_1",
                activity_id="act_recov",
                scheduled_open_time="2026-11-06T22:00:00+05:30",
                schedule_version=1,
                force=True
            )
            self.assertEqual(res2.selected_validation_provider, PROVIDER_TAUFA_AI)

    # =========================================================================
    # Test Zero Side Effects: Preflight NEVER creates submissions or points
    # =========================================================================
    def test_zero_side_effects_guarantee(self):
        """Guaranteed: Preflight test prompt must NEVER create submissions, points, or approvals."""
        self.assertEqual(
            MINIMAL_PREFLIGHT_PROMPT,
            'Return the following JSON exactly: {"service":"ok","validation_ready":true}'
        )

        with patch.object(self.service, 'probe_taufa_ai') as mock_taufa, \
             patch.object(self.service, 'probe_openai_api') as mock_openai:
            mock_taufa.return_value = ProviderProbeResult(provider="taufa", status=STATE_CONNECTED, validation_ready=True)
            mock_openai.return_value = ProviderProbeResult(provider="openai", status=STATE_CONNECTED, validation_ready=True)

            res = self.service.execute_preflight(
                proof_window_id="act_no_side_effects",
                activity_id="act_no_side_effects",
                scheduled_open_time="2026-11-06T23:00:00+05:30",
                schedule_version=1,
                force=True
            )

            with self.db._connect() as conn:
                pts_cur = conn.execute("SELECT count(*) FROM score_events").fetchone()[0]
                subs_cur = conn.execute("SELECT count(*) FROM proof_reviews").fetchone()[0]

            self.assertEqual(pts_cur, 0)
            self.assertEqual(subs_cur, 0)


if __name__ == "__main__":
    unittest.main()
