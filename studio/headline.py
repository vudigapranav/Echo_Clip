"""The headline: a short line on top of the clip ("SPEED SEES HIS GTA 6 CHARACTER 😭"), in its own language.

The AI writes it in English, Hindi and Hinglish in the same request that ranks the moments (highlights.rank), so
choosing another language, editing it, restyling it or switching it off never asks the AI again. With no AI, the
headline is words that were really said in the clip.

Settings (a clip's "headline", over the video's defaults):
    enabled    on / off
    text       the owner's own wording (wins over the AI's)
    lang       source | en | hi | hinglish   ("source" follows the language spoken)
    style      box | bold | neon | yellow   (captions.HEADLINE_STYLES)
    anim       pop | fade | scale | static
    duration   seconds on screen from the start (0 = the whole clip); 3 by default
    position   auto | top | upper | middle"""
import re

LANGS = {"source": "Same as the video", "en": "English", "hi": "Hindi", "hinglish": "Hinglish"}
ANIMS = {"pop": "Pop in", "fade": "Fade in", "scale": "Scale in", "static": "No animation"}
POSITIONS = {"auto": "Auto (clear of faces and captions)", "top": "Top", "upper": "Upper third", "middle": "Middle"}
BASE = {"enabled": True, "text": "", "lang": "source", "style": "box", "anim": "pop", "duration": 3.0, "position": "auto"}


def clean(v) -> dict:
    from . import captions
    if not isinstance(v, dict):
        raise ValueError("Bad headline settings.")
    out = {}
    for k, x in v.items():
        if k not in BASE or x is None:
            continue
        if k == "enabled":
            x = bool(x) and x not in ("0", "false", "off")
        elif k == "text":
            x = re.sub(r"[\x00-\x1f\x7f]+", " ", str(x)).strip()[:120]
        elif k == "lang":
            x = x if x in LANGS else "source"
        elif k == "style":
            x = x if x in captions.HEADLINE_STYLES else "box"
        elif k == "anim":
            x = x if x in ANIMS else "pop"
        elif k == "duration":
            x = max(0.0, min(60.0, float(x)))
        elif k == "position":
            x = x if x in POSITIONS else "auto"
        out[k] = x
    return out


def resolve(clip_setting, job_setting=None) -> dict:
    return {**BASE, **(job_setting or {}), **(clip_setting or {})}


def lang_for(setting_lang, src_lang) -> str:
    """The language the headline is shown in."""
    if setting_lang and setting_lang != "source":
        return setting_lang
    src = (src_lang or "en").lower()
    return "hinglish" if src == "hinglish" else "hi" if src in ("hi", "ur") else "en"


def text_for(clip, setting, src_lang) -> str:
    """What the headline says: the owner's own text, else the AI's in the chosen language (falling back to the
    other languages it wrote, then the hook), else words that were said."""
    if setting.get("text"):
        return setting["text"]
    hs = clip.get("headlines") or {}
    lang = lang_for(setting.get("lang"), src_lang)
    for k in (lang, "en", "hinglish", "hi", "local"):
        if hs.get(k):
            return hs[k]
    return clip.get("hook") or ""


def place(position, plan_hook_y, cap_top, cap_bottom, box_h) -> int:
    """Where the middle of the headline goes: the chosen spot, moved so it never covers the captions."""
    y = {"top": 200, "upper": 520, "middle": 960}.get(position, plan_hook_y)
    top, bottom = y - box_h / 2, y + box_h / 2
    if bottom > cap_top - 20 and top < cap_bottom + 20:          # would cover the captions: go above them
        y = cap_top - 30 - box_h / 2
        if y - box_h / 2 < 60:                                  # no room above: below them instead
            y = cap_bottom + 30 + box_h / 2
    return int(max(box_h / 2 + 40, min(1920 - box_h / 2 - 40, y)))
