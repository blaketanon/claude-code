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


# xfade transition names per our TransitionSpec.kind.
_XFADE_KIND = {"crossfade": "fade", "fade": "fade", "dissolve": "dissolve",
               "hold": "fade"}


class FFmpegVideo(VideoProvider):
    """Real render: scenes joined with the slow overlapping crossfades.

    Each scene's still visual is held for its full duration and gently crossfaded
    into the next via ffmpeg's ``xfade``/``acrossfade`` filters — the actual
    90s/2000s pacing, encoded into pixels and sound. SVG visuals (the default
    mock imagery) are rasterised to PNG first via ``rsvg-convert`` since ffmpeg
    can't read SVG directly.
    """

    def __init__(self, fps: int = 30, width: int = 1280, height: int = 720,
                 crf: int = 23, **_: object) -> None:
        self.fps = int(fps)
        self.width = int(width)
        self.height = int(height)
        self.crf = int(crf)

    @staticmethod
    def _ffmpeg() -> str:
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError(
                "FFmpegVideo needs the 'ffmpeg' binary on PATH. Install ffmpeg, "
                "or set providers.video.provider = 'mock' in channel.toml."
            )
        return exe

    def _raster(self, visual_path: str | None, dest: Path) -> str | None:
        """Return a raster image path for ``visual_path`` (rasterising SVG)."""
        if not visual_path:
            return None
        src = Path(visual_path)
        if src.suffix.lower() != ".svg":
            return str(src)
        rsvg = shutil.which("rsvg-convert")
        if not rsvg:
            raise RuntimeError(
                "Rendering SVG visuals needs 'rsvg-convert' (librsvg2-bin). "
                "Install it, or use an image provider that outputs PNG/JPG."
            )
        png = dest / (src.stem + ".png")
        subprocess.run(
            [rsvg, "-w", str(self.width), "-h", str(self.height),
             str(src), "-o", str(png)],
            check=True, capture_output=True,
        )
        return str(png)

    def assemble(self, *, project, out_path: str) -> str:
        ffmpeg = self._ffmpeg()
        out = Path(out_path).with_suffix(".mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        scenes = project.script.scenes
        tmp = out.parent / "_clips"
        tmp.mkdir(exist_ok=True)

        n = len(scenes)
        images = [self._raster(s.visual_path, tmp) for s in scenes]
        durs = [float(s.duration_seconds) for s in scenes]
        # Transition leading into scene i (i>=1); clamp so it never exceeds a clip.
        trans = [min(float(s.transition.duration_seconds), durs[i] - 0.1, durs[i-1] - 0.1)
                 for i, s in enumerate(scenes)]

        cmd: list[str] = [ffmpeg, "-y"]
        # Inputs: all images first (looped to their duration), then all audio.
        for i in range(n):
            cmd += ["-loop", "1", "-t", f"{durs[i]:.3f}", "-i", images[i] or ""]
        for i, s in enumerate(scenes):
            if s.audio_path:
                cmd += ["-i", s.audio_path]
            else:  # silent bed of the right length
                cmd += ["-f", "lavfi", "-t", f"{durs[i]:.3f}",
                        "-i", "anullsrc=r=44100:cl=stereo"]

        parts: list[str] = []
        for i in range(n):
            parts.append(
                f"[{i}:v]scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                f"fps={self.fps},format=yuv420p[v{i}]"
            )
        for i, s in enumerate(scenes):
            parts.append(
                f"[{n+i}:a]aresample=44100,apad,atrim=0:{durs[i]:.3f},"
                f"asetpts=PTS-STARTPTS[a{i}]"
            )

        # Chain video xfades and audio crossfades; offsets follow the overlap
        # model so total runtime == sum(durations) - sum(transitions).
        vprev, aprev, length = "v0", "a0", durs[0]
        for i in range(1, n):
            t = max(0.05, trans[i])
            offset = length - t
            vout, aout = f"vx{i}", f"ax{i}"
            kind = _XFADE_KIND.get(scenes[i].transition.kind, "fade")
            parts.append(f"[{vprev}][v{i}]xfade=transition={kind}:"
                         f"duration={t:.3f}:offset={offset:.3f}[{vout}]")
            parts.append(f"[{aprev}][a{i}]acrossfade=d={t:.3f}[{aout}]")
            vprev, aprev = vout, aout
            length = length + durs[i] - t

        cmd += [
            "-filter_complex", ";".join(parts),
            "-map", f"[{vprev}]", "-map", f"[{aprev}]",
            "-c:v", "libx264", "-crf", str(self.crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
            raise RuntimeError(f"ffmpeg render failed:\n{tail}")
        return str(out)
