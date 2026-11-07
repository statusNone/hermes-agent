"""Canonical connection-local presentation opt-ins, never principal permissions."""
from hermes_state_runtime import RuntimeStoreError


def indexing_snapshot_fields(connection, session_id):
    from gateway.session_indexing_status import snapshot
    if 'session.indexing' not in connection.event_capabilities:
        return {}
    return {'indexing_status': snapshot(connection.authority, session_id)}


def advertise(connection, params):
    from tui_gateway.client_capabilities import advertise as advertise_transport
    if not connection.actor.capabilities:
        raise RuntimeStoreError('permission_denied')
    for name, value in params.items():
        if type(value) is not bool:
            raise RuntimeStoreError('invalid_params', field=name, detail='must be a boolean')
    if 'indexing_status' in params:
        change = connection.event_capabilities.add if params['indexing_status'] else connection.event_capabilities.discard
        change('session.indexing')
    return {**advertise_transport(connection.transport, params),
            'indexing_status': 'session.indexing' in connection.event_capabilities}
