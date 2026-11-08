"""Legacy schedule migration preserves history and is transactional on failure."""
import sqlite3
import pytest
from core.database import StrictRecordsDB


def legacy(tmp_path, *, collide_index=False):
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE scheduled_activities (
                activity_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                date TEXT NOT NULL, type TEXT NOT NULL, scheduled_time TEXT NOT NULL,
                status TEXT NOT NULL, retained_json TEXT,
                UNIQUE(account_id, date, type, scheduled_time));
            CREATE TABLE reference(record_id TEXT PRIMARY KEY, activity_id TEXT REFERENCES scheduled_activities(activity_id));
            CREATE TABLE audit(value TEXT);
            CREATE INDEX retained_schedule_index ON scheduled_activities(retained_json);
            CREATE TRIGGER retained_schedule_trigger AFTER UPDATE ON scheduled_activities
              BEGIN INSERT INTO audit(value) VALUES(new.activity_id); END;
            INSERT INTO scheduled_activities VALUES ('slot-9','account','2026-10-20','HOURLY','09:00','SCHEDULED','{"history":"nine"}');
            INSERT INTO scheduled_activities VALUES ('slot-10','account','2026-10-20','HOURLY','10:00','CANCELLED','{"history":"ten"}');
            INSERT INTO reference VALUES ('original-reference','slot-10');
        ''')
        if collide_index:
            conn.execute('CREATE INDEX scheduled_activities_live_time ON audit(value)')
    db = object.__new__(StrictRecordsDB); db.db_path = str(path)
    return db


def test_migration_preserves_values_references_indexes_and_triggers(tmp_path):
    db = legacy(tmp_path)
    with db._connect() as conn:
        before = [tuple(row) for row in conn.execute('SELECT * FROM scheduled_activities ORDER BY activity_id')]
    db._migrate_cancelled_schedule_uniqueness()
    with db._connect() as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM scheduled_activities ORDER BY activity_id')] == before
        assert conn.execute('PRAGMA foreign_key_list(reference)').fetchone()['table'] == 'scheduled_activities'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        assert conn.execute('SELECT COUNT(*) FROM audit').fetchone()[0] == 0
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='retained_schedule_index'").fetchone()
        conn.execute("UPDATE scheduled_activities SET scheduled_time='10:00' WHERE activity_id='slot-9'")
        assert conn.execute('SELECT value FROM audit').fetchone()[0] == 'slot-9'
        assert conn.execute('SELECT activity_id FROM reference').fetchone()[0] == 'slot-10'
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO scheduled_activities VALUES('duplicate','account','2026-10-20','HOURLY','10:00','OPEN','{}')")
    db._migrate_cancelled_schedule_uniqueness()  # Safe second startup.
    with db._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM scheduled_activities').fetchone()[0] == 2


def test_failed_migration_rolls_back_table_and_constraints(tmp_path):
    db = legacy(tmp_path, collide_index=True)
    with pytest.raises(sqlite3.OperationalError):
        db._migrate_cancelled_schedule_uniqueness()
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scheduled_activities").fetchone()[0] == 2
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='_scheduled_activities_v3'").fetchone()
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE scheduled_activities SET scheduled_time='10:00' WHERE activity_id='slot-9'")
