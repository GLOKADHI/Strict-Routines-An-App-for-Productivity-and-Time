path = 'core/ui_dashboard.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update strictStudyHardware display
old_hw = "set('strictStudyHardware',`Camera: ${hw.camera||'OFF'} · Bluetooth audio:"
new_hw = "const dc=hw.dual_camera;const dcText=dc?`Dual Camera: ${dc.composite_label} (Win: ${dc.windows_camera?.processing_fps||15} FPS · iOS: ${dc.ios_camera?.processing_fps||0} FPS) · `:''\n  set('strictStudyHardware',`${dcText}Camera: ${hw.camera||'OFF'} · Bluetooth audio:"

content = content.replace(old_hw, new_hw)

# 2. Update startStrictSessionUI check
old_start_check = "if(typeof strictPreparationData==='undefined'||!strictPreparationData?.ready){showToast('Complete the Windows, iPad and live hardware checks first.','info');return;}"
new_start_check = """if(typeof strictPreparationData==='undefined'||!strictPreparationData?.ready){showToast('Complete the Windows, iPad and live hardware checks first.','info');return;}
  const iosDev=typeof strictPreparationData!=='undefined'?strictPreparationData?.devices?.IOS:null;
  const iosFeedConfirmed=typeof ssIOSCameraStatusData!=='undefined'&&ssIOSCameraStatusData?.status?.streaming===true&&(!!ssIOSCameraStatusData?.preview?.jpeg||iosDev?.camera_ready);
  if(iosDev&&(!iosDev.joined||!iosFeedConfirmed)){showToast('Cannot enter Strict Session: Live video feed from iPhone/iPad has not been confirmed by Windows. Tap Start Camera in Safari.','error');return;}"""

content = content.replace(old_start_check, new_start_check)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Successfully patched core/ui_dashboard.py")
