"""Captions drawn by Echo Clip itself (with Pillow), word by word, and laid over the video by ffmpeg.

Why not ffmpeg's own subtitle filter: many ffmpeg builds (Homebrew's, for one) are made without it, and then no
caption could be burned in at all. Drawing them here works with every ffmpeg, and the preview on the settings page
is made by this same code, so what is shown before rendering is what the clip gets.

A style is a plain dict (see PRESETS). A clip's captions are a narrow strip of the frame, redrawn only when
something in it changes, and sent to ffmpeg as raw pictures while it encodes."""
import io
import re
from pathlib import Path

from . import config

W, H = config.OUT_W, config.OUT_H
FONT_DIR = Path(__file__).resolve().parent / "fonts"
MAX_W = 960            # captions never run wider than this, whatever is said

# family -> {weight: file}. All under the SIL Open Font License (licence texts sit next to the files).
FONTS = {
    "Poppins": {600: "Poppins-SemiBold.ttf", 700: "Poppins-Bold.ttf", 800: "Poppins-ExtraBold.ttf", 900: "Poppins-Black.ttf"},
    "Anton": {400: "Anton-Regular.ttf"},
    "Bebas Neue": {400: "BebasNeue-Regular.ttf"},
    "Archivo Black": {900: "ArchivoBlack-Regular.ttf"},
    "Bangers": {400: "Bangers-Regular.ttf"},
}
# Hindi (Devanagari): one variable font, every weight from 100 to 900. Used for any word written in Devanagari,
# whatever the preset's font is, since the Latin caption fonts have no Devanagari letters.
DEVA_FONT = "Noto Sans Devanagari"
DEVA_FILE = "NotoSansDevanagari-Variable.ttf"

BASE = {"enabled": True, "font": "Poppins", "weight": 800, "size": 96, "color": "#FFFFFF", "highlight": "#FFE600",
        "stroke": 7, "stroke_color": "#000000", "shadow": 0, "shadow_color": "#000000",
        "background": "none", "bg_color": "#000000", "bg_opacity": 60,
        "position": "auto", "offset_y": 0, "upper": True, "per": 3, "animation": "pop", "hook": True}

# The looks offered on the page. Anything in BASE can be changed on top of one of these.
PRESETS = {
    "gaming_bold": {"label": "Gaming Bold", "font": "Anton", "weight": 400, "size": 132, "highlight": "#FFE600", "stroke": 9,
                    "shadow": 6, "per": 2},
    "neon_purple": {"label": "Neon Purple", "font": "Poppins", "weight": 900, "size": 100, "highlight": "#C77DFF", "stroke": 6,
                    "stroke_color": "#1A0633", "shadow": 16, "shadow_color": "#A855F7", "per": 3},
    "classic_white": {"label": "Classic White", "font": "Poppins", "weight": 800, "size": 88, "highlight": "#FFFFFF", "stroke": 7,
                      "upper": False, "per": 4},
    "viral_highlight": {"label": "Viral Highlight", "font": "Archivo Black", "weight": 900, "size": 96, "highlight": "#111111",
                        "stroke": 7, "background": "word", "bg_color": "#FFE600", "bg_opacity": 100, "per": 3},
    "minimal_clean": {"label": "Minimal Clean", "font": "Poppins", "weight": 600, "size": 62, "highlight": "#22D3EE", "stroke": 0,
                      "shadow": 0, "background": "box", "bg_color": "#000000", "bg_opacity": 62, "upper": False, "per": 5,
                      "animation": "none"},
    # the three looks of PeakCut (the project Echo Clip is built on), kept so older videos and creators re-render the way they were made
    "bounce": {"label": "Bounce", "font": "Poppins", "weight": 800, "size": 94, "highlight": "#FFF000", "stroke": 7, "per": 3},
    "hype": {"label": "Hype", "font": "Anton", "weight": 400, "size": 120, "highlight": "#FF3C3C", "stroke": 7, "per": 2},
    "clean": {"label": "Clean", "font": "Poppins", "weight": 800, "size": 72, "highlight": "#FFD76B", "stroke": 5, "upper": False, "per": 4},
}
DEFAULT = "gaming_bold"
POSITIONS = {"auto": "Auto", "top": "Top", "middle": "Middle", "bottom": "Bottom"}
BACKGROUNDS = {"none": "None", "box": "Box behind the line", "word": "Box behind the spoken word"}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_NUM = {"size": (36, 180), "stroke": (0, 16), "shadow": (0, 30), "bg_opacity": (0, 100), "offset_y": (-500, 500), "per": (1, 6)}
HOOK = {"font": ("Poppins", 800), "size": 68, "ink": "#14100E", "box": "#FFFFFF", "pad_x": 36, "pad_y": 22, "radius": 26, "max_w": 900}
# Headline looks: box colour (None: no box), text colour, outline
HEADLINE_STYLES = {"box": {"label": "White box", "box": "#FFFFFF", "ink": "#14100E", "stroke": 0},
                   "bold": {"label": "Bold outline", "box": None, "ink": "#FFFFFF", "stroke": 8},
                   "neon": {"label": "Neon purple", "box": "#A855F7", "ink": "#FFFFFF", "stroke": 0},
                   "yellow": {"label": "Yellow box", "box": "#FFE600", "ink": "#111111", "stroke": 0}}


