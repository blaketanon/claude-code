"""The production pipeline.

Orchestrates a :class:`~ycm.models.VideoProject` through its stages:

    script  ->  narration  ->  visuals  ->  assembly  ->  publish

Each stage is idempotent and the project is persisted after every step, so a
run can be resumed from wherever it left off (e.g. after adding API keys, or
re-rendering only the final video). Stages are skipped when the project has
already reached or passed their output status.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import ChannelConfig
from .models import ProjectStatus, VideoProject, status_reached
from .providers import get_image, get_llm, get_publisher, get_tts, get_video
from .storage import ProjectStore
from .style import PacingStyle

log = logging.getLogger("ycm.pipeline")


class Pipeline:
    def __init__(self, cfg: ChannelConfig, store: ProjectStore | None = None) -> None:
        self.cfg = cfg
        self.style = PacingStyle.from_config(cfg.style)
        self.workspace = cfg.workspace
        self.store = store or ProjectStore(self.workspace / "ycm.db")

    # ----------------------------------------------------------------- assets
    def _project_dir(self, project: VideoProject) -> Path:
        d = self.workspace / "projects" / project.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _persist(self, project: VideoProject, status: ProjectStatus) -> None:
        project.status = status
        self.store.save(project)
        log.info("project %s -> %s", project.id, status.value)

    # ------------------------------------------------------------------ stages
    def stage_script(self, project: VideoProject) -> None:
        if status_reached(project.status, ProjectStatus.SCRIPTED) and project.script:
            log.info("script: already done for %s", project.id)
            return
        llm = get_llm(self.cfg)
        style_notes = (
            f"Read slowly (~{self.style.words_per_minute:.0f} words/min). "
            f"Hold each scene {self.style.min_scene_seconds:.0f}-"
            f"{self.style.max_scene_seconds:.0f}s. Calm 90s/2000s feel, no rapid "
            "cuts. One simple idea per scene. Soothing, reassuring tone."
        )
        script = llm.write_script(
            topic=project.topic,
            audience=self.cfg.target_audience,
            language=self.cfg.language,
            scene_count=self.style.target_scene_count(),
            style_notes=style_notes,
        )
        self.style.apply(script)  # pacing engine sets durations & transitions
        project.script = script
        (self._project_dir(project) / "script.txt").write_text(
            self._render_script_text(script), encoding="utf-8"
        )
        self._persist(project, ProjectStatus.SCRIPTED)

    def stage_narration(self, project: VideoProject) -> None:
        if status_reached(project.status, ProjectStatus.NARRATED):
            return
        assert project.script, "script stage must run first"
        tts = get_tts(self.cfg)
        audio_dir = self._project_dir(project) / "audio"
        for scene in project.script.scenes:
            out = audio_dir / f"scene_{scene.index:03d}.wav"
            dur = tts.synthesize(
                text=scene.narration,
                out_path=str(out),
                target_seconds=scene.duration_seconds,
            )
            scene.audio_path = str(out)
            scene.audio_duration_seconds = dur
        self._persist(project, ProjectStatus.NARRATED)

    def stage_visuals(self, project: VideoProject) -> None:
        if status_reached(project.status, ProjectStatus.VISUALS_READY):
            return
        assert project.script, "script stage must run first"
        image = get_image(self.cfg)
        vis_dir = self._project_dir(project) / "visuals"
        for scene in project.script.scenes:
            out = vis_dir / f"scene_{scene.index:03d}"
            scene.visual_path = image.generate(
                prompt=scene.visual_prompt, out_path=str(out), index=scene.index
            )
        self._persist(project, ProjectStatus.VISUALS_READY)

    def stage_assembly(self, project: VideoProject) -> None:
        if status_reached(project.status, ProjectStatus.ASSEMBLED) and project.rendered_path:
            return
        video = get_video(self.cfg)
        out = self._project_dir(project) / "render" / "video"
        project.rendered_path = video.assemble(project=project, out_path=str(out))
        self._persist(project, ProjectStatus.ASSEMBLED)

    def stage_publish(self, project: VideoProject, *, privacy: str = "private",
                      scheduled_at: float | None = None) -> None:
        publisher = get_publisher(self.cfg)
        info = publisher.publish(
            project=project, privacy=privacy, scheduled_at=scheduled_at
        )
        project.publish = info
        status = (
            ProjectStatus.SCHEDULED if info.status == "scheduled"
            else ProjectStatus.PUBLISHED
        )
        self._persist(project, status)

    # -------------------------------------------------------------- public API
    def create(self, topic: str) -> VideoProject:
        project = VideoProject(topic=topic)
        self.store.save(project)
        return project

    def produce(self, project: VideoProject) -> VideoProject:
        """Run everything up to (but not including) publishing."""
        try:
            self.stage_script(project)
            self.stage_narration(project)
            self.stage_visuals(project)
            self.stage_assembly(project)
        except Exception as exc:  # noqa: BLE001 - record & re-raise for the CLI
            project.error = f"{type(exc).__name__}: {exc}"
            self._persist(project, ProjectStatus.FAILED)
            raise
        return project

    def run_all(self, topic: str, *, publish: bool = False, privacy: str = "private",
                scheduled_at: float | None = None) -> VideoProject:
        project = self.create(topic)
        self.produce(project)
        if publish or scheduled_at:
            self.stage_publish(project, privacy=privacy, scheduled_at=scheduled_at)
        return project

    def process_due(self, now: float) -> list[VideoProject]:
        """Publish any scheduled projects whose time has come."""
        published = []
        for project in self.store.due_scheduled(now):
            self.stage_publish(project, privacy=project.publish.privacy)
            published.append(project)
        return published

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _render_script_text(script) -> str:
        lines = [script.title, "=" * len(script.title), "",
                 f"Description: {script.description}", "",
                 f"Hook: {script.hook}", ""]
        for s in script.scenes:
            lines += [
                f"--- Scene {s.index + 1}  ({s.duration_seconds:.1f}s, "
                f"transition: {s.transition.kind} {s.transition.duration_seconds:.1f}s)",
                f"VISUAL: {s.visual_prompt}",
                f"NARRATION: {s.narration}",
                "",
            ]
        lines += [f"Outro: {script.outro}", "",
                  f"Total runtime: {script.total_duration_seconds:.1f}s "
                  f"({script.total_duration_seconds/60:.1f} min), "
                  f"{script.word_count} words."]
        return "\n".join(lines)
