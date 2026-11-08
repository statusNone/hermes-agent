"""Opt-in presentation uses existing field visibility and producer facts."""
from types import SimpleNamespace

import pytest
from cli import HermesCLI, load_cli_config
from hermes_cli import config
from hermes_cli.cli_indexing_status import (
    CatchupSnapshot, IndexingScope, IndexingStatus, RollingContextSnapshot,
    RollingDisplaySnapshot,
)

FIELDS = ['model', 'context_detail', 'context_pct', 'compressions', 'bg_tasks',
          'bg_processes', 'bg_subagents', 'goal', 'focus', 'yolo', 'stash']


@pytest.fixture
def bar(tmp_path, monkeypatch):
    import cli
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(tmp_path / 'managed'))
    monkeypatch.setattr(cli, '_hermes_home', tmp_path)
    import json
    config.set_config_value('display.status_bar.fields', json.dumps(FIELDS))
    config.set_config_value('display.status_bar.compact', 'true')
    obj = HermesCLI.__new__(HermesCLI)
    obj.config = load_cli_config()
    monkeypatch.setattr(cli, 'CLI_CONFIG', obj.config)
    obj._status_bar_visible = True
    obj._modern_layout = False
    obj._prompt_stash = SimpleNamespace(indicator=lambda: '')
    obj._is_session_yolo_active = lambda: False
    obj._get_tui_terminal_width = lambda: 160
    obj.state = dict(model_short='gpt-6-astra', duration='12m',
        context_percent=20, context_length=200000, context_tokens=40000,
        compressions=99, cache_hit_pct=93, avg_latency_label='7.1s',
        avg_velocity_label='29 tok/s', prompt_elapsed='⏲ 5s', idle_since='✓ 2s')
    obj._get_status_bar_snapshot = lambda: obj.state.copy()
    scope = IndexingScope('fixture', 'session', 'generation')
    obj._indexing_status = IndexingStatus(scope, lambda: None)
    obj._indexing_status.update(CatchupSnapshot(scope, 0, 0, 0,
        authoritative_complete=True, rolling_context=RollingContextSnapshot(63300, 50000, 25000),
        rolling_display=RollingDisplaySnapshot(0, '2026-11-08 06:00')))
    return obj


def text(bar):
    return ''.join(v for _, v in bar._get_status_bar_fragments())


def test_compact_native_loader_and_producer_facts(bar):
    assert bar.config['display']['status_bar']['compact'] is True
    value = text(bar)
    assert '~63.3K/50K high' in value
    assert '[██████████] ~127% of high · low 25K' in value
    assert '⋈ 0' in value and 'Index Idle' in value
    for noise in ('2026', '@', '12m', '93%', '7.1s', '29 tok/s', '⏲', '✓', '🗜'):
        assert noise not in value
    assert bar.state['context_tokens'] == 40000
    assert '▶' not in value and '⚙' not in value and '⛓' not in value
    bar.state.update(active_background_tasks=1, active_background_processes=2, active_background_subagents=3)
    value = text(bar)
    assert '▶ 1' in value and '⚙ 2' in value and '⛓ 3' in value
    bar._is_session_yolo_active = lambda: True
    bar._prompt_stash = SimpleNamespace(indicator=lambda: '📌 1')
    assert 'YOLO' in text(bar) and '📌 1' in text(bar)


@pytest.mark.parametrize('width', [48, 60, 80, 120, 160])
def test_compact_width_preserves_above_high_and_hides_whole_index(bar, width):
    bar._get_tui_terminal_width = lambda: width
    value = text(bar)
    assert '~127% of high' in value
    assert bar._status_bar_display_width(value) <= width
    if width < 120:
        assert 'Index' not in value
    else:
        assert 'Index Idle' in value


def test_unconfigured_retains_stock_and_boundary(bar):
    bar.config = {'display': {}}
    bar._status_bar_field_set_cache = None
    value = text(bar)
    assert '☤' in value and '2026-11-08 06:00' in value
    assert '◎ 93.0%' in value and '12m' in value
    assert 'of high' not in value


def test_missing_contribution_retains_host_usage(bar):
    bar._indexing_status.close()
    value = text(bar)
    assert '40K/200K' in value and '20%' in value
    assert 'high' not in value and 'Index' not in value and '⋈' not in value
    bar.state.update(context_length=None, context_percent=None)
    value = text(bar)
    assert '--' in value and 'high' not in value
