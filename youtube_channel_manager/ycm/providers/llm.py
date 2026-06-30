"""LLM providers: script & idea generation.

``MockLLM`` is fully offline and deterministic so the pipeline runs with no API
keys. ``AnthropicLLM`` is a real integration that lazily imports the SDK and
asks Claude to return a structured script as JSON.
"""

from __future__ import annotations

import json
import os
import textwrap

from .base import LLMProvider
from ..models import Idea, Scene, Script


# --------------------------------------------------------------------------- #
# Offline mock
# --------------------------------------------------------------------------- #
class MockLLM(LLMProvider):
    """Deterministic, dependency-free script writer.

    It can't match a real model's creativity, but it produces a coherent,
    calmly-paced children's script structured exactly like the real provider's
    output, which is what the rest of the pipeline needs to do real work.
    """

    def __init__(self, **_: object) -> None:  # accept/ignore config options
        pass

    def brainstorm(self, *, niche: str, audience: str, count: int) -> list[Idea]:
        seeds = [
            ("The Quiet Pond and Its Friends", "observe a calm habitat"),
            ("How Bread Is Made, Step by Slow Step", "a gentle how-it's-made"),
            ("Counting the Stars Before Bedtime", "soothing counting story"),
            ("A Walk Through the Four Seasons", "slow seasonal journey"),
            ("The Little Seed That Took Its Time", "patience & growth"),
            ("Where Does the River Go?", "follow a river calmly"),
            ("Colors of a Sleepy Sunset", "color recognition, calm"),
            ("The Friendly Snail's Long Day", "slowness as a virtue"),
        ]
        ideas: list[Idea] = []
        for i in range(count):
            topic, angle = seeds[i % len(seeds)]
            ideas.append(
                Idea(
                    topic=topic,
                    angle=angle,
                    rationale=(
                        f"Fits '{niche}' for {audience}: a single calm subject "
                        "explored slowly, ideal for long held shots."
                    ),
                )
            )
        return ideas

    def write_script(
        self,
        *,
        topic: str,
        audience: str,
        language: str,
        scene_count: int,
        style_notes: str,
    ) -> Script:
        scenes: list[Scene] = []
        # A simple, calm narrative arc: greeting -> exploration beats -> wind down.
        beats = self._beats(topic, scene_count)
        for i, (narration, visual) in enumerate(beats):
            scenes.append(
                Scene(index=i, narration=narration, visual_prompt=visual,
                      on_screen_text=topic if i == 0 else None)
            )
        title = self._titleize(topic)
        return Script(
            title=title,
            description=(
                f"A slow, gentle video for {audience}: {topic}. "
                "Made with long, calm shots and unhurried narration to give "
                "young viewers time to look, listen, and wonder."
            ),
            tags=["kids", "calm", "educational", "slow tv", "bedtime",
                  *topic.lower().split()[:4]],
            hook=f"Today, let's take our time and explore {topic.lower()}.",
            outro="Thank you for watching slowly with us. See you next time.",
            scenes=scenes,
        )

    @staticmethod
    def _titleize(topic: str) -> str:
        t = topic.strip().rstrip(".")
        return t if t[:1].isupper() else t.capitalize()

    @staticmethod
    def _beats(topic: str, n: int) -> list[tuple[str, str]]:
        subject = topic.lower().rstrip(".")
        templates = [
            (f"Hello, little friends. Let's slow down together and look at {subject}. "
             "Take a deep breath. There is no hurry here.",
             f"Soft warm title card introducing {subject}, gentle pastel colors"),
            (f"First, let's just watch. Notice the shapes and the colors of {subject}. "
             "What do you see? Let's look for a long, quiet moment.",
             f"Wide calm establishing shot of {subject}, soft natural light"),
            (f"Everything about {subject} happens slowly, and that's a good thing. "
             "Slow things give us time to understand them.",
             f"Slow close-up detail of {subject}, shallow focus, peaceful"),
            (f"Let's count what we can find. One... two... three. "
             "We can count as slowly as we like.",
             f"Simple countable objects related to {subject}, clean background"),
            (f"Listen closely. Can you hear how calm it is? "
             "When we are calm, we notice so much more.",
             f"Atmospheric scene of {subject} at golden hour, gentle motion"),
            (f"Now we rest our eyes for a moment and remember what we learned about "
             f"{subject}. Good thinking takes time.",
             f"Soft, dim wind-down scene of {subject}, dusk tones"),
            ("It is almost time to say goodbye. Let's take one more slow look, "
             "and breathe out together.",
             f"Final tranquil wide shot of {subject}, fading light"),
        ]
        out = []
        for i in range(max(3, n)):
            out.append(templates[i % len(templates)])
        return out


# --------------------------------------------------------------------------- #
# Real integration (lazy import)
# --------------------------------------------------------------------------- #
class AnthropicLLM(LLMProvider):
    """Uses Claude to write scripts. Requires the ``anthropic`` extra."""

    def __init__(self, model: str = "claude-opus-4-8", api_key_env: str = "ANTHROPIC_API_KEY",
                 **_: object) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self._client = None

    def _client_or_raise(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic  # type: ignore
        except ImportError as e:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "AnthropicLLM requires the 'anthropic' package. "
                "Install with: pip install 'youtube-channel-manager[anthropic]'"
            ) from e
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"Set {self.api_key_env} (e.g. in .env) to use AnthropicLLM."
            )
        self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def _complete_json(self, prompt: str) -> dict:
        client = self._client_or_raise()
        msg = client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in msg.content if getattr(block, "type", "") == "text"
        )
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            text = text.partition("\n")[2] if "\n" in text else text
            text = text.rstrip("`").strip()
        return json.loads(text)

    def brainstorm(self, *, niche: str, audience: str, count: int) -> list[Idea]:
        prompt = textwrap.dedent(f"""
            Brainstorm {count} ideas for calm, slow-paced children's videos.
            Channel niche: {niche}. Audience: {audience}.
            Each idea must suit long held shots and unhurried narration
            (90s/2000s style, attention-span friendly).
            Return ONLY a JSON array of objects with keys:
            "topic", "angle", "rationale".
        """).strip()
        data = self._complete_json(prompt)
        return [Idea.from_dict(d) for d in data]

    def write_script(
        self,
        *,
        topic: str,
        audience: str,
        language: str,
        scene_count: int,
        style_notes: str,
    ) -> Script:
        prompt = textwrap.dedent(f"""
            Write a calm, slow-paced children's video script in {language}.
            Topic: {topic}
            Audience: {audience}
            Aim for about {scene_count} scenes.

            STYLE (very important):
            {style_notes}

            Return ONLY JSON with this shape:
            {{
              "title": str, "description": str, "tags": [str],
              "hook": str, "outro": str,
              "scenes": [{{"narration": str, "visual_prompt": str}}]
            }}
            Keep narration gentle and simple. One clear idea per scene.
        """).strip()
        data = self._complete_json(prompt)
        scenes = [
            Scene(index=i, narration=s["narration"], visual_prompt=s["visual_prompt"])
            for i, s in enumerate(data.get("scenes", []))
        ]
        return Script(
            title=data.get("title", topic),
            description=data.get("description", ""),
            tags=list(data.get("tags", [])),
            hook=data.get("hook", ""),
            outro=data.get("outro", ""),
            scenes=scenes,
        )
