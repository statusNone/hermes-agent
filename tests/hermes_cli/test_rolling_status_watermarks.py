"""Offline display contracts; also runnable with stdlib unittest (no provisioning)."""
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from indexing_status_demo import StockRenderer, renderer_facade, demo_snapshots
from hermes_cli.cli_indexing_status import (
    IndexingStatus, RollingContextSnapshot, RollingDisplaySnapshot, indexing_text,
)


class WatermarkTests(unittest.TestCase):
    def make_renderer(self, current=12500, high=25000, low=15000, modern=False):
        cli = StockRenderer(modern)
        cli.width = 240
        state = replace(demo_snapshots()[6], rolling_context=RollingContextSnapshot(current, high, low))
        cli._indexing_status = IndexingStatus(state.scope, lambda: None)
        cli._indexing_status.update(state)
        return cli, state

    def test_true_percent_and_one_context_gauge(self):
        with renderer_facade():
            for modern in (False, True):
                for current, percent in ((0, 0), (12500, 50), (25000, 100), (30000, 120)):
                    cli, _ = self.make_renderer(current, modern=modern)
                    text = ''.join(v for _, v in cli._get_status_bar_fragments())
                    self.assertIn('/25K high', text)
                    self.assertIn(f'~{percent}% · low 15K', text)
                    self.assertIn(cli._build_context_bar(percent), text)
                    self.assertEqual(text.count('['), 1)
                    self.assertNotIn('/32', text)
                    self.assertIn('Index Idle', text)
                    self.assertEqual(cli.state['context_percent'], 25)

    def test_config_changes_and_plain_renderer(self):
        with renderer_facade():
            cli, state = self.make_renderer(high=50000, low=20000)
            text = cli._build_status_bar_text(240)
            self.assertIn('~12.5K/50K high', text)
            self.assertIn('~25% · low 20K', text)
            self.assertIn(cli._build_context_bar(25), text)
            self.assertEqual(indexing_text(state), 'Index Idle')

    def test_missing_inactive_clear_close_exact_stock(self):
        with renderer_facade():
            for modern in (False, True):
                for width in (20, 51, 52, 60, 75, 76, 100, 240):
                    cli = StockRenderer(modern)
                    cli.width = width
                    stock = cli._get_status_bar_fragments()
                    state = demo_snapshots()[0]
                    handle = cli._indexing_status = IndexingStatus(state.scope, lambda: None)
                    self.assertEqual(cli._get_status_bar_fragments(), stock)
                    handle.update(state)  # no rolling contribution: stock context segments
                    expected = cli._status_bar_segments(cli.state, width, None, False, styled=True)
                    handle.update(replace(state, revision=1, rolling_context=None))
                    self.assertEqual(cli._status_bar_segments(cli.state, width, None, False, styled=True), expected)
                    handle.clear(2)
                    self.assertEqual(cli._get_status_bar_fragments(), stock)
                    handle.close()
                    self.assertEqual(cli._get_status_bar_fragments(), stock)

    def test_only_context_segments_change_and_indexing_is_independent(self):
        with renderer_facade():
            cli, state = self.make_renderer()
            cli.state.update(compressions=3, active_background_tasks=2, avg_latency_label='1.2s',
                             avg_velocity_label='40 tok/s', prompt_elapsed='2s', idle_since='3s',
                             focus_label='focus', cache_hit_pct=75, goal_active=True, goal_turns_used=1,
                             goal_max_turns=5)
            fields = frozenset(('model', 'duration', 'cache_hit', 'compressions', 'bg_tasks',
                                'latency', 'tps', 'prompt_elapsed', 'idle_since', 'focus', 'goal'))
            active = cli._status_bar_segments(cli.state, 240, fields, True, styled=True)
            cli._indexing_status.update(replace(state, revision=7, rolling_context=None))
            self.assertEqual(active, cli._status_bar_segments(cli.state, 240, fields, True, styled=True))
            progress = replace(demo_snapshots()[0], revision=8, rolling_context=state.rolling_context)
            cli._indexing_status.update(progress)
            text = ''.join(v for _, v in cli._get_status_bar_fragments())
            self.assertIn(indexing_text(progress), text)
            self.assertEqual(indexing_text(progress), indexing_text(replace(progress, rolling_context=None)))

    def test_cell_widths_and_stock_narrow_tiers(self):
        with renderer_facade():
            for modern in (False, True):
                cli, _ = self.make_renderer(30000, modern=modern)
                for width in range(10, 241):
                    cli.width = width
                    text = ''.join(v for _, v in cli._get_status_bar_fragments())
                    self.assertLessEqual(cli._status_bar_display_width(text), width)
                    if width < 52:
                        self.assertNotIn('high', text)
                        self.assertNotIn('low', text)
                    if width < 76:
                        self.assertNotIn('Index', text)

    def test_index_gauge_reuses_stock_glyphs_width_and_theme(self):
        with renderer_facade():
            for modern in (False, True):
                for published, percent in ((1, 25), (2, 50), (3, 75)):
                    cli, state = self.make_renderer(modern=modern)
                    progress = replace(demo_snapshots()[0], revision=8,
                        published=published, pending=3-published,
                        rolling_context=state.rolling_context)
                    cli._indexing_status.update(progress)
                    fragments = cli._get_status_bar_fragments()
                    text = ''.join(v for _, v in fragments)
                    self.assertIn('~50% · low 15K', text)
                    self.assertIn(f'Index Indexing {published}/4', text)
                    bars = [(s, v) for s, v in fragments if v.startswith('[')]
                    background = ' bg:default' if modern else ''
                    self.assertEqual(bars, [
                        (cli._status_bar_context_style(50) + background, cli._build_context_bar(50)),
                        (cli._status_bar_context_style(percent) + background, cli._build_context_bar(percent))])
                    self.assertTrue(all(cli._status_bar_display_width(v) == 12 for _, v in bars))
                    self.assertNotIn('#', text)

    def test_committed_rolling_count_and_boundary_replace_stock_only_when_present(self):
        with renderer_facade():
            cli, state = self.make_renderer()
            cli.state['compressions'] = 7
            stock = ''.join(v for _, v in cli._get_status_bar_fragments())
            self.assertIn('🗜️ 7', stock)
            first = replace(state, revision=8,
                rolling_display=RollingDisplaySnapshot(1, '10:30 AM'))
            self.assertTrue(cli._indexing_status.update(first))
            text = ''.join(v for _, v in cli._get_status_bar_fragments())
            self.assertIn('⋈ 1 @10:30 AM', text)
            self.assertNotIn('🗜️ 7', text)
            # A fresh publication of the same committed selection is a no-op:
            # host rendering must not turn a repaint into another commit.
            noop = replace(first, revision=9)
            self.assertTrue(cli._indexing_status.update(noop))
            self.assertIn('⋈ 1 @10:30 AM',
                          ''.join(v for _, v in cli._get_status_bar_fragments()))
            committed = replace(first, revision=10,
                rolling_display=RollingDisplaySnapshot(2, '10:45 AM'))
            self.assertTrue(cli._indexing_status.update(committed))
            changed = ''.join(v for _, v in cli._get_status_bar_fragments())
            self.assertIn('⋈ 2 @10:45 AM', changed)
            cli.width = 60
            narrow = ''.join(v for _, v in cli._get_status_bar_fragments())
            self.assertIn('⋈ 2', narrow)
            self.assertNotIn('@10:45 AM', narrow)

    def test_scope_revision_and_invalid_data(self):
        cli, state = self.make_renderer()
        for field in ('profile', 'session', 'generation'):
            self.assertFalse(cli._indexing_status.update(replace(state, revision=9,
                scope=replace(state.scope, **{field: 'other'}))))
        self.assertFalse(cli._indexing_status.update(replace(state, revision=0)))
        for values in ((-1, 25000, 15000), (1, 0, 0), (1, 2, 2), (True, 2, 1)):
            with self.assertRaises(ValueError):
                RollingContextSnapshot(*values)
        for values in ((-1, '10:30'), (True, '10:30'), (1, ''), (1, 'x' * 129)):
            with self.assertRaises(ValueError):
                RollingDisplaySnapshot(*values)


if __name__ == '__main__':
    unittest.main()
