import { test } from "node:test";
import assert from "node:assert/strict";
import { sentenceSplitter } from "../public/lib/sentences.js";
import { sanitizeHistory, thinkFilter, parseJsonLoose, buildSystemPrompt } from "../server/providers/llm.js";

const split = (chunks) => {
  const out = [];
  const sp = sentenceSplitter((s) => out.push(s));
  chunks.forEach((c) => sp.push(c));
  sp.flush();
  return out;
};

test("sentenceSplitter emits complete sentences as they stream, merging short ones", () => {
  const text = "Oh! Hey there, it is really good to see you. I have been thinking about jazz all day. Bye";
  const chunks = text.match(/.{1,3}/gs);
  assert.deepEqual(split(chunks), [
    "Oh! Hey there, it is really good to see you.",
    "I have been thinking about jazz all day.",
    "Bye",
  ]);
  assert.deepEqual(split(["Short."]), ["Short."]);
  assert.deepEqual(split([""]), []);
});

test("thinkFilter hides reasoning even when tags are split across chunks", () => {
  let out = "";
  const push = thinkFilter((t) => (out += t));
  ["<thi", "nk>secret</th", "ink>\n\nHello", " a < b", " there"].forEach(push);
  assert.equal(out, "Hello a < b there");
});

test("parseJsonLoose tolerates fences, think blocks and surrounding prose", () => {
  assert.deepEqual(parseJsonLoose('<think>hmm</think>```json\n{"a":1}\n```'), { a: 1 });
  assert.deepEqual(parseJsonLoose('Sure! Here it is: {"a":{"b":2}} hope that helps'), { a: { b: 2 } });
  assert.throws(() => parseJsonLoose("no json here"));
});

test("sanitizeHistory makes a valid alternating conversation ending on the user", () => {
  const h = sanitizeHistory([
    { role: "assistant", content: "greeting" },
    { role: "user", content: "a" },
    { role: "user", content: "b" },
    { role: "system", content: "ignore me" },
    { role: "assistant", content: "  " },
    { role: "assistant", content: "reply" },
    { role: "user", content: "c" },
  ]);
  assert.deepEqual(h, [
    { role: "user", content: "a\n\nb" },
    { role: "assistant", content: "reply" },
    { role: "user", content: "c" },
  ]);
  assert.throws(() => sanitizeHistory([]));
  assert.throws(() => sanitizeHistory([{ role: "user", content: "x" }, { role: "assistant", content: "y" }]));
});

test("buildSystemPrompt carries the persona, transcript and safety rules", () => {
  const prompt = buildSystemPrompt({
    name: "Ada",
    persona: {
      summary: "S", traits: ["kind"], speakingStyle: "fast", catchphrases: [], vocabulary: "v",
      topics: [], values: [], humor: "dry", knownFacts: [], exampleLines: ["hi all"], greeting: "hey",
    },
    transcript: "TRANSCRIPT-BODY",
    notes: "",
  });
  assert.match(prompt, /AI avatar of Ada/);
  assert.match(prompt, /TRANSCRIPT-BODY/);
  assert.match(prompt, /say plainly that you are an AI avatar of Ada/);
  assert.match(prompt, /"hi all"/);
});
