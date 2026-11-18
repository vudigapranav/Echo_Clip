"""The clip pipeline's own parts, with the network replaced: captions, layouts, the facecam search, finding and
ranking moments, the hook check, choosing clips, and the AI settings (Featherless, local only, budget, fallback,
the connection test). Nothing leaves this machine, no key is needed and no AI credit is spent."""
import json
import os
import sys
import tempfile

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="ec-pipe-")
os.environ.update(DATABASE_URL="", PEAKCUT_KEYCHAIN="0", PEAKCUT_AI_PROVIDER="featherless", PEAKCUT_AI_MODELS="",
                  PEAKCUT_AI_KEY="", PEAKCUT_AI_KEY_FILE="", PEAKCUT_AI_BASE_URL="")
sys.path.insert(0, ".")
import numpy as np  # noqa: E402

from studio import ai, brain, captions, compose, config, ctx, db, face, highlights, usage  # noqa: E402

db.init()
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"   {extra}" if extra and not ok else ""))
    ok or fails.append(name)


# ================================================================ captions
for k in captions.PRESETS:
    img = captions.preview(k, width=270)
    check(f"caption preset {k} draws a preview", img[:2] == b"\xff\xd8" and len(img) > 3000)
st = captions.clean({"preset": "neon_purple", "size": 999, "color": "#a855f7", "font": "Bebas Neue", "weight": 900, "junk": 1})
check("caption settings are checked: sizes clamped, colours upper-cased, unknown keys dropped",
      st == {"preset": "neon_purple", "size": 180, "color": "#A855F7", "font": "Bebas Neue", "weight": 900}, str(st))
check("...and the nearest weight a font comes in is used", captions.resolve(st)["weight"] == 400)
for bad in ({"color": "red"}, {"font": "Comic Sans"}, {"position": "sideways"}, 5):
    try:
        captions.clean(bad)
        check(f"bad caption setting refused: {bad}", False)
    except ValueError:
        check(f"bad caption setting refused: {bad}", True)
check("an unknown preset falls back to the default", captions.resolve({"preset": "nope"})["preset"] == captions.DEFAULT)
words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.35} for i, w in enumerate("oh my god the cops are right behind me no way".split())]
tr = captions.Track(words, 0.0, 5.0, "gaming_bold", ["cops"], 1380, 30)
frames = list(tr.frames())
check("one caption strip per frame of the clip", len(frames) == 150 and all(len(f) == 1080 * tr.height * 4 for f in frames))
check("the strip sits around the caption line", tr.y0 < 1380 < tr.y0 + tr.height)
check("captions show while words are said and are see-through before the first one",
      tr.image(0.1).getbbox() is not None and captions.Track(words, -2.0, 5.0, "gaming_bold").image(0.5).getbbox() is None)
shown = [tr._state(t / 30)[0] if tr._state(t / 30) else None for t in range(150)]
check("never two caption groups at once, and groups follow each other in order",
      [g for i, g in enumerate(shown) if g is not None and (i == 0 or shown[i - 1] != g)] == sorted({g for g in shown if g is not None}))
messy = [{"w": "one", "s": 0.0, "e": 2.5}, {"w": "two", "s": 0.3, "e": 2.6}, {"w": "three", "s": 0.6, "e": 2.7}, {"w": "four", "s": 0.5, "e": 3.0}]
check("overlapping word times still draw one group at a time", captions.Track(messy, 0, 4, "classic_white").image(1.0).getbbox() is not None)
jp = captions.Track([{"w": "こんにちは", "s": 0, "e": 1}], 0, 1, "gaming_bold")
check("a script the caption fonts lack is drawn with a system font (or the caption font when none)", jp.image(0.5).getbbox() is not None)
long = captions.Track([{"w": "Supercalifragilisticexpialidocious" * 2, "s": 0, "e": 1}], 0, 1, "gaming_bold")
box = long.image(0.5).getbbox()
check("a long word is shrunk to stay inside the frame", box and box[0] >= 40 and box[2] <= 1040, str(box))
hook = captions.hook_image("WAIT FOR THE COPS TO SHOW UP", 300)
check("the hook box fits its text inside the frame", hook and hook[0].getbbox()[0] >= 60 and hook[0].getbbox()[2] <= 1020)

