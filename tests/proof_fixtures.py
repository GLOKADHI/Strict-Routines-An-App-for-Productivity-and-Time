"""Explicit retained-image/admin fixtures; never used by application code."""
import io
from PIL import Image

def seed_approved_bath(db,engine,date,points=10):
    db.ensure_daily_record(date)
    identity='fixture-bath-'+date
    with db._connect() as conn:
        conn.execute("UPDATE scheduled_activities SET is_enabled=0 WHERE date=? AND type='BATH'",(date,))
        conn.execute("INSERT OR REPLACE INTO scheduled_activities(activity_id,account_id,type,date,scheduled_time,window_start,window_end,notification_time,status,points) VALUES(?,'user_lok','BATH',?,?,?,?,?,'SUBMITTED',?)",
            (identity,date,date+'T07:30:00+05:30',date+'T07:00:00+05:30',date+'T10:00:00+05:30',date+'T06:59:00+05:30',points))
    output=io.BytesIO();Image.new('RGB',(120,120),(50,110,160)).save(output,'JPEG')
    evidence=engine.store_evidence_file(date,'Bath',identity,'fixture-phone',file_bytes=output.getvalue(),metadata={'activity_id':identity})
    return engine.apply_proof_approval(evidence['evidence_id'],'independent-fixture-admin',points=points)
