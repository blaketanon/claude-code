"""Pacing & style engine.

This is the heart of the channel's identity: producing children's videos that
feel like the calm, slow-paced programming of the 90s and early 2000s. Instead
of the rapid jump-cuts and constant stimulation of modern kids' content, scenes
are *held* for a long time and transitions are gentle and slow. The goal stated
by the channel owner is to help young viewers build, rather than erode, their
attention spans.

The :class:`PacingStyle` turns those intentions into concrete numbers and then
applies them to a :class:`~ycm.models.Script`: it sets each scene's on-screen
duration from its narration (read at a deliberately slow words-per-minute),
clamps every scene to a generous minimum hold time, and inserts slow crossfade
transitions between scenes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Script, TransitionSpec


@dataclass
class PacingStyle:
    """Tunable knobs for the slow, calm house style.

    All defaults are chosen to feel unhurried. They can be overridden per
    channel via configuration.
    """

    # Narration is read slowly. Typical energetic YouTube narration is
    # ~150-170 wpm; calm children's narration sits much lower.
    words_per_minute: float = 110.0

    # Every scene is held on screen for at least this long, even if the
    # narration is short. This is what creates the "lingering" 90s feel.
    min_scene_seconds: float = 8.0
    max_scene_seconds: float = 22.0

    # A quiet beat of held image after the narration finishes, before the
    # transition begins. Lets the moment breathe.
    tail_pause_seconds: float = 1.5

    # Slow, gentle transitions instead of hard cuts.
    transition_kind: str = "crossfade"
    transition_seconds: float = 1.5

    # Soft guidance for the overall video length (minutes). Used for planning
    # how many scenes to request, not as a hard cap.
    target_minutes: float = 6.0

    def narration_seconds(self, text: str) -> float:
        """How long ``text`` takes to read aloud at the configured slow pace."""
        words = max(1, len(text.split()))
        return (words / self.words_per_minute) * 60.0

    def scene_duration(self, narration: str) -> float:
        """On-screen hold time for a scene: narration + tail pause, clamped."""
        raw = self.narration_seconds(narration) + self.tail_pause_seconds
        clamped = max(self.min_scene_seconds, min(self.max_scene_seconds, raw))
        return round(clamped, 2)

    def transition(self, scene_index: int) -> TransitionSpec:
        """Transition leading *into* the given scene index.

        The very first scene has no incoming transition (it simply fades up
        from black via the renderer), so we give it a zero-duration hold.
        """
        if scene_index == 0:
            return TransitionSpec(kind="hold", duration_seconds=0.0)
        return TransitionSpec(
            kind=self.transition_kind,
            duration_seconds=self.transition_seconds,
        )

    def target_scene_count(self) -> int:
        """Roughly how many scenes fill ``target_minutes`` at this pace."""
        avg_scene = (self.min_scene_seconds + self.max_scene_seconds) / 2.0
        total_seconds = self.target_minutes * 60.0
        return max(3, round(total_seconds / (avg_scene + self.transition_seconds)))

    def apply(self, script: Script) -> Script:
        """Stamp pacing onto every scene of ``script`` in place and return it.

        This is idempotent: re-running it recomputes durations and transitions
        from the (possibly edited) narration text.
        """
        for scene in script.scenes:
            scene.duration_seconds = self.scene_duration(scene.narration)
            scene.transition = self.transition(scene.index)
        return script

    @classmethod
    def from_config(cls, cfg: dict) -> "PacingStyle":
        """Build from a config mapping, ignoring unknown keys."""
        known = {
            "words_per_minute",
            "min_scene_seconds",
            "max_scene_seconds",
            "tail_pause_seconds",
            "transition_kind",
            "transition_seconds",
            "target_minutes",
        }
        return cls(**{k: v for k, v in (cfg or {}).items() if k in known})
