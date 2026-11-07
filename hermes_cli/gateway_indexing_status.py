"""Push-only indexing mirror. Wire fences are independent of UI generations."""
from dataclasses import replace
from threading import RLock
from hermes_cli.cli_indexing_status import (
    CatchupSnapshot, IndexingScope, RollingContextSnapshot, RollingDisplaySnapshot,
)


async def negotiate(client):
    """Opt this classic viewer in before attaching, on every socket life.

    Older hosts neither advertise this capability nor accept the new field. Do not send
    it speculatively, and do not hide transport/auth failures as an older-host verdict.
    """
    from hermes_cli.gateway_client import GatewayRPCError
    try:
        description = await client.rpc('runtime.describe')
        if 'session-indexing-v1' not in description.get('capabilities', []):
            return False
        result = await client.rpc('client.capabilities', indexing_status=True)
        return result.get('indexing_status') is True
    except GatewayRPCError as exc:
        if exc.code == -32601 or (exc.code == 4001 and str(exc) == 'invalid_params'):
            return False
        raise


class IndexingCache:
    def __init__(self, session):
        self.session = session
        self.epoch = None
        self.generation = -1
        self.revision = -1
        self.value = None
        self.serial = 0
        self.listeners = set()
        self.lock = RLock()

    def adopt(self, payload, *, reattach=False):
        with self.lock:
            if payload is None:
                if reattach:
                    self.value = None
                    self._notify()
                return
            try:
                epoch, generation = payload['epoch'], payload['generation']
                if not isinstance(epoch, str) or not epoch or type(generation) is not int or generation < 1:
                    return
                raw = payload['snapshot']
                value = None
                if raw is not None:
                    raw = dict(raw)
                    raw['scope'] = IndexingScope(**raw['scope'])
                    for key, cls in (('rolling_context', RollingContextSnapshot), ('rolling_display', RollingDisplaySnapshot)):
                        if raw.get(key) is not None:
                            raw[key] = cls(**raw[key])
                    value = CatchupSnapshot(**raw)
                    if value.scope.session != self.session:
                        return
                revision = value.revision if value else 10**12  # closed generation
            except (KeyError, TypeError, ValueError):
                return
            if self.epoch is not None and epoch != self.epoch and not reattach:
                return
            same = epoch == self.epoch
            if same and (generation < self.generation or
                         (generation == self.generation and revision <= self.revision and not reattach)):
                return
            if same and generation == self.generation and revision < self.revision:
                return
            self.epoch, self.generation, self.revision = epoch, generation, revision
            self.value = value
            self._notify()

    def _notify(self):
        self.serial += 1
        for handle in tuple(self.listeners):
            if self.value is None:
                handle.clear(self.serial)
            else:
                handle.update(replace(self.value, scope=handle.scope, revision=self.serial))

    def disconnected(self):
        with self.lock:
            if self.value is not None:
                self.value = replace(self.value, health='stale', authoritative_complete=False)
                self._notify()

    def subscribe(self, handle):
        with self.lock:
            self.listeners.add(handle)
            self._notify()
        def unsubscribe():
            with self.lock:
                self.listeners.discard(handle)
                handle.close()
        return unsubscribe
