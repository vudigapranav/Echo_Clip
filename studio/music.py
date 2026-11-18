"""Background music: a small library on this computer, a pick per clip, and a mix that steps aside for the voice.

The library lives in data/music/. It starts with a few tracks Echo Clip composes itself (simple synthesised loops:
original, so free to use anywhere). The owner can add their own: only music they made, bought a licence for, or
that is public domain or free to use. Nothing is ever downloaded from anywhere.

A clip's music setting:
    enabled    on / off
    track      "auto" (chosen from the clip's kind of moment), a track id, or "none"
    volume     the music's level, percent (12 by default: present, never in the way)
    original   the clip's own sound, percent (100 = unchanged)
    duck       lower the music while someone talks (on by default)
    fade_in    seconds, fade_out seconds

The mix: the music is looped or cut to the clip's length, faded in and out, lowered by a compressor that listens to
the clip's own sound (so it dips smoothly when the streamer talks and comes back in the pauses), then mixed under
the original sound and kept below clipping."""
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave
from pathlib import Path

import numpy as np

from . import config
from .media import NO_WINDOW

CATEGORIES = {"energetic": "Gaming energetic", "funny": "Funny / comedic", "suspense": "Suspense",
              "cinematic": "Cinematic", "chill": "Chill", "dramatic": "Dramatic", "action": "Action"}
# which music suits which kind of moment (highlights.CATEGORIES); no AI call needed per clip
FOR_MOMENT = {"funny": "funny", "fail": "funny", "dialogue": "funny", "npc": "funny", "argument": "dramatic",
              "chase": "energetic", "driving": "energetic", "gameplay": "action", "near_miss": "action",
              "surprise": "suspense", "death": "dramatic", "reaction": "dramatic", "other": "energetic",
              "clutch": "action", "rage": "dramatic", "shock": "suspense", "take": "chill", "story": "cinematic"}
AUDIO_TYPES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
BASE = {"enabled": True, "track": "auto", "volume": 12, "original": 100, "duck": True, "fade_in": 1.0, "fade_out": 1.5}
_LIMITS = {"volume": (0, 60), "original": (0, 200), "fade_in": (0, 10), "fade_out": (0, 10)}
_lock = threading.Lock()


def folder() -> Path:
    d = config.DATA_DIR / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_file():
    return folder() / "library.json"


def _read() -> dict:
    try:
        d = json.loads(_index_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d):
    tmp = _index_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_index_file())


def clean(v) -> dict:
    if not isinstance(v, dict):
        raise ValueError("Bad music settings.")
    out = {}
    for k, x in v.items():
        if k not in BASE or x is None:
            continue
        if k in ("enabled", "duck"):
            x = bool(x) and x not in ("0", "false", "off")
        elif k == "track":
            x = str(x)
            if x not in ("auto", "none") and not re.fullmatch(r"[\w\-]{3,40}", x):
                raise ValueError("Bad music track.")
        else:
            lo, hi = _LIMITS[k]
            x = max(lo, min(hi, float(x)))
        out[k] = x
    return out


def defaults() -> dict:
    return {**BASE, **(_read().get("defaults") or {})}


def resolve(clip_setting, job_setting=None) -> dict:
    return {**defaults(), **(job_setting or {}), **(clip_setting or {})}


def tracks() -> list:
    ensure_builtin()
    return [t for t in _read().get("tracks", []) if (folder() / t["file"]).is_file()]


def get(tid):
    return next((t for t in tracks() if t["id"] == tid), None)


def pick(setting, moment_category="other"):
    """The track for a clip, or None for no music."""
    if not setting.get("enabled") or setting.get("track") == "none":
        return None
    if setting.get("track") not in (None, "", "auto"):
        return get(setting["track"])
    lib = tracks()
    if not lib:
        return None
    want = FOR_MOMENT.get(moment_category or "other", "energetic")
    d = _read().get("defaults", {}).get("per_category", {})
    if d.get(want) and get(d[want]):
        return get(d[want])
    same = [t for t in lib if t["category"] == want]
    return (same or lib)[0]


def public() -> dict:
    return {"tracks": tracks(), "categories": CATEGORIES, "defaults": defaults(), "formats": sorted(e[1:].upper() for e in AUDIO_TYPES),
            "for_moment": FOR_MOMENT}


def set_defaults(v: dict) -> dict:
    with _lock:
        d = _read()
        cur = d.get("defaults") or {}
        cur.update(clean({k: x for k, x in v.items() if k in BASE}))
        pc = v.get("per_category")
        if isinstance(pc, dict):
            cur["per_category"] = {k: str(x) for k, x in pc.items() if k in CATEGORIES and (x == "" or get(str(x)))}
        d["defaults"] = cur
        _write(d)
    return public()


def probe(path: Path) -> float:
    """The length of a music file, after checking it really holds sound. Raises ValueError with the reason."""
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                       capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
    if p.returncode != 0:
        raise ValueError("That file isn't music Echo Clip can read: it is damaged or not an audio file.")
    data = json.loads(p.stdout or "{}")
    if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
        raise ValueError("That file has no sound in it.")
    dur = float((data.get("format") or {}).get("duration") or 0)
    if dur < 3:
        raise ValueError("That track is shorter than 3 seconds.")
    return dur