def public() -> dict:
    """What the settings page needs to offer the choices."""
    return {"presets": {k: {"label": v["label"], **resolve(k)} for k, v in PRESETS.items()}, "default": DEFAULT,
            "fonts": {name: sorted(ws) for name, ws in FONTS.items()}, "positions": POSITIONS, "backgrounds": BACKGROUNDS,
            "limits": _NUM}


def clean(style) -> dict:
    """A caption choice from the page, checked: a preset name plus only known settings inside their ranges.
    Returns {"preset": name, ...changes}. Raises ValueError on nonsense."""
    if isinstance(style, str):
        style = {"preset": style}
    if not isinstance(style, dict):
        raise ValueError("Bad caption settings.")
    preset = style.get("preset") if style.get("preset") in PRESETS else DEFAULT
    out = {"preset": preset}
    for k, v in style.items():
        if k not in BASE or v is None or v == "":
            continue
        if k in ("enabled", "upper", "hook"):
            v = bool(v) and v not in ("0", "false", "off")
        elif k in _NUM:
            lo, hi = _NUM[k]
            v = max(lo, min(hi, int(float(v))))
        elif k in ("color", "highlight", "stroke_color", "shadow_color", "bg_color"):
            v = str(v).strip()
            if not _HEX.fullmatch(v):
                raise ValueError("Colours are written like #A855F7.")
            v = v.upper()
        elif k == "font":
            if v not in FONTS:
                raise ValueError("Choose one of the fonts in the list.")
        elif k == "weight":
            v = int(float(v))
        elif k == "position":
            if v not in POSITIONS:
                raise ValueError("Bad caption position.")
        elif k == "background":
            if v not in BACKGROUNDS:
                raise ValueError("Bad caption background.")
        elif k == "animation":
            v = v if v in ("pop", "none") else "pop"
        out[k] = v
    return out


def resolve(style) -> dict:
    """The full set of settings to draw with: the base, the preset, then whatever was changed on top."""
    if isinstance(style, str) or style is None:
        style = {"preset": style or DEFAULT}
    preset = style.get("preset") if style.get("preset") in PRESETS else DEFAULT
    st = {**BASE, **{k: v for k, v in PRESETS[preset].items() if k != "label"}, **{k: v for k, v in style.items() if k in BASE}}
    st["preset"], st["label"] = preset, PRESETS[preset]["label"]
    weights = FONTS.get(st["font"]) or FONTS["Poppins"]
    if st["font"] not in FONTS:
        st["font"] = "Poppins"
    st["weight"] = min(weights, key=lambda w: abs(w - int(st["weight"])))      # the nearest weight this font comes in
    return st


def label(style) -> str:
    return resolve(style)["label"]


