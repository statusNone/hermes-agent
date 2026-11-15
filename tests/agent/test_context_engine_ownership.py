"""Context engines may own request retention instead of host compression."""

from types import SimpleNamespace

from agent.agent_init import _host_automatic_compression_enabled
from agent.agent_init_compression_notice import _emit_compression_summary


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


def test_default_and_legacy_engines_keep_configured_host_behavior():
    assert _host_automatic_compression_enabled(True, SimpleNamespace()) is True
    assert _host_automatic_compression_enabled(True, SimpleNamespace(manages_own_context=False)) is True
    assert _host_automatic_compression_enabled(False, SimpleNamespace(manages_own_context=False)) is False