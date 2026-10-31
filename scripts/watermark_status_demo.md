# Rolling watermark footer (paired offline change)

The existing owner-scoped `CatchupSnapshot` optionally carries a
`RollingContextSnapshot`. It replaces only the stock context-detail / context-percent
segments in classic, modern and plain renderers. Other field selection, ordering,
width tiers, clipping and title placement are unchanged. The independent Index
progress segment reuses the stock `_build_context_bar` (ten █/░ cells) and
`_status_bar_context_style` theme classes, just like the watermark gauge.
Index counters, integer percent and state decisions remain separate and unchanged;
only fill rounding now follows the stock renderer (75% fills eight cells).
Caught-up is still `Index Idle`; failure, unknown, stale, unavailable and verifying
labels retain their existing meaning. Unknown has no invented percentage.
No plugin / missing snapshot / inactive rolling / cleared or closed owner uses stock
rendering. Under 52 cells the stock tier omits context; 52–75 shows the percentage
against `high` and `low` without a gauge. At 76+ the normal detail and gauge slots
show e.g. `~12.5K/25K high │ [█████░░░░░] ~50% · low 15K`.
Field visibility settings still apply. Only gauge fill saturates at 100%; numeric
percent can exceed 100. High is a soft policy watermark, not provider capacity.

Numerator contract: latest `RollingPolicy.select`'s `input_units`, after its trim
and before that selection's recall injection, in canonical Hermes estimated tokens.
Existing resident evidence is included; newly injected recall is not. This is not
host total usage, post-recall request size, reserved output, a lifetime counter or
an index count. The engine estimator removes persistence fields and uses the usage
anchor when applicable, otherwise the host rough estimate. The UI does not count.
Low/high are read from the active policy config, never provider context length.

Timing: publication observer samples the latest completed policy-count assignment
on its existing loop (0.5-second wait plus the index reader's time, bounded by that
reader's ten-second timeout). It does not update per emitted token. Startup without
a selection count falls back to stock. Session-start clears the previous session's
diagnostic count; the existing scoped handle owns revision, rejection and teardown.
Index failures retain the independently sampled rolling contribution. No budget,
selection, indexing, authorization, ingestion or provider behavior is changed.

Bounded offline demo (existing interpreter; no install or live Hermes startup):

```
python scripts/watermark_status_demo.py --evidence /absolute/scratch/watermarks
```

Synthetic states exercise below/at/above high, changed low/high, matching independent
index gauges, failure/unknown states and stock fallback
in actual classic/modern PTYs at 240 and 60 cells. Outputs: `terminal.ansi`,
`frames.json`, `screens.txt`, lifecycle `report.json`, and `summary.json`. The
harness denies socket connections, requires preserved draft/focus/cursor and
restored terminal state, and kills/reaps a child that exceeds 15 seconds. This is
display evidence, not full live-session or provider integration certification.
