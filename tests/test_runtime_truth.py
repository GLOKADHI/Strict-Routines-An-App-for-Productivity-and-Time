"""Regression: URL policy ALLOW must not hide the browser engine's score denial."""
from pathlib import Path
from types import SimpleNamespace,ModuleType
import ast,sys,unittest
from unittest.mock import patch

r=Path(__file__).parent
real=r.parent
sys.path.insert(0,str(real))
import windows.restriction_manager as original
tree=ast.parse((real/'windows/restriction_manager.py').read_text(encoding='utf-8'))
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='YouTubeRestrictionManager')
scope=dict(vars(original))
for name in ('get_desktop_access_status','get_browser_tab_restriction'):
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[method],type_ignores=[]),'<patched manager>','exec'),scope)

def policy(allowed=True):
    return {'effectiveAccessAllowed':allowed,'enforcementStatus':'APPLIED','policyVersion':10,'requestedRestrictionEnabled':False,'policyReason':'Saved administrator release'}
def decision(allowed=False,**extra):
    return dict(restricted=not allowed,allowed=allowed,enforcementStatus='APPLIED',reason='Score -3/80 is below threshold (83 pts needed).',currentScore=-3,requiredScore=80,**extra)

class RuntimeTruthTests(unittest.TestCase):
    def snapshot(self,raw,live):
        calls=[]
        def read(service,**kwargs):calls.append((service,kwargs));return live
        db=SimpleNamespace(get_sidebar_restrictions_status=lambda:{'status':{'youtube':raw,'instagram':raw}})
        manager=SimpleNamespace(db=db,get_browser_tab_restriction=read)
        result=scope['get_desktop_access_status'](manager)
        self.assertEqual(len(calls),2)
        self.assertTrue(all(c[1]=={'force_points_refresh':False,'activate_rewards':False} for c in calls))
        return result['status']['youtube']
    def test_saved_admin_allow_does_not_hide_runtime_score_block(self):
        row=self.snapshot(policy(True),decision(False))
        self.assertFalse(row['effectiveAccessAllowed']);self.assertEqual(row['access'],'BLOCKED')
        self.assertEqual(row['policyVersion'],10);self.assertFalse(row['requestedRestrictionEnabled'])
        self.assertTrue(row['urlPolicyAccessAllowed']);self.assertIn('83 pts needed',row['policyReason'])
    def test_runtime_allow_and_url_block_remains_blocked(self):
        row=self.snapshot(policy(False),decision(True));self.assertFalse(row['effectiveAccessAllowed'])
        self.assertEqual(row['policyReason'],'Saved administrator release')
    def test_every_layer_must_allow(self):
        self.assertTrue(self.snapshot(policy(True),decision(True))['effectiveAccessAllowed'])
    def test_failed_or_unavailable_engine_is_unknown(self):
        for live in (decision(False,unavailable=True),dict(restricted=True,enforcementStatus='FAILED')):
            row=self.snapshot(policy(True),live);self.assertIsNone(row['effectiveAccessAllowed']);self.assertEqual(row['access'],'UNKNOWN')
    def test_pending_registry_cannot_advertise_allow(self):
        row=self.snapshot(dict(policy(True),enforcementStatus='PENDING'),decision(True))
        self.assertIsNone(row['effectiveAccessAllowed'])
    def test_engine_failure_does_not_restore_saved_allow(self):
        def fail(*a,**kw):raise RuntimeError('simulated unavailable engine')
        m=SimpleNamespace(db=SimpleNamespace(get_sidebar_restrictions_status=lambda:{'status':{'youtube':policy(True)}}),get_browser_tab_restriction=fail)
        result=scope['get_desktop_access_status'](m)
        self.assertTrue(result['success']);self.assertEqual(result['status']['youtube']['access'],'UNKNOWN')
    def test_status_check_does_not_activate_available_rewards(self):
        calls=[]
        reward=ModuleType('core.entertainment_reward_engine')
        class FakeRewards:
            def __init__(self,db):pass
            def get_reward_status(self,*a):return {'total_available':1,'available_rewards':[{'entitlement_id':'test'}]}
            def activate_reward(self,*a):calls.append(a);return {'session_active':False}
        reward.EntertainmentRewardEngine=FakeRewards;reward.extract_canonical_video_id=lambda url:None
        cloud=ModuleType('core.youtube_reward_cloud');cloud.client=lambda db:SimpleNamespace(youtube_active=lambda:None)
        db=SimpleNamespace(get_settings=lambda:{},is_wakeup_completed_today=lambda:False,get_active_strict_session=lambda:None)
        m=SimpleNamespace(db=db,educational_unlock_active=False,
            get_points_status=lambda **kw:{'current_score':-3,'required_score':80,'score_available':True},
            get_service_policy=lambda key:{'policyVersion':10,'manuallyRestricted':False},
            _build_canonical_decision=original.YouTubeRestrictionManager._build_canonical_decision)
        # Bind the canonical result builder normally.
        m._build_canonical_decision=original.YouTubeRestrictionManager._build_canonical_decision.__get__(m)
        with patch.dict(sys.modules,{'core.entertainment_reward_engine':reward,'core.youtube_reward_cloud':cloud}):
            checked=scope['get_browser_tab_restriction'](m,'youtube',activate_rewards=False,force_points_refresh=False)
            self.assertTrue(checked['restricted']);self.assertEqual(calls,[])
            scope['get_browser_tab_restriction'](m,'youtube')
            self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main(verbosity=2)
