// Claude: distils a personality profile from the transcript, then role-plays that persona in chat.
import Anthropic from "@anthropic-ai/sdk";
import * as z from "zod/v4";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import { CLAUDE_MODEL } from "../config.js";

// Created lazily so the server can start (in demo mode) without credentials.
let client;
const anthropic = () => (client ??= new Anthropic());

// Opt into server-side refusal fallbacks so a classifier decline is retried on
// Anthropic's recommended fallback model instead of failing the turn.
const FALLBACK = { betas: ["server-side-fallback-2026-07-01"], fallbacks: "default" };

// Keep the verbatim transcript in the system prompt so the model can mirror real phrasing.
const MAX_TRANSCRIPT_CHARS = 200_000;

export const PersonaSchema = z.object({
  summary: z.string().describe("2-3 sentence description of who this person seems to be"),
  traits: z.array(z.string()).describe("5-8 personality traits evidenced in the transcript"),
  speakingStyle: z.string().describe("How they talk: pacing, sentence length, formality, energy, filler words"),
  catchphrases: z.array(z.string()).describe("Phrases or verbal tics they actually repeat; empty if none"),
  vocabulary: z.string().describe("Notable word choices, jargon, slang, regionalisms"),
  topics: z.array(z.string()).describe("Subjects they talk about or clearly care about"),
  values: z.array(z.string()).describe("Beliefs, priorities or opinions they expressed"),
  humor: z.string().describe("Their sense of humour, or 'not evident'"),
  knownFacts: z.array(z.string()).describe("Biographical facts they stated about themselves (job, places, people, hobbies)"),
  exampleLines: z.array(z.string()).describe("6-12 short verbatim quotes that best capture their voice"),
  greeting: z.string().describe("How they would open a casual conversation, written in their voice"),
});

const EXTRACT_PROMPT = `You are building a conversational "digital twin" of a real person who has consented to it.
Below is an automatic transcript of a video of them speaking. Analyse ONLY what is evidenced in the transcript -
do not invent biography. Where the transcript is thin, say so in the relevant field rather than guessing.
If the transcript contains several speakers, profile the one who speaks most.`;

export async function extractPersona({ name, transcript, notes }) {
  const response = await anthropic().beta.messages.parse({
    model: CLAUDE_MODEL,
    max_tokens: 16000,
    output_config: { effort: "high", format: betaZodOutputFormat(PersonaSchema) },
    ...FALLBACK,
    messages: [
      {
        role: "user",
        content:
          `${EXTRACT_PROMPT}\n\nPerson's name: ${name}\n` +
          (notes ? `\nAdditional notes the person wrote about themselves:\n<notes>\n${notes}\n</notes>\n` : "") +
          `\n<transcript>\n${transcript.slice(0, MAX_TRANSCRIPT_CHARS)}\n</transcript>`,
      },
    ],
  });
  if (response.stop_reason === "refusal") {
    throw new Error(`Persona extraction was declined (${response.stop_details?.category ?? "unknown"})`);
  }
  if (!response.parsed_output) throw new Error(`Persona extraction returned no structured output (stop_reason: ${response.stop_reason})`);
  return response.parsed_output;
}

function bullet(list) {
  return list?.length ? list.map((x) => `- ${x}`).join("\n") : "- (none evident)";
}

/** Stable per-avatar system prompt (cached), built once from the persona + transcript. */
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
${transcript.slice(0, MAX_TRANSCRIPT_CHARS)}
</transcript>`;
}

const MODE_NOTES = {
  chat: null,
  voice:
    "This reply will be spoken aloud by a voice clone and lip-synced on video. Write only words that are natural to say out loud: no emoji, no markdown, no lists, no stage directions. Keep it to 1-3 sentences.",
};

/** Normalise client-held history into a valid alternating user/assistant list ending on a user turn. */
export function sanitizeHistory(history, maxTurns = 40) {
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

/**
 * Streams a reply. Calls onText(delta) as text arrives and resolves with the full reply.
 */
export async function streamReply({ systemPrompt, history, mode = "chat", onText, signal }) {
  const messages = sanitizeHistory(history);
  const note = MODE_NOTES[mode];
  // Mode-specific guidance goes in a trailing mid-conversation system message so the
  // large, stable system prompt (persona + transcript) stays byte-identical and cached.
  if (note) messages.push({ role: "system", content: note });

  const stream = anthropic().beta.messages.stream(
    {
      model: CLAUDE_MODEL,
      max_tokens: 16000,
      output_config: { effort: "low" }, // conversational latency matters more than deep reasoning
      ...FALLBACK,
      system: [{ type: "text", text: systemPrompt, cache_control: { type: "ephemeral" } }],
      messages,
    },
    { signal },
  );

  stream.on("text", (delta) => onText?.(delta));
  const final = await stream.finalMessage();

  if (final.stop_reason === "refusal") {
    const err = new Error("The model declined to answer that.");
    err.code = "refusal";
    throw err;
  }
  return final.content
    .filter((b) => b.type === "text")
    .map((b) => b.text)
    .join("")
    .trim();
}
