"""Uploading a video from this computer: every format Echo Clip takes, the files it must refuse (and why),
uploads cut off half way and carried on, no room on the drive, cancelling, and proof that an uploaded video goes
through exactly the same pipeline as one fetched from a link. Synthetic videos only; nothing leaves the machine.

Part 1 runs against its own server. Part 2 runs the pipeline in this process, with the download replaced by a copy
of the same file, to compare a "link" video with an uploaded one. Needs ffmpeg, macOS `say` and the speech model
(data/models); without them it says SKIP. Usage: test_uploads.py"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("ECHO_UP_PORT", "8796"))
BASE = f"http://127.0.0.1:{PORT}"
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"   {extra}" if extra and not ok else ""))
    ok or fails.append(name)


models = Path(os.environ.get("PEAKCUT_MODELS") or ROOT / "data" / "models")
if not shutil.which("say") or not (models / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8" / "tokens.txt").is_file():
    print("SKIP the upload test needs macOS `say` and the speech model in data/models")
    print("ALL PASSED")
    sys.exit(0)

base = Path(tempfile.mkdtemp(prefix="ec-up-"))
vids = base / "my videos"
vids.mkdir()
SPEECH = base / "speech.aiff"
subprocess.run(["say", "-r", "180", "-o", str(SPEECH),
                "Oh my god the cops are right behind me. No way, he just crashed into the bus! That is hilarious. "
                "Okay we made it, let's go! I cannot believe we escaped the police. Wait, what is that? Run, run, run!"],
               check=True)


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def make(name, vcodec, acodec, extra=(), size="640x360", secs=None):
    out = vids / name
    a = ["-i", str(SPEECH)] if acodec else []
    ff("-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30", *a, *(["-t", str(secs)] if secs else ["-shortest"]),
       "-c:v", *vcodec, *(["-c:a", *acodec] if acodec else ["-an"]), *extra, str(out))
    return out


H264, AAC = ["libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"], ["aac"]
good = {
    "MP4": make("stream.mp4", H264, AAC),
    "MOV": make("stream.mov", H264, AAC),
    "MKV": make("stream.mkv", H264, AAC),
    "WebM": make("stream.webm", ["libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "300k"], ["libopus"]),
    "AVI": make("stream.avi", ["mpeg4", "-q:v", "6"], ["pcm_s16le"]),
    "M4V": make("stream.m4v", H264, AAC),
}
silent = make("no sound.mp4", H264, None, secs=6)
audio_only = vids / "sound only.mp4"
ff("-i", str(SPEECH), "-c:a", "aac", str(audio_only))
corrupt = vids / "corrupt.mp4"
corrupt.write_bytes(os.urandom(300_000))
whole = good["MP4"].read_bytes()
truncated = vids / "cut off.mov"
make("big.mov", H264, AAC, extra=["-movflags", "0"])              # moov at the end: cutting the file loses it
truncated.write_bytes((vids / "big.mov").read_bytes()[:150_000])
text = vids / "notes.mov"
text.write_text("this is not a video at all\n" * 500)
big = make("long stream.mp4", ["libx264", "-preset", "ultrafast", "-crf", "8", "-pix_fmt", "yuv420p"], AAC, size="1920x1080")

data = base / "Echo Clip data"
env = {**os.environ, "PEAKCUT_DATA": str(data), "PEAKCUT_PORT": str(PORT), "PEAKCUT_PUBLIC_URL": BASE, "PEAKCUT_ACCOUNTS": "0",
       "PEAKCUT_MODELS": str(models), "PEAKCUT_FETCH_MODEL": "0", "PEAKCUT_UPDATE_CHECK": "0", "PEAKCUT_KEYCHAIN": "0",
       "DATABASE_URL": "", "PEAKCUT_AI_PROVIDER": "featherless", "PEAKCUT_AI_MODELS": "", "PEAKCUT_AI_KEY": "",
       "PEAKCUT_AI_KEY_FILE": "", "PEAKCUT_AUTO_RELOAD": "0", "PYTHONUNBUFFERED": "1", "PEAKCUT_MAX_UPLOAD_MB": "0",
       "PEAKCUT_UPLOAD_PART_MB": "5", "PEAKCUT_VERIFY_EMAIL": "0", "PEAKCUT_MAX_JOBS_PER_USER": "0"}
log = (base / "server.log").open("w")
server = subprocess.Popen([sys.executable, "-m", "studio.server"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)


def call(path, body=None, method=None, raw=None):
    data_ = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data_, method=method or ("POST" if data_ is not None else "GET"),
                                 headers={"Content-Type": "application/octet-stream" if raw is not None else "application/json",
                                          "Origin": BASE})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def upload(f: Path, opts=None, skip=()):
    """Send a file the way the page does: start, the pieces, finish. `skip`: pieces left out (a cut-off upload)."""
    blob = f.read_bytes()
    st, s = call("/api/upload/start", {"name": f.name, "size": len(blob), "options": opts or {"max_clips": 2, "duration": "15-20"}})
    if st != 200:
        return st, s, None
    for n in range(s["parts"]):
        if n in skip:
            continue
        call(f"/api/upload/{s['id']}/{n}", method="PUT", raw=blob[n * s["part"]:(n + 1) * s["part"]])
    st, r = call(f"/api/upload/{s['id']}/finish", {})
    return st, r, s


try:
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    st = call("/api/status")[1]
    check("the page is told which formats are taken and how much room there is",
          st["upload_formats"] == ["MP4", "MOV", "MKV", "WebM", "AVI", "M4V"] and st["disk_free_gb"] > 0 and st["max_upload_mb"] == 0)
    jobs_made = []
    for kind, f in good.items():
        code, r, _s = upload(f)
        m = r.get("media") or {}
        check(f"{kind}: accepted, its streams read with ffprobe (640x360, about {m.get('duration', 0):.0f} s, {m.get('video_codec')}/{m.get('audio_codec')})",
              code == 200 and m.get("width") == 640 and m.get("height") == 360 and m.get("duration", 0) > 10 and m.get("audio_codec"),
              f"{code} {r}")
        if code == 200:
            check(f"{kind}: kept as the project's source, and its job is queued with the size and length already known",
                  (data / "users" / "u_local" / "jobs" / r["id"] / ("source" + f.suffix)).is_file() and r["width"] == 640 and r["duration"] > 10)
            jobs_made.append(r["id"])
    for name, f, says in (("random bytes called .mp4", corrupt, "damaged"),
                          ("a recording cut off half way", truncated, "damaged"),
                          ("text renamed .mov", text, "damaged"),
                          ("a video with no sound", silent, "no sound"),
                          ("sound with no picture", audio_only, "no video")):
        before = len(call("/api/jobs")[1])
        code, r, _s = upload(f)
        check(f"refused, with the reason: {name}", code == 422 and says in r.get("detail", ""), f"{code} {r}")
        check(f"...and nothing is left behind: {name}", len(call("/api/jobs")[1]) == before
              and not any((data / "tmp" / "uploads").glob("up-*")))
    code, r = call("/api/upload/start", {"name": "setup.exe", "size": 1000})
    check("a file that is not a video by name is refused before anything is sent", code == 400 and "MP4, MOV, MKV, WebM, AVI or M4V" in r["detail"])
    code, r = call("/api/upload/start", {"name": "empty.mp4", "size": 0})
    check("an empty file is refused", code == 400)
    code, r = call("/api/upload/start", {"name": "huge.mp4", "size": 10 ** 15})
    check("a file bigger than the free disk space is refused before it is sent, saying how much room there is",
          code == 507 and "free disk space" in r["detail"] and "GB free" in r["detail"], f"{code} {r}")

    # ---- a big file in many pieces, cut off half way, then carried on
    blob = big.read_bytes()
    code, r, s = upload(big, skip={1, 3})
    check(f"a {len(blob) / 1e6:.0f} MB file goes in {s['parts']} pieces", s["parts"] >= 4, str(s["parts"]))
    check("finishing with pieces missing says which, and keeps what arrived", code == 409 and "has not arrived" in r["detail"])
    code, state = call(f"/api/upload/{s['id']}")
    check("the upload says which pieces it already has", code == 200 and state["have"] == [n for n in range(s["parts"]) if n not in (1, 3)], str(state))
    for n in (1, 3):
        call(f"/api/upload/{s['id']}/{n}", method="PUT", raw=blob[n * s["part"]:(n + 1) * s["part"]])
    code, r = call(f"/api/upload/{s['id']}/finish", {})
    src = data / "users" / "u_local" / "jobs" / r.get("id", "x") / "source.mp4"
    check("sending only the missing pieces finishes the upload", code == 200 and r["media"]["width"] == 1920, f"{code} {r}")
    check("the joined file is byte for byte the original", src.is_file() and hashlib.sha256(src.read_bytes()).digest() == hashlib.sha256(blob).digest())
    check("the pieces are gone once joined", not (data / "tmp" / "uploads" / f"up-{s['id']}").exists())
    code, s2 = call("/api/upload/start", {"name": "cancel me.mp4", "size": len(whole)})
    call(f"/api/upload/{s2['id']}", method="DELETE")
    check("a cancelled upload is removed", call(f"/api/upload/{s2['id']}")[0] == 404)
    code, r = call("/api/upload/0123456789abcdef01234567/finish", {})
    check("finishing an upload that does not exist says so", code == 404)
    # ---- one of them all the way to a rendered clip
    t0 = time.time()
    jid = jobs_made[1]                                   # the MOV
    while time.time() - t0 < 600:
        j = call(f"/api/jobs/{jid}")[1]
        if j["status"] in ("done", "error"):
            break
        time.sleep(1)
    done = [c for c in j["clips"] if c["status"] == "done"]
    check("an uploaded MOV is transcribed, ranked and rendered by the same pipeline", j["status"] == "done" and done
          and j["phase"] == "ready" and j["candidates"], f"{j['status']} {j.get('error')}")
    if done:
        f = data / "users" / "u_local" / "jobs" / jid / "clips" / done[0]["file"]
        p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(f)],
                                      capture_output=True, text=True).stdout)["streams"]
        v = next(x for x in p if x["codec_type"] == "video")
        check("...into a 1080x1920 H.264 + AAC clip", (v["codec_name"], v["width"], v["height"]) == ("h264", 1080, 1920)
              and any(x["codec_type"] == "audio" and x["codec_name"] == "aac" for x in p))
finally:
    server.terminate()
    try:
        server.wait(timeout=20)
    except subprocess.TimeoutExpired:
        server.kill()
    log.close()
    if fails:
        print((base / "server.log").read_text()[-2500:])

# ================================================================ part 2: a "link" video and an upload, side by side
os.environ.update(PEAKCUT_DATA=str(base / "inproc"), PEAKCUT_MODELS=str(models), PEAKCUT_KEYCHAIN="0", DATABASE_URL="",
                  PEAKCUT_ACCOUNTS="0", PEAKCUT_FETCH_MODEL="0", PEAKCUT_AI_KEY="", PEAKCUT_AI_MODELS="", PEAKCUT_UPDATE_CHECK="0")
sys.path.insert(0, str(ROOT))
from studio import auth, ctx, db, jobs, media  # noqa: E402

db.init()
uid = auth.local_user().id
same = good["MP4"]


def fake_info(url):
    return {"title": "A stream", "uploader": "Someone", "duration": float(media.probe(same)["duration"]), "is_live": False,
            "thumbnail": "", "kind": "video"}


def fake_audio(url, d, on_progress=None):
    out = Path(d) / "audio_src.mp4"
    shutil.copyfile(same, out)
    return out


def fake_video(url, d, height=720):
    out = Path(d) / "source.mp4"
    shutil.copyfile(same, out)
    return out


media.info, media.download_audio, media.download = fake_info, fake_audio, fake_video
media.save_thumb = lambda *a, **k: False
media.youtube_words = lambda url: None
opts = {"max_clips": 2, "duration": "15-20", "layout": "auto"}
with ctx.as_user(uid):
    a = jobs.create({"type": "url", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "name": "link"}, opts, enqueue=False)
    b = jobs.create({"type": "file", "path": "", "name": "upload.mp4"}, opts, enqueue=False)
    dest = jobs.job_dir(b["id"]) / "source.mp4"
    shutil.copyfile(same, dest)
    b["source"]["path"] = str(dest)
    jobs._run_job(a)
    jobs._run_job(b)
spans = lambda j: [(c["start"], c["end"]) for c in j["clips"]]  # noqa: E731
check("a video from a link and the same video uploaded go through the same steps to the same clips",
      a["status"] == b["status"] == "done" and spans(a) == spans(b) and spans(a)
      and all(c["status"] == "done" for c in a["clips"] + b["clips"]), f"{spans(a)} vs {spans(b)}")
check("...with the same candidates and the same ranking", [c["start"] for c in a["candidates"]] == [c["start"] for c in b["candidates"]])


tries = []


def refused(url):
    tries.append(url)
    raise media.YouTubeBlocked(media.YT_BLOCKED)


media.info = refused
jobs.start()
with ctx.as_user(uid):
    c = jobs.create({"type": "url", "url": "https://youtu.be/bbbbbbbbbbb", "name": "https://youtu.be/bbbbbbbbbbb"}, opts)
for _ in range(60):
    if c["status"] == "error":
        break
    time.sleep(0.5)
check("a link that cannot be downloaded says to upload a local copy, and keeps the link",
      c["status"] == "error" and c.get("upload_instead") is True and c["error"].startswith(media.UPLOAD_INSTEAD)
      and c["source"]["url"] == "https://youtu.be/bbbbbbbbbbb", str((c["status"], c.get("error"))))
time.sleep(2)
check("...and it is not retried over and over", len(tries) == 1, str(len(tries)))

db.engine.dispose()
shutil.rmtree(base, ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
