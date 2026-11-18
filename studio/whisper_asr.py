"""Speech in any language: Whisper large-v3-turbo through faster-whisper (CTranslate2), on this machine.

Parakeet (transcribe.py) stays the engine for English: it is quicker and its word timings are exact. Everything
else (Hindi, Hinglish, Urdu, Spanish...) is written by Whisper, which also says which language is spoken.

Measured on an M4 MacBook Air (2026-11-16), with speech made by the system's Hindi and English voices:
  Hindi       detected "hi" at 0.97, written in Devanagari with word timings (a few words off where the voice
              itself was unclear)
  Hinglish    detected "en" 0.68 / "hi" 0.27; written with language "hi" it keeps the mix exactly:
              "भाई, this is actually the craziest thing I have ever seen. मतलब seriously, पुलिस वाले ने मेरी कार…"
  English     word-perfect
  speed       about 1.6× faster on the Apple GPU (Metal) than on the processors; ~2 GB of memory; model 1.5 GB

The model is fetched once into the models folder the first time it is needed (about 1.6 GB).

    PEAKCUT_WHISPER_MODEL    large-v3-turbo (default), or e.g. "small" on a machine short of room (worse at Hindi)
    PEAKCUT_WHISPER_DEVICE   auto (Apple GPU when it works, else the processors), mps, cpu
"""
import os
import sys
import threading
import warnings

import numpy as np

from . import config

MODEL = os.environ.get("PEAKCUT_WHISPER_MODEL", "large-v3-turbo").strip() or "large-v3-turbo"
DEVICE = os.environ.get("PEAKCUT_WHISPER_DEVICE", "auto").strip().lower() or "auto"
FOLDER = config.MODELS_DIR / "whisper"
_lock = threading.Lock()                 # one copy of the model, used by one video at a time
_model = {}
state = {"doing": False, "error": "", "device": ""}

# Hindi and Urdu are close as spoken; Whisper often hesitates between them. Both are treated as one family when
# deciding how to write a video down, and Devanagari is used unless Urdu clearly wins.
HINDI_FAMILY = ("hi", "ur")
NAMES = {"en": "English", "hi": "Hindi", "hinglish": "Hinglish (Hindi + English)", "ur": "Urdu", "bn": "Bengali",
         "ta": "Tamil", "te": "Telugu", "mr": "Marathi", "pa": "Punjabi", "gu": "Gujarati", "es": "Spanish",
         "fr": "French", "de": "German", "pt": "Portuguese", "ja": "Japanese", "ko": "Korean", "ru": "Russian",
         "ar": "Arabic", "id": "Indonesian", "it": "Italian", "tr": "Turkish"}


def name(lang: str) -> str:
    return NAMES.get(lang or "", (lang or "unknown").upper())


def ready() -> bool:
    """Is the model on this machine already (no download needed)?"""
    return FOLDER.is_dir() and any(FOLDER.glob("**/model.bin"))


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _load():
    if "m" in _model:
        return _model["m"]
    from faster_whisper import WhisperModel
    FOLDER.mkdir(parents=True, exist_ok=True)
    if not ready():
        state.update(doing=True, error="")
        print(f"\n  Downloading the multilingual speech model ({MODEL}, about 1.6 GB, once)...\n", flush=True)
    tries = []
    if DEVICE in ("auto", "mps") and sys.platform == "darwin":
        tries.append(("mps", "float16"))
    if DEVICE in ("auto", "cpu") or not tries:
        tries.append(("cpu", "int8"))
    err = None
    for dev, ct in tries:
        try:
            m = WhisperModel(MODEL, device=dev, compute_type=ct, cpu_threads=max(2, (os.cpu_count() or 4) - 2),
                             download_root=str(FOLDER))
            if dev == "mps":                      # the Apple GPU path is new: proven on a second of silence first
                with warnings.catch_warnings(), np.errstate(all="ignore"):
                    warnings.simplefilter("ignore")
                    list(m.transcribe(np.zeros(16000, np.float32), language="en")[0])
            _model["m"] = m
            state.update(device=dev, doing=False, error="")
            return m
        except Exception as e:  # noqa: BLE001 - the next way of running it is tried
            err = e
    state.update(doing=False, error=str(err)[-200:])
    raise RuntimeError("Echo Clip couldn't load its multilingual speech model (needed for Hindi and other languages). "
                       "Check the internet connection (it is downloaded once, about 1.6 GB) and free disk space.") from err


def _f32(audio) -> np.ndarray:
    a = np.asarray(audio)
    return (a.astype(np.float32) / 32768.0) if a.dtype == np.int16 else a.astype(np.float32)


