from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from core.server import StrictRecordsRequestHandler


def test_remote_and_foreign_origin_cannot_start_or_complete_study():
    for address,headers in [('192.168.1.7',{'Host':'127.0.0.1:8765','X-StrictRoutines-Study':'1'}),
        ('127.0.0.1',{'Host':'127.0.0.1:8765','X-StrictRoutines-Study':'1','Origin':'https://unrelated.example'}),
        ('127.0.0.1',{'Host':'127.0.0.1:8765'})]:
        request=object.__new__(StrictRecordsRequestHandler)
        request.client_address=(address,1234);request.headers=headers;request._send_json=Mock()
        assert not request._strict_study_local_action()
        assert request._send_json.call_args.kwargs['status']==403
    request.client_address=('127.0.0.1',1234)
    request.headers={'Host':'127.0.0.1:8765','X-StrictRoutines-Study':'1','Origin':'http://127.0.0.1:8765'}
    assert request._strict_study_local_action()