def add(src: Path, name: str, title: str, category: str, licence: str) -> dict:
    """Keep an uploaded track. The owner states where it comes from (it is shown with the track)."""
    ext = Path(name).suffix.lower()
    if ext not in AUDIO_TYPES:
        raise ValueError("Upload MP3, WAV, M4A, AAC, FLAC or OGG.")
    if category not in CATEGORIES:
        raise ValueError("Choose a category for the track.")
    dur = probe(src)
    tid = "u" + uuid.uuid4().hex[:10]
    dest = folder() / f"{tid}{ext}"
    shutil.move(str(src), dest)
    t = {"id": tid, "title": re.sub(r"[\x00-\x1f]+", " ", title or Path(name).stem).strip()[:80] or "Untitled",
         "category": category, "file": dest.name, "duration": round(dur, 1), "source": "upload",
         "licence": re.sub(r"[\x00-\x1f]+", " ", licence or "").strip()[:160] or "Added by you", "added": time.time()}
    with _lock:
        d = _read()
        d.setdefault("tracks", []).append(t)
        _write(d)
    return t


def remove(tid: str) -> bool:
    with _lock:
        d = _read()
        t = next((x for x in d.get("tracks", []) if x["id"] == tid), None)
        if not t:
            return False
        d["tracks"] = [x for x in d["tracks"] if x["id"] != tid]
        pc = (d.get("defaults") or {}).get("per_category") or {}
        for k in [k for k, v in pc.items() if v == tid]:
            pc.pop(k)
        _write(d)
    (folder() / t["file"]).unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------- the mix
def audio_graph(dur: float, setting: dict, has_music: bool, music_index: int, normalize: bool) -> str:
    """The ffmpeg audio filter: [0:a] is the clip's own sound, [music_index:a] the looping track. Ends in [a]."""
    orig = setting.get("original", 100) / 100
    chain = (f"[0:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
             f"volume={orig:.3f}")
    if not has_music:
        return chain + (",loudnorm=I=-14:TP=-1.5:LRA=11" if normalize else "") + ",alimiter=limit=0.95[a]"
    vol = setting.get("volume", 12) / 100
    fi, fo = setting.get("fade_in", 1.0), setting.get("fade_out", 1.5)
    fo = min(fo, max(0.0, dur - 0.1))
    music = (f"[{music_index}:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
             f"atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,volume={vol:.3f}"
             + (f",afade=t=in:st=0:d={fi:.2f}" if fi > 0 else "")
             + (f",afade=t=out:st={max(0.0, dur - fo):.2f}:d={fo:.2f}" if fo > 0 else ""))
    if setting.get("duck", True):
        # the voice drives a compressor on the music: about 12 dB lower while someone talks, back in the pauses
        g = (chain + ",asplit=2[voice][key];" + music + "[mus];"
             "[mus][key]sidechaincompress=threshold=0.015:ratio=10:attack=25:release=450:knee=4[duck];"
             "[voice][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0")
    else:
        g = chain + "[voice];" + music + "[mus];[voice][mus]amix=inputs=2:duration=first:dropout_transition=0:normalize=0"
    return g + (",loudnorm=I=-14:TP=-1.5:LRA=11" if normalize else "") + ",alimiter=limit=0.95[a]"


# ---------------------------------------------------------------- tracks Echo Clip composes itself
BUILTIN = [
    ("arcade-rush", "Arcade Rush", "energetic", 128, "minor", "drive"),
    ("goofy-bounce", "Goofy Bounce", "funny", 112, "major", "bounce"),
    ("night-tension", "Night Tension", "suspense", 88, "minor", "tension"),
    ("lofi-drift", "Lo-fi Drift", "chill", 78, "major", "chill"),
    ("big-moment", "Big Moment", "dramatic", 70, "minor", "epic"),
]
LICENCE = "Composed by Echo Clip (original, free to use anywhere)"


def ensure_builtin():
    """Make the built-in tracks the first time (a few seconds), and list them."""
    d = _read()
    have = {t["id"] for t in d.get("tracks", [])}
    missing = [b for b in BUILTIN if f"builtin-{b[0]}" not in have or not (folder() / f"builtin-{b[0]}.m4a").is_file()]
    if not missing:
        return
    with _lock:
        d = _read()
        d.setdefault("tracks", [])
        for slug, title, cat, bpm, scale, kind in missing:
            tid = f"builtin-{slug}"
            out = folder() / f"{tid}.m4a"
            try:
                _compose(out, bpm, scale, kind)
            except Exception as e:  # noqa: BLE001 - the library simply has fewer tracks
                print(f"  Could not make the music track {title}: {e}", flush=True)
                continue
            d["tracks"] = [t for t in d["tracks"] if t["id"] != tid] + [
                {"id": tid, "title": title, "category": cat, "file": out.name, "duration": 32.0, "source": "builtin",
                 "licence": LICENCE, "added": time.time()}]
        _write(d)


