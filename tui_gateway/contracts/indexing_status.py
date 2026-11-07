"""Optional push-only context indexing status on a canonical session."""
from pydantic import Field
from hermes_cli.cli_indexing_status import CatchupSnapshot
from .base import Payload
from .registry import event


class IndexingStatusPayload(Payload):
    epoch: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=1)
    snapshot: CatchupSnapshot | None


event('session.indexing', IndexingStatusPayload,
      doc='Opt-in unsequenced resident context-engine snapshot; never ordinary replay or observers. '
          'Producer lifetime, not turn lifetime. Null retires it.')
