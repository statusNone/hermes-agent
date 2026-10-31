"""Optional engine-to-stock-row binding, owned by this CLI's agent lifetime."""
import hashlib
import uuid


def local_context_agent(**kwargs):
    """Explicit local bridge; gateway ownership/route mutations are unsupported."""
    if kwargs.get('one_turn') or kwargs.get('request_overrides'):
        raise ValueError('Local critical context does not support gateway route overrides')
    for key in ('push_reasoning', 'request_overrides', 'model_confirm_callback',
                'modal_withdraw_callback', 'connection_update_callback', 'one_turn', 'one_turn_reasoning', 'one_turn_alias'):
        kwargs.pop(key, None)
    from run_agent import AIAgent
    return AIAgent(**kwargs)


def detach_context_status(cli):
    engine = getattr(cli, '_indexing_engine', None)
    if engine is not None:
        engine.detach_indexing_status()
        cli._indexing_engine = None
    handle = getattr(cli, '_indexing_status', None)
    if handle is not None:
        handle.close()


def attach_context_status(cli):
    if getattr(cli, 'config', {}).get('display', {}).get('indexing_status') is False:
        return
    agent = getattr(cli, 'agent', None)
    engine = getattr(agent, 'context_compressor', None)
    if not getattr(cli, '_app', None) or not callable(getattr(engine, 'attach_indexing_status', None)):
        return
    if getattr(cli, '_indexing_engine', None) is engine:
        return
    detach_context_status(cli)
    from hermes_constants import get_hermes_home
    from hermes_cli.cli_indexing_status import IndexingScope, bind_indexing_status
    profile = hashlib.sha256(str(get_hermes_home()).encode()).hexdigest()
    handle = bind_indexing_status(cli, IndexingScope(profile, str(agent.session_id), uuid.uuid4().hex))
    cli._indexing_engine = engine
    engine.attach_indexing_status(handle)
