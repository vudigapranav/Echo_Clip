# Echo Clip — development and verification record

This file is the authoritative record of what Echo Clip is, what was built, what was actually verified (and how),
and what is still open. README.md is for the person using the app; AGENTS.md is the short code map.

Last full update: 2026-11-16 (Claude, audit-and-repair pass after the earlier Qwen3-Coder-Next pass).

---

## 1. Status at a glance

**Overall: ready for limited real-world testing.** The core workflow (link or upload → transcribe → rank → hook
check → vertical render → review → download) works end to end and is covered by an automated end-to-end test.
Two things still need you: a real Featherless connection test with your key (credits), and a YouTube re-test once
YouTube stops returning its bot check to this machine (see §12).

| Area | State |
|---|---|
| Rendering without libass (the original failure) | Fixed and verified on the real video and in tests |
| Six real clips from https://youtu.be/E07OTlhncP0 | Recovered from the saved source and candidates, all six rendered, no AI re-spend |
| Featherless provider | Implemented, settings in the web app, key in the macOS Keychain; real-key test pending (§12) |
| Ranking (one AI request per video, validated, cached) + local fallback | Implemented and tested with mocked AI |
| Viral Hook Detector | Rewritten, wired into the pipeline and the editor, tested |
| Facecam detection + Smart auto layouts | Rewritten, verified on the real video, tested |
| Rebranding / promotional content | Done in the app, server messages, docs; upstream attribution kept |
| Direct local upload (drag and drop, FFprobe validation, resume) | Implemented and demonstrated through the page (§17) |
| English / Hindi / Hinglish, caption languages, headlines, music | Implemented; transcription, rendering and mixing verified locally; real-AI Hindi ranking and translation quality not yet tested (§18) |
| Tests | 18 suites, ~670 checks, all passing (§11, §18) |

---

## 2. Original architecture (PeakCut)

FastAPI server (`studio/server.py`), one-file frontend (`web/index.html`), job pipeline with a fair queue
(`studio/jobs.py`), yt-dlp ingestion (`studio/media.py`), local Parakeet transcription via sherpa-onnx
(`studio/transcribe.py`), AI clip picking per 15-minute window (`studio/brain.py`), ffmpeg rendering with ASS
captions through ffmpeg's `ass` filter (`studio/render.py`), SQLite usage records, per-user data folders,
optional accounts, creator workspaces, live recording and social posting.

## 3. Review of the previous Qwen3-Coder-Next work

Verified against the code, not the earlier report.

| Item | Category | Finding | Action |
|---|---|---|---|
| Python venv + requirements | A/B | venv fine; requirements pinned `opencv-python-headless==4.10.0.84` but 5.0.0.93 was installed | Pinned to the tested 5.0.0.93 |
| `load.py` macOS CPU/RAM monitor | A | Correct (macOS `host_statistics`) | Kept |
| `captions.py` (Pillow captions, presets) | B | Good engine, but **never called** by the renderer | Kept, extended (system-font fallback for scripts the bundled fonts lack), wired into rendering |
| `render.py` | **D (broken)** | Still used ffmpeg's `ass` filter (the cause of "No option name near 'clip04.ass…'"); referenced undefined `_filter` and `_detect_facecam`; dead `_render_with_captions` that would have written one PNG per frame with a wrong `Track` call | Render path rewritten (§7); ASS builder kept only for reference/tests |
| `face.py` | **D** | `detect_faces_in_video` only ever looked at frame 0 (`frame_count < max_frames` with `sample_interval=30`); not used by anything | Rewritten (§6) |
| `highlights.py` | **C/D** | Not wired into the pipeline; candidate windows not on sentence boundaries; the audio-spike check compared raw negative dB with `> 1.8` (never true); bypassed usage tracking; schema required a field it never defined | Rewritten (§5) |
| `hook_detector.py` | **C** | Keyword-only, weights not as specified, not wired in | Replaced by `highlights.hook()`; file removed (backed up) |
| Featherless provider | C | One row in `PROVIDERS` with example model `gpt-4o-mini` (not a Featherless model) and a key URL that does not resolve | Full integration (§4) |
| Rebranding | C | Main page renamed; server messages, docs and LICENSE handling incomplete; LICENSE **replaced** the original copyright line (MIT requires keeping it); `REPO_URL` default pointed at a made-up `your-username/echo-clip` | Finished; `LICENSE` names the Echo Clip owner, PeakCut's MIT notice kept in `THIRD_PARTY_NOTICES.md` (as MIT requires); buttons off by default |
| "All 286 tests pass" | B | Re-run: the 14 original suites pass (374 checks counted today) | New suites added (§11) |
| Qwen.md claims "end-to-end working" | D | No real render had succeeded | Superseded by this record |