def _compose(out: Path, bpm, scale, kind, secs=32.0, sr=44100):
    """A simple loop: drums, bass, chords and a little melody, all synthesised here."""
    rng = np.random.default_rng(abs(hash(kind)) % 2 ** 32)
    n = int(secs * sr)
    beat = 60.0 / bpm
    mix = np.zeros(n)
    root = 110.0 if kind != "epic" else 82.4
    steps = [0, 2, 3, 5, 7, 8, 10] if scale == "minor" else [0, 2, 4, 5, 7, 9, 11]
    prog = {"drive": [0, 5, 3, 4], "bounce": [0, 3, 4, 0], "tension": [0, 0, 5, 1], "chill": [0, 5, 3, 4], "epic": [0, 5, 2, 6]}[kind]

    def note(deg, octave=0):
        semis = steps[deg % 7] + 12 * (deg // 7 + octave)
        return root * 2 ** (semis / 12)

    def env(length, a=0.005, r=0.2):
        m = int(length * sr)
        e = np.ones(m)
        ai = max(1, int(a * sr))
        e[:ai] = np.linspace(0, 1, ai)
        e *= np.exp(-np.arange(m) / sr / max(1e-3, r))
        return e

    def put(sig, at):
        i = int(at * sr)
        j = min(n, i + len(sig))
        if i < n:
            mix[i:j] += sig[: j - i]

    bar = 4 * beat
    for b in range(int(secs / bar) + 1):
        chord = prog[b % len(prog)]
        start = b * bar
        # chords: soft detuned saw pads (sines for chill)
        for k, deg in enumerate((chord, chord + 2, chord + 4)):
            f = note(deg, 1)
            tt = np.arange(int(bar * sr)) / sr
            wave_ = (np.sin(2 * np.pi * f * tt) + 0.5 * np.sin(2 * np.pi * f * 1.003 * tt) +
                     (0.0 if kind == "chill" else 0.25 * np.sin(2 * np.pi * 2 * f * tt)))
            pad = wave_ * np.minimum(1, tt / 0.4) * np.minimum(1, (bar - tt) / 0.3) * (0.05 if kind != "epic" else 0.08)
            put(pad, start)
        # bass
        hits = {"drive": 8, "bounce": 4, "tension": 8, "chill": 2, "epic": 2}[kind]
        for h in range(hits):
            f = note(chord, -1) * (2 if (kind == "bounce" and h % 2) else 1)
            ln = bar / hits
            tt = np.arange(int(ln * sr)) / sr
            sig = np.sign(np.sin(2 * np.pi * f * tt)) * 0.12 if kind == "drive" else np.sin(2 * np.pi * f * tt) * 0.22
            put(sig * env(ln, 0.005, ln * 0.6)[: len(tt)], start + h * ln)
        # drums
        for q in range(4):
            at = start + q * beat
            if kind != "tension" or q % 2 == 0:
                kl = 0.25
                tt = np.arange(int(kl * sr)) / sr
                kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-tt * 30)) * tt) * env(kl, 0.001, 0.12)[: len(tt)]
                put(kick * (0.5 if kind == "chill" else 0.8), at)
            if q in (1, 3) and kind not in ("tension", "epic"):
                sl = 0.18
                snare = rng.normal(0, 1, int(sl * sr)) * env(sl, 0.001, 0.05)[: int(sl * sr)]
                put(snare * (0.12 if kind == "chill" else 0.25), at)
            if kind in ("drive", "bounce"):
                for e8 in (0, 0.5):
                    hl = 0.05
                    hat = np.diff(rng.normal(0, 1, int(hl * sr) + 1)) * env(hl, 0.001, 0.015)[: int(hl * sr)]
                    put(hat * 0.06, at + e8 * beat)
            if kind == "epic" and q == 0:
                tl = 1.2
                tt = np.arange(int(tl * sr)) / sr
                put(np.sin(2 * np.pi * 65 * tt) * env(tl, 0.003, 0.5)[: len(tt)] * 0.6, at)
        # a little melody
        if kind in ("bounce", "drive", "tension"):
            notes = 8 if kind != "tension" else 2
            for m in range(notes):
                deg = chord + int(rng.choice([0, 2, 4, 5, 7]))
                ln = bar / notes
                tt = np.arange(int(ln * 0.8 * sr)) / sr
                f = note(deg, 2)
                tone = (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * 3 * f * tt)) * env(ln * 0.8, 0.003, 0.08 if kind == "bounce" else 0.25)[: len(tt)]
                put(tone * 0.07, start + m * ln)
    mix /= max(1e-6, np.max(np.abs(mix))) / 0.7
    stereo = np.stack([mix, np.roll(mix, int(0.012 * sr))], axis=1)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    tmp = out.with_suffix(".wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-c:a", "aac", "-b:a", "160k", str(out)],
                       check=True, capture_output=True, creationflags=NO_WINDOW)
    finally:
        tmp.unlink(missing_ok=True)
