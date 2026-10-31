#!/usr/bin/env python3
"""Bounded OFFLINE real-PTY watermark frames; no Hermes/provider startup.

Run: python scripts/watermark_status_demo.py --evidence /absolute/scratch/path
Uses the existing stock renderer demo and VT decoder. Synthetic inputs are
explicitly not a live accounting/ingestion certification.
"""
import argparse
import codecs
from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import time

from indexing_status_demo import IndexingDemo
from check_indexing_status import Screen
from hermes_cli.cli_indexing_status import RollingContextSnapshot


def deny_network(*args, **kwargs):
    raise AssertionError('OFFLINE demo forbids network connections')


def child(report, modern):
    socket.socket.connect = deny_network
    demo = IndexingDemo(modern)
    idle = demo.snapshots[6]
    values = ((12500, 25000, 15000), (25000, 25000, 15000),
              (30000, 25000, 15000), (12500, 50000, 20000), None)
    demo.snapshots = tuple(replace(idle, revision=i + 1,
        rolling_context=RollingContextSnapshot(*value) if value else None)
        for i, value in enumerate(values))
    demo.handle.update(demo.snapshots[0])
    report.write_text(json.dumps(demo.run(0.3), indent=2))


def capture(evidence, width, modern):
    import fcntl
    import pty
    import termios
    path = evidence / f'{"modern" if modern else "classic"}-{width}'
    path.mkdir(parents=True, exist_ok=True)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 12, width, 0, 0))
    before = termios.tcgetattr(slave)
    env = {'PATH': os.environ.get('PATH', ''), 'TERM': 'xterm-256color',
           'LANG': 'C.UTF-8', 'HOME': str(path), 'HERMES_HOME': str(path / 'home'),
           'TMPDIR': str(evidence)}
    command = [sys.executable, str(Path(__file__).resolve()), '--child', str(path / 'report.json')]
    if modern:
        command.append('--modern')
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env,
                               start_new_session=True)
    raw, frames = bytearray(), []
    screen = Screen(width)
    decoder = codecs.getincrementaldecoder('utf-8')()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                raw.extend(chunk)
                rows = screen.feed(decoder.decode(chunk))
                frames.append({'rows': rows, 'cursor': [screen.x, screen.y]})
            elif process.poll() is not None:
                break
        assert process.poll() is not None, 'PTY exceeded 15-second bound'
        assert process.wait(timeout=2) == 0, raw.decode(errors='replace')
        assert termios.tcgetattr(slave) == before
        assert not screen.unknown, screen.unknown
        report = json.loads((path / 'report.json').read_text())
        assert report['closed'] and report['worker_joined'] and report['late_rejected']
        assert all(o['focus'] and o['draft'] == 'keep my draft' and o['cursor'] == 5
                   for o in report['observations'])
        rows = [f['rows'][10] for f in frames if 'DEMO' in f['rows'][10]]
        assert rows
        if width == 240:
            for text in ('~50% · low 15K', '~100% · low 15K', '~120% · low 15K', '~25% · low 20K'):
                assert any(text in row and 'Index Idle' in row for row in rows), text
            assert any(' high' not in row and '25%' in row for row in rows), 'stock fallback missing'
            assert all(row.count('[') == 1 for row in rows)
        else:
            assert all('Index' not in row for row in rows)
        return {'width': width, 'modern': modern, 'passed': True, 'frames': len(frames),
                'termios_restored': True, 'child_reaped': True, 'input_bytes': 0}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        (path / 'terminal.ansi').write_bytes(raw)
        (path / 'frames.json').write_text(json.dumps(frames, indent=2))
        (path / 'screens.txt').write_text('\n\n--- FRAME ---\n'.join('\n'.join(f['rows']) for f in frames))
        os.close(master)
        os.close(slave)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--child', type=Path)
    parser.add_argument('--modern', action='store_true')
    args = parser.parse_args()
    if args.child:
        child(args.child, args.modern)
        return
    if not args.evidence or not args.evidence.is_absolute():
        parser.error('--evidence requires an absolute scratch directory')
    args.evidence.mkdir(parents=True, exist_ok=True)
    results = [capture(args.evidence, width, modern) for modern in (False, True) for width in (240, 60)]
    (args.evidence / 'summary.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
