import wave
from pathlib import Path

import pytest

from ycm.config import ChannelConfig, ProviderConfig
from ycm.models import ProjectStatus
from ycm.pipeline import Pipeline


@pytest.fixture
def cfg(tmp_path) -> ChannelConfig:
    return ChannelConfig(
        workspace_dir=str(tmp_path / "workspace"),
        style={"min_scene_seconds": 2, "max_scene_seconds": 4, "target_minutes": 1},
        llm=ProviderConfig("mock"),
        tts=ProviderConfig("mock", {"sample_rate": 8000}),
        image=ProviderConfig("mock"),
        video=ProviderConfig("mock"),
        publisher=ProviderConfig(
            "mock", {"ledger_path": str(tmp_path / "ledger.json")}
        ),
    )


def test_full_pipeline_offline(cfg):
    pipe = Pipeline(cfg)
    project = pipe.run_all("The Quiet Pond", publish=True)

    assert project.status == ProjectStatus.PUBLISHED
    assert project.script and len(project.script.scenes) >= 3

    # Every scene got real audio + a real visual on disk.
    for scene in project.script.scenes:
        assert Path(scene.audio_path).exists()
        assert Path(scene.visual_path).exists()
        with wave.open(scene.audio_path) as w:
            assert w.getnframes() > 0

    # The render manifest exists and the project is reloadable from the store.
    assert Path(project.rendered_path).exists()
    assert pipe.store.get(project.id).status == ProjectStatus.PUBLISHED
    assert project.publish.url.startswith("https://youtu.be/")


def test_pipeline_is_resumable(cfg):
    pipe = Pipeline(cfg)
    project = pipe.create("A Walk Through the Seasons")
    pipe.stage_script(project)
    assert project.status == ProjectStatus.SCRIPTED

    # Re-running script should be a no-op (idempotent skip).
    scenes_before = len(project.script.scenes)
    pipe.stage_script(project)
    assert len(project.script.scenes) == scenes_before

    pipe.produce(project)
    assert project.status == ProjectStatus.ASSEMBLED


def test_scheduling_defers_publish(cfg):
    pipe = Pipeline(cfg)
    future = 9_999_999_999.0  # far in the future
    project = pipe.run_all("Counting Stars", scheduled_at=future)
    assert project.status == ProjectStatus.SCHEDULED
    assert project.publish.status == "scheduled"

    # Not due yet -> nothing happens.
    assert pipe.process_due(now=0) == []

    # Due now -> it publishes.
    published = pipe.process_due(now=future + 1)
    assert [p.id for p in published] == [project.id]
    assert pipe.store.get(project.id).status == ProjectStatus.PUBLISHED


def test_pacing_makes_calm_videos(cfg):
    """Sanity-check the house style: scenes are held, transitions are slow."""
    pipe = Pipeline(cfg)
    project = pipe.run_all("The Friendly Snail", publish=False)
    for scene in project.script.scenes:
        assert scene.duration_seconds >= 2.0  # min hold honoured
    for scene in project.script.scenes[1:]:
        assert scene.transition.duration_seconds > 0  # gentle, not hard cuts
