#!/usr/bin/env python3
"""Offline acceptance checks; stdlib + the existing prompt_toolkit dependency.

No HermesCLI construction, service connection, pytest provisioning or live home.
Run this file directly. PTY evidence lives under --evidence (required).
--baseline-dir optionally compares runtime output with exported base modules.
"""
import argparse
import codecs

from dataclasses import replace
import errno
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import signal
import struct
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace

from indexing_status_demo import StockRenderer, demo_snapshots, renderer_facade, ROOT
from hermes_cli.cli_indexing_status import (
    CatchupSnapshot, IndexingStatus, append_indexing_status,
    bind_indexing_status, indexing_text,
)

EVIDENCE = None



class ContractTests(unittest.TestCase):
    def test_dynamic_counts_and_truthful_states(self):
        states = demo_snapshots()
        self.assertIn('75%', indexing_text(states[1]))
        self.assertIn('37%', indexing_text(states[2]))
        self.assertEqual((states[3].published, states[3].known_total),
                         (states[4].published, states[4].known_total))
        self.assertEqual([s.status for s in states], [
            'Indexing', 'Indexing', 'Indexing', 'Failed', 'Indexing',
            'Verifying', 'Idle', 'Stale', 'Unavailable', 'Unknown', 'Idle'])
        self.assertNotIn('100%', indexing_text(states[5]))
        self.assertNotIn('%', indexing_text(states[9]))
        empty = CatchupSnapshot(states[0].scope, 0, 0, 0)
        self.assertEqual(empty.status, 'Verifying')
        self.assertEqual(replace(empty, authoritative_complete=True).status, 'Idle')
        for changes in ({'published': -1}, {'known_total': True}, {'revision': True},
                        {'pending': 1.5}, {'known_total': 5}, {'published': 4},
                        {'health': 'ready'}, {'authoritative_complete': 1},
                        {'authoritative_complete': True}, {'scope': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(states[0], **changes)

    def test_owner_revision_clear_close_and_rebind(self):
        cli = StockRenderer()
        calls = []
        cli._app = SimpleNamespace(invalidate=lambda: calls.append(True))
        state = demo_snapshots()[0]
        handle = bind_indexing_status(cli, state.scope)
        with self.assertRaises(RuntimeError):
            bind_indexing_status(cli, state.scope)
        self.assertTrue(handle.update(state))
        self.assertFalse(handle.update(state))
        for field in ('profile', 'session', 'generation'):
            self.assertFalse(handle.update(replace(state, revision=1,
                scope=replace(state.scope, **{field: 'other'}))))
        self.assertTrue(handle.clear(2))
        self.assertIsNone(handle.snapshot())
        self.assertFalse(handle.update(replace(state, revision=1)))
        handle.close()
        handle.close()
        self.assertFalse(handle.update(replace(state, revision=3)))
        self.assertFalse(handle.clear(3))
        self.assertEqual(len(calls), 3)
        self.assertIsNone(handle._repaint)
        fresh = bind_indexing_status(cli, replace(state.scope, generation='fresh'))
        self.assertTrue(fresh.update(replace(state, scope=fresh.scope)))
        fresh.close()

    def test_absent_hidden_and_closed_contributions_preserve_fragments(self):
        cli = StockRenderer()
        fragments = [('stock', 'stock context')]
        self.assertIs(append_indexing_status(cli, fragments, 200), fragments)
        cli._indexing_status = IndexingStatus(demo_snapshots()[0].scope, lambda: None)
        for state in demo_snapshots():
            cli._indexing_status.update(state)
            for width in range(76):
                self.assertIs(append_indexing_status(cli, fragments, width), fragments)
        cli._indexing_status.close()
        self.assertIs(append_indexing_status(cli, fragments, 200), fragments)

    def test_actual_renderers_width_reservations_and_context_preservation(self):
        with renderer_facade():
            for modern in (False, True):
                cli = StockRenderer(modern)
                cli._indexing_status = IndexingStatus(demo_snapshots()[0].scope, lambda: None)
                for state in demo_snapshots():
                    cli._indexing_status.update(state)
                    for width in range(10, 241):
                        cli.width = width
                        for title, vim in (('', False), ('session title', False),
                                           ('session title', True), ('', True)):
                            cli.state['session_title'] = title
                            cli._vim_mode = vim
                            cli._app = SimpleNamespace(vi_state=SimpleNamespace(input_mode='vi-insert'))
                            result = cli._get_status_bar_fragments()
                            text = ''.join(v for _, v in result)
                            self.assertLessEqual(cli._status_bar_display_width(text), width)
                            if width < 76:
                                self.assertNotIn('Index ', text)
                            if 'Index ' in text:
                                self.assertIn(indexing_text(state), text, (modern, width, title, vim, text))
                                self.assertEqual(text.count(cli._build_context_bar(25)), 1)
                cli._indexing_status.close()


class Screen:
    """Decode the finite VT subset emitted by this demo, including UTF-8 cells."""
    def __init__(self, width, height=12):
        self.width, self.height = width, height
        self.rows = [[' '] * width for _ in range(height)]
        self.x = self.y = 0
        self.pending = ''
        self.unknown = []

    def resize(self, width):
        self.rows = [(row + [' '] * width)[:width] for row in self.rows]
        self.width = width
        self.x = min(self.x, width - 1)

    def feed(self, text):
        from prompt_toolkit.utils import get_cwidth
        self.pending += text
        while self.pending:
            text = self.pending
            if text.startswith('\x1b['):
                match = re.match(r'\x1b\[([0-?]*)([ -/]*)([@-~])', text)
                if not match:
                    break
                self.pending = text[match.end():]
                params, _intermediate, command = match.groups()
                values = [int(v or 0) for v in params.lstrip('?<>').split(';')]
                n = values[0] or 1
                if command in 'Hf':
                    self.y, self.x = n - 1, (values[1] or 1) - 1 if len(values) > 1 else 0
                elif command == 'A': self.y -= n
                elif command == 'B': self.y += n
                elif command == 'C': self.x += n
                elif command == 'D': self.x -= n
                elif command == 'G': self.x = n - 1
                elif command == 'K':
                    start, end = ((0, self.width) if values[0] == 2 else
                                  (0, self.x + 1) if values[0] == 1 else (self.x, self.width))
                    self.rows[self.y][start:end] = [' '] * (end - start)
                elif command == 'J':
                    if values[0] in (2, 3):
                        self.rows = [[' '] * self.width for _ in range(self.height)]
                    else:
                        self.rows[self.y][self.x:] = [' '] * (self.width - self.x)
                        for row in range(self.y + 1, self.height):
                            self.rows[row] = [' '] * self.width
                elif command not in 'mhlntqc':
                    self.unknown.append(match.group())
                self.x = max(0, min(self.width - 1, self.x))
                self.y = max(0, min(self.height - 1, self.y))
            elif text.startswith('\x1b'):
                if len(text) < 2 or (text[1] in '()' and len(text) < 3):
                    break
                count = 3 if text[1] in '()' else 2
                token, self.pending = text[:count], text[count:]
                if token not in ('\x1b(B', '\x1b=', '\x1b>'):
                    self.unknown.append(token)
            else:
                char, self.pending = text[0], text[1:]
                if char == '\r': self.x = 0
                elif char == '\n': self.y = min(self.height - 1, self.y + 1)
                elif char == '\b': self.x = max(0, self.x - 1)
                elif char >= ' ':
                    cells = get_cwidth(char)
                    if cells:
                        self.rows[self.y][self.x] = char
                        if cells == 2 and self.x + 1 < self.width:
                            self.rows[self.y][self.x + 1] = ''
                        self.x = min(self.width - 1, self.x + cells)
        return [''.join(row).rstrip() for row in self.rows]


@unittest.skipUnless(os.name == 'posix', 'real POSIX PTY required')
class PtyTests(unittest.TestCase):
    def run_pty(self, modern=False, resize=False, keyboard=False, absent=False):
        import fcntl
        import pty
        import termios
        label = ('modern' if modern else 'classic') + ('-resize' if resize else '-keys' if keyboard else '-absent' if absent else '-full')
        evidence = EVIDENCE / label
        evidence.mkdir(parents=True, exist_ok=True)
        report = evidence / 'child.json'
        master, slave = pty.openpty()
        before = termios.tcgetattr(slave)
        width = 240
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 12, width, 0, 0))
        # Explicit clean child environment: no credentials, runtime/profile or gateway.
        env = {'PATH': os.defpath, 'TERM': 'xterm-256color', 'LANG': 'C.UTF-8',
               'PROMPT_TOOLKIT_NO_CPR': '1', 'PYTHONDONTWRITEBYTECODE': '1',
               'HOME': str(evidence), 'HERMES_HOME': str(evidence / 'home'),
               'TMPDIR': str(evidence)}
        command = [sys.executable, str(ROOT / 'scripts/indexing_status_demo.py'),
                   '--interval', '0.3', '--report', str(report)]
        if modern: command.append('--modern')
        if absent: command.append('--no-contribution')
        child = None
        raw, frames = bytearray(), []
        screen = Screen(width)
        decoder = codecs.getincrementaldecoder('utf-8')()
        phase = 0
        input_bytes = 0
        try:
            child = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                     cwd=ROOT, env=env, start_new_session=True)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.05)
                if readable:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO: raise
                        break
                    if not chunk: break
                    raw.extend(chunk)
                    rows = screen.feed(decoder.decode(chunk))
                    frames.append(dict(width=width, rows=rows, cursor=[screen.x, screen.y]))
                    row = rows[10]
                    if resize and phase == 0 and '75%' in row:
                        width, phase = 60, 1
                        screen.resize(width)
                        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 12, width, 0, 0))
                        os.kill(child.pid, signal.SIGWINCH)
                    elif resize and phase == 1 and 'Index' not in row and 'DEMO' in row:
                        width, phase = 240, 2
                        screen.resize(width)
                        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 12, width, 0, 0))
                        os.kill(child.pid, signal.SIGWINCH)
                    elif keyboard and phase == 0 and 'Index Indexing' in row:
                        input_bytes += os.write(master, b'Z')
                        phase = 1
                    elif keyboard and phase == 1 and 'keep Zmy draft' in rows[11]:
                        input_bytes += os.write(master, b'\x03')
                        phase = 2
                elif child.poll() is not None:
                    break
            self.assertIsNotNone(child.poll(), 'child exceeded deadline')
            self.assertEqual(child.wait(timeout=2), 0, raw.decode('utf-8', errors='replace'))
            self.assertEqual(termios.tcgetattr(slave), before)
            for escape in (b'\x1b[?1049l', b'\x1b[?2004l', b'\x1b[?25h'):
                self.assertIn(escape, raw)
            self.assertEqual(screen.unknown, [])
            data = json.loads(report.read_text())
            self.assertTrue(data['closed'])
            self.assertTrue(data['worker_joined'])
            self.assertTrue(data['late_rejected'])
            self.assertEqual(data['refresh_interval'], 0)
            self.assertTrue(all(o['focus'] for o in data['observations']))
            if keyboard:
                self.assertEqual(phase, 2)
                self.assertTrue(any(o['draft'] == 'keep Zmy draft' and o['cursor'] == 6
                                    for o in data['observations']))
            else:
                self.assertEqual(input_bytes, 0)
                self.assertTrue(all(o['draft'] == 'keep my draft' and o['cursor'] == 5
                                    for o in data['observations']))
            full_frames = [f for f in frames if f['width'] == 240 and 'DEMO' in f['rows'][10]]
            for frame in full_frames:
                row = frame['rows'][10]
                self.assertEqual(row.count(StockRenderer()._build_context_bar(25)), 1)
                self.assertFalse(any('Index ' in r for r in frame['rows'][:10] + frame['rows'][11:]))
            if absent:
                self.assertTrue(full_frames)
                self.assertTrue(all('Index ' not in f['rows'][10] for f in frames))
            elif not keyboard:
                expected = ['1/4', '3/4', '3/8', 'Failed', 'Verifying', 'Index Idle',
                            'Index Stale', 'Index Unavailable', '2/?', 'Index Idle']
                index = -1
                for text in expected:
                    matches = [i for i in range(index + 1, len(frames)) if
                               text in frames[i]['rows'][10] and frames[i]['cursor'] == [5, 11]]
                    self.assertTrue(matches, f'missing ordered terminal state: {text}')
                    index = matches[0]
                if resize:
                    self.assertEqual(phase, 2)
                    narrow = [f for f in frames if f['width'] == 60 and 'DEMO' in f['rows'][10]]
                    self.assertTrue(narrow)
                    self.assertTrue(all('Index' not in f['rows'][10] for f in narrow))
                    hidden_at = next(i for i, f in enumerate(frames) if f in narrow)
                    self.assertTrue(any(f['width'] == 240 and '3/4' in f['rows'][10]
                                        for f in frames[hidden_at + 1:]),
                                    'resize did not restore the unchanged snapshot')
            (evidence / 'result.json').write_text(json.dumps(dict(
                passed=True, input_bytes=input_bytes, resize_phase=phase,
                termios_restored=True, child_reaped=True), indent=2))
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=2)
            (evidence / 'terminal.ansi').write_bytes(raw)
            (evidence / 'frames.json').write_text(json.dumps(frames, indent=2))
            (evidence / 'screens.txt').write_text('\n\n--- FRAME ---\n'.join(
                f"width={f['width']} cursor={f['cursor']}\n" + '\n'.join(f['rows']) for f in frames))
            os.close(master)
            os.close(slave)
        print(f'PTY evidence: {evidence}', flush=True)

    def test_classic_no_input(self): self.run_pty()
    def test_modern_no_input(self): self.run_pty(modern=True)
    def test_classic_resize(self): self.run_pty(resize=True)
    def test_modern_resize(self): self.run_pty(modern=True, resize=True)
    def test_input_and_ctrl_c_cleanup(self): self.run_pty(keyboard=True)
    def test_modern_input_and_ctrl_c_cleanup(self): self.run_pty(modern=True, keyboard=True)
    def test_no_contribution_pty(self): self.run_pty(absent=True)


