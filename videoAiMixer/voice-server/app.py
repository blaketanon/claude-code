"""
Self-hosted voice server for Video AI Mixer.

  GET    /health                     model/device status
  POST   /v1/audio/transcriptions    OpenAI-compatible speech-to-text (faster-whisper)
  POST   /voices                     register a voice from a sample (multipart: file, name) -> {voice_id}
  GET    /voices                     list registered voices
  DELETE /voices/{voice_id}          delete a voice and its reference clip
  POST   /tts                        {voice_id, text, format: mp3|wav} -> audio in that voice (Chatterbox)

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import engines

DATA_DIR = Path(os.getenv("VOICE_DATA_DIR", "./voice-data")).resolve()
VOICES_DIR = DATA_DIR / "voices"
API_KEY = os.getenv("VOICE_SERVER_API_KEY", "")
REF_SECONDS = int(os.getenv("REF_SECONDS", "15"))  # Chatterbox works best with ~10-20s of clean speech
MAX_CHUNK_CHARS = 280

VOICES_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Video AI Mixer voice server")


def check_key(authorization: str | None = Header(default=None)):
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(401, "Invalid or missing API key")


def voice_dir(voice_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{16}", voice_id):
        raise HTTPException(404, "Unknown voice")
    return VOICES_DIR / voice_id


def ffmpeg(*args: str) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise HTTPException(400, f"ffmpeg failed: {proc.stderr[-500:]}")


def save_upload(upload: UploadFile, tmpdir: str) -> str:
    suffix = Path(upload.filename or "").suffix[:8] or ".bin"
    path = os.path.join(tmpdir, f"upload{suffix}")
    with open(path, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return path


def split_sentences(text: str) -> list[str]:
    """Chatterbox degrades on long inputs, so speak sentence-sized chunks."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks, cur = [], ""
    for s in sentences:
        if len(s) > MAX_CHUNK_CHARS and cur:
            chunks.append(cur)
            cur = ""
        while len(s) > MAX_CHUNK_CHARS:  # very long sentence: break on a comma/space
            cut = max(s.rfind(",", 0, MAX_CHUNK_CHARS), s.rfind(" ", 0, MAX_CHUNK_CHARS))
            cut = cut if cut > 0 else MAX_CHUNK_CHARS
            chunks.append(s[: cut + 1].strip())
            s = s[cut + 1 :].strip()
        if cur and len(cur) + len(s) + 1 > MAX_CHUNK_CHARS:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


@app.get("/health")
def health():
    return {"ok": True, **engines.status()}


@app.post("/v1/audio/transcriptions", dependencies=[Depends(check_key)])
def transcriptions(
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),  # accepted for OpenAI compatibility; STT_MODEL decides
    language: str | None = Form(None),
    response_format: str = Form("json"),
):
    with tempfile.TemporaryDirectory() as tmp:
        src = save_upload(file, tmp)
        wav = os.path.join(tmp, "audio.wav")
        ffmpeg("-i", src, "-vn", "-ac", "1", "-ar", "16000", wav)
        text = engines.transcribe(wav, language=language or None)
    if response_format == "text":
        return PlainTextResponse(text)
    return {"text": text}


@app.post("/voices", dependencies=[Depends(check_key)])
def create_voice(file: UploadFile = File(...), name: str = Form("")):
    voice_id = secrets.token_hex(8)
    vdir = VOICES_DIR / voice_id
    vdir.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = save_upload(file, tmp)
            # Drop long pauses and keep the first REF_SECONDS of actual speech as the reference.
            ffmpeg(
                "-i", src, "-vn", "-ac", "1", "-ar", "24000",
                "-af", "highpass=f=80,silenceremove=start_periods=1:start_threshold=-40dB:"
                       "stop_periods=-1:stop_duration=0.6:stop_threshold=-40dB,loudnorm",
                "-t", str(REF_SECONDS), str(vdir / "reference.wav"),
            )
        if (vdir / "reference.wav").stat().st_size < 24000 * 2 * 3:  # < ~3s of 16-bit audio
            raise HTTPException(400, "Not enough speech in the sample to clone a voice (need 3s+)")
        (vdir / "meta.json").write_text(json.dumps({"name": name[:200], "created_at": time.time()}))
    except BaseException:
        shutil.rmtree(vdir, ignore_errors=True)
        raise
    return {"voice_id": voice_id}


@app.get("/voices", dependencies=[Depends(check_key)])
def list_voices():
    out = []
    for vdir in sorted(VOICES_DIR.iterdir()):
        meta = vdir / "meta.json"
        if meta.exists():
            out.append({"voice_id": vdir.name, **json.loads(meta.read_text())})
    return out


@app.delete("/voices/{voice_id}", dependencies=[Depends(check_key)])
def delete_voice(voice_id: str):
    vdir = voice_dir(voice_id)
    if not vdir.exists():
        raise HTTPException(404, "Unknown voice")
    shutil.rmtree(vdir)
    return {"ok": True}


class TTSRequest(BaseModel):
    voice_id: str
    text: str = Field(min_length=1, max_length=2000)
    format: str = Field("mp3", pattern="^(mp3|wav)$")


@app.post("/tts", dependencies=[Depends(check_key)])
def tts(req: TTSRequest):
    reference = voice_dir(req.voice_id) / "reference.wav"
    if not reference.exists():
        raise HTTPException(404, "Unknown voice")
    chunks = split_sentences(req.text)
    if not chunks:
        raise HTTPException(400, "Nothing to say")

    tmp = tempfile.mkdtemp()
    try:
        wav = os.path.join(tmp, "out.wav")
        engines.synthesize(chunks, str(reference), wav)
        out = wav
        if req.format == "mp3":
            out = os.path.join(tmp, "out.mp3")
            ffmpeg("-i", wav, "-codec:a", "libmp3lame", "-b:a", "128k", out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return FileResponse(
        out,
        media_type="audio/mpeg" if req.format == "mp3" else "audio/wav",
        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
    )


@app.exception_handler(Exception)
async def unhandled(_request, exc: Exception):
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


if os.getenv("PRELOAD") == "1":
    engines._load_stt()
    engines._load_tts()
