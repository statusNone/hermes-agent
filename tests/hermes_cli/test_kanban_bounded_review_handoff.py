"""Regression coverage for separate review successors and block finding lineage."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _running(conn, title: str = "implementation") -> str:
    task_id = kb.create_task(conn, title=title, assignee="builder")
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (task_id,))
    assert kb.claim_task(conn, task_id, claimer="builder:1") is not None
    return task_id


def _block_until_triage(conn, task_id: str) -> None:
    assert kb.block_task(conn, task_id, reason="missing approval", kind="needs_input")
    assert kb.unblock_task(conn, task_id)
    assert kb.claim_task(conn, task_id, claimer="builder:2") is not None
    assert kb.block_task(conn, task_id, reason="missing approval", kind="needs_input")
    assert kb.get_task(conn, task_id).status == "triage"


def test_independent_review_is_atomic_and_has_exact_successor_contract(kanban_home: Path) -> None:
    with kbc.connect_closing() as conn:
        source = _running(conn, "historical initial candidate")
        review_id = kb.create_independent_review_task(
            conn, source, reviewer="reviewer", candidate="commit:abc123",
            review_contract="Review commit:abc123 against the remediation acceptance criteria.",
        )
        review = kb.get_task(conn, review_id)
        assert review is not None
        assert (review.status, review.assignee, review.review_of_task_id) == ("review", "reviewer", source)
        assert review.review_candidate == "commit:abc123"
        assert review.review_contract == "Review commit:abc123 against the remediation acceptance criteria."
        assert "historical initial candidate" not in (review.body or "")
        claimed = kb.claim_review_task(conn, review_id, claimer="reviewer:1")
        assert claimed is not None and claimed.assignee == "reviewer"
        handoff = [e for e in kb.list_events(conn, source) if e.kind == "independent_review_handed_off"][-1]
        assert handoff.payload["review_task_id"] == review_id


def test_declared_continuation_keeps_block_recurrence_and_stops_after_loop(kanban_home: Path) -> None:
    with kbc.connect_closing() as conn:
        original = _running(conn)
        assert kb.block_task(conn, original, reason="missing approval", kind="needs_input")
        successor = kb.create_task(
            conn, title="remediate approval", assignee="builder", continuation_of_task_id=original,
        )
        continued = kb.get_task(conn, successor)
        assert (continued.finding_lineage_id, continued.continuation_of_task_id) == (original, original)
        assert (continued.block_kind, continued.block_recurrences) == ("needs_input", 1)
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (successor,))
        assert kb.claim_task(conn, successor, claimer="builder:2") is not None
        assert kb.block_task(conn, successor, reason="missing approval", kind="needs_input")
        assert kb.get_task(conn, successor).status == "triage"
        stopped = kb.create_task(
            conn, title="another remediation", assignee="builder", continuation_of_task_id=original,
        )
        parked = kb.get_task(conn, stopped)
        assert parked.status == "triage"
        assert parked.block_recurrences == kb.BLOCK_RECURRENCE_LIMIT
        assert [e for e in kb.list_events(conn, stopped) if e.kind == "continuation_stopped"]


def test_cli_labels_reopen_as_implementer_path_and_offers_independent_handoff(kanban_home: Path) -> None:
    (kanban_home / "profiles" / "reviewer").mkdir(parents=True)
    (kanban_home / "profiles" / "reviewer" / "config.yaml").write_text("{}\n")
    help_text = kc.run_slash("reopen-review --help")
    assert "implementer" in help_text
    assert "independent re-review" in help_text
    with kbc.connect_closing() as conn:
        source = kb.create_task(conn, title="source", assignee="builder")
    output = kc.run_slash(
        f"handoff-independent-review {source} --reviewer reviewer "
        "--candidate commit:def456 --review-contract 'Review commit:def456 only'"
    )
    assert "Created independent review" in output


def test_independent_review_tool_requires_exact_candidate_and_contract(kanban_home: Path) -> None:
    from tools import kanban_tools as tools

    (kanban_home / "profiles" / "reviewer").mkdir(parents=True)
    (kanban_home / "profiles" / "reviewer" / "config.yaml").write_text("{}\n")
    with kbc.connect_closing() as conn:
        source = kb.create_task(conn, title="source", assignee="builder")
    missing = json.loads(tools._handle_handoff_independent_review({
        "implementation_task_id": source, "reviewer": "reviewer", "candidate": "commit:abc",
    }))
    assert missing["ok"] is False
    created = json.loads(tools._handle_handoff_independent_review({
        "implementation_task_id": source, "reviewer": "reviewer", "candidate": "commit:abc",
        "review_contract": "Review commit:abc against the fix criteria.",
    }))
    assert created["ok"] is True
    with kbc.connect_closing() as conn:
        review = kb.get_task(conn, created["task_id"])
        assert review is not None
        assert review.review_candidate == "commit:abc"
