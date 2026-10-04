// D-ID Talks API: animates the portrait so it lip-syncs to an audio clip.
import fs from "node:fs/promises";
import path from "node:path";
import { did as didConfig } from "../config.js";

const BASE = "https://api.d-id.com";
const POLL_MS = 1500;
const TIMEOUT_MS = 3 * 60 * 1000;

async function call(pathname, init) {
  const res = await fetch(`${BASE}${pathname}`, {
    ...init,
    headers: { authorization: `Basic ${didConfig.apiKey}`, accept: "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`D-ID ${pathname} failed (${res.status}): ${body.slice(0, 400)}`);
  }
  return res.json();
}

async function upload(kind, file, type) {
  const form = new FormData();
  form.append(kind, new Blob([await fs.readFile(file)], { type }), path.basename(file));
  const json = await call(`/${kind}s`, { method: "POST", body: form });
  return json.url;
}

/** Upload once per avatar and cache the returned URL on the avatar record. */
export function uploadPortrait(imageFile) {
  return upload("image", imageFile, "image/jpeg");
}

/**
 * Renders a lip-synced clip and returns its (temporary, D-ID hosted) MP4 URL.
 * The caller downloads it so the clip outlives D-ID's link expiry.
 */
export async function renderTalk({ portraitUrl, audioFile }) {
  const audioUrl = await upload("audio", audioFile, "audio/mpeg");
  const { id } = await call("/talks", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      source_url: portraitUrl,
      script: { type: "audio", audio_url: audioUrl },
      config: { stitch: true, fluent: true },
    }),
  });

  const deadline = Date.now() + TIMEOUT_MS;
  while (Date.now() < deadline) {
    const talk = await call(`/talks/${id}`);
    if (talk.status === "done") return talk.result_url;
    if (talk.status === "error" || talk.status === "rejected") {
      throw new Error(`D-ID render ${talk.status}: ${JSON.stringify(talk.error || {}).slice(0, 300)}`);
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
  throw new Error("D-ID render timed out");
}

export async function download(url, outFile) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  await fs.writeFile(outFile, Buffer.from(await res.arrayBuffer()));
  return outFile;
}
