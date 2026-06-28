"""In-memory job manager for background production runs.

The web UI starts a production run as a background thread and polls a job's
status to show live, per-stage progress. Each job runs the pipeline with its
*own* :class:`ProjectStore` (SQLite connections are per-thread), so the request
handlers and the worker never share a connection.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from ..models import ProjectStatus, VideoProject
from ..pipeline import Pipeline


@dataclass
class JobState:
    id: str
    topic: str
    status: str = "running"  # running | done | error
    project_id: str | None = None
    error: str | None = None
    events: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "topic": self.topic,
            "status": self.status,
            "project_id": self.project_id,
            "error": self.error,
            "events": list(self.events),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self, pipeline_factory: Callable[[Callable], Pipeline]) -> None:
        # pipeline_factory(on_status) -> Pipeline, called inside the worker
        # thread so the pipeline's store lives on that thread.
        self._pipeline_factory = pipeline_factory
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def start(self, topic: str, *, publish: bool = False,
              privacy: str = "private", scheduled_at: float | None = None) -> str:
        job_id = uuid.uuid4().hex[:12]
        state = JobState(id=job_id, topic=topic)
        with self._lock:
            self._jobs[job_id] = state
        threading.Thread(
            target=self._run, args=(state, topic, publish, privacy, scheduled_at),
            daemon=True,
        ).start()
        return job_id

    def _run(self, state: JobState, topic: str, publish: bool,
             privacy: str, scheduled_at: float | None) -> None:
        def on_status(project: VideoProject, status: ProjectStatus) -> None:
            with self._lock:
                state.project_id = project.id
                state.events.append({"stage": status.value, "at": time.time()})

        pipe = self._pipeline_factory(on_status)
        try:
            project = pipe.create(topic)
            with self._lock:
                state.project_id = project.id
                state.events.append({"stage": "created", "at": time.time()})
            pipe.produce(project)
            if publish or scheduled_at:
                pipe.stage_publish(project, privacy=privacy, scheduled_at=scheduled_at)
            with self._lock:
                state.status = "done"
                state.finished_at = time.time()
        except Exception as exc:  # noqa: BLE001 - surface to the UI
            with self._lock:
                state.status = "error"
                state.error = f"{type(exc).__name__}: {exc}"
                state.finished_at = time.time()
        finally:
            try:
                pipe.store.close()
            except Exception:  # noqa: BLE001
                pass

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None
