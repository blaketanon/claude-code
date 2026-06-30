"""Command-line interface for the YouTube Channel Manager.

    ycm init                     scaffold config + workspace
    ycm ideas [--count N]        brainstorm video topics
    ycm script "<topic>"         generate (only) a script and print it
    ycm produce "<topic>"        full production run (no publish)
    ycm publish <project_id>     publish/schedule a produced project
    ycm run "<topic>" [--publish|--at ...]   produce and optionally publish
    ycm list [--status S]        list projects
    ycm show <project_id>        show one project's details
    ycm tick                     publish any scheduled projects now due
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import ChannelConfig
from .models import ProjectStatus
from .pipeline import Pipeline
from .providers import get_llm
from .storage import ProjectStore

LOG_FMT = "%(asctime)s  %(levelname)-7s %(name)s  %(message)s"

SAMPLE_CONFIG = """\
# YouTube Channel Manager configuration

[channel]
name = "Slow & Gentle Kids"
niche = "gentle educational stories for young children"
target_audience = "children ages 3-7"
language = "en"
workspace_dir = "workspace"

# The slow, 90s/2000s pacing house style. Tune to taste.
[style]
words_per_minute = 110      # calm, unhurried narration
min_scene_seconds = 8       # always hold a scene this long
max_scene_seconds = 22
tail_pause_seconds = 1.5    # quiet beat after narration
transition_kind = "crossfade"
transition_seconds = 1.5    # slow, gentle transitions
target_minutes = 6

# Providers. Defaults are offline mocks so it runs with zero API keys.
# Swap 'mock' for a real backend and add its options when ready.
[providers.llm]
provider = "mock"           # or "anthropic" (set ANTHROPIC_API_KEY in .env)

[providers.tts]
provider = "mock"           # or "pyttsx3"

[providers.image]
provider = "mock"

[providers.video]
provider = "mock"           # or "ffmpeg" (needs the ffmpeg binary)

