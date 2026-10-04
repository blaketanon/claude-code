import express from "express";
import multer from "multer";
import path from "node:path";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import { PORT, ROOT, DATA_DIR, MAX_UPLOAD_MB, DEMO_MODE, APP_PASSWORD, features, llm as llmConfig, voice as voiceConfig } from "./config.js";
import * as store from "./store.js";
import { runPipeline, initialSteps } from "./pipeline.js";
import * as llm from "./providers/llm.js";
import * as voice from "./providers/voice.js";
import * as did from "./providers/did.js";
import * as demo from "./providers/demo.js";
import { extractPortrait } from "./media.js";

const CONSENT_STATEMENT =
  "I am the person in this video, or I have their explicit permission to create an AI avatar, voice clone and personality model of them.";

const app = express();
app.set("trust proxy", "loopback, uniquelocal"); // behind Caddy in the AWS deploy

if (APP_PASSWORD) {
  const expected = crypto.createHash("sha256").update(APP_PASSWORD).digest();
  app.use((req, res, next) => {
    const [scheme, encoded] = (req.headers.authorization || "").split(" ");
    const password = scheme === "Basic" ? Buffer.from(encoded || "", "base64").toString().split(":").slice(1).join(":") : "";
    const given = crypto.createHash("sha256").update(password).digest();
    if (crypto.timingSafeEqual(given, expected)) return next();
    res.set("WWW-Authenticate", 'Basic realm="Avatar Studio", charset="UTF-8"').status(401).send("Password required");
  });
}

app.use(express.json({ limit: "2mb" }));
app.use(express.static(path.join(ROOT, "public")));

const upload = multer({
  dest: path.join(DATA_DIR, "uploads"),
  limits: { fileSize: MAX_UPLOAD_MB * 1024 * 1024 },
});

const wrap = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

async function loadReady(req, res) {
  const avatar = await store.getAvatar(req.params.id);
  if (!avatar) {
    res.status(404).json({ error: "Avatar not found" });
    return null;
  }
  if (avatar.status !== "ready") {
    res.status(409).json({ error: `Avatar is ${avatar.status}` });
    return null;
  }
  return avatar;
}

app.get("/api/config", (_req, res) => {
  res.json({ features, consentStatement: CONSENT_STATEMENT, maxUploadMb: MAX_UPLOAD_MB });
});

app.get("/api/avatars", wrap(async (_req, res) => {
  res.json((await store.listAvatars()).map(store.publicAvatar));
}));

app.get("/api/avatars/:id", wrap(async (req, res) => {
  const avatar = await store.getAvatar(req.params.id);
  if (!avatar) return res.status(404).json({ error: "Avatar not found" });
  res.json(store.publicAvatar(avatar));
}));

// Create an avatar from a video (+ optional portrait override). Training runs in the background.
app.post(
  "/api/avatars",
  upload.fields([{ name: "video", maxCount: 1 }, { name: "portrait", maxCount: 1 }]),
  wrap(async (req, res) => {
    const videoFile = req.files?.video?.[0];
    const portraitFile = req.files?.portrait?.[0];
    const cleanup = () =>
      Promise.all([videoFile, portraitFile].filter(Boolean).map((f) => fs.rm(f.path, { force: true })));

    const name = String(req.body.name || "").trim().slice(0, 80);
    const notes = String(req.body.notes || "").trim().slice(0, 4000);
    if (req.body.consent !== "true") {
      await cleanup();
      return res.status(400).json({ error: "Consent is required: only create avatars of yourself or people who have explicitly agreed." });
    }
    if (!name) {
      await cleanup();
      return res.status(400).json({ error: "Name is required" });
    }
    const looksLikeMedia = (f) =>
      /^(video|audio)\//.test(f.mimetype) || /\.(mp4|mov|m4v|webm|mkv|avi|mp3|m4a|wav)$/i.test(f.originalname);
    if (!videoFile || !looksLikeMedia(videoFile)) {
      await cleanup();
      return res.status(400).json({ error: "Upload a video file of the person talking" });
    }
    if (portraitFile && !/^image\//.test(portraitFile.mimetype)) {
      await cleanup();
      return res.status(400).json({ error: "Portrait must be an image" });
    }

    const id = store.newId();
    const dir = await store.ensureAvatarDir(id);
    const ext = path.extname(videoFile.originalname).toLowerCase().replace(/[^.a-z0-9]/g, "") || ".mp4";
    const sourceFile = `source${ext}`;
    await fs.rename(videoFile.path, path.join(dir, sourceFile));
    if (portraitFile) {
      // Normalise any image format to the square JPEG the pipeline expects.
      await extractPortrait(portraitFile.path, path.join(dir, "media", "portrait.jpg"), { isImage: true }).finally(() =>
        fs.rm(portraitFile.path, { force: true }),
      );
    }

    const now = new Date().toISOString();
    const avatar = await store.saveAvatar({
      id,
      name,
      notes,
      sourceFile,
      status: "queued",
      steps: initialSteps(),
      consent: { statement: CONSENT_STATEMENT, acceptedAt: now, ip: req.ip },
      createdAt: now,
    });
    runPipeline(id); // fire and forget; progress is polled via GET /api/avatars/:id
    res.status(201).json(store.publicAvatar(avatar));
  }),
);

app.post("/api/avatars/:id/retrain", wrap(async (req, res) => {
  const avatar = await store.getAvatar(req.params.id);
  if (!avatar) return res.status(404).json({ error: "Avatar not found" });
  if (avatar.status === "processing") return res.status(409).json({ error: "Already processing" });
  if (avatar.voiceId && features.voice) await voice.deleteVoice(avatar.voiceId).catch(() => {});
  if (typeof req.body?.notes === "string") avatar.notes = req.body.notes.trim().slice(0, 4000);
  await store.saveAvatar({ ...avatar, voiceId: null, status: "queued", steps: initialSteps() });
  runPipeline(avatar.id);
  res.json({ ok: true });
}));

