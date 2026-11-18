"""Turns a time range into a vertical clip: 9:16 layout, word-by-word captions, hook title, cover.

Captions are drawn by Echo Clip itself (captions.py, with Pillow) and laid over the video while ffmpeg encodes it,
so every ffmpeg works, including builds without the subtitle library (Homebrew's ffmpeg has none). build_ass()
below is the older way of describing the same captions for ffmpeg's subtitle filter; it is kept for reference and
its tests, and is not used to render."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import captions, compose, config, roman
from .media import NO_WINDOW, run

W, H = config.OUT_W, config.OUT_H

# ASS colours are &HBBGGRR. `size` is the ASS font size (line height); _Font turns it into real pixels.
ASS_STYLES = {
    "bounce": {"label": "Bounce", "font": "Poppins ExtraBold", "size": 132, "base": "FFFFFF", "active": "00F0FF",
               "strong": "FFFFFF", "outline": 7, "pop": 112, "upper": True, "per": 3},
    "hype": {"label": "Hype", "font": "Anton", "size": 152, "base": "FFFFFF", "active": "3C3CFF",
             "strong": "00D7FF", "outline": 7, "pop": 114, "upper": True, "per": 2},
    "clean": {"label": "Clean", "font": "Poppins ExtraBold", "size": 100, "base": "FFFFFF", "active": "6BD7FF",
              "strong": "FFFFFF", "outline": 5, "pop": 106, "upper": False, "per": 4},
}
HOOK = {"font": "Poppins ExtraBold", "size": 96, "ink": "14100E", "box": "FFFFFF", "pad_x": 36, "pad_y": 22, "radius": 26,
        "max_w": 900}
FONT_FILES = {"Poppins ExtraBold": "Poppins-ExtraBold.ttf", "Anton": "Anton-Regular.ttf", "Archivo Black": "ArchivoBlack-Regular.ttf"}
CAP_MAX_W = 960   # captions never run wider than this, whatever is said


class _Font:
    """Measures text exactly as the renderer will draw it, so every word can be placed by hand: the highlighted
    word can grow without pushing its neighbours, and the heading box fits its text."""
    _cache: dict = {}

    def __init__(self, name):
        import struct
        path = Path(__file__).resolve().parent / "fonts" / FONT_FILES.get(name, FONT_FILES["Poppins ExtraBold"])
        d = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", d[4:6])[0]):
            tag, _, off, _ = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
            tables[tag] = off
        self.upm = struct.unpack(">H", d[tables[b"head"] + 18: tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        self.asc, self.desc = struct.unpack(">HH", d[o + 74: o + 78])      # what the renderer sizes the font by
        self.cap = struct.unpack(">h", d[o + 88: o + 90])[0] or int(self.upm * 0.7)
        self.path = str(path)
        self._pil = {}

    @classmethod
    def get(cls, name):
        if name not in cls._cache:
            cls._cache[name] = cls(name)
        return cls._cache[name]

    def em(self, size):
        return size * self.upm / (self.asc + self.desc)

    def width(self, text, size):
        """Pixels wide at this ASS size."""
        try:
            from PIL import ImageFont
            if "f" not in self._pil:
                self._pil["f"] = ImageFont.truetype(self.path, 200)
            return self._pil["f"].getlength(text) * self.em(size) / 200
        except Exception:  # noqa: BLE001 - no measuring library: a fair estimate keeps clips rendering
            return len(text) * self.em(size) * 0.62

    def cap_shift(self, size):
        """How far below the anchor the middle of a capital letter sits when text is centred on a point."""
        return ((self.asc - self.desc) / 2 - self.cap / 2) * size / (self.asc + self.desc)


CAPTION_LEAD = 0.0  # measured: word stamps are within 0.15 s of the voice, so no shift is needed
def install_fonts():
    """Copy the bundled caption fonts next to the data, so captions look the same on every machine and server."""
    src = Path(__file__).resolve().parent / "fonts"
    for f in src.glob("*.ttf"):
        if not (config.FONTS_DIR / f.name).exists():
            shutil.copyfile(f, config.FONTS_DIR / f.name)


LAYOUTS = compose.MODES
# the caption looks the page offers (captions.PRESETS); a clip's "style" is one of these names
STYLES = {k: {"label": v["label"]} for k, v in captions.PRESETS.items()}


def _ts(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def _clean(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", re.sub(r"[{}\\]", "", str(text))).strip()


def _key(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


def _groups(words, per):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or sum(len(x["w"]) for x in cur) + len(w["w"]) > 16
                    or w["s"] - cur[-1]["e"] > 0.45 or cur[-1]["w"][-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def _hook_events(hook, dur, y):
    """The heading: dark text in a white rounded box that fits it, on one line or two balanced ones."""
    f, size = _Font.get(HOOK["font"]), HOOK["size"]
    words = hook.split()
    if not words:
        return []
    lines = [" ".join(words)]
    if f.width(lines[0], size) > HOOK["max_w"] and len(words) > 1:      # two lines, split where they come out most even
        cut = min(range(1, len(words)), key=lambda i: max(f.width(" ".join(words[:i]), size), f.width(" ".join(words[i:]), size)))
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    widest = max(f.width(l, size) for l in lines)
    if widest > HOOK["max_w"]:
        size = max(40, int(size * HOOK["max_w"] / widest))
        widest = max(f.width(l, size) for l in lines)
    line_h = f.em(size) * 1.16
    bw, bh = widest + 2 * HOOK["pad_x"], line_h * len(lines) + 2 * HOOK["pad_y"]
    x0, y0, r = (W - bw) / 2, y - bh / 2, min(HOOK["radius"], bh / 2)
    x1, y1 = x0 + bw, y0 + bh
    n = lambda v: f"{v:.0f}"                                                         # noqa: E731
    box = (f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} b {n(x1)} {n(y0)} {n(x1)} {n(y0)} {n(x1)} {n(y0 + r)} "
           f"l {n(x1)} {n(y1 - r)} b {n(x1)} {n(y1)} {n(x1)} {n(y1)} {n(x1 - r)} {n(y1)} "
           f"l {n(x0 + r)} {n(y1)} b {n(x0)} {n(y1)} {n(x0)} {n(y1)} {n(x0)} {n(y1 - r)} "
           f"l {n(x0)} {n(y0 + r)} b {n(x0)} {n(y0)} {n(x0)} {n(y0)} {n(x0 + r)} {n(y0)}")
    span = f"{_ts(0)},{_ts(dur)}"
    ev = [f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,0)\\fad(160,0)\\c&H{HOOK['box']}&\\p1}}{box}{{\\p0}}"]
    for i, line in enumerate(lines):
        cy = y - line_h * (len(lines) - 1) / 2 + i * line_h - f.cap_shift(size)
        ev.append(f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an5\\pos({W // 2},{cy:.0f})\\fad(160,0)\\fs{size}}}{line}")
    return ev


def build_ass(words, start, end, hook, style_name, highlight, hook_y, cap_y) -> str:
    st = ASS_STYLES.get(style_name, ASS_STYLES["bounce"])
    words = [dict(w, s=w["s"] - CAPTION_LEAD, e=w["e"] - CAPTION_LEAD) for w in words]
    strong = {_key(h) for h in highlight if _key(h)}
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},&H00{st['base']},&H00{st['base']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{st['outline']},0,5,0,0,0,1
Style: Hook,{HOOK['font']},{HOOK['size']},&H00{HOOK['ink']},&H00{HOOK['ink']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dur = end - start
    ev = _hook_events(_clean(hook), dur, hook_y)
    f = _Font.get(st["font"])
    inside = [dict(w, w=_clean(w["w"])) for w in words if w["s"] >= start - 0.05 and w["s"] < end]
    inside = sorted((w for w in inside if w["w"]), key=lambda w: w["s"])
    for i, w in enumerate(inside[:-1]):
        w["e"] = max(w["s"], min(w["e"], inside[i + 1]["s"]))
    groups = _groups(inside, st["per"])
    for gi, g in enumerate(groups):
        g_start = max(0.0, g[0]["s"] - start)
        g_end = min(g[-1]["e"] + 0.25, groups[gi + 1][0]["s"] if gi + 1 < len(groups) else end) - start
        if g_end - g_start < 0.02:
            continue
        texts = []
        for x in g:
            t = re.sub(r"[.,;:]+$", "", x["w"])
            texts.append(t.upper() if st["upper"] else t)
        if not all(roman.drawable(t) for t in texts):
            # A script the caption font cannot draw (Japanese, Thai, Chinese...): its words cannot be measured, so
            # they are not placed one by one. The line is drawn whole and centred, in a font the system has for it.
            line = " ".join(texts)
            fs = max(44, min(int(st["size"] * 0.7), int(1500 / max(1, len(line)) * 1.6)))
            ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(g_end)},Cap,,60,60,0,,{{\\an5\\pos({W // 2},{cap_y:.0f})\\q0\\fs{fs}}}{line}")
            continue
        size = st["size"]
        gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
        total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        if total > CAP_MAX_W:                                  # a long phrase: shrink it to fit, never run off the frame
            size = max(36, int(size * CAP_MAX_W / total))
            gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
            total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        cy = cap_y - f.cap_shift(size)
        x = (W - total) / 2
        p = st["pop"]
        for i, (w, t) in enumerate(zip(g, texts)):
            wd = f.width(t, size)
            p = max(104, min(st["pop"], 100 + int(2200 / max(wd, 1))))   # long words grow less, so they never touch their neighbours
            pos = f"\\an5\\pos({x + wd / 2:.0f},{cy:.0f})\\fs{size}"
            x += wd + gap
            a = max(g_start, w["s"] - start)
            b = (g[i + 1]["s"] - start) if i + 1 < len(g) else g_end
            base = st["strong"] if _key(w["w"]) in strong else st["base"]
            if a - g_start >= 0.02:                                                   # waiting to be said
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(a)},Cap,,0,0,0,,{{{pos}\\c&H{base}&}}{t}")
            if b - a >= 0.02:                                                         # being said: colour and a quick pop, in front
                ev.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}\\c&H{st['active']}&\\fscx94\\fscy94"
                          f"\\t(0,90,\\fscx{p}\\fscy{p})\\t(90,200,\\fscx{p - 5}\\fscy{p - 5})}}{t}")
            if g_end - b >= 0.02:                                                     # said
                ev.append(f"Dialogue: 1,{_ts(b)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}\\c&H{base}&\\fscx{p - 5}\\fscy{p - 5}"
                          f"\\t(0,90,\\fscx100\\fscy100)}}{t}")
    return head + "\n".join(ev) + "\n"




# ================================================================ rendering
RENDER_SECONDS = 900      # one clip's encode; far beyond normal, so a frozen encoder is stopped, not waited on
QUALITY = {"high": ("18", "medium"), "standard": ("21", "veryfast"), "draft": ("25", "ultrafast")}


def work_root() -> Path:
    """Where clips are made before being moved into place: inside Echo Clip's own folder, so a render that was cut
    off (Echo Clip closed half way) is cleared at the next start instead of piling up in the system's temp."""
    d = config.TMP_DIR / "render"
    d.mkdir(parents=True, exist_ok=True)
    return d


