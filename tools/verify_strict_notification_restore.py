"""Brief OS policy check with unconditional exact restoration; no study session."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from windows.strict_notification_filter import StrictNotificationFilter,Registry,state_path

if __name__=='__main__':
    if state_path().exists():raise RuntimeError('Existing session owns notification state; do not interfere.')
    registry=Registry();before=registry.read();guard=StrictNotificationFilter()
    result={}
    try:
        result['policy_readback_active']=guard.start()['active']
        result['watchdog_started']=guard.token is not None
    finally:
        result['exact_setting_restored']=guard.restore() and registry.read()==before
        result['recovery_snapshot_cleared']=not state_path().exists()
        result.update(session_started=False,points_modified=False,banner_delivery_tested=False)
        (ROOT/'reports/strict-proctoring-20261103/notification-os-check.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
    assert result['policy_readback_active'] and result['exact_setting_restored'] and result['recovery_snapshot_cleared']
