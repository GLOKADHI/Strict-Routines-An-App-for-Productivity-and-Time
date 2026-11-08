from pathlib import Path
import json
import subprocess
import sys
from datetime import datetime
from core.gym_mode_engine import is_within_activation_window,calculate_access_expiry
from core.daily_college_decision import DailyCollegeDecision

ROOT=Path(__file__).resolve().parents[1]
def test_shared_android_backend_vectors():
    cases=json.loads((ROOT/'shared/planning_contract_vectors.json').read_text())
    for v in cases['activation']:
        at=datetime.fromisoformat(v['at'])
        assert is_within_activation_window(at)==v['allowed']
        if 'expiry' in v:assert calculate_access_expiry(at)==datetime.fromisoformat(v['expiry'])
    for v in cases['bath_reservations']:
        assert DailyCollegeDecision.reserved_hourly_target(v['planned'])==v['target']

def test_generated_android_policy_has_no_drift():
    subprocess.run([sys.executable,str(ROOT/'scripts/generate_product_policy.py'),'--check'],check=True)
