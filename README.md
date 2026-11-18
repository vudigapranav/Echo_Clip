# Echo Clip

**AI-powered gaming streamer clipping studio.** Echo Clip turns a long gaming stream or VOD into captioned
vertical Shorts (1080 × 1920), on your own Mac. It finds the moments worth watching, ranks them, trims slow
openings, lays the streamer's facecam and the gameplay out for a vertical screen, and burns in word-by-word
captions. You review, edit and download; nothing is ever published for you.

Echo Clip is built on the open-source [PeakCut](https://github.com/haziqahmedqasmi/PeakCut) project (MIT). See
[LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## The daily workflow

Open Echo Clip → paste the link of a stream you are allowed to clip (or upload the recording) → choose how many
clips (5–10) and how long (30–45 s by default) and a caption style → **Generate** → watch the clips, fix any
timestamps or styles → **Download** one, a selection, or all of them.

## Two ways in: a link, or a video from your Mac

The home page has both, one above the other: **paste a link** (YouTube, Twitch, Kick) or **upload a video**
(drag it onto the box, or press **Choose video file**). Both go through exactly the same pipeline with the same
settings (number of clips, length, caption style, captions on/off, layout and the rest).

- **Formats:** MP4, MOV, MKV, WebM, AVI, M4V. The name is only a first filter: Echo Clip looks inside every
  uploaded file with FFprobe (it needs a video stream that really decodes, a sound track and a readable length;
  up to 4K) and refuses anything else with the reason: damaged or incomplete, sound only, no sound, not a video.
- **Before uploading** the page shows the file's name, size and (where the browser can preview the format) its
  resolution and length; MKV and AVI details are shown once Echo Clip has checked the file. **Change** swaps the
  file, **×** removes it.
- **Size and space:** no size limit by default (`PEAKCUT_MAX_UPLOAD_MB` sets one). The upload is refused up front
  if the drive does not have the file's size plus 3 GB free. The home page shows how much room there is.
- **Big files** are sent in 40 MB pieces (never loaded whole into the browser), two at a time, with progress in
  megabytes. A piece that fails is retried; if the connection drops, the row offers **Resume upload**, which sends
  only the missing pieces (an unfinished upload is kept for 6 hours).
- **Where it goes:** `data/users/u_local/jobs/<project>/source.<ext>`, unchanged, kept even if a later AI or
  rendering step fails. Nothing is uploaded anywhere else: video and audio stay on the Mac.
- **When a link can't be downloaded** (for example YouTube refusing this computer), the link stays on screen with
  "Unable to download this video. You can upload an authorized local copy instead." and an **Upload a local copy**
  button. Echo Clip does not keep retrying and does not try to get around the refusal.

## English, Hindi and Hinglish

Echo Clip listens to which language is spoken (no setting needed; you can override it under More options) and
writes it down on your Mac:

| Spoken | How it is written down | Engine |
|---|---|---|
| English | English | Parakeet (fast, exact word timings) |
| Hindi | Devanagari, as spoken | Whisper large-v3-turbo (about 1.6 GB, downloaded once) |
| Hinglish | Hindi words in Devanagari, English words in Latin letters, as the creator mixed them | Whisper |
| Other languages | in their own language | Whisper |

The transcript is never rewritten or translated: it is kept as spoken, with word timings and Whisper's confidence
per word. The AI ranks Hindi and Hinglish moments **in the language they are spoken in** (jokes, sarcasm, roasts,
punchlines), from compact text excerpts only, in the same single request as before.

**Captions** are a separate choice from the spoken language: **Original** (as spoken), **Hindi**, **English**,
**Hindi + English** (bilingual: the spoken line word by word with its translation under or over it; choose Hindi or
English on top), or **Hindi in Roman letters**. Translations are made by your AI service in one request per video,
kept in the project (`translations.json`), and shown as whole phrases timed to the speech they translate (translated
words never get made-up timings). The clip editor shows each spoken line next to its translation to compare.

Hindi text needs the FriBiDi library for correct letter shaping (क्या, क्षत्रिय): `brew install fribidi`. Echo Clip
turns shaping on by itself at start and refuses to draw Hindi without it rather than drawing it wrong.

## Headlines and music

**Headlines:** the AI writes each clip's headline in English, Hindi and Hinglish in the ranking request (4–10 words,
true to what happens, at most one emoji). Choose the language independently of captions (or "same as the video"),
edit it by hand, pick a look (white box, bold outline, neon purple, yellow box), an animation (pop, fade, scale,
none), how long it stays (3 s by default; 0 = the whole clip) and where (auto keeps clear of faces and captions).
Changing any of this never asks the AI again.

**Music:** Settings → Music holds the library: five tracks Echo Clip composes itself (original, free to use) plus
any you add (MP3, WAV, M4A, AAC, FLAC, OGG; only music you made, licensed, or that is free to use; you confirm it).
"Auto" picks a track for each clip from its kind of moment (funny → playful, chase → energetic, surprise →
suspense…). The mix keeps the original sound at full level, puts the music at 12% by default, lowers it by about
11 dB while someone talks (ducking) and brings it back in the pauses, fades it in and out, and never clips. You can
listen to the mix before rendering.

## What happens to a video

| Step | Where it runs |
|---|---|
| Import a YouTube/Twitch/Kick link (yt-dlp) or an uploaded MP4/MOV/MKV/WebM/AVI/M4V | This Mac |
| Speech to text with word timings (Parakeet through sherpa-onnx) | This Mac |
| 20–40 candidate moments from the transcript, loudness, reaction words and laughter | This Mac |
| Ranking: **one** request per video with a compact text list of the candidates, scored on entertainment 25%, reaction 20%, surprise 20%, hook 15%, context 10%, replay 10%, with the opening judged in the same request | Your AI service (text only), or this Mac when the AI is off |
| Hook check of each picked clip's first seconds (dead air, fillers, a quiet start), with a safer later start suggested | This Mac |
| Facecam search (YuNet face detection across the whole video) and the vertical layout | This Mac |
| Rendering with FFmpeg: H.264 + AAC, 30 fps, captions drawn with Pillow (no libass needed) | This Mac |

Video, audio and frames never leave the machine. Only the candidates' words go to the AI service you choose.

## Quick start (macOS)

You need Python 3.10+ and FFmpeg (`brew install ffmpeg`).

```bash
cd "Echo Clip"
```

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python -m studio.server
```

Open http://127.0.0.1:8765. The first start downloads the speech model by itself (about 480 MB, once).

## Choose your AI (Featherless by default)

Go to **Settings → AI**:

1. Service: **Featherless AI** (OpenAI-compatible, `https://api.featherless.ai/v1`).
2. Paste your API key. It is stored in the **macOS Keychain** (on other systems: `data/ai.json`, readable by you
   only). It is never sent back to the page, logged, or committed.
3. Model: recommended `Qwen/Qwen3-Next-80B-A3B-Instruct` (Featherless model class `qwen3next-80b`; 32k context).
   **Find models** lists the catalogue with prices; **Check model** looks one up for free.
4. **Save and test**. The test first looks the model up in the catalogue (free), then makes one tiny request, and
   says plainly whether the key, the credit, the rate limit, the model or the network is the problem.

Credit protection: one ranking request per video, cached on disk by content hash (re-renders, caption or layout
changes and restarts never call the AI again), an answer-length cap, limited retries (never on "no credit"), a
"Local only" switch, a monthly budget computed from real token counts and the prices you enter, and an optional
second ("fallback") service that is used only if you tick **Allow paid fallback**. With no AI, or when the credit
runs out, clips are ranked on this Mac and the app says so.

Other services still work: Gemini, OpenAI, Grok, Groq, OpenRouter, Claude API, Ollama, or any OpenAI-compatible
address. (A Claude Pro subscription is not API credit: the Claude option needs an Anthropic API key.)

## Layouts for 16:9 streams

| Layout | What it does |
|---|---|
| Smart auto (default) | Picks per clip: facecam + gameplay stacked when a facecam is found, a tall crop around a big on-camera face, gameplay focus when nobody is found |
| Facecam + gameplay | The facecam on top, the gameplay (cropped around the action, away from the facecam) below |
| Gameplay focus | A large 4:5 gameplay crop that follows where things move, over a blurred fill |
| Facecam focus | The reaction big, the game below (or a 9:16 crop around a full-frame camera) |
| Full frame / Zoomed / Centre crop | The original simple layouts |
| Manual | You choose the facecam area, the gameplay area, the split and the zoom |

Nothing is ever stretched, and there are no black bars (a blurred fill is used where the panels do not cover the
frame).

## Caption styles

Gaming Bold, Neon Purple, Classic White, Viral Highlight and Minimal Clean (plus the original Bounce, Hype and
Clean), each adjustable: font, weight, size, colour, highlight colour, outline, shadow/glow, box, position, words
at a time, pop animation, hook title on/off. The preview is drawn by the same code that renders the clips, on a
real frame of the clip.

## Where things are kept

Everything is in `data/` (or the folder chosen in **Settings → Storage**):

```
data/users/u_local/jobs/<project id>/
    source.mp4         the imported video (never changed)
    words.json         the transcript, word by word
    loudness.json      loudness per second
    job.json           settings, candidates, scores, hook checks, clip list
    clips/clipNN.mp4   the finished clips, with clipNN.jpg covers
data/ai_cache/         saved AI rankings
data/models/           the speech model
```

Projects survive restarts; an interrupted import picks up where it stopped. **Settings → Storage** shows each
project's size, can remove a source video while keeping its clips, delete a project, and clean up leftovers.

## Tests

```bash
.venv/bin/python tests/run.py
```

Starts throwaway copies of the app with empty temporary data folders and runs every suite, including an end-to-end
test that uploads a synthetic landscape video with real speech, transcribes it, ranks and renders clips, and checks
the MP4s (size, codecs, length, audio sync, captions), re-rendering, downloads and a restart, and an upload test
(every format, broken files, resumed uploads, disk space, and a link and an upload of the same video giving the
same clips). No keys are needed and
no AI credit is spent.

## Security

- Listens on `127.0.0.1` only by default; refuses changes sent by other websites.
- Keys are kept server-side (Keychain on macOS), never returned to the browser.
- Text from videos is treated as data in prompts, never as instructions.
- Only known video sites are fetched. Echo Clip does not bypass DRM, sign-ins, paywalls or any access restriction.

## Using it responsibly

Clip videos you made or have permission to use. A public video is not automatically licensed for reuse. You are
responsible for the rights to what you download, clip and post, and for following the terms of the sites you use.

The **Classic studio** (`/classic`) keeps PeakCut's creator workspaces, live recording and posting features.

## Licence

[MIT](LICENSE). Fonts and the face-detection model keep their own licences: see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Echo Clip is not affiliated with Rockstar Games, YouTube,
OpusClip, Featherless AI or any streamer.
