"""Profile-local blank composer keeps state cues and default presentation intact."""
import pytest

from agent.i18n import t
from cli import HermesCLI, load_cli_config
from hermes_cli import config, skin_engine
from hermes_cli.prompt_stash import PromptStash


@pytest.fixture
def composer(tmp_path, monkeypatch):
    import cli

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(tmp_path / "managed"))
    monkeypatch.setattr(cli, "_hermes_home", tmp_path)
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "race-hysteresis-gateway")
    (tmp_path / "config.yaml").write_text("display: {}\n", encoding="utf-8")
    skins = tmp_path / "skins"
    skins.mkdir()
    (skins / "blank-prompt.yaml").write_text(
        "name: blank-prompt\nbranding:\n  prompt_symbol: '>'\n", encoding="utf-8"
    )
    config.set_config_value("display.skin", "blank-prompt")
    obj = HermesCLI.__new__(HermesCLI)
    obj.config = load_cli_config()
    skin_engine.init_skin_from_config(obj.config)
    for attr in (
        "_voice_recording", "_voice_processing", "_sudo_state", "_secret_state",
        "_connection_state", "_approval_state", "_slash_confirm_state",
        "_clarify_freetext", "_clarify_state", "_command_running", "_agent_running", "_voice_mode",
    ):
        setattr(obj, attr, False)
    obj._modern_layout = False
    obj._composer_placeholder = "Set a reminder or schedule a recurring task"
    obj._prompt_stash = PromptStash()
    obj._get_tui_terminal_width = lambda default=(80, 24): 100
    obj._use_minimal_tui_chrome = lambda width=None: False
    obj._command_spinner_frame = lambda: "⠋"
    obj._command_status = "Running command"
    yield obj
    skin_engine.set_active_skin("default")


def test_blank_prompt_round_trip_and_unchanged_defaults(composer, tmp_path, monkeypatch):
    import cli

    # The native skin controls the glyph, but defaults keep the profile and example.
    assert composer._get_tui_prompt_text() == "race-hysteresis-gateway > "
    assert composer._tui_placeholder_text() == composer._composer_placeholder
    config.set_config_value("display.composer_placeholder", "")
    config.set_config_value("display.show_profile_in_prompt", "false")
    composer.config = load_cli_config()
    assert composer.config["display"]["composer_placeholder"] == ""
    assert composer.config["display"]["show_profile_in_prompt"] is False
    assert composer._tui_placeholder_text() == ""
    assert composer._get_tui_prompt_text() == "> "

    # Another profile with no overrides retains the original behavior.
    other = tmp_path / "other"
    other.mkdir()
    (other / "config.yaml").write_text("display: {}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(other))
    monkeypatch.setattr(cli, "_hermes_home", other)
    composer.config = load_cli_config()
    assert composer._get_tui_prompt_text() == "race-hysteresis-gateway > "
    assert composer._tui_placeholder_text() == composer._composer_placeholder


@pytest.mark.parametrize("value,expected", [("Your message", "Your message"), (None, None)])
def test_custom_or_default_idle_placeholder(composer, value, expected):
    composer.config["display"]["composer_placeholder"] = value
    assert composer._tui_placeholder_text() == (composer._composer_placeholder if expected is None else expected)


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("state,icon,key", [
    ("_sudo_state", "🔐", "cli.tui.placeholder_password"),
    ("_secret_state", "🔑", "cli.tui.placeholder_secret"),
    ("_approval_state", "⚠", None),
    ("_slash_confirm_state", "⚠", "cli.tui.placeholder_choice"),
    ("_clarify_freetext", "✎", "cli.tui.placeholder_answer"),
    ("_clarify_state", "?", None),
    ("_agent_running", "☤", "busy"),
    ("_command_running", "⠋", "command"),
])
def test_blank_idle_settings_preserve_state_cues(composer, modern, state, icon, key):
    composer.config["display"].update(composer_placeholder="", show_profile_in_prompt=False)
    composer._modern_layout = modern
    composer.busy_input_mode = "interrupt"
    setattr(composer, state, {"active": True})
    expected = "" if key is None else (
        t("cli.modern.placeholder_busy_interrupt" if modern else "cli.tui.placeholder_busy")
        if key == "busy" else "⠋ Running command" if key == "command" else t(key)
    )
    assert composer._tui_placeholder_text() == expected
    assert composer._get_tui_prompt_text() == f"{icon} > "


def test_blank_idle_setting_preserves_parked_draft(composer):
    composer.config["display"]["composer_placeholder"] = ""
    composer._prompt_stash.stash("a parked draft")
    hint = composer._prompt_stash.placeholder_hint()
    assert hint
    assert composer._tui_placeholder_text() == hint
