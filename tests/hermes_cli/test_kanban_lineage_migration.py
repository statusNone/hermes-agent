"""Preserve pre-lineage boards across initialization and subsequent opens."""
import sqlite3

from hermes_cli import kanban_db_connect as kbc


def test_lineage_migration_preserves_rows_and_is_idempotent(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT,
                assignee TEXT, status TEXT NOT NULL, priority INTEGER DEFAULT 0,
                created_by TEXT, created_at INTEGER NOT NULL, started_at INTEGER,
                completed_at INTEGER, workspace_kind TEXT DEFAULT 'scratch',
                workspace_path TEXT, claim_lock TEXT, claim_expires INTEGER
            );
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT, created_at INTEGER NOT NULL
            );
            INSERT INTO tasks (id, title, body, status, created_at)
            VALUES ('legacy', 'Keep this task', 'Keep its contract', 'ready', 1);
        """)
    for _ in range(2):
        # Bypass the process-local optimization to exercise migration twice.
        from hermes_cli import kanban_db as kb
        kb._INITIALIZED_PATHS.clear()
        with kbc.connect(path) as conn:
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(tasks)')}
            assert {'finding_lineage_id', 'continuation_of_task_id'} <= columns
            index = conn.execute('PRAGMA index_info(idx_tasks_finding_lineage)').fetchall()
            assert [row['name'] for row in index] == ['finding_lineage_id']
            rows = conn.execute(
                'SELECT id, title, body, status, finding_lineage_id FROM tasks'
            ).fetchall()
            assert [tuple(row) for row in rows] == [
                ('legacy', 'Keep this task', 'Keep its contract', 'ready', None)
            ]