# ================================================================ layouts
SCENE = {"face": [0.07, 0.59, 0.27, 0.24], "cam": [0.0, 0.41, 0.43, 0.59], "face_size": 0.08, "action": [0.6, 0.4]}
BIG = {"face": [0.4, 0.2, 0.22, 0.4], "cam": None, "face_size": 0.22, "action": [0.5, 0.5]}
for mode in compose.MODES:
    for sw, sh, scene in ((1280, 720, SCENE), (1920, 1080, None), (1280, 720, BIG), (720, 1280, None), (853, 480, SCENE)):
        p = compose.plan(mode, sw, sh, scene, {"face": [0, 0.5, 0.4, 0.5], "split": 0.35, "zoom": 1.5})
        ok = bool(p["panels"])
        for pn in p["panels"]:
            x, y, w, h = pn["src"]
            dx, dy, dw, dh = pn["dst"]
            ok = ok and x >= 0 and y >= 0 and x + w <= sw and y + h <= sh and w % 2 == 0 and h % 2 == 0 \
                and abs(w / h - dw / dh) < 0.03 and 0 <= dy and dy + dh <= 1920 and dw == 1080
        covered = sum(pn["dst"][3] for pn in p["panels"])
        ok = ok and (p["blur"] or covered == 1920)        # no black bars: panels fill the frame or a blurred fill is behind
        check(f"layout {mode} on {sw}x{sh} ({'face' if scene and scene.get('face') else 'no face'}): no stretching, inside the frame, no bars", ok,
              str(p["panels"]))
        g = compose.graph(p, 30, "slow", 10)
        check(f"layout {mode} on {sw}x{sh}: an ffmpeg filter ending in [v0]", g.startswith("[0:v]") and g.endswith("[v0]") and ";;" not in g)
check("smart auto stacks a small facecam over the gameplay", compose.plan("auto", 1280, 720, SCENE)["used"] == "stack")
check("smart auto fills the screen around a big face", compose.plan("auto", 1280, 720, BIG)["used"] == "facecam")
check("smart auto with nobody found keeps the gameplay big", compose.plan("auto", 1280, 720, None)["used"] == "gameplay")
check("a facecam layout with no face found falls back to one that needs none", compose.plan("stack", 1280, 720, None)["used"] == "gameplay")
p = compose.plan("stack", 1280, 720, SCENE)
fx, fy, fw, fh = p["panels"][0]["src"]
check("the stacked facecam panel holds the whole facecam", fx <= 0.0 * 1280 + 1 and fy <= 0.41 * 720 + 1 and fx + fw >= 0.43 * 1280 - 2)
check("old layout names still work", compose.mode_of("stack_top") == "stack" and compose.mode_of("pip") == "stack" and compose.mode_of("nonsense") == "auto")

# ================================================================ facecam search, with the frames and the model replaced
seq = iter(range(1000))
frame = np.zeros((360, 640, 3), np.uint8)
face.available = lambda: True
face.grab = lambda src, t, w, h: frame
streamer = (40, 210, 52, 70, 0.95)
friend = (180, 250, 40, 48, 0.9)


def fake_faces(_f):
    i = next(seq)
    out = [streamer] if i % 10 != 3 else []
    if i % 3:
        out.append(friend)
    if i % 7 == 0:                                      # a face in the game, somewhere new each time
        out.append((300 + (i * 37) % 250, 40 + (i * 13) % 200, 60, 70, 0.99))
    return out


face.faces = fake_faces
fc = face.find_facecam("x.mp4", 600, 1280, 720)
check("the facecam is where faces stay all video, not where game faces flash by",
      fc["ok"] and fc["cam"] and fc["cam"][0] < 0.05 and fc["cam"][0] + fc["cam"][2] < 0.5 and fc["share"] >= 0.8, str(fc))
