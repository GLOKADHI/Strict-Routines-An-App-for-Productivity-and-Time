"""Pure access-decision checks: no production database, registry or services."""
from types import SimpleNamespace
import pytest
from windows.restriction_manager import YouTubeRestrictionManager
from core.restriction_targets import TARGETS


def manager(score, wake=True, manual=False):
    obj=YouTubeRestrictionManager.__new__(YouTubeRestrictionManager)
    obj.current_score=score
    obj.educational_unlock_active=False
    obj.db=SimpleNamespace(get_settings=lambda:SimpleNamespace(reward_threshold=80,controlled_mode_threshold=60),
        get_active_youtube_reward=lambda:None,get_gym_mode_state=lambda:None,
        is_wakeup_completed_today=lambda:wake,get_active_strict_session=lambda:None)
    obj.get_points_status=lambda force=False:{'score_available':True,'current_score':score,'required_score':80}
    obj.get_service_policy=lambda service:{'manuallyRestricted':manual,'policyVersion':3}
    return obj


@pytest.mark.parametrize('service',list(TARGETS))
@pytest.mark.parametrize('score',[0,59,60,61,79,80,81,100])
def test_each_website_uses_its_own_inclusive_threshold(service,score):
    obj=manager(score)
    value=obj.get_browser_tab_restriction(service)
    threshold=80 if service in ('youtube','instagram') else 60
    assert value['requiredScore']==threshold
    assert value['currentScore']==score
    assert value['scoreEligible']==(score>=threshold)
    assert value['allowed']==(score>=threshold)
    assert value['restricted']==(score<threshold)


def test_score_eligibility_is_not_reported_as_a_wakeup_or_manual_failure():
    for wake,manual,reason in [(False,False,'Wake-up protocol'),(True,True,'user preference')]:
        value=manager(95,wake,manual).get_browser_tab_restriction('instagram')
        assert value['scoreEligible'] is True and value['restricted'] is True
        assert reason in value['reason']


def test_music_has_no_wakeup_gate():
    for service in ('spotify','gaana','jiosaavn','masstamilan'):
        assert manager(60,wake=False).get_browser_tab_restriction(service)['allowed'] is True


def test_unavailable_points_are_never_turned_into_zero_or_allowed():
    obj=manager(100)
    obj.get_points_status=lambda force=False:{'score_available':False}
    value=obj.get_browser_tab_restriction('spotify')
    assert value['currentScore'] is None and value['unavailable'] and value['restricted']


def test_exception_keeps_actual_score_eligibility():
    obj=manager(10)
    value=obj._build_canonical_decision('spotify','Spotify',60,True,'Active session','ALLOW',temporary_exception='GYM_MODE')
    assert value['allowed'] is True and value['scoreEligible'] is False


def test_endpoint_returns_a_structured_service_error_instead_of_dropping_socket():
    from core.server import StrictRecordsRequestHandler
    obj=object.__new__(StrictRecordsRequestHandler)
    obj.path='/api/restriction/browser_status?service=spotify&url=https%3A%2F%2Fopen.spotify.com%2F&browser=comet'
    obj.headers={}
    obj.client_address=('127.0.0.1',12345)
    def fail(*args):raise RuntimeError('fixture decision error')
    obj.restriction_mgr=SimpleNamespace(note_browser_extension_heartbeat=lambda *_:None,get_browser_tab_restriction=fail)
    replies=[]
    obj._send_json=lambda data,**kwargs:replies.append((data,kwargs))
    obj.do_GET()
    assert replies[-1][1]['status']==503
    assert replies[-1][0]['code']=='DECISION_SERVICE_ERROR'
    assert replies[-1][0]['restricted'] is True


def test_snapshot_score_is_not_changed_by_another_request():
    obj=manager(100)
    value=obj._build_canonical_decision('spotify','Spotify',60,False,'Below threshold','BLOCK',current_score=59)
    assert value['scoreEligible'] is False and value['currentScore']==59


def test_preparing_update_preserves_browser_preferences_and_monitor_enrollment(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from windows.browser_setup import BrowserExtensionSetup
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    profile=tmp_path/'local/Perplexity/Comet/User Data/Default'
    profile.mkdir(parents=True)
    preferences={'extensions':{'ui':{'developer_mode':False}}}
    (profile/'Preferences').write_text(json.dumps(preferences))
    protected={'extensions':{'settings':{'old-id':{'path':'StrictRecords/browser_extension/old'}}}}
    (profile/'Secure Preferences').write_text(json.dumps(protected))
    browser={'id':'prepared-comet','key':'comet','name':'Comet'}
    setup=BrowserExtensionSetup(source=Path(__file__).resolve().parents[1]/'browser_extension',storage=tmp_path/'prepared',detector=lambda:[])
    folder=setup._folder(browser);folder.mkdir(parents=True)
    monitor={'enrollment_id':'retained-monitor'}
    config={'browser_key':'comet','installation_id':'prepared-comet','monitor':monitor}
    (folder/'setup.json').write_text(json.dumps(config))
    setup._prepare(browser)
    assert json.loads((profile/'Preferences').read_text())==preferences
    assert json.loads((profile/'Secure Preferences').read_text())==protected
    assert json.loads((folder/'setup.json').read_text())['monitor']==monitor
    assert (folder/'ui.css').is_file() and (folder/'college_planning.js').is_file()
