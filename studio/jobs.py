"""Job pipeline: get video -> audio -> transcript -> AI picks -> rendered clips. One heavy task at a time."""
import collections
import json
import os
import math
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import (ai, brain, captions, compose, config, creators, ctx, face, headline, highlights, load, media, music, render,
               roman, safe, subtitles, transcribe, usage, whisper_asr)

class _FairQueue:
    """Whose video is next. Each person has their own line and the lines take turns, so someone who adds ten
    videos does not make everyone else wait behind all ten. Small tasks (re-making one clip) go first."""

    def __init__(self):
        self.cv = threading.Condition()
        self.quick = collections.deque()
        self.lines = {}                       # user -> their waiting videos, oldest first
        self.turn = collections.deque()       # users with something waiting, in the order they will be served
        self.epoch = 0                        # goes up whenever the line moves
        self._pos = (0.0, {})
        self.running = 0                      # videos being worked on right now
        self.last_start = 0.0
        self.held = False                     # videos are waiting only because the machine is near full

    def _room(self) -> bool:
        """May one more video start? One always may. More only while the machine has room, and a few seconds
        after the last one started, so its weight is already showing in the measurements."""
        if self.running == 0:
            return True
        return self.running < config.JOB_WORKERS and time.time() - self.last_start > 6 and not load.busy()

    def done(self):
        with self.cv:
            self.running = max(0, self.running - 1)
            self.cv.notify_all()

    def put(self, item, user=None):
        with self.cv:
            if user is None:
                self.quick.append(item)
            else:
                if user not in self.lines:
                    self.lines[user] = collections.deque()
                    self.turn.append(user)
                self.lines[user].append(item)
            self.epoch += 1
            self.cv.notify()

    def get(self, quick_only=False):
        with self.cv:
            while True:
                if self.quick:
                    self.epoch += 1
                    return self.quick.popleft()
                if not quick_only and self.turn:
                    if self._room():
                        break
                    if not self.held:
                        self.held = True
                        self.epoch += 1
                elif self.held:
                    self.held = False
                    self.epoch += 1
                self.cv.wait(1.0)             # looked at again every second: the line moves the moment there is room
            self.epoch += 1
            self.held = False
            self.running += 1
            self.last_start = time.time()
            user = self.turn.popleft()
            item = self.lines[user].popleft()
            if self.lines[user]:
                self.turn.append(user)
            else:
                del self.lines[user]
            return item

    def ahead(self) -> dict:
        """job id -> how many videos will start before it. Worked out at most once a second."""
        with self.cv:
            if time.time() - self._pos[0] < 1:
                return self._pos[1]
            out, lines, order, n = {}, {u: list(q) for u, q in self.lines.items()}, list(self.turn), 0
            depth = 0
            while order:
                nxt = []
                for u in order:
                    if depth < len(lines[u]):
                        out[lines[u][depth][1]] = n
                        n += 1
                        if depth + 1 < len(lines[u]):
                            nxt.append(u)
                order, depth = nxt, depth + 1
            self._pos = (time.time(), out)
            return out


_q = _FairQueue()
_live_q: "queue.Queue" = queue.Queue()   # live clips never wait behind a long video
_jobs: dict = {}
_by_user: dict = {}                       # user -> {job id: job}: listing one person's videos never scans everyone's
_lock = threading.RLock()
on_clip_ready: list = []   # callbacks (job, clip), run once when a clip first finishes

DEFAULTS = {"max_clips": 5, "min_len": 30, "max_len": 45, "duration": "30-45", "style": "gaming_bold", "layout": "auto",
            "quality": 720, "range_start": None, "range_end": None, "rank_mode": "balanced", "hook_trim": True,
            "zoom": "none", "normalize": True, "render_quality": "standard", "auto_render": True, "captions": None,
            "candidates": 30, "language": "auto", "caption_lang": "original", "caption_order": "hi_first",
            "headline_lang": "source", "headline": None, "music": None}


LIMITS = {"max_clips": (1, 20), "min_len": (5, 120), "max_len": (10, 180), "quality": (360, 1080), "candidates": (10, 40)}
CHOICES = {"rank_mode": tuple(highlights.MODES), "zoom": ("none", "slow", "punch"),
           "render_quality": tuple(render.QUALITY), "duration": (*highlights.DURATIONS, "custom"),
           "language": ("auto", "hinglish", *whisper_asr.NAMES), "caption_lang": tuple(subtitles.MODES),
           "caption_order": ("hi_first", "en_first"), "headline_lang": tuple(headline.LANGS)}


def clean_options(options) -> dict:
    """Only known settings, each inside its allowed range. Raises ValueError on nonsense."""
    out = {}
    for k, v in (options if isinstance(options, dict) else {}).items():
        if k not in DEFAULTS or v in ("", None):
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            v = max(lo, min(hi, int(float(v))))
        elif k == "style":
            v = v if v in render.STYLES else DEFAULTS["style"]
        elif k == "layout":
            v = compose.mode_of(v)
        elif k in CHOICES:
            v = v if v in CHOICES[k] else DEFAULTS[k]
        elif k in ("hook_trim", "normalize", "auto_render"):
            v = bool(v) and v not in ("0", "false", "off")
        elif k == "captions":
            v = captions.clean(v)
        elif k == "headline":
            v = headline.clean(v)
        elif k == "music":
            v = music.clean(v)
        else:                                    # range_start, range_end: seconds into the video
            v = float(v)
            if not math.isfinite(v) or v < 0:
                raise ValueError("That part of the video isn't valid.")
        out[k] = v
    if out.get("duration") in highlights.DURATIONS:  # a length preset sets the shortest and longest clip
        out["min_len"], out["max_len"] = highlights.DURATIONS[out["duration"]]
    if out.get("range_end") is not None and out["range_end"] <= out.get("range_start", 0):
        raise ValueError("The end of the chosen part must be after its start.")
    return out


def job_dir(jid) -> Path:
    return Path(_jobs[jid]["dir"])


HIDDEN = ("dir", "user", "source_path", "recover")


