// Tiny JSON-on-disk store. One directory per avatar:
//   data/avatars/<id>/avatar.json, source video, extracted audio, portrait, generated media.
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { DATA_DIR } from "./config.js";

const AVATARS_DIR = path.join(DATA_DIR, "avatars");

export function newId() {
  return crypto.randomBytes(8).toString("hex");
}

export function avatarDir(id) {
  if (!/^[a-f0-9]{16}$/.test(id)) throw new Error("Invalid avatar id");
  return path.join(AVATARS_DIR, id);
}

export async function ensureAvatarDir(id) {
  const dir = avatarDir(id);
  await fs.mkdir(path.join(dir, "media"), { recursive: true });
  return dir;
}

export async function saveAvatar(avatar) {
  const dir = await ensureAvatarDir(avatar.id);
  avatar.updatedAt = new Date().toISOString();
  const tmp = path.join(dir, "avatar.json.tmp");
  await fs.writeFile(tmp, JSON.stringify(avatar, null, 2));
  await fs.rename(tmp, path.join(dir, "avatar.json"));
  return avatar;
}

export async function getAvatar(id) {
  try {
    const raw = await fs.readFile(path.join(avatarDir(id), "avatar.json"), "utf8");
    return JSON.parse(raw);
  } catch (err) {
    if (err.code === "ENOENT" || err.message === "Invalid avatar id") return null;
    throw err;
  }
}

export async function updateAvatar(id, patch) {
  const avatar = await getAvatar(id);
  if (!avatar) throw new Error(`Avatar ${id} not found`);
  return saveAvatar({ ...avatar, ...patch });
}

export async function listAvatars() {
  await fs.mkdir(AVATARS_DIR, { recursive: true });
  const ids = await fs.readdir(AVATARS_DIR);
  const avatars = await Promise.all(ids.map((id) => getAvatar(id)));
  return avatars
    .filter(Boolean)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function deleteAvatar(id) {
  await fs.rm(avatarDir(id), { recursive: true, force: true });
}

// Strip server-only fields before sending to the browser.
export function publicAvatar(a) {
  const { persona, transcript, systemPrompt, voiceId, didPortraitUrl, consent, ...rest } = a;
  return {
    ...rest,
    consentAcceptedAt: consent?.acceptedAt ?? null,
    hasVoiceClone: Boolean(voiceId),
    hasVideoAvatar: Boolean(didPortraitUrl),
    hasTranscript: Boolean(transcript),
    persona: persona
      ? { summary: persona.summary, traits: persona.traits, speakingStyle: persona.speakingStyle, topics: persona.topics, greeting: persona.greeting }
      : null,
  };
}
