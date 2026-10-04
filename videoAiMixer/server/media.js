// ffmpeg helpers: pull the voice track and a representative portrait frame out of the upload.
import { spawn } from "node:child_process";

function run(cmd, args) {
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("error", (err) =>
      reject(err.code === "ENOENT" ? new Error(`${cmd} is not installed or not on PATH`) : err),
    );
    proc.on("close", (code) =>
      code === 0 ? resolve(stdout) : reject(new Error(`${cmd} exited ${code}: ${stderr.slice(-800)}`)),
    );
  });
}

export async function probeDuration(file) {
  const out = await run("ffprobe", [
    "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", file,
  ]);
  const seconds = parseFloat(out.trim());
  return Number.isFinite(seconds) ? seconds : null;
}

export async function hasAudioStream(file) {
  const out = await run("ffprobe", [
    "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", file,
  ]);
  return out.trim().length > 0;
}

/** Mono 44.1kHz MP3 - good input for both transcription and voice cloning. */
export async function extractAudio(videoFile, outFile) {
  await run("ffmpeg", [
    "-y", "-i", videoFile, "-vn", "-ac", "1", "-ar", "44100",
    // light cleanup: cut rumble, normalise loudness
    "-af", "highpass=f=80,loudnorm",
    "-codec:a", "libmp3lame", "-b:a", "128k", outFile,
  ]);
  return outFile;
}

/**
 * Picks a representative frame from the first ~30s (ffmpeg's `thumbnail` filter scores
 * frames for being typical of their batch, which skips blinks and transition frames),
 * then crops to a centred square portrait - the shape talking-head services expect.
 */
export async function extractPortrait(inputFile, outFile, { isImage = false } = {}) {
  const square = "crop='min(iw,ih)':'min(iw,ih)',scale=768:768";
  await run("ffmpeg", [
    "-y", ...(isImage ? [] : ["-ss", "1", "-t", "30"]), "-i", inputFile,
    "-vf", isImage ? square : `thumbnail=150,${square}`,
    "-frames:v", "1", "-q:v", "2", outFile,
  ]);
  return outFile;
}
