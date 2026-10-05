"""GET /api/sessions project-selection contract."""

import time

import pytest


@pytest.fixture
def client(monkeypatch, _isolate_hermes_home):
    from starlette.testclient import TestClient

    import hermes_state
    from hermes_constants import get_hermes_home
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db")
    test_client = TestClient(app)
    test_client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return test_client


def _seed(
    session_id, *, cwd=None, source="cli", pinned=False, archived=False, started_at=None
):
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        db.create_session(session_id, source=source, cwd=cwd)
        db.append_message(session_id, role="user", content=session_id)
        if pinned:
            db.set_session_pinned(session_id, True)
        if archived:
            db.set_session_archived(session_id, True)
        if started_at is not None:
            db._conn.execute(
                "UPDATE sessions SET started_at = ? WHERE id = ?",
                (started_at, session_id),
            )
            db._conn.commit()
    finally:
        db.close()


def test_session_project_scopes_are_bounded_and_match_counts(client):
    now = time.time()
    for index in range(12):
        _seed(f"pinned-{index}", cwd="/repo/app", pinned=True, started_at=now - index)
    _seed("nested", cwd="/repo/app/nested", started_at=now + 1)
    _seed("sibling", cwd="/repo/app-child", started_at=now + 2)
    _seed("projectless")
    _seed("legacy-empty")
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        db._conn.execute("UPDATE sessions SET cwd = '' WHERE id = 'legacy-empty'")
        db._conn.commit()
    finally:
        db.close()

    exact = client.get(
        "/api/sessions",
        params={
            "cwd_exact": "/repo/app/.",
            "include_pinned": "false",
            "limit": 10,
        },
    )
    assert exact.status_code == 200
    exact_payload = exact.json()
    assert len(exact_payload["sessions"]) == 10
    assert exact_payload["total"] == 12
    assert {row["id"] for row in exact_payload["sessions"]}.isdisjoint({
        "nested",
        "sibling",
    })

    projectless = client.get(
        "/api/sessions", params={"projectless": "true", "limit": 10}
    )
    assert projectless.status_code == 200
    projectless_payload = projectless.json()
    assert {row["id"] for row in projectless_payload["sessions"]} == {
        "projectless",
        "legacy-empty",
    }
    assert projectless_payload["total"] == 2


def test_session_project_scopes_compose_with_source_and_archive_filters(client):
    _seed("exact-live", cwd="/repo/app", source="cli")
    _seed("exact-archived", cwd="/repo/app", source="cli", archived=True)
    _seed("exact-other-source", cwd="/repo/app", source="cron")
    _seed("projectless-live", source="cli")
    _seed("projectless-archived", source="cli", archived=True)

    exact = client.get(
        "/api/sessions",
        params={
            "cwd_exact": "/repo/app",
            "source": "cli",
            "archived": "only",
        },
    )
    assert exact.status_code == 200
    assert [row["id"] for row in exact.json()["sessions"]] == ["exact-archived"]
    assert exact.json()["total"] == 1

    projectless = client.get(
        "/api/sessions",
        params={
            "projectless": "true",
            "source": "cli",
            "archived": "exclude",
        },
    )
    assert projectless.status_code == 200
    assert [row["id"] for row in projectless.json()["sessions"]] == ["projectless-live"]
    assert projectless.json()["total"] == 1


def test_session_project_scopes_compose_with_an_explicit_profile(client, monkeypatch):
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB
    import hermes_cli.web_server_cron as web_server_cron

    worker_home = get_hermes_home() / "profiles" / "worker"
    worker_home.mkdir(parents=True)
    (worker_home / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        web_server_cron, "_cron_profile_home", lambda _profile: ("worker", worker_home)
    )

    worker_db = SessionDB(worker_home / "state.db")
    try:
        worker_db.create_session("worker-exact", source="cli", cwd="/repo/app")
        worker_db.append_message("worker-exact", role="user", content="worker")
    finally:
        worker_db.close()
    _seed("default-exact", cwd="/repo/app", source="cli")

    response = client.get(
        "/api/sessions",
        params={
            "profile": "worker",
            "cwd_exact": "/repo/app",
            "source": "cli",
        },
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["sessions"]] == ["worker-exact"]
    assert response.json()["total"] == 1


def test_session_project_scopes_reject_contradictory_requests_before_opening_db(
    client, monkeypatch
):
    import hermes_cli.web_routers.sessions as sessions_router

    def should_not_open(*_args, **_kwargs):
        raise AssertionError("contradictory scope opened the session DB")

    monkeypatch.setattr(
        sessions_router, "_open_session_db_for_profile", should_not_open
    )
    response = client.get(
        "/api/sessions", params={"cwd_prefix": "/repo", "projectless": "true"}
    )
    assert response.status_code == 400
    assert "only one" in response.json()["detail"]
