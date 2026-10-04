// HTTP API in demo mode (no LLM or voice backends), including the full pipeline on a tiny video.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "avatar-studio-test-"));
const port = 3900 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const auth = { authorization: "Basic " + Buffer.from("me:test-password").toString("base64") };
let proc;

const video = path.join(tmp, "clip.mp4");

before(async () => {
  execFileSync("ffmpeg", [
    "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-f", "lavfi",
    "-i", "sine=frequency=220", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video,
  ]);
  proc = spawn(process.execPath, ["server/index.js"], {
    cwd: root,
    env: { ...process.env, DEMO_MODE: "1", PORT: String(port), DATA_DIR: path.join(tmp, "data"), APP_PASSWORD: "test-password" },
    stdio: "ignore",
  });
  for (let i = 0; i < 50; i++) {
    try {
      await fetch(`${base}/api/config`);
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 100));
    }
  }
  throw new Error("server did not start");
});

after(() => {
  proc?.kill();
  fs.rmSync(tmp, { recursive: true, force: true });
});

function upload(fields) {
  const form = new FormData();
  for (const [k, v] of Object.entries(fields)) form.append(k, v);
  form.append("video", new Blob([fs.readFileSync(video)], { type: "video/mp4" }), "clip.mp4");
  return fetch(`${base}/api/avatars`, { method: "POST", headers: auth, body: form });
}

test("password gate", async () => {
  assert.equal((await fetch(`${base}/api/config`)).status, 401);
  const wrong = { authorization: "Basic " + Buffer.from("me:nope").toString("base64") };
  assert.equal((await fetch(`${base}/api/config`, { headers: wrong })).status, 401);
  assert.equal((await fetch(`${base}/api/config`, { headers: auth })).status, 200);
});

test("creating an avatar requires consent", async () => {
  const res = await upload({ name: "Ada", consent: "false" });
  assert.equal(res.status, 400);
  assert.match((await res.json()).error, /Consent/);
});

test("pipeline runs end to end and chat streams a reply", async () => {
  const res = await upload({ name: "Ada", consent: "true" });
  assert.equal(res.status, 201);
  const { id } = await res.json();

  let avatar;
  for (let i = 0; i < 100; i++) {
    avatar = await (await fetch(`${base}/api/avatars/${id}`, { headers: auth })).json();
    if (avatar.status === "ready" || avatar.status === "error") break;
    await new Promise((r) => setTimeout(r, 200));
  }
  assert.equal(avatar.status, "ready", avatar.error);
  assert.equal(avatar.systemPrompt, undefined, "server-only fields must not leak");
  assert.ok(avatar.steps.every((s) => s.status === "done" || s.status === "skipped"));

  const portrait = await fetch(`${base}/media/${id}/portrait.jpg`, { headers: auth });
  assert.equal(portrait.headers.get("content-type"), "image/jpeg");
  assert.equal((await fetch(`${base}/media/${id}/..%2Favatar.json`, { headers: auth })).status, 404);

  const chat = await fetch(`${base}/api/avatars/${id}/chat`, {
    method: "POST",
    headers: { ...auth, "content-type": "application/json" },
    body: JSON.stringify({ history: [{ role: "user", content: "hello" }] }),
  });
  const events = (await chat.text()).trim().split("\n\n").map((l) => JSON.parse(l.replace(/^data: /, "")));
  assert.equal(events.at(-1).type, "done");
  assert.match(events.at(-1).text, /hello/);

  const bad = await fetch(`${base}/api/avatars/${id}/chat`, {
    method: "POST",
    headers: { ...auth, "content-type": "application/json" },
    body: JSON.stringify({ history: [] }),
  });
  assert.equal(bad.status, 400);

  assert.equal((await fetch(`${base}/api/avatars/${id}`, { method: "DELETE", headers: auth })).status, 200);
  assert.equal((await fetch(`${base}/api/avatars/${id}`, { headers: auth })).status, 404);
});
