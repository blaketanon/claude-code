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


def test_real_providers_fail_clearly_without_deps():
    """Selecting a real backend without its extra gives a helpful error."""
    from ycm.providers.llm import AnthropicLLM

    llm = AnthropicLLM(api_key_env="DEFINITELY_UNSET_KEY_XYZ")
    with pytest.raises(RuntimeError):
        # Either the SDK import or the missing key raises — both are RuntimeError.
        llm.brainstorm(niche="x", audience="y", count=1)
