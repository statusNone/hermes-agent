"""Exact interaction acceptance through the registered production dispatcher."""
import threading
from types import SimpleNamespace

from tui_gateway import server, server_requests


def rpc(method, **params):
    return server.handle_request({'jsonrpc': '2.0', 'id': 'acceptance',
                                  'method': method, 'params': params})


def test_local_clarify_partial_completion_expiry_and_profile_rejection(monkeypatch, tmp_path):
    sid = 'exact-local-owner'
    monkeypatch.setitem(server._sessions, sid, {
        'session_key': sid, 'history': [], 'profile_home': str(tmp_path / 'owner'),
    })
    req = server_requests.ServerRequest(sid, 'clarify', {}, qids=['first', 'second'])
    monkeypatch.setitem(server_requests._open, req.id, req)
    params = dict(session_id=sid, request_id=req.id, question_id='first', answer='one')
    assert rpc('clarify.lock.exact', **params, profile='default')['result']['status'] == 'forbidden'
    assert not req.locked and not req.event.is_set()
    stale = {**params, 'session_id': 'gone'}
    assert rpc('clarify.lock.exact', **stale)['result']['status'] == 'stale'
    invalid = {**params, 'question_id': 'unknown'}
    assert rpc('clarify.lock.exact', **invalid)['error']['code'] == 4002
    assert not req.locked
    assert rpc('clarify.lock.exact', **params)['result'] == {'status': 'ok', 'remaining': ['second']}
    assert req.locked == {'first': 'one'} and not req.event.is_set()
    assert rpc('clarify.lock.exact', **{**params, 'question_id': 'second', 'answer': None})['result'] == {
        'status': 'ok', 'remaining': [],
    }
    assert req.event.is_set() and req.answered
    assert req.result == {'answers': {'first': 'one', 'second': None}, 'outcome': 'submitted'}
    assert req.id not in server_requests._open
    assert rpc('clarify.lock.exact', **params)['result']['status'] == 'expired'


def test_exact_approval_wrong_profile_preserves_named_queue(monkeypatch, tmp_path):
    from tools import approval
    from tools.approval_gateway_wait import _ApprovalEntry

    sid = 'exact-profile-owner'
    monkeypatch.setitem(server._sessions, sid, {
        'session_key': sid, 'history': [], 'profile_home': str(tmp_path / 'owner'),
    })
    entry = _ApprovalEntry({'request_id': 'named', 'choices': ['deny']})
    monkeypatch.setitem(approval._gateway_queues, sid, [entry])
    result = rpc('approval.respond.exact', session_id=sid, request_id='named',
                 choice='deny', profile='default')
    assert result['result'] == {'status': 'forbidden', 'request_id': 'named', 'resolved': 0}
    assert approval._gateway_queues[sid] == [entry] and not entry.event.is_set()


def test_compute_host_exact_owner_locks_and_completes_without_cross_owner_forward(monkeypatch):
    sid = 'exact-host-owner'
    session = {'session_key': sid, 'history': [], 'history_lock': threading.RLock(),
               '_compute_host_active': True, 'agent': None}
    monkeypatch.setitem(server._sessions, sid, session)
    monkeypatch.setitem(server._sessions, 'exact-host-other', {'session_key': 'other', 'history': []})
    monkeypatch.setattr(server, '_load_cfg', lambda: {'dashboard': {'turn_isolation': True}})
    monkeypatch.setattr(server, 'write_json', lambda message: True)
    calls = []

    def respond(owner, params):
        calls.append((owner, params))
        remaining = ['second'] if params['lock']['question_id'] == 'first' else []
        return {'type': 'respond.ack', 'response': {'result': {'status': 'ok', 'remaining': remaining}}}

    monkeypatch.setattr(server, '_get_compute_host_supervisor', lambda: SimpleNamespace(respond=respond))
    server._relay_compute_host_rpc({'jsonrpc': '2.0', 'id': 'srq-exact-host', 'method': 'clarify',
                                   'params': {'session_id': sid, 'questions': [
                                       {'qid': 'first', 'question': 'First?'},
                                       {'qid': 'second', 'question': 'Second?'},
                                   ]}})
    params = dict(session_id=sid, request_id='srq-exact-host', question_id='first', answer='one')
    assert rpc('clarify.lock.exact', **{**params, 'session_id': 'exact-host-other'})['result']['status'] == 'forbidden'
    assert calls == []
    assert rpc('clarify.lock.exact', **params)['result'] == {'status': 'ok', 'remaining': ['second']}
    assert session['_compute_host_open_request']['params']['answers'] == {'first': 'one'}
    assert rpc('clarify.lock.exact', **{**params, 'question_id': 'second', 'answer': 'two'})['result'] == {
        'status': 'ok', 'remaining': [],
    }
    assert calls == [(sid, {'lock': {'request_id': 'srq-exact-host', 'question_id': q, 'answer': a}})
                     for q, a in [('first', 'one'), ('second', 'two')]]
    assert '_compute_host_open_request' not in session
