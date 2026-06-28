"""Configuration loading.

Channel configuration lives in a TOML file (read with the stdlib ``tomllib``).
A small ``.env`` loader pulls secrets like API keys into the environment so the
config file itself never has to contain them.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_NAME = "channel.toml"


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal ``.env`` loader (no external dependency).

    Parses ``KEY=VALUE`` lines, ignoring blanks and ``#`` comments. Existing
    environment variables are never overwritten, so real env always wins.
    """
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass
class ProviderConfig:
    """Which implementation to use for one provider slot, plus its options."""

    provider: str = "mock"
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_section(cls, section: dict[str, Any]) -> "ProviderConfig":
        section = dict(section or {})
        provider = section.pop("provider", "mock")
        return cls(provider=provider, options=section)


@dataclass
class ChannelConfig:
    """Top-level configuration for a single channel/workspace."""

    name: str = "My Kids Channel"
    niche: str = "gentle educational stories for young children"
    target_audience: str = "children ages 3-7"
    language: str = "en"
    workspace_dir: str = "workspace"

    style: dict[str, Any] = field(default_factory=dict)

    llm: ProviderConfig = field(default_factory=ProviderConfig)
    tts: ProviderConfig = field(default_factory=ProviderConfig)
    image: ProviderConfig = field(default_factory=ProviderConfig)
    video: ProviderConfig = field(default_factory=ProviderConfig)
    publisher: ProviderConfig = field(default_factory=ProviderConfig)

    # Resolved at load time.
    config_path: str = ""

    @property
    def workspace(self) -> Path:
        return Path(self.workspace_dir)

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "ChannelConfig":
        """Load config from ``path`` (or ``./channel.toml``).

        Also loads a sibling ``.env`` file if present. Missing config falls
        back to sensible defaults so the system runs out of the box.
        """
        cfg_path = Path(path) if path else Path(DEFAULT_CONFIG_NAME)
        load_dotenv(cfg_path.parent / ".env" if cfg_path.parent != Path("") else ".env")
        load_dotenv(".env")

        if not cfg_path.exists():
            return cls(config_path=str(cfg_path))

        with cfg_path.open("rb") as fh:
            data = tomllib.load(fh)

        channel = data.get("channel", {})
        providers = data.get("providers", {})

        return cls(
            name=channel.get("name", cls.name),
            niche=channel.get("niche", cls.niche),
            target_audience=channel.get("target_audience", cls.target_audience),
            language=channel.get("language", cls.language),
            workspace_dir=channel.get("workspace_dir", cls.workspace_dir),
            style=data.get("style", {}),
            llm=ProviderConfig.from_section(providers.get("llm", {})),
            tts=ProviderConfig.from_section(providers.get("tts", {})),
            image=ProviderConfig.from_section(providers.get("image", {})),
            video=ProviderConfig.from_section(providers.get("video", {})),
            publisher=ProviderConfig.from_section(providers.get("publisher", {})),
            config_path=str(cfg_path),
        )