[providers.publisher]
provider = "mock"           # or "youtube" (needs OAuth client_secret.json)
"""

SAMPLE_ENV = """\
# Secrets for real providers. This file should never be committed.
# ANTHROPIC_API_KEY=sk-ant-...
"""


def _parse_when(value: str) -> float:
    """Parse a schedule time: ISO 8601, or relative '+90m' / '+2h' / '+1d'."""
    value = value.strip()
    if value.startswith("+"):
        num, unit = value[1:-1], value[-1].lower()
        mult = {"m": 60, "h": 3600, "d": 86400}.get(unit)
        if mult is None:
            raise ValueError("relative time must end in m, h, or d (e.g. +2h)")
        return time.time() + float(num) * mult
    # ISO 8601 (assume local time if no tz)
    import datetime as _dt
    dt = _dt.datetime.fromisoformat(value)
    return dt.timestamp()


def _load(args) -> tuple[ChannelConfig, Pipeline]:
    cfg = ChannelConfig.load(args.config)
    return cfg, Pipeline(cfg)


# --------------------------------------------------------------------- commands
def cmd_init(args) -> int:
    cfg_path = Path(args.config)
    if cfg_path.exists() and not args.force:
        print(f"{cfg_path} already exists (use --force to overwrite).")
        return 1
    cfg_path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    env = cfg_path.parent / ".env"
    if not env.exists():
        env.write_text(SAMPLE_ENV, encoding="utf-8")
    cfg = ChannelConfig.load(str(cfg_path))
    cfg.workspace.mkdir(parents=True, exist_ok=True)
    print(f"Wrote {cfg_path} and prepared workspace '{cfg.workspace}'.")
    print("Edit channel.toml, then try:  ycm run \"The Quiet Pond\"")
    return 0


def cmd_ideas(args) -> int:
    cfg, _ = _load(args)
    ideas = get_llm(cfg).brainstorm(
        niche=cfg.niche, audience=cfg.target_audience, count=args.count
    )
    for i, idea in enumerate(ideas, 1):
        print(f"{i:>2}. {idea.topic}")
        if idea.angle:
            print(f"     angle: {idea.angle}")
    return 0


def cmd_script(args) -> int:
    cfg, pipe = _load(args)
    project = pipe.create(args.topic)
    pipe.stage_script(project)
    print(pipe._render_script_text(project.script))
    print(f"\n[project {project.id}] saved at status '{project.status.value}'.")
    return 0


def cmd_produce(args) -> int:
    _, pipe = _load(args)
    project = pipe.run_all(args.topic, publish=False)
    _summary(project)
    return 0


def cmd_run(args) -> int:
    _, pipe = _load(args)
    scheduled_at = _parse_when(args.at) if args.at else None
    project = pipe.run_all(
        args.topic, publish=args.publish or bool(scheduled_at),
        privacy=args.privacy, scheduled_at=scheduled_at,
    )
    _summary(project)
    return 0


def cmd_publish(args) -> int:
    _, pipe = _load(args)
    project = pipe.store.get(args.project_id)
    if not project:
        print(f"No project '{args.project_id}'.")
        return 1
    scheduled_at = _parse_when(args.at) if args.at else None
    pipe.stage_publish(project, privacy=args.privacy, scheduled_at=scheduled_at)
    _summary(project)
    return 0


def cmd_list(args) -> int:
    _, pipe = _load(args)
    status = ProjectStatus(args.status) if args.status else None
    projects = pipe.store.list(status)
    if not projects:
        print("No projects yet.")
        return 0
    for p in projects:
        title = p.script.title if p.script else p.topic
        print(f"{p.id}  {p.status.value:<13}  {title}")
    return 0


def cmd_show(args) -> int:
    _, pipe = _load(args)
    p = pipe.store.get(args.project_id)
    if not p:
        print(f"No project '{args.project_id}'.")
        return 1
    print(f"id:        {p.id}")
    print(f"topic:     {p.topic}")
    print(f"status:    {p.status.value}")
    if p.script:
        print(f"title:     {p.script.title}")
        print(f"runtime:   {p.script.total_duration_seconds:.1f}s "
              f"({p.script.total_duration_seconds/60:.1f} min), "
              f"{len(p.script.scenes)} scenes")
    if p.rendered_path:
        print(f"rendered:  {p.rendered_path}")
    if p.publish.status != "draft":
        print(f"publish:   {p.publish.status}  {p.publish.url or ''}")
        if p.publish.scheduled_at:
            print(f"scheduled: {time.ctime(p.publish.scheduled_at)}")
    if p.error:
        print(f"error:     {p.error}")
    return 0


def cmd_serve(args) -> int:
    cfg = ChannelConfig.load(args.config)
    from .web.app import serve  # lazy: only needs the 'web' extra here
    print(f"Serving dashboard for '{cfg.name}' at http://{args.host}:{args.port}")
    serve(cfg, host=args.host, port=args.port)
    return 0


def cmd_tick(args) -> int:
    _, pipe = _load(args)
    published = pipe.process_due(time.time())
    if not published:
        print("Nothing due.")
    for p in published:
        print(f"published {p.id}  {p.publish.url}")
    return 0


def _summary(project) -> None:
    print(f"\n[project {project.id}]")
    print(f"  status:   {project.status.value}")
    if project.script:
        print(f"  title:    {project.script.title}")
        print(f"  runtime:  {project.script.total_duration_seconds:.1f}s "
              f"({project.script.total_duration_seconds/60:.1f} min)")
    if project.rendered_path:
        print(f"  rendered: {project.rendered_path}")
    if project.publish.status != "draft":
        print(f"  publish:  {project.publish.status}  {project.publish.url or ''}")


# ------------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ycm", description="YouTube Channel Manager")
    p.add_argument("--config", default="channel.toml", help="path to channel.toml")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="scaffold config + workspace")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("ideas", help="brainstorm topics")
    sp.add_argument("--count", type=int, default=8)
    sp.set_defaults(func=cmd_ideas)

    sp = sub.add_parser("script", help="generate a script only")
    sp.add_argument("topic")
    sp.set_defaults(func=cmd_script)

    sp = sub.add_parser("produce", help="full production run (no publish)")
    sp.add_argument("topic")
    sp.set_defaults(func=cmd_produce)

    sp = sub.add_parser("run", help="produce and optionally publish/schedule")
    sp.add_argument("topic")
    sp.add_argument("--publish", action="store_true", help="publish immediately")
    sp.add_argument("--at", help="schedule time: ISO 8601 or relative (+2h, +1d)")
    sp.add_argument("--privacy", default="private",
                    choices=["private", "unlisted", "public"])
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("publish", help="publish/schedule a produced project")
    sp.add_argument("project_id")
    sp.add_argument("--at", help="schedule time: ISO 8601 or relative (+2h, +1d)")
    sp.add_argument("--privacy", default="private",
                    choices=["private", "unlisted", "public"])
    sp.set_defaults(func=cmd_publish)

    sp = sub.add_parser("list", help="list projects")
    sp.add_argument("--status", choices=[s.value for s in ProjectStatus])
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="show one project")
    sp.add_argument("project_id")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("tick", help="publish scheduled projects now due")
    sp.set_defaults(func=cmd_tick)

    sp = sub.add_parser("serve", help="launch the web dashboard")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format=LOG_FMT
    )
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        if args.verbose:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