def public(job) -> dict:
    """What the browser may see: no server paths, no owner id."""
    out = {k: v for k, v in job.items() if k not in HIDDEN}
    out["source"] = {k: v for k, v in job["source"].items() if k != "path"}
    out["clips"] = [{k: v for k, v in c.items() if k not in ("src", "fixed_words", "fixed_for", "scene_for")} for c in job["clips"]]
    out["has_source"] = bool(job.get("source_path") and Path(job["source_path"]).is_file())
    out["lang_name"] = whisper_asr.name(job.get("lang")) if job.get("lang") else ""
    ai = job.get("ai") or {}
    out["ai"] = {"tokens": ai.get("tokens", 0), "note": ai.get("note", ""), "used": bool(ai.get("model"))}   # never the model, never raw errors
    if job.get("status") == "queued":
        n = _q.ahead().get(job["id"])
        if n:
            out["stage"] = (f"High demand right now: {n} video{'s' if n != 1 else ''} ahead of yours" if _q.held else
                            f"Waiting in line: {n} video{'s' if n != 1 else ''} ahead of yours")
        elif n == 0 and _q.held:
            out["stage"] = "High demand right now: yours is next"
    return out


def save(job):
    ctx.bump(job.get("user"))
    with _lock:
        d = Path(job["dir"])
        for _ in range(5):
            try:
                text = json.dumps(job, indent=1)
                break
            except RuntimeError:  # changed mid-dump by another thread
                time.sleep(0.05)
        else:
            return
        tmp = d / "job.json.tmp"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(d / "job.json")


def _set(job, **kw):
    with _lock:
        job.update(kw)
    save(job)


def _remap(path, folder: Path):
    """Paths saved before data moved into per-user folders (or on another machine): find the file by name."""
    if path and not Path(path).exists():
        cand = folder / Path(str(path).replace("\\", "/")).name
        if cand.exists():
            return str(cand)
    return path


def _load_file(f: Path, uid: str):
    try:
        job = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    job.update(dir=str(f.parent), user=uid)
    job.setdefault("kind", "video")
    job.setdefault("creator", None)
    if job.get("source_path"):
        job["source_path"] = _remap(job["source_path"], f.parent)
    if job.get("source", {}).get("path"):
        job["source"]["path"] = _remap(job["source"]["path"], f.parent)
    if job.get("status") in ("queued", "running") and job.get("kind") != "live":
        # Picked up again as soon as the server is back (jobs.start), from the last finished part of the transcript.
        job.update(status="queued", stage="Picking up where it stopped", error=None, resume=True)
    if job.get("status") == "live":
        job.update(status="done", stage="Recording paused by a server update", was_live=True)
    buffered = job.get("kind") == "live" and any((f.parent / "buffer").glob("*.ts"))
    for c in job.get("clips", []):
        if c.get("src"):
            c["src"] = _remap(c["src"], f.parent / "clips")
        if c.get("status") in ("rendering", "queued"):
            if buffered and not c.get("src"):
                c["status"] = "queued"  # the stream is still on disk: live.recover() finishes these
                job["recover"] = True
            elif job.get("resume"):
                c["status"] = "queued"  # the video is being picked up again: this clip is made then
            elif job.get("kind") != "live" and (f.parent / "words.json").is_file():
                c["status"] = "queued"  # a single clip being re-made: jobs.start() makes it again
                job.setdefault("remake", []).append(c["id"])
            else:
                c.update(status="error", error="Interrupted by a restart. Open it and press Save and re-make.")
    _jobs[job["id"]] = job
    _by_user.setdefault(uid, {})[job["id"]] = job


def load_user(uid: str):
    d = config.USERS_DIR / uid
    for f in list((d / "jobs").glob("*/job.json")) + list((d / "creators").glob("*/*/*/job.json")):
        _load_file(f, uid)


def load_all():
    for d in config.USERS_DIR.iterdir():
        if d.is_dir():
            load_user(d.name)


def all_jobs() -> list:
    """Every user's jobs. Only for background work; never send this to a browser."""
    with _lock:
        return list(_jobs.values())


def list_jobs():
    u = ctx.uid()
    with _lock:
        return sorted((dict(j) for j in _by_user.get(u, {}).values()), key=lambda j: -j["created"])


def get(jid):
    """The job, only if it belongs to whoever this code is running for."""
    job = _jobs.get(jid)
    return job if job and job["user"] == ctx.uid() else None


def busy_count() -> int:
    u = ctx.uid()
    return sum(1 for j in list(_by_user.get(u, {}).values()) if j["kind"] == "video" and j["status"] in ("queued", "running"))


def waiting_total() -> int:
    return sum(1 for j in list(_jobs.values()) if j["kind"] == "video" and j["status"] == "queued")


def queue_epoch() -> int:
    return _q.epoch


def running_now() -> int:
    return _q.running


def create(source: dict, options: dict, enqueue=True, creator=None, kind="video") -> dict:
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:10]
    c = creators.get(creator) if creator else None
    if c:
        d = creators.root(c["id"]) / ("live" if kind == "live" else "videos") / jid
        base = {**DEFAULTS, **{k: c[k] for k in ("max_clips", "min_len", "max_len", "style", "layout")}}
    else:
        d, base, creator = config.user_dir(ctx.uid()) / "jobs" / jid, dict(DEFAULTS), None
    d.mkdir(parents=True)
    opts = {**base, **clean_options(options)}
    if opts["min_len"] >= opts["max_len"]:
        opts["min_len"] = max(5, opts["max_len"] - 5)
    job = {"id": jid, "user": ctx.uid(), "kind": kind, "creator": creator, "dir": str(d), "created": time.time(), "source": source,
           "options": opts, "status": "queued", "stage": "Waiting", "progress": 0.0,
           "title": source.get("name", ""), "uploader": "", "duration": 0, "clips": [], "error": None, "ai": {}}
    with _lock:
        _jobs[jid] = job
        _by_user.setdefault(job["user"], {})[jid] = job
    save(job)
    if enqueue:
        _q.put(("job", jid, None), user=job["user"])
    return job


def enqueue(job):
    save(job)
    _q.put(("job", job["id"], None), user=job["user"])


def submit(fn):
    """Run any heavy task (a render, a cleanup) on the single worker, in order."""
    _q.put(("fn", ctx.bind(fn), None))


