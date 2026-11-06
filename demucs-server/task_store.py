"""Durable task metadata. Each state transition is a SQLite transaction."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc)


class TaskStore:
    def __init__(self, path, retention_hours):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_hours = retention_hours
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def __contains__(self, task_id):
        with self.connection() as db:
            return db.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is not None

    def __getitem__(self, task_id):
        with self.connection() as db:
            row = db.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return json.loads(row[0])

    def create(self, task_id, filename, **metadata):
        data = {"status": "uploading", "filename": filename,
                "created_at": utcnow().isoformat(), "stems": {}, **metadata}
        with self.connection() as db:
            result = db.execute("INSERT OR IGNORE INTO tasks VALUES (?, ?)",
                                (task_id, json.dumps(data)))
            return result.rowcount == 1

    def update(self, task_id, **changes):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            data = json.loads(row[0])
            data.update(changes)
            db.execute("UPDATE tasks SET data = ? WHERE id = ?", (json.dumps(data), task_id))
        return data

    def finish(self, task_id, status, now=None, **changes):
        if status not in {"completed", "failed"}:
            raise ValueError("Expected a terminal task status")
        now = now or utcnow()
        return self.update(task_id, status=status,
                           **{("completed_at" if status == "completed" else "failed_at"): now.isoformat()},
                           expires_at=(now + timedelta(hours=self.retention_hours)).isoformat(), **changes)

    def snapshot(self):
        with self.connection() as db:
            return {task_id: json.loads(data) for task_id, data in db.execute("SELECT id, data FROM tasks")}

    def recover_interrupted(self):
        # A new process cannot safely resume an in-flight model invocation or upload.
        # Preserve the task and report an actionable failure instead of polling forever.
        for task_id, task in self.snapshot().items():
            if task["status"] in {"uploading", "queued", "processing"}:
                self.finish(task_id, "failed", error="服务重启导致任务中断，请重新提交文件。")

    @staticmethod
    def is_expired(task, now=None):
        if task["status"] == "expired":
            return True
        return bool(task.get("expires_at") and
                    datetime.fromisoformat(task["expires_at"]) <= (now or utcnow()))
