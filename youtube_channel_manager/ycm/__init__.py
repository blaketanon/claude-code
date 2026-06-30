"""YouTube Channel Manager — automated production of calm, slow-paced kids' videos.

A provider-agnostic pipeline that gathers scripts, narrates them, builds
visuals, assembles a video, and manages publishing/scheduling — all with an
unhurried 90s/2000s house style designed to be gentle on young attention spans.
"""

__version__ = "0.1.0"

from .config import ChannelConfig
from .models import ProjectStatus, Script, VideoProject
from .pipeline import Pipeline
from .style import PacingStyle

__all__ = [
    "ChannelConfig",
    "Pipeline",
    "PacingStyle",
    "ProjectStatus",
    "Script",
    "VideoProject",
    "__version__",
]
