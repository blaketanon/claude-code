// The LLM client against an in-process fake OpenAI-compatible server.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";

const requests = [];
let mode = "ok";
const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (d) => (body += d));
  req.on("end", async () => {
    const j = JSON.parse(body || "{}");
    requests.push(j);
    if (!j.stream) {
      if (j.response_format?.type === "json_schema") {
        res.writeHead(400);
        return res.end('{"error":{"message":"json_schema unsupported"}}');
      }
      if (mode === "server-error") {
        res.writeHead(503);
        return res.end("busy");
      }
      const content = '<think>x</think>{"summary":"S","traits":["warm"],"catchphrases":"oops not a list"}';
      return res.end(JSON.stringify({ choices: [{ message: { content } }] }));
    }
    res.writeHead(200, { "content-type": "text/event-stream" });
    for (const piece of ["<think>plan</th", "ink>Hi", " there."]) {
      res.write(`data: ${JSON.stringify({ choices: [{ delta: { content: piece } }] })}\n\n`);
    }
    res.end("data: [DONE]\n\n");
  });
});

let llm;
before(async () => {
  await new Promise((r) => server.listen(0, r));
  process.env.LLM_BASE_URL = `http://127.0.0.1:${server.address().port}/v1`;
  process.env.LLM_MODEL = "test-model";
  llm = await import("../server/providers/llm.js");
});
after(() => server.close());

test("extractPersona falls back from json_schema to json_object and repairs bad fields", async () => {
  requests.length = 0;
  const persona = await llm.extractPersona({ name: "Ada", transcript: "hello", notes: "" });
  assert.deepEqual(requests.map((r) => r.response_format?.type), ["json_schema", "json_object"]);
  assert.equal(requests[0].model, "test-model");
  assert.equal(persona.summary, "S");
  assert.deepEqual(persona.catchphrases, []);
  assert.deepEqual(persona.topics, []);
});

test("extractPersona does not retry on server errors", async () => {
  mode = "server-error";
  requests.length = 0;
  await assert.rejects(llm.extractPersona({ name: "Ada", transcript: "hello", notes: "" }), /503/);
  mode = "ok";
});

test("streamReply streams visible text only and appends the voice-mode note to the system prompt", async () => {
  requests.length = 0;
  let streamed = "";
  const text = await llm.streamReply({
    systemPrompt: "SYSTEM",
    history: [{ role: "user", content: "hey" }],
    mode: "voice",
    onText: (t) => (streamed += t),
  });
  assert.equal(text, "Hi there.");
  assert.equal(streamed, "Hi there.");
  const [sys, user] = requests[0].messages;
  assert.equal(sys.role, "system");
  assert.ok(sys.content.startsWith("SYSTEM"));
  assert.match(sys.content, /spoken aloud/);
  assert.deepEqual(user, { role: "user", content: "hey" });
});