def parity_check(directory):
    """Execute exported baseline modules; compare outputs, not source shape."""
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    import hermes_cli.cli_modern_layout as current_modern
    baseline_status = load('baseline_status', directory / 'cli_status_bar_mixin.py')
    baseline_modern = load('baseline_modern', directory / 'cli_modern_layout.py')
    base_type = type('BaseRenderer', (StockRenderer,), {
        name: value for name, value in vars(baseline_status.CLIStatusBarMixin).items()
        if name in ('_get_status_bar_fragments', '_build_status_bar_text')})
    count = 0
    with renderer_facade():
        for modern in (False, True):
            for width in range(10, 241):
                for title in ('', 'stock title', '標題'):
                    for vim in (False, True):
                        new, old = StockRenderer(modern), base_type(modern)
                        for obj in (new, old):
                            obj.width = width
                            obj.state['session_title'] = title
                            obj._vim_mode = vim
                            obj._app = SimpleNamespace(vi_state=SimpleNamespace(input_mode='vi-insert'))
                        actual = new._get_status_bar_fragments()
                        try:
                            sys.modules['hermes_cli.cli_modern_layout'] = baseline_modern
                            expected = old._get_status_bar_fragments()
                        finally:
                            sys.modules['hermes_cli.cli_modern_layout'] = current_modern
                        if actual != expected or new._build_status_bar_text(width) != old._build_status_bar_text(width):
                            raise AssertionError((modern, width, title, vim, actual, expected))
                        count += 1
    (EVIDENCE / 'baseline-parity.json').write_text(json.dumps({'comparisons': count, 'passed': True}))
    print(f'Exact base no-contribution runtime parity: {count} cases', flush=True)


def main():
    global EVIDENCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--baseline-dir', type=Path)
    args = parser.parse_args()
    EVIDENCE = args.evidence.resolve()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if args.baseline_dir:
        parity_check(args.baseline_dir)
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    (EVIDENCE / 'summary.json').write_text(json.dumps(dict(
        tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors),
        skipped=len(result.skipped), passed=result.wasSuccessful()), indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
