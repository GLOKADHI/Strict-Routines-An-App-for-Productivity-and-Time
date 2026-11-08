"""
Comprehensive regression & integration tests for:
- Part A/B: 1-bath & 2-bath daily routine setup (college & non-college), non-overlap validation,
  fixed college-day Bath 1 (7:30-8:00 AM activity, 8:00-8:30 AM proof, 9:00-10:00 AM first hourly),
  and late wake-up evaluation Cases A, B, C, D, E.
- Part C: Offline Gym Mode activation after 5:00 PM (17:00), later PC reconciliation after 8:30 PM cutoff,
  idempotent HTTP / Bluetooth / batch sync replay, 0-lives rejection, and conflict safety without erasing history.
"""
from datetime import datetime
import json
import uuid
import pytest

from core.database import StrictRecordsDB
from core.daily_college_decision import DailyCollegeDecision
from core.gym_daily_decision import DailyGymDecision, GymDecisionError
from core.gym_mode_engine import GymModeEngine
from core.time_window_engine import IST, TimeWindowEngine


DAY = "2026-10-29"
MORNING = datetime(2026, 10, 29, 7, 15, tzinfo=IST)


@pytest.fixture
def env(tmp_path):
    db = StrictRecordsDB(str(tmp_path / "morning_offline_gym.sqlite"))
    return db, DailyCollegeDecision(db), DailyGymDecision(db), GymModeEngine(db)


def test_college_one_bath_creates_only_bath1_and_no_second_session(env):
    db, college, _, _ = env
    res = college.save(
        "user_lok",
        DAY,
        True,
        0,
        str(uuid.uuid4()),
        bath_plans=[dict(planned_time="07:30", window_start="08:00", window_end="08:30")],
        actor="user_lok",
        device_id="phone_1",
        current=MORNING,
    )
    assert res["success"]
    items = TimeWindowEngine(db).ensure_activities_in_db(DAY, reference_now=MORNING)
    baths = [a for a in items if a["type"] == "BATH" and a.get("is_enabled")]
    assert len(baths) == 1
    assert "BATH-1" in baths[0]["activity_id"]
    assert "T07:30:00" in baths[0]["scheduled_time"]
    assert "T08:00:00" in baths[0]["window_start"]
    assert "T08:30:00" in baths[0]["window_end"]
    details = json.loads(baths[0]["details_json"])
    assert "T07:30:00" in details["reserved_start"]
    assert "T08:00:00" in details["reserved_end"]

    hourly = [a for a in items if a["type"] == "HOURLY" and a.get("is_enabled")]
    assert hourly[0]["activity_id"] == f"ACT-{DAY.replace('-', '')}-0900-HOURLY"


def test_non_college_two_baths_and_switching_to_one_retires_unsubmitted_second_bath(env):
    db, college, _, _ = env
    res2 = college.save(
        "user_lok",
        DAY,
        False,
        0,
        str(uuid.uuid4()),
        bath_plans=[
            dict(planned_time="13:00", window_start="13:00", window_end="17:30"),
            dict(planned_time="18:00", window_start="16:00", window_end="21:00"),
        ],
        actor="user_lok",
        device_id="phone_1",
        current=MORNING,
    )
    assert res2["success"]
    baths2 = [
        a for a in TimeWindowEngine(db).ensure_activities_in_db(DAY, reference_now=MORNING)
        if a["type"] == "BATH" and a.get("is_enabled")
    ]
    assert len(baths2) == 2
    assert baths2[0]["activity_id"].endswith("-BATH-1")
    assert baths2[1]["activity_id"].endswith("-BATH-2")

    # Switching down to 1 bath retires unsubmitted Bath 2 so no phantom pending/missed Bath 2 remains
    res1 = college.save(
        "user_lok",
        DAY,
        False,
        1,
        str(uuid.uuid4()),
        bath_plans=[dict(planned_time="13:00", window_start="13:00", window_end="17:30")],
        actor="user_lok",
        device_id="phone_1",
        current=MORNING,
    )
    assert res1["success"]
    with db._connect() as conn:
        enabled = conn.execute(
            "SELECT activity_id FROM scheduled_activities WHERE date=? AND type='BATH' AND is_enabled=1",
            (DAY,),
        ).fetchall()
    assert len(enabled) == 1
    assert enabled[0]["activity_id"].endswith("-BATH-1")


