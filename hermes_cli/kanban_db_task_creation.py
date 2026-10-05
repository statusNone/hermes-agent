"""Task creation and declared remediation-lineage routing.

Split from ``kanban_db`` to keep the public facade small. Origin-resident
helpers are reached late-bound through ``_kb`` so existing monkeypatches of the
facade keep working and the import cycle remains harmless.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Iterable, Optional


def _resolve_project_workspace(conn, project_id, project_source_task_id, workspace_kind, workspace_path, branch_name, board):
    """Validate workspace inputs and resolve the project-backed workspace."""
    if project_id is None and workspace_kind != "scratch":
        try:
            project_id = (_kb._board_meta_for(board).get("project_id") or "").strip() or None
        # health: allow BLE001 -- board project metadata is advisory; preserve legacy fallback.
        except Exception:
            _kb._log.debug("kanban: unable to read board project metadata", exc_info=True)
    workspace_kind = workspace_kind or "scratch"
    if workspace_kind not in _kb.VALID_WORKSPACE_KINDS:
        raise ValueError(
            f"workspace_kind must be one of {sorted(_kb.VALID_WORKSPACE_KINDS)}, "
            f"got {workspace_kind!r}"
        )
    branch_name = str(branch_name).strip() or None if branch_name is not None else None
    if branch_name and workspace_kind != "worktree":
        raise ValueError("branch_name is only valid for worktree workspaces")
    project_id, project_obj, project_repo, workspace_kind = _kb._resolve_project_link(
        conn, project_id, project_source_task_id, workspace_kind, workspace_path
    )
    return project_id, project_obj, project_repo, workspace_kind, branch_name


def _inherited_lineage(conn, continuation_of_task_id):
    """Return inherited lineage state for a declared remediation successor."""
    if not continuation_of_task_id:
        return None, None, 0, False
    predecessor = conn.execute(
        "SELECT id, finding_lineage_id FROM tasks WHERE id = ?", (continuation_of_task_id,),
    ).fetchone()
    if predecessor is None:
        raise ValueError(f"continuation task not found: {continuation_of_task_id}")
    lineage_id = _kb._nonblank_str(_kb._row_get(predecessor, "finding_lineage_id")) or predecessor["id"]
    lineage_state = conn.execute(
        "SELECT block_kind, block_recurrences FROM tasks WHERE id = ? OR finding_lineage_id = ? "
        "ORDER BY block_recurrences DESC, created_at DESC LIMIT 1", (lineage_id, lineage_id),
    ).fetchone()
    inherited_kind = _kb._row_get(lineage_state, "block_kind") if lineage_state else None
    recurrences = int(_kb._row_get(lineage_state, "block_recurrences") or 0) if lineage_state else 0
    return lineage_id, inherited_kind, recurrences, recurrences >= _kb.BLOCK_RECURRENCE_LIMIT


def create_task(
    conn: sqlite3.Connection, *, title: str, body: Optional[str] = None,
    assignee: Optional[str] = None, created_by: Optional[str] = None,
    workspace_kind: Optional[str] = None, workspace_path: Optional[str] = None,
    branch_name: Optional[str] = None, tenant: Optional[str] = None, priority: int = 0,
    parents: Iterable[str] = (), triage: bool = False, idempotency_key: Optional[str] = None,
    max_runtime_seconds: Optional[int] = None, skills: Optional[Iterable[str]] = None,
    max_retries: Optional[int] = None, model_override: Optional[str] = None,
    provider_override: Optional[str] = None, reasoning_effort: Optional[str] = None,
    goal_mode: bool = False, goal_max_turns: Optional[int] = None, initial_status: str = "running",
    session_id: Optional[str] = None, board: Optional[str] = None, project_id: Optional[str] = None,
    project_source_task_id: Optional[str] = None, creator_task_id: Optional[str] = None,
    completion_contract: Optional[str] = None, continuation_of_task_id: Optional[str] = None,
) -> str:
    """Create a task, optionally inheriting a declared finding lineage."""
    from hermes_cli.kanban_db_graph import initial_task_state, inherit_creator_origin
    from hermes_cli.kanban_pr_acceptance import validate_contract

    completion_contract = validate_contract(completion_contract)
    model_override, provider_override = _kb._validate_model_override(model_override, provider_override)
    reasoning_effort = _kb.normalize_reasoning_effort(reasoning_effort)
    assignee = _kb._canonical_assignee(assignee)
    if not title or not title.strip():
        raise ValueError("title is required")
    if initial_status not in _kb.VALID_INITIAL_STATUSES:
        raise ValueError(f"initial_status must be one of {sorted(_kb.VALID_INITIAL_STATUSES)}")
    project_id, project_obj, project_repo, workspace_kind, branch_name = _resolve_project_workspace(
        conn, project_id, project_source_task_id, workspace_kind, workspace_path, branch_name, board
    )
    parents = tuple(p for p in parents if p)
    skills_list = _kb._normalize_task_skills(skills)
    continuation_of_task_id = _kb._nonblank_str(continuation_of_task_id)

    if idempotency_key:
        row = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ? "
            "AND status != 'archived' ORDER BY created_at DESC LIMIT 1", (idempotency_key,),
        ).fetchone()
        if row:
            return row["id"]

    now = int(time.time())
    if workspace_path is None and project_repo is None and workspace_kind in {"dir", "worktree"}:
        board_default = _kb._board_meta_for(board).get("default_workdir")
        if board_default:
            workspace_path = str(board_default)

    for attempt in range(2):
        task_id = _kb._new_task_id()
        try:
            with _kb.write_txn(conn, allow_nested=True):
                task_status, tenant = initial_task_state(conn, parents, initial_status, triage, tenant)
                lineage_id, inherited_kind, inherited_recurrences, lineage_stopped = _inherited_lineage(
                    conn, continuation_of_task_id
                )
                if lineage_stopped:
                    task_status = "triage"
                if project_obj is not None and workspace_kind == "worktree":
                    if project_repo and not workspace_path:
                        workspace_path = os.path.join(project_repo, ".worktrees", task_id)
                    if not branch_name:
                        branch_name = _kb._project_branch_name(project_obj, task_id, title)

                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, title, body, assignee, status, priority,
                        created_by, created_at, workspace_kind, workspace_path,
                        branch_name, project_id, tenant, idempotency_key,
                        max_runtime_seconds, skills, max_retries, model_override,
                        provider_override, reasoning_effort, goal_mode, goal_max_turns,
                        session_id, completion_contract, finding_lineage_id,
                        continuation_of_task_id, block_kind, block_recurrences
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id, title.strip(), body, assignee, task_status, priority,
                        created_by, now, workspace_kind, workspace_path,
                        branch_name, project_id, tenant, idempotency_key,
                        _kb._opt_int(max_runtime_seconds),
                        json.dumps(skills_list) if skills_list is not None else None,
                        _kb._opt_int(max_retries), model_override, provider_override, reasoning_effort,
                        1 if goal_mode else 0, _kb._opt_int(goal_max_turns), session_id, completion_contract,
                        lineage_id, continuation_of_task_id, inherited_kind, inherited_recurrences,
                    ),
                )
                for pid in parents:
                    _kb._link(conn, pid, task_id)
                _kb._append_event(
                    conn, task_id, "created",
                    {
                        "assignee": assignee, "status": task_status, "parents": list(parents),
                        "creator_task_id": creator_task_id, "tenant": tenant,
                        "workspace_kind": workspace_kind, "workspace_path": workspace_path,
                        "branch_name": branch_name, "project_id": project_id,
                        "skills": list(skills_list) if skills_list else None,
                        "goal_mode": bool(goal_mode) or None, "model_override": model_override,
                        "provider_override": provider_override,
                        "continuation_of_task_id": continuation_of_task_id,
                        "finding_lineage_id": lineage_id,
                    },
                )
                if task_status == "blocked":
                    _kb._append_event(
                        conn, task_id, "blocked",
                        {"reason": "initial_status", "status": "blocked", "actor": created_by or "user"},
                    )
                if task_status == "todo":
                    gating = [p for p in parents if _kb._task_status(conn, p) not in ("done", "archived")]
                    if gating:
                        _kb._append_event(
                            conn, task_id, "dependency_wait",
                            {"reason": "parent_not_done", "parent": gating[0]},
                        )
                if continuation_of_task_id and task_status == "triage":
                    _kb._append_event(
                        conn, task_id, "continuation_stopped",
                        {"continuation_of": continuation_of_task_id, "lineage_id": lineage_id,
                         "reason": "block_loop_detected", "recurrences": inherited_recurrences},
                    )
                inherit_creator_origin(conn, task_id, creator_task_id, created_at=now)
                _kb._inherit_notify_subs(conn, task_id, parents, created_at=now)
            return task_id
        except sqlite3.IntegrityError:
            if attempt == 1:
                raise
    raise RuntimeError("unreachable")


# Imported last: the facade is fully populated before this late binding runs.
from hermes_cli import kanban_db as _kb  # noqa: E402
