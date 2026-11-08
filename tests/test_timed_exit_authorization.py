"""Disposable database only: explicit exit must never trust client completion."""
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import sqlite3
import threading
from unittest.mock import Mock, patch

import pytest
from core.exit_challenge import ExitChallenge


class Store:
    def __init__(self, path):
        self.path = path
        self.record_audit_log = Mock()
        with self._connect() as conn:
            conn.executescript('CREATE TABLE points(value INTEGER); INSERT INTO points VALUES(17);')

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10); conn.row_factory = sqlite3.Row
        try:
            with conn: yield conn
        finally: conn.close()


@pytest.fixture
def setup(tmp_path):
    db = Store(tmp_path/'exit.sqlite'); now = [10000.0]
    service = ExitChallenge(db, clock=lambda:now[0])
    yield db, service, now
    with db._connect() as conn:
        assert conn.execute('SELECT value FROM points').fetchone()[0] == 17


def solve(service, db, correct=25):
    public = service.start('1234'); cid = public['challenge_id']
    with db._connect() as conn:
        qs = json.loads(conn.execute('SELECT questions FROM desktop_exit_challenges WHERE challenge_id=?',(cid,)).fetchone()[0])
    for i,q in enumerate(qs):
        answer = q['correct_answer'] if i < correct else next(a for a in q['options'] if a != q['correct_answer'])
        service.answer(cid,q['id'],answer)
    return cid, service.complete(cid)


def test_pin_required_and_no_answers_or_raw_json_leaked(setup):
    _, service, _ = setup
    for pin in ('wrong',None,1234,'1234 '):
        with pytest.raises(ValueError): service.start(pin)
    result = service.start('1234')
    assert result['total_questions'] == 25 and result['seconds_remaining'] == 1500
    assert result['required_correct'] == 24
    assert 'correct_answer' not in json.dumps(result) and 'questions_json' not in result
    assert not service.consume(result['challenge_id'])


def test_all_answers_required_and_invalid_answers_rejected(setup):
    _, service, _ = setup; result=service.start('1234'); cid=result['challenge_id']; q=result['questions'][0]
    with pytest.raises(ValueError): service.answer(cid,'invented',q['options'][0])
    with pytest.raises(ValueError): service.answer(cid,q['id'],'invented')
    service.answer(cid,q['id'],q['options'][0])
    with pytest.raises(ValueError,match='all 25'): service.complete(cid)
    assert not service.consume(cid)


@pytest.mark.parametrize('correct,passed',[(0,False),(23,False),(24,True),(25,True)])
def test_actual_score_enforced(setup,correct,passed):
    db,service,_=setup; cid,result=solve(service,db,correct)
    assert result['score']==correct and result['passed'] is passed
    assert result['percentage']==correct*4 and result['points_impact']==0
    assert (result['authorized_until'] > result['server_time']) is passed
    assert service.consume(cid) is passed
    assert not service.consume(cid)


def test_timer_resumes_across_restart_and_requires_pin_again(setup):
    db,service,now=setup; first=service.start('1234'); q=first['questions'][0]
    service.answer(first['challenge_id'],q['id'],q['options'][1]); now[0]+=600
    second=ExitChallenge(db,clock=lambda:now[0])
    with pytest.raises(ValueError): second.get(first['challenge_id'])
    resumed=second.start('1234')
    assert resumed['challenge_id']==first['challenge_id'] and resumed['deadline']==first['deadline']
    assert resumed['seconds_remaining']==900 and resumed['answers'][q['id']]==q['options'][1]
    now[0]=first['deadline']
    assert second.complete(first['challenge_id'])['status']=='EXPIRED'
    assert not second.consume(first['challenge_id'])
    assert second.start('1234')['challenge_id']!=first['challenge_id']


def test_no_clock_rollback_extension_and_no_expired_grant(setup):
    db,service,now=setup; cid,result=solve(service,db)
    now[0]+=60; service.get(cid); now[0]-=1000
    assert service.get(cid)['seconds_remaining']==1440
    now[0]=result['server_time']+120
    assert not service.consume(cid)
    assert service.start('1234')['challenge_id']!=cid


def test_grant_consumed_only_once_under_concurrency(setup):
    db,service,_=setup; cid,_=solve(service,db)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _:service.consume(cid),range(8))).count(True)==1


def api_class():
    tree=ast.parse(Path('desktop_app.py').read_text(encoding='utf-8-sig'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DesktopApi')
    ns={'threading':threading,'logger':logging.getLogger('exit-fixture')}
    exec(compile(ast.Module(body=[cls],type_ignores=[]),'isolated-desktop-api','exec'),ns)
    return ns['DesktopApi']


def test_all_normal_entry_points_require_graded_challenge(setup):
    db,service,_=setup; api=api_class()(); api._db=db; api._exit_challenge=service
    with patch.object(api,'_begin_shutdown',return_value={'success':True}) as stop, patch.object(api,'show_dashboard'):
        assert not api.exit_app('wrong')['success']
        assert api.exit_app('1234')['status']=='challenge_required'
        assert not api.exit_via_mcq('forged')['success']
        cid=service.start('1234')['challenge_id']
        assert not api.exit_via_mcq(cid)['success']
        stop.assert_not_called(); db.record_audit_log.assert_not_called()
        solve(service,db,24)
        assert api.exit_via_mcq(cid)['success']; stop.assert_called_once()
        db.record_audit_log.assert_called_once()


def test_http_exit_requires_local_origin_header_and_shared_gate():
    tree=ast.parse(Path('core/server.py').read_text(encoding='utf-8-sig'))
    method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_handle_exit_check')
    ns={};exec(compile(ast.Module(body=[method],type_ignores=[]),'isolated-exit-route','exec'),ns)
    from types import SimpleNamespace
    api=Mock();api.exit_via_mcq.return_value={'success':False,'error':'not passed'}
    handler=SimpleNamespace(headers={'Host':'127.0.0.1:8765','Origin':'http://evil.example','X-StrictRoutines-Exit':'1'},
                            _is_local_request=lambda:True,_send_json=Mock(),desktop_api=api)
    ns['_handle_exit_check'](handler,'/api/exit/mcq',{'challenge_id':'forged'})
    api.exit_via_mcq.assert_not_called()
    handler.headers['Origin']='http://127.0.0.1:8765'
    ns['_handle_exit_check'](handler,'/api/exit/mcq',{'challenge_id':'forged'})
    api.exit_via_mcq.assert_called_once_with('forged')
    assert handler._send_json.call_args.kwargs['status']==403
