import os
import json
import sqlite3
from datetime import datetime

def remediate_database(db_path: str):
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        return

    print(f"=== Remediating database: {db_path} ===")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find all negative transactions
    cursor.execute("""
        SELECT event_id, date, is_dev_mode, timestamp, event_type, description, points, audit_json
        FROM score_events
        WHERE points < 0
        ORDER BY date ASC, timestamp ASC
    """)
    negative_events = cursor.fetchall()
    print(f"Found {len(negative_events)} negative events to inspect.")

    remediated_count = 0
    skipped_count = 0

    for ev in negative_events:
        eid = ev["event_id"]
        date = ev["date"]
        pts = ev["points"]
        desc = ev["description"]
        dev_mode = ev["is_dev_mode"]
        try:
            audit = json.loads(ev["audit_json"])
        except Exception:
            audit = {}

        # Check if already reversed or has valid human authorization
        has_human_approval = (
            audit.get("approval_source") in ("ANDROID_HUMAN_CONFIRMATION", "AUTHENTICATED_HUMAN_ADMIN")
            and bool(audit.get("approved_by"))
        )
        if has_human_approval:
            print(f"Skipping authorized event: {eid} on {date} ({pts} pts)")
            skipped_count += 1
            continue

        comp_eid = f"se_comp_{eid}"

        # Check if compensating event already exists
        cursor.execute("SELECT 1 FROM score_events WHERE event_id = ?", (comp_eid,))
        if cursor.fetchone():
            print(f"Compensating event already exists for {eid}: {comp_eid}")
            skipped_count += 1
            continue

        # If date == 2026-10-16 and INITIAL_RESET was already applied for -160, check
        if date == "2026-10-16":
            cursor.execute("SELECT 1 FROM score_events WHERE date = '2026-10-16' AND event_type = 'INITIAL_RESET'")
            if cursor.fetchone():
                print(f"Skipping 2026-10-16 event {eid}: already reversed via INITIAL_RESET (+160)")
                # Mark original audit
                audit["remediation_status"] = "REVERSED"
                audit["remediation_event_id"] = "INITIAL_RESET_user_lok_2026-10-16_prod"
                audit["remediation_reason"] = "Reversed via initial reset to 0"
                cursor.execute(
                    "UPDATE score_events SET audit_json = ? WHERE event_id = ?",
                    (json.dumps(audit), eid)
                )
                skipped_count += 1
                continue

        # If date == 2026-10-15 and admin adjustment was already applied for -20, check
        if date == "2026-10-15":
            cursor.execute("SELECT 1 FROM score_events WHERE date = '2026-10-15' AND event_id = 'se_adj_e8db897510'")
            if cursor.fetchone():
                print(f"Skipping 2026-10-15 event {eid}: already reconciled via se_adj_e8db897510")
                audit["remediation_status"] = "REVERSED"
                audit["remediation_event_id"] = "se_adj_e8db897510"
                audit["remediation_reason"] = "Reconciled to mobile ledger balance"
                cursor.execute(
                    "UPDATE score_events SET audit_json = ? WHERE event_id = ?",
                    (json.dumps(audit), eid)
                )
                skipped_count += 1
                continue

        # If date == 2026-10-12 and se_reconcile_op_audit_reconcile_20261012 was already applied, check
        if date == "2026-10-12":
            cursor.execute("SELECT 1 FROM score_events WHERE date = '2026-10-12' AND event_id = 'se_reconcile_op_audit_reconcile_20261012'")
            if cursor.fetchone():
                print(f"Skipping 2026-10-12 event {eid}: already reconciled via se_reconcile_op_audit_reconcile_20261012")
                audit["remediation_status"] = "REVERSED"
                audit["remediation_event_id"] = "se_reconcile_op_audit_reconcile_20261012"
                audit["remediation_reason"] = "Reconciled to audit baseline"
                cursor.execute(
                    "UPDATE score_events SET audit_json = ? WHERE event_id = ?",
                    (json.dumps(audit), eid)
                )
                skipped_count += 1
                continue

        # Create linked compensating transaction
        reversal_pts = abs(pts)
        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        comp_desc = f"[REVERSAL] Reversing unauthorized secondary client deduction ({eid}): {desc}"
        comp_audit = {
            "reversal_for_event_id": eid,
            "original_points": pts,
            "reversal_points": reversal_pts,
            "reason": "Reversal of unauthorized secondary client deduction without human confirmation",
            "authorized_by": "REMEDIATION_AUDIT_20261016",
            "remediation_date": now_ts,
            "is_compensating_reconciliation": True
        }

        # Check column names in score_events
        cursor.execute("PRAGMA table_info(score_events)")
        cols = [c[1] for c in cursor.fetchall()]

        insert_cols = ["event_id", "date", "is_dev_mode", "timestamp", "event_type", "description", "points", "audit_json"]
        vals = [comp_eid, date, dev_mode, now_ts, "RECONCILIATION_COMPENSATION", comp_desc, reversal_pts, json.dumps(comp_audit)]

        if "device_id" in cols:
            insert_cols.append("device_id")
            vals.append("AUDIT_REMEDIATION")
        if "account_id" in cols:
            insert_cols.append("account_id")
            vals.append("user_lok")
        if "rule_version" in cols:
            insert_cols.append("rule_version")
            vals.append(1)

        placeholders = ", ".join(["?"] * len(vals))
        col_names = ", ".join(insert_cols)

        cursor.execute(f"INSERT OR IGNORE INTO score_events ({col_names}) VALUES ({placeholders})", vals)

        # Update original event's audit_json to link remediation
        audit["remediation_status"] = "REVERSED"
        audit["remediation_event_id"] = comp_eid
        audit["remediation_reason"] = "Reversed by compensating transaction"
        cursor.execute(
            "UPDATE score_events SET audit_json = ? WHERE event_id = ?",
            (json.dumps(audit), eid)
        )

        remediated_count += 1

    conn.commit()

    # Recompute daily totals
    cursor.execute("""
        SELECT date, is_dev_mode,
               SUM(CASE WHEN points > 0 THEN points ELSE 0 END) as pos,
               SUM(CASE WHEN points < 0 THEN points ELSE 0 END) as neg,
               SUM(points) as total
        FROM score_events
        GROUP BY date, is_dev_mode
        ORDER BY date DESC
    """)
    daily_totals = cursor.fetchall()

    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_records'")
    has_daily_records = cursor.fetchone() is not None

    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_state'")
    has_daily_state = cursor.fetchone() is not None

    for row in daily_totals:
        d = row["date"]
        dm = row["is_dev_mode"]
        tot = max(0, row["total"])  # score bounded by [0, 100]
        capped_score = min(100, tot)

        if has_daily_records:
            cursor.execute("""
                UPDATE daily_records
                SET final_score = ?, negative_points = 0
                WHERE date = ? AND is_dev_mode = ?
            """, (capped_score, d, dm))

        if has_daily_state:
            cursor.execute("""
                UPDATE daily_state
                SET score = ?, penalty_points = 0
                WHERE date = ? AND is_dev_mode = ?
            """, (capped_score, d, dm))

    conn.commit()
    conn.close()
    print(f"Completed {db_path}: {remediated_count} remediated, {skipped_count} skipped.")

if __name__ == "__main__":
    db1 = "data/strictrecords.db"
    db2 = os.path.expandvars(r"%LOCALAPPDATA%\StrictRecords\data\strictrecords.db")

    remediate_database(db1)
    if os.path.exists(db2):
        remediate_database(db2)