# ---------------------------------------------------------------- measuring and drawing
_fonts: dict = {}
DEVA = re.compile(r"[\u0900-\u097F\uA8E0-\uA8FF]")
EMOJI = re.compile(r"(?:[\U0001F1E6-\U0001F1FF]{2}|[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF]"
                   r"[\uFE0F\U0001F3FB-\U0001F3FF]?(?:\u200D[\U0001F300-\U0001FAFF\u2600-\u27BF][\uFE0F]?)*)")
EMOJI_FONTS = ["/System/Library/Fonts/Apple Color Emoji.ttc", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"]

# For words the caption fonts have no letters for (Japanese, Chinese, Thai...): a font the system has for them.
SYSTEM_FONTS = ["/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
                "/System/Library/Fonts/AppleSDGothicNeo.ttc", "/Library/Fonts/Arial Unicode.ttf",
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "C:/Windows/Fonts/msgothic.ttc", "C:/Windows/Fonts/malgun.ttf"]
FALLBACK = "__system__"


def _fallback_file():
    return next((f for f in SYSTEM_FONTS if Path(f).is_file()), None)


def has_deva(text) -> bool:
    return bool(DEVA.search(str(text or "")))


def _check_shaping(texts):
    """Devanagari drawn without shaping comes out broken; better to stop and say how to fix it."""
    if any(has_deva(t) for t in texts):
        from . import shaping
        if not shaping.ok():
            raise RuntimeError(shaping.HELP)


def _font(family, weight, size):
    from PIL import ImageFont
    key = (family, weight, int(size))
    if key not in _fonts:
        if len(_fonts) > 400:
            _fonts.clear()
        if family == DEVA_FONT:
            f = ImageFont.truetype(str(FONT_DIR / DEVA_FILE), int(size))
            try:
                f.set_variation_by_axes([max(100, min(900, int(weight or 700))), 100])
            except Exception:  # noqa: BLE001 - a FreeType without variable fonts: the regular weight
                pass
            _fonts[key] = f
        else:
            path = _fallback_file() if family == FALLBACK else None
            if family == FALLBACK and not path:
                family, weight = "Poppins", 800
            _fonts[key] = ImageFont.truetype(path or str(FONT_DIR / FONTS[family][weight]), int(size))
    return _fonts[key]


def font_for(text, family, weight):
    """The font a word is drawn in: Devanagari words in Noto Sans Devanagari at the same weight, words the caption
    fonts cannot draw in a system font, everything else in the chosen font."""
    if has_deva(text):
        return DEVA_FONT, {400: 700}.get(weight, weight) if family in ("Anton", "Bebas Neue", "Bangers") else weight
    from . import roman
    if not roman.drawable(text):
        return FALLBACK, 0
    return family, weight


def _cap_h(font) -> float:
    return -font.getbbox("H", anchor="ls")[1]


def _rgba(hex_colour, alpha=255):
    h = hex_colour.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(alpha))


def _key(word):
    return re.sub(r"[^a-z0-9'\u0900-\u097F]", "", word.lower())


def _clean(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text)).strip()


def group_words(words, per):
    """Words into the short groups shown together: a few words, never across a pause or the end of a sentence."""
    budget = 16 if per <= 3 else 8 * per - 8
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or sum(len(x["w"]) for x in cur) + len(w["w"]) > budget
                    or w["s"] - cur[-1]["e"] > 0.45 or cur[-1]["w"][-1:] in ".?!\u0964"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def cap_y(style, auto_y) -> int:
    st = resolve(style)
    y = {"top": 430, "middle": 960, "bottom": 1420}.get(st["position"], auto_y)
    return int(max(160, min(H - 200, y + st["offset_y"])))


def _shown(t, upper):
    t = re.sub(r"[.,;:\u0964]+$", "", t)
    return t.upper() if upper and not has_deva(t) else t