def clear_leftovers():
    for d in work_root().glob("ec*"):
        shutil.rmtree(d, ignore_errors=True)


def caption_style(clip) -> dict:
    """The caption settings for a clip: its own changes (`captions`) on top of its preset (`style`)."""
    own = clip.get("captions") if isinstance(clip.get("captions"), dict) else {}
    return captions.resolve({"preset": own.get("preset") or clip.get("style") or captions.DEFAULT, **own})


def layout_plan(clip, src_w, src_h) -> dict:
    return compose.plan(clip.get("layout", "auto"), src_w, src_h, clip.get("scene"), clip.get("manual"))


def render_clip(src: Path, out_dir: Path, name: str, clip: dict, words: list, src_w: int, src_h: int) -> dict:
    """Writes <name>.mp4 and <name>.jpg into out_dir. `clip` has start and end (seconds in `src`), layout, style,
    highlight_words, and optionally: scene (face.analyse), manual, captions, zoom, normalize, quality;
    caption_data (subtitles.build: word-by-word, translated or bilingual captions; without it the words are used);
    headline_text and headline_set (headline.resolve); music_path and music_set (music.resolve).
    Returns the file names and the layout that was used. Raises with a readable message if anything fails:
    a clip is never written without the captions it was asked to have."""
    from . import headline as hl
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = float(clip["start"]), float(clip["end"])
    dur = end - start
    if dur < 1:
        raise RuntimeError("This clip is shorter than a second, so there is nothing to render.")
    p = layout_plan(clip, src_w, src_h)
    st = caption_style(clip)
    fps = config.OUT_FPS
    # The clip is made in a short work folder (short, plain names: paths with spaces or colons never reach
    # ffmpeg's filter syntax) and moved into place afterwards.
    work = Path(tempfile.mkdtemp(prefix="ec", dir=work_root()))
    try:
        track = None
        if st["enabled"]:
            cd = clip.get("caption_data") or {"kind": "words", "words": words}
            y = captions.cap_y(st, p["cap_y"])
            hi = clip.get("highlight_words") or []
            if cd["kind"] == "phrases" and cd.get("phrases"):
                track = captions.PhraseTrack(cd["phrases"], start, end, st, hi, y, fps)
            else:
                near = [w for w in cd.get("words") or [] if start - 0.05 <= w["s"] < end]
                track = captions.Track(near, start, end, st, hi, y, fps)
        head = None
        hs = clip.get("headline_set") or {**hl.BASE, "enabled": st["hook"]}
        text = clip.get("headline_text") if clip.get("headline_text") is not None else roman.to_roman(clip.get("hook") or "")
        if hs.get("enabled") and st["hook"] and text:
            got = captions.headline_image(text, 0, hs.get("style", "box"))
            if got:
                img, _ = got
                cap_top, cap_bot = (track.y0, track.y0 + track.height) if track else (-10 ** 4, -10 ** 4)
                mid = hl.place(hs.get("position", "auto"), p["hook_y"], cap_top, cap_bot, img.height)
                head = (img, int(max(0, min(H - img.height, mid - img.height / 2))), hs)
        _encode(work, src, start, dur, p, track, head, clip)
        cover_t = min(max(1.2, min(float(hs.get("duration") or 0), 3.0) * 0.6) if head else 1.2, dur / 2)
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{cover_t:.2f}", "-i", "out.mp4", "-frames:v", "1",
             "-q:v", "3", "cover.jpg"], cwd=work)
        shutil.move(str(work / "out.mp4"), str(out_dir / f"{name}.mp4"))
        shutil.move(str(work / "cover.jpg"), str(out_dir / f"{name}.jpg"))
        (out_dir / f"{name}.ass").unlink(missing_ok=True)       # left by the older renderer
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"file": f"{name}.mp4", "cover": f"{name}.jpg", "layout_used": p["used"],
            "caption_label": st["label"]}


