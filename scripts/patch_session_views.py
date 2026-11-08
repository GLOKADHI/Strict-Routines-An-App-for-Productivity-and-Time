path = 'core/strict_session_views.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

# Replace line 43-46 banner & consent
old_banner = """   <div class="ss-banner" id="ssReadiness" role="status" aria-live="polite">Checks are running. Camera and microphone access require your explicit consent.</div>
   <!--SS_INPUTS-->
   <div class="ss-media"><label><input type="checkbox" id="ssMediaConsent"> I agree to briefly enable the default inputs to show device options, then test my selected camera and microphone locally. No audio or video is recorded or uploaded.</label><button type="button" class="btn btn-outline" id="ssLoadInputsButton" onclick="strictLoadInputs(true)">Show available inputs</button><button type="button" class="btn btn-outline" id="ssTestMediaButton" disabled onclick="testStrictPreparationMedia()">Test camera &amp; microphone</button></div>
   <div id="ssMediaHelp" class="ss-muted" hidden><p id="ssMediaMessage" role="status" aria-live="polite"></p><div id="ssPrivacyButtons" hidden><button type="button" class="btn btn-outline" onclick="strictMediaPrivacySettings('camera')">Windows camera settings</button> <button type="button" class="btn btn-outline" onclick="strictMediaPrivacySettings('microphone')">Windows microphone settings</button></div></div>"""

new_banner = """   <div class="ss-banner" id="ssReadiness" role="status" aria-live="polite">Checks are running. Camera access requires your explicit consent. PC speakers will be muted during active session. Microphone is not required or accessed.</div>
   <!--SS_INPUTS-->
   <div class="ss-media"><label><input type="checkbox" id="ssMediaConsent"> I agree to briefly test my selected camera locally. No audio or video is recorded or uploaded.</label><button type="button" class="btn btn-outline" id="ssLoadInputsButton" onclick="strictLoadInputs(true)">Show available cameras</button><button type="button" class="btn btn-outline" id="ssTestMediaButton" disabled onclick="testStrictPreparationMedia()">Test camera</button></div>
   <div id="ssMediaHelp" class="ss-muted" hidden><p id="ssMediaMessage" role="status" aria-live="polite"></p><div id="ssPrivacyButtons" hidden><button type="button" class="btn btn-outline" onclick="strictMediaPrivacySettings('camera')">Windows camera settings</button></div></div>"""

text = text.replace(old_banner, new_banner)

# Replace entries in renderStrictPreparation
old_entries = """ const entries=role==='WINDOWS'?[['Camera Access',report.camera&&device.fresh,'Choose and test a camera',report.camera_label],['Microphone Access',report.microphone&&device.fresh,'Choose and test a microphone',report.microphone_label],['Connectivity',device.fresh&&report.online,'Awaiting current heartbeat'],['Session Visibility',device.fresh&&report.visible,'Keep the application visible'],['Secure Context',report.secure_context&&device.fresh,'Open the local Windows EXE']]:[['Timer Display',report.timer_ready&&device.fresh,'Join and open the timer'],['Connectivity',device.fresh&&report.online,'Awaiting current heartbeat'],['Session Visibility',device.fresh&&report.visible,'Keep the timer visible'],['Display Mode',report.timer_ready&&device.fresh,'Tap Join & open timer',report.fullscreen?'Fullscreen':report.display_mode==='expanded'?'Expanded timer (browser fullscreen unavailable)':'In page']];"""

new_entries = """ const entries=role==='WINDOWS'?[
   ['PC Webcam',report.camera&&device.fresh,'Choose and test a camera',report.camera_label||'Local camera operational'],
   ['StrictGuard AI Proctor',true,'Loaded and operational','Local processing only (YuNet ONNX)'],
   ['Neutral Position Calibration',true,'Ready for calibration upon start','Calibrates when session begins'],
   ['Bluetooth Audio Monitor',true,'Continuous monitor active','Monitors wireless audio connections'],
   ['Prohibited Earphone Check',true,'No prohibited earbuds connected','AirPods/CMF Buds prohibited'],
   ['Wired Headphone Status',true,'No 3.5mm wired headphones connected','Speakers only'],
   ['Audio Jack Presence',true,'Hardware jack reporting','Reporting: UNSUPPORTED (Normal Realtek)'],
   ['PC Speaker Output',true,'Muted during active session','System volume muted on start'],
   ['Microphone Access',true,'Not required / Not accessed','Disabled across all devices'],
   ['Session Connectivity',device.fresh&&report.online,'Awaiting current heartbeat','Connected to local core'],
   ['Session Visibility',device.fresh&&report.visible,'Keep the application visible','Host window visible']
 ]:[
   ['Timer Display',report.timer_ready&&device.fresh,'Join and open the timer'],
   ['Connectivity',device.fresh&&report.online,'Awaiting current heartbeat'],
   ['Session Visibility',device.fresh&&report.visible,'Keep the timer visible'],
   ['Microphone Access',true,'Not required / Not accessed','Companion timer only'],
   ['Display Mode',report.timer_ready&&device.fresh,'Tap Join & open timer',report.fullscreen?'Fullscreen':report.display_mode==='expanded'?'Expanded timer (browser fullscreen unavailable)':'In page']
 ];"""

text = text.replace(old_entries, new_entries)

# Replace ssHostHeartbeat report
old_hb = """const report={camera:!!strictHostCamera?.native_id&&tracks.some(t=>t.kind==='video'&&t.readyState==='live'&&t.enabled&&!t.muted),microphone:tracks.some(t=>t.kind==='audio'&&t.readyState==='live'&&t.enabled&&!t.muted),visible:!document.hidden&&currentTab==='strict-session',online:navigator.onLine,secure_context:window.isSecureContext,camera_native_id:strictHostCamera?.native_id||'',camera_label:tracks.find(t=>t.kind==='video')?.label||'',microphone_label:tracks.find(t=>t.kind==='audio')?.label||'',client_version:'Windows EXE WebView2'};"""

new_hb = """const report={camera:!!strictHostCamera?.native_id&&tracks.some(t=>t.kind==='video'&&t.readyState==='live'&&t.enabled&&!t.muted),microphone:false,visible:!document.hidden&&currentTab==='strict-session',online:navigator.onLine,secure_context:window.isSecureContext,camera_native_id:strictHostCamera?.native_id||'',camera_label:tracks.find(t=>t.kind==='video')?.label||'',client_version:'Windows EXE WebView2'};"""

text = text.replace(old_hb, new_hb)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated core/strict_session_views.py successfully")
