"""Optional resident-engine producer; cached on the authorized session stream.

No reader, timer, global fanout or turn-lifetime callback. Viewers never own the
producer. The agent's real close tears it down; replacing it fences late updates.
"""
from dataclasses import asdict
import hashlib
import logging
import uuid

LOG = logging.getLogger(__name__)


def attach(authority, session_id, agent):
    engine = getattr(agent, 'context_compressor', None)
    hook = getattr(engine, 'attach_indexing_status', None)
    live = authority.sessions.get(session_id)
    if live is None or not callable(hook):
        return
    previous = getattr(agent, '_gateway_indexing_owner', None)
    if previous is not None and previous[0] is engine and not previous[1].closed:
        return
    close(agent)
    from hermes_cli.cli_indexing_status import IndexingScope, IndexingStatus
    scope = IndexingScope(hashlib.sha256(str(authority.profile_id).encode()).hexdigest(),
                          session_id, uuid.uuid4().hex)
    with live.event_stream.lock:
        sequence = getattr(live, '_indexing_sequence', 0) + 1
        live._indexing_sequence = sequence
        live._indexing_epoch = getattr(live, '_indexing_epoch', uuid.uuid4().hex)
        epoch = live._indexing_epoch
        live._indexing_payload = None
    handle = None

    def publish():
        assert handle is not None
        snapshot = handle.snapshot()
        with live.event_stream.lock:
            if live._indexing_sequence != sequence:
                return
            payload = dict(epoch=epoch, generation=sequence,
                           snapshot=asdict(snapshot) if snapshot else None)
            live._indexing_payload = payload
            live.event_stream.publish(session_id, payload, event_type='session.indexing')

    handle = IndexingStatus(scope, publish)
    agent._gateway_indexing_owner = (engine, handle)
    try:
        hook(handle)
    except Exception:
        LOG.warning('Optional context indexing status attachment failed', exc_info=True)
        # Do not turn optional telemetry into agent construction failure.
        from hermes_cli.cli_indexing_status import CatchupSnapshot
        handle.update(CatchupSnapshot(scope, 0, 0, None, health='unavailable'))


def snapshot(authority, session_id):
    live = authority.sessions[session_id]
    with live.event_stream.lock:
        return getattr(live, '_indexing_payload', None)


def close(agent):
    owner = getattr(agent, '_gateway_indexing_owner', None)
    if owner is None:
        return
    agent._gateway_indexing_owner = None
    engine, handle = owner
    # Fence publication first. Engine teardown joins its worker, not a viewer.
    handle.close()
    try:
        engine.on_session_end(getattr(agent, 'session_id', ''), [])
    finally:
        detach = getattr(engine, 'detach_indexing_status', None)
        if callable(detach):
            detach()
