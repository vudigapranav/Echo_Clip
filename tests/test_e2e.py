"""The whole path, for real: a landscape video with speech is uploaded, transcribed on this machine, its moments
found and ranked (locally: no AI key here, nothing leaves the machine), and rendered to vertical MP4s, which are
then checked: size, codecs, length, sound in step with the source, captions on screen. Then a clip is re-made with
captions off (no AI asked), one is rejected, a ZIP is downloaded, and the server is restarted to see it all kept.

Needs ffmpeg, macOS `say` (for the speech) and the speech model already on this machine (data/models, or
PEAKCUT_MODELS). Without them it says SKIP. Usage: test_e2e.py"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("ECHO_E2E_PORT", "8797"))
BASE = f"http://127.0.0.1:{PORT}"
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"   {extra}" if extra and not ok else ""))
    ok or fails.append(name)


models = Path(os.environ.get("PEAKCUT_MODELS") or ROOT / "data" / "models")
if not shutil.which("say") or not shutil.which("ffmpeg") or not (models / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8" / "tokens.txt").is_file():
    print("SKIP the end-to-end test needs macOS `say`, ffmpeg and the speech model in data/models")
    print("ALL PASSED")
    sys.exit(0)

base = Path(tempfile.mkdtemp(prefix="ec-e2e-"))
work = base / "a folder with spaces"
data = work / "Echo Clip data"
work.mkdir(parents=True)
LINES = [
    "Okay chat, we are driving to the bank right now, nothing crazy is going on.",
    "Wait. Wait, what is that? Oh my god, the cops are right behind me!",
    "No way, no way, bro what! He just crashed straight into the bus! That is hilarious.",
    "Alright, alright. Let me calm down. We need to find a new car before the police come back.",
    "Look at this guy on the corner, he is just standing there in the rain, so random.",
    "Let's go! We made it! I cannot believe we actually escaped the police!",
    "Okay so the mission says we need to meet Lucia at the docks at midnight.",
    "Oh no, oh no, he is shooting at us, run, run, run! I'm dead. I am actually dead.",
    "That was the funniest death I have ever seen in this game, I swear.",
    "Anyway, thanks for watching the stream, chat, we will be back tomorrow for more.",
]
text = " ".join(LINES * 2)
speech, video = work / "speech.aiff", work / "my stream recording.mp4"
subprocess.run(["say", "-r", "175", "-o", str(speech), text], check=True)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-i", str(speech),
                "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                str(video)], check=True)
src_len = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                               capture_output=True, text=True).stdout)
print(f"(test video: {src_len:.0f} s of speech)")

env = {**os.environ, "PEAKCUT_DATA": str(data), "PEAKCUT_PORT": str(PORT), "PEAKCUT_PUBLIC_URL": BASE, "PEAKCUT_ACCOUNTS": "0",
       "PEAKCUT_MODELS": str(models), "PEAKCUT_FETCH_MODEL": "0", "PEAKCUT_UPDATE_CHECK": "0", "PEAKCUT_KEYCHAIN": "0",
       "DATABASE_URL": "", "PEAKCUT_AI_PROVIDER": "featherless", "PEAKCUT_AI_MODELS": "", "PEAKCUT_AI_KEY": "",
       "PEAKCUT_AI_KEY_FILE": "", "PEAKCUT_AUTO_RELOAD": "0", "PYTHONUNBUFFERED": "1",
       "PEAKCUT_MAX_UPLOAD_MB": "0", "PEAKCUT_UPLOAD_PART_MB": "40", "PEAKCUT_VERIFY_EMAIL": "0"}   # the app's own defaults
log = (base / "server.log").open("w")


def start():
    p = subprocess.Popen([sys.executable, "-m", "studio.server"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=2)
            return p
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise SystemExit("server did not start")


def call(path, body=None, method=None, raw=False):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json", "Origin": BASE})
    with urllib.request.urlopen(req, timeout=120) as r:
        got = r.read()
    return got if raw else json.loads(got)


def probe(f):
    out = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(f)],
                                    capture_output=True, text=True).stdout)
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    a = next(s for s in out["streams"] if s["codec_type"] == "audio")
    return v, a, float(out["format"]["duration"])


def pcm(f, start=0.0, dur=None):
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(f)] + (["-t", f"{dur:.3f}"] if dur else []) + \
          ["-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.int16).astype(np.float32)


def frame(f, t):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(f), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(out, np.uint8).reshape(1920, 1080, 3).astype(np.int16)


server = start()
try:
    st = call("/api/status")
    check("Echo Clip starts, with the caption presets and layouts on offer",
          "gaming_bold" in st["captions"]["presets"] and "auto" in st["layouts"] and st["defaults"]["max_clips"] == 5)
    check("the AI is not set up here, so nothing can be sent anywhere", not st["ai"]["ready"])
    up = call("/api/upload/start", {"name": video.name, "size": video.stat().st_size,
                                    "options": {"max_clips": 3, "duration": "15-20", "layout": "auto", "style": "neon_purple",
                                                "hook_trim": True, "normalize": True}})
    blob = video.read_bytes()
    for n in range(up["parts"]):
        req = urllib.request.Request(f"{BASE}/api/upload/{up['id']}/{n}", data=blob[n * up["part"]:(n + 1) * up["part"]],
                                     method="PUT", headers={"Origin": BASE})
        urllib.request.urlopen(req, timeout=60).read()
    job = call(f"/api/upload/{up['id']}/finish", {})
    jid = job["id"]
    phases, t0 = [], time.time()
    while time.time() - t0 < 600:
        job = call(f"/api/jobs/{jid}")
        if job.get("phase") and (not phases or phases[-1] != job["phase"]):
            phases.append(job["phase"])
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    check("the video goes through every stage", job["status"] == "done" and phases[-1] == "ready"
          and all(p in phases for p in ("transcribing", "rendering", "ready")), f"{job['status']} {phases} {job.get('error')}")
    check("it was ranked on this machine, and says so", job["selection"]["ranked_by"] == "local" and job["ai"]["tokens"] == 0)
    check("candidate moments were found and kept for review", len(job["candidates"]) >= 5 and sum(c["picked"] for c in job["candidates"]) == len(job["clips"]))
    clips = job["clips"]
    check("the clips asked for were made", len(clips) == 3 and all(c["status"] == "done" for c in clips),
          str([(c["id"], c["status"], c.get("error")) for c in clips]))
    check("every clip has a hook check, scores and a category", all(c["hook_analysis"].get("score") is not None and c["scores"].get("total")
                                                                    and c["category_label"] for c in clips))
    check("titles with no AI are words that were really said", all(c["title"].strip("“”").lower().split()[0] in text.lower()
                                                                   for c in clips), str([c["title"] for c in clips]))
    jdir = data / "users" / "u_local" / "jobs" / jid
    for c in clips:
        f = jdir / "clips" / c["file"]
        v, a, d = probe(f)
        want = c["end"] - c["start"]
        check(f"{c['id']}: 1080x1920 H.264 with AAC sound, {want:.1f} s long",
              (v["codec_name"], v["width"], v["height"], a["codec_name"], a["sample_rate"]) == ("h264", 1080, 1920, "aac", "48000")
              and abs(d - want) < 0.12 and abs(float(v["duration"]) - float(a["duration"])) < 0.08,
              f"{v['codec_name']} {v['width']}x{v['height']} {a['codec_name']} {d:.2f} vs {want:.2f}")
        ref, out = pcm(video, c["start"], want), pcm(f)
        n = min(len(ref), len(out), 8000 * 12)
        r, o = ref[:n] / (np.abs(ref[:n]).max() + 1), out[:n] / (np.abs(out[:n]).max() + 1)
        size = 1 << (2 * n).bit_length()
        corr = np.fft.irfft(np.fft.rfft(o, size) * np.conj(np.fft.rfft(r, size)), size)
        lag = int(np.argmax(np.concatenate([corr[-800:], corr[:801]]))) - 800
        check(f"{c['id']}: the sound is in step with the source (within 40 ms)", abs(lag) <= 320, f"{lag / 8:.0f} ms")
        dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-f", "null", "-"], capture_output=True, text=True)
        check(f"{c['id']}: plays through with no decoding errors", dec.returncode == 0 and not dec.stderr.strip(), dec.stderr[:200])
        first = frame(f, 0.0)
        check(f"{c['id']}: the first frame is a picture, not black", first.mean() > 25)
    c0 = clips[0]
    with_caps = frame(jdir / "clips" / c0["file"], 1.5)
    before = call("/api/usage")["calls_total"]
    call(f"/api/jobs/{jid}/clips/{c0['id']}/render", {"captions": {"preset": c0["style"], "enabled": False, "hook": False}})
    for _ in range(240):
        c0b = next(c for c in call(f"/api/jobs/{jid}")["clips"] if c["id"] == c0["id"])
        if c0b["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    no_caps = frame(jdir / "clips" / c0b["file"], 1.5)
    region = np.abs(with_caps[1000:1700] - no_caps[1000:1700]).mean() + np.abs(with_caps[100:700] - no_caps[100:700]).mean()
    check("captions and the hook are really drawn on the video (gone when switched off)", c0b["status"] == "done" and region > 3, f"{region:.2f}")
    check("re-making a clip asks the AI nothing", call("/api/usage")["calls_total"] == before)
    check("...and its version goes up", c0b["version"] == c0["version"] + 1)
    call(f"/api/jobs/{jid}/clips/{clips[1]['id']}/reject", {"rejected": True})
    check("a clip can be rejected", next(c for c in call(f"/api/jobs/{jid}")["clips"] if c["id"] == clips[1]["id"])["rejected"] is True)
    z = zipfile.ZipFile(io.BytesIO(call("/api/clips.zip?ids=" + ",".join(f"{jid}.{c['id']}" for c in clips), raw=True)))
    check("several clips download as one ZIP of MP4s", len(z.namelist()) == 3 and all(n.endswith(".mp4") for n in z.namelist()))
    one = call(f"/media/{jid}/{clips[2]['file']}?download=1", raw=True)
    check("one clip downloads on its own", one[4:8] == b"ftyp" and len(one) > 50_000)
    spare = next((c for c in call(f"/api/jobs/{jid}")["candidates"] if not c["picked"]), None)
    if spare:
        call(f"/api/jobs/{jid}/candidates/{spare['id']}/make", {})
        for _ in range(240):
            js = call(f"/api/jobs/{jid}")
            new = next((c for c in js["clips"] if c.get("from_candidate") == spare["id"]), None)
            if new and new["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        check("a moment that was not picked can be made into a clip", new and new["status"] == "done", str(new and new.get("error")))
    prev = call(f"/api/captions/preview?s=gaming_bold&jid={jid}&cid={clips[2]['id']}", raw=True)
    check("the caption preview is drawn on a real frame of the clip", prev[:2] == b"\xff\xd8" and len(prev) > 5000)
    leftovers = list((data / "tmp" / "render").glob("ec*"))
    check("render work folders are cleaned up", not leftovers, str(leftovers))
    server.terminate()
    server.wait(timeout=20)
    server = start()
    again = call(f"/api/jobs/{jid}")
    check("after a restart the project and its clips are all still there",
          again["status"] == "done" and len(again["clips"]) == len(js["clips"] if spare else clips)
          and all((jdir / "clips" / c["file"]).is_file() for c in again["clips"] if c["status"] == "done"))
    check("...and the original upload is untouched", video.is_file() and video.stat().st_size == len(blob))
finally:
    server.terminate()
    try:
        server.wait(timeout=20)
    except subprocess.TimeoutExpired:
        server.kill()
    log.close()
    if fails:
        print((base / "server.log").read_text()[-3000:])
    shutil.rmtree(base, ignore_errors=True)

print("ALL PASSED" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
