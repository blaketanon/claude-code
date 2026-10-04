// Client for the self-hosted voice server (see /voice-server): speech-to-text,
// zero-shot voice cloning from a reference clip, and text-to-speech in that voice.
import fs from "node:fs/promises";
import path from "node:path";
import { voice } from "../config.js";

async function call(base, pathname, init = {}) {
  const res = await fetch(`${base}${pathname}`, {
    ...init,
    headers: { ...(voice.apiKey ? { authorization: `Bearer ${voice.apiKey}` } : {}), ...(init.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Voice server ${pathname} failed (${res.status}): ${body.slice(0, 400)}`);
  }
  return res;
}

async function fileBlob(file, type) {
  return new Blob([await fs.readFile(file)], { type });
}

export async function ping() {
  const res = await fetch(`${voice.baseUrl}/health`, { signal: AbortSignal.timeout(3000) });
  return res.ok;
}

/** OpenAI-compatible transcription, so STT_BASE_URL can also point at speaches, LocalAI, etc. */
export async function transcribe(audioFile, mimeType = "audio/mpeg") {
  const form = new FormData();
  form.append("model", voice.sttModel);
  form.append("response_format", "json");
  form.append("file", await fileBlob(audioFile, mimeType), path.basename(audioFile));
  const res = await call(voice.sttBaseUrl, "/v1/audio/transcriptions", { method: "POST", body: form });
  const json = await res.json();
  return (json.text || "").trim();
}

/** Registers the person's voice sample; the server keeps a cleaned reference clip. */
export async function cloneVoice({ name, audioFile }) {
  const form = new FormData();
  form.append("name", name);
  form.append("file", await fileBlob(audioFile, "audio/mpeg"), "sample.mp3");
  const res = await call(voice.baseUrl, "/voices", { method: "POST", body: form });
  return (await res.json()).voice_id;
}

export async function deleteVoice(voiceId) {
  await call(voice.baseUrl, `/voices/${encodeURIComponent(voiceId)}`, { method: "DELETE" });
}

/** Returns an MP3 Buffer of `text` spoken in the cloned voice. */
export async function speak(voiceId, text) {
  const res = await call(voice.baseUrl, "/tts", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ voice_id: voiceId, text, format: "mp3" }),
  });
  return Buffer.from(await res.arrayBuffer());
}
