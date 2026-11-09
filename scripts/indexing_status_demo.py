#!/usr/bin/env python3
"""Offline real-stock-renderer demo. Synthetic state; NOT a Hermes launcher.

No full HermesCLI construction, gateway, provider, config or index access.
The facade dependency is restricted to the real pure token-format helper.
"""
import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sys
from threading import Event, Thread
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.usage_pricing import format_token_count_compact
from hermes_cli.cli_status_bar_mixin import CLIStatusBarMixin
from hermes_cli.cli_indexing_status import CatchupSnapshot, IndexingScope, bind_indexing_status
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window, FormattedTextControl
from prompt_toolkit.widgets import TextArea


@contextmanager
def renderer_facade():
    """Supply only a real pure helper; never import the side-effectful CLI facade.

    Demo/test process only. All status algorithms are imported production code,
    not source extraction, copies or a fixture stock row.
    """
    previous = sys.modules.get("cli")
    facade = ModuleType("cli")
    facade.format_token_count_compact = format_token_count_compact
    facade.CLI_CONFIG = {}
    sys.modules["cli"] = facade
    try:
        yield
    finally:
        if previous is None:
            del sys.modules["cli"]
        else:
            sys.modules["cli"] = previous


def demo_snapshots():
    first = CatchupSnapshot(IndexingScope("synthetic-profile", "synthetic-session", "demo-1"),
                            0, 1, 4, pending=2, inflight=1)
    progress = replace(first, revision=1, published=3, pending=0)
    admitted = replace(progress, revision=2, known_total=8, pending=4)
    failed = replace(admitted, revision=3, pending=3, failed=1)
    retry = replace(failed, revision=4, failed=0, inflight=2)
    verifying = replace(retry, revision=5, published=8, pending=0, inflight=0)
    idle = replace(verifying, revision=6, authoritative_complete=True)
    return (first, progress, admitted, failed, retry, verifying, idle,
            replace(idle, revision=7, health="stale"),
            replace(idle, revision=8, health="unavailable"),
            CatchupSnapshot(first.scope, 9, 2, None, pending=2, inflight=1),
            replace(idle, revision=10))


class StockRenderer(CLIStatusBarMixin):
    """Synthetic inputs only; inherited production rendering/width/title methods."""
    def __init__(self, modern=False):
        self._modern_layout = modern
        self._status_bar_visible = True
        self._prompt_stash = SimpleNamespace(indicator=lambda: "")
        self.fields = None
        self.width = None
        self.state = dict(model_short="DEMO", duration="2m", context_percent=25,
                          context_length=32768, context_tokens=8192, compressions=0)

    def _get_status_bar_snapshot(self):
        return self.state.copy()

    def _get_status_bar_field_set(self):
        return self.fields

    def _is_session_yolo_active(self):
        return False

    def _get_tui_terminal_width(self):
        return self.width if self.width is not None else super()._get_tui_terminal_width()


class IndexingDemo(StockRenderer):
    def __init__(self, modern=False, contribution=True):
        super().__init__(modern)
        self.contribution = contribution
        self.snapshots = demo_snapshots()
        self.stop = Event()
        self.worker = None
        self.observations = []
        self.input_area = TextArea(text="keep my draft", height=1, multiline=False)
        self.input_area.buffer.cursor_position = 5
        bindings = KeyBindings()

        @bindings.add("c-c")
        def interrupt(event):
            event.app.exit()

        self._app = Application(
            layout=Layout(HSplit([
                Window(FormattedTextControl("DEMO synthetic state; real stock renderer; no live bridge"), height=1),
                Window(FormattedTextControl("Auto-exits; Ctrl-C stops; type to edit draft"), height=1),
                Window(),
                Window(FormattedTextControl(self._get_status_bar_fragments), height=1, wrap_lines=False),
                self.input_area,
            ]), focused_element=self.input_area),
            key_bindings=bindings, full_screen=True, refresh_interval=0)
        self.handle = bind_indexing_status(self, self.snapshots[0].scope)
        if contribution:
            self.handle.update(self.snapshots[0])
        self._app.after_render += self.observe

    def observe(self, app):
        self.observations.append(dict(
            draft=self.input_area.text, cursor=self.input_area.buffer.cursor_position,
            focus=app.layout.current_control is self.input_area.control,
            revision=self.handle.snapshot().revision if self.handle.snapshot() else None))

    def start(self, interval):
        loop = asyncio.get_running_loop()

        def work():
            for snapshot in self.snapshots[1:]:
                if self.stop.wait(interval):
                    return
                if self.contribution:
                    self.handle.update(snapshot)
            if not self.stop.wait(max(0.6, interval)):
                loop.call_soon_threadsafe(self._app.exit)

        self.worker = Thread(target=work, name="offline-indexing-demo", daemon=False)
        self.worker.start()

    def run(self, interval):
        try:
            with renderer_facade():
                self._app.run(pre_run=lambda: self.start(interval))
        finally:
            self.stop.set()
            if self.worker is not None:
                self.worker.join(timeout=3)
            self.handle.close()
        return dict(closed=self.handle.closed,
                    worker_joined=self.worker is None or not self.worker.is_alive(),
                    late_rejected=not self.handle.update(replace(self.snapshots[-1], revision=99)),
                    refresh_interval=self._app.refresh_interval,
                    observations=self.observations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=0.4)
    parser.add_argument("--modern", action="store_true")
    parser.add_argument("--no-contribution", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not 0.05 <= args.interval <= 2:
        parser.error("interval must be 0.05..2 seconds")
    report = IndexingDemo(args.modern, not args.no_contribution).run(args.interval)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2))
    print("DEMO exited; no real indexing, transport or model replies.")


if __name__ == "__main__":
    main()