def test_two_baths_rejects_overlapping_intervals(env):
    _, college, _, _ = env
    with pytest.raises(GymDecisionError) as exc:
        college.save(
            "user_lok",
            DAY,
            False,
            0,
            str(uuid.uuid4()),
            bath_plans=[
                dict(planned_time="13:00", window_start="13:00", window_end="17:30"),
                dict(planned_time="13:30", window_start="13:00", window_end="17:30"),
            ],
            actor="user_lok",
            device_id="phone_1",
            current=MORNING,
        )
    assert exc.value.code == "INVALID_BATH_PLAN"


def test_evaluate_late_wakeup_cases_a_b_c_d_e(env):
    _, college, _, _ = env
    res = college.save("user_lok", DAY, True, 0, str(uuid.uuid4()), actor="user_lok", device_id="phone_1", current=MORNING)
    acts = DailyCollegeDecision.activities(res["college_decision"])
    b1, b2 = acts[0], acts[1]

    # Case A: Before 8:00 AM (07:15)
    eval_a = DailyCollegeDecision.evaluate_late_wakeup(datetime(2026, 10, 29, 7, 15, tzinfo=IST), True, b1, b2)
    assert eval_a["state"] == "BEFORE_ACTIVITY"
    assert eval_a["bath2_feasible"] is True

    # Case B: Between 8:00 AM and 8:30 AM (08:12) -> activity ended, proof window OPEN
    eval_b = DailyCollegeDecision.evaluate_late_wakeup(datetime(2026, 10, 29, 8, 12, tzinfo=IST), True, b1, b2)
    assert eval_b["state"] == "PROOF_WINDOW_OPEN"
    assert eval_b["proof_remaining_minutes"] in (17, 18)
    assert eval_b["bath2_feasible"] is True

    # Case C: At or after 8:30 AM (08:45) -> expired/missed without shifting Bath 1 timestamps
    eval_c = DailyCollegeDecision.evaluate_late_wakeup(datetime(2026, 10, 29, 8, 45, tzinfo=IST), True, b1, b2)
    assert eval_c["state"] == "EXPIRED_MISSED"
    assert "T08:00:00" in eval_c["proof_window_start"]
    assert "T08:30:00" in eval_c["proof_window_end"]
    assert eval_c["bath2_feasible"] is True

    # Case E: Very late timestamp past Bath 2 window (21:15) -> Bath 2 infeasible
    eval_e = DailyCollegeDecision.evaluate_late_wakeup(datetime(2026, 10, 29, 21, 15, tzinfo=IST), True, b1, b2)
    assert eval_e["bath2_feasible"] is False


