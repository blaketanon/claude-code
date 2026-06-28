"""Text-to-speech providers.

``MockTTS`` writes a *real* WAV file (valid, playable audio) for each scene
using only the standard library, so the rest of the pipeline gets genuine media
to work with offline. ``PyttsxTTS`` is a real, fully-offline voice via the
optional ``pyttsx3`` engine.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from .base import TTSProvider


class MockTTS(TTSProvider):
    """Generates a quiet placeholder tone of the right duration.

    Not speech, but valid audio: a soft, low sine wave at very low amplitude so
    silent-but-valid WAV files prove the narration timing end to end. Duration
    matches the scene's planned hold time exactly.
    """

    def __init__(self, sample_rate: int = 16000, frequency: float = 174.0,
                 amplitude: float = 0.04, **_: object) -> None:
        self.sample_rate = int(sample_rate)
        self.frequency = float(frequency)
        self.amplitude = float(amplitude)

    def synthesize(self, *, text: str, out_path: str, target_seconds: float) -> float:
        duration = max(0.5, float(target_seconds))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        n_frames = int(duration * self.sample_rate)
        amp = int(self.amplitude * 32767)
        with wave.open(out_path, "w") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            frames = bytearray()
            # Gentle fade in/out so there are no clicks — keeps it calm.
            fade = int(0.1 * self.sample_rate)
            for i in range(n_frames):
                env = 1.0
                if i < fade:
                    env = i / fade
                elif i > n_frames - fade:
                    env = max(0.0, (n_frames - i) / fade)
                sample = int(amp * env * math.sin(2 * math.pi * self.frequency * i / self.sample_rate))
                frames += struct.pack("<h", sample)
            wav.writeframes(bytes(frames))
        return round(n_frames / self.sample_rate, 3)


class PyttsxTTS(TTSProvider):
    """Real offline narration via the optional ``pyttsx3`` engine."""

    def __init__(self, rate: int = 130, voice: str | None = None, **_: object) -> None:
        # A slow rate (~130 wpm) matches the calm house style.
        self.rate = int(rate)
        self.voice = voice

    def synthesize(self, *, text: str, out_path: str, target_seconds: float) -> float:
        try:
            import pyttsx3  # type: ignore
        except ImportError as e:  # pragma: no cover - optional extra
            raise RuntimeError(
                "PyttsxTTS requires 'pyttsx3'. Install with: "
                "pip install 'youtube-channel-manager[tts]'"
            ) from e
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        engine = pyttsx3.init()
        engine.setProperty("rate", self.rate)
        if self.voice:
            engine.setProperty("voice", self.voice)
        engine.save_to_file(text, out_path)
        engine.runAndWait()
        # Best-effort duration read; fall back to the requested target.
        try:
            with wave.open(out_path) as wav:
                return round(wav.getnframes() / wav.getframerate(), 3)
        except Exception:
            return float(target_seconds)
