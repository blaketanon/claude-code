import "dotenv/config";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const trimSlash = (u) => u.replace(/\/+$/, "");

export const ROOT = path.resolve(here, "..");
export const DATA_DIR = path.resolve(process.env.DATA_DIR || path.join(ROOT, "data"));
export const PORT = Number(process.env.PORT || 3000);
export const MAX_UPLOAD_MB = Number(process.env.MAX_UPLOAD_MB || 500);
export const DEMO_MODE = process.env.DEMO_MODE === "1";
// When set, every request needs HTTP basic auth (any username, this password). Set it on any public deploy.
export const APP_PASSWORD = process.env.APP_PASSWORD || "";

// Any OpenAI-compatible chat server. Default: Ollama on this machine.
export const llm = {
  baseUrl: trimSlash(process.env.LLM_BASE_URL || "http://localhost:11434/v1"),
  model: process.env.LLM_MODEL || "llama3.1:8b",
  apiKey: process.env.LLM_API_KEY || "",
  temperature: Number(process.env.LLM_TEMPERATURE || 0.8),
  maxTranscriptChars: Number(process.env.LLM_MAX_TRANSCRIPT_CHARS || 16000),
};

// The bundled voice-server (faster-whisper + Chatterbox), or anything implementing its API.
export const voice = {
  baseUrl: trimSlash(process.env.VOICE_SERVER_URL || "http://localhost:8000"),
  apiKey: process.env.VOICE_SERVER_API_KEY || "",
  // Speech-to-text can point at a different OpenAI-compatible /v1/audio/transcriptions server.
  sttBaseUrl: trimSlash(process.env.STT_BASE_URL || process.env.VOICE_SERVER_URL || "http://localhost:8000"),
  sttModel: process.env.STT_MODEL || "whisper-1",
};

export const did = { apiKey: process.env.DID_API_KEY || "" };

// Which backends are reachable. Refreshed periodically by index.js so you can start
// Ollama / the voice server after the app and it picks them up.
export const features = {
  llm: false,
  voice: false,
  video: !DEMO_MODE && Boolean(did.apiKey),
};
