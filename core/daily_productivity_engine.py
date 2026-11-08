"""
StrictRecords — Taufa AI Daily Productivity Engine & YouTube Gate 2 Controller
=============================================================================
Evaluates daily productivity for the study schedule in designated Taufa thread:
891a3c1e-eed2-460e-bc05-6b8ac94bec5b

Enforces the Two-Gate YouTube Access Policy:
- Gate 1: Points balance >= 80 points unlocks normal YouTube independently.
- Gate 2: Daily Productivity Score >= 70% at 9:40 PM IST when points < 80.
  Grants temporary YouTube access expiring STRICTLY at 10:00 PM IST (22:00).
  At 10:00 PM IST, the override strictly expires and pause/blur restriction is restored.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import time
import urllib.request
import uuid
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.taufa_client import (
    CHAT_API_PATH,
    DEFAULT_TAUFA_HOST,
    DESIGNATED_CHAT_URL,
    DESIGNATED_CONVERSATION_ID,
    TaufaClient,
    _open_taufa_request,
)

logger = logging.getLogger("DailyProductivityEngine")
TIMEZONE_IST = "Asia/Kolkata"


def now_ist() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE_IST))


def get_ist_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def get_ten_pm_ist_epoch_ms(date_str: str) -> int:
    tz = ZoneInfo(TIMEZONE_IST)
    parts = [int(p) for p in date_str.split("-")]
    ten_pm = datetime(parts[0], parts[1], parts[2], 22, 0, 0, 0, tzinfo=tz)
    return int(ten_pm.timestamp() * 1000)


def _get_db_conn(db: Any):
    if not db:
        return None
    if hasattr(db, "_connect"):
        return db._connect()
    if hasattr(db, "get_connection"):
        return db.get_connection()
    if hasattr(db, "conn"):
        return db.conn
    return None


def ensure_productivity_table(db: Any) -> None:
    conn_ctx = _get_db_conn(db)
    if not conn_ctx:
        return
    with conn_ctx as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_productivity_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                productivity_score INTEGER NOT NULL,
                summary TEXT,
                reason TEXT,
                study_hours_completed REAL DEFAULT 0.0,
                study_hours_scheduled REAL DEFAULT 0.0,
                gate2_eligible INTEGER DEFAULT 0,
                youtube_override_active INTEGER DEFAULT 0,
                youtube_override_expires_at INTEGER DEFAULT 0,
                raw_response TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


class DailyProductivityEngine:
    def __init__(self, db: Optional[Any] = None, client: Optional[TaufaClient] = None):
        self.db = db
        self.client = client or TaufaClient()
        if self.db:
            ensure_productivity_table(self.db)

    def evaluate_daily_productivity(
        self,
        date_str: Optional[str] = None,
        force: bool = False,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        Gathers daily activity schedule, verifies proofs, and evaluates
        genuine 0-100% productivity score in designated Taufa conversation.
        """
        now = now_ist()
        today_str = date_str or now.strftime("%Y-%m-%d")
        if self.db:
            ensure_productivity_table(self.db)

        # Check existing evaluation unless force=True
        if not force and self.db:
            user_points = 0
            if hasattr(self.db, "get_total_points"):
                try:
                    user_points = int(self.db.get_total_points("user_lok"))
                except Exception:
                    user_points = 0
            conn_ctx = _get_db_conn(self.db)
            if conn_ctx:
                with conn_ctx as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT productivity_score, summary, reason, gate2_eligible, youtube_override_active, youtube_override_expires_at, study_hours_completed, study_hours_scheduled FROM daily_productivity_evaluations WHERE date = ?",
                        (today_str,),
                    )
                    row = cur.fetchone()
                    if row:
                        score, summary, reason, g2, ov_act, ov_exp, comp_h, sched_h = row
                        hourly_schedule_table = self.get_daily_study_schedule(today_str)
                        cats = self.get_category_breakdown(today_str)
                        return {
                            "success": True,
                            "date": today_str,
                            "productivity_score": score,
                            "summary": summary,
                            "reason": reason,
                            "gate2_eligible": bool(g2),
                            "youtube_override_active": bool(ov_act),
                            "youtube_override_expires_at": ov_exp,
                            "study_hours_completed": comp_h,
                            "study_hours_scheduled": sched_h,
                            "user_points": user_points,
                            "gate1_active": user_points >= 80,
                            "hourly_schedule": hourly_schedule_table,
                            "categories": cats,
                            "cached": True,
                        }

        # Build comprehensive summary of the day's study schedule
        from core.activity_report import build_truthful_daily_summary
        summary_data = build_truthful_daily_summary(self.db, today_str) if self.db else {}
        hourly_records = summary_data.get("hourly_records", [])
        scheduled_items = summary_data.get("scheduled_activities", [])
        
        # Calculate study stats
        study_hours_scheduled = 0
        study_hours_verified = 0
        missed_hours = 0
        study_breakdown = []

        for item in scheduled_items:
            cat = str(item.get("category") or "").upper()
            title = str(item.get("title") or item.get("activity_name") or cat)
            slot = f"{item.get('start_time', '00:00')} - {item.get('end_time', '00:00')}"
            status = str(item.get("status") or "PENDING").upper()
            is_study = cat in ("STUDY", "COLLEGE", "RESEARCH", "CODING", "PROJECT") or "HOURLY" in str(item.get("type") or "").upper()
            if is_study:
                study_hours_scheduled += 1
                if status in ("COMPLETED", "APPROVED", "VERIFIED"):
                    study_hours_verified += 1
                elif status in ("MISSED", "REJECTED"):
                    missed_hours += 1
            study_breakdown.append({
                "slot": slot,
                "title": title,
                "category": cat,
                "status": status,
                "is_study": is_study,
            })

        hourly_schedule_table = self.get_daily_study_schedule(today_str)
        cats = self.get_category_breakdown(today_str)

        cookie_header = self.client.get_cookie_header()
        if not cookie_header or not self.client.has_active_session():
            logger.warning("Taufa AI session cookies not configured for daily productivity evaluation")
            heuristic_score = int(round((study_hours_verified / max(1, study_hours_scheduled)) * 100)) if study_hours_scheduled > 0 else 75
            return self._commit_evaluation(
                today_str,
                heuristic_score,
                f"Heuristic Daily Summary: {study_hours_verified}/{study_hours_scheduled} study hours verified.",
                "Taufa session offline; evaluated using verified local proof records.",
                study_hours_verified,
                study_hours_scheduled,
                now,
            )

        prompt_text = (
            f"You are the StrictRecords AI Daily Productivity Evaluator in designated thread {DESIGNATED_CONVERSATION_ID}.\n"
            f"Evaluate the user's daily academic productivity for date: {today_str}.\n\n"
            f"DAILY STUDY SCHEDULE RECORD:\n"
            f"- Scheduled Study Hours: {study_hours_scheduled}\n"
            f"- Verified Completed Hours: {study_hours_verified}\n"
            f"- Missed/Unverified Hours: {missed_hours}\n"
            f"- Detailed Hourly Breakdown:\n"
        )
        for row in study_breakdown[:16]:
            prompt_text += f"  * [{row['slot']}] {row['title']} ({row['category']}) - Status: {row['status']}\n"

        prompt_text += (
            f"\nINSTRUCTIONS:\n"
            f"Calculate an authentic daily productivity percentage (0 to 100).\n"
            f"Return JSON only:\n"
            f'{{"date": "{today_str}", "productivity_score": <integer 0-100>, "gate2_eligible": <true if productivity_score >= 70 else false>, "summary": "<1 sentence concise summary>", "reason": "<1-2 sentences evaluation reasoning>"}}'
        )

        ctx = ssl.create_default_context()
        new_id = self.client._branch_message(cookie_header, prompt_text, ctx)
        if new_id:
            payload = {
                "conversationId": DESIGNATED_CONVERSATION_ID,
                "id": DESIGNATED_CONVERSATION_ID,
                "trigger": "regenerate-message",
                "messageId": new_id,
                "messages": [{
                    "id": new_id,
                    "role": "user",
                    "content": prompt_text,
                    "parts": [{"type": "text", "text": prompt_text}]
                }]
            }
        else:
            msg_id = str(uuid.uuid4())
            payload = {
                "conversationId": DESIGNATED_CONVERSATION_ID,
                "id": DESIGNATED_CONVERSATION_ID,
                "messages": [{
                    "id": msg_id,
                    "role": "user",
                    "content": prompt_text,
                    "parts": [{"type": "text", "text": prompt_text}]
                }],
                "trigger": "submit-message"
            }

        url_endpoint = f"{DEFAULT_TAUFA_HOST}{CHAT_API_PATH}"
        try:
            req = urllib.request.Request(
                url_endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                    "Content-Type": "application/json",
                    "Cookie": cookie_header,
                    "Referer": DESIGNATED_CHAT_URL,
                    "Origin": DEFAULT_TAUFA_HOST,
                    "X-Conversation-Id": DESIGNATED_CONVERSATION_ID,
                },
                method="POST"
            )
            with _open_taufa_request(req, context=ctx, timeout=timeout) as resp:
                raw_text = resp.read(1024 * 1024).decode("utf-8", errors="ignore")

            clean_text = TaufaClient._clean_ai_stream(raw_text)
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_text, re.DOTALL)
            cand = match.group(1) if match else clean_text
            s_idx = cand.find("{")
            e_idx = cand.rfind("}")
            if s_idx != -1 and e_idx != -1:
                data = json.loads(cand[s_idx : e_idx + 1])
                score = int(data.get("productivity_score", 0))
                summary = str(data.get("summary") or "Daily productivity evaluated.").strip()
                reason = str(data.get("reason") or "Evaluated by Taufa AI in designated thread.").strip()
            else:
                score = int(round((study_hours_verified / max(1, study_hours_scheduled)) * 100)) if study_hours_scheduled > 0 else 75
                summary = f"Daily study completion: {study_hours_verified}/{study_hours_scheduled} verified hours."
                reason = clean_text[:200]
        except Exception as eval_err:
            logger.warning("Taufa daily productivity evaluation failed: %s", eval_err)
            score = int(round((study_hours_verified / max(1, study_hours_scheduled)) * 100)) if study_hours_scheduled > 0 else 75
            summary = f"Daily study summary: {study_hours_verified}/{study_hours_scheduled} hours completed."
            reason = f"Local fallback evaluation: {study_hours_verified} of {study_hours_scheduled} study sessions verified."

        return self._commit_evaluation(
            today_str,
            score,
            summary,
            reason,
            study_hours_verified,
            study_hours_scheduled,
            now,
        )

    def _commit_evaluation(
        self,
        date_str: str,
        score: int,
        summary: str,
        reason: str,
        comp_h: float,
        sched_h: float,
        now: datetime,
    ) -> Dict[str, Any]:
        score = max(0, min(100, score))
        gate2_eligible = score >= 70

        # Check Gate 1 balance: if user has >= 80 points, Gate 1 handles YouTube independently!
        user_points = 0
        if self.db and hasattr(self.db, "get_total_points"):
            try:
                user_points = int(self.db.get_total_points("user_lok"))
            except Exception:
                user_points = 0

        # Gate 2 applies only when user points < 80 and score >= 70%
        # Expiry is strictly 10:00 PM IST (22:00)
        ten_pm_ms = get_ten_pm_ist_epoch_ms(date_str)
        now_ms = get_ist_epoch_ms(now)
        override_active = False
        override_exp = 0

        if gate2_eligible and user_points < 80:
            if now_ms < ten_pm_ms:
                override_active = True
                override_exp = ten_pm_ms

        if self.db:
            conn_ctx = _get_db_conn(self.db)
            if conn_ctx:
                with conn_ctx as conn:
                    conn.execute("""
                        INSERT INTO daily_productivity_evaluations
                            (date, productivity_score, summary, reason, study_hours_completed, study_hours_scheduled, gate2_eligible, youtube_override_active, youtube_override_expires_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date) DO UPDATE SET
                            productivity_score = excluded.productivity_score,
                            summary = excluded.summary,
                            reason = excluded.reason,
                            study_hours_completed = excluded.study_hours_completed,
                            study_hours_scheduled = excluded.study_hours_scheduled,
                            gate2_eligible = excluded.gate2_eligible,
                            youtube_override_active = excluded.youtube_override_active,
                            youtube_override_expires_at = excluded.youtube_override_expires_at
                    """, (
                        date_str,
                        score,
                        summary,
                        reason,
                        comp_h,
                        sched_h,
                        1 if gate2_eligible else 0,
                        1 if override_active else 0,
                        override_exp,
                        now.isoformat(),
                    ))
                    conn.commit()

        hourly_schedule_table = self.get_daily_study_schedule(date_str)
        cats = self.get_category_breakdown(date_str)

        return {
            "success": True,
            "date": date_str,
            "productivity_score": score,
            "summary": summary,
            "reason": reason,
            "gate2_eligible": gate2_eligible,
            "youtube_override_active": override_active,
            "youtube_override_expires_at": override_exp,
            "study_hours_completed": comp_h,
            "study_hours_scheduled": sched_h,
            "user_points": user_points,
            "gate1_active": user_points >= 80,
            "hourly_schedule": hourly_schedule_table,
            "categories": cats,
        }

    def get_category_breakdown(self, date_str: str) -> Dict[str, Dict[str, int]]:
        """
        Categorization of activities:
        - Technical / Coding
        - Aptitude
        - Reasoning
        - English
        - Core academic subjects
        """
        categories = {
            "Technical / Coding": {"completed": 0, "scheduled": 0},
            "Aptitude": {"completed": 0, "scheduled": 0},
            "Reasoning": {"completed": 0, "scheduled": 0},
            "English": {"completed": 0, "scheduled": 0},
            "Core Academic": {"completed": 0, "scheduled": 0},
        }
        if not self.db:
            return categories

        try:
            conn_ctx = _get_db_conn(self.db)
            if conn_ctx:
                with conn_ctx as conn:
                    conn.row_factory = None
                    rows = conn.execute(
                        "SELECT title, type, status, details_json FROM scheduled_activities WHERE date = ?",
                        (date_str,),
                    ).fetchall()
                    for r in rows:
                        title = (r[0] or "").lower()
                        act_type = (r[1] or "").upper()
                        status = (r[2] or "").upper()
                        is_done = status in ("COMPLETED", "APPROVED", "VERIFIED")

                        matched_cat = None
                        if any(k in title for k in ("code", "coding", "python", "java", "dsa", "algorithm", "dev", "project", "technical")):
                            matched_cat = "Technical / Coding"
                        elif any(k in title for k in ("aptitude", "quant", "math", "arithmetic")):
                            matched_cat = "Aptitude"
                        elif any(k in title for k in ("reason", "logic", "puzzle", "reasoning")):
                            matched_cat = "Reasoning"
                        elif any(k in title for k in ("english", "verbal", "grammar", "reading")):
                            matched_cat = "English"
                        elif any(k in title for k in ("study", "academic", "college", "course", "exam", "test")):
                            matched_cat = "Core Academic"
                        elif "HOURLY" in act_type:
                            matched_cat = "Core Academic"

                        if matched_cat:
                            categories[matched_cat]["scheduled"] += 1
                            if is_done:
                                categories[matched_cat]["completed"] += 1
        except Exception as e:
            logger.warning("Error computing category breakdown: %s", e)

        return categories

    def get_daily_study_schedule(self, date_str: str) -> List[Dict[str, Any]]:
        """
        Constructs the 16 hourly slots (06:00 to 22:00) with activity, duration, proof status, and source.
        """
        hourly_definitions = [
            ("06:00", "07:00", "06:00–07:00"),
            ("07:00", "08:00", "07:00–08:00"),
            ("08:00", "09:00", "08:00–09:00"),
            ("09:00", "10:00", "09:00–10:00"),
            ("10:00", "11:00", "10:00–11:00"),
            ("11:00", "12:00", "11:00–12:00"),
            ("12:00", "13:00", "12:00–13:00"),
            ("13:00", "14:00", "13:00–14:00"),
            ("14:00", "15:00", "14:00–15:00"),
            ("15:00", "16:00", "15:00–16:00"),
            ("16:00", "17:00", "16:00–17:00"),
            ("17:00", "18:00", "17:00–18:00"),
            ("18:00", "19:00", "18:00–19:00"),
            ("19:00", "20:00", "19:00–20:00"),
            ("20:00", "21:00", "20:00–21:00"),
            ("21:00", "22:00", "21:00–22:00"),
        ]

        if not self.db:
            return [
                {
                    "slot": label,
                    "activity": "Study & Discipline Session",
                    "duration": "60m",
                    "proof_status": "Scheduled",
                    "source": "Windows EXE",
                    "category": "Core Academic",
                }
                for _, _, label in hourly_definitions
            ]

        try:
            conn_ctx = _get_db_conn(self.db)
            if conn_ctx:
                with conn_ctx as conn:
                    conn.row_factory = None
                    act_rows = conn.execute(
                        "SELECT activity_id, title, type, scheduled_time, status, validation_status, window_duration_minutes FROM scheduled_activities WHERE date = ? ORDER BY scheduled_time ASC",
                        (date_str,),
                    ).fetchall()
                    rev_rows = conn.execute(
                        "SELECT review_id, activity_id, hour_slot, status, decision FROM proof_reviews WHERE date = ?",
                        (date_str,),
                    ).fetchall()
        except Exception as e:
            logger.warning("Error reading scheduled activities: %s", e)
            act_rows = []
            rev_rows = []

        now = now_ist()
        is_today = date_str == now.strftime("%Y-%m-%d")
        current_hour = now.hour if is_today else (24 if date_str < now.strftime("%Y-%m-%d") else -1)

        result = []
        for s_start, s_end, label in hourly_definitions:
            slot_h_start = int(s_start.split(":")[0])
            slot_h_end = int(s_end.split(":")[0])

            # Match activity
            matched_act = None
            for a in act_rows:
                act_id, title, act_type, sched_time, status, val_status, dur = a
                title_s = title or ""
                st_s = sched_time or ""
                if f"({s_start}-{s_end})" in title_s or f"{s_start}-{s_end}" in title_s:
                    matched_act = a
                    break
                elif f"T{s_start}:00" in st_s or (f"T{s_end}:00" in st_s and "HOURLY" in (act_type or "")):
                    matched_act = a

            # Match review
            matched_rev = None
            for r in rev_rows:
                rev_id, r_act_id, r_slot, r_status, r_dec = r
                if r_slot in (s_start, f"{s_start}-{s_end}", s_end) or (matched_act and r_act_id == matched_act[0]):
                    matched_rev = r
                    break

            # Activity Title
            if matched_act and matched_act[1]:
                act_title = matched_act[1]
            else:
                act_title = f"Hourly Discipline Check ({s_start}-{s_end})"

            # Duration
            dur_mins = 60
            if matched_act and matched_act[6]:
                try:
                    dur_mins = int(matched_act[6])
                except Exception:
                    dur_mins = 60

            # Proof Status: Verified, Pending, Rejected, Missed
            status_str = "Scheduled"
            if matched_rev:
                dec = (matched_rev[4] or "").upper()
                st = (matched_rev[3] or "").upper()
                if dec in ("APPROVED", "VERIFIED") or st in ("APPROVED", "VERIFIED"):
                    status_str = "Verified"
                elif dec == "REJECTED" or st == "REJECTED":
                    status_str = "Rejected"
                elif st in ("PENDING", "SUBMITTED", "AI_PROVISIONAL", "UNDER_REVIEW"):
                    status_str = "Pending"
            elif matched_act:
                act_st = (matched_act[4] or "").upper()
                val_st = (matched_act[5] or "").upper()
                if act_st in ("COMPLETED", "APPROVED", "VERIFIED") or val_st in ("VERIFIED", "APPROVED"):
                    status_str = "Verified"
                elif act_st in ("SUBMITTED", "PENDING", "VALIDATING") or val_st in ("PENDING", "SUBMITTED"):
                    status_str = "Pending"
                elif act_st in ("MISSED", "EXCUSED") or val_st == "REJECTED":
                    status_str = "Missed"
                elif current_hour >= slot_h_end:
                    status_str = "Missed"
                elif current_hour >= slot_h_start:
                    status_str = "In Progress"
                else:
                    status_str = "Scheduled"
            else:
                if current_hour >= slot_h_end:
                    status_str = "Missed"
                elif current_hour >= slot_h_start:
                    status_str = "In Progress"
                else:
                    status_str = "Scheduled"

            # Source: Android APK or Windows EXE
            source_str = "Android APK" if "Check" in act_title or "HOURLY" in str(matched_act[2] if matched_act else "") else "Windows EXE"

            # Category
            cat_str = "Core Academic"
            t_lower = act_title.lower()
            if any(k in t_lower for k in ("code", "coding", "python", "java", "dsa", "dev")):
                cat_str = "Technical / Coding"
            elif any(k in t_lower for k in ("aptitude", "quant", "math")):
                cat_str = "Aptitude"
            elif any(k in t_lower for k in ("reason", "logic")):
                cat_str = "Reasoning"
            elif any(k in t_lower for k in ("english", "verbal")):
                cat_str = "English"

            result.append({
                "slot": label,
                "activity": act_title,
                "duration": f"{dur_mins}m",
                "proof_status": status_str,
                "source": source_str,
                "category": cat_str,
            })

        return result

    def get_active_override(self, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Returns active Gate 2 YouTube access override if score >= 70%, points < 80,
        and current time is before 10:00 PM IST.
        At or after 10:00 PM IST, returns None (override strictly expires).
        """
        if not self.db:
            return None
        now = now_ist()
        cur_ms = now_ms or get_ist_epoch_ms(now)
        today_str = now.strftime("%Y-%m-%d")
        ten_pm_ms = get_ten_pm_ist_epoch_ms(today_str)

        # Strictly expired at or past 10:00 PM IST
        if cur_ms >= ten_pm_ms:
            return None

        ensure_productivity_table(self.db)
        conn_ctx = _get_db_conn(self.db)
        if conn_ctx:
            with conn_ctx as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT productivity_score, summary, reason, youtube_override_expires_at FROM daily_productivity_evaluations WHERE date = ? AND youtube_override_active = 1",
                    (today_str,),
                )
                row = cur.fetchone()
                if row:
                    score, summary, reason, exp = row
                    if exp > cur_ms:
                        return {
                            "score": score,
                            "summary": summary,
                            "reason": reason,
                            "expires_at_ms": exp,
                            "gate": "GATE_2_PRODUCTIVITY_OVERRIDE",
                        }
        return None
