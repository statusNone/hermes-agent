"""State-query coverage for exact-CWD and projectless session-list scopes."""

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    database = SessionDB(tmp_path / "state.db")
    try:
        yield database
    finally:
        database.close()


def _seed(db, session_id, *, cwd=None, parent_session_id=None):
    db.create_session(
        session_id, source="cli", cwd=cwd, parent_session_id=parent_session_id
    )
    db.append_message(session_id, role="user", content=session_id)


def test_project_scopes_follow_compression_tip_before_pagination_and_count(db):
    _seed(db, "app-root", cwd="/repo/app")
    db.end_session("app-root", "compression")
    _seed(db, "other-tip", cwd="/repo/other", parent_session_id="app-root")

    _seed(db, "projectless-root")
    db.end_session("projectless-root", "compression")
    _seed(
        db,
        "projectless-other-tip",
        cwd="/repo/other",
        parent_session_id="projectless-root",
    )

    _seed(db, "app-projectless-root", cwd="/repo/app")
    db.end_session("app-projectless-root", "compression")
    _seed(db, "projectless-tip", parent_session_id="app-projectless-root")
    # A continuation can persist without a workspace even though its root had one.
    db._conn.execute("UPDATE sessions SET cwd = NULL WHERE id = 'projectless-tip'")
    db._conn.commit()

    app_rows = db.list_sessions_rich(
        cwd_exact="/repo/app", include_pinned=False, order_by_last_active=True, limit=10
    )
    assert app_rows == []
    assert db.session_count(cwd_exact="/repo/app", exclude_children=True) == len(
        app_rows
    )

    other_rows = db.list_sessions_rich(
        cwd_exact="/repo/other",
        include_pinned=False,
        order_by_last_active=True,
        limit=10,
    )
    assert {row["id"] for row in other_rows} == {"other-tip", "projectless-other-tip"}
    assert all(row["cwd"] == "/repo/other" for row in other_rows)
    assert db.session_count(cwd_exact="/repo/other", exclude_children=True) == len(
        other_rows
    )

    projectless_rows = db.list_sessions_rich(
        projectless=True, include_pinned=False, order_by_last_active=True, limit=10
    )
    assert [row["id"] for row in projectless_rows] == ["projectless-tip"]
    assert not projectless_rows[0]["cwd"]
    assert db.session_count(projectless=True, exclude_children=True) == len(
        projectless_rows
    )


def test_exact_cwd_and_projectless_scopes_match_list_and_count(db):
    _seed(db, "exact", cwd="/repo/app")
    _seed(db, "nested", cwd="/repo/app/nested")
    _seed(db, "sibling", cwd="/repo/app-child")
    _seed(db, "null-project")
    _seed(db, "empty-project")
    db._conn.execute("UPDATE sessions SET cwd = '' WHERE id = 'empty-project'")
    db._conn.commit()

    exact_rows = db.list_sessions_rich(cwd_exact="/repo/app", include_pinned=False)
    assert [row["id"] for row in exact_rows] == ["exact"]
    assert db.session_count(cwd_exact="/repo/app", exclude_children=True) == len(
        exact_rows
    )

    projectless_rows = db.list_sessions_rich(projectless=True, include_pinned=False)
    assert {row["id"] for row in projectless_rows} == {"null-project", "empty-project"}
    assert db.session_count(projectless=True, exclude_children=True) == len(
        projectless_rows
    )

    all_rows = db.list_sessions_rich(include_pinned=False)
    assert {row["id"] for row in all_rows} == {
        "exact",
        "nested",
        "sibling",
        "null-project",
        "empty-project",
    }