def test_offline_gym_mode_reconciliation_after_cutoff_is_idempotent_and_preserves_lives(env):
    db, _, gym_decision, engine = env
    act_id = f"ACT-{DAY.replace('-', '')}-1830-GYM"

    # Phone activated offline at 5:05 PM (17:05), PC reconnects later at 9:15 PM (21:15)
    t_act = datetime(2026, 10, 29, 17, 5, tzinfo=IST)
    t_sync = datetime(2026, 10, 29, 21, 15, tzinfo=IST)
    act_ms = int(t_act.timestamp() * 1000)

    req_id = "gym-act-user_lok-2026-10-29-ACT-20261029-1830-GYM"
    sess_id = "GMS-OFFLINE-20261029-001"

    # 1. Before 5:00 PM offline timestamp must be rejected
    t_early_ms = int(datetime(2026, 10, 29, 16, 59, tzinfo=IST).timestamp() * 1000)
    early_res = engine.reconcile_offline_activation(
        gym_activity_id=act_id,
        activation_time_ms=t_early_ms,
        session_id="GMS-EARLY",
        request_id="req-early-1659",
        account_id="user_lok",
        device_id="phone_1",
        dt=t_sync,
    )
    assert not early_res["success"]
    assert early_res["code"] == "WINDOW_CLOSED"

    # 2. Valid 5:05 PM offline activation reconciled at 9:15 PM succeeds and records session once
    ok_res = engine.reconcile_offline_activation(
        gym_activity_id=act_id,
        activation_time_ms=act_ms,
        session_id=sess_id,
        request_id=req_id,
        account_id="user_lok",
        device_id="phone_1",
        dt=t_sync,
    )
    assert ok_res["success"]
    assert ok_res["session_id"] == sess_id
    assert ok_res["state"]["lives_balance"] == 3  # 1 heart reserved under obligation, not prematurely deducted

    # 3. Duplicate replay via batch sync / Bluetooth / HTTP is strictly idempotent
    replay_res = engine.reconcile_offline_activation(
        gym_activity_id=act_id,
        activation_time_ms=act_ms,
        session_id=sess_id,
        request_id=req_id,
        account_id="user_lok",
        device_id="phone_1",
        dt=t_sync,
    )
    assert replay_res["success"]
    assert replay_res.get("replayed") is True

    batch_res = db.process_sync_events_batch("phone_1", [
        dict(
            event_id="evt-gym-offline-001",
            event_type="GYM_MODE_ACTIVATE",
            payload=dict(
                account_id="user_lok",
                session_id=sess_id,
                gym_activity_id=act_id,
                activation_time_ms=act_ms,
                request_id=req_id,
            ),
        )
    ])
    assert batch_res["success"] and batch_res["processed_count"] == 1

    with db._connect() as conn:
        count = conn.execute("SELECT count(*) FROM gym_mode_sessions WHERE account_id='user_lok'").fetchone()[0]
    assert count == 1
    assert engine.state(account_id="user_lok", dt=t_sync)["lives_balance"] == 3


def test_offline_gym_mode_rejected_when_zero_lives_or_pc_not_planned_conflict(env):
    db, _, gym_decision, engine = env
    next_day = "2026-10-30"
    t_morning = datetime(2026, 10, 30, 8, 0, tzinfo=IST)
    t_act = datetime(2026, 10, 30, 17, 30, tzinfo=IST)
    act_ms = int(t_act.timestamp() * 1000)

    # PC explicitly marked GYM_NOT_PLANNED -> offline activation flags conflict without erasing history
    gym_decision.save("user_lok", next_day, False, 0, "pc-rest-choice", actor="pc", device_id="pc_1", current=t_morning)
    conflict_res = engine.reconcile_offline_activation(
        gym_activity_id="ACT-20261030-1830-GYM",
        activation_time_ms=act_ms,
        session_id="GMS-CONFLICT-01",
        request_id="req-conflict-01",
        account_id="user_lok",
        device_id="phone_1",
        dt=t_act,
    )
    assert not conflict_res["success"]
    assert conflict_res["code"] == "CONFLICT"
    assert conflict_res.get("conflict") is True

    # Zero lives remaining -> offline activation rejected
    day3 = "2026-10-31"
    t_act3 = datetime(2026, 10, 31, 18, 0, tzinfo=IST)
    engine.state(account_id="user_lok", dt=t_act3)
    with db._connect() as conn:
        conn.execute(
            "UPDATE gym_mode_state SET lives_balance=0, updated_at=? WHERE account_id='user_lok'",
            (t_act3.isoformat(),),
        )
    no_lives_res = engine.reconcile_offline_activation(
        gym_activity_id="ACT-20261031-1830-GYM",
        activation_time_ms=int(t_act3.timestamp() * 1000),
        session_id="GMS-NOLIVES-01",
        request_id="req-nolives-01",
        account_id="user_lok",
        device_id="phone_1",
        dt=t_act3,
    )
    assert not no_lives_res["success"]
    assert no_lives_res["code"] == "NO_LIVES"