def _row(texts, st, size, max_w=MAX_W, min_size=30):
    """One row of words, each in its own font, centred and shrunk to fit. Returns (size, items, x0, x1)."""
    gap_extra = 2 * st["stroke"] + (18 if st["background"] == "word" else 6)
    while True:
        fonts = [font_for(t, st["font"], st["weight"]) for t in texts]
        widths = [_font(f, w, size).getlength(t) for (f, w), t in zip(fonts, texts)]
        gap = _font(st["font"], st["weight"], size).getlength(" ") + gap_extra * size / max(1, st["size"])
        total = sum(widths) + gap * (len(texts) - 1)
        if total <= max_w or size <= min_size:
            break
        size = max(min_size, int(size * max_w / total) - 1)
    x, items = (W - total) / 2, []
    for (f, w), t, wd in zip(fonts, texts, widths):
        items.append({"t": t, "cx": x + wd / 2, "w": wd, "fam": f, "wt": w})
        x += wd + gap
    return size, items, (W - total) / 2, (W + total) / 2


def _wrap(text, st, size, max_lines=2, max_w=MAX_W, min_size=30):
    """A sentence on up to `max_lines` rows, split where the rows come out most even, shrunk until it fits.
    Words are never cut: Devanagari letters stay joined. Returns (size, [row items...])."""
    words = _clean(text).split()
    if not words:
        return size, []
    while True:
        best = None
        for n in range(1, min(max_lines, len(words)) + 1):
            # split into n rows of about equal width
            cuts = _even_cuts(words, n, st, size)
            rows = [_row(r, st, size, max_w=10 ** 6) for r in cuts]
            widest = max(r[3] - r[2] for r in rows)
            if widest <= max_w:
                best = rows
                break
        if best or size <= min_size:
            return size, best or [_row(words, st, size)]
        size = max(min_size, int(size * 0.9))


def _even_cuts(words, n, st, size):
    if n == 1:
        return [words]
    widths = [_font(*font_for(w, st["font"], st["weight"]), size).getlength(w) + size * 0.3 for w in words]
    target, rows, cur, acc = sum(widths) / n, [], [], 0.0
    for w, wd in zip(words, widths):
        if cur and acc + wd / 2 > target * (len(rows) + 1) and len(rows) < n - 1:
            rows.append(cur)
            cur = []
        cur.append(w)
        acc += wd
    rows.append(cur)
    return rows


def _scale_state(t, w):
    """(state, scale%) of a timed word at time t: 0 waiting, 1 being said (pops), 2 said."""
    if t < w["a"]:
        return (0, 100)
    if t < w["b"]:
        dt, p = t - w["a"], w["pop"]
        s = 0.94 + (p - 0.94) * dt / 0.09 if dt < 0.09 else p - 0.05 * min(1.0, (dt - 0.09) / 0.11)
        return (1, int(round((s if p > 1 else 1.0) * 100)))
    dt, p = t - w["b"], w["pop"]
    s = (p - 0.05) + (1 - (p - 0.05)) * min(1.0, dt / 0.09)
    return (2, int(round((s if p > 1 else 1.0) * 100)))