## 4. Featherless and the AI layer (`studio/ai.py`, `studio/brain.py`)

- Provider `featherless`: `https://api.featherless.ai/v1`, OpenAI-compatible Chat Completions. Listed first.
- **Model:** `Qwen/Qwen3-Next-80B-A3B-Instruct`. Verified in the live Featherless catalogue on 2026-11-16: model
  class `qwen3next-80b`, 32 768 context, status active, $0.125 per 1M input tokens and $1.15 per 1M output tokens.
  ("qwen3next-80b" is the class name; the model id is the one above.) Nothing is substituted silently: an unknown
  model is reported as such. Your saved model is currently `Qwen/Qwen3-Coder-30B-A3B-Instruct` (it worked in your
  first run); switch to the recommended one in Settings → AI if you want.
- Settings (all from the web app, Settings → AI): provider, key (save/replace/remove, show/hide while typing),
  base URL, model (Find models with prices, Check model, manual entry), temperature (default 0.3), max answer
  tokens (4096), timeout (120 s), mode (AI / local only), prices per 1M tokens, monthly budget, optional fallback
  service with "Allow paid fallback" (off by default).
- **Keys:** stored in the macOS Keychain (service "Echo Clip AI key (main, <data-folder hash>)"), written through
  `security -i` on standard input so the key is never on a command line, and read back and compared before the
  file copy is removed. Your existing key was migrated this way at the first start (verified: `ai.json` now holds
  only `key_in: keychain`, and the app reads the key back). Elsewhere: `data/ai.json`, mode 0600.
  The key is never returned to the browser (`public()` gives only `has_key` and the last 4 characters), never
  logged, and error texts have it replaced with `<key>`. `data/` is git-ignored.
- **Connection test:** free catalogue lookup (`GET /v1/models/{id}`) first, then one request capped at 16 output
  tokens. Result codes: `missing_key`, `invalid_key`, `credits` (HTTP 402 / "insufficient"), `rate_limit`,
  `invalid_model`, `network`, `ok`; saved and shown in Settings.
- `brain.ask_ai` (every AI call): refuses when local-only or over budget; retries at most twice, only on 429/5xx;
  stops at once on no-credit or refused key; uses the fallback only when allowed; records tokens per call.
- Claude API is the `anthropic` provider (needs an Anthropic API key; a Claude Pro subscription is not API credit).

## 5. Finding and ranking moments (`studio/highlights.py`)

1. **Candidates (local):** per-second interest from loudness z-score, reaction phrases, laughter, game words
   (cops, crash, chase, dead…) and bursts of speech; peaks become windows with the moment ~55% in (setup first),
   snapped to a line start and a sentence end inside the chosen length; overlaps limited to 35%; gaps filled on
   quiet videos. 20–40 where the video is long enough (a 16-minute VOD gave 29).
2. **Ranking (one request per video):** compact text per candidate (`c07 351.0-385.8 (35s) L=+1.2: [351] words…`,
   long ones keep start and end). The model returns the six scores, category, title, hook text, reason, highlight
   words, caption ideas, an opening judgement (engagement, curiosity, emotion, clarity, payoff, suggested start)
   and ending quality, for its best `max(n+2, min(2n, 16))` only. Validation: unknown ids, duplicate ids, missing or
   non-numeric scores are dropped; scores clamped 0–100; **the weighted total is computed locally**
   (25/20/20/15/10/10), never taken from the model; text cleaned of links and control characters. Cached in
   `data/ai_cache/rank-<sha256>.json` keyed by prompt + model + provider.
3. **Selection:** modes balanced / best / funny / reaction / surprise; no two clips of the same incident (overlap
   > 25%, or adjacent within 6 s with the same category); balanced caps one category to a third; with AI ranking,
   candidates under 55 are not made, and when fewer clips than asked are made the job says why.
