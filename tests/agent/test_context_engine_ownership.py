"""Context engines may own request retention instead of host compression."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.agent_init import _host_automatic_compression_enabled
from agent.agent_init_compression_notice import _emit_compression_summary


def _actual_hysteresis_engine(monkeypatch):
    """Construct the candidate plugin engine without its external reader transport."""
    pytest.importorskip("hysteresis_context")
    import hysteresis_context.engine as hysteresis_engine

    # The command supplies the isolated candidate plugin path; reject a differently
    # shaped import rather than silently exercising an installed package.
    module_file = hysteresis_engine.__file__
    assert module_file is not None
    module_path = Path(module_file).resolve()
    assert module_path.parts[-3:] == ("plugins", "hysteresis_context", "engine.py")
    monkeypatch.setattr(
        hysteresis_engine,
        "configuration",
        lambda **_kwargs: {"session_binding": "native"},
    )
    return hysteresis_engine.HysteresisContextEngine()


def _actual_hysteresis_agent(monkeypatch):
    """Run host construction with the real engine, mocking only external binding I/O."""
    import agent.agent_init as agent_init
    from run_agent import AIAgent

    engine = _actual_hysteresis_engine(monkeypatch)
    with patch.object(agent_init, "_select_context_engine", return_value=engine):
        agent = AIAgent(
            model="offline-context-test",
            provider="openai",
            api_mode="chat_completions",
            api_key="offline-not-a-credential",
            base_url="http://127.0.0.1:1/v1",
            enabled_toolsets=[],
            skip_memory=True,
            skip_context_files=True,
            skip_background_review=True,
            load_soul_identity=False,
            max_iterations=1,
            fallback_model=None,
            quiet_mode=False,
        )
    return agent, engine


def test_actual_hysteresis_host_startup_disables_automatic_compression_and_aux_notice(
    monkeypatch, capsys
):
    agent, engine = _actual_hysteresis_agent(monkeypatch)
    try:
        assert agent.context_compressor is engine
        assert engine.manages_own_context is True
        assert agent.compression_enabled is False
        # Host construction clears the host-only autoraised threshold before startup emits notices.
        assert agent._compression_threshold_autoraised is None
        assert "auto-compaction was raised" not in capsys.readouterr().out
    finally:
        agent.close()


def test_actual_hysteresis_skips_above_threshold_host_preflight_and_aux_feasibility(
    monkeypatch,
):
    """The ordinary preflight gate cannot reach host compression for a real Hysteresis engine."""
    from agent.turn_preflight import PreflightGateVerdict, run_preflight_compression
    import agent.turn_preflight as preflight

    agent, engine = _actual_hysteresis_agent(monkeypatch)
    try:
        engine.should_compress = MagicMock(return_value=True)
        agent._compress_context = MagicMock()
        feasibility = MagicMock()
        monkeypatch.setattr(preflight, "ensure_compression_feasibility_checked", feasibility)
        messages = [
            {"role": "system", "content": "fixed"},
            {"role": "user", "content": "above threshold"},
        ]
        verdict = PreflightGateVerdict(
            action="", pending_moa_prepared_request=None, messages=messages,
            active_system_prompt="fixed", conversation_history=[], api_call_count=1,
            compression_attempts=0, final_response="", failed=False, _turn_exit_reason=None,
            _compression_timeout_exhausted=False, _preflight_compression_blocked=False,
            _provider_overflow_recovery_pending=False, _last_preflight_pressure=None,
        )
        result = run_preflight_compression(
            agent,
            verdict,
            compressor=engine,
            request_pressure_tokens=999_999,
            provider_overflow_preflight=False, defer_preflight=lambda _tokens: False,
            moa_prepared_request=None, system_message="fixed", user_message=messages[-1],
            max_compression_attempts=2, effective_task_id="disposable",
        )
        assert result.action == "fallthrough"
        engine.should_compress.assert_not_called()
        feasibility.assert_not_called()
        agent._compress_context.assert_not_called()
    finally:
        agent.close()


def test_actual_hysteresis_live_refresh_cannot_reenable_host_compression(monkeypatch):
    """The TUI's ordinary next-turn config refresh retains engine ownership."""
    from tui_gateway import server

    agent, _engine = _actual_hysteresis_agent(monkeypatch)
    try:
        session = {"agent": agent, "session_key": "disposable"}
        monkeypatch.setattr(server, "_load_cfg", lambda: {"compression": {"enabled": True}})
        server._sync_agent_compression_with_config("sid", session)
        assert agent.compression_enabled is False
    finally:
        agent.close()


def test_default_and_legacy_engines_keep_configured_host_behavior():
    assert _host_automatic_compression_enabled(True, SimpleNamespace()) is True
    assert _host_automatic_compression_enabled(True, SimpleNamespace(manages_own_context=False)) is True
    assert _host_automatic_compression_enabled(False, SimpleNamespace(manages_own_context=False)) is False


def test_owning_engine_disables_host_automatic_compression_and_banner(capsys):
    engine = SimpleNamespace(manages_own_context=True, context_length=128_000)
    agent = SimpleNamespace(
        compression_enabled=_host_automatic_compression_enabled(True, engine),
        context_compressor=engine,
        _compression_threshold_autoraised={"model": "gpt-5.5", "from": 0.5, "to": 0.75},
        quiet_mode=False,
        _safe_print=lambda *_args, **_kwargs: None,
    )
    config = SimpleNamespace(enabled=True, autoraise_notice_enabled=True, threshold=0.5)

    _emit_compression_summary(agent, config)

    assert agent.compression_enabled is False
    assert "auto-compression disabled" in capsys.readouterr().out
    assert agent._compression_warning is None
