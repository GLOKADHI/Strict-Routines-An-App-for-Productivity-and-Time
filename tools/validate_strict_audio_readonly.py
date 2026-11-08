"""Read-only endpoint check: never starts monitoring, muting or violations."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from windows.strict_headphone_detector import StrictHeadphoneDetector

if __name__=='__main__':
    detector=StrictHeadphoneDetector()
    value=detector.check_audio_devices()
    result={key:value.get(key) for key in ('detection_known','bluetooth_devices','wired_devices',
        'jack_detection','wired_state_known','prohibited_connected')}
    result.update(monitoring_started=False,speakers_muted=False,violations_created=False)
    report=ROOT/'reports/strict-proctoring-20261103/clear-audio-detection.json'
    report.write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
