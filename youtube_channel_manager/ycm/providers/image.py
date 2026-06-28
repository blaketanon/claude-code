"""Image/visual providers for scenes.

``MockImage`` writes a real, self-contained SVG per scene — a calm pastel
gradient card carrying the visual prompt — so every scene has a genuine visual
asset on disk without any model or network. Real text-to-image integrations can
be added by implementing :class:`~ycm.providers.base.ImageProvider`.
"""

from __future__ import annotations

import html
import textwrap
from pathlib import Path

from .base import ImageProvider


# A rotating set of soft, low-saturation palettes — nothing harsh or flashing.
_PALETTES = [
    ("#cfe8e0", "#a7ccd6"),
    ("#f3e3c3", "#e6c79c"),
    ("#dbe7c9", "#b9d39b"),
    ("#e5d4ef", "#c5a8de"),
    ("#fde2d4", "#f6c1a8"),
    ("#d6e2f0", "#aac4e2"),
]


class MockImage(ImageProvider):
    """Renders a gentle gradient placeholder card as SVG."""

    def __init__(self, width: int = 1280, height: int = 720, **_: object) -> None:
        self.width = int(width)
        self.height = int(height)

    def generate(self, *, prompt: str, out_path: str, index: int) -> str:
        out = Path(out_path).with_suffix(".svg")
        out.parent.mkdir(parents=True, exist_ok=True)
        top, bottom = _PALETTES[index % len(_PALETTES)]
        wrapped = textwrap.wrap(prompt, width=42)[:6]
        lines = "".join(
            f'<tspan x="50%" dy="{1.4 if i else 0}em">{html.escape(line)}</tspan>'
            for i, line in enumerate(wrapped)
        )
        svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}"
     viewBox="0 0 {self.width} {self.height}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{top}"/>
      <stop offset="100%" stop-color="{bottom}"/>
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#g)"/>
  <circle cx="{self.width*0.82:.0f}" cy="{self.height*0.22:.0f}" r="80"
          fill="#ffffff" fill-opacity="0.35"/>
  <text x="50%" y="46%" text-anchor="middle" font-family="Georgia, serif"
        font-size="34" fill="#3a3a3a" fill-opacity="0.85">{lines}</text>
  <text x="50%" y="92%" text-anchor="middle" font-family="Georgia, serif"
        font-size="20" fill="#3a3a3a" fill-opacity="0.5">scene {index + 1}</text>
</svg>
"""
        out.write_text(svg, encoding="utf-8")
        return str(out)
