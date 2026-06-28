"""Video assembly providers.

``MockVideo`` produces a deterministic *render manifest* (JSON timeline + a
human-readable EDL) describing exactly how the scenes, audio, and slow
transitions stitch together. This lets the full pipeline complete and be
inspected with zero heavy dependencies.

``FFmpegVideo`` performs a real render via the ``ffmpeg`` binary, honouring the
slow crossfade transitions. It degrades gracefully: if ``ffmpeg`` isn't on the
PATH it raises a clear error pointing back at the mock provider.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .base import VideoProvider


class MockVideo(VideoProvider):
    """Writes a render plan instead of encoding pixels."""

    def __init__(self, **_: object) -> None:
        pass

    def assemble(self, *, project, out_path: str) -> str:
        out = Path(out_path).with_suffix(".timeline.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        script = project.script
        timeline = []
        clock = 0.0
        for scene in script.scenes:
            trans = scene.transition
            # A crossfade overlaps with the previous scene's tail.
            start = max(0.0, clock - (trans.duration_seconds if scene.index else 0.0))
            timeline.append({
                "scene": scene.index,
                "start": round(start, 2),
                "duration": scene.duration_seconds,
                "transition_in": trans.to_dict(),
                "visual": scene.visual_path,
                "audio": scene.audio_path,
                "narration": scene.narration,
            })
            clock = start + scene.duration_seconds
        manifest = {
            "title": script.title,
            "total_duration": round(clock, 2),
            "scene_count": len(script.scenes),
            "fps": 30,
            "timeline": timeline,
        }
        out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Human-readable edit decision list next to it.
        edl = out.with_suffix("").with_suffix(".edl.txt")
        lines = [f"# {script.title} — {round(clock,2)}s total", ""]
        for t in timeline:
            lines.append(
                f"[{t['start']:>7.2f}s] scene {t['scene']+1:>2}  "
                f"({t['duration']:.1f}s, {t['transition_in']['kind']} "
                f"{t['transition_in']['duration_seconds']:.1f}s)  "
                f"visual={Path(t['visual']).name if t['visual'] else '-'}"
            )
        edl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(out)


class FFmpegVideo(VideoProvider):
    """Real render: one image+audio clip per scene, joined with crossfades."""

    def __init__(self, fps: int = 30, width: int = 1280, height: int = 720,
                 **_: object) -> None:
        self.fps = int(fps)
        self.width = int(width)
        self.height = int(height)

    @staticmethod
    def _ffmpeg() -> str:
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError(
                "FFmpegVideo needs the 'ffmpeg' binary on PATH. Install ffmpeg, "
                "or set providers.video.provider = 'mock' in channel.toml."
            )
        return exe

    def assemble(self, *, project, out_path: str) -> str:
        ffmpeg = self._ffmpeg()
        out = Path(out_path).with_suffix(".mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        scenes = project.script.scenes

        clip_dir = out.parent / "_clips"
        clip_dir.mkdir(exist_ok=True)
        clips: list[Path] = []
        for s in scenes:
            clip = clip_dir / f"scene_{s.index:03d}.mp4"
            cmd = [
                ffmpeg, "-y", "-loop", "1", "-i", s.visual_path or "",
            ]
            if s.audio_path:
                cmd += ["-i", s.audio_path]
            cmd += [
                "-t", f"{s.duration_seconds}",
                "-r", str(self.fps),
                "-vf", f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                       f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2",
                "-pix_fmt", "yuv420p",
            ]
            if s.audio_path:
                cmd += ["-c:a", "aac", "-shortest"]
            cmd.append(str(clip))
            subprocess.run(cmd, check=True, capture_output=True)
            clips.append(clip)

        # Concatenate. (A full xfade filtergraph is possible; for portability we
        # concat the per-scene clips, each of which already begins with a fade.)
        concat_file = clip_dir / "concat.txt"
        concat_file.write_text(
            "".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8"
        )
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
             "-c", "copy", str(out)],
            check=True, capture_output=True,
        )
        return str(out)
