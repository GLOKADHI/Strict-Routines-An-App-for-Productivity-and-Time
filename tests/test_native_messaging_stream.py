"""Real native framing regression: the browser keeps stdin open for the reply."""
from pathlib import Path
import json
import queue
import struct
import subprocess
import threading
import pytest

BRIDGE=Path(__file__).resolve().parents[1]/'windows/StrictRecordsNativeBridge.exe'
ORIGIN='chrome-extension://iincmekgilffpjmmmcgloliadppioajc/'

@pytest.mark.skipif(not BRIDGE.is_file(),reason='Windows native bridge is not built')
def test_native_replies_before_stdin_eof_and_handles_multiple_frames():
    p=subprocess.Popen([str(BRIDGE),ORIGIN],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(2):
            payload=json.dumps({'action':'ping'}).encode()
            p.stdin.write(struct.pack('<I',len(payload))+payload);p.stdin.flush()
            output=queue.Queue()
            def read():
                size=p.stdout.read(4)
                output.put(json.loads(p.stdout.read(struct.unpack('<I',size)[0])) if len(size)==4 else None)
            threading.Thread(target=read,daemon=True).start()
            assert output.get(timeout=12)['success']
            assert p.poll() is None
        p.stdin.close();assert p.wait(5)==0
    finally:
        if p.poll() is None:p.terminate();p.wait(5)

@pytest.mark.skipif(not BRIDGE.is_file(),reason='Windows native bridge is not built')
def test_native_rejects_a_different_extension_origin():
    result=subprocess.run([str(BRIDGE),'chrome-extension://wrong/'],input=b'',capture_output=True,timeout=5)
    assert result.returncode==2 and not result.stdout
