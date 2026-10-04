# Avatar Studio

Upload a video of a person talking → get an AI avatar of them that you can **text-chat** with and **video-call**, speaking in their cloned voice with their personality. The LLM and voice stack are **self-hosted**.

> **Consent first.** Only create avatars of yourself or of people who have explicitly agreed. The app requires a consent attestation per avatar (stored with a timestamp), labels the call stage as an AI-generated avatar, tells the model to admit it's an AI when sincerely asked and never to act on the person's behalf, and **Delete** removes the voice reference from the voice server as well as all local media.

## How it works

```
video ──ffmpeg──► voice track ──► Whisper (faster-whisper) ──► transcript ──► your LLM ──► personality profile
   │                   └────────► Chatterbox voice reference (15s of clean speech)                  │
   └──ffmpeg──► portrait frame ──► (optional) D-ID                                                  ▼
                                                                     system prompt = profile + verbatim transcript

chat:        you type ──► LLM (streams, in character) ──► text (▶ to hear it in their voice)
video call:  hold-to-talk mic ──► Whisper ──► LLM ──► Chatterbox TTS (cloned voice) ──► lip-sync video or animated portrait
```

| Piece | Default (self-hosted) | Swappable for | License |
|---|---|---|---|
| LLM | [Ollama](https://ollama.com) + `llama3.1:8b` | Anything with an OpenAI-compatible `/v1/chat/completions`: vLLM, llama.cpp `llama-server`, LM Studio, LocalAI, TGI | model-dependent |
| Speech-to-text | `voice-server/` → [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Any OpenAI-compatible `/v1/audio/transcriptions` (set `STT_BASE_URL`), e.g. speaches | MIT |
| Voice cloning + TTS | `voice-server/` → [Chatterbox](https://github.com/resemble-ai/chatterbox) (zero-shot cloning from a reference clip) | Anything implementing the voice-server API below | MIT |
| Lip-sync video | D-ID (hosted, optional) | — | — |

"Training" here means a zero-shot voice clone plus an LLM-written personality profile, not fine-tuning weights. A few minutes of footage is too little to fine-tune on, while a profile plus the person's verbatim words gives a convincing likeness in about a minute.

If a backend is unreachable, the app falls back rather than failing: echo replies for the LLM; placeholder transcript, browser speech synthesis and browser dictation for voice; an audio-reactive portrait for video. Backends are re-checked every 30s, so start order doesn't matter (use **Retrain** on avatars built while something was down).

## Run it

### Option A: Docker Compose (everything)

Requires Docker; an NVIDIA GPU + NVIDIA Container Toolkit is strongly recommended (remove the `deploy:` blocks in `docker-compose.yml` to run on CPU, which is slow).

```bash
cd avatar-studio
docker compose up -d --build
docker compose exec ollama ollama pull llama3.1:8b
open http://localhost:3000
```

The voice server downloads the Whisper and Chatterbox weights (~3 GB) on first start; `docker compose logs -f voice` shows progress.

### Option B: Run each piece yourself

Requires Node 20+, Python 3.11, and `ffmpeg` on PATH.

```bash
# 1. LLM
ollama serve &                          # or vLLM / llama-server / LM Studio ...
ollama pull llama3.1:8b
# Ollama's default context is small; give it room for the persona + transcript:
#   OLLAMA_CONTEXT_LENGTH=16384 ollama serve

# 2. Voice server
cd avatar-studio/voice-server
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --port 8000

# 3. App
cd avatar-studio
npm install
cp .env.example .env                    # point LLM_BASE_URL / LLM_MODEL / VOICE_SERVER_URL at your servers
npm start                               # http://localhost:3000
```

Then: **Create an avatar** → pick a video (2–5 min of one person talking naturally, face visible) → tick the consent box → **Build avatar**. When it says *ready*, use the **Chat** tab or the **Video call** tab (hold the button or Space to talk, or type).

### Option C: AWS (test from your phone)

`deploy/aws/deploy.sh` launches one GPU EC2 instance (default `g5.xlarge`, about $1/hour) in the default VPC and runs the whole stack there, with Caddy in front for automatic HTTPS on a `<ip>.sslip.io` hostname. Phones only allow microphone and camera access over HTTPS. The app is password-protected (HTTP basic auth: any username, your password).

```bash
cd avatar-studio
# needs the AWS CLI configured for the target account, with EC2, S3 and SSM read access
APP_PASSWORD=pick-a-long-password AWS_REGION=us-east-1 ./deploy/aws/deploy.sh
# ... prints https://1-2-3-4.sslip.io when it's up (first boot takes ~15-25 min)
./deploy/aws/teardown.sh    # stop paying: terminates the instance, deletes the security group and bucket
```

New AWS accounts often have a quota of 0 for GPU instances ("Running On-Demand G and VT instances"). If launching fails with `VcpuLimitExceeded`, request a quota of 4+ vCPUs in Service Quotas.

### Choosing a model

Persona quality is mostly the LLM. Rough guide:
- 8 GB VRAM: `llama3.1:8b`, `qwen2.5:7b`. Fine for chat, plainer personality.
- 24 GB: `gemma2:27b`, `qwen2.5:32b` (4-bit), `mistral-small`. Noticeably better at staying in character.
- 48 GB+ or vLLM on multi-GPU: `llama3.3:70b`, `qwen2.5:72b`.

Reasoning models (Qwen3, DeepSeek-R1) work; their `<think>` output is stripped. If your server supports JSON-schema constrained output (vLLM, llama.cpp, recent Ollama) persona extraction uses it, otherwise it falls back to JSON mode and then to plain prompting.

Chatterbox needs ~4–6 GB VRAM. On CPU each sentence takes several seconds, so the video call gets laggy. Set `STT_MODEL=distil-large-v3` on the voice server for better transcripts on a GPU.

## Voice server API

`voice-server/app.py`, FastAPI. Optional bearer auth via `VOICE_SERVER_API_KEY`.

| Method | Path | |
|---|---|---|
| `GET` | `/health` | Device and model status |
| `POST` | `/v1/audio/transcriptions` | OpenAI-compatible: multipart `file`, optional `language` → `{text}` |
| `POST` | `/voices` | multipart `file`, `name` → `{voice_id}`. Keeps up to `REF_SECONDS` (15) of speech, silences removed |
| `GET` | `/voices` | List voices |
| `DELETE` | `/voices/{voice_id}` | Delete voice + reference clip |
| `POST` | `/tts` | `{voice_id, text, format: "mp3"\|"wav"}` → audio, spoken sentence by sentence |

Tuning env vars: `DEVICE` (`auto`/`cuda`/`mps`/`cpu`), `STT_MODEL`, `TTS_EXAGGERATION` (emotion intensity, default 0.5), `TTS_CFG_WEIGHT` (pacing, default 0.5), `PRELOAD=1`.

To use a different TTS engine (XTTS, F5-TTS, Fish Speech, OpenVoice...), replace `synthesize()` in `voice-server/engines.py`; the HTTP API stays the same. Check the model weights' license: XTTS-v2 and F5-TTS weights are non-commercial.

## App API

| Method | Path | |
|---|---|---|
| `POST` | `/api/avatars` | multipart: `video`, `name`, `consent=true`, optional `portrait`, `notes`. Starts training. |
| `GET` | `/api/avatars[/:id]` | List / poll status and training steps |
| `POST` | `/api/avatars/:id/chat` | `{history:[{role,content}], mode:"chat"\|"voice"}` → server-sent events `text` … `done` |
| `POST` | `/api/avatars/:id/speak` | `{text, video?:bool}` → `{audioUrl, videoUrl}` in the cloned voice |
| `POST` | `/api/transcribe` | multipart `audio` → `{text}` |
| `POST` | `/api/avatars/:id/retrain` | Re-run the pipeline (optionally with new `notes`) |
| `DELETE` | `/api/avatars/:id` | Deletes local data and the voice-server voice |

## Code map

- `server/pipeline.js`: the training steps and their progress reporting
- `server/providers/llm.js`: OpenAI-compatible client, persona extraction, in-character streaming chat
- `server/providers/voice.js`: client for the voice server
- `server/providers/did.js`: optional lip-synced video rendering
- `server/media.js`: ffmpeg audio and portrait extraction
- `voice-server/`: self-hosted STT + voice cloning + TTS
- `public/`: single-page UI (no build step)
- `test/`, `voice-server/test_app.py`: tests (`npm test`; `pytest` in `voice-server/`). Model engines are faked, so neither needs a GPU or downloads.

## Limits and next steps

- **Lip-sync is still hosted (D-ID).** To make video self-hosted too, add a lip-sync engine such as MuseTalk, SadTalker or LivePortrait behind a `/lipsync` endpoint and swap `did.js`. Without D-ID you get the audio-reactive portrait.
- Call replies are voiced sentence by sentence as the LLM streams them, so the avatar starts talking after the first sentence. With D-ID each sentence is also a separate video render, which adds a few seconds per sentence.
- Data is stored on local disk and there are no user accounts, only the shared `APP_PASSWORD`. Set it on any shared deploy, and keep the voice server off the public internet (or set `VOICE_SERVER_API_KEY`).
