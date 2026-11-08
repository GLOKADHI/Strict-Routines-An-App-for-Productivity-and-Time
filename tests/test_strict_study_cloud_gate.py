import pytest
from unittest.mock import patch
from cloud_points.youtube_rewards import strict_study, activate
from tests.test_single_youtube_cloud import store, principal, grant, submission, NOW


def test_android_and_extension_cannot_claim_before_manual_windows_finalization(store):
    body=dict(session_id='strict-1',activity_id=submission()['activity_id'],submission_id='sub-1')
    strict_study(store,principal(store,'exe'),'begin',body)
    rid=grant(store)['available_rewards'][0]['reward_id']
    with patch('cloud_points.youtube_rewards._now',return_value=NOW):
        for device in ['apk','exe','extension']:
            with pytest.raises(ValueError,match='manual Windows'):activate(store,principal(store,device),rid)
        with pytest.raises(PermissionError):strict_study(store,principal(store,'apk'),'finalize',body)
        with pytest.raises(PermissionError):strict_study(store,principal(store,'extension'),'finalize',body)
        strict_study(store,principal(store,'exe'),'finalize',body)
        first=activate(store,principal(store,'exe'),rid)['active_session']
        strict_study(store,principal(store,'exe'),'finalize',body)
        again=activate(store,principal(store,'exe'),rid)['active_session']
        assert first==again and first['expiry_ms']-first['activation_ms']==1200000


def test_abandoned_session_never_receives_its_corresponding_reward(store):
    body=dict(session_id='strict-1',activity_id=submission()['activity_id'])
    strict_study(store,principal(store,'exe'),'begin',body)
    rid=grant(store)['available_rewards'][0]['reward_id']
    strict_study(store,principal(store,'exe'),'abort',body)
    with patch('cloud_points.youtube_rewards._now',return_value=NOW):
        with pytest.raises(ValueError):activate(store,principal(store),rid)
    with pytest.raises(ValueError):strict_study(store,principal(store,'exe'),'finalize',dict(body,submission_id='sub-1'))


def test_changed_schedule_old_hold_is_closed_and_completion_receipt_is_immutable(store):
    old=dict(session_id='strict-1',activity_id='previous-window')
    new=dict(session_id='strict-1',activity_id=submission()['activity_id'],submission_id='sub-1')
    strict_study(store,principal(store,'exe'),'begin',old)
    strict_study(store,principal(store,'exe'),'begin',new)
    rid=grant(store)['available_rewards'][0]['reward_id']
    strict_study(store,principal(store,'exe'),'finalize',new)
    strict_study(store,principal(store,'exe'),'begin',new)  # retry does not reopen
    with pytest.raises(ValueError):strict_study(store,principal(store,'exe'),'finalize',dict(new,submission_id='unrelated'))
    with patch('cloud_points.youtube_rewards._now',return_value=NOW):assert activate(store,principal(store,'exe'),rid)['active_session']