app.delete("/api/avatars/:id", wrap(async (req, res) => {
  const avatar = await store.getAvatar(req.params.id);
  if (!avatar) return res.status(404).json({ error: "Avatar not found" });
  // Remove the reference clip from the voice server too, not just our local copy.
  if (avatar.voiceId && features.voice) {
    await voice.deleteVoice(avatar.voiceId).catch((err) => console.warn("voice delete failed:", err.message));
  }
  await store.deleteAvatar(avatar.id);
  res.json({ ok: true });
}));

// Streamed chat reply as server-sent events: {type:"text", text} ... {type:"done", text} | {type:"error"}.
app.post("/api/avatars/:id/chat", wrap(async (req, res) => {
  const avatar = await loadReady(req, res);
  if (!avatar) return;
  const { mode = "chat" } = req.body || {};
  let history;
  try {
    history = llm.sanitizeHistory(req.body?.history);
  } catch (err) {
    return res.status(400).json({ error: err.message });
  }

  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
  const abort = new AbortController();
  res.on("close", () => abort.abort());

  try {
    const onText = (text) => send({ type: "text", text });
    const text = features.llm
      ? await llm.streamReply({ systemPrompt: avatar.systemPrompt, history, mode, onText, signal: abort.signal })
      : await demo.demoReply({ name: avatar.name, history, onText });
    send({ type: "done", text });
  } catch (err) {
    if (!abort.signal.aborted) {
      console.error("[chat]", err);
      send({ type: "error", error: err.code === "refusal" ? err.message : `Reply failed: ${err.message}` });
    }
  }
  res.end();
}));

// Speech-to-text for the user's microphone during a video call.
app.post("/api/transcribe", upload.single("audio"), wrap(async (req, res) => {
  if (!req.file) return res.status(400).json({ error: "No audio uploaded" });
  try {
    if (!features.voice) return res.status(501).json({ error: "Voice server is not reachable; use typed input or browser dictation." });
    const text = await voice.transcribe(req.file.path, req.file.mimetype);
    res.json({ text });
  } finally {
    await fs.rm(req.file.path, { force: true });
  }
}));

// Turn reply text into the avatar's voice (and, when configured, a lip-synced video clip).
app.post("/api/avatars/:id/speak", wrap(async (req, res) => {
  const avatar = await loadReady(req, res);
  if (!avatar) return;
  const text = String(req.body?.text || "").trim().slice(0, 2000);
  const wantVideo = req.body?.video !== false;
  if (!text) return res.status(400).json({ error: "text is required" });
  if (!features.voice || !avatar.voiceId) return res.json({ audioUrl: null, videoUrl: null });

  const mediaDir = path.join(store.avatarDir(avatar.id), "media");
  const clipId = store.newId();
  const audioName = `say-${clipId}.mp3`;
  await fs.writeFile(path.join(mediaDir, audioName), await voice.speak(avatar.voiceId, text));
  const audioUrl = `/media/${avatar.id}/${audioName}`;

  let videoUrl = null;
  let videoError = null;
  if (wantVideo && features.video && avatar.didPortraitUrl) {
    try {
      const remote = await did.renderTalk({ portraitUrl: avatar.didPortraitUrl, audioFile: path.join(mediaDir, audioName) });
      const videoName = `say-${clipId}.mp4`;
      await did.download(remote, path.join(mediaDir, videoName));
      videoUrl = `/media/${avatar.id}/${videoName}`;
    } catch (err) {
      // Voice still works without the video - degrade instead of failing the turn.
      console.error("[speak video]", err);
      videoError = err.message;
    }
  }
  res.json({ audioUrl, videoUrl, videoError });
}));

// Generated media + portrait. Only files in the avatar's media/ folder are reachable.
app.get("/media/:id/:file", wrap(async (req, res) => {
  const { id, file } = req.params;
  if (!/^[a-z0-9-]+\.(jpg|mp3|mp4)$/.test(file)) return res.status(404).end();
  let dir;
  try {
    dir = path.join(store.avatarDir(id), "media");
  } catch {
    return res.status(404).end();
  }
  res.sendFile(path.join(dir, file), (err) => err && !res.headersSent && res.status(404).end());
}));

app.use((err, _req, res, _next) => {
  console.error(err);
  if (res.headersSent) return res.end();
  const status = err.code === "LIMIT_FILE_SIZE" ? 413 : 500;
  res.status(status).json({ error: err.message });
});

// Avatars left mid-training by a restart would otherwise sit at "processing" forever.
for (const a of await store.listAvatars()) {
  if (a.status === "processing" || a.status === "queued") {
    await store.saveAvatar({ ...a, status: "error", error: "Interrupted by a server restart - click Retrain." });
  }
}

// Probe the self-hosted backends; re-probe so they can be started after the app.
async function refreshFeatures() {
  const before = JSON.stringify(features);
  const up = (p) => (DEMO_MODE ? Promise.resolve(false) : p().catch(() => false));
  [features.llm, features.voice] = await Promise.all([up(llm.ping), up(voice.ping)]);
  if (JSON.stringify(features) !== before) {
    console.log(
      `  LLM (${llmConfig.model} @ ${llmConfig.baseUrl}): ${features.llm ? "on" : "DEMO"} | ` +
        `Voice (${voiceConfig.baseUrl}): ${features.voice ? "on" : "off"} | Video (D-ID): ${features.video ? "on" : "off"}`,
    );
  }
}
await refreshFeatures();
setInterval(refreshFeatures, 30_000).unref();

app.listen(PORT, () => console.log(`Avatar Studio running at http://localhost:${PORT}`));
