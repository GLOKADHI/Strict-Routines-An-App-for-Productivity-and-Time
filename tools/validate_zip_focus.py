"""Validate the actual ZIP React UI inside the explicit offline EXE fixture.

Uses only the application's opt-in loopback diagnostic endpoint and WebView2's
own DOM evaluation/capture. No desktop input, camera, production session or
Computer Use is involved. The receipt is bound to the tested executable hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


SIZES = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080))


class Fixture:
    def __init__(self, folder: Path):
        self.folder = folder.resolve()
        self.connection = json.loads((self.folder / 'connection.json').read_text(encoding='utf-8'))
        port = self.connection['port']
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('Invalid isolated fixture port')
        self.origin = f'http://127.0.0.1:{port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path: str, body=None, timeout=35):
        request = urllib.request.Request(
            self.origin + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'},
            method='GET' if body is None else 'POST',
        )
        with self.opener.open(request, timeout=timeout) as response:
            return json.load(response)

    def event(self, action, **body):
        result = self.request('/validation/event', {'action': action, **body})
        if not result.get('success'):
            raise AssertionError(f'{action} failed: {result}')
        return result

    def script(self, script):
        result = self.event('zip_script', script=script)
        value = result.get('value')
        # WebView2 serializes JS results. Accept either a decoded result or the
        # JSON string form so this tool works across the diagnostic host builds.
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return value

    def wait_script(self, script, *, timeout=15):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.script(script)
            if last:
                return last
            time.sleep(.12)
        raise AssertionError(f'Frontend did not settle: {script[:180]} -> {last!r}')

    def click(self, testid):
        found = self.script(
            '(()=>{const el=document.querySelector(' + json.dumps(f'[data-testid="{testid}"]') +
            ');if(!el||el.disabled)return false;el.click();return true})()'
        )
        assert found, f'Missing or disabled functional control: {testid}'

    def native_state(self):
        result = self.request('/validation/focus-frontend')
        assert result.get('success'), f'Native frontend snapshot failed: {result}'
        return result


DOM_METRICS = r"""(()=>{
 const visible=el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden'&&getComputedStyle(el).display!=='none'};
 const rect=el=>{if(!el)return null;const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
 const controls=[...document.querySelectorAll('button')].filter(visible).map(el=>({
   id:el.dataset.testid||el.getAttribute('aria-label')||el.title||el.textContent.trim(),
   rect:rect(el),disabled:el.disabled,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth
 }));
 const slots=[...document.querySelectorAll('[data-native-pane]')].filter(visible).map(el=>({pane:el.dataset.nativePane,rect:rect(el)}));
 const camera=document.querySelector('[data-testid="camera-pip"]');
 return {width:innerWidth,height:innerHeight,devicePixelRatio,overflowX:document.documentElement.scrollWidth>innerWidth,
   title:document.title,text:document.body.innerText,controls,slots,camera:camera&&visible(camera)?rect(camera):null,
   state:window.StrictRecordsBridge?.getStateSnapshot?.()||null,
   runtime:window.StrictRecordsRuntime?.getState?.()||null};
})()"""


def ensure_no_fake_monitoring(metrics):
    state = metrics.get('state') or {}
    if not state:
        raise AssertionError('The imported React provider did not expose its actual state snapshot')
    camera = state.get('camera') or {}
    proctor = state.get('proctoring') or {}
    capture = state.get('screenCapture') or {}
    assert not camera.get('isStreaming'), 'Offline fixture claims a live camera'
    assert camera.get('sourceType') != 'DEMO_SIMULATED', 'Synthetic camera feed was enabled in the EXE'
    assert not proctor.get('monitoringActive'), 'Offline fixture claims active monitoring'
    assert not proctor.get('screenProctoringEnabled'), 'Offline fixture claims screen proctoring'
    assert not capture.get('screenShareActive'), 'Offline fixture claims screen capture'
    assert 'OFFLINE' in metrics['text'].upper() or 'DEMO' in metrics['text'].upper()
    return True


def ensure_geometry(metrics):
    width, height = metrics['width'], metrics['height']
    assert not metrics['overflowX'], f'Document overflows horizontally at {width}'
    for item in metrics['controls']:
        r = item['rect']
        assert r['x'] >= -1 and r['y'] >= -1, f'Control clipped above/left: {item["id"]}'
        assert r['right'] <= width + 1 and r['bottom'] <= height + 1, f'Control outside viewport: {item["id"]}'
    slots = metrics['slots']
    assert len(slots) == 2, f'Expected two actual native browser slots at {width}: {slots}'
    assert abs(slots[0]['rect']['width'] - slots[1]['rect']['width']) <= 2, f'Default panes not equal at {width}'
    assert min(s['rect']['height'] for s in slots) > 180, f'Browser viewport is not usable at {width}'
    camera = metrics['camera']
    if camera:
        assert camera['x'] >= 11 and camera['y'] >= 11, f'Camera escapes top/left at {width}'
        assert camera['right'] <= width - 11 and camera['bottom'] <= height - 11, f'Camera escapes bottom/right at {width}'
    return True


def validate(folder: Path):
    import psutil
    fixture = Fixture(folder)
    executable = Path(psutil.Process(fixture.connection['pid']).exe()).resolve()
    result = {
        'success': False,
        'executable': str(executable),
        'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
        'frozen': fixture.connection.get('frozen'),
        'production_session_started': False,
        'live_camera_opened': False,
        'desktop_input_injected': False,
        'computer_use_used': False,
        'responsive_sizes': [],
    }
    output = fixture.folder / 'zip-native-functional-checks.json'
    try:
        try:
            existing = fixture.request('/validation/focus-frontend')
        except (OSError, urllib.error.URLError):
            existing = {}
        if not (existing.get('success') and not existing.get('closed') and existing.get('frontend') == 'supplied-react-zip'):
            fixture.event('focus_layout')
        fixture.wait_script("Boolean(window.StrictRecordsBridge?.getStateSnapshot?.()&&document.querySelector('[data-native-pane]'))")
        fixture.event('focus_frontend_demo')
        fixture.wait_script("Boolean(window.StrictRecordsBridge?.getStateSnapshot?.().meta.hostBridgeConnected)&&window.StrictRecordsBridge.getStateSnapshot().timer.remainingSeconds===599")
        host_remaining = fixture.script('window.StrictRecordsBridge.getStateSnapshot().timer.remainingSeconds')
        for width, height in SIZES:
            fixture.event('zip_resize', width=width, height=height)
            fixture.wait_script(f'Math.abs(innerWidth*devicePixelRatio-{width})<=2&&Math.abs(innerHeight*devicePixelRatio-{height})<=2')
            time.sleep(.16)
            metrics = fixture.script(DOM_METRICS)
            ensure_no_fake_monitoring(metrics)
            ensure_geometry(metrics)
            result['responsive_sizes'].append({
                'width': width, 'height': height,
                'css_width': metrics['width'], 'css_height': metrics['height'],
                'device_pixel_ratio': metrics['devicePixelRatio'],
                'equal_split': True, 'controls_inside_viewport': True,
                'no_horizontal_overflow': True, 'offline_monitoring_truthful': True,
                'slots': metrics['slots'], 'camera': metrics['camera'],
            })
        fixture.event('zip_resize', width=1600, height=900)
        fixture.wait_script('Math.abs(innerWidth*devicePixelRatio-1600)<=2&&Math.abs(innerHeight*devicePixelRatio-900)<=2')
        time.sleep(.16)
        # Control test IDs are the imported components' real event handlers.
        # The frontend team keeps these stable for native integration tests.
        fixture.click('layout-swap')
        fixture.click('layout-restore')
        fixture.click('layout-chatgpt')
        fixture.wait_script("[...document.querySelectorAll('[data-native-pane]')].filter(e=>e.getBoundingClientRect().width>0&&getComputedStyle(e).display!=='none').length===1")
        fixture.click('layout-leetcode')
        fixture.click('layout-restore')
        fixture.wait_script("[...document.querySelectorAll('[data-native-pane]')].filter(e=>e.getBoundingClientRect().width>0&&getComputedStyle(e).display!=='none').length===2")
        result['shared_layout_controls_work'] = True
        fixture.click('monitoring-toggle')
        fixture.wait_script("Boolean(document.querySelector('[data-testid=\"monitoring-details\"]'))")
        fixture.click('monitoring-toggle')
        result['diagnostics_expand_and_collapse'] = True
        fixture.click('camera-hide')
        fixture.wait_script("!document.querySelector('[data-testid=\"camera-pip\"]')")
        fixture.click('camera-restore')
        fixture.wait_script("Boolean(document.querySelector('[data-testid=\"camera-pip\"]'))")
        result['camera_hide_and_restore_work'] = True
        before = fixture.script("(()=>{const r=document.querySelector('[data-testid=\"camera-pip\"]').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})()")
        fixture.script(r"""(()=>{
          const el=document.querySelector('[data-testid="camera-drag"]');const r=el.getBoundingClientRect();
          const opts={bubbles:true,pointerId:19,pointerType:'mouse',isPrimary:true,button:0,buttons:1,clientX:r.x+40,clientY:r.y+12};
          el.dispatchEvent(new PointerEvent('pointerdown',opts));return true;
        })()""")
        fixture.script(r"""(()=>{
          const el=document.querySelector('[data-testid="camera-drag"]');const r=el.getBoundingClientRect();
          window.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerId:19,pointerType:'mouse',isPrimary:true,buttons:1,clientX:r.x-20,clientY:r.y+55}));
          window.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerId:19,pointerType:'mouse',isPrimary:true,button:0,buttons:0,clientX:r.x-20,clientY:r.y+55}));return true;
        })()""")
        time.sleep(.12)
        after = fixture.script("(()=>{const r=document.querySelector('[data-testid=\"camera-pip\"]').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})()")
        assert before['x'] != after['x'] or before['y'] != after['y'], 'Camera drag handler did not move the actual PiP'
        result['camera_drag_works'] = True
        resize_before = after
        fixture.script(r"""(()=>{
          const el=document.querySelector('[data-testid="camera-resize-se"]');const r=el.getBoundingClientRect();
          el.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerId:20,pointerType:'mouse',isPrimary:true,button:0,buttons:1,clientX:r.x+2,clientY:r.y+2}));return true;
        })()""")
        fixture.script(r"""(()=>{
          const el=document.querySelector('[data-testid="camera-resize-se"]');const r=el.getBoundingClientRect();
          window.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,pointerId:20,pointerType:'mouse',isPrimary:true,buttons:1,clientX:r.x+65,clientY:r.y+38}));
          window.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerId:20,pointerType:'mouse',isPrimary:true,button:0,buttons:0,clientX:r.x+65,clientY:r.y+38}));return true;
        })()""")
        time.sleep(.12)
        after = fixture.script("(()=>{const r=document.querySelector('[data-testid=\"camera-pip\"]').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})()")
        assert abs(resize_before['width'] - after['width']) >= 2, 'Camera resize handler did not change the actual PiP'
        assert abs(after['height'] - after['width'] * 9 / 16) <= 2, 'Camera ratio was not preserved'
        ensure_geometry(fixture.script(DOM_METRICS))
        result['camera_resize_and_bounds_work'] = True
        assert fixture.script('window.StrictRecordsBridge.getStateSnapshot().timer.remainingSeconds') == host_remaining, 'The React UI advanced a host-authoritative timer without a host update'
        result['timer_is_host_authoritative'] = True
        # Render production loading/error content using the real imported
        # components. The diagnostic native controller remains offline, so a
        # Retry command can be observed without opening an external website.
        before_commands = len(fixture.native_state().get('commands', []))
        fixture.script(r"""(()=>{
          const rt=window.StrictRecordsRuntime;window.__validationSavedRuntime=rt.getState();
          rt.apply({...rt.getState(),offline:false,browser:{...rt.getState().browser,
            chatgpt:{state:'loading',url:'https://chatgpt.com/',error:''}}});return true;
        })()""")
        fixture.wait_script("document.querySelector('[data-native-pane=\"chatgpt\"]')?.dataset.state==='loading'&&document.body.innerText.includes('LOADING APPROVED WEBSITE')")
        result['production_loading_state_visible'] = True
        fixture.script(r"""(()=>{
          const rt=window.StrictRecordsRuntime;
          rt.apply({...rt.getState(),offline:false,browser:{...rt.getState().browser,
            chatgpt:{state:'error',url:'https://chatgpt.com/',error:'Isolated connection-recovery fixture'}}});return true;
        })()""")
        fixture.wait_script("document.querySelector('[data-native-pane=\"chatgpt\"]')?.dataset.state==='error'&&Boolean(document.querySelector('[data-testid=\"retry-chatgpt\"]'))&&document.body.innerText.includes('Isolated connection-recovery fixture')")
        fixture.click('retry-chatgpt')
        deadline = time.monotonic() + 5
        retry_observed = False
        while time.monotonic() < deadline:
            commands = fixture.native_state().get('commands', [])[before_commands:]
            if any(item.get('type') == 'NAVIGATE' and item.get('pane') == 'chatgpt' and item.get('action') == 'reload' for item in commands):
                retry_observed = True
                break
            time.sleep(.12)
        assert retry_observed, 'The actual React Retry control did not send reload to the native host'
        assert fixture.native_state()['browser']['chatgpt']['state'] == 'offline', 'The offline host unexpectedly navigated an external website'
        fixture.script("(()=>{window.StrictRecordsRuntime.apply(window.__validationSavedRuntime);delete window.__validationSavedRuntime;return true})()")
        fixture.wait_script("document.querySelector('[data-native-pane=\"chatgpt\"]')?.dataset.state==='offline'")
        result['production_error_and_retry_bridge_work'] = True
        result['retry_test_opened_external_site'] = False
        assert fixture.script("document.querySelectorAll('[data-testid=\"session-end\"]').length") == 1, 'Duplicate footer end controls'
        fixture.event('zip_capture', name='strict-session-zip-final.png')
        fixture.click('session-end')
        fixture.wait_script("Boolean(document.querySelector('dialog[open]'))")
        fixture.click('session-end-confirm')
        deadline = time.monotonic() + 8
        closed = None
        while time.monotonic() < deadline:
            closed = fixture.native_state()
            if closed.get('closed'):
                break
            time.sleep(.12)
        assert closed and closed.get('closed') and closed.get('completion_calls') == 1, f'The actual demo completion callback failed: {closed}'
        result['end_dialog_calls_existing_completion_once'] = True
        result['success'] = True
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.folder), indent=2))


if __name__ == '__main__':
    main()