check("the streamer's friend on the same camera is inside the facecam", fc["face"][0] + fc["face"][2] >= (180 + 40) / 640 - 0.01)
face.faces = lambda _f: []
check("no faces: no facecam, and it says why", face.find_facecam("x.mp4", 600, 1280, 720)["cam"] is None)
face.grab = lambda *a: None
sc = face.analyse("x.mp4", 0, 30, 1280, 720)
check("frames that cannot be read give a plain answer, not an error", sc["ok"] is False and sc["action"] == [0.5, 0.5] and sc["note"])

# ================================================================ finding moments
rng = np.random.default_rng(3)
talk = ("so we are just driving around the city right now and nothing much is happening at all today "
        "okay let me grab this car and head to the bank for the next mission ").split()
ws, t, script = [], 0.0, []
for i in range(1300):                             # about eleven minutes of talking, with two real moments in it
    tick = i % len(talk)
    word = talk[tick]
    if 200 <= i < 214:
        word = "oh my god the cops are right behind me no way bro what".split()[(i - 200) % 12]
    if 420 <= i < 432:
        word = "hahaha he just crashed into the bus lol that is hilarious".split()[(i - 420) % 11]
    if tick == len(talk) - 1 or i in (213, 431):
        word += "."
    ws.append({"w": word, "s": round(t, 2), "e": round(t + 0.35, 2)})
    t += 0.5 if tick != len(talk) - 1 else 1.4
dur = t + 2
dbv = np.full(int(dur) + 1, -30.0) + rng.normal(0, 1, int(dur) + 1)
dbv[int(ws[200]["s"]):int(ws[214]["s"])] = -12
cands = highlights.candidates(ws, dbv, dur, 30, 45, 30)
check("20 to 40 candidate moments are found", 20 <= len(cands) <= 40, str(len(cands)))
check("every candidate keeps to the chosen length", all(29 <= c["duration"] <= 45.6 for c in cands), str([c["duration"] for c in cands]))
check("candidates do not overlap much", all(min(a["end"], b["end"]) - max(a["start"], b["start"]) <= 0.36 * min(a["duration"], b["duration"])
                                            for i, a in enumerate(cands) for b in cands[i + 1:]))
ends = set(round(e, 2) for e in brain.sentence_ends(ws))
check("candidates end where a sentence ends", sum(any(abs(c["end"] - e) < 0.5 for e in ends) for c in cands) >= len(cands) * 0.8)
top = cands[0]
check("the loud reaction is the strongest moment found", top["start"] <= ws[205]["s"] <= top["end"], f"{top['start']}-{top['end']}")
check("ids are given in order", [c["id"] for c in cands[:3]] == ["c01", "c02", "c03"])
short = highlights.candidates(ws, dbv, dur, 15, 20, 25)
check("short lengths are kept too", all(14 <= c["duration"] <= 20.6 for c in short))

# ================================================================ ranking: one AI request, checked, cached
calls = []


def fake_ask(prompt, schema=None, purpose=""):
    calls.append(prompt)
    ids = [c["id"] for c in cands]
    return ([{"id": ids[1], "entertainment": 90, "reaction": 85, "surprise": 80, "hook": 70, "context": 80, "replay": 75,
              "category": "chase", "title": "Cops right behind him", "hook_text": "cops behind!", "reason": "A real chase payoff.",
              "highlight_words": ["cops"], "captions": ["NO WAY"], "opening": {"engagement": 80, "curiosity": 70, "emotion": 90,
              "clarity": 80, "payoff": 70, "start_at": cands[1]["start"] + 2}, "ending": "strong", "total": 3},
             {"id": "c99", "entertainment": 100, "reaction": 100, "surprise": 100, "hook": 100, "context": 100, "replay": 100,
              "category": "x", "title": "made up", "reason": "made up"},
             {"id": ids[0], "entertainment": "lots", "category": "funny", "title": "broken", "reason": "x"},
             "nonsense"], "model-x", 1234)