def _paint(img, st, rows, states):
    """Draw rows of words onto a strip. rows: [{"items", "size", "cy", "x0", "x1", "colour", "timed"}]; states: for
    each timed word, (state, scale%), in order."""
    from PIL import Image, ImageDraw, ImageFilter
    d = ImageDraw.Draw(img)
    it = iter(states)
    plan = []
    for r in rows:
        cap = _cap_h(_font(st["font"], st["weight"], r["size"]))
        if st["background"] == "box" and st["bg_opacity"] > 0:
            px, py = r["size"] * 0.42, r["size"] * 0.34
            d.rounded_rectangle([r["x0"] - px, r["cy"] - cap / 2 - py, r["x1"] + px, r["cy"] + cap / 2 + py],
                                radius=r["size"] * 0.28, fill=_rgba(st["bg_color"], st["bg_opacity"] * 2.55))
        for w in r["items"]:
            state = next(it) if r["timed"] else (0, 100)
            plan.append((r, w, state, cap))
    if st["background"] == "word" and st["bg_opacity"] > 0:
        for r, w, (state, scale), cap in plan:
            if r["timed"] and state == 1:
                s = scale / 100
                px, py = r["size"] * 0.16, r["size"] * 0.2
                d.rounded_rectangle([w["cx"] - w["w"] * s / 2 - px, r["cy"] - cap * s / 2 - py,
                                     w["cx"] + w["w"] * s / 2 + px, r["cy"] + cap * s / 2 + py],
                                    radius=r["size"] * 0.18, fill=_rgba(st["bg_color"], st["bg_opacity"] * 2.55))

    def put(layer, fill_of, stroke_fill, dx=0, dy=0):
        for r, w, (state, scale), cap in plan:
            s = scale / 100
            f = _font(w["fam"], w["wt"], r["size"] * s)
            boxed = st["background"] == "word" and r["timed"] and state == 1
            stroke = 0 if boxed else max(0, round(st["stroke"] * r["size"] / max(1, st["size"])))
            layer.text((w["cx"] + dx, r["cy"] + cap * s / 2 + dy), w["t"], font=f, anchor="ms",
                       fill=fill_of(r, w, state), stroke_width=stroke, stroke_fill=stroke_fill)

    if st["shadow"] > 0:
        glow = st["shadow_color"].upper() not in ("#000000", st["stroke_color"].upper())
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        col = _rgba(st["shadow_color"], 255 if glow else 190)
        off = 0 if glow else max(2, st["shadow"] // 2)
        put(ImageDraw.Draw(sh), lambda r, w, s: col, col, off, off)
        sh = sh.filter(ImageFilter.GaussianBlur(st["shadow"] * (0.7 if glow else 0.35)))
        if glow:                                   # a glow reads better a little stronger than one blurred copy
            sh = Image.alpha_composite(sh, sh)
        img.alpha_composite(sh)
        d = ImageDraw.Draw(img)
    base, hi = _rgba(st["color"]), _rgba(st["highlight"])
    put(d, lambda r, w, state: r.get("colour") or (hi if state == 1 or (w.get("strong") and st["background"] != "word") else base),
        _rgba(st["stroke_color"]))


class Track:
    """One clip's captions as a strip of the frame, word by word. `y0` and `height` say where the strip sits;
    frames() gives the strip for every frame of the clip, as raw RGBA bytes. Hindi words are drawn in Devanagari
    with full shaping; each word keeps its own timing, and a word is never split."""

    def __init__(self, words, start, end, style, highlight=(), y=1380, fps=30):
        self.st = st = resolve(style)
        self.fps, self.dur = fps, max(0.1, end - start)
        self.n_frames = max(1, int(round(self.dur * fps)))
        size = st["size"]
        pad = int(size * 0.75 + st["stroke"] + st["shadow"] * 2 + 24)
        self.height = (int(size * 1.25) + 2 * pad) // 2 * 2
        self.y0 = int(max(0, min(H - self.height, y - self.height // 2)))
        self.cy = y - self.y0                       # where the middle of a capital letter sits, inside the strip
        self.groups = self._layout(words, start, end, set(_key(h) for h in highlight if _key(h)))
        self._blank = bytes(W * self.height * 4)

    def _layout(self, words, start, end, strong):
        st = self.st
        inside = [dict(w, w=_clean(w["w"])) for w in words if start - 0.05 <= w["s"] < end]
        inside = sorted((w for w in inside if w["w"]), key=lambda w: w["s"])
        _check_shaping([w["w"] for w in inside])
        for i, w in enumerate(inside[:-1]):
            w["e"] = max(w["s"], min(w["e"], inside[i + 1]["s"]))
        raw = group_words(inside, st["per"])
        out = []
        for gi, g in enumerate(raw):
            a = max(0.0, g[0]["s"] - start)
            b = min(g[-1]["e"] + 0.25, raw[gi + 1][0]["s"] if gi + 1 < len(raw) else end) - start
            if b - a < 0.02:
                continue
            size, items, x0, x1 = _row([_shown(x["w"], st["upper"]) for x in g], st, st["size"])
            for i, (w, item) in enumerate(zip(g, items)):
                item.update(a=max(a, w["s"] - start), b=(g[i + 1]["s"] - start) if i + 1 < len(g) else b,
                            strong=_key(w["w"]) in strong,
                            # long words grow less, so they never touch their neighbours
                            pop=1.0 if st["animation"] == "none" else max(1.04, min(1.13, 1 + 22 / max(item["w"], 1))))
            out.append({"a": a, "b": b, "rows": [{"items": items, "size": size, "cy": self.cy, "x0": x0, "x1": x1, "timed": True}]})
        return out

    def _state(self, t):
        """What the strip shows at time t, as something that can be compared with the frame before."""
        for gi, g in enumerate(self.groups):
            if g["a"] <= t < g["b"]:
                return (gi, *[_scale_state(t, w) for r in g["rows"] if r["timed"] for w in r["items"]])
        return None

    def image(self, t):
        """The strip at time t, as a picture (see-through where there is no text)."""
        from PIL import Image
        img = Image.new("RGBA", (W, self.height), (0, 0, 0, 0))
        key = self._state(t)
        if key is not None:
            _paint(img, self.st, self.groups[key[0]]["rows"], key[1:])
        return img

    def frames(self):
        """Every frame's strip, in order. A strip is only drawn again when what it shows has changed."""
        last_key, last = object(), self._blank
        for i in range(self.n_frames):
            key = self._state((i + 0.5) / self.fps)
            if key != last_key:
                last_key = key
                last = self._blank if key is None else self.image((i + 0.5) / self.fps).tobytes()
            yield last


class PhraseTrack(Track):
    """Captions a phrase at a time: a translation on its own, or two languages at once (bilingual).

    phrases: [{"s", "e", "main": {"words": [timed words]} or {"text": str}, "sub": str or None, "sub_first": bool}]
    The main line is the one spoken (word by word when its words have timings) or a translation (the whole phrase,
    shown for as long as it was being said: translated words have no timings of their own, and none are made up).
    The sub line is the other language, smaller, under or over it."""

    def __init__(self, phrases, start, end, style, highlight=(), y=1380, fps=30):
        self.st = st = resolve(style)
        self.fps, self.dur = fps, max(0.1, end - start)
        self.n_frames = max(1, int(round(self.dur * fps)))
        strong = set(_key(h) for h in highlight if _key(h))
        _check_shaping([p.get("sub") or "" for p in phrases] + [p["main"].get("text") or " ".join(w["w"] for w in p["main"].get("words") or [])
                                                                 for p in phrases])
        two = any(p.get("sub") for p in phrases)
        self.main_size = int(st["size"] * (0.78 if two else 0.86))
        self.sub_size = max(38, int(self.main_size * 0.62))
        built = [self._build(p, start, end, strong) for p in phrases]
        built = [b for b in built if b]
        tallest = max([b["height"] for b in built] or [self.main_size * 1.3])
        pad = int(st["stroke"] + st["shadow"] * 2 + 24)
        self.height = (int(tallest) + 2 * pad) // 2 * 2
        self.y0 = int(max(0, min(H - self.height, y - self.height // 2)))
        self.cy = y - self.y0
        for b in built:                               # rows were laid out around 0: move them to the strip's middle
            for r in b["rows"]:
                r["cy"] += self.cy
        self.groups = built
        self._blank = bytes(W * self.height * 4)

    def _build(self, p, start, end, strong):
        st = self.st
        a, b = max(0.0, p["s"] - start), min(end, p["e"] + 0.2) - start
        if b - a < 0.05:
            return None
        rows_main, rows_sub = [], []
        words = [dict(w, w=_clean(w["w"])) for w in (p["main"].get("words") or []) if _clean(w["w"])]
        if words:
            size, items, x0, x1 = _row([_shown(w["w"], st["upper"]) for w in words], st, self.main_size, min_size=34)
            for i, (w, item) in enumerate(zip(words, items)):
                item.update(a=max(a, w["s"] - start), b=(words[i + 1]["s"] - start) if i + 1 < len(words) else b,
                            strong=_key(w["w"]) in strong, pop=1.0 if st["animation"] == "none" else max(1.03, min(1.09, 1 + 16 / max(item["w"], 1))))
            rows_main = [{"items": items, "size": size, "x0": x0, "x1": x1, "timed": True}]
        elif p["main"].get("text"):
            size, rows = _wrap(_shown(p["main"]["text"], False) if has_deva(p["main"]["text"]) else
                               (p["main"]["text"].upper() if st["upper"] else p["main"]["text"]), st, self.main_size, 2, min_size=34)
            rows_main = [{"items": r[1], "size": r[0], "x0": r[2], "x1": r[3], "timed": False} for r in rows]
        if p.get("sub"):
            size, rows = _wrap(p["sub"], st, self.sub_size, 2, min_size=30)
            rows_sub = [{"items": r[1], "size": r[0], "x0": r[2], "x1": r[3], "timed": False, "colour": _rgba("#F1EEFB")}
                        for r in rows]
        order = (rows_sub + rows_main) if p.get("sub_first") else (rows_main + rows_sub)
        if not order:
            return None
        heights = [r["size"] * 1.18 for r in order]
        total = sum(heights) + (len(order) - 1) * self.main_size * 0.12
        y = -total / 2
        for r, h in zip(order, heights):
            r["cy"] = y + h / 2
            y += h + self.main_size * 0.12
        return {"a": a, "b": b, "rows": order, "height": total + self.main_size * 0.4}


# ---------------------------------------------------------------- the headline
def _emoji_font():
    from PIL import ImageFont
    if "emoji" not in _fonts:
        path = next((f for f in EMOJI_FONTS if Path(f).is_file()), None)
        try:
            _fonts["emoji"] = ImageFont.truetype(path, 160) if path else None   # colour emoji come in one size
        except OSError:
            _fonts["emoji"] = None
    return _fonts["emoji"]


def _runs(text):
    """A headline as pieces: ("emoji", "😂") or ("text", "SPEED REACTS")."""
    out, pos = [], 0
    for m in EMOJI.finditer(text):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        out.append(("emoji", m.group(0)))
        pos = m.end()
    if pos < len(text):
        out.append(("text", text[pos:]))
    return out


def _measure_token(tok, size, fam, weight):
    """Width of one word of a headline (which may hold emoji)."""
    w = 0.0
    for kind, piece in _runs(tok):
        if kind == "emoji":
            w += size * 1.12 * max(1, len(EMOJI.findall(piece)))
        else:
            f, wt = font_for(piece, fam, weight)
            w += _font(f, wt, size).getlength(piece)
    return w


def headline_image(text, y, style="box", max_lines=3, size=None):
    """The headline: text (English, Hindi or both, emoji allowed) in the chosen look, on up to three balanced lines,
    shrunk until it fits. Returns (picture, top edge in the frame) or None when there is no headline."""
    from PIL import Image, ImageDraw
    text = _clean(text)
    if not _emoji_font():
        text = EMOJI.sub("", text).strip()
    words = text.split()
    if not words:
        return None
    _check_shaping([text])
    look = HEADLINE_STYLES.get(style, HEADLINE_STYLES["box"])
    fam, weight = HOOK["font"]
    size = size or (HOOK["size"] if look["box"] else 84)
    max_w = HOOK["max_w"] if look["box"] else 960
    while True:
        lines = None
        for n in range(1, min(max_lines, len(words)) + 1):
            cand = _balanced(words, n, size, fam, weight)
            if max(_measure_token(" ".join(l), size, fam, weight) for l in cand) <= max_w:
                lines = cand
                break
        if lines or size <= 34:
            lines = lines or _balanced(words, min(max_lines, len(words)), size, fam, weight)
            break
        size = int(size * 0.92)
    line_h = size * (1.32 if has_deva(text) else 1.16)
    widest = max(_measure_token(" ".join(l), size, fam, weight) for l in lines)
    pad_x, pad_y = (HOOK["pad_x"], HOOK["pad_y"]) if look["box"] else (look["stroke"] + 6, look["stroke"] + 6)
    bw, bh = int(widest + 2 * pad_x), int(line_h * len(lines) + 2 * pad_y)
    img = Image.new("RGBA", (W, bh + 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0 = (W - bw) / 2
    if look["box"]:
        d.rounded_rectangle([x0, 2, x0 + bw, 2 + bh], radius=min(HOOK["radius"], bh / 2), fill=_rgba(look["box"]))
    cap = _cap_h(_font(fam, weight, size))
    ef = _emoji_font()
    for i, line in enumerate(lines):
        s = " ".join(line)
        lw = _measure_token(s, size, fam, weight)
        x = (W - lw) / 2
        base = 2 + bh / 2 - line_h * (len(lines) - 1) / 2 + i * line_h + cap / 2
        for kind, piece in _runs(s):
            if kind == "emoji":
                for e in EMOJI.findall(piece):
                    g = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
                    ImageDraw.Draw(g).text((20, 20), e, font=ef, embedded_color=True)
                    box = g.getbbox()
                    if box:
                        g = g.crop(box)
                        side = int(size * 1.02)
                        g = g.resize((side, int(side * g.height / max(1, g.width))))
                        img.alpha_composite(g, (int(x + size * 0.05), int(base - cap / 2 - g.height / 2)))
                    x += size * 1.12
            else:
                for j, wtxt in enumerate(piece.split(" ")):
                    if wtxt:
                        f, wt = font_for(wtxt, fam, weight)
                        font = _font(f, wt, size)
                        d.text((x, base), wtxt, font=font, anchor="ls", fill=_rgba(look["ink"]),
                               stroke_width=look["stroke"], stroke_fill=_rgba("#000000"))
                        x += font.getlength(wtxt)
                    if j < len(piece.split(" ")) - 1:
                        x += _font(fam, weight, size).getlength(" ")
    return img, int(max(0, min(H - bh - 4, y - bh / 2)))


def _balanced(words, n, size, fam, weight):
    if n <= 1:
        return [words]
    widths = [_measure_token(w, size, fam, weight) for w in words]
    target, rows, cur, acc = sum(widths) / n, [], [], 0.0
    for w, wd in zip(words, widths):
        if cur and acc + wd / 2 > target * (len(rows) + 1) and len(rows) < n - 1:
            rows.append(cur)
            cur = []
        cur.append(w)
        acc += wd
    rows.append(cur)
    return rows


def hook_image(text, y):
    """The original white-box heading (kept for anything that still asks for it)."""
    return headline_image(text, y, "box", max_lines=2)


SAMPLE = [("no", 0.0), ("way", 0.3), ("he", 0.62), ("just", 0.8), ("did", 1.05), ("that", 1.3)]


def preview(style, background=None, hook="WAIT FOR IT", hook_y=300, auto_y=1380, at=0.45, width=405, phrases=None,
            headline_style="box", sample=None) -> bytes:
    """A picture of the look, made by the same code that makes the clips. `background` is a 1080x1920 picture
    (a frame of the real video laid out the way the clip will be); without one a plain dark frame is used.
    `phrases` (PhraseTrack shape) or `sample` (timed words) show the real captions of a clip."""
    from PIL import Image, ImageDraw
    st = resolve(style)
    if background is not None:
        frame = background.convert("RGBA").resize((W, H))
    else:
        frame = Image.new("RGBA", (W, H), (14, 10, 26, 255))
        d = ImageDraw.Draw(frame)
        for i in range(0, H, 4):                     # a quiet gradient, so light and dark text can both be judged
            v = i / H
            d.rectangle([0, i, W, i + 4], fill=(int(22 + 30 * v), int(14 + 10 * v), int(46 + 50 * v), 255))
    if st["enabled"]:
        if phrases:
            tr = PhraseTrack(phrases, phrases[0]["s"], phrases[-1]["e"] + 1, st, [], cap_y(st, auto_y))
            t = (phrases[0]["s"] + phrases[0]["e"]) / 2 - phrases[0]["s"]
        else:
            words = sample or [{"w": w, "s": s, "e": s + 0.28} for w, s in SAMPLE]
            tr = Track(words, words[0]["s"], words[-1]["e"] + 1, st, ["way"], cap_y(st, auto_y))
            t = at
        frame.alpha_composite(tr.image(t), (0, tr.y0))
    if st["hook"] and hook:
        got = headline_image(hook, hook_y, headline_style)
        if got:
            frame.alpha_composite(got[0], (0, got[1]))
    out = io.BytesIO()
    frame.convert("RGB").resize((width, width * H // W)).save(out, "JPEG", quality=88)
    return out.getvalue()
