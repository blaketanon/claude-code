// Self-hosted LLM over the OpenAI-compatible Chat Completions API.
// Works with Ollama, vLLM, llama.cpp's llama-server, LM Studio, LocalAI, TGI, etc.
import * as z from "zod/v4";
import { llm } from "../config.js";

// Local models usually run with 8k-32k context, so the transcript is capped (configurable).
const PROMPT_TRANSCRIPT_CHARS = llm.maxTranscriptChars;
const EXTRACT_TRANSCRIPT_CHARS = llm.maxTranscriptChars * 2;

const str = (fallback = "") => z.string().catch(fallback);
const list = () => z.array(z.string()).catch([]);

// Lenient: small models sometimes drop or mistype a field, so every field has a fallback.
export const PersonaSchema = z.object({
  summary: str("").describe("2-3 sentence description of who this person seems to be"),
  traits: list().describe("5-8 personality traits evidenced in the transcript"),
  speakingStyle: str("").describe("How they talk: pacing, sentence length, formality, energy, filler words"),
  catchphrases: list().describe("Phrases or verbal tics they actually repeat; empty if none"),
  vocabulary: str("").describe("Notable word choices, jargon, slang, regionalisms"),
  topics: list().describe("Subjects they talk about or clearly care about"),
  values: list().describe("Beliefs, priorities or opinions they expressed"),
  humor: str("not evident").describe("Their sense of humour, or 'not evident'"),
  knownFacts: list().describe("Biographical facts they stated about themselves (job, places, people, hobbies)"),
  exampleLines: list().describe("6-12 short verbatim quotes that best capture their voice"),
  greeting: str("").describe("How they would open a casual conversation, written in their voice"),
});

const PERSONA_JSON_SCHEMA = z.toJSONSchema(PersonaSchema, { io: "input" });

function headers() {
  return {
    "content-type": "application/json",
    ...(llm.apiKey ? { authorization: `Bearer ${llm.apiKey}` } : {}),
  };
}

async function post(body, signal) {
  const res = await fetch(`${llm.baseUrl}/chat/completions`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ model: llm.model, ...body }),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    const err = new Error(`LLM request failed (${res.status}): ${text.slice(0, 400)}`);
    err.status = res.status;
    throw err;
  }
  return res;
}

/** Reachability check used to decide whether to fall back to demo mode. */
export async function ping() {
  const res = await fetch(`${llm.baseUrl}/models`, { headers: headers(), signal: AbortSignal.timeout(3000) });
  return res.ok;
}

// Reasoning models (Qwen3, DeepSeek-R1, ...) may emit <think>...</think> in the content.
const stripThink = (s) => s.replace(/<think>[\s\S]*?(<\/think>|$)/g, "").trim();

function parseJsonLoose(text) {
  const cleaned = stripThink(text).replace(/^```(?:json)?\s*|\s*```$/g, "");
  try {
    return JSON.parse(cleaned);
  } catch {
    const start = cleaned.indexOf("{");
    const end = cleaned.lastIndexOf("}");
    if (start >= 0 && end > start) return JSON.parse(cleaned.slice(start, end + 1));
    throw new Error("The LLM did not return JSON");
  }
}

const EXTRACT_PROMPT = `You are building a conversational "digital twin" of a real person who has consented to it.
Below is an automatic transcript of a video of them speaking. Analyse ONLY what is evidenced in the transcript -
do not invent biography. Where the transcript is thin, say so in the relevant field rather than guessing.
If the transcript contains several speakers, profile the one who speaks most.
Reply with a single JSON object with exactly these keys: ${Object.keys(PersonaSchema.shape).join(", ")}.`;

export async function extractPersona({ name, transcript, notes }) {
  const messages = [
    { role: "system", content: EXTRACT_PROMPT },
    {
      role: "user",
      content:
        `Person's name: ${name}\n` +
        (notes ? `\nAdditional notes the person wrote about themselves:\n<notes>\n${notes}\n</notes>\n` : "") +
        `\n<transcript>\n${transcript.slice(0, EXTRACT_TRANSCRIPT_CHARS)}\n</transcript>`,
    },
  ];
  // Prefer schema-constrained decoding; servers without json_schema support get plain JSON mode.
  const formats = [
    { type: "json_schema", json_schema: { name: "persona", schema: PERSONA_JSON_SCHEMA } },
    { type: "json_object" },
    undefined,
  ];
  let lastErr;
  for (const response_format of formats) {
    try {
      const res = await post({ messages, temperature: 0.3, stream: false, ...(response_format && { response_format }) });
      const json = await res.json();
      const persona = PersonaSchema.parse(parseJsonLoose(json.choices?.[0]?.message?.content ?? ""));
      if (!persona.summary && !persona.traits.length) throw new Error("The LLM returned an empty persona");
      return persona;
    } catch (err) {
      lastErr = err;
      // Only retry with a looser format for "unsupported parameter" style errors or bad JSON.
      if (err.status && err.status !== 400 && err.status !== 422) throw err;
    }
  }
  throw lastErr;
}

function bullet(items) {
  return items?.length ? items.map((x) => `- ${x}`).join("\n") : "- (none evident)";
}

