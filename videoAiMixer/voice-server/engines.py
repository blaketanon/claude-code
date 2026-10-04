"""Model wrappers. Imported lazily so the API starts fast and models load on first use."""
import os
import threading

DEVICE_ENV = os.getenv("DEVICE", "auto")
STT_MODEL = os.getenv("STT_MODEL", "small")  # tiny | base | small | medium | large-v3 | distil-large-v3
TTS_EXAGGERATION = float(os.getenv("TTS_EXAGGERATION", "0.5"))
TTS_CFG_WEIGHT = float(os.getenv("TTS_CFG_WEIGHT", "0.5"))

_stt = None
_tts = None
_stt_lock = threading.Lock()
_tts_lock = threading.Lock()


def device() -> str:
    if DEVICE_ENV != "auto":
        return DEVICE_ENV
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _load_stt():
    global _stt
    if _stt is None:
        from faster_whisper import WhisperModel

        dev = "cuda" if device() == "cuda" else "cpu"  # ctranslate2 has no MPS backend
        _stt = WhisperModel(STT_MODEL, device=dev, compute_type="float16" if dev == "cuda" else "int8")
    return _stt


def _load_tts():
    global _tts
    if _tts is None:
        from chatterbox.tts import ChatterboxTTS

        _tts = ChatterboxTTS.from_pretrained(device=device())
    return _tts


def status() -> dict:
    return {"device": device(), "stt_model": STT_MODEL, "stt_loaded": _stt is not None, "tts_loaded": _tts is not None}


def transcribe(path: str, language: str | None = None) -> str:
    with _stt_lock:
        model = _load_stt()
        segments, _info = model.transcribe(path, language=language, beam_size=5, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


def synthesize(chunks: list[str], reference_wav: str, out_wav: str) -> None:
    """Speak each chunk in the reference voice and write one WAV with short pauses between chunks."""
    import torch
    import torchaudio

    with _tts_lock:
        model = _load_tts()
        pieces = []
        gap = torch.zeros(1, int(model.sr * 0.15))
        for i, chunk in enumerate(chunks):
            wav = model.generate(
                chunk,
                audio_prompt_path=reference_wav,
                exaggeration=TTS_EXAGGERATION,
                cfg_weight=TTS_CFG_WEIGHT,
            )
            pieces.append(wav.cpu())
            if i < len(chunks) - 1:
                pieces.append(gap)
        torchaudio.save(out_wav, torch.cat(pieces, dim=1), model.sr)