brain.ask_ai = fake_ask
ranked, info = highlights.rank([dict(c) for c in cands], ws, "T", "U", 5)
check("with no AI set up, ranking is local and no request is made", info["ranked_by"] == "local" and not calls and "set up" in info["note"])
ai.save("featherless", "Qwen/Qwen3-Next-80B-A3B-Instruct", "", "fl-test-key-0000000000")
check("Featherless is a service with its own address", ai.ready() and ai.base_url() == "https://api.featherless.ai/v1" and ai.label() == "Featherless AI")
ranked, info = highlights.rank([dict(c) for c in cands], ws, "T", "U", 5)
byid = {c["id"]: c for c in ranked}
check("one request ranks all candidates", len(calls) == 1 and info["ranked_by"] == "ai" and info["tokens"] == 1234)
check("only text is sent: compact candidates, no file paths or media", all(c["id"] in calls[0] for c in cands) and ".mp4" not in calls[0]
      and len(calls[0]) < 40000)
c2 = byid[cands[1]["id"]]
check("the total is worked out here from the six scores, never taken from the AI",
      c2["total"] == round(90 * .25 + 85 * .2 + 80 * .2 + 70 * .15 + 80 * .1 + 75 * .1) and c2["ai"]["category"] == "chase")
check("an id the AI made up is ignored", "c99" not in byid and not any(c.get("ai", {}).get("title") == "made up" for c in ranked))
check("an answer with broken scores is ignored", not byid[cands[0]["id"]].get("ai"))
check("text from the AI is tidied: hook in capitals, no links", c2["ai"]["hook_text"] == "COPS BEHIND!")
ranked2, info2 = highlights.rank([dict(c) for c in cands], ws, "T", "U", 5)
check("the same video ranked again uses the saved answer: no second request", len(calls) == 1 and info2["cached"] and info2["ranked_by"] == "ai")
brain.ask_ai = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 402 insufficient credits"))
ranked3, info3 = highlights.rank([dict(c) for c in cands], ws, "Other title", "U", 5)
check("when the AI fails (no credit), clips are still ranked locally and it says so", info3["ranked_by"] == "local" and "locally" in info3["note"])
brain.ask_ai = lambda *a, **k: ("not a list", "m", 5)
check("an answer that is not a list is set aside", highlights.rank([dict(c) for c in cands], ws, "Third", "U", 5)[1]["ranked_by"] == "local")
brain.ask_ai = fake_ask

# ================================================================ choosing, and the hook
picked, note = highlights.select(ranked, 5, "balanced", "ai")
check("clips worth making are picked, none of them the same incident",
      picked and all(not highlights.same_incident(a, b) for i, a in enumerate(picked) for b in picked[i + 1:]))
check("when there are not enough good moments, fewer are made and it says why", len(picked) < 5 and "Only" in note and "made up" in note, note)
loc, note2 = highlights.select([dict(c) for c in cands], 5, "best", "local")
check("ranked locally, the number asked for is made", len(loc) == 5 and not note2)
silent = {"id": "h", "start": 100.0, "end": 140.0, "peak": 120.0, "signals": {}}
hw = [{"w": "so", "s": 102.5, "e": 102.8}, {"w": "um", "s": 103.0, "e": 103.2}] + \
     [{"w": f"word{i}", "s": 103.5 + i * 0.4, "e": 103.8 + i * 0.4} for i in range(60)]
h = highlights.hook(silent, hw, np.full(200, -20.0), 0.0, 15)
check("dead air and a filler opening are spotted", any("silence" in i for i in h["issues"]) and any("filler" in i for i in h["issues"]), str(h["issues"]))
check("the suggested start is where speech begins, never past the moment", 102.0 <= h["recommended_start"] <= 104 and h["recommended_start"] < 120)
check("the hook score uses the five weighted parts", h["score"] == round(sum(h["parts"][k] * w for k, w in highlights.HOOK_WEIGHTS.items())))
lively = {"id": "g", "start": 10.0, "end": 40.0, "peak": 18.0, "signals": {}}
gw = [{"w": w, "s": 10.1 + i * 0.3, "e": 10.35 + i * 0.3} for i, w in enumerate("oh my god no way bro what".split())] + \
     [{"w": "x", "s": 13 + i * 0.4, "e": 13.3 + i * 0.4} for i in range(60)]
