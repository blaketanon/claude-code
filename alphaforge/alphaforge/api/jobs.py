"""Thread-pool job runner with DB-backed status so the UI can poll progress."""
from __future__ import annotations

import logging
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select

from alphaforge.db import session_scope
from alphaforge.db.models import Job

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="af-job")
        self._cancel: dict[str, threading.Event] = {}

    def submit(self, kind: str, params: dict, fn: Callable[["JobContext"], dict]) -> str:
        job_id = uuid.uuid4().hex[:12]
        with session_scope() as s:
            s.add(Job(job_id=job_id, kind=kind, params=params, status="queued"))
        self._cancel[job_id] = threading.Event()
        self._pool.submit(self._run, job_id, fn)
        return job_id

    def cancel(self, job_id: str) -> bool:
        ev = self._cancel.get(job_id)
        if ev is None:
            return False
        ev.set()
        return True

    def _run(self, job_id: str, fn):
        ctx = JobContext(job_id, self._cancel[job_id])
        self._update(job_id, status="running", started_at=datetime.now(timezone.utc))
        try:
            result = fn(ctx) or {}
            self._update(job_id, status="succeeded", result=result, progress=1.0, finished_at=datetime.now(timezone.utc))
        except JobCancelled:
            self._update(job_id, status="cancelled", finished_at=datetime.now(timezone.utc))
        except Exception as e:
            log.exception("job %s failed", job_id)
            self._update(job_id, status="failed", error=f"{e}\n{traceback.format_exc()[-2000:]}", finished_at=datetime.now(timezone.utc))
        finally:
            self._cancel.pop(job_id, None)

    @staticmethod
    def _update(job_id: str, **fields):
        with session_scope() as s:
            j = s.scalar(select(Job).where(Job.job_id == job_id))
            if j:
                for k, v in fields.items():
                    setattr(j, k, v)


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, job_id: str, cancel: threading.Event):
        self.job_id, self._cancel = job_id, cancel

    def progress(self, p: float, msg: str = ""):
        if self._cancel.is_set():
            raise JobCancelled()
        with session_scope() as s:
            j = s.scalar(select(Job).where(Job.job_id == self.job_id))
            if j:
                j.progress = float(p)
                if msg:
                    j.log = (j.log + f"\n[{datetime.now(timezone.utc):%H:%M:%S}] {msg}")[-20000:]
