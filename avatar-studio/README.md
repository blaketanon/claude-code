# Avatar Studio

Upload a video of a person talking → get an AI avatar of them that you can **text-chat** with and **video-call**, speaking in their cloned voice with their personality.

> **Consent first.** Only create avatars of yourself or of people who have explicitly agreed. The app requires a consent attestation per avatar (stored with a timestamp), labels the call stage as an AI-generated avatar, tells the model to admit it's an AI when sincerely asked and never to act on the person's behalf, and **Delete** removes the cloned voice from ElevenLabs as well as all local media.

## How it works

```
video ──ffmpeg──► voice track (mp3) ──► ElevenLabs Speech-to-Text ──► transcript ──► Claude ──► personality profile
   │                    └─────────────► ElevenLabs Instant Voice Clone ──► voice_id                      │
   └──ffmpeg──► portrait frame ──► D-ID image upload                                                     ▼
                                                                         system prompt = profile + verbatim transcript

chat:        you type ──► Claude (streams, in character) ──► text (▶ to hear it in their voice)
video call:  hold-to-talk mic ──► STT ──► Claude ──► ElevenLabs TTS (cloned voice) ──► D-ID lip-sync ──► video
```

"Training" here means building a voice clone + personality profile, not fine-tuning weights. With only a few minutes of footage there isn't enough data for a useful fine-tune; prompting a strong model with a distilled profile *and* the person's verbatim words gets you a convincing likeness, ready in about a minute.

| Stage | Provider | Without a key the app falls back to |
|---|---|---|
| Personality + conversation | Claude (`claude-opus-5-5`) | Echo replies (demo) |
| Transcription, voice clone, speech | ElevenLabs | Placeholder transcript, browser speech synthesis, browser dictation |
| Lip-synced video | D-ID Talks API | Portrait that pulses with the audio |

So you can run the whole flow with zero keys to try the UI, then add keys one at a time.

## Run it

Requires Node 20+ and `ffmpeg`/`ffprobe` on your PATH.

```bash
cd avatar-studio
npm install
cp .env.example .env     # add whichever keys you have
npm start                # http://localhost:3000
```

Then: **Create an avatar** → pick a video (2–5 min of one person talking naturally, face visible) → tick the consent box → **Build avatar**. Watch the training steps on the left; when it says *ready*, use the **Chat** tab or the **Video call** tab (hold the button or Space to talk, or type).

Tips for a better avatar:
- More natural, unscripted speech = better personality. Interviews, vlogs and storytelling beat a read script.
- Use **About this person** for facts and opinions the video doesn't cover. Hit **Retrain** after editing.
- Upload a separate **portrait** if the auto-picked frame is poor; it should be a front-facing, well-lit head-and-shoulders shot.

## API

| Method | Path | |
|---|---|---|
| `POST` | `/api/avatars` | multipart: `video`, `name`, `consent=true`, optional `portrait`, `notes`. Starts training. |
| `GET` | `/api/avatars[/:id]` | List / poll status and training steps |
| `POST` | `/api/avatars/:id/chat` | `{history:[{role,content}], mode:"chat"\|"voice"}` → server-sent events `text` … `done` |
| `POST` | `/api/avatars/:id/speak` | `{text, video?:bool}` → `{audioUrl, videoUrl}` in the cloned voice |
| `POST` | `/api/transcribe` | multipart `audio` → `{text}` |
| `POST` | `/api/avatars/:id/retrain` | Re-run the pipeline (optionally with new `notes`) |
| `DELETE` | `/api/avatars/:id` | Deletes local data and the ElevenLabs voice |

## Code map

- `server/pipeline.js` – the training steps and their progress reporting
- `server/providers/claude.js` – persona extraction (structured output) and in-character streaming chat
- `server/providers/elevenlabs.js` – STT, voice cloning, TTS
- `server/providers/did.js` – lip-synced video rendering
- `server/media.js` – ffmpeg audio/portrait extraction
- `public/` – single-page UI (no build step)

## Limits and next steps

- Each video-call reply waits for D-ID to render (typically several seconds). For real-time streaming, swap `did.js` for D-ID's or HeyGen's WebRTC streaming avatar APIs, and stream TTS sentence-by-sentence.
- Data is stored on local disk with no user accounts; add auth before deploying anywhere shared.
- Instant voice clones are good from ~1 minute of clean audio; ElevenLabs' Professional Voice Cloning (30+ min) is noticeably closer.
