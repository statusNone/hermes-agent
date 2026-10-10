"""Contract and containment checks for the fixed in-process version capability."""

from __future__ import annotations

import os
import socket
import subprocess
from types import SimpleNamespace

import pytest

from tui_gateway import server


def _request(params: dict) -> dict:
    response = server.handle_request({
        "id": "exact-version",
        "method": "cli.exec.exact",
        "params": params,
    })
    assert response is not None
    return response


def _fail(*_args, **_kwargs):
    raise AssertionError("cli.exec.exact must not touch this surface")


def test_cli_exec_exact_version_returns_only_bounded_banner() -> None:
    response = _request({"command": "version"})

    assert response["result"]["code"] == 0
    assert set(response["result"]) == {"output", "code"}
    assert response["result"]["output"].startswith("Hermes Agent v")
    assert 0 < len(response["result"]["output"]) < 512
    assert "Python: " in response["result"]["output"]
    assert "cli.exec.exact" not in server._LONG_HANDLERS


def test_gateway_capabilities_advertises_registered_exact_version_handler(
    monkeypatch,
) -> None:
    def capabilities() -> dict:
        response = server.handle_request({
            "id": "capabilities",
            "method": "gateway.capabilities",
            "params": {},
        })
        assert response is not None
        return response

    response = capabilities()

    assert response["result"]["cli_exec_exact_version"] is True
    assert isinstance(response["result"]["per_session_exclusive_submit"], bool)

    monkeypatch.delitem(server._methods, "cli.exec.exact")

    assert capabilities()["result"]["cli_exec_exact_version"] is False


@pytest.mark.parametrize(
    "params, code",
    [
        ({}, 4003),
        ({"command": None}, 4003),
        ({"command": 1}, 4003),
        ({"command": ""}, 4003),
        ({"command": "status"}, 4011),
        ({"argv": ["--version"]}, 4000),
        ({"command": "version", "timeout": 1}, 4000),
        ({"command": "version", "profile": "other"}, 4000),
        ({"command": "version", "session_id": "session"}, 4000),
        ({"command": "version", "extra": True}, 4000),
    ],
)
def test_cli_exec_exact_rejects_every_non_exact_request(
    params: dict, code: int
) -> None:
    response = _request(params)

    assert response["error"]["code"] == code


def test_cli_exec_exact_touches_no_execution_network_credential_or_config_surface(
    monkeypatch,
) -> None:
    import hermes_cli.source_check as source_check
    import tools.environments.local as local_env

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _fail)
    for name in ("system", "execv", "execve", "execvp", "execvpe"):
        monkeypatch.setattr(os, name, _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)
    monkeypatch.setattr(source_check, "check_for_updates", _fail)
    monkeypatch.setattr(local_env, "hermes_subprocess_env", _fail)
    monkeypatch.setattr(server, "_write_config_key", _fail)
    monkeypatch.setattr(server, "_save_cfg", _fail)

    response = _request({"command": "version"})

    assert response["result"]["code"] == 0


def test_cli_exec_remains_the_legacy_subprocess_handler(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def captured(rid, argv, timeout, *, on_result, timeout_err, fail_code, env):
        calls.update(
            rid=rid,
            argv=argv,
            timeout=timeout,
            timeout_err=timeout_err,
            fail_code=fail_code,
            env=env,
        )
        return on_result(
            SimpleNamespace(returncode=7, stdout="legacy output", stderr="")
        )

    monkeypatch.setattr(server, "_captured_exec", captured)
    monkeypatch.setattr(
        server, "hermes_subprocess_env", lambda **kwargs: {"legacy": str(kwargs)}
    )
    response = server._methods["cli.exec"](
        "legacy", {"argv": ["status"], "timeout": 12}
    )

    assert response["result"] == {
        "blocked": False,
        "code": 7,
        "output": "legacy output",
    }
    assert calls["argv"] == [server.sys.executable, "-m", "hermes_cli.main", "status"]
    assert calls["timeout"] == 12
