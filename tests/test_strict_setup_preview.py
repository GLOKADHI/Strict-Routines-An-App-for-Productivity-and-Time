import json
import subprocess
import threading
import urllib.error

import cv2
import numpy as np
import pytest

from core.strict_camera_preview import JS
from windows.strict_setup_preview import SetupPreviewCache
from windows.strict_focus_bridge import request_json
from test_strict_focus_bridge import bridge


def test_cached_preview_expires_clears_and_cannot_reappear_on_clock_rollback():
    now=[0.]
    cache=SetupPreviewCache(clock=lambda:now[0])
    cache.update(dict(current=True,jpeg='image'))
    assert cache.read()==dict(current=True,jpeg='image')
    now[0]=2
    assert cache.read()==dict(current=False,jpeg=None)
    now[0]=0
    assert not cache.read()['current']
    cache.update(dict(current=True,jpeg='image'));cache.update(dict(current=False))
    assert cache.read()['jpeg'] is None
    with pytest.raises(ValueError):cache.update(dict(current=True,jpeg='x'*65537))


def test_setup_preview_bridge_requires_auth_and_does_not_open_camera_or_session(bridge):
    owner,controller=bridge
    url=f'http://127.0.0.1:{owner.server.server_port}/focus/setup_preview'
    with pytest.raises(urllib.error.HTTPError) as error:
        request_json(url,dict(preview=dict(current=True,jpeg='image')),'wrong')
    assert error.value.code==403
    assert owner.api._strict_setup_preview.read()['jpeg'] is None
    assert request_json(url,dict(preview=dict(current=True,jpeg='image')),owner.token)['success']
    assert owner.api._strict_setup_preview.read()['jpeg']=='image'
    controller.open.assert_not_called();controller.update.assert_not_called()


def test_thumbnail_has_visible_face_box_without_changing_detection_input():
    from windows.strict_guard_proctor import StrictGuardProctor
    proctor=StrictGuardProctor.__new__(StrictGuardProctor)
    proctor._lock=threading.RLock();proctor._last_preview_mono=-100
    proctor._preview=None;proctor.face_framing='VISIBLE'
    frame=np.zeros((240,320,3),dtype=np.uint8)
    proctor._store_preview(frame,np.array([[50,40,100,120]]),10)
    import base64
    picture=cv2.imdecode(np.frombuffer(base64.b64decode(proctor._preview),dtype=np.uint8),cv2.IMREAD_COLOR)
    assert picture.shape==(240,320,3) and picture[40,50].max()>50
    assert not frame.any()
    proctor._store_preview(frame,None,10.1)
    assert proctor._last_preview_mono==10
    proctor._store_preview(frame,None,10.14)
    assert proctor._last_preview_mono==10.14


def preview_js(body):
    setup="""
const nodes=new Map();function node(id){if(!nodes.has(id))nodes.set(id,{hidden:false,dataset:{},checked:id==='ssMediaConsent',removeAttribute(name){delete this[name];}});return nodes.get(id);}
global.document={hidden:false,getElementById:node,addEventListener(){}};
global.window={pywebview:{api:{}}};global.currentTab='strict-session';global.strictSetupMonitor=true;
global.strictMediaGeneration=0;global.ssActive=()=>false;global.ssText=()=>{};global.setInterval=()=>{};
"""
    result=subprocess.run(['node','-'],input=setup+JS+'\n'+body,encoding='utf-8',capture_output=True,check=True,timeout=15)
    return json.loads(result.stdout)


def test_camera_feedback_never_marks_unknown_or_stopped_checks_green():
    result=preview_js("""
const data={environment:{hardware_checks:{face:{passed:true},lighting:{passed:false},calibration:{passed:null}}}};
console.log(JSON.stringify({active:ssCameraFeedback(data,true),stopped:ssCameraFeedback(data,false)}));
""")
    assert [item['ok'] for item in result['active']]==[True,False,False]
    assert not any(item['ok'] for item in result['stopped'])


def test_late_frame_cannot_reappear_after_stop_and_no_preview_read_without_consent():
    result=preview_js("""
(async()=>{
 let finish,calls=0;window.pywebview.api.get_strict_setup_preview=()=>{calls++;return new Promise(resolve=>finish=resolve);};
 const pending=updateStrictSetupPreview();strictMediaGeneration++;strictSetupMonitor=false;
 finish({current:true,jpeg:'image'});await pending;
 const afterStop={hidden:node('ssSetupCameraImage').hidden,src:node('ssSetupCameraImage').src||null};
 strictSetupMonitor=true;node('ssMediaConsent').checked=false;await updateStrictSetupPreview();
 console.log(JSON.stringify({afterStop,calls}));
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
    assert result==dict(afterStop=dict(hidden=True,src=None),calls=1)


def test_slow_preview_response_clears_picture_without_starting_overlapping_reads():
    result=preview_js("""
(async()=>{
 let finish,calls=0,deadline;global.setTimeout=callback=>{deadline=callback;return 1;};global.clearTimeout=()=>{};
 window.pywebview.api.get_strict_setup_preview=()=>{calls++;return new Promise(resolve=>finish=resolve);};
 const pending=updateStrictSetupPreview();await updateStrictSetupPreview();deadline();
 const hiddenAtDeadline=node('ssSetupCameraImage').hidden;
 finish({current:true,jpeg:'late'});await pending;
 console.log(JSON.stringify({calls,hiddenAtDeadline,hiddenAfterReply:node('ssSetupCameraImage').hidden}));
})().catch(error=>{console.error(error);process.exitCode=1;});
""")
    assert result==dict(calls=1,hiddenAtDeadline=True,hiddenAfterReply=True)
