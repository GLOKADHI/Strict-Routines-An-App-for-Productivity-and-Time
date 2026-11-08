"""Build only session integrity modules and the existing React frontend."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import build_zip_frontend_release as build

build.REPORT=build.ROOT/'reports/strict-session-integrity-20261103'
build.RELEASE=build.ROOT/'releases/StrictRecords-SessionIntegrity-20261103/Windows/StrictRecords'
build.WORK=build.ROOT/'.codex-work/session-integrity-20261103'
build.MODULES=build.MODULES+('windows.strict_guard_proctor','windows.strict_integrity_events',
    'windows.strict_screen_monitor','windows.strict_visual_objects','windows.strict_study_hardware',
    'windows.strict_study_workflow','windows.strict_demo_workflow','windows.strict_session_manager','windows.strict_focus_bridge')
build.PATCH_METHODS={'windows.strict_session_manager':{
    'StrictSessionManager.start_session','StrictSessionManager._install_keyboard_hook',
    'StrictSessionManager._check_and_enforce_foreground_process',
    'StrictSessionManager._live_study_hardware','StrictSessionManager._tick_study'}}
build.EXTRA_ASSETS=('assets/vision/phone_detection_yolox_2022nov.onnx','assets/vision/LICENSE-YOLOX.txt','assets/vision/SOURCE-YOLOX.json')
if __name__=='__main__':build.main()
