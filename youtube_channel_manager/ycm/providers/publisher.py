"""Publishing providers.

``MockPublisher`` records uploads/schedules to a local JSON ledger and assigns a
fake video id + URL, so publishing and scheduling can be exercised offline.
``YouTubePublisher`` is a real integration that lazily imports the Google API
client and uploads via the YouTube Data API v3.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .base import PublisherProvider
from ..models import PublishInfo


class MockPublisher(PublisherProvider):
    """Simulates YouTube publishing against a local ledger file."""

    def __init__(self, ledger_path: str = "workspace/publish_ledger.json",
                 **_: object) -> None:
        self.ledger_path = Path(ledger_path)

    def _append(self, record: dict) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger = []
        if self.ledger_path.exists():
            ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        ledger.append(record)
        self.ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")

    def publish(self, *, project, privacy: str, scheduled_at: float | None) -> PublishInfo:
        fake_id = f"mock-{project.id}"
        info = PublishInfo(
            video_id=fake_id,
            url=f"https://youtu.be/{fake_id}",
            privacy=privacy,
        )
        if scheduled_at and scheduled_at > time.time():
            info.status = "scheduled"
            info.scheduled_at = scheduled_at
        else:
            info.status = "published"
            info.published_at = time.time()
        self._append({
            "project_id": project.id,
            "title": project.script.title if project.script else project.topic,
            "rendered_path": project.rendered_path,
            "status": info.status,
            "video_id": info.video_id,
            "url": info.url,
            "privacy": privacy,
            "scheduled_at": info.scheduled_at,
            "published_at": info.published_at,
            "recorded_at": time.time(),
        })
        return info


class YouTubePublisher(PublisherProvider):
    """Uploads to YouTube via the Data API. Requires the ``youtube`` extra."""

    def __init__(self, client_secrets: str = "client_secret.json",
                 token_path: str = "youtube_token.json",
                 category_id: str = "27", **_: object) -> None:
        self.client_secrets = client_secrets
        self.token_path = token_path
        self.category_id = category_id  # 27 = Education

    def _service(self):
        try:
            from google.oauth2.credentials import Credentials  # type: ignore
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
        except ImportError as e:  # pragma: no cover - optional extra
            raise RuntimeError(
                "YouTubePublisher requires the 'youtube' extra. Install with: "
                "pip install 'youtube-channel-manager[youtube]'"
            ) from e
        scopes = ["https://www.googleapis.com/auth/youtube.upload"]
        creds = None
        tok = Path(self.token_path)
        if tok.exists():
            creds = Credentials.from_authorized_user_file(self.token_path, scopes)
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets, scopes)
            creds = flow.run_local_server(port=0)
            tok.write_text(creds.to_json(), encoding="utf-8")
        return build("youtube", "v3", credentials=creds)

    def publish(self, *, project, privacy: str, scheduled_at: float | None) -> PublishInfo:
        from googleapiclient.http import MediaFileUpload  # type: ignore

        if not project.rendered_path:
            raise RuntimeError("Project has no rendered video to upload.")
        svc = self._service()
        script = project.script
        status = {"privacyStatus": "private" if scheduled_at else privacy,
                  "selfDeclaredMadeForKids": True}
        if scheduled_at:
            status["publishAt"] = (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(scheduled_at))
            )
        body = {
            "snippet": {
                "title": script.title if script else project.topic,
                "description": script.description if script else "",
                "tags": script.tags if script else [],
                "categoryId": self.category_id,
            },
            "status": status,
        }
        media = MediaFileUpload(project.rendered_path, resumable=True)
        request = svc.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()
        vid = response["id"]
        return PublishInfo(
            status="scheduled" if scheduled_at else "published",
            video_id=vid,
            url=f"https://youtu.be/{vid}",
            scheduled_at=scheduled_at,
            published_at=None if scheduled_at else time.time(),
            privacy=status["privacyStatus"],
        )
