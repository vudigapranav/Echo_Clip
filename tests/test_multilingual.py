"""English, Hindi and Hinglish, all the way: real speech (made by this Mac's own Hindi and English voices),
transcribed on this machine, its language detected, ranked through the real AI request path, and rendered with
captions in the spoken language, translated, bilingual, with headlines in three languages and background music.

The AI is a local stand-in that answers in the same OpenAI-compatible format Featherless uses (so the real request
path is used, and nothing is spent). Its "translations" are a crude word-by-word gloss: this test checks what is
sent, kept, placed and synchronised, never the quality of a translation. Transcription here is real.

Needs ffmpeg, macOS `say` with the Hindi voice "Lekha" and an English voice, text shaping (brew install fribidi),
and the speech models (data/models). Without them it says SKIP. Usage: test_multilingual.py"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PORT = int(os.environ.get("ECHO_ML_PORT", "8795"))
BASE = f"http://127.0.0.1:{PORT}"
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"   {extra}" if extra and not ok else ""))
    ok or fails.append(name)


models = Path(os.environ.get("PEAKCUT_MODELS") or ROOT / "data" / "models")
voices = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout if shutil.which("say") else ""
from studio import shaping  # noqa: E402

if "Lekha" not in voices or not (models / "whisper").is_dir() or not (models / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8").is_dir():
    print("SKIP needs macOS `say` with the Lekha (Hindi) voice and both speech models in data/models")
    print("ALL PASSED")
    sys.exit(0)

base = Path(tempfile.mkdtemp(prefix="ec-ml-"))
P = "[[slnc 1300]]"                       # a pause, so there are quiet stretches between lines
HINDI = [
    "अरे भाई, ये क्या बकवास है! मैंने सोचा था पुलिस पीछे नहीं आएगी।",
    "वाह भाई वाह, क्या ड्राइविंग है, सीधा खंभे में। लाइसेंस किसने दिया तुझे?",
    "ये बंदा तो ऐसे भाग रहा है जैसे इसकी मम्मी ने रिपोर्ट कार्ड देख लिया हो।",
    "चलो बैंक लूटते हैं, क्या ही हो जाएगा।",
    "पुलिस आ गई! भागो भागो! गाड़ी निकालो जल्दी!",
    "बस भाई, मैं तो गया। ये गेम मुझसे नफरत करता है।",
    "हा हा हा, ये तो कमाल हो गया! इतिहास में लिखा जाएगा।",
]
ENGLISH = [
    "Okay chat, we are driving to the bank right now, nothing crazy is going on.",
    "Wait, what is that? Oh my god, the cops are right behind me!",
    "No way, he just crashed straight into the bus. That is hilarious.",
    "Look at this guy on the corner, he is just standing there in the rain.",
    "Let's go! We made it! I cannot believe we actually escaped the police!",
    "That was the funniest death I have ever seen in this game, I swear.",
]
HINGLISH = [("Lekha", "भाई, this is actually the craziest thing I have ever seen! मतलब seriously, पुलिस वाले ने मेरी कार को उड़ा दिया।"),
            ("Rishi", "Bro, I told you not to drive into the police station, but you never listen."),
            ("Lekha", "अरे यार, mission fail हो गया, फिर से करना पड़ेगा। Honestly ये game मुझसे नफरत करता है।"),
            ("Rishi", "Okay okay, one more try. This time we go slow and we do not crash the car."),
            ("Lekha", "भाई ये देख, फिर से crash! हा हा हा, मैं हार गया।")]


def speak(lines, out, voice):
    subprocess.run(["say", "-v", voice, "-r", "165", "-o", str(out), (" " + P + " ").join(lines * 2)], check=True)


def video(audio_files, out):
    lst = base / (out.stem + ".txt")
    lst.write_text("".join(f"file '{a}'\n" for a in audio_files))
    joined = base / (out.stem + ".wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-af", "apad=pad_dur=3",
                    "-ar", "48000", "-ac", "2", str(joined)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-i", str(joined),
                    "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(out)], check=True)
    return out


speak(HINDI, base / "hi.aiff", "Lekha")
speak(ENGLISH, base / "en.aiff", "Samantha" if "Samantha" in voices else "Alex")
parts = []
for i, (v, line) in enumerate(HINGLISH * 2):
    f = base / f"hg{i}.aiff"
    subprocess.run(["say", "-v", v if v in voices else "Lekha", "-r", "165", "-o", str(f), line + " " + P], check=True)
    parts.append(f)
V = {"hi": video([base / "hi.aiff"], base / "hindi stream.mp4"), "en": video([base / "en.aiff"], base / "english stream.mp4"),
     "hinglish": video(parts, base / "hinglish stream.mp4")}


# ================================================================ the stand-in AI (OpenAI chat format)
GLOSS = {"अरे": "hey", "भाई": "bro", "ये": "this", "क्या": "what", "बकवास": "nonsense", "है": "is", "मैंने": "I",
         "सोचा": "thought", "पुलिस": "police", "पीछे": "behind", "नहीं": "not", "आएगी": "will come", "वाह": "wow",
         "ड्राइविंग": "driving", "सीधा": "straight", "खंभे": "pole", "में": "into", "लाइसेंस": "licence", "किसने": "who",
         "दिया": "gave", "तुझे": "you", "बंदा": "guy", "भाग": "run", "रहा": "is", "गाड़ी": "car", "भागो": "run",
         "गया": "gone", "गेम": "game", "कमाल": "amazing", "हा": "ha", "बैंक": "bank", "जल्दी": "quick"}
log = {"rank": [], "translate": [], "tidy": [], "other": []}


class AI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][0]["content"]
        if "You rank moments" in prompt:
            log["rank"].append(prompt)
            ids = re.findall(r"^(c\d\d) ", prompt.split("<candidates>")[1], re.M)
            items = []
            for k, cid in enumerate(ids[:4]):
                items.append({"id": cid, "entertainment": 88 - k * 5, "reaction": 80, "surprise": 75, "hook": 78, "context": 80,
                              "replay": 70, "category": "funny", "title": f"Moment {k + 1}", "hook_text": "BRO NO WAY",
                              "reason": "A joke that lands.", "highlight_words": ["भाई", "police"], "captions": ["LOL"],
                              "opening": {"engagement": 80, "curiosity": 70, "emotion": 80, "clarity": 80, "payoff": 70},
                              "ending": "strong", "issues": ["missing_punchline"] if k == 1 else [],
                              "headline_en": "HIS OWN GAME ROASTED HIM 😂", "headline_hi": "भाई का गेम ने ही मज़ाक उड़ा दिया 😂",
                              "headline_hinglish": "Bhai ka game ne hi roast kar diya 😂"})
            items.append({"id": "c99", "entertainment": 100, "reaction": 100, "surprise": 100, "hook": 100, "context": 100,
                          "replay": 100, "category": "funny", "title": "made up", "reason": "made up"})
            answer = {"items": items}
        elif "Translate each into" in prompt:
            log["translate"].append(prompt)
            lines = re.findall(r"^\d+: (.*)$", prompt.split("Phrases (")[1], re.M)
            to_en = "spoken English" in prompt
            answer = {"items": [" ".join(GLOSS.get(w.strip("।,!?."), w) for w in l.split()) if to_en
                                else "हिंदी: " + l for l in lines]}
        elif "Rewrite each word" in prompt:
            log["tidy"].append(prompt)
            answer = {"items": re.findall(r"^\d+: (.*)$", prompt.split("Words (")[1], re.M)}
        else:
            log["other"].append(prompt)
            answer = {"items": []}
        out = json.dumps({"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 120}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


mock = ThreadingHTTPServer(("127.0.0.1", 0), AI)
threading.Thread(target=mock.serve_forever, daemon=True).start()
MOCK = f"http://127.0.0.1:{mock.server_address[1]}/v1"

data = base / "Echo Clip data"
env = {**shaping.env_for_children(), "PEAKCUT_DATA": str(data), "PEAKCUT_PORT": str(PORT), "PEAKCUT_PUBLIC_URL": BASE,
       "PEAKCUT_ACCOUNTS": "0", "PEAKCUT_MODELS": str(models), "PEAKCUT_FETCH_MODEL": "0", "PEAKCUT_UPDATE_CHECK": "0",
       "PEAKCUT_KEYCHAIN": "0", "DATABASE_URL": "", "PEAKCUT_AI_PROVIDER": "custom", "PEAKCUT_AI_BASE_URL": MOCK,
       "PEAKCUT_AI_MODELS": "stand-in", "PEAKCUT_AI_KEY": "", "PEAKCUT_AI_KEY_FILE": "", "PYTHONUNBUFFERED": "1",
       "PEAKCUT_MAX_UPLOAD_MB": "0", "PEAKCUT_UPLOAD_PART_MB": "40", "PEAKCUT_VERIFY_EMAIL": "0"}
slog = (base / "server.log").open("w")
server = subprocess.Popen([sys.executable, "-m", "studio.server"], cwd=ROOT, env=env, stdout=slog, stderr=subprocess.STDOUT)


def call(path, body=None, method=None, raw=False):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", "Origin": BASE})
    with urllib.request.urlopen(req, timeout=600) as r:
        got = r.read()
    return got if raw else json.loads(got)


def upload(f, opts):
    blob = f.read_bytes()
    s = call("/api/upload/start", {"name": f.name, "size": len(blob), "options": opts})
    for n in range(s["parts"]):
        urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/upload/{s['id']}/{n}", data=blob[n * s["part"]:(n + 1) * s["part"]],
                                                      method="PUT", headers={"Origin": BASE}), timeout=60).read()
    return call(f"/api/upload/{s['id']}/finish", {})


def wait(jid, cid=None, version=None):
    for _ in range(1200):
        j = call(f"/api/jobs/{jid}")
        if cid:
            c = next(x for x in j["clips"] if x["id"] == cid)
            if c["status"] in ("done", "error") and (version is None or c.get("version", 0) > version):
                return j
        elif j["status"] in ("done", "error"):
            return j
        time.sleep(0.5)
    return j


def probe(f):
    out = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(f)],
                                    capture_output=True, text=True).stdout)
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    a = next(s for s in out["streams"] if s["codec_type"] == "audio")
    return v, a, float(out["format"]["duration"])


def pcm(f, start=0.0, dur=None, rate=8000):
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(f)] + (["-t", f"{dur:.3f}"] if dur else []) + \
          ["-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.int16).astype(np.float32)


def frame(f, t):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(f), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(out, np.uint8).reshape(1920, 1080, 3).astype(np.int16)


def jdir(jid):
    return data / "users" / "u_local" / "jobs" / jid


def verify_mp4(label, job, clip):
    f = jdir(job["id"]) / "clips" / clip["file"]
    v, a, d = probe(f)
    want = clip["end"] - clip["start"]
    check(f"{label}: 1080x1920 H.264 + AAC, {want:.1f} s", (v["codec_name"], v["width"], v["height"], a["codec_name"]) ==
          ("h264", 1080, 1920, "aac") and abs(d - want) < 0.12, f"{v['width']}x{v['height']} {d:.2f}")
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-f", "null", "-"], capture_output=True, text=True)
    check(f"{label}: plays through with no decoding errors", dec.returncode == 0 and not dec.stderr.strip(), dec.stderr[:160])
    src = Path(job["source_path"]) if job.get("source_path") else None
    if src is None:
        src = next(jdir(job["id"]).glob("source.*"))
    ref, out = pcm(src, clip["start"], want), pcm(f)
    n = min(len(ref), len(out), 8000 * 12)
    r, o = ref[:n] / (np.abs(ref[:n]).max() + 1), out[:n] / (np.abs(out[:n]).max() + 1)
    size = 1 << (2 * n).bit_length()
    corr = np.fft.irfft(np.fft.rfft(o, size) * np.conj(np.fft.rfft(r, size)), size)
    lag = int(np.argmax(np.concatenate([corr[-800:], corr[:801]]))) - 800
    check(f"{label}: sound in step with the source (within 40 ms)", abs(lag) <= 320, f"{lag / 8:.0f} ms")
    return f


def words_of(job):
    return json.loads((jdir(job["id"]) / "words.json").read_text(encoding="utf-8"))


def similarity(a, b):
    import difflib
    norm = lambda s: re.sub(r"[^\wऀ-ॿ]+", " ", s.lower()).split()  # noqa: E731
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


try:
    for _ in range(90):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    st = call("/api/status")
    check("Hindi text shaping is on in the running app", st["hindi_text_ok"] is True)
    check("the caption languages, headline and music settings are offered",
          set(st["caption_langs"]) >= {"original", "hi", "en", "bilingual"} and "hinglish" in st["headline"]["langs"]
          and "funny" in st["music"]["categories"])
    common = {"max_clips": 2, "duration": "15-20", "layout": "auto", "music": {"enabled": True, "volume": 14}}
    jobs = {}
    for key, opts in (("en", {"caption_lang": "original", "headline_lang": "en"}),
                      ("hi", {"caption_lang": "original", "headline_lang": "source", "style": "neon_purple",
                              "headline": {"style": "neon", "duration": 3}}),
                      ("hinglish", {"caption_lang": "original", "headline_lang": "hinglish"})):
        j = upload(V[key], {**common, **opts})
        jobs[key] = wait(j["id"])
        check(f"{key}: the video was processed", jobs[key]["status"] == "done" and any(c["status"] == "done" for c in jobs[key]["clips"]),
              str((jobs[key]["status"], jobs[key].get("error"), [(c["id"], c["status"], c.get("error")) for c in jobs[key]["clips"]])))
    en, hi, hg = jobs["en"], jobs["hi"], jobs["hinglish"]

    # ---- language detection and transcription (real, on this machine)
    check("English is detected and written by the English engine", en["lang"] == "en" and en["lang_info"]["engine"] == "parakeet", str(en["lang_info"]))
    check("Hindi is detected automatically, with no setting chosen", hi["lang"] == "hi" and hi["lang_info"]["probability"] > 0.8, str(hi["lang_info"]))
    check("Hinglish (two speakers, Hindi and English) is detected as Hinglish", hg["lang"] == "hinglish", str(hg["lang_info"]))
    hw, ew, gw = words_of(hi), words_of(en), words_of(hg)
    htxt, etxt, gtxt = " ".join(w["w"] for w in hw), " ".join(w["w"] for w in ew), " ".join(w["w"] for w in gw)
    check("the Hindi transcript is in Devanagari, never Roman letters or English", len(re.findall(r"[ऀ-ॿ]", htxt)) > 0.7 * len(re.sub(r"\W", "", htxt)))
    sim = similarity(htxt, " ".join(HINDI * 2))
    check(f"the Hindi transcript matches what was said (word match {sim:.0%})", sim >= 0.6, htxt[:200])
    for word in ("पुलिस", "भाई", "बैंक", "गेम"):
        check(f"Hindi word heard: {word}", word in htxt)
    check(f"the English transcript matches what was said (word match {similarity(etxt, ' '.join(ENGLISH * 2)):.0%})",
          similarity(etxt, " ".join(ENGLISH * 2)) >= 0.8)
    latin = [w["w"] for w in gw if re.search(r"[a-zA-Z]", w["w"])]
    deva = [w["w"] for w in gw if re.search(r"[ऀ-ॿ]", w["w"])]
    check(f"Hinglish keeps both scripts: Hindi words in Devanagari ({len(deva)}) and English words in Latin ({len(latin)})",
          len(deva) >= 10 and len(latin) >= 10, gtxt[:200])
    check("...English sentences stay English, not translated", "police" in gtxt.lower() and ("drive" in gtxt.lower() or "crash" in gtxt.lower()))
    for lbl, ws in (("Hindi", hw), ("English", ew), ("Hinglish", gw)):
        check(f"{lbl}: every word has a start and end time, in order", all(w["e"] > w["s"] >= 0 for w in ws)
              and all(a["s"] <= b["s"] for a, b in zip(ws, ws[1:])))
    check("Whisper's confidence is kept with each Hindi word", all("p" in w for w in hw))
    check("the original transcript is kept, never rewritten by a caption language", (jdir(hi["id"]) / "words.json").is_file())

    # ---- ranking: one request per video, in the language spoken
    check("one ranking request per video", len(log["rank"]) == 3, str(len(log["rank"])))
    hp = next(p for p in log["rank"] if "Spoken language: Hindi" in p)
    check("the Hindi video is ranked in Hindi: its Devanagari words are sent as they are, with the Hindi note",
          re.search(r"[ऀ-ॿ]{3,}", hp.split("<candidates>")[1]) and "jokes, sarcasm, roasts" in hp and "Do not translate first" in hp)
    gp = next(p for p in log["rank"] if "Spoken language: Hinglish" in p)
    check("the Hinglish video is ranked as Hinglish", "the switch between the two" in gp)
    check("only text is sent: no audio, video or folder paths (an upload's title is its file name, and that is all)",
          all(str(data) not in p and "/Users/" not in p and "\x00" not in p and len(p) < 60000 for p in log["rank"]))
    hc = hi["clips"][0]
    check("the AI's headlines come back in English, Hindi and Hinglish", set(hc["headlines"]) == {"en", "hi", "hinglish"}, str(hc["headlines"]))
    pen = [c for c in hi["clips"] if c.get("issues")]
    check("a problem the AI names (missing punchline) takes points off, worked out here",
          not pen or all(c["scores"]["total"] == c["scores"]["weighted"] - 15 for c in pen))
    check("a candidate id the AI made up is ignored", all(c["from_candidate"] != "c99" for j in jobs.values() for c in j["clips"]))
    check("no translation was asked for while captions are in the spoken language", len(log["translate"]) == 0)

    # ---- 1. English video, English captions, English headline, music
    ec = next(c for c in en["clips"] if c["status"] == "done")
    f_en = verify_mp4("English video, English captions", en, ec)
    check("English headline shown", ec["headline_shown"] == "HIS OWN GAME ROASTED HIM 😂", ec["headline_shown"])
    check("music was picked for the moment", ec["music_used"] and ec["music_used"]["title"] == "Goofy Bounce", str(ec["music_used"]))

    # ---- 2. Hindi video, Hindi captions, Hindi headline (the video's own language), music
    f_hi = verify_mp4("Hindi video, Hindi captions", hi, hc)
    check("Hindi headline shown (follows the spoken language)", hc["headline_shown"] == "भाई का गेम ने ही मज़ाक उड़ा दिया 😂")
    purple = lambda fr: int((np.abs(fr[:700] - np.array([168, 85, 247])).sum(axis=2) < 60).sum())  # noqa: E731
    on, off = purple(frame(f_hi, 1.2)), purple(frame(f_hi, min(hc["end"] - hc["start"] - 0.5, 6.0)))
    check(f"the headline is on screen at the start and gone after its 3 seconds ({on} vs {off} purple pixels)", on > 20000 and off < on / 10)
    yellow = 0
    for t in np.arange(0.5, hc["end"] - hc["start"], 0.7):
        fr = frame(f_hi, t)
        yellow = max(yellow, int((np.abs(fr - np.array([199, 125, 255])).sum(axis=2) < 50).sum()))
    check("Hindi captions are drawn while the words are said", yellow > 300, str(yellow))
    shutil.copy(f_hi, base / "keep_hi_original.mp4")

    # ---- 3. the same Hindi clip, English captions: one translation request, ranking not asked again
    v0 = hc["version"]
    call(f"/api/jobs/{hi['id']}/clips/{hc['id']}/render", {"caption_lang": "en", "headline": {"lang": "en"}})
    hi = wait(hi["id"], hc["id"], v0)
    hc = next(c for c in hi["clips"] if c["id"] == hc["id"])
    f_hi_en = verify_mp4("Hindi video, English captions", hi, hc)
    check("translating the captions took one AI request, and ranking was not asked again",
          len(log["translate"]) == 1 and len(log["rank"]) == 3, f"{len(log['translate'])} / {len(log['rank'])}")
    check("the translation request carries the Hindi phrases and asks to keep the joke", "Hindi" in log["translate"][0]
          and "Keep the meaning, the joke, the sarcasm" in log["translate"][0] and re.search(r"[ऀ-ॿ]", log["translate"][0]))
    caps = call(f"/api/jobs/{hi['id']}/clips/{hc['id']}/captions?mode=en")
    check("each English line sits on the time of the Hindi phrase it translates, with the original kept to compare",
          caps["to"] == "en" and caps["lines"] and all(l["translation"] and re.search(r"[ऀ-ॿ]", l["spoken"]) for l in caps["lines"])
          and not caps["missing"], str(caps["lines"][:2]))
    check("English headline on the Hindi video", hc["headline_shown"] == "HIS OWN GAME ROASTED HIM 😂")
    check("no caption warning on the clip", not hc.get("caption_note"), hc.get("caption_note"))
    shutil.copy(f_hi_en, base / "keep_hi_en.mp4")

    # ---- 4. bilingual, Hindi on top: the stored translation is reused (no new request)
    v0 = hc["version"]
    call(f"/api/jobs/{hi['id']}/clips/{hc['id']}/render", {"caption_lang": "bilingual", "caption_order": "hi_first",
                                                            "headline": {"lang": "hi"}})
    hi = wait(hi["id"], hc["id"], v0)
    hc = next(c for c in hi["clips"] if c["id"] == hc["id"])
    f_bi = verify_mp4("Hindi video, bilingual captions", hi, hc)
    check("bilingual captions reuse the stored translation: still one translation request", len(log["translate"]) == 1)
    shutil.copy(f_bi, base / "keep_bi.mp4")

    # ---- music: audible in the pauses, lowered while someone talks; the voice stays clear
    v0 = hc["version"]
    call(f"/api/jobs/{hi['id']}/clips/{hc['id']}/render", {"music": {"enabled": False}, "normalize": False})
    hi = wait(hi["id"], hc["id"], v0)
    shutil.copy(jdir(hi["id"]) / "clips" / hc["file"], base / "nomusic.mp4")
    v0 = next(c for c in hi["clips"] if c["id"] == hc["id"])["version"]
    call(f"/api/jobs/{hi['id']}/clips/{hc['id']}/render", {"music": {"enabled": True, "volume": 14, "duck": True}, "normalize": False})
    hi = wait(hi["id"], hc["id"], v0)
    hc = next(c for c in hi["clips"] if c["id"] == hc["id"])
    a_on, a_off = pcm(jdir(hi["id"]) / "clips" / hc["file"], rate=16000), pcm(base / "nomusic.mp4", rate=16000)
    n = min(len(a_on), len(a_off))
    m = a_on[:n] - a_off[:n]                                    # what the music added
    ws = [w for w in hw if hc["start"] <= w["s"] < hc["end"]]
    talk = np.zeros(n, bool)
    for w in ws:
        talk[int((w["s"] - hc["start"]) * 16000):int((w["e"] - hc["start"]) * 16000)] = True
    quiet = ~talk
    quiet[:int(1.5 * 16000)] = False                            # leave out the fade-in and fade-out
    quiet[-int(2 * 16000):] = False
    rms = lambda x: float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0  # noqa: E731
    ducked = 20 * np.log10((rms(m[talk]) + 1e-6) / (rms(m[quiet]) + 1e-6))
    check(f"the music is audible in the pauses ({20 * np.log10(rms(m[quiet]) / 32768 + 1e-9):.0f} dBFS)", rms(m[quiet]) > 150)
    check(f"the music is lowered while someone talks ({ducked:.1f} dB)", ducked <= -6, f"{ducked:.1f} dB")
    voice, under = rms(a_off[:n][talk]), rms(m[talk])
    check(f"the voice stays well above the music ({20 * np.log10(voice / (under + 1e-6)):.0f} dB clearer)", voice / (under + 1e-6) > 4)
    check("no clipping in the mix", float(np.abs(a_on).max()) < 32700)
    prev = call(f"/api/jobs/{hi['id']}/clips/{hc['id']}/audio-preview?m=" + urllib.request.quote(json.dumps({"volume": 20})), raw=True)
    check("the sound mix can be listened to before rendering", len(prev) > 20000)

    # ---- 5. Hinglish video, captions as spoken (both scripts), Hinglish headline
    gc = next(c for c in hg["clips"] if c["status"] == "done")
    f_hg = verify_mp4("Hinglish video, captions as spoken", hg, gc)
    check("Hinglish headline shown", gc["headline_shown"] == "Bhai ka game ne hi roast kar diya 😂")
    shutil.copy(f_hg, base / "keep_hinglish.mp4")
    # ---- headline edited by hand: no AI request
    before = (len(log["rank"]), len(log["translate"]))
    v0 = gc["version"]
    call(f"/api/jobs/{hg['id']}/clips/{gc['id']}/render", {"headline": {"text": "मेरा अपना हेडलाइन 🔥", "anim": "fade"}})
    hg = wait(hg["id"], gc["id"], v0)
    gc = next(c for c in hg["clips"] if c["id"] == gc["id"])
    check("a headline written by hand is used, and no AI request is made", gc["headline_shown"] == "मेरा अपना हेडलाइन 🔥"
          and (len(log["rank"]), len(log["translate"])) == before)
    check("caption tidying was never asked for Hindi or Hinglish words", all("भाई" not in p for p in log["tidy"]))
    # contact sheets for a person to look at (Hindi shaping, bilingual layout, headlines)
    sheet = base / "sheet.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.2", "-i", str(base / "keep_hi_original.mp4"), "-ss", "1.2", "-i",
                    str(base / "keep_hi_en.mp4"), "-ss", "1.2", "-i", str(base / "keep_bi.mp4"), "-ss", "1.2", "-i",
                    str(base / "keep_hinglish.mp4"), "-ss", "1.2", "-i", str(f_en), "-filter_complex",
                    "[0:v]scale=270:480[a];[1:v]scale=270:480[b];[2:v]scale=270:480[c];[3:v]scale=270:480[d];[4:v]scale=270:480[e];"
                    "[a][b][c][d][e]hstack=5", "-frames:v", "1", str(sheet)], check=True)
    keep = os.environ.get("ECHO_ML_KEEP")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        for f in [sheet, *base.glob("keep_*.mp4"), f_en]:
            shutil.copy(f, Path(keep) / f.name)
finally:
    server.terminate()
    try:
        server.wait(timeout=20)
    except subprocess.TimeoutExpired:
        server.kill()
    slog.close()
    mock.shutdown()
    if fails:
        print((base / "server.log").read_text()[-3000:])
    shutil.rmtree(base, ignore_errors=True)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
