"""Provider registry: maps config names to concrete implementations.

Adding a new backend is a one-line registration here plus the implementing
class. The pipeline asks the registry for instances based on ``ChannelConfig``.
"""

from __future__ import annotations

from ..config import ChannelConfig, ProviderConfig
from . import image as image_mod
from . import llm as llm_mod
from . import publisher as pub_mod
from . import tts as tts_mod
from . import video as video_mod
from .base import (
    ImageProvider,
    LLMProvider,
    PublisherProvider,
    TTSProvider,
    VideoProvider,
)

_LLM = {"mock": llm_mod.MockLLM, "anthropic": llm_mod.AnthropicLLM}
_TTS = {"mock": tts_mod.MockTTS, "pyttsx3": tts_mod.PyttsxTTS}
_IMAGE = {"mock": image_mod.MockImage}
_VIDEO = {"mock": video_mod.MockVideo, "ffmpeg": video_mod.FFmpegVideo}
_PUBLISHER = {"mock": pub_mod.MockPublisher, "youtube": pub_mod.YouTubePublisher}


def _build(table: dict, pc: ProviderConfig, slot: str):
    if pc.provider not in table:
        raise ValueError(
            f"Unknown {slot} provider '{pc.provider}'. "
            f"Available: {', '.join(sorted(table))}."
        )
    return table[pc.provider](**pc.options)


def get_llm(cfg: ChannelConfig) -> LLMProvider:
    return _build(_LLM, cfg.llm, "llm")


def get_tts(cfg: ChannelConfig) -> TTSProvider:
    return _build(_TTS, cfg.tts, "tts")


def get_image(cfg: ChannelConfig) -> ImageProvider:
    return _build(_IMAGE, cfg.image, "image")


def get_video(cfg: ChannelConfig) -> VideoProvider:
    return _build(_VIDEO, cfg.video, "video")


def get_publisher(cfg: ChannelConfig) -> PublisherProvider:
    return _build(_PUBLISHER, cfg.publisher, "publisher")
