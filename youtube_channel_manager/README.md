# YouTube Channel Manager

A provider-agnostic pipeline for **automatically producing and publishing
calm, slow-paced children's videos** in the spirit of 90s/2000s programming —
long, lingering shots and gentle transitions designed to be kind to young
attention spans rather than to fragment them.

It takes a topic and carries it all the way through:

```
idea  ->  script  ->  narration  ->  visuals  ->  assembly  ->  publish/schedule
```

Every external capability (the writing model, the voice, the imagery, the video
encoder, the uploader) is a **pluggable provider**. The defaults are fully
offline mocks, so the whole system runs end-to-end with **zero API keys** and no
heavy dependencies — then you swap in real backends one at a time as you're
ready.

## The "slow TV for kids" house style

The pacing is the product. A dedicated [`PacingStyle`](ycm/style.py) engine
encodes the channel's identity as concrete numbers and stamps them onto every
script:

| Knob | Default | Why |
|------|---------|-----|
| `words_per_minute` | 110 | Unhurried narration (typical YouTube is ~160). |
| `min_scene_seconds` | 8 | Every shot is *held*, even for short lines — the lingering 90s feel. |
| `max_scene_seconds` | 22 | Keeps any single beat from dragging too long. |
| `tail_pause_seconds` | 1.5 | A quiet beat after the narration, to let it breathe. |
| `transition_seconds` | 1.5 | Slow crossfades instead of rapid hard cuts. |

These are all configurable in [`channel.toml`](channel.toml).

## Quick start

```bash
# No install needed to try it — the core is pure standard library.
cd youtube_channel_manager
export PYTHONPATH=$PWD

# 1. Scaffold config + workspace (writes channel.toml and .env)
python -m ycm init

# 2. Brainstorm topics
python -m ycm ideas --count 8

# 3. Produce a full video (script + narration + visuals + assembly) and publish
python -m ycm run "The Quiet Pond and Its Friends" --publish

# Or schedule it for later (ISO 8601, or relative like +2h / +1d)
python -m ycm run "Counting the Stars Before Bedtime" --at +1d

# 4. Inspect what you have
python -m ycm list
python -m ycm show <project_id>

# 5. Publish anything whose scheduled time has arrived (run from cron)
python -m ycm tick
```

Installed as a package (`pip install -e .`) the same commands are available as
the `ycm` console script.

### What lands on disk

Each run writes a self-contained project folder under `workspace/projects/<id>/`:

```
script.txt                 human-readable script with per-scene timing
audio/scene_000.wav ...    one narration clip per scene (real WAV)
visuals/scene_000.svg ...  one visual per scene
render/video.timeline.json the render plan (start times, durations, transitions)
render/video.edl.txt       a readable edit decision list
```

Project state is tracked in a SQLite database (`workspace/ycm.db`), which makes
runs **resumable** — re-running a project skips stages it has already completed,
so you can, say, add real API keys and re-render only the final video.

## Going from mocks to the real thing

Edit the `[providers.*]` sections in `channel.toml` and install the matching
extra. Each real provider fails with a clear, actionable message if its
dependency or credential is missing.

| Slot | Mock (default) | Real option | Install / configure |
|------|----------------|-------------|---------------------|
| `llm` | deterministic script writer | `anthropic` (Claude) | `pip install '.[anthropic]'`, set `ANTHROPIC_API_KEY` in `.env` |
| `tts` | valid placeholder WAV | `pyttsx3` (offline voice) | `pip install '.[tts]'` |
| `image` | pastel SVG cards | *(add your own)* | implement `ImageProvider` |
| `video` | render plan (JSON + EDL) | `ffmpeg` | install the `ffmpeg` binary |
| `publisher` | local upload ledger | `youtube` | `pip install '.[youtube]'`, add OAuth `client_secret.json` |

Secrets go in `.env` (gitignored) or real environment variables — never in
`channel.toml`.

## Architecture

```
ycm/
  models.py          dataclasses: VideoProject, Script, Scene, ... (+ JSON I/O)
  style.py           PacingStyle — the slow 90s/2000s pacing engine
  config.py          channel.toml + .env loading
  storage.py         SQLite-backed, resumable project store
  pipeline.py        orchestrates the stages; idempotent + resumable
  cli.py             the `ycm` command-line interface
  providers/
    base.py          abstract interfaces (LLM/TTS/Image/Video/Publisher)
    llm.py  tts.py  image.py  video.py  publisher.py   mock + real backends
    registry.py      maps config names -> implementations
tests/               pytest suite (offline, no network)
```

### Adding a provider

1. Implement the relevant base class in `ycm/providers/`.
2. Register it in [`ycm/providers/registry.py`](ycm/providers/registry.py).
3. Reference it by name in `channel.toml`.

## Development

```bash
pip install pytest
python -m pytest          # 15 tests, fully offline
```

## Responsible use

This tool is built for original, calm children's content. If you publish for
kids, follow the platform's rules for made-for-kids content (the YouTube
publisher sets `selfDeclaredMadeForKids` and defaults uploads to private) and
relevant regulations such as COPPA. Don't use it to mass-produce low-effort or
misleading content.

## License

MIT.
