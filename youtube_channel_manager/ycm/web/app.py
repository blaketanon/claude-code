"""FastAPI web dashboard for the YouTube Channel Manager.

Exposes a small REST API over the same pipeline + store the CLI uses, plus a
single-page dashboard (``static/index.html``) to drive it: brainstorm topics,
start a production run and watch live per-stage progress, preview the generated
script / scene timing / visuals, play the narration, and publish or schedule.

Requires the optional ``web`` extra::

    pip install 'youtube-channel-manager[web]'
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..config import ChannelConfig
from ..models import ProjectStatus
from ..pipeline import Pipeline
from ..providers import get_llm
from ..storage import ProjectStore
from ..style import PacingStyle
from .jobs import JobManager

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError as e:  # pragma: no cover - optional extra
    raise RuntimeError(
        "The web dashboard requires the 'web' extra. Install with: "
        "pip install 'youtube-channel-manager[web]'"
    ) from e

_STATIC = Path(__file__).parent / "static"


class CreateRequest(BaseModel):
    topic: str
    publish: bool = False
    privacy: str = "private"
    at: str | None = None  # ISO 8601 or relative (+2h, +1d)


class PublishRequest(BaseModel):
    privacy: str = "private"
    at: str | None = None


def _project_payload(project) -> dict:
    """JSON view of a project, with media URLs the frontend can load."""
    data = project.to_dict()
    if project.script:
        for scene in data["script"]["scenes"]:
            idx = scene["index"]
            scene["audio_url"] = (
                f"/api/projects/{project.id}/media/audio/{idx}"
                if scene.get("audio_path") else None
            )
            scene["visual_url"] = (
                f"/api/projects/{project.id}/media/visual/{idx}"
                if scene.get("visual_path") else None
            )
        data["script"]["total_duration_seconds"] = project.script.total_duration_seconds
        data["script"]["word_count"] = project.script.word_count
    return data


def create_app(cfg: ChannelConfig) -> "FastAPI":
    app = FastAPI(title="YouTube Channel Manager", version="0.1.0")
    db_path = cfg.workspace / "ycm.db"
    cfg.workspace.mkdir(parents=True, exist_ok=True)

    def pipeline_factory(on_status: Callable | None = None) -> Pipeline:
        # Fresh store per call so each worker thread owns its SQLite connection.
        return Pipeline(cfg, store=ProjectStore(db_path), on_status=on_status)

    jobs = JobManager(pipeline_factory)

    def _parse_when(value: str | None):
        if not value:
            return None
        from ..cli import _parse_when as parse
        return parse(value)

    def _store() -> ProjectStore:
        return ProjectStore(db_path)

    # ----------------------------------------------------------------- routes
    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (_STATIC / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    @app.get("/api/config")
    def get_config():
        style = PacingStyle.from_config(cfg.style)
        return {
            "channel": {
                "name": cfg.name, "niche": cfg.niche,
                "target_audience": cfg.target_audience, "language": cfg.language,
            },
            "style": style.__dict__,
            "providers": {
                "llm": cfg.llm.provider, "tts": cfg.tts.provider,
                "image": cfg.image.provider, "video": cfg.video.provider,
                "publisher": cfg.publisher.provider,
            },
        }

    @app.get("/api/ideas")
    def ideas(count: int = 8):
        out = get_llm(cfg).brainstorm(
            niche=cfg.niche, audience=cfg.target_audience, count=count
        )
        return [i.to_dict() for i in out]

    @app.get("/api/projects")
    def list_projects(status: str | None = None):
        store = _store()
        try:
            st = ProjectStatus(status) if status else None
            return [
                {
                    "id": p.id, "topic": p.topic, "status": p.status.value,
                    "title": p.script.title if p.script else p.topic,
                    "runtime": p.script.total_duration_seconds if p.script else None,
                    "publish_status": p.publish.status, "url": p.publish.url,
                    "updated_at": p.updated_at,
                }
                for p in store.list(st)
            ]
        finally:
            store.close()

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        store = _store()
        try:
            project = store.get(project_id)
            if not project:
                raise HTTPException(404, "project not found")
            return _project_payload(project)
        finally:
            store.close()

    @app.post("/api/projects")
    def create_project(req: CreateRequest):
        if not req.topic.strip():
            raise HTTPException(400, "topic is required")
        scheduled_at = _parse_when(req.at)
        job_id = jobs.start(
            req.topic.strip(), publish=req.publish or bool(scheduled_at),
            privacy=req.privacy, scheduled_at=scheduled_at,
        )
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.post("/api/projects/{project_id}/publish")
    def publish_project(project_id: str, req: PublishRequest):
        store = _store()
        try:
            project = store.get(project_id)
            if not project:
                raise HTTPException(404, "project not found")
            pipe = Pipeline(cfg, store=store)
            pipe.stage_publish(
                project, privacy=req.privacy, scheduled_at=_parse_when(req.at)
            )
            return _project_payload(project)
        finally:
            store.close()

    @app.post("/api/tick")
    def tick():
        import time as _t
        store = _store()
        try:
            pipe = Pipeline(cfg, store=store)
            published = pipe.process_due(_t.time())
            return {"published": [p.id for p in published]}
        finally:
            store.close()

    @app.get("/api/projects/{project_id}/media/{kind}/{index}")
    def media(project_id: str, kind: str, index: int):
        store = _store()
        try:
            project = store.get(project_id)
            if not project or not project.script:
                raise HTTPException(404, "not found")
            scenes = project.script.scenes
            if index < 0 or index >= len(scenes):
                raise HTTPException(404, "scene out of range")
            scene = scenes[index]
            path = scene.audio_path if kind == "audio" else scene.visual_path
            if not path or not Path(path).exists():
                raise HTTPException(404, "media not found")
            media_type = "audio/wav" if kind == "audio" else "image/svg+xml"
            return FileResponse(path, media_type=media_type)
        finally:
            store.close()

    if _STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    return app


def serve(cfg: ChannelConfig, host: str = "127.0.0.1", port: int = 8000) -> None:
    try:
        import uvicorn
    except ImportError as e:  # pragma: no cover - optional extra
        raise RuntimeError(
            "Serving requires 'uvicorn'. Install with: "
            "pip install 'youtube-channel-manager[web]'"
        ) from e
    uvicorn.run(create_app(cfg), host=host, port=port)
