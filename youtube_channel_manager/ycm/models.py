"""Core data models for the YouTube Channel Manager.

Everything that flows through the pipeline is represented here as a plain
``dataclass`` so it can be serialized to JSON and stored without any external
dependency. Nested structures get explicit ``to_dict`` / ``from_dict`` helpers
to keep round-tripping robust across versions.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class ProjectStatus(str, Enum):
    """Lifecycle of a single video project as it moves through the pipeline."""

    CREATED = "created"
    SCRIPTED = "scripted"
    NARRATED = "narrated"
    VISUALS_READY = "visuals_ready"
    ASSEMBLED = "assembled"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"


# Ordering used to decide whether a stage still needs to run.
_STATUS_ORDER = [
    ProjectStatus.CREATED,
    ProjectStatus.SCRIPTED,
    ProjectStatus.NARRATED,
    ProjectStatus.VISUALS_READY,
    ProjectStatus.ASSEMBLED,
    ProjectStatus.SCHEDULED,
    ProjectStatus.PUBLISHED,
]


def status_reached(current: ProjectStatus, target: ProjectStatus) -> bool:
    """True if ``current`` is at or beyond ``target`` (FAILED never counts)."""
    if current == ProjectStatus.FAILED:
        return False
    return _STATUS_ORDER.index(current) >= _STATUS_ORDER.index(target)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class TransitionSpec:
    """A deliberately slow transition between two scenes.

    The defaults lean into the 90s/2000s aesthetic: gentle, unhurried fades
    rather than rapid hard cuts.
    """

    kind: str = "crossfade"  # crossfade | fade | dissolve | hold
    duration_seconds: float = 1.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TransitionSpec":
        return cls(**d)


@dataclass
class Scene:
    """A single beat of the video: some narration over a single visual."""

    index: int
    narration: str
    visual_prompt: str
    duration_seconds: float = 0.0
    transition: TransitionSpec = field(default_factory=TransitionSpec)
    # Filled in by later stages:
    audio_path: Optional[str] = None
    audio_duration_seconds: Optional[float] = None
    visual_path: Optional[str] = None
    on_screen_text: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["transition"] = self.transition.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Scene":
        d = dict(d)
        d["transition"] = TransitionSpec.from_dict(
            d.get("transition") or {}
        )
        return cls(**d)


@dataclass
class Script:
    """The narrative blueprint for one video."""

    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    hook: str = ""
    outro: str = ""
    scenes: list[Scene] = field(default_factory=list)

    @property
    def total_duration_seconds(self) -> float:
        # Transitions are crossfades that overlap adjacent scenes, so each one
        # *shortens* the total rather than adding to it (matching the renderer).
        body = sum(s.duration_seconds for s in self.scenes)
        overlap = sum(s.transition.duration_seconds for s in self.scenes[1:])
        return round(max(0.0, body - overlap), 2)

    @property
    def word_count(self) -> int:
        return sum(len(s.narration.split()) for s in self.scenes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "hook": self.hook,
            "outro": self.outro,
            "scenes": [s.to_dict() for s in self.scenes],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Script":
        return cls(
            title=d["title"],
            description=d.get("description", ""),
            tags=list(d.get("tags", [])),
            hook=d.get("hook", ""),
            outro=d.get("outro", ""),
            scenes=[Scene.from_dict(s) for s in d.get("scenes", [])],
        )


@dataclass
class PublishInfo:
    """Where/when a finished video went (or is going)."""

    status: str = "draft"  # draft | scheduled | published | failed
    video_id: Optional[str] = None
    url: Optional[str] = None
    scheduled_at: Optional[float] = None  # epoch seconds
    published_at: Optional[float] = None
    privacy: str = "private"  # private | unlisted | public

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PublishInfo":
        return cls(**d)


@dataclass
class VideoProject:
    """The unit of work tracked by the system from idea to published video."""

    topic: str
    id: str = field(default_factory=_new_id)
    status: ProjectStatus = ProjectStatus.CREATED
    script: Optional[Script] = None
    rendered_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    publish: PublishInfo = field(default_factory=PublishInfo)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: Optional[str] = None

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "status": self.status.value,
            "script": self.script.to_dict() if self.script else None,
            "rendered_path": self.rendered_path,
            "thumbnail_path": self.thumbnail_path,
            "publish": self.publish.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VideoProject":
        return cls(
            id=d["id"],
            topic=d["topic"],
            status=ProjectStatus(d.get("status", "created")),
            script=Script.from_dict(d["script"]) if d.get("script") else None,
            rendered_path=d.get("rendered_path"),
            thumbnail_path=d.get("thumbnail_path"),
            publish=PublishInfo.from_dict(d.get("publish") or {}),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            error=d.get("error"),
        )


@dataclass
class Idea:
    """A candidate video topic produced by the ideation step."""

    topic: str
    angle: str = ""
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Idea":
        return cls(**d)
