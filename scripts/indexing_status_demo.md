# Optional same-row indexing status: isolated host candidate

This is an opt-in, in-process host UI slice, not a native plugin registration
API, installed-runtime patch, Hermes launcher, or connected Hysteresis bridge.
The demo producer and all context/indexing counters are SYNTHETIC. The stock
status renderer code is real: classic and modern-classic prompt_toolkit paths
are imported from this checkout, not copied or replaced with a fixture row.

## Run without services

Use an existing Python environment with this checkout's prompt_toolkit and
runtime dependencies. Do not activate an installed Hermes instance to test it.
From this worktree:

    PYTHONDONTWRITEBYTECODE=1 python scripts/indexing_status_demo.py --interval 0.4
    PYTHONDONTWRITEBYTECODE=1 python scripts/indexing_status_demo.py --modern --interval 0.4
    PYTHONDONTWRITEBYTECODE=1 python scripts/indexing_status_demo.py --no-contribution

The standalone demo auto-exits. Ctrl-C stops it early; ordinary typing edits the
seeded draft. Use a wide terminal (240 columns demonstrates both layouts with
the long worktree path); resize to 60 columns and back. Context and indexing
share the same stock row. There is no extra indexing window. Context is never
fabricated from indexing counters or rendered twice.

The header and model label say DEMO. The demo uses the real pure token-format
helper in a temporary `cli` facade, solely to avoid importing/initializing the
side-effectful full CLI. It overrides the status data source and field filter,
not the production rendering algorithms. It does not construct HermesCLI,
connect to a gateway, call a model/provider, or read/write a live index. Modern
stock cwd rendering can read the worktree's cwd and Git branch.

## Narrow host seam

`hermes_cli.cli_indexing_status.bind_indexing_status(cli, scope)` attaches after
Application construction on the UI thread. Only the host receives the CLI
object; the producer receives an `IndexingStatus` handle with `update`, `clear`,
`snapshot`, and `close`. There is no registry, event bus, timer or extra worker
in the host extension. The synthetic demo alone owns a producer thread.

The immutable `CatchupSnapshot` carries explicit profile/session/generation
identity, increasing revision, published/known_total, pending/inflight/failed,
health, and authoritative_complete. Known counters must partition known_total.
Published means cataloged, embedded AND published/retrievable logical source
versions, not embedding calls or retry attempts. New admissions may increase
the denominator and decrease the percentage. Unknown totals show an unknown
gauge without a percentage. Equal counts without authoritative confirmation
show Verifying. Only confirmed fully published live state displays Idle;
Failed, Stale and Unavailable remain distinguishable.

Freshness and publication truth belong to the future producer. There is no
heartbeat timeout: the host does not discover that a silent producer died.
Such a producer must publish stale/unavailable and must never assert completion
from an empty queue alone. An unknown gauge is static, not a fake percentage
or independent animation. Progress changes on producer updates.

Whole snapshots and lifecycle mutations are lock protected. Scope mismatches,
non-increasing revisions and updates after close are rejected. Clear retains
the revision watermark. Close removes the contribution and releases its redraw
callback. The host must close before session switch/detach/shutdown; a new
binding requires a new owner/generation. No automatic binding or session-switch
integration is installed by this slice.

Updates call Application.invalidate, prompt_toolkit's thread-safe/coalesced
notification, directly rather than the CLI's throttled `_invalidate`. This
avoids losing a final Idle update with refresh_interval=0. No producer draws,
prints terminal control sequences, or performs index I/O in a render callback.
The application owns redraw scheduling and resize handling. Full Hermes
runtime-specific resize/stream guards have not been exercised here.

## Placement and width rule

The two production renderers call one shared append helper after stock fields
and reservations are calculated. The plain-text fallback uses it too. The
entire segment is omitted when the supplied left-side width budget is below
76 cells OR stock content plus the entire segment does not fit. A narrower
left-side budget can result from the stock title, Vim label or modern hints.
All stock content has priority, including modern cwd. There is no compact
index label and no partial/truncated indexing gauge. Idle is also hidden in
the compact tiers. Resizing restores the cached state without a model turn,
keypress, or new producer snapshot when enough space returns.

No-contribution returns the original fragment list. No fields, context state,
layout row allocation, approval UI, indexing policy, rolling choice wiring or
hard-limit behavior are changed.

## Offline acceptance checker

    PYTHONDONTWRITEBYTECODE=1 python scripts/check_indexing_status.py --evidence "$TMPDIR/same-row-indexing-check"

The checker is standalone stdlib unittest plus existing runtime dependencies.
It avoids provisioning the canonical pytest environment or starting the full
CLI. This does not replace the repository's canonical CI suite. The current
operator environment has no pytest; the canonical runner can provision an
environment and also uses /var/tmp. Neither was authorized for this isolated
check, so no pytest provisioning or runner/environment changes were made.
For the local commit hook only, pinned ruff 0.15.22 was provisioned as a
standalone tool under Jonathan's scratch directory, with its tool, executable
and download-cache directories all explicitly redirected there. No existing
Hermes environment, shell configuration or installed runtime was changed.

The checker launches actual child PTYs with clean environments, no credentials,
refresh_interval=0, UTF-8 ANSI-delta decoding and bounded cleanup. It tests:

- dynamic denominator growth, retries, validation and explicit completion;
- owner/revision rejection, clear, close, and rebind;
- actual stock renderers across widths, title and Vim reservations;
- no-keypress full playback in both layouts;
- live 240 -> 60 -> 240 resize and restoration of the unchanged snapshot;
- one stock context gauge and indexing in the same row;
- input focus, seeded draft/logical cursor and decoded terminal cursor;
- physical typing and Ctrl-C in both layouts, worker joining, late rejection;
- termios, alternate screen, paste and cursor restoration, child reap;
- no-contribution PTY behavior.

`terminal.ansi`, `frames.json`, `screens.txt`, `child.json` and `result.json`
are retained per PTY scenario. `summary.json` records the aggregate result.
Scratch evidence may be pruned; checks regenerate it.

For exact no-contribution parity, export the two unmodified base renderer
modules to a scratch directory using `git show BASE:hermes_cli/FILE.py`, then:

    PYTHONDONTWRITEBYTECODE=1 python scripts/check_indexing_status.py --evidence "$TMPDIR/same-row-indexing-check" --baseline-dir "$TMPDIR/same-row-indexing-baseline"

The optional comparison executes both baseline and candidate modules and
compares styled fragments and plain text across both layouts, widths 10..240,
empty/ASCII/CJK titles and Vim on/off. It does not assert source shape.

## Explicit integration gap

Existing `hermes_cli/gateway_connection.py:SessionState` has usage listeners
and snapshot adoption, but no indexing state projection. Reusing it correctly
requires an authoritative scoped publisher, idle delivery, reconnect/current
snapshot, disconnect freshness and session-generation lifecycle. Adding an
unused indexing event branch would not make that bridge real. No transport
changes or runtime registration were made. The exported handle is the bounded
host insertion point for a separately authorized adapter. This candidate is
not end-to-end real-index acceptance and is not approved for activation.