def submit_live(fn):
    """Live-stream renders and cleanups: their own worker, so a long video can never hold them up."""
    _live_q.put(ctx.bind(fn))


def _live_worker():
    while True:
        fn = _live_q.get()
        try:
            fn()
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def delete(jid):
    with _lock:
        job = _jobs.pop(jid, None)
        if job:
            _by_user.get(job["user"], {}).pop(jid, None)
    if job:
        ctx.bump(job["user"])
        shutil.rmtree(job["dir"], ignore_errors=True)
    return bool(job)


CLIP_EDITS = {"hook": 60, "title": 100, "description": 2000}


def _local_title(job, c):
    """With no AI: words that were really said around the moment, never made-up ones."""
    try:
        words = json.loads((Path(job["dir"]) / "words.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        words = []
    near = [w["w"] for w in words if c.get("peak", c["start"]) - 2.5 <= w["s"] < c.get("peak", c["start"]) + 2.5]
    said = brain._text(" ".join(near), 200)
    return (f"\u201c{said[:64].rsplit(' ', 1)[0] if len(said) > 64 else said}\u201d" if said else "Highlight",
            " ".join(said.split()[:4]).upper() if said else "")


def _clip_from(job, c, n) -> dict:
    """A clip, from a ranked candidate: its times (a later start when the hook check found a slow opening and
    the owner allows trimming), what the ranking said about it, and the look chosen for the video."""
    o = job["options"]
    if not c.get("hook"):
        try:
            lj = json.loads((Path(job["dir"]) / "loudness.json").read_text(encoding="utf-8"))
            words = json.loads((Path(job["dir"]) / "words.json").read_text(encoding="utf-8"))
            c["hook"] = highlights.hook(c, words, lj["db"], lj["from"], int(o["min_len"]))
        except (OSError, ValueError, KeyError):
            c["hook"] = {}
    h, a = c.get("hook") or {}, c.get("ai") or {}
    start = c["start"]
    if o.get("hook_trim", True) and h.get("recommended_start") and not h.get("context_warning"):
        start = h["recommended_start"]
    cat = a.get("category") or "other"
    title, said = _local_title(job, c) if not a.get("title") else (a["title"], "")
    heads = dict(a.get("headlines") or {})
    if not heads:                                    # no AI: the words that were said, in every language slot
        heads = {"local": said or title.strip("\u201c\u201d")}
    return {"id": f"clip{n:02d}", "from_candidate": c["id"], "start": round(start, 2), "end": round(c["end"], 2),
            "original_start": round(c["start"], 2), "score": int(c.get("total", 0)), "scores": c.get("scores") or {},
            "ranked_by": "ai" if a else "local", "category": cat, "category_label": highlights.CATEGORY_LABELS.get(cat, "Highlight"),
            "hook": a.get("hook_text") or said, "title": title, "description": a.get("reason", ""),
            "hashtags": ["gaming", "shorts", re.sub(r"\W", "", cat)], "highlight_words": a.get("highlight_words") or [],
            "caption_ideas": a.get("captions") or [], "reason": a.get("reason") or "Picked by local signals: "
            + ", ".join(f"{k} {v}" for k, v in (c.get("signals") or {}).items() if k in ("loud", "reactions", "laughs")),
            "ending": a.get("ending", ""), "hook_analysis": h, "style": o.get("style", DEFAULTS["style"]),
            "captions": o.get("captions"), "layout": compose.mode_of(o.get("layout")), "zoom": o.get("zoom", "none"),
            "normalize": o.get("normalize", True), "quality": o.get("render_quality", "standard"),
            "headlines": heads, "issues": a.get("issues") or [], "caption_lang": o.get("caption_lang", "original"),
            "caption_order": o.get("caption_order", "hi_first"), "headline": None, "music": None,
            "status": "queued", "version": 0, "rejected": False}


def request_render(jid, cid, changes: dict):
    """Re-make one clip with new times, look or layout. The AI is not asked again: the ranking, titles and the
    tidied caption words already made for this clip are reused (only words that come into a longer clip are
    tidied, and only if the AI is allowed)."""
    job = _jobs[jid]
    clip = next(c for c in job["clips"] if c["id"] == cid)
    new = dict(clip)
    if changes.get("use_start") in ("original", "recommended"):
        h = clip.get("hook_analysis") or {}
        pick = h.get("original_start") if changes["use_start"] == "original" else h.get("recommended_start")
        if pick is not None:
            new["start"] = float(pick)
    for k in ("start", "end"):
        if k in changes:
            new[k] = round(float(changes[k]), 2)
    if "src_offset" in clip:  # live clip: can only move inside the saved piece of stream
        new["start"] = max(new["start"], clip["src_offset"])
        new["end"] = min(new["end"], clip["src_end"])
    if not (math.isfinite(new["start"]) and math.isfinite(new["end"])) or new["start"] < 0:
        raise ValueError("Those times aren't valid.")
    if job.get("duration") and new["end"] > job["duration"] + 0.5 and "src_offset" not in clip:
        raise ValueError("The end is past the end of the video.")
    if not 3 <= new["end"] - new["start"] <= 180:
        raise ValueError("A clip can be 3 to 180 seconds long.")
    for k, limit in CLIP_EDITS.items():
        if k in changes:
            new[k] = str(changes[k])[:limit]
    if "style" in changes:
        new["style"] = changes["style"] if changes["style"] in render.STYLES else DEFAULTS["style"]
        new.pop("captions", None)                 # a new preset starts from that preset's own look
    if "layout" in changes:
        new["layout"] = compose.mode_of(changes["layout"])
    if "captions" in changes:
        new["captions"] = captions.clean(changes["captions"]) if changes["captions"] else None
    if "manual" in changes:
        m = changes["manual"] if isinstance(changes["manual"], dict) else {}
        new["manual"] = {k: m[k] for k in ("face", "game", "split", "zoom", "order") if k in m}
        new["layout"] = "manual"
    if "zoom" in changes:
        new["zoom"] = changes["zoom"] if changes["zoom"] in CHOICES["zoom"] else "none"
    if "caption_lang" in changes:
        new["caption_lang"] = changes["caption_lang"] if changes["caption_lang"] in subtitles.MODES else "original"
    if "caption_order" in changes:
        new["caption_order"] = "en_first" if changes["caption_order"] == "en_first" else "hi_first"
    if "headline" in changes:
        new["headline"] = {**(clip.get("headline") or {}), **headline.clean(changes["headline"] or {})}
    if "music" in changes:
        new["music"] = {**(clip.get("music") or {}), **music.clean(changes["music"] or {})}
    if changes.get("retranslate"):                  # asked for in so many words: the translations are made again
        subtitles.forget(job["dir"])
    if "normalize" in changes:
        new["normalize"] = bool(changes["normalize"])
    if "quality" in changes:
        new["quality"] = changes["quality"] if changes["quality"] in render.QUALITY else "standard"
    if new.get("style") not in render.STYLES:
        new["style"] = DEFAULTS["style"]
    new["layout"] = compose.mode_of(new.get("layout"))
    new["rejected"] = False
    clip.update(new, status="queued", error=None)
    save(job)
    _q.put(("clip", jid, cid))


def set_rejected(jid, cid, rejected: bool):
    job = _jobs[jid]
    clip = next(c for c in job["clips"] if c["id"] == cid)
    clip["rejected"] = bool(rejected)
    save(job)


def add_candidate(jid, cand_id):
    """Make a clip from one of the moments that was found but not picked. Uses what the ranking already said."""
    job = _jobs[jid]
    cand = next((c for c in job.get("candidates") or [] if c["id"] == cand_id), None)
    if not cand:
        raise ValueError("No such moment.")
    if any(c.get("from_candidate") == cand_id for c in job["clips"]):
        raise ValueError("That moment is already a clip.")
    clip = _clip_from(job, cand, len(job["clips"]) + 1)
    job["clips"].append(clip)
    save(job)
    _q.put(("clip", jid, clip["id"]))
    return clip


def _fixed_words(clip, words, job, off):
    """The caption words for this clip. English is tidied by the AI once and reused (numbers as digits, names
    spelled right); when the clip was moved or made longer, only the new words are tidied. Any other language is
    kept exactly as it was spoken and written down: Hindi stays in Devanagari, Hinglish keeps its mix. Changing
    the caption language never rewrites these words: a translation is kept apart from them (subtitles.py)."""
    if subtitles.family(job.get("lang") or "en") != "en":
        return [w for w in words if clip["start"] - 1 <= w["s"] <= clip["end"] + 1]
    span = [round(clip["start"], 2), round(clip["end"], 2), "en2"]
    have = clip.get("fixed_words") or []
    old = clip.get("fixed_for") or []
    if old == span and have and not any(roman.needs_roman(w.get("w", "")) for w in have):
        return have
    raw = [w for w in words if clip["start"] - 1 <= w["s"] <= clip["end"] + 1]
    if have and len(old) == 3:
        a, b = old[0] - 1, old[1] + 1
        keep = [w for w in have if clip["start"] - 1 <= w["s"] <= clip["end"] + 1]
        extra = [w for w in raw if not a <= w["s"] <= b]
        if not extra:
            clip.update(fixed_words=keep, fixed_for=span)
            return keep
        raw, keep_part = extra, keep
    else:
        keep_part = []
    try:
        usage.set_context(job.get("creator"), job.get("title") or "")
        if not ai.allowed():
            raise RuntimeError("AI off")
        raw = brain.english_captions(raw, job.get("lang") or "", job.get("title") or "",
                                     youtube_english=lambda: _english_part(job, clip))
    except Exception:  # noqa: BLE001 - captions still render with the words as heard
        raw = roman.words(raw)
    got = sorted(keep_part + raw, key=lambda w: w["s"])
    clip.update(fixed_words=got, fixed_for=span)
    return got


def _dress(job, clip, local, near):
    """Captions in the chosen language, the headline and the music, for one render. Translations come from the
    project's store (made once); a missing one is made now, or the spoken words are shown and the clip says so."""
    d, o = Path(job["dir"]), job["options"]
    src_lang = job.get("lang") or "en"
    mode = clip.get("caption_lang") or o.get("caption_lang") or "original"
    order = clip.get("caption_order") or o.get("caption_order") or "hi_first"
    to = subtitles.needs_translation(src_lang, mode)
    tr, note = {}, ""
    if to:
        texts = [ph["text"] for ph in subtitles.phrases(near)]
        try:
            usage.set_context(job.get("creator"), job.get("title") or "")
            tr = subtitles.translate(d, texts, to, src_lang, job.get("title") or "")
        except Exception as e:  # noqa: BLE001 - the spoken words are shown, and the reason is said
            tr = subtitles.cached(d, texts, to)
            note = f"Captions couldn't be translated ({safe.friendly(e, 160)}), so the spoken words are shown."
    cd = subtitles.build(near, src_lang, mode, order, tr)
    if to and cd["missing"] and not note:
        note = f"{cd['missing']} caption line{'s' if cd['missing'] != 1 else ''} had no translation and show the spoken words."
    clip["caption_note"] = note
    local["caption_data"] = cd
    # projects made before headlines and music existed keep looking the way they were made: the title box for the
    # whole clip, and no music, unless the owner changes that on a clip
    older = "headline_lang" not in o
    hs = headline.resolve(clip.get("headline"), {**({"duration": 0} if older else {}),
                                                 **({"lang": o["headline_lang"]} if o.get("headline_lang") else {}),
                                                 **(o.get("headline") or {})})
    local["headline_set"] = hs
    local["headline_text"] = headline.text_for(clip, hs, src_lang)
    clip["headline_shown"] = local["headline_text"] if hs.get("enabled") else ""
    ms = music.resolve(clip.get("music"), {"enabled": False} if "music" not in o else o.get("music"))
    t = music.pick(ms, clip.get("category"))
    local["music_set"] = ms
    local["music_path"] = str(music.folder() / t["file"]) if t else ""
    clip["music_used"] = {"id": t["id"], "title": t["title"]} if t else None


def render_one(job, clip, words):
    d = Path(job["dir"])
    clip["status"] = "rendering"
    clip["error"] = None
    save(job)
    try:
        off = clip.get("src_offset", 0.0)
        src = Path(clip["src"]) if clip.get("src") else Path(job["source_path"])
        if not clip.get("src"):
            clip["end"] = min(clip["end"], job["duration"])
        raw = _fixed_words(clip, words, job, off)
        near = [dict(w, s=w["s"] - off, e=w["e"] - off) for w in raw]
        local = dict(clip, start=clip["start"] - off, end=clip["end"] - off)
        _dress(job, clip, local, near)
        if clip.get("src"):                       # a piece fetched for this clip: its own size
            info = media.probe(src)
            w, h = info["width"], info["height"]
        else:
            w, h = job["width"], job["height"]
        local["scene"] = clip["scene"] = _scene(job, clip, src, local["start"], local["end"], w, h)
        out = render.render_clip(src, d / "clips", clip["id"], local, near, w, h)
        clip.update(out, status="done", version=clip.get("version", 0) + 1, error=None)
        post = {k: clip.get(k) for k in ("title", "description", "hashtags", "hook", "start", "end", "score")}
        (d / "clips" / f"{clip['id']}.json").write_text(json.dumps(post, indent=1), encoding="utf-8")
        if clip["version"] == 1:
            clip.setdefault("made_at", time.time())
            creators.log(job["creator"], f"Clip ready (score {clip.get('score', 0)}): {clip.get('title') or clip.get('hook')}")
            save(job)
            for fn in on_clip_ready:
                try:
                    fn(job, clip)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
    except Exception as e:  # noqa: BLE001
        clip.update(status="error", error=safe.friendly(e, 400))
        creators.log(job["creator"], f"Clip failed: {safe.friendly(e, 200)}")
    save(job)


_facecam_lock = threading.Lock()


def _scene(job, clip, src, start, end, w, h) -> dict:
    """What the layout needs to know about this clip (face, facecam, action), looked at once and kept. The facecam
    is searched for across the whole video once per video; a piece fetched for one clip is searched on its own."""
    key = [round(start, 2), round(end, 2), str(src.name)]
    if clip.get("scene") and clip.get("scene_for") == key:
        return clip["scene"]
    if clip.get("src"):
        fc = face.find_facecam(src, media.probe(src)["duration"], w, h, samples=12)
    else:
        with _facecam_lock:
            if not job.get("facecam") or job.get("facecam_for") != str(src.name):
                job["facecam"] = face.find_facecam(src, job.get("duration") or end, w, h)
                job["facecam_for"] = str(src.name)
                save(job)
        fc = job["facecam"]
    sc = face.analyse(src, start, end, w, h, fc)
    clip["scene_for"] = key
    return sc


CLIP_MINUTES = float(config.env("PEAKCUT_CLIP_MINUTES", "15"))     # the longest one clip may take to make


def _wait_for_clips(job, futures, began, workers):
    """Wait for a video's clips, but not for ever. A clip that hangs (a download that stalls, an encoder that
    freezes) used to keep the whole video "in progress", and no other clip could be re-made. Now it is marked as
    failed after CLIP_MINUTES and the video finishes with the clips that did work."""
    left, given_up = list(futures), 0
    late = "This clip took too long and was stopped. Open it and press Save and re-make to try again."
    while left:
        still = []
        for f, clip in left:
            if f.done():
                f.result()
                continue
            t = began.get(clip["id"])
            if (t and time.time() - t > CLIP_MINUTES * 60) or (not t and given_up >= workers):
                given_up += 1
                clip.update(status="error", error=late)
                creators.log(job["creator"], f"Clip stopped after {CLIP_MINUTES:g} minutes: {clip.get('title') or clip.get('hook') or clip['id']}")
                save(job)
                continue
            still.append((f, clip))
        left = still
        if left:
            time.sleep(1)


def _english_part(job, clip):
    """YouTube's English captions for just this clip, for a video in another language. Fetched once per video."""
    if (job.get("source") or {}).get("type") != "url":
        return None
    f = Path(job["dir"]) / "words_en.json"
    if f.is_file():
        en = json.loads(f.read_text(encoding="utf-8"))
    else:
        try:
            en = media.youtube_english_words(job["source"]["url"]) or []
        except Exception:  # noqa: BLE001 - YouTube often refuses its translations: the AI translates instead
            en = []
        f.write_text(json.dumps(en), encoding="utf-8")       # asked once per video, refused or not
    return [w for w in en if clip["start"] - 0.5 <= w["s"] <= clip["end"] + 0.5]


LONG_VIDEO = 40 * 60   # above this, fetch only the pieces that become clips
PAD = 8                # seconds kept either side of a fetched piece, so clips can be re-trimmed


def _fetch_piece(job, clip, full_audio):
    """Long videos: download just this clip's stretch and pin down exactly where it starts."""
    d = Path(job["dir"])
    o = job["options"]
    try:
        want = max(0.0, clip["start"] - PAD)
        piece = media.download_section(job["source"]["url"], d / "clips", f"{clip['id']}.src", want,
                                       min(job["duration"], clip["end"] + PAD), int(o["quality"]))
        wav = d / "clips" / f"{clip['id']}.wav"
        media.extract_audio(piece, wav)
        part = media.load_audio(wav)
        wav.unlink(missing_ok=True)
        meta = media.probe(piece)
        off = media.find_offset(full_audio, part, want)
        clip.update(src=str(piece), src_offset=round(off, 2), src_end=round(off + meta["duration"], 2))
        clip["start"], clip["end"] = max(clip["start"], clip["src_offset"]), min(clip["end"], clip["src_end"])
        job.setdefault("width", meta["width"])
        job.setdefault("height", meta["height"])
    except Exception as e:  # noqa: BLE001
        clip.update(status="error", error=f"Could not fetch this part of the video: {safe.friendly(e, 250)}")


BLOCK = 10 * 60         # long videos are transcribed and clipped one block at a time
PROGRESSIVE_FROM = 20 * 60


def _covers(words: list, start: float, end: float) -> bool:
    """Is a borrowed transcript good enough to find clips with? It has to reach the end of the part being clipped
    and have a normal amount of talking in it; otherwise our own model writes the transcript."""
    inside = [w for w in words if start <= w["s"] <= end]
    minutes = max(0.5, (end - start) / 60)
    return len(inside) >= 25 * minutes and inside[-1]["s"] >= start + 0.85 * (end - start)


def _language(job, o, yt, audio) -> dict:
    """The language of the video, and so which engine writes it down (see whisper_asr.classify)."""
    if o.get("language") and o["language"] != "auto":
        return whisper_asr.for_override(o["language"])
    if yt:
        code = (yt[1] or "").lower()
        if code.startswith("en"):
            return {"lang": "en", "whisper_lang": "en", "probability": 1.0, "engine": "parakeet", "probs": {}, "from": "youtube"}
        base = code.split("-")[0]
        return {"lang": base, "whisper_lang": base, "probability": 1.0, "engine": "whisper", "probs": {}, "from": "youtube"}
    if not whisper_asr.available():
        return {"lang": "en", "whisper_lang": "en", "probability": 0.0, "engine": "parakeet", "probs": {},
                "note": "The multilingual speech engine isn't installed, so the video was taken to be English."}
    _set(job, phase="transcribing", stage="Listening for the language" if whisper_asr.ready() else
         "Getting the multilingual speech model (one time, about 1.6 GB)")
    try:
        return whisper_asr.detect(audio)
    except Exception as e:  # noqa: BLE001 - said on the video; English is assumed
        return {"lang": "en", "whisper_lang": "en", "probability": 0.0, "engine": "parakeet", "probs": {},
                "note": f"The language couldn't be detected ({safe.friendly(e, 160)}), so the video was taken to be English. "
                        "Choose the language under More options and make it again if that is wrong."}


def _download_message(e) -> str:
    if isinstance(e, media.YouTubeBlocked):
        return str(e)
    why = safe.friendly(e, 300).replace("ERROR: ", "").strip()
    return media.UPLOAD_INSTEAD + (f" ({why[-220:]})" if why else "")


def _run_job(job):
    from concurrent.futures import ThreadPoolExecutor
    d = Path(job["dir"])
    o = job["options"]
    src = job["source"]
    t0 = time.time()
    notes = []
    usage.set_context(job["creator"], job["title"] or src.get("name"))
    resuming = bool(job.pop("resume", False)) and (d / "words.json").is_file()
    if resuming:   # after a restart: keep the transcript parts, clips and AI answers already made, and carry on
        _set(job, status="running", stage="Picking up where it stopped", error=None)
    else:
        _set(job, status="running", phase="importing", stage="Importing the video", progress=0.03, error=None, clips=[], ai={},
             done_blocks=0, candidates=[], selection={})
    if not transcribe.model_ready() and not transcribe.REMOTE:
        from . import fetch_model
        _set(job, stage="Getting the speech model (one time, about 480 MB)")
        fetch_model.ensure()
        _set(job, stage="Importing the video")
    fetch = ThreadPoolExecutor(1)
    video = None
    if src["type"] == "url":
        try:
            meta = media.info(src["url"])
        except Exception as e:  # noqa: BLE001 - said plainly, with the way out: upload the file instead
            raise media.DownloadFailed(_download_message(e)) from None
        if meta["kind"] != "video":
            raise RuntimeError("That link is a channel or playlist, not a single video. Paste the link of one video.")
        if ((o["range_end"] or meta["duration"]) - (o["range_start"] or 0)) > config.MAX_VIDEO_HOURS * 3600:
            raise RuntimeError(f"That is longer than {config.MAX_VIDEO_HOURS:g} hours. Choose a shorter part of it under Options.")
        if meta["is_live"]:
            raise RuntimeError("This link is a live stream, so it would never finish downloading. Open the creator, "
                               "put this link under Setup, and press Start recording instead.")
        _set(job, title=meta["title"], uploader=meta["uploader"], duration=meta["duration"])
        if not job.get("thumb") and media.save_thumb(d, url=src["url"]):
            _set(job, thumb=True)
        usage.set_context(job["creator"], meta["title"])
        span = (o["range_end"] or meta["duration"]) - (o["range_start"] or 0)
        pieces = span > LONG_VIDEO or meta["duration"] <= 0
        if not pieces:  # shorter video: fetch all of it in the background while the audio is worked on
            video = fetch.submit(media.download, src["url"], d, int(o["quality"]))
        try:
            audio_file = media.download_audio(src["url"], d, lambda p: _set(job, progress=0.03 + 0.1 * p))
        except Exception as e:  # noqa: BLE001
            raise media.DownloadFailed(_download_message(e)) from None
    else:
        pieces, audio_file = False, Path(src["path"])
        info = media.probe(audio_file)
        if info["duration"] > config.MAX_VIDEO_HOURS * 3600:
            raise RuntimeError(f"That video is longer than {config.MAX_VIDEO_HOURS:g} hours, which is the limit.")
        _set(job, source_path=str(audio_file), width=info["width"], height=info["height"], duration=info["duration"])
        if not job.get("thumb") and media.save_thumb(d, video=audio_file):
            _set(job, thumb=True)
    wav = d / "audio.wav"
    media.extract_audio(audio_file, wav)
    audio = media.load_audio(wav)
    wav.unlink(missing_ok=True)
    if src["type"] == "url":
        audio_file.unlink(missing_ok=True)
    if not job["duration"]:
        job["duration"] = len(audio) / 16000
    lo = int((o["range_start"] or 0) * 16000)
    hi = min(len(audio), int(o["range_end"] * 16000)) if o["range_end"] else len(audio)
    span = (hi - lo) / 16000
    db = media.loudness_per_second(audio[lo:hi])

    vlock, vstate = threading.Lock(), {}

    def whole_video():
        """Wait for the background download once; retry it, or give up and use pieces."""
        with vlock:
            if "path" not in vstate:
                path = None
                try:
                    path = video.result()
                except Exception as e:  # noqa: BLE001 - sites sometimes refuse a download
                    notes.append(f"Video download refused once ({str(e)[-80:]}), retrying")
                    try:
                        path = media.download(src["url"], d, int(o["quality"]))
                    except Exception as e2:  # noqa: BLE001
                        notes.append(f"Whole-video download failed ({str(e2)[-80:]}), fetching clip parts instead")
                if path:
                    info = media.probe(path)
                    job.update(source_path=str(path), width=info["width"], height=info["height"])
                vstate["path"] = path
            return vstate["path"]

    exact = {}      # clip id -> (from, to, words): our own model's words for just that clip

    began = {}      # clip id -> when work on it started

    def make(clip, words_now):
        began[clip["id"]] = time.time()
        if src["type"] == "url" and (pieces or not whole_video()):
            _fetch_piece(job, clip, audio)
        if clip["status"] != "error" and yt and yt[1].lower().startswith("en") and li["engine"] == "parakeet":
            # The clip was found with YouTube's transcript. What is shown on screen comes from our own model,
            # run on just this clip: full punctuation, nothing blanked out, timings to the word.
            a0, b0 = max(lo, int((clip["start"] - 1.5) * 16000)), min(hi, int((clip["end"] + 1.5) * 16000))
            try:
                tw = [dict(w, s=round(w["s"] + a0 / 16000, 2), e=round(w["e"] + a0 / 16000, 2)) for w in transcribe.transcribe(audio[a0:b0])]
            except Exception:  # noqa: BLE001 - the clip is still made, with YouTube's words
                tw = []
            if len(tw) >= 3:
                exact[clip["id"]] = (a0 / 16000, b0 / 16000, tw)
                words_now = tw
        if clip["status"] != "error":
            render_one(job, clip, words_now)

    cpus = os.cpu_count() or 4
    renders = ThreadPoolExecutor(2 if cpus < 8 else min(6, cpus // 6))  # more clips made at once on a bigger machine
    futures = []
    step = BLOCK * 16000 if span > PROGRESSIVE_FROM else hi - lo
    blocks = [(x, min(hi, x + step)) for x in range(lo, hi, step)] or [(lo, hi)]
    words, tokens, model = [], 0, None
    done_blocks = 0
    yt = None
    if src["type"] == "url" and config.YT_TRANSCRIPT and media.is_youtube(src["url"]):
        try:
            yt = media.youtube_words(src["url"])
        except Exception:  # noqa: BLE001 - no transcript to borrow: ours is written as usual
            yt = None
        if yt and not _covers(yt[0], lo / 16000, hi / 16000):
            yt = None
        job["transcript_from"] = "youtube" if yt else "own"
    # ---- which language is spoken: the owner's choice, else what YouTube says, else a listen to the audio itself
    if not (resuming and job.get("lang_info")):
        job["lang_info"] = _language(job, o, yt, audio[lo:hi])
    li = job["lang_info"]
    job["lang"] = li["lang"]
    asr_lang = "" if li["engine"] == "parakeet" else li["whisper_lang"]
    lang_note = "" if li["lang"] == "en" else f" ({whisper_asr.name(li['lang'])})"
    save(job)
    if resuming:
        try:
            words = json.loads((d / "words.json").read_text(encoding="utf-8"))
        except ValueError:
            words = []
        done_blocks = min(int(job.get("done_blocks", 0)), len(blocks)) if words else 0
        tokens, model = int((job.get("ai") or {}).get("tokens", 0)), (job.get("ai") or {}).get("model")
        if done_blocks < len(blocks):     # words past the last finished part are transcribed again
            cut = blocks[done_blocks][0] / 16000
            words = [w for w in words if w["s"] < cut]
        for c in job["clips"]:            # clips found before the restart that were not finished yet
            if c.get("status") not in ("done", "error"):
                c["status"] = "queued"
                futures.append((renders.submit(ctx.bind(make), c, list(words)), c))
        save(job)
    # The transcript of the next part is written while the AI is still reading this one (the AI is a wait on the
    # network, the transcript is work on this machine), so neither waits for the other.
    ahead = ThreadPoolExecutor(2, thread_name_prefix="ahead")
    heard, now_on = {}, [done_blocks]

    def hear(i):
        if i < len(blocks) and i >= done_blocks and i not in heard:
            a_, b_ = blocks[i]
            last = [0.0]

            def tp(p, i=i, a_=a_, b_=b_):
                if now_on[0] == i and p - last[0] > 0.05:      # only the part being shown moves the bar
                    last[0] = p
                    _set(job, progress=max(job["progress"], 0.14 + 0.78 * ((a_ - lo) + p * (b_ - a_)) / max(1, hi - lo)))
            heard[i] = ahead.submit(ctx.bind(transcribe.transcribe), audio[a_:b_], tp, asr_lang)

    for bi, (a, b) in enumerate(blocks):
        if bi < done_blocks:
            continue                      # transcribed before the restart
        shift = a / 16000
        where = f" (minute {round((a - lo) / 16000 / 60)} of {round(span / 60)})" if len(blocks) > 1 else ""
        if yt:
            _set(job, phase="transcribing", stage="Reading the transcript" + where)
            bw = [dict(w, s=round(w["s"] - shift, 2), e=round(w["e"] - shift, 2)) for w in yt[0] if a / 16000 <= w["s"] < b / 16000]
        else:
            _set(job, phase="transcribing", stage="Transcribing on this Mac" + lang_note + where)
            now_on[0] = bi
            hear(bi)
            hear(bi + 1)
            hear(bi + 2)
            bw = heard.pop(bi).result()
        _set(job, progress=max(job["progress"], 0.14 + 0.78 * (b - lo) / max(1, hi - lo)))
        words += [dict(w, s=round(w["s"] + shift, 2), e=round(w["e"] + shift, 2)) for w in bw]
        (d / "words.json").write_text(json.dumps(words), encoding="utf-8")
        job["done_blocks"] = bi + 1       # a restart from here on carries on with the next part
        _set(job, word_count=len(words))
    lo_s, hi_s = lo / 16000, hi / 16000
    (d / "loudness.json").write_text(json.dumps({"from": lo_s, "db": [round(float(x), 2) for x in db]}), encoding="utf-8")
    if not job["clips"]:                  # (a restart after the clips were chosen keeps them: no second AI call)
        _set(job, phase="analysing", stage="Analysing the moments", progress=max(job["progress"], 0.9))
        rel = [dict(w, s=w["s"] - lo_s, e=w["e"] - lo_s) for w in words if lo_s <= w["s"] < hi_s]
        cands = highlights.candidates(rel, db, hi_s, int(o["min_len"]), int(o["max_len"]), int(o["candidates"]), lo=lo_s)
        _set(job, phase="selecting", stage=f"Selecting highlights from {len(cands)} moments")
        cands, rk = highlights.rank(cands, words, job["title"], job["uploader"], int(o["max_clips"]), o["rank_mode"], notes.append,
                                    lang=job.get("lang") or "en")
        tokens += rk.get("tokens", 0)
        model = rk.get("model") or model
        picked, why_fewer = highlights.select(cands, int(o["max_clips"]), o["rank_mode"], rk["ranked_by"])
        for c in picked:
            c["hook"] = highlights.hook(c, words, db, lo_s, int(o["min_len"]))
        chosen = {c["id"] for c in picked}
        job["candidates"] = [{k: v for k, v in c.items()} | {"picked": c["id"] in chosen} for c in cands]
        job["selection"] = {"ranked_by": rk["ranked_by"], "cached": rk.get("cached", False), "note": rk.get("note", ""),
                            "fewer": why_fewer, "asked": int(o["max_clips"]), "found": len(cands), "mode": o["rank_mode"]}
        for c in picked:
            job["clips"].append(_clip_from(job, c, len(job["clips"]) + 1))
        job["ai"] = {"model": model, "tokens": tokens, "note": rk.get("note", ""), "log": notes[-5:],
                     "ranked_by": rk["ranked_by"], "cached": rk.get("cached", False)}
        to = subtitles.needs_translation(job.get("lang") or "en", o.get("caption_lang", "original"))
        if to and job["clips"]:
            _set(job, stage="Translating the captions")
            texts = [ph["text"] for c in job["clips"]
                     for ph in subtitles.phrases([w for w in words if c["start"] - 1 <= w["s"] <= c["end"] + 1])]
            try:
                subtitles.translate(d, texts, to, job.get("lang") or "", job["title"])
            except Exception as e:  # noqa: BLE001 - each clip then says its captions show the spoken words
                notes.append(f"Captions could not be translated: {safe.friendly(e, 200)}")
        save(job)
        for c in job["clips"]:
            if o.get("auto_render", True):
                futures.append((renders.submit(ctx.bind(make), c, list(words)), c))
            else:
                c["status"] = "pending"   # waits for the owner to look at it and press Render
        save(job)
    if futures:
        _set(job, phase="rendering", stage=f"Rendering {len(futures)} clip{'s' if len(futures) != 1 else ''}",
             progress=max(job["progress"], 0.92))
    _wait_for_clips(job, futures, began, renders._max_workers)
    if exact:           # keep our own words for the clips in the saved transcript, so editing a clip later uses them
        for a_s, b_s, tw in exact.values():
            words = [w for w in words if not a_s <= w["s"] < b_s] + tw
        words.sort(key=lambda w: w["s"])
        (d / "words.json").write_text(json.dumps(words), encoding="utf-8")
    renders.shutdown(wait=False)
    ahead.shutdown(wait=False)
    fetch.shutdown(wait=False)
    del audio
    clips = job["clips"]
    failed = sum(1 for c in clips if c.get("status") == "error")
    stage = ("Review ready" if clips and not failed else
             f"Review ready: {failed} of {len(clips)} clips failed to render" if clips else
             "Done, but nothing clip-worthy was found")
    _set(job, status="done", phase="ready", stage=stage, progress=1.0, took=round(time.time() - t0))
    try:
        from . import notices
        name = job.get("title") or job["source"].get("name") or "your video"
        notices.add(job.get("user"), "clips", f"{len(clips)} clip{'s' if len(clips) != 1 else ''} ready" if clips else "Nothing clip-worthy found",
                    name, f"/classic/creators/{job['creator']}" if job.get("creator") else "/app/videos")
    except Exception:  # noqa: BLE001
        pass
    creators.log(job["creator"], f"Video finished with {len(clips)} clips in {round(time.time() - t0)}s: {job['title']}")


def _worker(quick_only=False):
    while True:
        kind, a, cid = _q.get(quick_only)
        if kind == "fn":
            try:
                a()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            continue
        job = _jobs.get(a)
        if not job:
            if kind == "job":
                _q.done()
            continue
        token = ctx._uid.set(job["user"])  # everything this job does runs as its owner
        try:
            if kind == "job":
                _run_job(job)
            else:
                words = json.loads((Path(job["dir"]) / "words.json").read_text(encoding="utf-8"))
                clip = next(c for c in job["clips"] if c["id"] == cid)
                render_one(job, clip, words)
        except Exception as e:  # noqa: BLE001
            (Path(job["dir"]) / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            if kind == "job":
                for c in job["clips"]:
                    if c.get("status") in ("queued", "rendering"):
                        c.update(status="error", error="The video stopped before this clip was made.")
                _set(job, status="error", error=safe.friendly(e), stage="Stopped",
                     upload_instead=isinstance(e, media.DownloadFailed))
                creators.log(job["creator"], f"Video failed: {safe.friendly(e)[:200]}")
        finally:
            ctx._uid.reset(token)
            if kind == "job":
                _q.done()


def _pick_up_after_restart():
    """Videos and single clips that were being made when the server stopped: queued again, oldest first."""
    waiting = sorted((j for j in all_jobs() if j.get("status") == "queued" and j.get("resume")), key=lambda j: j["created"])
    for j in waiting:
        _q.put(("job", j["id"], None), user=j["user"])
    for j in all_jobs():
        for cid in j.pop("remake", []):
            _q.put(("clip", j["id"], cid))
        if j.get("status") == "done":
            save(j)


def start():
    load.start()
    load_all()
    _pick_up_after_restart()
    for _ in range(max(1, config.JOB_WORKERS)):
        threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_worker, args=(True,), daemon=True).start()   # re-making a single clip never waits behind whole videos
    threading.Thread(target=_live_worker, daemon=True).start()