4. **Hook check** (`hook()`): dead air before the first word, pauses, filler/weak openings, repeated words, a quiet
   start, a late main moment. Score = engagement 30 + curiosity 25 + emotion 20 + clarity 15 + payoff 10 (the
   AI's opening scores when it gave them, local ones otherwise). A later start is suggested only onto the start of
   a line, never past the moment, never past 35% of the clip, never leaving it shorter than the minimum; dropping 6+
   words or a question gives a context warning, and **a start with a warning is not applied automatically**. The
   editor offers "Use recommended start" / "Keep original start" and source previews of both openings.

## 6. Facecam detection (`studio/face.py`)

- YuNet (`studio/models/face_detection_yunet_2023mar.onnx`, 230 KB, MIT) through OpenCV 5.0 (CPU, fine on Apple
  Silicon). Frames are read with ffmpeg at 640 px wide.
- `find_facecam`: 36 frames across the whole video, all faces clustered by position; the most persistent cluster is
  the streamer; others join only if almost as persistent and right beside it (a second person on the same camera).
  Faces in the game come and go and are ignored. Result kept on the job, once per video.
- Real video check: per-clip detection first locked onto a trailer character's face; the whole-video search found
  Speed (94% of frames) and his friend (92%) in the bottom-left facecam and ignored a 36% trailer face.
- `analyse` per clip: where the action is (frame differences, with the facecam and the stream's overlay edges
  ignored) and how many cuts. Kept on the clip, so re-renders do not look again.
- Fallback: no OpenCV/model, unreadable frames or no facecam → layouts that need no face.

## 7. Layouts and rendering (`studio/compose.py`, `studio/render.py`, `studio/captions.py`)

- **Root cause of the original failure:** Homebrew's FFmpeg 9.0.2 on this Mac is built without libass, so the
  `ass` filter does not exist and every render stopped with "No option name near 'clipNN.ass:fontsdir=f'".
- **Fix:** captions are drawn by `captions.Track` with Pillow as a narrow RGBA strip (only redrawn when it
  changes) and piped to ffmpeg as raw frames, overlaid at its position; the hook box is one PNG overlaid with a
  fade. Works with any ffmpeg. No per-frame full-size images, no files per frame.
- Layouts: Smart auto (per clip), stacked facecam + gameplay, gameplay focus (4:5 crop following the action),
  facecam focus, full frame/zoomed/centre crop (original), manual (facecam area, gameplay area, split, zoom,
  order). Every crop has the panel's exact shape (no stretching), stays inside the frame, and uncovered areas get a
  blurred fill (no black bars). Optional slow push-in or punch-in zoom.
- Output: 1080×1920, 30 fps, H.264 High (libx264, CRF 18/21/25 for high/standard/draft), AAC 192k 48 kHz,
  `+faststart`, loudness normalisation (EBU R128, −14 LUFS) unless switched off. Work happens in a short temporary
  folder with an absolute source path (paths with spaces are safe), removed afterwards.
- Caption presets: Gaming Bold, Neon Purple, Classic White, Viral Highlight, Minimal Clean (+ Bounce, Hype, Clean),
  every property adjustable; previews are drawn by the same code, on a real frame of the clip.

## 8. Pipeline and stages (`studio/jobs.py`)

Phases shown to the user: Waiting → Importing → Transcribing → Analysing → Selecting highlights → Rendering →
Review ready (or the error, at the step it happened). Transcription runs block by block as before (with resume
after a restart); moments are found and ranked once after the transcript is complete; picked clips are rendered
(or left "pending" when automatic rendering is off). Re-rendering reuses the ranking and the AI-tidied caption
words (only words that come into a lengthened clip are tidied, and only when the AI is allowed): **style, font,
layout or a failed render never cost AI credits** (verified in the end-to-end test and on the real job: AI call
count 8 before and after re-rendering all six clips). Moments that were not picked can be made into clips later
without AI. Clips can be rejected/restored.

## 9. Ingestion

- YouTube/Twitch/Kick links (only those sites; `site_allowed` refuses other hosts, look-alike hosts and non-HTTP
  schemes — verified), preview of title/thumbnail/duration, audio first, then video; long videos fetch only the
  clips' parts. Uploads: mp4/mov/mkv/webm/m4v/ts/flv, sent in pieces, probed (video track, audio track, ≤ 4K),
  source kept unchanged.
- Echo Clip does not bypass DRM, sign-ins, paywalls or bot checks. When YouTube refuses the machine, links are
  paused for 10 minutes with a plain message.

## 10. Storage

`data/users/u_local/jobs/<id>/` holds `source.*`, `words.json`, `loudness.json`, `job.json`, `clips/`. (The
`data/projects/<id>/{source,audio,…}` layout from the brief was not adopted: moving the existing structure would
risk the existing data for no functional gain; the mapping is in README.md.) Settings → Storage: per-project
sizes, remove a source video but keep its clips, delete a project, clean leftovers (unfinished uploads, old ZIPs,
`.part` downloads; never projects, sources or clips).

## 11. Tests (run `.venv/bin/python tests/run.py`)

| Suite | Checks | Result |
|---|---|---|
| 14 original suites (permissions, abuse, queue, deleting, uploads, AI services, no sign-in, Hindi/Urdu, connecting, updates, limits, stuck clips, fresh installs, storage) | 372–374 (the queue-under-load suite prints 27–29 checks depending on timing) | pass |
| `tests/test_pipeline.py`: every caption preset; caption validation; strip timing; fallback fonts; every layout on 5 frame shapes (no stretching, inside the frame, no bars, valid ffmpeg filter); facecam clustering; candidates (count, lengths, overlap, sentence ends); ranking with a mocked AI (one request, made-up ids and broken scores dropped, local totals, cache, no-credit fallback); selection and "fewer clips" note; hook check; local-only, budget, fallback opt-in, key never shown, connection-test codes, Remove key | 162 | pass |
| `tests/test_e2e.py`: synthetic 1280×720 video with real speech (`say`) in a path with spaces → chunked upload → local transcription → candidates → local ranking → hook → 3 renders checked for 1080×1920 H.264/AAC 48 kHz, exact length, audio in step with the source (±40 ms, by cross-correlation), clean decode, non-black first frame; captions-off re-render proves captions are drawn; no AI calls on re-render; reject; ZIP and single download; make a clip from an unpicked moment; caption preview on a real frame; temp folders cleaned; restart keeps everything; source untouched | 31 | pass |
| `tests/test_uploads.py` (§17) | 40 | pass |
| `tests/test_multilingual.py` (§18) | 64 | pass |
| **Total** | **~670** | **all pass** (last run 2026-11-18: 18 of 18 suites) |

Real-video verification (outside the test suite): the six saved clips of the iShowSpeed GTA VI trailer reaction
were re-rendered through the app's own route with Smart auto: all 1080×1920 H.264 High + AAC 48 kHz stereo, lengths
equal to their timestamps (17.2, 34.8, 16.3, 41.2, 18.3, 15.7 s), full decode without errors, no black frames,
facecam stacked over the trailer with hook and captions visible. No Featherless credit was used.

## 12. Known limitations and what needs you

1. **Featherless real-key test:** not run, to avoid spending your credits without your OK. Settings → AI → Test
   connection costs a few tokens (a fraction of a cent).
2. **YouTube:** at the time of testing YouTube answered this machine with "Sign in to confirm you're not a bot",
   so a fresh YouTube import could not be re-tested today. Your first run (same code path) did download the video
   and its transcript. Echo Clip will not work around the check; the page now offers "Upload a local copy" (§17).
3. The gameplay crop follows where things move; it cannot know which part of the game matters. Manual layout is
   there for those clips. The stream's own chat overlay can end up in the gameplay crop.
4. Local ranking (no AI) is a heuristic from loudness and words; the AI ranking is much better at "is this funny".
5. Speaker IDs and per-word confidence are not available from the speech model.
6. The 16-character caption-group limit and pop animation are tuned for English; very long words shrink.

## 13. Start Echo Clip

```bash
cd ~/"Desktop/Projects/Money Printer GTA 6/Echo Clip" && .venv/bin/python -m studio.server
```

Open http://127.0.0.1:8765/app. Classic studio (creators, live, posting): http://127.0.0.1:8765/classic.
Generated clips: `data/users/u_local/jobs/<project>/clips/`.

## 14. First use

1. Settings → AI: Featherless AI, paste the key, model `Qwen/Qwen3-Next-80B-A3B-Instruct` (Use this), Save and
   test. Optionally set prices (filled from the catalogue) and a monthly budget.
2. Home: paste the stream link (or upload), choose 5–10 clips, 30–45 s, a caption style, Generate.
3. Watch the stages; when it says Review ready, open a clip to check the hook, timestamps, layout and captions;
   Save and re-render (free); reject what you do not want.
4. Download a clip, the selected ones, or all.

## 15. Files changed in this pass

Created: `studio/compose.py`, `tests/test_pipeline.py`, `tests/test_e2e.py`, `THIRD_PARTY_NOTICES.md`.
Rewritten: `studio/face.py`, `studio/highlights.py`, `studio/ai.py`, the render path of `studio/render.py`, `README.md`,
this file. Modified: `studio/jobs.py` (options, phases, ranking pass, scene cache, re-render reuse, reject,
candidates), `studio/server.py` (routes: classic, reject, make-from-candidate, drop-source, caption preview,
source preview, storage projects/cleanup, AI check-model/test, Keychain migration), `studio/brain.py` (ask_ai
guard/retries/fallback), `studio/captions.py` (fallback fonts), `studio/updates.py` (off unless configured),
`studio/config.py` (no promo links), user-facing "PeakCut" → "Echo Clip" across `studio/*.py`, `LICENSE`
(the Echo Clip owner's copyright; PeakCut's notice moved to `THIRD_PARTY_NOTICES.md`), `AGENTS.md`, `.env.example`, `DEPLOY.md`, `requirements.txt`, `tests/run.py`,
`tests/test_updates.py` (names a repository now that there is no default), every `tests/test_*.py` (Keychain off),
`web/index.html` (§16), `web/classic.html` (promo buttons removed). Deleted: `studio/hook_detector.py`
(superseded; backed up). Frontend: see §16.

## 16. Frontend (`web/index.html`, served at /app)

Rebuilt on the original single-page app (no build step), with the dark Echo Clip theme ported from
`classic.html`:

- **Removed:** Star on GitHub, Follow, Instagram feedback, Plans, the update banner and its "git pull" dialog, and
  every posting action (Echo Clip never publishes; posting lives only in the Classic studio at /classic).
- **New project:** link or upload, number of clips (5–10 or custom), length (15–20 / 20–30 / 30–45 recommended /
  custom), caption style with live previews, customise captions; more options: layout, ranking focus, trim slow
  openings, even out volume, render automatically, zoom, quality, part of the video.
- **Progress:** Waiting → Importing → Transcribing → Analysing → Selecting highlights → Rendering → Review ready,
  with the error shown at the failing step; afterwards "Ranked by AI" or "Ranked on this Mac", tokens used, and
  why fewer clips were made.
- **Review:** clip cards (cover, number, length, start–end, title, category, score, caption style, layout used,
  render status, rejected), play, editor, download, reject/restore, render/retry; download selected / download all
  (skips rejected); "More moments" with Make clip.
- **Editor:** video, start/end with nudges, hook and title, opening check with Use recommended / Keep original and
  4-second previews of both openings from the source, score bars, caption controls with a live preview on a real
  frame in the chosen layout, layout incl. manual (boxes drawn over a frame), zoom, volume, quality, "Save and
  re-render" (sends only what changed; notes that it uses no AI credits).
- **Settings:** AI (§4), Storage (projects with sizes, remove source, delete, clean leftovers), About (licences,
  credits, fair use, not-affiliated note).

Verified in the in-app browser against the real data (2026-11-16): the composer, the six real clip cards, the
editor with its real-frame caption preview, Settings → AI showing the real key stored in the Keychain (only its last four characters are ever shown), the free
"Check model" lookup against the live Featherless catalogue (found; 32,768 context; $0.125/$1.15 per 1M filled in;
nothing saved; AI call count unchanged at 8), Storage and About; no console errors; the main script passes
`node --check`. A subagent also exercised reject/restore, error display on re-render and Save and test (with a
fake key, on a copy of the data). Not clicked through in the browser: Make clip (covered by the end-to-end API
test), and the confirm-guarded "remove source" / "delete project" actions (not run on your real project).
The six clips from the first run were ranked by the old pipeline, so they have no opening check or score bars in
the editor; new projects have both.

Fixed after the UI review: "Remove key" was refused by the backend ("Paste the key for this service"); removing a
key now works and leaves the AI not set up.

Incident, resolved: running `tests/test_local.py` on its own (outside `tests/run.py`) once wrote a fake test key
into the login Keychain under a throwaway service name. That item was deleted (your real item is untouched and
still works), and every test file now switches the Keychain off itself.

## 17. Direct local upload (2026-11-16)

**What changed.** The home page offers two equal ways in: paste a link, or upload a video. The upload area sits
under the link box with **Choose video file**, drag and drop, the accepted formats, the size limit (none by
default) and the free disk space. A chosen file shows its name, size, and (where the browser can preview it)
resolution and length, with **Change** and **×**.

**Formats:** MP4, MOV, MKV, WebM, AVI, M4V (`.ts` and `.flv` are still accepted as before). The extension is only a
first filter; `media.inspect()` runs FFprobe on the uploaded file and decodes one frame. It refuses, with the
reason: unreadable or damaged files ("Invalid data", missing `moov`, broken EBML), no video stream (sound only;
cover art is not a video), no sound track, no readable length, bigger than 4K, longer than the configured limit,
and a picture that will not decode.

**Workflow.** Page: type/extension, empty file, size limit, free-space check against the status's free disk;
quick look in the browser (`<video>` metadata). Server `POST /api/upload/start`: name, size, options, and room on
the drive (file size + `PEAKCUT_MIN_FREE_DISK_GB`, 3 GB) or HTTP 507 with the numbers. Pieces of 40 MB
(`PEAKCUT_UPLOAD_PART_MB`), two at a time, each retried up to 3 times; progress shown in MB. `GET /api/upload/{id}`
says which pieces arrived, so **Resume upload** sends only the missing ones (kept 6 hours). `finish` joins the
pieces in place (no doubling on disk), runs the inspection, and only then creates the job: a bad file leaves no
job and no file behind (HTTP 422 with the reason). The good file becomes `jobs/<id>/source.<ext>` unchanged, the
job gets its size and length straight away, and the response carries the details (shown in a toast). Files up
to 40 MB go in one request through the same checks.

**Same pipeline.** An upload is an ordinary job with `source.type = "file"`: the same `jobs._run_job` (audio →
local transcription → candidates → ranking → hook check → render) as a link. Verified in `tests/test_uploads.py`
by running a "link" job (download replaced by a copy of the file) and an upload of the same video in-process:
identical candidates and identical clips.

**Link failures.** A link whose metadata or audio cannot be downloaded fails the job with
"Unable to download this video. You can upload an authorized local copy instead." plus the reason, marks it
`upload_instead`, keeps the link, and is not retried (verified: one attempt). The job card and a refused link in
the composer both show **Upload a local copy**, which opens the file picker on a page with the upload box.

**Also fixed on the way:** with no size limit, a huge file was refused with "The limit is 10000000 MB" (the
internal "no limit" value leaked into the message); the free-disk check now decides. Renders now work inside
`data/tmp/render/` and leftovers from a render cut off by closing Echo Clip are cleared at the next start (they
used to stay in the system temp folder). `tests/test_upload_parts.py` uploaded random bytes as a video; it now
uploads a real (padded) video, since uploads are checked.

**Tests (`tests/test_uploads.py`, 40 checks, own server, synthetic videos):** each of MP4, MOV, MKV, WebM (VP9/Opus),
AVI (MPEG-4/PCM), M4V accepted with streams read; random bytes, a cut-off MOV, text renamed `.mov`, a video with no
sound and sound with no video all refused with the right reason and nothing left behind; non-video name and empty
file refused before sending; a file bigger than the free space refused up front; a 1080p file in 5 MB pieces cut
off (2 pieces missing), state reported, resumed, joined byte for byte, pieces removed; cancel; unknown upload; an
uploaded MOV rendered to a 1080×1920 H.264/AAC clip; link and upload giving identical clips; the YouTube refusal
marked `upload_instead` and tried once. Full suite after the change: 17 of 17 suites pass.

**Demonstrated through the page (2026-11-16, a throwaway copy of Echo Clip with its own data folder and no AI
key):** a 2:30, 1280×720 cut of the GTA VI trailer reaction recording was dropped onto the upload area (the box
highlighted; the row showed 22 MB · 1280×720 · 2:30). Generate clips with the defaults showed "Uploading 49% · 10 MB
of 22 MB", then "Checking the video…", then the toast "1280×720, 2:30, h264/aac". The job ran to Review ready with
3 clips (and the note explaining why 3 of the 5 asked for: a 2½-minute video holds 3 separate 30–45 s moments):
1080×1920 H.264 + AAC 48 kHz, 40.5 s, 39.8 s and 36.2 s, decoding with no errors, facecam stacked over the
gameplay, Gaming Bold captions and hook. In the same page: a text file named `.mov` was refused after upload with
the reason; the YouTube link showed the upload message and the **Upload a local copy** button; and an upload with
piece 3 failing showed **Resume upload**, which then sent only that piece and finished. Drag and drop was driven
by a scripted `drop` event carrying a real file (the in-app browser cannot drag from Finder).

**Limits:** the browser can only preview MP4/MOV/WebM-type files before uploading; MKV/AVI details appear after
Echo Clip has checked them. Uploading needs disk room for the file plus 3 GB. Your Mac's drive had about 15 GB
free during testing.

## 18. Multilingual understanding, caption languages, headlines and music (2026-11-18)

### What was found first
- The speech model (NVIDIA Parakeet TDT 0.6B v2) is **English-only**. On Hindi speech it produced Roman-letter
  nonsense ("Arab Hai, Yikya Batwashe…" for "अरे भाई, ये क्या बकवास है"). Hindi could not be understood at all.
- The caption step (`brain.english_captions` + `roman.words` in `jobs.render_one`) turned any Hindi into Roman
  letters or English before drawing it: the original Hindi was never shown.
- Pillow on this Mac had **no complex-script shaping** (`raqm` off: FriBiDi missing). Devanagari drawn without it is
  broken (क्या with a loose virama, ि after its consonant, क्षत्रिय falling apart). Verified with an image.
- Ranking prompts, local reaction words, fillers and the hook check were English-only.

### Transcription (local) — `studio/whisper_asr.py`, `studio/transcribe.py`
- Added **Whisper large-v3-turbo** through **faster-whisper 1.2.1 / CTranslate2 4.9** (1.5 GB, fetched once into
  `data/models/whisper/`). Parakeet stays for English (faster, exact timings): two engines, each for what it does
  best, chosen per video. On Apple Silicon Whisper runs on the GPU (Metal/MPS, ~1.6× faster than the CPU path, which
  is the automatic fallback); about 2 GB of memory. faster-whisper's own file decoder breaks with the PyAV it
  installs, so audio is passed as samples (it already is in memory).
- Measured with the Mac's own voices (Lekha hi-IN, Samantha en-US, Rishi en-IN): Hindi detected at 0.97 and written
  in Devanagari (72% word match to the script over a 60 s monologue, mostly where the synthetic voice slurs words);
  English 100%; Hinglish written exactly as mixed when transcribed with language "hi":
  "भाई, this is actually the craziest thing I have ever seen. मतलब seriously, पुलिस वाले ने मेरी कार को उरा दिया."
- **Language detection** (`whisper_asr.detect`): up to five 30 s windows with the most sound, spread over the video,
  averaged. Calibrated: English (American and Indian voices) gives Hindi a share of 0.000; Hinglish 0.04–0.29. So:
  English only when Hindi/Urdu stays under 0.02 on average and under 0.1 in every window; Hindi when the Hindi family
  leads; Hinglish when English leads with a real Hindi share; anything else in its own language. Override:
  "language" option (auto / en / hi / hinglish / any code). YouTube's own transcript language is used when present.
- Stored per project: `words.json` (original words, timings, Whisper's per-word confidence `p`), `lang`,
  `lang_info` (probabilities, engine, override/YouTube, notes). **Speaker identification is not available** (neither
  engine separates speakers).
- Limitation: Whisper sometimes writes English words of Hinglish in Devanagari ("दिस इस इंसेन") — seen in one of two
  synthetic Hinglish samples, partly because the Hindi voice mispronounced Roman text.

### Ranking — `studio/highlights.py`
- The single ranking request now says the spoken language and, for Hindi/Hinglish, tells the model to judge jokes,
  sarcasm, roasts, slang, punchlines and timing in that language, **not translating first and not by English
  keywords**; Devanagari excerpts are sent as they are. "Shouting or swearing alone is not entertaining."
- Weighted score unchanged (25/20/20/15/10/10). New **penalties** the model names and Echo Clip subtracts:
  incomplete joke 12, missing punchline 15, incomplete conversation 10, excessive silence 8, repetitive 10, missing
  context 10, weak opening 6, no payoff 15 (`scores.weighted − scores.penalty = total`).
- Same request also returns **headlines in English, Hindi (Devanagari) and Hinglish (Roman)**.
- Local signals gained Hindi/Hinglish reaction words, laughter (हा हा), game words (पुलिस, गाड़ी…), Hindi fillers
  (तो, मतलब, अच्छा…) and weak starts (दोस्तों, आज हम…); the hook check keeps Devanagari letters (it used to drop
  them) and ends sentences at the danda (।).

### Caption languages — `studio/subtitles.py`, `studio/captions.py`
- Modes: original / hi / en / bilingual (order Hindi-first or English-first) / roman. Spoken words keep word-by-word
  highlighting; translations are whole phrases (≤7 words, split at pauses and sentence ends) shown for exactly the
  time their spoken phrase took — no word timings are invented for translated words.
- Translations: one AI request for all picked clips at job time when the chosen mode needs it, otherwise when a
  clip is switched to such a mode; kept in `translations.json` per project (keyed by text and target), reused by
  every later render; "translate again" only on request. Prompt: keep meaning, joke, sarcasm, slang, emotion;
  names, usernames and game titles unchanged; no additions, no censoring. Without an AI the spoken words are shown
  and the clip carries a plain `caption_note`.
- English transcripts are still tidied by the AI once (numbers, names); **Hindi/Hinglish words are never rewritten**.
- Drawing: each word gets its own font (Devanagari → Noto Sans Devanagari variable at the preset's weight; Latin →
  the preset's font; other scripts → a system font). Words are never split, so grapheme clusters stay whole.
  `PhraseTrack` draws translated and bilingual captions (main line + smaller second line, wrapped to ≤2 rows, shrunk
  to fit). All 8 presets work with Hindi, English, Hinglish and bilingual.
- Shaping: `studio/shaping.py` restarts the server once with Homebrew's library folder on the search path so
  Pillow's raqm finds FriBiDi (`brew install fribidi`, done on this Mac); captions refuse to draw Devanagari without
  shaping (plain error naming the fix) rather than drawing it wrong. Verified visually: क्या, रिएक्शन, क्षत्रिय,
  प्रतिष्ठित, कृष्ण correct.

### Headlines — `studio/headline.py`, `captions.headline_image`, `render._headline_filter`
- Language independent of captions: same as the video / English / Hindi / Hinglish; manual text wins; without an AI
  the headline is words actually said. Looks: white box, bold outline, neon purple, yellow box. Animation: pop,
  fade, scale, static (ffmpeg scale+pad per frame, alpha fades). Duration 3 s by default (0 = whole clip). Position
  auto keeps it at the layout's safe spot (below a stacked facecam) and moves it off the captions if they would meet.
  Up to three balanced lines, emoji in colour (Apple Color Emoji, drawn at its native size and scaled).
- Projects made before this change keep their title box for the whole clip and no music on re-render.

### Music — `studio/music.py`
- Library in `data/music/`: five built-in tracks composed by Echo Clip's own code at first start (original, free
  to use; ~3 s to make): Arcade Rush (energetic), Goofy Bounce (funny), Night Tension (suspense), Lo-fi Drift
  (chill), Big Moment (dramatic). Uploads: MP3/WAV/M4A/AAC/FLAC/OGG, checked with FFprobe, owner must confirm the
  right to use it and say where it comes from. Categories: energetic, funny, suspense, cinematic, chill, dramatic,
  action. Auto pick by moment category, overridable per category in Settings → Music.
- Mix (ffmpeg): original at 100% (adjustable), music 12% (0–60%), looped/trimmed to the clip, fade in 1 s / out
  1.5 s, ducked by a sidechain compressor keyed on the clip's own sound, mixed without normalising the inputs,
  loudness-normalised (unless off) and limited below clipping. Measured: music −11.3 dB while someone talks, voice
  34 dB above the music, no clipping. `GET …/audio-preview` plays the first 20 s of the mix before rendering.

### Tests (`tests/test_multilingual.py`, 64 checks, real speech + real local transcription, stand-in AI)
English, Hindi and Hinglish videos made from the Mac's voices (Hindi script with a joke, sarcasm and a roast; the
Hinglish one has two speakers), uploaded through the API, transcribed for real, ranked through the real AI request
path against a local OpenAI-format stand-in (so the prompts are checked, nothing is spent). Verified: automatic
detection en / hi / hinglish; Devanagari transcript, key words heard, timings ordered, confidence kept; Hinglish
keeps both scripts (81 Devanagari / 73 Latin words); one ranking request per video; the Hindi prompt carries
Devanagari and the Hindi instructions; made-up ids dropped; penalty arithmetic; three-language headlines; **five
renders** — English video with English captions; Hindi video with Hindi captions; the same Hindi clip with English
captions (exactly one translation request, ranking not repeated, each English line on its Hindi phrase's time);
bilingual Hindi-on-top (translation reused, no new request); Hinglish with captions as spoken — all 1080×1920 H.264 +
AAC, right length, clean decode, sound within 40 ms of the source; headline on screen then gone after 3 s; captions
drawn; music audible in pauses, −11.3 dB under speech, voice 34 dB clearer, no clipping; a hand-written Hindi
headline rendered with no AI request. Contact sheet inspected by eye: shaping, bilingual layout and headlines
correct. Full suite on 2026-11-18: 18 of 18 suites pass (~670 checks).

### Not verified / limitations
- **No real AI call was made on Hindi** (to spend no credits): whether Featherless's model actually finds Hindi
  jokes funny, and the quality of its Hindi↔English translations and Hindi headlines, are untested. The mechanics
  (what is sent, validated, cached, placed) are tested with the stand-in. One real ranking of a Hindi video
  costs a fraction of a cent.
- Test speech is synthetic (system voices), not real creators; real Hindi streams with background game audio,
  shouting and slang will be harder than the 72% measured here.
- Speaker identification is not available.
- `cv2` and PyAV both bundle libavdevice; macOS prints a duplicate-class warning at start. Neither uses camera
  capture, and no problem was seen in the tests.
- Hindi captions need FriBiDi (Homebrew) on any other Mac.
