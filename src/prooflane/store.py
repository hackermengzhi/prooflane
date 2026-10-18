"""Durable local task and evidence registry."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    uri TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    kind TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    files TEXT NOT NULL,
                    superseded_by TEXT REFERENCES notes(id),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    stage TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, title: str) -> str:
        if not title.strip():
            raise ValueError("Task title cannot be empty")
        task_id = str(uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO tasks(id, title) VALUES (?, ?)", (task_id, title))
        return task_id

    def tasks(self) -> list[dict]:
        with self.connect() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY created_at, rowid")
            ]

    def task(self, task_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown task")
        return dict(row)

    def attach(self, task_id: str, uri: str, description: str) -> str:
        if not uri.strip():
            raise ValueError("Evidence URI cannot be empty")
        evidence_id = str(uuid4())
        with self.connect() as db:
            db.execute(
                "INSERT INTO evidence(id, task_id, uri, description) VALUES (?, ?, ?, ?)",
                (evidence_id, task_id, uri, description),
            )
        return evidence_id

    def transition(self, task_id: str, status: str, detail: dict) -> None:
        # A task is pending until it starts, active while it runs, then terminal.
        # Stages within a run are recorded as events, not as task statuses.
        transitions = {
            "pending": {"active", "blocked", "cancelled"},
            "active": {"completed", "failed", "blocked", "cancelled"},
        }
        with self.connect() as connection:
            task = connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise ValueError("Unknown task")
            if status not in transitions.get(task["status"], set()):
                raise ValueError(f"Invalid task transition: {task['status']} -> {status}")
            connection.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
            connection.execute(
                "INSERT INTO events(task_id, stage, detail) VALUES (?, ?, ?)",
                (task_id, status, json.dumps(detail, ensure_ascii=False)),
            )

    def events(self, task_id: str) -> list[dict]:
        with self.connect() as connection:
            return [
                {**dict(row), "detail": json.loads(row["detail"])}
                for row in connection.execute(
                    "SELECT * FROM events WHERE task_id = ? ORDER BY sequence", (task_id,)
                )
            ]

    def record_note(self, task_id: str, workspace: str, note: dict, replaces: str | None) -> str:
        """Add a workspace note, superseding the active note it replaces in the same write."""
        note_id = str(uuid4())
        with self.connect() as connection:
            if replaces is not None:
                replaced = connection.execute(
                    "SELECT superseded_by FROM notes WHERE id = ? AND workspace = ?",
                    (replaces, workspace),
                ).fetchone()
                if replaced is None or replaced["superseded_by"] is not None:
                    raise ValueError("Only an active note of this workspace can be replaced")
            connection.execute(
                "INSERT INTO notes(id, workspace, task_id, kind, statement, evidence, files) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    note_id,
                    workspace,
                    task_id,
                    note["kind"],
                    note["statement"],
                    json.dumps(note["evidence"], ensure_ascii=False),
                    json.dumps(note["files"]),
                ),
            )
            if replaces is not None:
                connection.execute(
                    "UPDATE notes SET superseded_by = ? WHERE id = ?", (note_id, replaces)
                )
        return note_id

    def notes(self, workspace: str) -> list[dict]:
        """Active notes of a workspace, oldest first."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM notes WHERE workspace = ? AND superseded_by IS NULL ORDER BY rowid",
                (workspace,),
            ).fetchall()
        return [
            {
                **dict(row),
                "evidence": json.loads(row["evidence"]),
                "files": json.loads(row["files"]),
            }
            for row in rows
        ]

    def record_event(self, task_id: str, stage: str, detail: dict) -> None:
        with self.connect() as connection:
            task = connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None or task["status"] != "active":
                raise ValueError("Stage events require an active task")
            connection.execute(
                "INSERT INTO events(task_id, stage, detail) VALUES (?, ?, ?)",
                (task_id, stage, json.dumps(detail, ensure_ascii=False)),
            )
