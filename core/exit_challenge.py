"""Durable, server-graded explicit exit authorization. Never changes points."""
import json
import secrets
import threading
import time

from windows.exit_authentication import verify_exit_key


class ExitChallenge:
    QUESTION_COUNT = 25
    REQUIRED_CORRECT = 24  # 96%, the first possible result above 95%.
    DURATION_SECONDS = 25 * 60

    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock
        self.session = secrets.token_hex(24)
        self.lock = threading.RLock()
        with db._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS desktop_exit_challenges (
                challenge_id TEXT PRIMARY KEY, session TEXT NOT NULL,
                questions TEXT NOT NULL, answers TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL, started REAL NOT NULL, deadline REAL NOT NULL,
                last_seen REAL NOT NULL, score INTEGER NOT NULL DEFAULT 0,
                authorized_until REAL NOT NULL DEFAULT 0)""")

    def _read(self, conn, cid):
        row = conn.execute("SELECT * FROM desktop_exit_challenges WHERE challenge_id=?", (str(cid),)).fetchone()
        if not row or row['session'] != self.session:
            raise ValueError('Start or resume the exit check with your PIN first.')
        row = dict(row)
        now = max(float(self.clock()), row['last_seen'])
        if ((now >= row['deadline'] and row['status'] in ('IN_PROGRESS', 'PASSED'))
                or (row['status'] == 'PASSED' and now >= row['authorized_until'])):
            row['status'] = 'EXPIRED'
        conn.execute("UPDATE desktop_exit_challenges SET last_seen=?,status=? WHERE challenge_id=?",
                     (now, row['status'], cid))
        row['now'] = now
        return row

    def _public(self, row):
        qs = json.loads(row['questions'])
        return dict(challenge_id=row['challenge_id'], status=row['status'],
                    questions=[{k: q[k] for k in ('id', 'category', 'prompt', 'options')} for q in qs],
                    answers=json.loads(row['answers']), total_questions=len(qs),
                    score=row['score'], percentage=row['score'] * 4,
                    required_correct=self.REQUIRED_CORRECT, duration_seconds=self.DURATION_SECONDS,
                    deadline=row['deadline'], server_time=row['now'],
                    authorized_until=row['authorized_until'],
                    seconds_remaining=max(0, int(row['deadline'] - row['now'])),
                    passed=row['status'] == 'PASSED', points_impact=0)

    def start(self, pin):
        if not verify_exit_key(pin):
            raise ValueError('Incorrect exit PIN. StrictRoutines remains running.')
        with self.lock, self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            now = float(self.clock())
            row = conn.execute("SELECT * FROM desktop_exit_challenges WHERE status IN ('IN_PROGRESS','PASSED') ORDER BY started DESC LIMIT 1").fetchone()
            if (row and max(now, row['last_seen']) < row['deadline']
                    and (row['status'] != 'PASSED' or max(now, row['last_seen']) < row['authorized_until'])):
                cid = row['challenge_id']
                # Reauthenticate on a new process; retain the questions, answers and deadline.
                conn.execute('UPDATE desktop_exit_challenges SET session=? WHERE challenge_id=?', (self.session, cid))
            else:
                conn.execute("UPDATE desktop_exit_challenges SET status='EXPIRED' WHERE status IN ('IN_PROGRESS','PASSED')")
                from core.aptitude_bank import get_all_questions
                # Use the arithmetic generator's unambiguous questions, avoiding legacy
                # hand-written questions whose explanations contain unresolved corrections.
                pool = [q for q in get_all_questions() if q['id'].startswith('apt_gen_')
                        and len(set(q['options'])) == 4 and q['options'].count(q['correct_answer']) == 1]
                qs = secrets.SystemRandom().sample(pool, self.QUESTION_COUNT)
                cid = 'exit_' + secrets.token_hex(16)
                conn.execute("INSERT INTO desktop_exit_challenges(challenge_id,session,questions,status,started,deadline,last_seen) VALUES(?,?,?,'IN_PROGRESS',?,?,?)",
                             (cid, self.session, json.dumps(qs), now, now + self.DURATION_SECONDS, now))
            return self._public(self._read(conn, cid))

    def get(self, cid):
        with self.lock, self.db._connect() as conn:
            return self._public(self._read(conn, cid))

    def answer(self, cid, qid, answer):
        with self.lock, self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._read(conn, cid)
            if row['status'] != 'IN_PROGRESS':
                return self._public(row)
            q = next((q for q in json.loads(row['questions']) if q['id'] == qid), None)
            if not q or answer not in q['options']:
                raise ValueError('Choose one of the options for this question.')
            answers = json.loads(row['answers']); answers[qid] = answer
            row['answers'] = json.dumps(answers)
            conn.execute('UPDATE desktop_exit_challenges SET answers=? WHERE challenge_id=?', (row['answers'], cid))
            return self._public(row)

    def complete(self, cid):
        with self.lock, self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._read(conn, cid)
            if row['status'] == 'IN_PROGRESS':
                qs, answers = json.loads(row['questions']), json.loads(row['answers'])
                if len(qs) != self.QUESTION_COUNT or any(q['id'] not in answers for q in qs):
                    raise ValueError('Answer all 25 questions before submitting.')
                row['score'] = sum(answers[q['id']] == q['correct_answer'] for q in qs)
                row['status'] = 'PASSED' if row['score'] >= self.REQUIRED_CORRECT else 'FAILED'
                until = min(row['deadline'], row['now'] + 120) if row['status'] == 'PASSED' else 0
                row['authorized_until'] = until
                conn.execute('UPDATE desktop_exit_challenges SET status=?,score=?,authorized_until=? WHERE challenge_id=?',
                             (row['status'], row['score'], until, cid))
            return self._public(row)

    def consume(self, cid):
        with self.lock, self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._read(conn, cid)
            if (row['status'] != 'PASSED' or row['score'] < self.REQUIRED_CORRECT
                    or row['now'] >= row['authorized_until']):
                return False
            return conn.execute("UPDATE desktop_exit_challenges SET status='CONSUMED' WHERE challenge_id=? AND status='PASSED'", (cid,)).rowcount == 1