g = highlights.hook(lively, gw, np.full(60, -20.0), 0.0, 15)
check("a strong opening is kept as it is", g["recommended_start"] == 10.0 and g["class"] in ("strong", "good"), str(g))
far = {"id": "f", "start": 0.0, "end": 30.0, "peak": 25.0, "signals": {}, "ai": {"start_at": 20.0, "opening": {k: 30 for k in highlights.HOOK_WEIGHTS}}}
fw = [{"w": f"w{i}.", "s": i * 1.0, "e": i * 1.0 + 0.5} for i in range(30)]
f = highlights.hook(far, fw, None, 0.0, 15)
check("an AI start that would cut too much (past a third, too short) is not followed", f["recommended_start"] == 0.0, str(f["recommended_start"]))
mid = dict(far, ai={"start_at": 6.0, "opening": {k: 30 for k in highlights.HOOK_WEIGHTS}})
m = highlights.hook(mid, fw, None, 0.0, 15)
check("an AI start that is safe is followed, onto the start of a line, with a warning about the setup it drops",
      5.5 <= m["recommended_start"] <= 6.0 and m["by"] == "ai" and "drops" in m["context_warning"], str(m))

# ================================================================ AI settings: local only, budget, fallback, the key
KEY = "fl-test-key-0000000000"
pub = ai.public()
check("the key itself never goes back to the page", KEY not in json.dumps(pub) and pub["has_key"] and pub["key_hint"] == "0000")
check("Featherless is offered first, with the verified model as its example",
      pub["providers"][0]["id"] == "featherless" and pub["providers"][0]["example"] == "Qwen/Qwen3-Next-80B-A3B-Instruct")
ai.save("featherless", "Qwen/Qwen3-Next-80B-A3B-Instruct", "", None, mode="local")
check("local only: the AI is not asked", not ai.allowed() and "local only" in ai.why_not())
calls.clear()
check("...and ranking stays on this machine", highlights.rank([dict(c) for c in cands], ws, "Fourth", "U", 5)[1]["ranked_by"] == "local" and not calls)
ai.save("featherless", "Qwen/Qwen3-Next-80B-A3B-Instruct", "", None, mode="ai", price_in=0.125, price_out=1.15, budget=0.01)
with ctx.as_user("u_budgettest"):
    usage.record(60000, 3000, "test")
ai._spent["at"] = 0
check("the month's spending is estimated from tokens and the entered prices", abs(ai.spent_this_month() - (60000 * 0.125 + 3000 * 1.15) / 1e6) < 1e-6)
check("with the budget reached the AI is not asked, and it says why", not ai.allowed() and "budget" in ai.why_not())
ai.save("featherless", "Qwen/Qwen3-Next-80B-A3B-Instruct", "", None, budget=0)
ai._spent["at"] = 0
check("raising the budget lets it be asked again", ai.allowed())
for bad in ({"budget": -1}, {"price_in": 99999}):
    try:
        ai.save("featherless", "m", "", None, **bad)
        check(f"a bad spending setting is refused: {bad}", False)
    except ValueError:
        check(f"a bad spending setting is refused: {bad}", True)
check("settings survive being read again from disk", (ai._saved.update(at=None) or ai.options()["price_out"]) == 1.15 and ai.key() == KEY)

sent = []


class Reply:
    def __init__(self, status, data):
        self.status_code, self._d, self.text = status, data, json.dumps(data)

    def json(self):
        return self._d


