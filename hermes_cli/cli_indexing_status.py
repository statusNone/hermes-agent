"""Explicit, optional in-process indexing contribution; no transport or registry.

The producer owns freshness, membership/version deduplication and publication
truth. Rendering only reads a cached immutable snapshot, never the index.
"""
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class IndexingScope:
    profile: str
    session: str
    generation: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v or len(v) > 128
               for v in (self.profile, self.session, self.generation)):
            raise ValueError("scope requires bounded profile/session/generation identities")


@dataclass(frozen=True)
class CatchupSnapshot:
    """Current logical source/version work, NOT lifetime counts or attempts.

    Published means cataloged, embedded AND published/retrievable. Counters
    partition known_total. New admissions can grow it and regress the gauge.
    None means discovery has not established a denominator. Completion is an
    authoritative assertion, not an inference from an empty pending queue.
    """
    scope: IndexingScope
    revision: int
    published: int
    known_total: int | None
    pending: int = 0
    inflight: int = 0
    failed: int = 0
    health: str = "live"
    authoritative_complete: bool = False

    def __post_init__(self):
        if not isinstance(self.scope, IndexingScope):
            raise ValueError("expected explicit IndexingScope")
        counters = (self.revision, self.published, self.pending, self.inflight, self.failed)
        if any(type(v) is not int or not 0 <= v <= 10**12 for v in counters):
            raise ValueError("revision and counters must be bounded nonnegative integers")
        if self.known_total is not None:
            if type(self.known_total) is not int or not 0 <= self.known_total <= 10**12:
                raise ValueError("known_total must be a bounded integer or None")
            if self.published + self.pending + self.inflight + self.failed != self.known_total:
                raise ValueError("counters must partition known_total")
        if self.health not in {"live", "stale", "unavailable"}:
            raise ValueError("unknown health")
        if type(self.authoritative_complete) is not bool:
            raise ValueError("completion must be boolean")
        if self.authoritative_complete and (self.known_total is None
                or self.published != self.known_total or self.pending or self.inflight or self.failed):
            raise ValueError("completion requires fully published known work and no backlog")

    @property
    def status(self):
        if self.health != "live":
            return self.health.capitalize()
        if self.failed:
            return "Failed"
        if self.authoritative_complete:
            return "Idle"
        if self.known_total is None:
            return "Unknown"
        if self.published == self.known_total:
            return "Verifying"
        return "Indexing"


def indexing_text(snapshot):
    """One complete ASCII segment. No compact variant, truncation or animation."""
    label = f"Index {snapshot.status}"
    if snapshot.status in {"Idle", "Stale", "Unavailable"}:
        return label
    total = snapshot.known_total
    counts = f" {snapshot.published}/{total if total is not None else '?'}"
    if total is None:
        return label + counts + " [??????????]"
    if snapshot.status == "Verifying":
        return label + counts
    filled = 10 * snapshot.published // total if total else 0
    percent = 100 * snapshot.published // total if total else 0
    return label + counts + f" [{('#' * filled) + ('-' * (10 - filled))}] {percent}%"


class IndexingStatus:
    """One owner-scoped handle. update/clear/close are worker-safe.

    clear retains the revision watermark; close is irreversible and releases the
    repaint callback. No timers, workers, ambient profile lookup or I/O here.
    """
    def __init__(self, scope, repaint):
        if not isinstance(scope, IndexingScope):
            raise TypeError("expected IndexingScope")
        self.scope = scope
        self._repaint = repaint
        self._lock = RLock()
        self._snapshot = None
        self._revision = -1
        self._closed = False

    @property
    def closed(self):
        with self._lock:
            return self._closed

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def update(self, snapshot):
        if not isinstance(snapshot, CatchupSnapshot):
            raise TypeError("expected CatchupSnapshot")
        with self._lock:
            if self._closed or snapshot.scope != self.scope or snapshot.revision <= self._revision:
                return False
            self._snapshot, self._revision = snapshot, snapshot.revision
            self._repaint()
            return True

    def clear(self, revision):
        if type(revision) is not int or not 0 <= revision <= 10**12:
            raise ValueError("invalid revision")
        with self._lock:
            if self._closed or revision <= self._revision:
                return False
            self._snapshot, self._revision = None, revision
            self._repaint()
            return True

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed, self._snapshot = True, None
                self._repaint()
                self._repaint = None


def bind_indexing_status(cli, scope):
    """Host/UI-thread attach after Application construction; producer gets handle.

    Close before detach/session switch, then bind a fresh generation. Direct
    Application.invalidate is thread-safe and coalesced WITHOUT the CLI throttle
    that can lose a final Idle repaint when refresh_interval=0.
    """
    previous = getattr(cli, "_indexing_status", None)
    if previous is not None and not previous.closed:
        raise RuntimeError("close the previous indexing owner before rebinding")
    handle = IndexingStatus(scope, cli._app.invalidate)
    cli._indexing_status = handle
    return handle


def append_indexing_status(cli, fragments, width):
    """Append whole segment only in spare cells, after ALL stock reservations.

    Call before stock alignment/clipping with its actual remaining left budget.
    Returning the original object when absent/hidden preserves normal behavior.
    """
    # Never show even the short Idle label in the stock compact width tiers.
    if width < 76:
        return fragments
    handle = getattr(cli, "_indexing_status", None)
    snapshot = handle.snapshot() if handle is not None else None
    if snapshot is None:
        return fragments
    text = " │ " + indexing_text(snapshot)
    used = sum(cli._status_bar_display_width(value) for _, value in fragments)
    if used + cli._status_bar_display_width(text) > width:
        return fragments
    return [*fragments, ("class:status-bar-dim", text)]
