// "Training" pipeline: video -> audio + portrait -> transcript -> voice clone -> personality profile -> video-ready avatar.
//
// Nothing here fine-tunes model weights. Voice is an ElevenLabs instant clone; personality is a
// Claude-written profile plus the verbatim transcript, both injected into the system prompt.
// That gets most of the realism of a fine-tune from a few minutes of footage, at no training cost.
import path from "node:path";
import fs from "node:fs/promises";
import { features } from "./config.js";
import { avatarDir, getAvatar, saveAvatar } from "./store.js";
import * as media from "./media.js";
import * as eleven from "./providers/elevenlabs.js";
import * as did from "./providers/did.js";
import * as claude from "./providers/claude.js";
import * as demo from "./providers/demo.js";

export const STEPS = [
  { key: "media", label: "Extract voice track & portrait" },
  { key: "transcript", label: "Transcribe speech" },
  { key: "voice", label: "Clone voice" },
  { key: "persona", label: "Learn personality" },
  { key: "video", label: "Prepare talking avatar" },
];

export function initialSteps() {
  return STEPS.map((s) => ({ ...s, status: "pending", detail: "" }));
}

async function setStep(id, key, status, detail = "") {
  const avatar = await getAvatar(id);
  avatar.steps = avatar.steps.map((s) => (s.key === key ? { ...s, status, detail } : s));
  return saveAvatar(avatar);
}

async function step(id, key, fn) {
  await setStep(id, key, "running");
  try {
    const result = await fn();
    const { status = "done", detail = "" } = result || {};
    await setStep(id, key, status, detail);
  } catch (err) {
    await setStep(id, key, "error", err.message);
    throw err;
  }
}

export async function runPipeline(id) {
  const dir = avatarDir(id);
  const mediaDir = path.join(dir, "media");
  let avatar = await getAvatar(id);
  const video = path.join(dir, avatar.sourceFile);
  const audioFile = path.join(dir, "voice.mp3");
  const portraitFile = path.join(mediaDir, "portrait.jpg");

  try {
    await saveAvatar({ ...avatar, status: "processing", error: null });

    await step(id, "media", async () => {
      const duration = await media.probeDuration(video);
      if (!(await media.hasAudioStream(video))) throw new Error("The video has no audio track - the avatar needs to hear the person speak.");
      await media.extractAudio(video, audioFile);
      const hasCustomPortrait = await fs.stat(portraitFile).then(() => true, () => false);
      if (!hasCustomPortrait) await media.extractPortrait(video, portraitFile);
      await saveAvatar({ ...(await getAvatar(id)), durationSec: duration, portrait: "portrait.jpg" });
      const warn = duration && duration < 60 ? " (under 1 minute - voice and personality will be rough; 2-5 minutes of natural talking works best)" : "";
      return { detail: `${duration ? Math.round(duration) + "s of footage" : "Done"}${warn}` };
    });

    await step(id, "transcript", async () => {
      const transcript = features.voice ? await eleven.transcribe(audioFile) : demo.demoTranscript(avatar.name);
      if (!transcript) throw new Error("No speech was detected in the video.");
      await saveAvatar({ ...(await getAvatar(id)), transcript });
      const words = transcript.split(/\s+/).length;
      return { status: features.voice ? "done" : "skipped", detail: features.voice ? `${words} words` : "Demo placeholder (no ELEVENLABS_API_KEY)" };
    });

    await step(id, "voice", async () => {
      if (!features.voice) return { status: "skipped", detail: "Browser voice will be used (no ELEVENLABS_API_KEY)" };
      avatar = await getAvatar(id);
      const voiceId = await eleven.cloneVoice({
        name: `Avatar Studio - ${avatar.name}`,
        audioFile,
        description: `Consented voice clone for ${avatar.name} (avatar ${id})`,
      });
      await saveAvatar({ ...avatar, voiceId });
      return { detail: "Instant voice clone created" };
    });

    await step(id, "persona", async () => {
      avatar = await getAvatar(id);
      const persona = features.claude
        ? await claude.extractPersona({ name: avatar.name, transcript: avatar.transcript, notes: avatar.notes })
        : demo.demoPersona(avatar.name);
      const systemPrompt = claude.buildSystemPrompt({ name: avatar.name, persona, transcript: avatar.transcript, notes: avatar.notes });
      await saveAvatar({ ...avatar, persona, systemPrompt });
      return features.claude
        ? { detail: persona.traits.slice(0, 4).join(", ") }
        : { status: "skipped", detail: "Demo persona (no ANTHROPIC_API_KEY)" };
    });

    await step(id, "video", async () => {
      if (!features.video) return { status: "skipped", detail: "Animated portrait will be used (no DID_API_KEY)" };
      const portraitUrl = await did.uploadPortrait(portraitFile);
      await saveAvatar({ ...(await getAvatar(id)), didPortraitUrl: portraitUrl });
      return { detail: "Portrait ready for lip-sync" };
    });

    await saveAvatar({ ...(await getAvatar(id)), status: "ready" });
  } catch (err) {
    console.error(`[pipeline ${id}]`, err);
    await saveAvatar({ ...(await getAvatar(id)), status: "error", error: err.message });
  }
}
