import wave
from pathlib import Path

from ycm.config import ChannelConfig, ProviderConfig
from ycm.providers import get_llm, get_video
from ycm.providers.image import MockImage
from ycm.providers.tts import MockTTS
from ycm.providers.registry import get_publisher
import pytest


def test_mock_tts_writes_valid_wav_of_right_length(tmp_path):
    tts = MockTTS(sample_rate=8000)
    out = tmp_path / "a.wav"
    dur = tts.synthesize(text="hello", out_path=str(out), target_seconds=3.0)
    assert abs(dur - 3.0) < 0.05
    with wave.open(str(out)) as w:
        assert w.getframerate() == 8000
        assert w.getnframes() == int(3.0 * 8000)


def test_mock_image_writes_svg(tmp_path):
    img = MockImage()
    path = img.generate(prompt="a calm pond", out_path=str(tmp_path / "s0"), index=0)
    assert path.endswith(".svg")
    assert "<svg" in Path(path).read_text()
    assert "scene 1" in Path(path).read_text()


def test_unknown_provider_raises():
    cfg = ChannelConfig(video=ProviderConfig("does-not-exist"))
    with pytest.raises(ValueError):
        get_video(cfg)


def test_mock_llm_is_deterministic():
    cfg = ChannelConfig()
    a = get_llm(cfg).write_script(
        topic="The Quiet Pond", audience="kids", language="en",
        scene_count=5, style_notes="",
    )
    b = get_llm(cfg).write_script(
        topic="The Quiet Pond", audience="kids", language="en",
        scene_count=5, style_notes="",
    )
    assert a.to_dict() == b.to_dict()
    assert len(a.scenes) >= 3


def _have(binary):
    import shutil
    return shutil.which(binary) is not None


@pytest.mark.skipif(not _have("espeak-ng") and not _have("espeak"),
                    reason="espeak-ng not installed")
def test_espeak_produces_real_speech(tmp_path):
    import audioop
    import wave
    from ycm.providers.tts import EspeakTTS

    out = tmp_path / "speak.wav"
    EspeakTTS(words_per_minute=120).synthesize(
        text="Hello little friends, let's slow down together.",
        out_path=str(out), target_seconds=3.0,
    )
    with wave.open(str(out)) as w:
        data = w.readframes(w.getnframes())
        # Real speech has substantial, varying amplitude (mock tone is near-silent).
        assert audioop.rms(data, w.getsampwidth()) > 500


@pytest.mark.skipif(not (_have("ffmpeg") and _have("rsvg-convert")),
                    reason="ffmpeg + rsvg-convert not installed")
def test_ffmpeg_renders_playable_mp4(tmp_path):
    import json
    import subprocess

    from ycm.config import ChannelConfig, ProviderConfig
    from ycm.pipeline import Pipeline

    cfg = ChannelConfig(
        workspace_dir=str(tmp_path / "ws"),
        style={"min_scene_seconds": 4, "max_scene_seconds": 6, "target_minutes": 1},
        llm=ProviderConfig("mock"), tts=ProviderConfig("mock", {"sample_rate": 8000}),
        image=ProviderConfig("mock"), video=ProviderConfig("ffmpeg"),
        publisher=ProviderConfig("mock", {"ledger_path": str(tmp_path / "l.json")}),
    )
    project = Pipeline(cfg).run_all("The Quiet Pond", publish=False)
    assert project.rendered_path.endswith(".mp4")
    assert Path(project.rendered_path).exists()

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type", "-of", "json", project.rendered_path],
        capture_output=True, text=True,
    )
    info = json.loads(probe.stdout)
    kinds = {s["codec_type"] for s in info["streams"]}
    assert {"video", "audio"} <= kinds
    assert float(info["format"]["duration"]) > 5  # real, non-trivial runtime


def test_real_providers_fail_clearly_without_deps():
    """Selecting a real backend without its extra gives a helpful error."""
    from ycm.providers.llm import AnthropicLLM

    llm = AnthropicLLM(api_key_env="DEFINITELY_UNSET_KEY_XYZ")
    with pytest.raises(RuntimeError):
        # Either the SDK import or the missing key raises — both are RuntimeError.
        llm.brainstorm(niche="x", audience="y", count=1)