def _speechy_windows(audio: np.ndarray, n=5, win=30):
    """Up to n 30-second stretches with the most sound in them, spread over the whole recording."""
    size = win * 16000
    if len(audio) <= size:
        return [audio]
    count = len(audio) // size
    picks = []
    for k in range(n):                                  # one per part of the video, the loudest window in that part
        lo, hi = k * count // n, max(k * count // n + 1, (k + 1) * count // n)
        best, best_e = None, -1.0
        for i in range(lo, hi):
            seg = audio[i * size:(i + 1) * size]
            e = float(np.sqrt(np.mean(seg.astype(np.float32) ** 2))) if len(seg) else 0.0
            if e > best_e:
                best, best_e = seg, e
        if best is not None:
            picks.append(best)
    return picks or [audio[:size]]


def detect(audio) -> dict:
    """Which language is spoken, from a few stretches of the recording. Returns
    {"lang": "en"|"hi"|"hinglish"|<other code>, "whisper_lang": the code to write it down with,
     "probability": 0..1, "probs": {code: share}, "engine": "parakeet"|"whisper"}"""
    a = _f32(audio)
    with _lock:
        m = _load()
        total: dict = {}
        peak = 0.0                                       # the most Hindi any one stretch had
        wins = _speechy_windows(a)
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore")
            for w in wins:
                _lang, _p, allp = m.detect_language(w)
                share = dict(allp)
                peak = max(peak, share.get("hi", 0.0) + share.get("ur", 0.0))
                for code, p in allp:
                    total[code] = total.get(code, 0.0) + p / len(wins)
    return classify(total, peak)


def classify(probs: dict, peak_hindi: float = 0.0) -> dict:
    """From language shares to how the video is written down. Hinglish (English with a real share of Hindi, or
    Hindi with English in it) is written with Hindi as the language: Whisper then keeps Hindi words in Devanagari
    and English words in Latin letters, the way the creator said them.

    Calibrated on 2026-11-16: English (American and Indian voices) gives Hindi a share of 0.000; Hinglish gives it
    0.04 to 0.29 even where English words are most of what is said. So any real share of Hindi means Hinglish."""
    probs = {k: round(float(v), 3) for k, v in sorted(probs.items(), key=lambda kv: -kv[1])[:6]}
    en = probs.get("en", 0.0)
    hi, ur = probs.get("hi", 0.0), probs.get("ur", 0.0)
    fam = hi + ur
    top = next(iter(probs), "en")
    script = "ur" if ur > hi * 1.5 else "hi"
    if en >= 0.5 and fam < 0.02 and peak_hindi < 0.1:
        out = {"lang": "en", "whisper_lang": "en", "probability": en, "engine": "parakeet"}
    elif (fam >= 0.02 or peak_hindi >= 0.1) and en >= fam:
        out = {"lang": "hinglish" if script == "hi" else "ur", "whisper_lang": script, "probability": round(en + fam, 3),
               "engine": "whisper"}
    elif fam > en and fam >= 0.3:
        out = {"lang": "hi" if script == "hi" else "ur", "whisper_lang": script, "probability": fam, "engine": "whisper"}
    elif top == "en":
        out = {"lang": "en", "whisper_lang": "en", "probability": en, "engine": "parakeet"}
    else:
        out = {"lang": top, "whisper_lang": top, "probability": probs.get(top, 0.0), "engine": "whisper"}
    out["probs"] = probs
    return out


def for_override(lang: str) -> dict:
    """The owner chose the language: how it is written down."""
    lang = (lang or "").lower()
    if lang == "en":
        return {"lang": "en", "whisper_lang": "en", "probability": 1.0, "engine": "parakeet", "probs": {}, "override": True}
    w = "hi" if lang in ("hi", "hinglish") else lang
    return {"lang": lang, "whisper_lang": w, "probability": 1.0, "engine": "whisper", "probs": {}, "override": True}


def transcribe(audio, language: str, on_progress=None) -> list:
    """[{'w', 's', 'e', 'p'}] for 16 kHz audio, in the given language. 'p' is Whisper's confidence in the word."""
    a = _f32(audio)
    dur = max(0.1, len(a) / 16000)
    out = []
    with _lock:
        m = _load()
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore")
            segs, _info = m.transcribe(a, language=language, word_timestamps=True, vad_filter=True,
                                       vad_parameters={"min_silence_duration_ms": 500}, condition_on_previous_text=False,
                                       beam_size=5)
            for seg in segs:
                for w in seg.words or []:
                    t = (w.word or "").strip()
                    if t:
                        out.append({"w": t, "s": round(float(w.start), 2), "e": round(float(w.end), 2),
                                    "p": round(float(w.probability or 0), 2)})
                if on_progress:
                    on_progress(min(1.0, float(seg.end) / dur))
    for i, w in enumerate(out):                   # a word lasts until the next starts, capped so pauses stay pauses
        nxt = out[i + 1]["s"] if i + 1 < len(out) else w["e"] + 0.4
        w["e"] = round(max(w["s"] + 0.08, min(nxt, max(w["e"], w["s"] + 0.6))), 2)
    return out