def _headline_filter(n, last, img, top, hs, dur):
    """The headline overlay: shown from the start for its duration (0 = the whole clip), with its animation."""
    show = float(hs.get("duration") or 0)
    show = dur if show <= 0 or show >= dur else show
    anim = hs.get("anim", "pop")
    h0 = img.height // 2 * 2
    f = f"[{n}:v]format=rgba"
    if anim in ("pop", "scale"):
        a, s0 = (0.2, 0.55) if anim == "pop" else (0.45, 0.85)
        f += (f",scale=w='trunc({W}*min(1\\,{s0}+{1 - s0}*sin(min(t/{a}\\,1)*PI/2))/2)*2':h=-2:eval=frame"
              f",pad=w={W}:h={h0}:x='(ow-iw)/2':y='(oh-ih)/2':color=black@0:eval=frame")
    if anim != "static":
        f += ",fade=t=in:st=0:d=0.18:alpha=1"
    if show < dur:
        f += f",fade=t=out:st={max(0.0, show - 0.3):.2f}:d=0.3:alpha=1"
    return f + f"[hk];[{last}][hk]overlay=0:{top}:eof_action=pass[vh]", show


def _encode(work: Path, src: Path, start: float, dur: float, p: dict, track, head, clip: dict):
    from . import music as mus
    fps = config.OUT_FPS
    crf, preset = QUALITY.get(clip.get("quality") or "standard", QUALITY["standard"])
    cmd = ["ffmpeg", "-y", "-v", "error", "-hide_banner",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(Path(src).resolve())]
    fc = compose.graph(p, fps, clip.get("zoom") or "none", dur)
    last, n = "v0", 1
    if track is not None:
        cmd += ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{track.height}", "-r", str(fps), "-i", "pipe:0"]
        fc += f";[{n}:v]format=rgba[cap];[{last}][cap]overlay=0:{track.y0}:eof_action=pass:format=auto[vc]"
        last, n = "vc", n + 1
    if head is not None:
        img, top, hs = head
        img.crop((0, 0, W, img.height // 2 * 2)).save(work / "head.png")
        part, show = _headline_filter(n, last, img, top, hs, dur)
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{show:.3f}", "-i", "head.png"]
        fc += ";" + part
        last, n = "vh", n + 1
    fc += f";[{last}]format=yuv420p[v]"
    ms = clip.get("music_set") or {**mus.BASE, "enabled": False}
    has_music = bool(clip.get("music_path")) and Path(clip["music_path"]).is_file() and ms.get("enabled")
    if has_music:
        cmd += ["-stream_loop", "-1", "-i", str(Path(clip["music_path"]).resolve())]
    fc += ";" + mus.audio_graph(dur, ms, has_music, n, clip.get("normalize", True))
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", crf, "-preset", preset, "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-r", str(fps), "-g", str(fps * 2), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{dur:.3f}", "-movflags", "+faststart", "out.mp4"]
    log = (work / "ffmpeg.log").open("w+", encoding="utf-8", errors="replace")
    import time
    t0 = time.time()
    try:
        proc = subprocess.Popen(cmd, cwd=work, stdin=subprocess.PIPE if track is not None else subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=log, creationflags=NO_WINDOW)
        try:
            if track is not None:
                try:
                    for frame in track.frames():
                        proc.stdin.write(frame)
                        if time.time() - t0 > RENDER_SECONDS:
                            raise TimeoutError
                except BrokenPipeError:
                    pass                                 # ffmpeg stopped early: its own error is read below
                finally:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
            proc.wait(timeout=max(5, RENDER_SECONDS - (time.time() - t0)))
        except (TimeoutError, subprocess.TimeoutExpired):
            proc.kill()
            raise RuntimeError("Rendering this clip took too long and was stopped.")
        log.seek(0)
        err = log.read()[-1500:]
    finally:
        log.close()
    if proc.returncode != 0 or not (work / "out.mp4").is_file():
        raise RuntimeError(f"Render failed: {err.strip() or 'ffmpeg stopped with code %s' % proc.returncode}")


def audio_preview(src: Path, clip: dict, seconds: float = 20.0) -> bytes:
    """The clip's sound with its music, as it will be mixed (the first `seconds`), for listening before rendering."""
    from . import music as mus
    start = float(clip["start"])
    dur = min(seconds, float(clip["end"]) - start)
    ms = clip.get("music_set") or {**mus.BASE, "enabled": False}
    has_music = bool(clip.get("music_path")) and Path(clip["music_path"]).is_file() and ms.get("enabled")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(Path(src).resolve())]
    if has_music:
        cmd += ["-stream_loop", "-1", "-i", str(Path(clip["music_path"]).resolve())]
    cmd += ["-filter_complex", mus.audio_graph(dur, ms, has_music, 1, clip.get("normalize", True)), "-map", "[a]",
            "-t", f"{dur:.3f}", "-c:a", "aac", "-b:a", "160k", "-f", "adts", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=NO_WINDOW)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError("Couldn't make the sound preview: " + out.stderr.decode("utf-8", "replace")[-300:])
    return out.stdout


def frame_preview(src: Path, clip: dict, src_w: int, src_h: int):
    """One frame of a clip (a second in), laid out the way it will be rendered, without captions: the background
    for the caption preview. Returns (picture, plan)."""
    from io import BytesIO
    from PIL import Image
    p = layout_plan(clip, src_w, src_h)
    t = float(clip["start"]) + min(1.0, (float(clip["end"]) - float(clip["start"])) / 2)
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(Path(src).resolve()), "-filter_complex",
                          compose.graph(p, config.OUT_FPS) + ";[v0]format=rgb24[v]", "-map", "[v]", "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "png", "pipe:1"], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError("Could not read a frame of the video.")
    return Image.open(BytesIO(out.stdout)).convert("RGBA"), p
