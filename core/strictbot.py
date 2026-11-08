"""
StrictBot Assistant Engine (Sections 43, 44, 49-51, 60-61, 108, 165)
Provides the 10 formal StrictBot states (WELCOME, MOTIVATING, REMINDER, WARNING,
MISSED, RESTRICTED, SUCCESS, REWARD, SYNC, ERROR), adaptive trend-based coaching,
next-opportunity reward calculations, and time-adaptive home prioritization.
Continuously answers:
1. WHAT SHOULD I BE DOING NOW?
2. DID I ACTUALLY DO IT?
3. HAVE I EARNED MY REWARD?
"""

from datetime import datetime
from typing import Any, Dict, Optional
from core.models import StrictBotState


class StrictBot:
    @staticmethod
    def format_12h_short(hhmm: str) -> str:
        """Converts '07:00' -> '7:00 AM', '18:30' -> '6:30 PM'."""
        try:
            parts = hhmm.strip().split(":")
            h = int(parts[0])
            m = int(parts[1])
            period = "AM" if h < 12 else "PM"
            h12 = h % 12
            if h12 == 0:
                h12 = 12
            return f"{h12}:{m:02d} {period}"
        except Exception:
            return hhmm

    @staticmethod
    def evaluate_schedule_message(
        today_activities: Optional[ list ] = None,
        next_day_schedule: Optional[Dict[str, Any]] = None,
        countdown_state: Optional[Dict[str, Any]] = None,
        event_trigger: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Section 14: Reads real schedule and activity state without fabricating
        verification or point awards.
        """
        today_activities = today_activities or []
        next_day_schedule = next_day_schedule or {}
        countdown_state = countdown_state or {}

        wakeup_act = next((a for a in today_activities if a.get("type") == "WAKE_UP"), None)
        wakeup_act_status = (wakeup_act or {}).get("status", "SCHEDULED")
        next_day_status = str(next_day_schedule.get("status", "DRAFT")).upper()
        next_day_wakeup = str(
            next_day_schedule.get("wake_up_time")
            or (next_day_schedule.get("wake_up") or {}).get("target_time")
            or "07:00"
        )
        formatted_next_wakeup = StrictBot.format_12h_short(next_day_wakeup)

        # Check explicit event triggers first, backed by real state
        if event_trigger == "WAKEUP_SUBMITTED":
            if wakeup_act_status in ("SUBMITTED", "VALIDATING", "VERIFIED"):
                return {
                    "event": "WAKEUP_SUBMITTED",
                    "bot_state": StrictBotState.REMINDER.value,
                    "message": "Your wake-up proof has been submitted. Let's plan tomorrow's routine.",
                    "prompt_next_day_schedule": next_day_status != "SCHEDULED",
                    "verified": wakeup_act_status == "VERIFIED",
                }

        if event_trigger == "SCHEDULE_CONFIRMED" or (
            event_trigger == "SCHEDULE_SAVED" and next_day_status == "SCHEDULED"
        ):
            return {
                "event": "SCHEDULE_CONFIRMED",
                "bot_state": StrictBotState.MOTIVATING.value,
                "message": f"Tomorrow's wake-up target is {formatted_next_wakeup}. Your other activities are scheduled.",
                "prompt_next_day_schedule": False,
                "verified": wakeup_act_status == "VERIFIED",
            }

        if event_trigger == "PRE_OPENING":
            return {
                "event": "PRE_OPENING",
                "bot_state": StrictBotState.REMINDER.value,
                "message": "Your next activity opens in one minute.",
                "prompt_next_day_schedule": False,
                "verified": False,
            }

        if event_trigger == "ACTIVITY_MISSED":
            return {
                "event": "ACTIVITY_MISSED",
                "bot_state": StrictBotState.MISSED.value,
                "message": "The activity window has closed. Moving to the next scheduled task.",
                "prompt_next_day_schedule": False,
                "verified": False,
            }

        # State-driven evaluation when no explicit trigger is passed
        cd_state = str(countdown_state.get("state", "")).upper()
        cd_secs = int(countdown_state.get("countdown_seconds", -1) or -1)
        if cd_state == "UPCOMING" and 0 < cd_secs <= 60:
            return {
                "event": "PRE_OPENING",
                "bot_state": StrictBotState.REMINDER.value,
                "message": "Your next activity opens in one minute.",
                "prompt_next_day_schedule": False,
                "verified": False,
            }

        if wakeup_act_status in ("SUBMITTED", "VALIDATING") and next_day_status != "SCHEDULED":
            return {
                "event": "WAKEUP_SUBMITTED",
                "bot_state": StrictBotState.REMINDER.value,
                "message": "Your wake-up proof has been submitted. Let's plan tomorrow's routine.",
                "prompt_next_day_schedule": True,
                "verified": False,
            }

        missed_acts = [a for a in today_activities if a.get("status") in ("MISSED", "CLOSED")]
        if missed_acts and cd_state not in ("OPEN", "SUBMITTED", "VALIDATING"):
            return {
                "event": "ACTIVITY_MISSED",
                "bot_state": StrictBotState.MISSED.value,
                "message": "The activity window has closed. Moving to the next scheduled task.",
                "prompt_next_day_schedule": False,
                "verified": False,
            }

        if next_day_status == "SCHEDULED":
            return {
                "event": "SCHEDULE_CONFIRMED",
                "bot_state": StrictBotState.MOTIVATING.value,
                "message": f"Tomorrow's wake-up target is {formatted_next_wakeup}. Your other activities are scheduled.",
                "prompt_next_day_schedule": False,
                "verified": wakeup_act_status == "VERIFIED",
            }

        return {
            "event": "IDLE",
            "bot_state": StrictBotState.MOTIVATING.value,
            "message": "Stay disciplined. Every hour counts.",
            "prompt_next_day_schedule": wakeup_act_status in ("SUBMITTED", "VALIDATING", "VERIFIED")
            and next_day_status != "SCHEDULED",
            "verified": wakeup_act_status == "VERIFIED",
        }

    @staticmethod
    def evaluate_state(
        daily_record: Dict[str, Any],
        settings: Dict[str, Any],
        current_time_hhmm: str = "",
        current_streak: int = 0,
        sync_error: Optional[str] = None,
        now_hhmm: str = "",
        today_activities: Optional[list] = None,
        next_day_schedule: Optional[Dict[str, Any]] = None,
        countdown_state: Optional[Dict[str, Any]] = None,
        event_trigger: Optional[str] = None,
    ) -> Dict[str, Any]:
        if now_hhmm and not current_time_hhmm:
            current_time_hhmm = now_hhmm
        if not current_time_hhmm:
            current_time_hhmm = datetime.now().strftime("%H:%M")

        score = int(daily_record.get("final_score", 0))
        elite_threshold = int(settings.get("elite_threshold", 90))
        reward_threshold = int(settings.get("reward_threshold", 80))
        controlled_threshold = int(settings.get("controlled_mode_threshold", 60))
        wakeup_status = daily_record.get("wakeup_status", "PENDING")
        bath_status = daily_record.get("bath_status", "PENDING")
        gym_status = daily_record.get("gym_status", "UPCOMING")
        bath_deadline = settings.get("bath_deadline", "10:00")
        gym_time = settings.get("gym_time", "18:00")
        wakeup_time = settings.get("wakeup_time", "07:00")
        overnight_start = settings.get("overnight_start_time", "22:30")

        # Sync statuses with real scheduled activities if provided (Section 14)
        if today_activities:
            for act in today_activities:
                atype = act.get("type")
                astatus = act.get("status", "SCHEDULED")
                if atype == "WAKE_UP":
                    if astatus in ("SUBMITTED", "VALIDATING") and wakeup_status not in ("VERIFIED", "ON_TIME"):
                        wakeup_status = astatus
                    elif astatus in ("MISSED", "CLOSED"):
                        wakeup_status = "MISSED"
                elif atype == "BATH":
                    if astatus in ("SUBMITTED", "VALIDATING") and bath_status not in ("VERIFIED", "COMPLETED"):
                        bath_status = astatus
                    elif astatus in ("MISSED", "CLOSED"):
                        bath_status = "MISSED"
                elif atype == "GYM":
                    if astatus in ("SUBMITTED", "VALIDATING") and gym_status not in ("VERIFIED", "COMPLETED"):
                        gym_status = astatus
                    elif astatus in ("MISSED", "CLOSED"):
                        gym_status = "MISSED"

        now_mins = StrictBot._hhmm_to_mins(current_time_hhmm)
        bath_mins = StrictBot._hhmm_to_mins(bath_deadline)
        gym_mins = StrictBot._hhmm_to_mins(gym_time)
        wakeup_mins = StrictBot._hhmm_to_mins(wakeup_time)
        overnight_mins = StrictBot._hhmm_to_mins(overnight_start)

        # Time-of-day dashboard adaptation (Sections 49, 50, 51, 68, 108, 165)
        if now_mins >= overnight_mins or now_mins < wakeup_mins - 15:
            dashboard_period = "OVERNIGHT"
            prominent_card = "OVERNIGHT_LOCK"
        elif wakeup_status in ("PENDING", "MISSED") and now_mins < bath_mins:
            dashboard_period = "MORNING"
            prominent_card = "WAKE_UP"
        elif bath_status not in ("COMPLETED", "VERIFIED") and now_mins < bath_mins:
            dashboard_period = "BEFORE_10_BATH"
            prominent_card = "BATH_DEADLINE"
        elif now_mins < gym_mins - 60:
            dashboard_period = "AFTERNOON"
            prominent_card = "HOURLY_CHECK"
        elif gym_status not in ("COMPLETED", "VERIFIED") and now_mins < gym_mins + 120:
            dashboard_period = "BEFORE_6_GYM"
            prominent_card = "GYM_SESSION"
        else:
            dashboard_period = "EVENING"
            prominent_card = "SCORE_AND_REWARD"

        what_now = "Stay focused on your current hourly block."
        next_deadline_label = f"Hourly checkpoint at {current_time_hhmm[:2]}:00"
        bot_state = StrictBotState.MOTIVATING.value
        mood = "FOCUSED"
        message = "Stay disciplined. Every hour counts."

        if sync_error:
            bot_state = StrictBotState.ERROR.value
            mood = "WARNING"
            message = f"Unable to sync right now ({sync_error}). Your records are saved locally."
        elif dashboard_period == "OVERNIGHT":
            bot_state = StrictBotState.REMINDER.value
            what_now = f"Overnight Mode active. Prepare for {wakeup_time} wake-up."
            next_deadline_label = f"Tomorrow Wake-Up at {wakeup_time}"
            message = "Day complete. Prepare for tomorrow."
        elif wakeup_status in ("PENDING", "MISSED") and now_mins >= wakeup_mins - 10:
            what_now = f"Complete Wake-Up Challenge (Target {wakeup_time})"
            next_deadline_label = f"Wake-up target {wakeup_time}"
            bot_state = StrictBotState.WELCOME.value if score == 0 else StrictBotState.WARNING.value
            mood = "WARNING"
            message = (
                "Good morning. Let's earn today's freedom."
                if score == 0 and daily_record.get("snooze_count", 0) == 0
                else "Your day starts now. Complete Camera, Bluetooth, and Motivation Track verification."
            )
        elif bath_status not in ("COMPLETED", "VERIFIED") and now_mins < bath_mins:
            rem = bath_mins - now_mins
            what_now = f"Complete Morning Bath before {bath_deadline}"
            next_deadline_label = f"Bath before {bath_deadline} ({rem}m remaining)"
            bot_state = StrictBotState.WARNING.value if rem <= 30 else StrictBotState.REMINDER.value
            mood = "URGENT" if rem <= 30 else "FOCUSED"
            message = f"You have {rem} minutes to complete your morning routine."
        elif gym_status not in ("COMPLETED", "VERIFIED", "SKIPPED") and abs(now_mins - gym_mins) <= 90:
            what_now = f"Complete Gym Session (Scheduled {gym_time})"
            next_deadline_label = f"Gym at {gym_time}"
            bot_state = StrictBotState.MOTIVATING.value
            mood = "FOCUSED"
            message = f"Scheduled: {gym_time}. Your discipline score: {score}. TIME TO TRAIN."
        else:
            next_hour = min(22, max(8, (now_mins // 60) + 1))
            next_deadline_label = f"{next_hour:02d}:00 Hourly Update"
            what_now = f"Execute productive work for {next_hour - 1:02d}:00-{next_hour:02d}:00 checkpoint"
            bot_state = StrictBotState.REMINDER.value
            message = "One more hour. What did you accomplish?"

        # Adaptive Motivation based on recent discipline trend (Section 44)
        missed_hourly = int(daily_record.get("hourly_missed_count", 0))
        mins_to_next_hour = max(1, 60 - (now_mins % 60))
        adaptive_trend_message = ""
        if missed_hourly >= 3:
            bot_state = StrictBotState.MISSED.value
            mood = "WARNING"
            adaptive_trend_message = (
                f"You've missed {missed_hourly} checkpoints today. "
                f"Your next check is in {mins_to_next_hour} minutes."
            )
            message = adaptive_trend_message
        elif current_streak >= 3 and score >= controlled_threshold:
            adaptive_trend_message = f"You're on your best streak ({current_streak} days)."

        # Next Opportunity calculation (Section 61)
        needed_for_reward = max(0, reward_threshold - score)
        if wakeup_status not in ("ON_TIME", "LATE", "VERY_LATE", "COMPLETED", "VERIFIED"):
            next_opp = {
                "task": "Wake-Up Verification",
                "points": int(settings.get("wakeup_points", 15)) + int(settings.get("motivation_track_points", 10)),
            }
        elif bath_status not in ("COMPLETED", "VERIFIED") and now_mins <= bath_mins:
            next_opp = {"task": "Bath before 10:00 AM", "points": int(settings.get("bath_points", 10))}
        elif gym_status not in ("COMPLETED", "VERIFIED", "SKIPPED"):
            next_opp = {"task": "Gym", "points": int(settings.get("gym_points", 15))}
        else:
            next_opp = {
                "task": "Hourly Update + Evidence",
                "points": int(settings.get("hourly_productive_points", 5)) + int(settings.get("evidence_points", 3)),
            }

        if 0 < needed_for_reward <= next_opp["points"]:
            next_opp["strictbot_hint"] = f"One successful {next_opp['task'].lower()} could unlock your reward."
        elif needed_for_reward > 0:
            next_opp["strictbot_hint"] = f"{needed_for_reward} points to unlock. Next opportunity: {next_opp['task']} +{next_opp['points']}."
        else:
            next_opp["strictbot_hint"] = "Reward threshold reached. Maintain elite consistency."

        # Determine reward & message override if unlocked or restricted
        if score >= elite_threshold:
            reward_answer = "YES — ELITE DAY 🏆 (All Rewards Unlocked)"
            bot_state = StrictBotState.REWARD.value
            mood = "REWARD"
            message = f"YOU EARNED IT. Elite Day Score: {score}/{settings.get('max_daily_score', 100)}. Freedom unlocked."
        elif score >= reward_threshold:
            reward_answer = "YES — REWARD UNLOCKED 🏆 (YouTube & Entertainment Allowed)"
            bot_state = StrictBotState.REWARD.value
            if mood == "FOCUSED":
                mood = "REWARD"
                message = f"YOU EARNED IT. Score: {score}/{settings.get('max_daily_score', 100)}. Entertainment is unlocked."
        elif score >= controlled_threshold:
            needed = reward_threshold - score
            reward_answer = f"CONTROLLED MODE ⚠ (Need {needed} more points for full unlock; Local Music allowed)"
            if daily_record.get("snooze_count", 0) > 0 or daily_record.get("violation_count", 0) > 0:
                bot_state = StrictBotState.WARNING.value
                mood = "WARNING"
                message = f"Not yet. You're {needed} points away. Score is {score}/{reward_threshold}."
        else:
            needed = reward_threshold - score
            reward_answer = f"LOCKED 🚨 STRICT MODE (Need {needed} more points for reward)"
            if daily_record.get("violation_count", 0) > 0 or daily_record.get("snooze_count", 0) > 0:
                bot_state = StrictBotState.WARNING.value
                mood = "WARNING"
                message = f"You're losing points. Score is {score}/100. Earn {needed} more points to unlock rewards."

        schedule_eval = StrictBot.evaluate_schedule_message(
            today_activities=today_activities,
            next_day_schedule=next_day_schedule,
            countdown_state=countdown_state,
            event_trigger=event_trigger,
        )
        if event_trigger or schedule_eval.get("event") in ("WAKEUP_SUBMITTED", "PRE_OPENING"):
            message = schedule_eval["message"]
            bot_state = schedule_eval["bot_state"]

        did_i_do_it = (
            f"Wake-up: {wakeup_status} | Bath: {bath_status} | "
            f"Hourly: {daily_record.get('hourly_completed_count', 0)}/{daily_record.get('hourly_total_count', 15)} | "
            f"Gym: {gym_status}"
        )

        real_ai_coaching = {}
        try:
            from core.real_ai_vlm import RealAIBotEngine
            vlm = RealAIBotEngine.get_instance()
            real_ai_coaching = vlm.generate_real_ai_coaching(
                daily_record=daily_record,
                score=score,
                current_streak=current_streak,
            )
        except Exception:
            pass

        anim_info = StrictBot.resolve_animation_for_event(
            event_type=event_trigger or schedule_eval.get("event") or "DASHBOARD_OPENS",
            bot_state=bot_state,
        )

        return {
            "bot_name": "STRICTBOT",
            "avatar": "🤖",
            "state": bot_state,
            "animation_state": anim_info["animation"],
            "supported_animations": StrictBot.SUPPORTED_ANIMATIONS,
            "supported_states": [s.value for s in StrictBotState],
            "mood": mood,
            "message": message,
            "schedule_message": schedule_eval["message"],
            "schedule_event": schedule_eval["event"],
            "prompt_next_day_schedule": schedule_eval["prompt_next_day_schedule"],
            "adaptive_trend_message": adaptive_trend_message,
            "dashboard_period": dashboard_period,
            "period": dashboard_period,
            "prominent_card": prominent_card,
            "next_opportunity": next_opp,
            "CurrentTime": current_time_hhmm,
            "NextDeadline": next_deadline_label,
            "real_ai_coaching": real_ai_coaching,
            "real_ai_advice": real_ai_coaching.get("coaching_advice", message),
            "real_ai_model": real_ai_coaching.get("model_name", "StrictBot VLM Phase 4"),
            "real_ai_headline": real_ai_coaching.get("headline", ""),
            "questions": {
                "what_should_i_be_doing_now": what_now,
                "did_i_actually_do_it": did_i_do_it,
                "have_i_earned_my_reward": reward_answer,
            },
        }

    SUPPORTED_ANIMATIONS = ["IDLE", "WALK", "FLY", "SPEAK", "REVIEW", "SUCCESS", "WARNING"]

    @staticmethod
    def resolve_animation_for_event(
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        bot_state: str = "",
        reduced_motion: bool = False,
    ) -> Dict[str, Any]:
        """
        Part 1 (Sections 1 & 2): Small finite-state animation controller mapping
        genuine application events to supported lightweight character animations:
        IDLE, WALK, FLY, SPEAK, REVIEW, SUCCESS, WARNING.
        Never claims an action completed unless the event actually arrived.
        """
        payload = payload or {}
        evt = (event_type or "DASHBOARD_OPENS").strip().upper()
        anim = "IDLE"
        factual_note = ""

        if evt in ("DASHBOARD_OPENS", "IDLE", "APP_RESUME"):
            anim = "IDLE"
            factual_note = "Dashboard active."
        elif evt in ("SECTION_NAVIGATE", "TAB_CHANGED", "WALK"):
            anim = "WALK"
            section = str(payload.get("section") or "view")
            factual_note = f"Switched to {section}."
        elif evt in ("STRICTBOT_ACTION", "FLY", "OPEN_ASSISTANT"):
            anim = "FLY"
            factual_note = "Opened StrictBot assistant action."
        elif evt in ("EVIDENCE_ANALYZING", "AI_VALIDATION_STARTED", "REVIEW", "PROOF_SUBMITTED"):
            anim = "REVIEW"
            factual_note = "Processing submitted evidence."
        elif evt in ("OCR_COMPLETED", "OCR_FINISHED"):
            ocr_st = str(payload.get("ocr_status") or payload.get("status") or "OCR_COMPLETED").upper()
            anim = "SPEAK"
            if ocr_st == "OCR_COMPLETED":
                tokens = int(payload.get("token_count") or 0)
                factual_note = f"OCR completed ({tokens} text tokens extracted)."
            elif ocr_st == "NO_TEXT_DETECTED":
                factual_note = "OCR completed: no readable text detected in image."
            else:
                factual_note = f"OCR status: {ocr_st}."
        elif evt in ("DUPLICATE_DETECTED", "DUPLICATE_EVIDENCE", "DUPLICATE_REVIEW"):
            dup_st = str(payload.get("duplicate_status") or payload.get("status") or "POSSIBLE_DUPLICATE")
            anim = "WARNING"
            factual_note = f"Duplicate check flagged: {dup_st}. Queued for review."
        elif evt in ("PROOF_ACCEPTED", "PROOF_APPROVED", "WAKEUP_SUBMITTED", "SUCCESS"):
            anim = "SUCCESS"
            factual_note = "Submission recorded."
        elif evt in ("TASK_MISSED", "ACTIVITY_MISSED", "PROOF_REJECTED", "WARNING", "PRE_OPENING"):
            anim = "WARNING"
            factual_note = "Task requires attention."
        elif evt in ("DAILY_SUMMARY_READY", "SPEAK", "OPEN_SUMMARY"):
            anim = "SPEAK"
            factual_note = "Today's activity summary is ready."
        else:
            st_upper = (bot_state or "").upper()
            if st_upper in ("WARNING", "MISSED", "RESTRICTED", "ERROR"):
                anim = "WARNING"
            elif st_upper in ("SUCCESS", "REWARD"):
                anim = "SUCCESS"
            else:
                anim = "IDLE"

        if reduced_motion and anim in ("WALK", "FLY"):
            anim = "IDLE"

        return {
            "event": evt,
            "animation": anim,
            "reduced_motion": bool(reduced_motion),
            "factual_note": factual_note,
        }

    @staticmethod
    def restriction_message(score: int, required: int, target_app: str) -> Dict[str, Any]:
        remaining = max(0, required - score)
        return {
            "bot_name": "STRICTBOT",
            "avatar": "🤖",
            "state": StrictBotState.RESTRICTED.value,
            "animation_state": "WARNING",
            "title": "NOT ALLOWED YET",
            "target_app": target_app,
            "current_score": score,
            "required_score": required,
            "remaining_points": remaining,
            "message": f"Not yet. You're {remaining} points away. Your score is {score}/{required}. Return to your task.",
            "action_label": "RETURN TO WORK",
        }

    @staticmethod
    def _hhmm_to_mins(hhmm: str) -> int:
        try:
            parts = hhmm.strip().split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return 0


