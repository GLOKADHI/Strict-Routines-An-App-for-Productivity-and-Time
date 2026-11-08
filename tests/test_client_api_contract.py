"""Real isolated HTTP and projection/schema checks; no live identities or grants."""
import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

import jsonschema
import pytest
import requests

from core.client_account import ClientAccountConnection
from core.client_parity import ClientParityService
from core.database import StrictRecordsDB
from core.google_auth import GoogleAuthConfig, GoogleAuthManager
from core.scoring_engine import ScoringEngine
from core.server import StrictRecordsRequestHandler

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_client(tmp_path):
    db = StrictRecordsDB(str(tmp_path / 'contract.sqlite'))
    engine = ScoringEngine(db, storage_root=str(tmp_path / 'evidence'))
    class Handler(StrictRecordsRequestHandler):
        def _get_windows_submission_identity(self):
            return 'user_lok', 'contract-windows'
        def log_message(self, *_):
            pass
    Handler.db, Handler.engine = db, engine
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    auth = GoogleAuthManager(GoogleAuthConfig(str(tmp_path / 'missing-client.json')),
                            session_file=str(tmp_path / 'isolated-session.enc'))
    server.client_account_connection = ClientAccountConnection(db, auth=auth)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client = requests.Session(); client.trust_env = False
    try:
        yield f'http://127.0.0.1:{server.server_port}', client, db, engine
    finally:
        client.close(); server.shutdown(); server.server_close(); worker.join(3)


def test_live_http_projection_matches_published_v1_schema(isolated_client):
    base, client, db, engine = isolated_client
    response = client.get(base + '/api/client/state?date=2026-10-31', timeout=5)
    assert response.status_code == 200
    schema = json.loads((ROOT / 'docs/contracts/client-state-v1.schema.json').read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(response.json(), schema)
    expected = ClientParityService(db, engine).state('user_lok', '2026-10-31')
    # Generated-at timestamps and time-based Gym eligibility are intentionally
    # live. Compare source-derived records rather than two wall-clock instants.
    for key in ('account_id', 'date', 'ledger', 'activities', 'college', 'gym', 'entry_confirmed'):
        assert response.json()[key] == expected[key]
    assert not (Path(db.db_path).parent / 'isolated-session.enc').exists()


@pytest.mark.parametrize('path', [
    '/api/client/state?account_id=another-account',
    '/api/client/calendar?month=2026-10&account_id=another-account',
])
def test_shared_reads_reject_cross_account_queries(isolated_client, path):
    base, client, *_ = isolated_client
    response = client.get(base + path, timeout=5)
    assert response.status_code == 403 and not response.json()['success']


def test_extension_origin_without_paired_credential_is_rejected(isolated_client):
    base, client, *_ = isolated_client
    response = client.get(base + '/api/client/state', headers={
        'Origin': 'chrome-extension://iincmekgilffpjmmmcgloliadppioajc'}, timeout=5)
    assert response.status_code == 403
    assert 'Pair this browser' in response.json()['error']


def test_extension_cannot_start_windows_oauth(isolated_client):
    base, client, *_ = isolated_client
    response = client.post(base + '/api/client/account/sign-in', json={}, headers={
        'Origin': 'chrome-extension://iincmekgilffpjmmmcgloliadppioajc'}, timeout=5)
    assert response.status_code == 403


def test_invalid_command_leaves_points_and_routine_unchanged(isolated_client):
    base, client, db, engine = isolated_client
    def records():
        with db._connect() as conn:
            return {name: [tuple(row) for row in conn.execute('SELECT * FROM '+name)]
                    for name in ('score_events', 'routine_schedules', 'scheduled_activities',
                                 'daily_college_decisions', 'daily_gym_decisions')}
    before = records()
    response = client.post(base + '/api/client/routine', json={
        'date': '2026-10-31', 'request_id': 'contract-invalid-command',
        'attends_college': 'not-a-boolean', 'gym_planned': False}, timeout=5)
    assert response.status_code == 400
    assert records() == before