def post(url, json=None, headers=None, timeout=None, **_k):  # noqa: A002
    sent.append((url, json, headers or {}, timeout))
    if "featherless" in url:
        return Reply(402, {"error": {"message": "Insufficient credits"}})
    return Reply(200, {"choices": [{"message": {"content": '{"items": ["ok"]}'}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 5, "completion_tokens": 2}})


def get(url, params=None, headers=None, timeout=None):
    if url.endswith("/models/Qwen/Qwen3-Next-80B-A3B-Instruct"):
        return Reply(200, {"id": "Qwen/Qwen3-Next-80B-A3B-Instruct", "context_length": 32768, "pricing": {"input": 0.125, "output": 1.15}})
    return Reply(404, {"error": {"message": "Model not found"}})


ai.httpx.post, ai.httpx.get = post, get
import importlib  # noqa: E402
importlib.reload(brain)                                  # the real ask_ai again (the fake was only for ranking)
ai.save("featherless", "Qwen/Qwen3-Next-80B-A3B-Instruct", "", None, temperature=0.2, max_tokens=900, timeout=33)
r = ai.test()
check("the connection test says plainly when there is no credit", r["ok"] is False and r["code"] == "credits" and KEY not in json.dumps(r), str(r))
check("the test asks for a tiny answer only", sent[-1][1]["max_tokens"] == 16 and sent[-1][1]["model"] == "Qwen/Qwen3-Next-80B-A3B-Instruct")
check("the test result is kept for the settings page", ai.public()["last_test"]["code"] == "credits")
ai.save("featherless", "Qwen/NoSuchModel", "", None)
r = ai.test()
check("an unknown model is caught by the free catalogue look-up, before any request", r["code"] == "invalid_model" and len(sent) == 1, str(r))
ai.save("featherless", "Qwen/Qwen3-Next-80B-A3B-Instruct", "", None)
sent.clear()
try:
    brain.ask_ai("hello", brain.SCHEMA, purpose="t")
except RuntimeError as e:
    check("with no credit left the request is not retried over and over", len(sent) == 1 and "credit" in str(e), str(len(sent)))
check("the saved temperature, answer length and wait are used", sent[0][1].get("max_tokens") == 900 and sent[0][1].get("temperature") == 0.2 and sent[0][3] == 33)
ai.save("featherless", "Qwen/Qwen3-Next-80B-A3B-Instruct", "", None,
        fallback={"provider": "openai", "models": "gpt-test", "key": "sk-fallback-0000000000"})
sent.clear()
try:
    brain.ask_ai("hello", brain.SCHEMA, purpose="t")
    check("a paid fallback is never used unless the owner allowed it", False)
except RuntimeError:
    check("a paid fallback is never used unless the owner allowed it", all("featherless" in s[0] for s in sent))
ai.save("featherless", "Qwen/Qwen3-Next-80B-A3B-Instruct", "", None, allow_fallback=True)
sent.clear()
with ctx.as_user("u_budgettest"):
    out, model, spent = brain.ask_ai("hello", brain.SCHEMA, purpose="t")
check("when allowed, the fallback answers after the main service fails", out == ["ok"] and model == "gpt-test" and "openai" in sent[-1][0])
check("the fallback key is not shown either", "sk-fallback-0000000000" not in json.dumps(ai.public()) and ai.public()["fallback"]["has_key"])
check("errors are told apart", [ai.classify(x) for x in ("HTTP 402 payment required", "HTTP 401 bad key", "HTTP 429 slow down",
                                                      "HTTP 404 model not found", "ConnectError: getaddrinfo failed", "odd")]
      == ["credits", "invalid_key", "rate_limit", "invalid_model", "network", "other"])
try:
    ai.save("openai", "m", "", None)
    check("a new paid service without a key is refused", False)
except ValueError:
    check("a new paid service without a key is refused", True)
ai.save("featherless", "Qwen/Qwen3-Next-80B-A3B-Instruct", "", "")
check("Remove key really removes it: the AI is then not ready and says so", not ai.ready() and not ai.public()["has_key"]
      and "set up" in ai.why_not())

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil  # noqa: E402
db.engine.dispose()
shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
