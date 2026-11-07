"""Classic viewer negotiation on every socket life, using the real owner and RPC client."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_controls import AuthorityConnection
from gateway.session_indexing_status import attach, close
from hermes_cli.cli_indexing_status import CatchupSnapshot
from hermes_cli.gateway_client import GatewayClient, GatewayClientError, GatewayRPCError
from hermes_cli.gateway_connection import GatewayConnection
from hermes_cli.gateway_indexing_status import negotiate
from hermes_state import SessionDB
from hermes_state_runtime import begin_runtime_epoch
from tests.waiting import wait_until


class Wire:
    """In-memory socket carrying real JSON-RPC frames, with no provider or listener port."""
    def __init__(self, owner):
        self.loop = asyncio.get_running_loop()
        self.incoming = asyncio.Queue()
        self.connection = AuthorityConnection(owner, self, {'user_id': 'viewer'})
        self.calls = []

    async def send(self, text):
        request = json.loads(text)
        self.calls.append((request['method'], request['params']))
        response = await self.connection.dispatch(request)
        self.write(response)

    def write(self, frame):
        self.loop.call_soon_threadsafe(self.incoming.put_nowait, json.dumps(frame))
        return True

    def close(self):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()


class Engine:
    def __init__(self):
        self.attachments = self.ends = 0

    def attach_indexing_status(self, handle):
        self.handle = handle
        self.attachments += 1

    def on_session_end(self, sid, messages):
        self.ends += 1

    def update(self, revision):
        self.handle.update(CatchupSnapshot(self.handle.scope, revision, 0, 1, pending=1))


def test_classic_reconnect_renegotiates_before_resume_and_receives_cached_and_live_status(tmp_path):
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        db.create_session('s', source='test')
        epoch = begin_runtime_epoch(db, instance_id='owner')
        owner = SessionAuthority(SimpleNamespace(_draining=False), profile_id=str(tmp_path),
                                 instance_id='owner', db=db, epoch=epoch)
        owner.sessions['s'] = LiveSession(None, 'route')
        engine = Engine()
        agent = SimpleNamespace(context_compressor=engine, session_id='s')
        attach(owner, 's', agent)
        engine.update(1)
        lives = []

        @asynccontextmanager
        async def dial():
            wire = Wire(owner)
            lives.append(wire)
            try:
                async with GatewayClient(wire) as client:
                    yield client
            finally:
                await wire.connection.close()

        connection = GatewayConnection(connect=dial)
        try:
            state = connection.state('s')
            state.adopt_snapshot(connection.call('session.resume', session_id='s'))
            assert state.indexing.value.revision == 1
            engine.update(2)
            wait_until(lambda: state.indexing.value.revision == 2, what='classic indexing push')
            thread = connection._thread
            connection.loop.call_soon_threadsafe(connection.client.events.put_nowait, GatewayClientError('lost'))
            thread.join(5)
            assert not thread.is_alive()
            engine.update(3)  # resident producer keeps publishing with no viewer
            assert connection.call('ping')['pong']
            assert state.indexing.value.revision == 3
            engine.update(4)
            wait_until(lambda: state.indexing.value.revision == 4, what='reconnected indexing push')
            for wire in lives:
                assert wire.calls[:3] == [('runtime.describe', {}), ('client.capabilities', {'indexing_status': True}),
                                         ('session.resume', {'session_id': 's'})]
            assert len(lives) == 2 and engine.attachments == 1 and engine.ends == 0
            assert owner.sessions['s'].event_stream.watermark()[1] == 0
        finally:
            connection.close()
            assert engine.ends == 0
            close(agent)


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [{}, {'capabilities': []}, GatewayRPCError('unknown_method', -32601)])
async def test_older_host_is_never_sent_the_new_field(response):
    client = SimpleNamespace(rpc=AsyncMock(side_effect=[response]))
    assert not await negotiate(client)
    client.rpc.assert_awaited_once_with('runtime.describe')


@pytest.mark.asyncio
@pytest.mark.parametrize('refusal', [GatewayRPCError('unknown_method', -32601), GatewayRPCError('invalid_params', 4001)])
async def test_compat_refusal_of_advertised_opt_in_is_nonfatal(refusal):
    client = SimpleNamespace(rpc=AsyncMock(side_effect=[{'capabilities': ['session-indexing-v1']}, refusal]))
    assert not await negotiate(client)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [GatewayRPCError('permission_denied', 4001), GatewayClientError('lost')])
async def test_transport_and_authorization_failures_are_not_compatibility(failure):
    client = SimpleNamespace(rpc=AsyncMock(side_effect=failure))
    with pytest.raises(type(failure), match=str(failure)):
        await negotiate(client)
