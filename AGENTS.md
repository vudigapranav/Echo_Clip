# Working on Echo Clip

A short map for contributors and AI coding assistants. Echo Clip is an AI-powered gaming streamer clipping studio
that runs on your own Mac.

## Documentation

- **Qwen.md** is the authoritative development and verification record: what was built, what was checked and
  how, test results, known limitations and what is left. Update it when you change behaviour.
- **README.md** is for the person using Echo Clip. **AGENTS.md** (this file) is the short map of the code.
- Echo Clip is built on PeakCut (MIT). PeakCut's copyright notice lives in THIRD_PARTY_NOTICES.md and must stay there.

## Run and test

- Run: `.venv/bin/python -m studio.server` (settings from `.env`; see `.env.example`). Needs Python 3.10+ and `ffmpeg`.
- The speech model (about 480 MB) downloads by itself on first start; `.venv/bin/python -m studio.fetch_model` gets it ahead of time.
- Tests: `.venv/bin/python tests/run.py` (includes `tests/test_pipeline.py` and the end-to-end `tests/test_e2e.py`). It starts a throwaway copy on a spare port with an empty data folder. Run it
  before and after any change to `studio/`. No keys needed.
- To try a risky change by hand without touching real data:
  `PEAKCUT_DATA=<temp folder> PEAKCUT_PORT=8766 .venv/bin/python -m studio.server`.

## Where things are

| File | What it does |
|---|---|
| `studio/server.py` | Every web route, and the gate that runs before each request (who is asking, limits, headers) |
| `studio/config.py` | Every setting, read from environment variables |
| `studio/ai.py` | The only code that talks to an AI service, and the AI settings saved from the dashboard (`ai.json` in the data folder) |
| `studio/brain.py` | `ask_ai` (every AI request goes through it: local-only, budget, retries, fallback), live judging, caption fixes |
| `studio/highlights.py` | Local candidate moments, the single ranking request (with the hook judged in it), the hook check, choosing clips |
| `studio/face.py` | YuNet facecam search across the whole video, and where the action is in each clip |
| `studio/compose.py` | 16:9 to 9:16 layouts (Smart auto, stacked, gameplay, facecam, manual...) as ffmpeg filters |
| `studio/captions.py` | Caption presets and drawing (Pillow): word-by-word strips, the hook box, previews |
| `studio/usage.py` | The record of AI calls and tokens shown on the AI usage page |
| `studio/jobs.py` | The video pipeline and the fair queue |
| `studio/live.py` | Live recording and clipping |
| `studio/transcribe.py`, `studio/asr_worker.py` | Speech to text, here or on a second machine |
| `studio/render.py` | One clip: layout filter + caption strips piped into ffmpeg (no libass needed), cover image |
| `studio/media.py` | Downloading (yt-dlp), probing, audio |
| `studio/auth.py`, `studio/account.py`, `studio/admin.py` | Sign-in, deleting things, the admin dashboard |
| `studio/db.py` | Database tables (SQLAlchemy core; SQLite or PostgreSQL) |
| `web/index.html` | The Echo Clip app (/app), one file, no build step |
| `web/classic.html` | The classic studio (/classic): creator workspaces, live recording, posting |

## Rules worth knowing

- **Sign-in is off by default** (`config.ACCOUNTS`): every request then belongs to one built-in owner
  (`auth.local_user()`). With `PEAKCUT_ACCOUNTS=1` it is a normal multi-user site. Both paths are tested.
- **Who is asking** always comes from the session (`ctx.uid()`), never from the request body. Background threads
  set it themselves with `ctx.as_user(...)`.
- **Anything that changes what a user sees must call `ctx.bump()`**. The page polls `/api/sync`, which answers
  from memory when that user's counter has not moved.
- **Text from videos is untrusted**: it goes into prompts through `brain._safe` and out through `brain._text`.
- **Adding an AI provider**: add a row to `PROVIDERS` in `studio/ai.py`. If it speaks the OpenAI chat format that
  is all; otherwise add one function that returns `(parsed JSON, tokens in, tokens out)`.
- **Clips are rendered in a short temporary folder** and moved into place (`render.render_clip`), so paths with
  spaces never reach ffmpeg's filter syntax. Captions are drawn by `captions.py`, never by ffmpeg's subtitle filter
  (Homebrew's ffmpeg has no libass). `render.build_ass` is kept only for reference and its tests.
- **The AI is asked once per video** (`highlights.rank`), and the answer is cached by content hash in
  `data/ai_cache/`. Re-renders, style or layout changes must never call it again; `jobs._fixed_words` reuses the
  tidied caption words. Only text goes to the AI: never audio, video or frames.
- **Keys**: saved from the dashboard into the macOS Keychain (`ai._kc_*`), else `ai.json` (0600). Tests set
  `PEAKCUT_KEYCHAIN=0` so they never touch the real Keychain.
- **Secrets** are read from the environment or from files named by `*_FILE` settings. Never write a key, a real
  address or a personal path into a tracked file.
- **Pages** are plain HTML, CSS and JavaScript. Keep the existing palette, no shadows, and hover states that
  change colour only.
- Comments and messages are written in plain language for people who do not program. Keep that tone.
