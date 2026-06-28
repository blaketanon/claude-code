"""Project persistence.

A tiny SQLite-backed store (stdlib ``sqlite3``) keeps the full state of every
:class:`~ycm.models.VideoProject` as a JSON blob. This makes the pipeline
resumable and gives the CLI ``list``/``show`` something to read from.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import ProjectStatus, VideoProject


class ProjectStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id          TEXT PRIMARY KEY,
                topic       TEXT NOT NULL,
                status      TEXT NOT NULL,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                data        TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def save(self, project: VideoProject) -> None:
        project.touch()
        self._conn.execute(
            """
            INSERT INTO projects (id, topic, status, created_at, updated_at, data)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                topic=excluded.topic, status=excluded.status,
                updated_at=excluded.updated_at, data=excluded.data
            """,
            (
                project.id,
                project.topic,
                project.status.value,
                project.created_at,
                project.updated_at,
                json.dumps(project.to_dict()),
            ),
        )
        self._conn.commit()

    def get(self, project_id: str) -> Optional[VideoProject]:
        row = self._conn.execute(
            "SELECT data FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            return None
        return VideoProject.from_dict(json.loads(row["data"]))

    def list(self, status: Optional[ProjectStatus] = None) -> list[VideoProject]:
        if status:
            rows = self._conn.execute(
                "SELECT data FROM projects WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM projects ORDER BY created_at DESC"
            ).fetchall()
        return [VideoProject.from_dict(json.loads(r["data"])) for r in rows]

    def due_scheduled(self, now: float) -> list[VideoProject]:
        """Projects scheduled to publish at or before ``now``."""
        out = []
        for p in self.list(ProjectStatus.SCHEDULED):
            if p.publish.scheduled_at and p.publish.scheduled_at <= now:
                out.append(p)
        return out

    def close(self) -> None:
        self._conn.close()
