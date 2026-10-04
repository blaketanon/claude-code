"""Voice server API tests. The model engines are replaced with fakes so this runs without a GPU or weights.

    pip install fastapi uvicorn python-multipart httpx pytest && pytest -q
"""
import os
import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path / "data"))
    import importlib

    import engines

    calls = []

    def fake_transcribe(path, language=None):
        calls.append(("stt", language))
        return "hello from the fake"

    def fake_synthesize(chunks, reference_wav, out_wav):
        calls.append(("tts", list(chunks)))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", "-ar", "24000", out_wav],
            check=True,
        )

    monkeypatch.setattr(engines, "transcribe", fake_transcribe)
    monkeypatch.setattr(engines, "synthesize", fake_synthesize)
    import app as app_module

    app_module = importlib.reload(app_module)  # pick up VOICE_DATA_DIR
    c = TestClient(app_module.app)
    c.calls = calls
    c.module = app_module
    return c


def make_audio(tmp_path, name, source):
    path = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", source, str(path)], check=True)
    return path


def test_split_sentences_keeps_order_and_caps_length(client):
    split = client.module.split_sentences
    long = "This is a long sentence, with commas, " * 12
    text = f"Hello there. {long} Bye! Short one."
    chunks = split(text)
    assert chunks[0] == "Hello there."
    assert chunks[-1].endswith("Short one.")
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")
    assert all(len(c) <= client.module.MAX_CHUNK_CHARS + 1 for c in chunks)
    assert split("   ") == []


def test_transcription_is_openai_compatible(client, tmp_path):
    audio = make_audio(tmp_path, "a.mp3", "sine=frequency=200:duration=2")
    r = client.post("/v1/audio/transcriptions", files={"file": ("a.mp3", audio.read_bytes())}, data={"model": "whisper-1"})
    assert r.status_code == 200
    assert r.json() == {"text": "hello from the fake"}
    r = client.post(
        "/v1/audio/transcriptions", files={"file": ("a.mp3", audio.read_bytes())}, data={"response_format": "text"}
    )
    assert r.text == "hello from the fake"


def test_voice_lifecycle_and_tts(client, tmp_path):
    sample = make_audio(tmp_path, "s.mp3", "sine=frequency=200:duration=20")
    r = client.post("/voices", files={"file": ("s.mp3", sample.read_bytes())}, data={"name": "Ada"})
    assert r.status_code == 200
    voice_id = r.json()["voice_id"]
    assert [v["voice_id"] for v in client.get("/voices").json()] == [voice_id]

    r = client.post("/tts", json={"voice_id": voice_id, "text": "Hi there. How are you?"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert len(r.content) > 1000
    assert ("tts", ["Hi there. How are you?"]) in client.calls

    assert client.delete(f"/voices/{voice_id}").status_code == 200
    assert client.get("/voices").json() == []
    assert client.post("/tts", json={"voice_id": voice_id, "text": "x"}).status_code == 404


def test_rejects_silence_and_bad_ids(client, tmp_path):
    quiet = make_audio(tmp_path, "q.mp3", "anullsrc=duration=10")
    r = client.post("/voices", files={"file": ("q.mp3", quiet.read_bytes())})
    assert r.status_code == 400
    assert client.get("/voices").json() == []  # nothing half-created
    assert client.post("/tts", json={"voice_id": "../../etc", "text": "x"}).status_code == 404
    assert client.delete("/voices/not-a-voice").status_code == 404


def test_api_key(client, monkeypatch):
    monkeypatch.setattr(client.module, "API_KEY", "k")
    assert client.get("/voices").status_code == 401
    assert client.get("/voices", headers={"authorization": "Bearer k"}).status_code == 200
    assert client.get("/health").status_code == 200  # health stays open for probes
