import glob
import sqlite3

def remediate():
    for dbpath in glob.glob('*.db') + glob.glob('data/*.db'):
        try:
            con = sqlite3.connect(dbpath)
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='score_events'")
            if cur.fetchone():
                cur.execute("SELECT event_id, points FROM score_events WHERE audit_json LIKE '%eff1491f99bd%'")
                rows = cur.fetchall()
                if rows:
                    print(f"{dbpath}: found {len(rows)} score_events for eff1491f99bd")
                    cur.execute("DELETE FROM score_events WHERE audit_json LIKE '%eff1491f99bd%'")
                    con.commit()
                    print(f"{dbpath}: deleted invalid score_events")
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='proof_reviews'")
            if cur.fetchone():
                cur.execute("SELECT evidence_id, status FROM proof_reviews WHERE evidence_id LIKE '%eff1491f99bd%'")
                rows = cur.fetchall()
                if rows:
                    print(f"{dbpath}: found {len(rows)} proof_reviews for eff1491f99bd")
                    cur.execute("UPDATE proof_reviews SET status='NEEDS_REVIEW', decision='PENDING', points_awarded=0, reason_note='Quarantined: Lacks verified Taufa AI approval' WHERE evidence_id LIKE '%eff1491f99bd%'")
                    con.commit()
                    print(f"{dbpath}: updated proof_reviews to NEEDS_REVIEW with 0 pts")
            con.close()
        except Exception as e:
            print(f"{dbpath} error: {e}")

if __name__ == '__main__':
    remediate()