/** Stable per-avatar system prompt, built once from the persona + transcript. */
export function buildSystemPrompt({ name, persona, transcript, notes }) {
  return `You are an AI avatar of ${name}, created with ${name}'s consent from a video of them speaking.
Talk as ${name} would: first person, in their voice, with their personality. You are having a live, casual conversation.

# Who ${name} is
${persona.summary}

Traits:
${bullet(persona.traits)}

Values and opinions:
${bullet(persona.values)}

Things ${name} has said about themselves:
${bullet(persona.knownFacts)}

Topics they care about:
${bullet(persona.topics)}

# How ${name} talks
${persona.speakingStyle}
Vocabulary: ${persona.vocabulary}
Humour: ${persona.humor}
Catchphrases / verbal habits (use sparingly and naturally, never every message):
${bullet(persona.catchphrases)}

Representative lines in their own words:
${bullet(persona.exampleLines.map((l) => `"${l}"`))}
${notes ? `\n# Notes ${name} wrote about themselves\n${notes}\n` : ""}
# Ground rules
- Stay in character. Match their rhythm and register rather than sounding like a generic assistant: no bullet lists, headings or markdown unless they'd plausibly write that way.
- Only the facts above and in the transcript are known about ${name}'s life. If asked about something not covered, respond the way they plausibly would (an opinion, a guess flagged as a guess, or "I'd have to think about that") - never invent specific private facts such as addresses, finances, health details, or named relationships.
- If someone sincerely asks whether they are talking to the real ${name} or to an AI, say plainly that you are an AI avatar of ${name}. Never claim to be the real person in a way meant to deceive, and don't agree to act on ${name}'s behalf (sign things, make commitments, authorise payments, share credentials).
- Keep replies conversational in length - usually 1-4 sentences - unless asked to go deeper.

# Source transcript (verbatim, may contain transcription errors)
<transcript>
${transcript.slice(0, PROMPT_TRANSCRIPT_CHARS)}
</transcript>`;
}

const MODE_NOTES = {
  chat: null,
  voice:
    "This reply will be spoken aloud by a voice clone and lip-synced on video. Write only words that are natural to say out loud: no emoji, no markdown, no lists, no stage directions. Keep it to 1-3 sentences.",
};

/** Normalise client-held history into a valid alternating user/assistant list ending on a user turn. */
export function sanitizeHistory(history, maxTurns = 30) {
  const clean = [];
  for (const m of Array.isArray(history) ? history : []) {
    if (!m || (m.role !== "user" && m.role !== "assistant")) continue;
    const text = typeof m.content === "string" ? m.content.trim().slice(0, 8000) : "";
    if (!text) continue;
    const last = clean[clean.length - 1];
    if (last && last.role === m.role) last.content += `\n\n${text}`;
    else clean.push({ role: m.role, content: text });
  }
  while (clean.length && clean[0].role !== "user") clean.shift();
  const trimmed = clean.slice(-maxTurns);
  while (trimmed.length && trimmed[0].role !== "user") trimmed.shift();
  if (!trimmed.length || trimmed[trimmed.length - 1].role !== "user") {
    throw new Error("Conversation must end with a user message");
  }
  return trimmed;
}

/** Hides <think>...</think> spans from a token stream, even when tags are split across chunks. */
function thinkFilter(emit) {
  let buf = "";
  let inThink = false;
  return (chunk) => {
    buf += chunk;
    for (;;) {
      if (inThink) {
        const end = buf.indexOf("</think>");
        if (end < 0) { buf = buf.slice(-7); return; }
        buf = buf.slice(end + 8).replace(/^\s+/, "");
        inThink = false;
      } else {
        const start = buf.indexOf("<think>");
        if (start >= 0) {
          if (start) emit(buf.slice(0, start));
          buf = buf.slice(start + 7);
          inThink = true;
          continue;
        }
        // Hold back a possible partial "<think" at the end of the buffer.
        const lt = buf.lastIndexOf("<");
        const safe = lt >= 0 && "<think>".startsWith(buf.slice(lt)) ? lt : buf.length;
        if (safe) emit(buf.slice(0, safe));
        buf = buf.slice(safe);
        return;
      }
    }
  };
}

/** Streams a reply. Calls onText(delta) as text arrives and resolves with the full reply. */
export async function streamReply({ systemPrompt, history, mode = "chat", onText, signal }) {
  const note = MODE_NOTES[mode];
  // The mode note goes at the END of the system prompt so the long persona + transcript
  // prefix stays identical across modes and the server's prefix/KV cache keeps hitting.
  // (Some chat templates reject system messages after the first turn, so no mid-chat system message.)
  const messages = [
    { role: "system", content: note ? `${systemPrompt}\n\n# For this reply\n${note}` : systemPrompt },
    ...sanitizeHistory(history),
  ];

  const res = await post({ messages, stream: true, temperature: llm.temperature, max_tokens: 1024 }, signal);

  let full = "";
  const push = thinkFilter((t) => {
    if (!full && !t.trim()) return;
    full += t;
    onText?.(t);
  });

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line.startsWith("data:")) continue;
      const data = line.slice(5).trim();
      if (data === "[DONE]") return full.trim();
      const evt = JSON.parse(data);
      if (evt.error) throw new Error(`LLM stream error: ${evt.error.message || JSON.stringify(evt.error)}`);
      // Only `content` is shown; `reasoning_content` (vLLM/llama.cpp reasoning parsers) is ignored.
      const delta = evt.choices?.[0]?.delta?.content;
      if (delta) push(delta);
    }
  }
  return full.trim();
}
