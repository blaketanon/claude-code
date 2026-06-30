"""Abstract provider interfaces.

Every external capability the system needs is expressed as a small abstract
base class. Concrete implementations live alongside (``mock.py`` for offline
defaults, plus real integrations like ``anthropic.py`` or ``youtube.py``).
This is what makes the whole system provider-agnostic: the pipeline only ever
talks to these interfaces.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import Idea, Script


class LLMProvider(ABC):
    """Generates ideas and full scripts from a topic and channel context."""

    @abstractmethod
    def brainstorm(self, *, niche: str, audience: str, count: int) -> list[Idea]:
        """Return ``count`` candidate video :class:`Idea` objects."""

    @abstractmethod
    def write_script(
        self,
        *,
        topic: str,
        audience: str,
        language: str,
        scene_count: int,
        style_notes: str,
    ) -> Script:
        """Write a full :class:`Script` for ``topic``.

        Implementations should honour ``scene_count`` and ``style_notes`` (which
        carries the slow-pace guidance) but the pacing engine has final say on
        per-scene timing.
        """


class TTSProvider(ABC):
    """Synthesises narration audio for a single piece of text."""

    @abstractmethod
    def synthesize(self, *, text: str, out_path: str, target_seconds: float) -> float:
        """Write narration for ``text`` to ``out_path``.

        ``target_seconds`` is the scene's planned hold time; providers may use
        it to pad/trim. Returns the actual duration of the produced audio.
        """


class ImageProvider(ABC):
    """Produces a still visual for a scene from a text prompt."""

    @abstractmethod
    def generate(self, *, prompt: str, out_path: str, index: int) -> str:
        """Create a visual at (or near) ``out_path``; return the real path."""


class VideoProvider(ABC):
    """Assembles per-scene visuals + audio into one rendered video."""

    @abstractmethod
    def assemble(self, *, project: Any, out_path: str) -> str:
        """Render ``project`` (a VideoProject) to ``out_path``; return real path."""


class PublisherProvider(ABC):
    """Uploads / schedules a finished video to a destination (e.g. YouTube)."""

    @abstractmethod
    def publish(self, *, project: Any, privacy: str, scheduled_at: float | None):
        """Publish or schedule ``project``. Returns a PublishInfo."""
