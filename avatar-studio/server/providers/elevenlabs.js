// ElevenLabs: speech-to-text (for the training transcript and live mic input),
// instant voice cloning, and text-to-speech in the cloned voice.
import fs from "node:fs/promises";
import path from "node:path";
import { keys } from "../config.js";

const BASE = "https://api.elevenlabs.io/v1";
const STT_MODEL = process.env.ELEVENLABS_STT_MODEL || "scribe_v1";
const TTS_MODEL = process.env.ELEVENLABS_TTS_MODEL || "eleven_multilingual_v2";

async function call(pathname, init) {
  const res = await fetch(`${BASE}${pathname}`, {
    ...init,
    headers: { "xi-api-key": keys.elevenlabs, ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`ElevenLabs ${pathname} failed (${res.status}): ${body.slice(0, 400)}`);
  }
  return res;
}

async function fileBlob(file, type) {
  return new Blob([await fs.readFile(file)], { type });
}

export async function transcribe(audioFile, mimeType = "audio/mpeg") {
  const form = new FormData();
  form.append("model_id", STT_MODEL);
  form.append("file", await fileBlob(audioFile, mimeType), path.basename(audioFile));
  const res = await call("/speech-to-text", { method: "POST", body: form });
  const json = await res.json();
  return (json.text || "").trim();
}

export async function cloneVoice({ name, audioFile, description }) {
  const form = new FormData();
  form.append("name", name);
  if (description) form.append("description", description.slice(0, 500));
  form.append("remove_background_noise", "true");
  form.append("files", await fileBlob(audioFile, "audio/mpeg"), "sample.mp3");
  const res = await call("/voices/add", { method: "POST", body: form });
  const json = await res.json();
  return json.voice_id;
}

export async function deleteVoice(voiceId) {
  await call(`/voices/${encodeURIComponent(voiceId)}`, { method: "DELETE" });
}

/** Returns an MP3 Buffer of `text` spoken in the cloned voice. */
export async function speak(voiceId, text) {
  const res = await call(
    `/text-to-speech/${encodeURIComponent(voiceId)}?output_format=mp3_44100_128`,
    {
      method: "POST",
      headers: { "content-type": "application/json", accept: "audio/mpeg" },
      body: JSON.stringify({
        text,
        model_id: TTS_MODEL,
        voice_settings: { stability: 0.45, similarity_boost: 0.85, style: 0.2, use_speaker_boost: true },
      }),
    },
  );
  return Buffer.from(await res.arrayBuffer());
}
