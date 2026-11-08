from pathlib import Path
import json

import pytest

from core.android_ledger import reconcile
from core.database import StrictRecordsDB
from core.scoring_engine import ScoringEngine

ROOT=Path(__file__).resolve().parents[1]
SHARED=ROOT/'.codex-work/points-ledger-android-shared-fixture.json'


@pytest.mark.skipif(not SHARED.is_file(),reason='Run Android SharedPointsLedgerFixtureTest to generate the shared fixture')
def test_windows_imports_the_exact_android_serialized_ledger(tmp_path):
    generated=json.loads(SHARED.read_text(encoding='utf-8'))
    snapshot=generated['ledger_snapshot']
    payload=json.loads(snapshot['payload_json'])
    db=StrictRecordsDB(str(tmp_path/'shared.db'))
    engine=ScoringEngine(db,str(tmp_path/'proofs'))
    receipt=reconcile(db,snapshot,payload['device_id'],payload['account_id'])
    assert receipt['accepted'] and receipt['sha256']==snapshot['sha256']
    assert engine.calculate_verified_score(payload['date'])==generated['android_calculated_score']==4
    assert db.get_point_ledger_summary(payload['date'])['verified_points']==4
    history=engine.get_unified_transaction_history(payload['date'])
    assert history['confirmed_balance']==4 and history['metrics']['points_gained']==4
    assert {tx['transaction_id'] for tx in history['transactions'] if tx.get('confirmed')}=={ev['event_id'] for ev in payload['score_events']}
