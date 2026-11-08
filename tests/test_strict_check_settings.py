"""Exercise actual UI status/review logic without camera, OS controls or ledger."""
import json
import subprocess
from core.strict_check_settings import JS


def run_js(body):
    bootstrap="global.document={getElementById:()=>({addEventListener:()=>{}})};\n"
    completed=subprocess.run(['node','-'],input=bootstrap+JS+'\n'+body,text=True,encoding='utf-8',capture_output=True,check=True,timeout=15)
    return json.loads(completed.stdout)


def test_unavailable_microphone_is_not_a_failed_prerequisite_and_optional_review_is_labelled():
    result=run_js("""
const data={preparation_id:'one',ready:false,environment:{hardware_checks:{physical_jack:{status:'NOT_VERIFIED',passed:null}}}};
const before=JSON.stringify(data);
const pending=ssCheckPresentation('WINDOWS','physical_jack',false,data,1000);
ssRememberCheckReview('WINDOWS','physical_jack',data,1000);
const reviewed=ssCheckPresentation('WINDOWS','physical_jack',false,data,1100);
console.log(JSON.stringify({pending,reviewed,mic:ssCheckPresentation('WINDOWS','microphone',false,data),
  ipadMic:ssCheckPresentation('IOS','microphone',false,data),unchanged:JSON.stringify(data)===before}));
""")
    assert result['pending']['state']=='pending'
    assert result['reviewed']['state']=='manual' and result['reviewed']['symbol']=='✓'
    assert result['mic']['state']==result['ipadMic']['state']=='not_required'
    assert result['unchanged']


def test_manual_reviews_expire_and_cannot_override_failed_camera_mute_or_ipad():
    result=run_js("""
const data={preparation_id:'one',ready:false,environment:{hardware_checks:{webcam:{passed:false},speaker_output:{passed:false},earbud_wearing:{status:'NOT_VERIFIED',passed:null}}}};
const rejected=[];
for(const [role,key] of [['WINDOWS','webcam'],['WINDOWS','speaker_output'],['IOS','timer']]){
 try{ssRememberCheckReview(role,key,data,1000);}catch(e){rejected.push(key);}
}
ssRememberCheckReview('WINDOWS','earbud_wearing',data,1000);
console.log(JSON.stringify({rejected,expired:ssCheckPresentation('WINDOWS','earbud_wearing',false,data,601000),
 camera:ssCheckPresentation('WINDOWS','webcam',false,data,1100),ready:data.ready}));
""")
    assert result['rejected']==['webcam','speaker_output','timer']
    assert result['expired']['state']==result['camera']['state']=='pending'
    assert result['ready'] is False


def test_connection_changes_invalidate_manual_review_and_detected_headsets_cannot_be_acknowledged():
    result=run_js("""
const data={preparation_id:'one',environment:{hardware_checks:{earbud_wearing:{passed:null,status:'NOT_VERIFIED'},prohibited_bluetooth:{status:'PASS'}}}};
ssRememberCheckReview('WINDOWS','earbud_wearing',data,1000);
data.environment.hardware_checks.prohibited_bluetooth={status:'ACTION_REQUIRED',detected_device:'AirPods'};
const invalid=ssCheckPresentation('WINDOWS','earbud_wearing',false,data,1100);
let rejected=false;try{ssRememberCheckReview('WINDOWS','earbud_wearing',data,1200);}catch(e){rejected=true;}
data.environment.hardware_checks.prohibited_bluetooth={status:'PASS'};
console.log(JSON.stringify({invalid,rejected,after:ssCheckPresentation('WINDOWS','earbud_wearing',false,data,1300)}));
""")
    assert result['invalid']['state']==result['after']['state']=='pending'
    assert result['rejected']


def test_new_preparation_and_clock_rollback_invalidate_manual_review():
    result=run_js("""
const data={preparation_id:'one',environment:{hardware_checks:{physical_jack:{passed:null,status:'NOT_VERIFIED'}}}};
ssRememberCheckReview('WINDOWS','physical_jack',data,1000);data.preparation_id='two';
const different=ssCheckPresentation('WINDOWS','physical_jack',false,data,1100);
ssRememberCheckReview('WINDOWS','physical_jack',data,1000);
console.log(JSON.stringify({different,backwards:ssCheckPresentation('WINDOWS','physical_jack',false,data,999)}));
""")
    assert result['different']['state']==result['backwards']['state']=='pending'


def test_unknown_wired_detection_is_not_automatic_success_and_supported_jack_is_verified():
    from windows.strict_preflight import hardware_checks
    unknown=hardware_checks(dict(audio_detection_known=False,wired_headphone_connected=False))
    assert unknown['wired_headphones']['passed'] is False
    known=hardware_checks(dict(audio_detection_known=True,wired_state_known=True,jack_detection='SUPPORTED'))
    assert known['physical_jack']['passed'] is True
