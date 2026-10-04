import "dotenv/config";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

export const ROOT = path.resolve(here, "..");
export const DATA_DIR = path.resolve(process.env.DATA_DIR || path.join(ROOT, "data"));
export const PORT = Number(process.env.PORT || 3000);

export const CLAUDE_MODEL = process.env.CLAUDE_MODEL || "claude-opus-5-5";

export const keys = {
  anthropic: process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN || "",
  elevenlabs: process.env.ELEVENLABS_API_KEY || "",
  did: process.env.DID_API_KEY || "",
};

export const DEMO_MODE = process.env.DEMO_MODE === "1";

export const features = {
  claude: !DEMO_MODE && Boolean(keys.anthropic),
  voice: !DEMO_MODE && Boolean(keys.elevenlabs),
  video: !DEMO_MODE && Boolean(keys.did),
};

export const MAX_UPLOAD_MB = Number(process.env.MAX_UPLOAD_MB || 500);
