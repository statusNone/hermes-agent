"""Focused optional producer/transport lifetime checks; no model or storage I/O."""
import unittest
from dataclasses import asdict, replace
from threading import RLock
from types import SimpleNamespace

from gateway.session_indexing_status import attach, close, snapshot
from hermes_cli.cli_indexing_status import CatchupSnapshot, IndexingScope, IndexingStatus
from hermes_cli.gateway_indexing_status import IndexingCache


class Stream:
    def __init__(self):
        self.lock = RLock()
        self.events = []
    def publish(self, sid, payload, **kwargs):
        self.events.append((sid,payload,kwargs))


class Engine:
    def __init__(self):
        self.attachments = 0
        self.ends = 0
    def attach_indexing_status(self, handle):
        self.handle = handle
        self.attachments += 1
    def detach_indexing_status(self):
        self.handle.close()
    def on_session_end(self, sid, messages):
        self.ends += 1


class StatusTests(unittest.TestCase):
    def test_resident_lifetime_fences_and_no_plugin(self):
        live = SimpleNamespace(event_stream=Stream())
        authority = SimpleNamespace(profile_id='p',sessions={'s':live})
        attach(authority,'s',SimpleNamespace())
        self.assertIsNone(snapshot(authority,'s'))
        engine=Engine();agent=SimpleNamespace(context_compressor=engine,session_id='s')
        attach(authority,'s',agent);attach(authority,'s',agent)
        self.assertEqual(engine.attachments,1)
        h=engine.handle
        s=CatchupSnapshot(h.scope,1,0,1,pending=1)
        self.assertTrue(h.update(s))
        self.assertFalse(h.update(s))
        self.assertFalse(h.update(replace(s,scope=replace(s.scope,session='other'),revision=2)))
        other=Engine();replacement=SimpleNamespace(context_compressor=other,session_id='s')
        attach(authority,'s',replacement)
        other.handle.update(CatchupSnapshot(other.handle.scope,1,1,1,authoritative_complete=True))
        accepted=snapshot(authority,'s')
        h.update(replace(s,revision=3))
        close(agent)
        self.assertEqual(snapshot(authority,'s'),accepted)
        close(replacement);close(replacement)
        self.assertEqual(other.ends,1)
        self.assertIsNone(snapshot(authority,'s')['snapshot'])
        self.assertTrue(other.handle.closed)

    def test_cache_generation_revision_disconnect_and_viewer_detach(self):
        cache=IndexingCache('s')
        scope=IndexingScope('p','s','producer')
        value=CatchupSnapshot(scope,2,1,1,authoritative_complete=True)
        def payload(value=value,generation=1,epoch='epoch'):
            return dict(epoch=epoch,generation=generation,snapshot=asdict(value) if value else None)
        ui=IndexingStatus(IndexingScope('local','s','view'),lambda:None)
        unsubscribe=cache.subscribe(ui)
        cache.adopt(payload())
        self.assertEqual(ui.snapshot().status,'Idle')
        serial=ui.snapshot().revision
        cache.adopt(payload(replace(value,revision=1)))
        cache.adopt(payload(replace(value,scope=replace(scope,session='other'),revision=3)))
        cache.adopt(payload(epoch='foreign'))
        self.assertEqual(ui.snapshot().revision,serial)
        cache.disconnected()
        self.assertEqual(ui.snapshot().status,'Stale')
        cache.adopt(payload(),reattach=True)
        self.assertEqual(ui.snapshot().status,'Idle')
        cache.adopt(payload(replace(value,revision=0),generation=2))
        cache.adopt(payload(replace(value,revision=50),generation=1))
        self.assertEqual(cache.generation,2)
        cache.adopt(payload(None,generation=2))
        cache.adopt(payload(replace(value,revision=99),generation=2))
        self.assertIsNone(ui.snapshot())
        cache.adopt(payload(epoch='restart'),reattach=True)
        self.assertEqual(ui.snapshot().status,'Idle')
        unsubscribe()
        self.assertTrue(ui.closed)
        self.assertEqual(cache.value.status,'Idle')
        self.assertFalse(cache.listeners)

    def test_attachment_failure_truthful_and_wire_contract(self):
        from tui_gateway.contracts.indexing_status import IndexingStatusPayload
        class Broken(Engine):
            def attach_indexing_status(self,handle):
                self.handle=handle
                raise RuntimeError('synthetic attachment failure')
        e=Broken();a=SimpleNamespace(context_compressor=e,session_id='s')
        authority=SimpleNamespace(profile_id='p',sessions={'s':SimpleNamespace(event_stream=Stream())})
        attach(authority,'s',a)
        p=snapshot(authority,'s')
        self.assertEqual(e.handle.snapshot().status,'Unavailable')
        self.assertEqual(IndexingStatusPayload.model_validate(p).snapshot.health,'unavailable')
        close(a)

if __name__=='__main__':
    unittest.main()
