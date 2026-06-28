import time

import pytest

from ycm.config import ChannelConfig, ProviderConfig

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ycm.web.app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    cfg = ChannelConfig(
        workspace_dir=str(tmp_path / "ws"),
        style={"min_scene_seconds": 2, "max_scene_seconds": 3, "target_minutes": 1},
        llm=ProviderConfig("mock"),
        tts=ProviderConfig("mock", {"sample_rate": 8000}),
        image=ProviderConfig("mock"),
        video=ProviderConfig("mock"),
        publisher=ProviderConfig("mock", {"ledger_path": str(tmp_path / "l.json")}),
    )
    return TestClient(create_app(cfg))


def _run_to_completion(client, topic, **body):
    r = client.post("/api/projects", json={"topic": topic, **body})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    for _ in range(100):  # up to ~10s
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish in time")


def test_index_and_config(client):
    assert "<title>YouTube Channel Manager" in client.get("/").text
    cfg = client.get("/api/config").json()
    assert cfg["providers"]["llm"] == "mock"
    assert cfg["style"]["words_per_minute"] > 0


def test_ideas_endpoint(client):
    ideas = client.get("/api/ideas?count=3").json()
    assert len(ideas) == 3
    assert all("topic" in i for i in ideas)


def test_create_produce_and_fetch_media(client):
    job = _run_to_completion(client, "The Quiet Pond", publish=True)
    assert job["status"] == "done", job.get("error")
    pid = job["project_id"]

    # Stage progression was reported live.
    stages = [e["stage"] for e in job["events"]]
    assert "scripted" in stages and "assembled" in stages

    project = client.get(f"/api/projects/{pid}").json()
    assert project["status"] == "published"
    scenes = project["script"]["scenes"]
    assert len(scenes) >= 3

    # Media endpoints serve real files with correct content types.
    audio = client.get(scenes[0]["audio_url"])
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/")
    visual = client.get(scenes[0]["visual_url"])
    assert visual.status_code == 200
    assert "svg" in visual.headers["content-type"]


def test_listing_and_404s(client):
    _run_to_completion(client, "Counting Stars")
    assert len(client.get("/api/projects").json()) >= 1
    assert client.get("/api/projects/nope").status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404


def test_publish_via_api(client):
    job = _run_to_completion(client, "A Walk Through the Seasons")
    pid = job["project_id"]
    r = client.post(f"/api/projects/{pid}/publish", json={"privacy": "private"})
    assert r.status_code == 200
    assert r.json()["publish"]["status"] == "published"
