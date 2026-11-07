"""Indexing is connection-local, unsequenced telemetry, not conversation replay."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_controls import AuthorityConnection
from gateway.session_indexing_status import attach, close, snapshot
from hermes_cli.cli_indexing_status import CatchupSnapshot
from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch
from tests.waiting import async_wait_until
from tui_gateway import event_replay
from tui_gateway.ws import WSTransport


class Socket:
    def __init__(self):
        self.frames = []

    async def send_text(self, text):
        self.frames.append(json.loads(text))


class Engine:
    def __init__(self):
        self.attachments = self.ends = 0

    def attach_indexing_status(self, handle):
        self.handle = handle
        self.attachments += 1

    def on_session_end(self, sid, messages):
        self.ends += 1

    def update(self, revision):
        return self.handle.update(CatchupSnapshot(self.handle.scope, revision, 0, 1, pending=1))


@pytest.fixture
def owner(tmp_path):
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        db.create_session('s', source='test')
        epoch = begin_runtime_epoch(db, instance_id='owner')
        authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id=str(tmp_path),
                                     instance_id='owner', db=db, epoch=epoch)
        authority.sessions['s'] = LiveSession(None, 'route')
        yield authority


def viewer(owner):
    socket = Socket()
    transport = WSTransport(socket, asyncio.get_running_loop())
    return AuthorityConnection(owner, transport, {'user_id': 'viewer'}), socket


async def rpc(connection, method, **params):
    response = await connection.dispatch({'id': 1, 'method': method, 'params': params})
    assert 'error' not in response, response
    return response['result']


async def drained(stream):
    def idle():
        with stream.fanout._lock:
            return not any(peer.writing for peer in stream.fanout._peers)
    await async_wait_until(idle, what='bounded fanout drained')


async def finish(*connections):
    for connection in connections:
        await connection.close()
        connection.transport.close()


def test_unwatched_indexing_never_enters_replay_or_observers(owner):
    stream = owner.sessions['s'].event_stream
    observed = []
    stream.observers.add(observed.append)
    stream.publish('s', {'text': 'before'})
    epoch, sequence = stream.watermark()
    for revision in range(10):
        assert not stream.publish('s', {'revision': revision}, event_type='session.indexing')
    assert stream.watermark() == (epoch, sequence)
    stream.publish('s', {'text': 'after'})
    assert [frame['seq'] for frame in stream.since(epoch, 0)['events']] == [1, 2]
    assert [frame['params']['type'] for frame in observed] == ['message.complete', 'message.complete']


@pytest.mark.asyncio
async def test_live_snapshot_replay_and_catchup_are_default_off(owner, monkeypatch):
    stream = owner.sessions['s'].event_stream
    observed = []
    stream.observers.add(observed.append)
    ordinary, plain = viewer(owner)
    opted, status = viewer(owner)
    engine = Engine()
    agent = SimpleNamespace(context_compressor=engine, session_id='s')
    attach(owner, 's', agent)
    try:
        first = await rpc(ordinary, 'session.resume', session_id='s')
        assert 'indexing_status' not in first
        description = await rpc(opted, 'runtime.describe')
        assert 'session-indexing-v1' in description['capabilities']
        assert (await rpc(opted, 'client.capabilities', indexing_status=True))['indexing_status']
        assert (await rpc(opted, 'session.resume', session_id='s'))['indexing_status'] is None
        epoch = first['replay_epoch']
        stream.publish('s', {'text': 'before'})
        engine.update(1)
        stream.publish('s', {'text': 'after'})
        await drained(stream)
        assert [f['params']['type'] for f in plain.frames] == ['message.complete', 'message.complete']
        assert [f['params']['seq'] for f in plain.frames] == [1, 2]
        assert [f['params']['type'] for f in status.frames] == [
            'message.complete', 'session.indexing', 'message.complete']
        assert 'seq' not in status.frames[1]['params']
        assert 'replay_epoch' not in status.frames[1]['params']
        assert [f['params']['seq'] for f in observed] == [1, 2]
        assert (await rpc(opted, 'session.resume', session_id='s'))['indexing_status'] == snapshot(owner, 's')
        assert 'indexing_status' not in await rpc(ordinary, 'session.resume', session_id='s')
        # Telemetry floods do not age the ordinary ring or move its watermark.
        monkeypatch.setattr(event_replay, '_REPLAY_BUFFER_MAX', 2)
        await rpc(opted, 'client.capabilities', indexing_status=False)
        for revision in range(2, 12):
            engine.update(revision)
        assert stream.watermark() == (epoch, 2)
        for connection in (ordinary, opted):
            replay = await rpc(connection, 'session.events.since', session_id='s', replay_epoch=epoch, last_sequence=0)
            assert [f['seq'] for f in replay['events']] == [1, 2]
            assert not replay['snapshot_required']
            assert connection.replay_cursors['s'] == (epoch, 2)
        # Between replay and resume, catch-up carries only newly published ordinary frames.
        await rpc(ordinary, 'session.detach', session_id='s', subscription_id=first['subscription_id'])
        plain.frames.clear()
        engine.update(12)
        stream.publish('s', {'text': 'catch-up'})
        resumed = await rpc(ordinary, 'session.resume', session_id='s')
        await drained(stream)
        await async_wait_until(lambda: len(plain.frames) == 1, what='WS catch-up delivered')
        assert plain.frames[0]['params']['seq'] == 3
        assert resumed['last_sequence'] == 3 and 'indexing_status' not in resumed
        assert 's' not in ordinary.replay_cursors
        assert (await rpc(ordinary, 'session.events.since', session_id='s', replay_epoch=epoch,
                          last_sequence=0))['snapshot_required']
        assert (await rpc(ordinary, 'session.events.since', session_id='s', replay_epoch='old',
                          last_sequence=3))['snapshot_required']
        assert engine.attachments == 1 and engine.ends == 0
    finally:
        await finish(ordinary, opted)
        assert engine.ends == 0  # leaving viewers never end the resident producer
        close(agent)
    assert engine.ends == 1


@pytest.mark.asyncio
async def test_no_plugin_opt_updates_and_fresh_connection_do_not_inherit(owner):
    stream = owner.sessions['s'].event_stream
    connection, socket = viewer(owner)
    fresh, fresh_socket = viewer(owner)
    try:
        attach(owner, 's', SimpleNamespace())
        assert (await rpc(connection, 'client.capabilities', indexing_status=True))['indexing_status']
        await rpc(connection, 'client.capabilities', server_requests=True)
        assert (await rpc(connection, 'session.resume', session_id='s'))['indexing_status'] is None
        assert 'indexing_status' not in await rpc(fresh, 'session.resume', session_id='s')
        bad = await connection.dispatch({'id': 2, 'method': 'client.capabilities',
                                         'params': {'indexing_status': 'false'}})
        assert bad['error']['message'] == 'invalid_params'
        unknown = await connection.dispatch({'id': 3, 'method': 'client.capabilities',
                                             'params': {'indexing_status': False, 'typo': True}})
        assert unknown['error']['message'] == 'invalid_params'
        assert (await rpc(connection, 'client.capabilities'))['indexing_status']
        await rpc(connection, 'client.capabilities', indexing_status=False)
        assert 'indexing_status' not in await rpc(connection, 'session.resume', session_id='s')
        stream.publish('s', {'text': 'ordinary'})
        await drained(stream)
        assert [f['params']['seq'] for f in socket.frames] == [1]
        assert fresh_socket.frames == socket.frames
        from tui_gateway import server_requests
        assert server_requests.answers_requests(connection.transport)
    finally:
        from tui_gateway import server_requests
        server_requests.forget(connection.transport)
        await finish(connection, fresh)


@pytest.mark.asyncio
async def test_capabilities_follow_host_socket_profile_projections_not_other_clients(owner, tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', owner.profile_id)
    from hermes_cli.profiles import get_profile_dir
    beta_home = get_profile_dir('beta')
    beta_home.mkdir(parents=True)
    with SessionDB(db_path=beta_home / 'state.db') as db:
        db.create_session('s', source='test')
        epoch = begin_runtime_epoch(db, instance_id='beta')
        beta = SessionAuthority(SimpleNamespace(_draining=False), profile_id=str(beta_home),
                                instance_id='beta', db=db, epoch=epoch)
        beta.sessions['s'] = LiveSession(None, 'route')
        owner.runner.session_authorities = [owner, beta]
        socket = Socket()
        transport = WSTransport(socket, asyncio.get_running_loop())
        host = AuthorityConnection(owner, transport, {'user_id': 'viewer', 'profile_scope': 'host'})
        other, other_socket = viewer(beta)
        try:
            await rpc(host, 'client.capabilities', indexing_status=True)
            resumed = await rpc(host, 'session.resume', session_id='s', profile='beta')
            assert resumed['indexing_status'] is None
            await rpc(other, 'session.resume', session_id='s')
            stream = beta.sessions['s'].event_stream
            payload = {'epoch': 'beta', 'generation': 1, 'snapshot': None}
            assert stream.publish('s', payload, event_type='session.indexing')
            await drained(stream)
            assert socket.frames[-1]['params']['payload'] == payload
            assert other_socket.frames == []
            await rpc(host, 'client.capabilities', indexing_status=False)
            assert not stream.publish('s', payload, event_type='session.indexing')
            stream.publish('s', {'text': 'barrier'})
            await drained(stream)
            assert [f['params']['type'] for f in socket.frames] == ['session.indexing', 'message.complete']
            assert [f['params']['type'] for f in other_socket.frames] == ['message.complete']
        finally:
            await finish(host, other)


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', ['frames', 'bytes'])
async def test_indexing_uses_bounded_ws_batches_and_normal_overflow_retirement(owner, monkeypatch, budget):
    stream = owner.sessions['s'].event_stream
    slow, slow_socket = viewer(owner)
    fast, fast_socket = viewer(owner)
    untouched, plain_socket = viewer(owner)
    engine = Engine()
    agent = SimpleNamespace(context_compressor=engine, session_id='s')
    attach(owner, 's', agent)
    entered = asyncio.Event()
    send = slow.transport._safe_send_many

    async def sending(lines, **kwargs):
        entered.set()
        return await send(lines, **kwargs)

    monkeypatch.setattr(slow.transport, '_safe_send_many', sending)
    try:
        for connection in (slow, fast):
            await rpc(connection, 'client.capabilities', indexing_status=True)
        for connection in (slow, fast, untouched):
            await rpc(connection, 'session.resume', session_id='s')
        await slow.transport._send_lock.acquire()
        engine.update(1)
        await asyncio.wait_for(entered.wait(), 5)
        # Real WS writer is awaiting send progress; there is no unbounded write() shim.
        monkeypatch.setattr(stream.fanout, '_MAX_PENDING_FRAMES', 2)
        if budget == 'bytes':
            monkeypatch.setattr(stream.fanout, '_MAX_PENDING_BYTES', 1)
        # Deliver to healthy peer before each next update, isolating slow-reader overflow.
        await async_wait_until(lambda: len(fast_socket.frames) == 1, what='healthy indexing frame')
        if budget == 'bytes':
            # Byte cap is global; remove fast while testing one oversized admission.
            await rpc(fast, 'client.capabilities', indexing_status=False)
        for revision in range(2, 5):
            engine.update(revision)
            if budget == 'frames':
                await async_wait_until(lambda: len(fast_socket.frames) == revision, what='healthy viewer progress')
        assert not stream.fanout.contains(slow.transport)
        assert slow.actor not in owner.sessions['s'].subscribers.values()
        assert stream.fanout.contains(untouched.transport)
        assert stream.watermark()[1] == 0
        slow.transport._send_lock.release()
        await drained(stream)
        assert [f['params']['type'] for f in slow_socket.frames] == ['session.replay_gap']
        assert slow_socket.frames[0]['params']['payload']['latest_seq'] == 0
        assert plain_socket.frames == []
        assert (await rpc(slow, 'ping'))['pong']
        monkeypatch.setattr(stream.fanout, '_MAX_PENDING_BYTES', 4 * 1024 * 1024)
        recovered = await rpc(slow, 'session.resume', session_id='s')
        assert recovered['indexing_status'] == snapshot(owner, 's')
        engine.update(5)
        await drained(stream)
        assert slow_socket.frames[-1]['params']['type'] == 'session.indexing'
        assert 'seq' not in slow_socket.frames[-1]['params']
        assert engine.attachments == 1 and engine.ends == 0
    finally:
        if slow.transport._send_lock.locked():
            slow.transport._send_lock.release()
        await finish(slow, fast, untouched)
        await drained(stream)
        close(agent)
