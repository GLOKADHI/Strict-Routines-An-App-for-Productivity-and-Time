"""
StrictRecords — Historical Evidence Validation & Points Reconciliation Engine
=============================================================================
Audits and reconciles historical submission inconsistencies:
1. Taufa AI or Fallback returned APPROVED with points, but points were not posted in score_events.
2. Evidence was falsely marked REJECTED despite valid approval from primary validator.
3. scheduled_activities has 0 points while proof was approved.
4. Ensures idempotency: previously rewarded evidence is NEVER rewarded again.
5. Preserves legitimate rejections.
6. Generates detailed audit trail and supports --dry-run mode.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ReconcileSubmissions")


def audit_historical_submissions(
    db: Any,
    date: Optional[str] = None,
    account_id: str = "user_lok",
    is_dev_mode: bool = False,
) -> Dict[str, Any]:
    """
    Audits database for historical evidence validation and points discrepancies.
    Read-only inspection.
    """
    dev_flag = 1 if is_dev_mode else 0
    discrepancies: List[Dict[str, Any]] = []

    with db._connect() as conn:
        query = """
            SELECT pr.*, ef.file_path, ef.sha256_hash, ef.is_deleted, ef.record_type,
                   sa.status AS activity_status, sa.points AS activity_points,
                   sa.validation_status AS activity_validation_status
            FROM proof_reviews pr
            LEFT JOIN evidence_files ef ON ef.evidence_id = pr.evidence_id
            LEFT JOIN scheduled_activities sa ON sa.activity_id = pr.activity_id
            WHERE (? IS NULL OR pr.date = ?) AND pr.user_id = ?
            ORDER BY pr.date ASC, pr.submitted_at ASC
        """
        rows = conn.execute(query, (date, date, account_id)).fetchall()

        total_checked = len(rows)

        for r in rows:
            pr = dict(r)
            ev_id = pr["evidence_id"]
            sub_id = ""
            signals = {}
            try:
                signals = json.loads(pr["signals_json"] or "{}")
                sub_id = signals.get("submission_id") or ""
            except Exception:
                signals = {}

            if not sub_id:
                ef_row = conn.execute("SELECT related_record_id FROM evidence_files WHERE evidence_id = ?", (ev_id,)).fetchone()
                if ef_row:
                    sub_id = ef_row["related_record_id"] or ""
            if not sub_id:
                sub_id = f"sub_{ev_id}"

            # Check existing score events
            se_rows = conn.execute(
                "SELECT * FROM score_events WHERE (evidence_id = ? OR event_id = ?) AND is_dev_mode = ? AND points > 0",
                (ev_id, f"pts_award_{sub_id}", dev_flag),
            ).fetchall()
            already_rewarded = len(se_rows) > 0
            total_points_in_events = sum(int(se["points"]) for se in se_rows)

            pr_status = str(pr.get("status") or "").upper()
            pr_decision = str(pr.get("decision") or "").upper()
            pr_pts = int(pr.get("points_awarded") or 0)

            # Check validator results in signals_json
            taufa_info = signals.get("taufa_ai") or signals.get("validator_result") or {}
            approved_by_ai = False
            ai_pts = 0

            if isinstance(taufa_info, dict):
                approved_by_ai = bool(
                    taufa_info.get("approved") is True
                    or (taufa_info.get("qualifies_for_points") is True and int(taufa_info.get("score") or 0) > 0)
                )
                ai_pts = int(taufa_info.get("awarded_points") or taufa_info.get("score") or 0)

            # Condition 1: Proof review approved with points, but score_events missing
            if (pr_status == "APPROVED" or pr_decision == "APPROVED") and pr_pts > 0 and not already_rewarded:
                discrepancies.append({
                    "submission_id": sub_id,
                    "evidence_id": ev_id,
                    "activity_id": pr.get("activity_id"),
                    "date": pr.get("date"),
                    "issue": "APPROVED_PROOF_MISSING_SCORE_EVENT",
                    "approved_points": pr_pts,
                    "recorded_score_events": total_points_in_events,
                    "activity_status": pr.get("activity_status"),
                    "activity_points": pr.get("activity_points"),
                    "reconciliation_action": "POST_MISSING_POINTS",
                })
            # Condition 2: False rejection due to heuristic while Taufa approved
            elif pr_status == "REJECTED" and approved_by_ai and ai_pts > 0 and not already_rewarded:
                discrepancies.append({
                    "submission_id": sub_id,
                    "evidence_id": ev_id,
                    "activity_id": pr.get("activity_id"),
                    "date": pr.get("date"),
                    "issue": "FALSE_REJECTION_REVERT",
                    "approved_points": ai_pts,
                    "recorded_score_events": total_points_in_events,
                    "activity_status": pr.get("activity_status"),
                    "activity_points": pr.get("activity_points"),
                    "reconciliation_action": "REVERT_REJECTION_AND_AWARD_POINTS",
                })
            # Condition 3: Proof approved, score event present, but scheduled_activities points == 0
            elif (pr_status == "APPROVED" or pr_decision == "APPROVED") and pr_pts > 0 and already_rewarded and bool(pr.get("activity_id")) and pr.get("activity_points") is not None and int(pr.get("activity_points") or 0) == 0:
                discrepancies.append({
                    "submission_id": sub_id,
                    "evidence_id": ev_id,
                    "activity_id": pr.get("activity_id"),
                    "date": pr.get("date"),
                    "issue": "SCHEDULED_ACTIVITY_POINTS_ZERO",
                    "approved_points": pr_pts,
                    "recorded_score_events": total_points_in_events,
                    "activity_status": pr.get("activity_status"),
                    "activity_points": pr.get("activity_points"),
                    "reconciliation_action": "SYNC_ACTIVITY_POINTS",
                })

    return {
        "total_checked": total_checked,
        "discrepancies_count": len(discrepancies),
        "discrepancies": discrepancies,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "account_id": account_id,
        "date_filter": date,
    }


def reconcile_historical_submissions(
    db: Any,
    scoring_engine: Any,
    date: Optional[str] = None,
    dry_run: bool = True,
    account_id: str = "user_lok",
    is_dev_mode: bool = False,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Safely executes reconciliation of historical inconsistencies.
    In dry-run mode, only returns planned actions.
    In apply mode, executes atomic database repair.
    """
    audit_report = audit_historical_submissions(db, date=date, account_id=account_id, is_dev_mode=is_dev_mode)
    discrepancies = audit_report["discrepancies"]
    results: List[Dict[str, Any]] = []

    if dry_run:
        report = {
            "mode": "DRY_RUN",
            "audited_at": audit_report["audited_at"],
            "total_checked": audit_report["total_checked"],
            "discrepancies_found": audit_report["discrepancies_count"],
            "planned_corrections": discrepancies,
            "applied_count": 0,
        }
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
        return report

    # Apply corrections atomically
    applied_count = 0
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for item in discrepancies:
        ev_id = item["evidence_id"]
        sub_id = item["submission_id"]
        act_id = item["activity_id"]
        target_date = item["date"]
        pts = item["approved_points"]
        action = item["reconciliation_action"]

        try:
            if action in ("POST_MISSING_POINTS", "REVERT_REJECTION_AND_AWARD_POINTS"):
                res = scoring_engine.award_evidence_points_atomically(
                    evidence_id=ev_id,
                    activity_id=act_id or "",
                    submission_id=sub_id,
                    points=pts,
                    admin_id="RECONCILIATION_ENGINE",
                    reason=f"Historical reconciliation: {item['issue']}",
                    date=target_date,
                    account_id=account_id,
                    is_dev_mode=is_dev_mode,
                )
                results.append({
                    "submission_id": sub_id,
                    "evidence_id": ev_id,
                    "status": "RECONCILED",
                    "points_awarded": pts,
                    "action": action,
                    "result": res,
                })
                applied_count += 1
            elif action == "SYNC_ACTIVITY_POINTS":
                with db._connect() as conn:
                    conn.execute(
                        "UPDATE scheduled_activities SET status = 'VERIFIED', validation_status = 'VERIFIED', points = ? WHERE activity_id = ?",
                        (pts, act_id),
                    )
                scoring_engine.recalculate_daily_score(target_date, user_id=account_id, is_dev_mode=is_dev_mode)
                results.append({
                    "submission_id": sub_id,
                    "evidence_id": ev_id,
                    "status": "RECONCILED",
                    "points_awarded": pts,
                    "action": action,
                })
                applied_count += 1
        except Exception as exc:
            logger.exception("Failed to reconcile submission %s: %s", sub_id, exc)
            results.append({
                "submission_id": sub_id,
                "evidence_id": ev_id,
                "status": "FAILED",
                "error": str(exc),
            })

    final_report = {
        "mode": "APPLIED",
        "reconciled_at": now_iso,
        "total_checked": audit_report["total_checked"],
        "discrepancies_found": audit_report["discrepancies_count"],
        "applied_count": applied_count,
        "results": results,
    }

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(final_report, f, indent=2)

    return final_report


def main():
    parser = argparse.ArgumentParser(description="StrictRecords Historical Submissions Reconciliation")
    parser.add_argument("--date", help="Specific date to reconcile (YYYY-MM-DD)")
    parser.add_argument("--apply", action="store_true", help="Apply corrections (default is dry-run)")
    parser.add_argument("--output", help="Output file path for audit JSON")
    args = parser.parse_args()

    from core.database import StrictRecordsDB
    from core.scoring_engine import ScoringEngine

    db = StrictRecordsDB()
    engine = ScoringEngine(db)

    dry_run = not args.apply
    mode_str = "APPLY" if args.apply else "DRY-RUN"
    print(f"Running StrictRecords Reconciliation in [{mode_str}] mode...")

    out = args.output or f"reconciliation_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report = reconcile_historical_submissions(db, engine, date=args.date, dry_run=dry_run, output_path=out)

    print(f"Total checked: {report['total_checked']}")
    print(f"Discrepancies found: {report['discrepancies_found']}")
    print(f"Applied count: {report['applied_count']}")
    print(f"Full report saved to: {out}")


if __name__ == "__main__":
    main()
