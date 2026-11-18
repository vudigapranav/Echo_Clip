"""Laying a landscape video out on a 1080x1920 frame.

A layout is one or two panels. Each panel takes a rectangle of the source (always with the panel's own shape, so
nothing is ever stretched) and fills a rectangle of the output. Behind them is either a blurred, darkened copy of
the whole frame (so there are no black bars) or nothing, when the panels cover the frame.

    plan(mode, src_w, src_h, scene, manual)  -> the panels, plus where the hook and captions go
    graph(p, fps, zoom, dur)                 -> the ffmpeg filter that draws it, ending in [v0]

`scene` is what face.analyse() found (or None). Smart auto picks a layout from it."""
from . import config

W, H = config.OUT_W, config.OUT_H

MODES = {
    "auto": "Smart auto",
    "stack": "Facecam + gameplay (stacked)",
    "gameplay": "Gameplay focus",
    "facecam": "Facecam focus",
    "blur": "Full frame + blurred fill",
    "zoom": "Zoomed in + blurred fill",
    "crop": "Fill the screen (centre crop)",
    "manual": "Manual positioning",
}
# names an earlier version used, read as the nearest layout here
ALIASES = {"stack_top": "stack", "stack_bottom": "stack", "pip": "stack", "game_focus": "gameplay", "face_focus": "facecam",
           "smart": "auto"}
FACECAM_H = 700          # the facecam panel's height in the stacked layout
BG = "0x0A0812"


def mode_of(name) -> str:
    name = ALIASES.get(name, name)
    return name if name in MODES else "auto"


def _even(v):
    return max(2, int(round(v / 2)) * 2)


def fit(cx, cy, aspect, src_w, src_h, want_w=None):
    """A rectangle of the source with this shape (width / height), centred as near (cx, cy) as the frame allows.
    As wide as `want_w` asks, never wider than fits. Returns (x, y, w, h) in source pixels, all even."""
    w = want_w if want_w else src_w
    w = min(w, src_w, src_h * aspect)
    h = w / aspect
    if h > src_h:
        h, w = src_h, src_h * aspect
    w, h = min(_even(w), src_w // 2 * 2), min(_even(h), src_h // 2 * 2)
    x = min(max(0, cx - w / 2), src_w - w)
    y = min(max(0, cy - h / 2), src_h - h)
    return int(x) // 2 * 2, int(y) // 2 * 2, w, h        # rounded down: never past the right or bottom edge


def _clamp_rect(r, src_w, src_h):
    x, y, w, h = r
    x, y = min(max(0, x), src_w - w), min(max(0, y), src_h - h)
    return int(x) // 2 * 2, int(y) // 2 * 2, w, h


def _facecam_rect(scene, src_w, src_h, aspect):
    """The facecam, cut to the panel's shape. When the whole video was searched, its facecam region is used;
    otherwise a region is worked out around the face: head and shoulders, the face a little above the middle."""
    cam = scene.get("cam")
    if cam:
        x, y, w, h = cam[0] * src_w, cam[1] * src_h, cam[2] * src_w, cam[3] * src_h
        want_w = max(w, h * aspect)                 # the whole facecam fits, whichever way the panel is shaped
        return fit(x + w / 2, y + h / 2, aspect, src_w, src_h, want_w)
    face = scene["face"]
    fx, fy, fw, fh = face[0] * src_w, face[1] * src_h, face[2] * src_w, face[3] * src_h
    want_w = max(fw * 3.4, fh * 3.0 * aspect)
    return fit(fx + fw / 2, fy + fh / 2 + fh * 0.35, aspect, src_w, src_h, want_w)


def _away_from_face(cx, scene, src_w, crop_w):
    """Move a gameplay crop sideways so it does not also show the facecam, when there is room to."""
    cam = (scene or {}).get("cam")
    face = (scene or {}).get("face")
    if cam:
        fx0, fx1 = cam[0] * src_w, (cam[0] + cam[2]) * src_w
    elif face:
        fx0, fx1 = (face[0] - face[2] * 1.4) * src_w, (face[0] + face[2] * 2.4) * src_w
    else:
        return cx
    x0 = cx - crop_w / 2
    if x0 < fx1 and x0 + crop_w > fx0:              # it overlaps the facecam
        left, right = fx0 - crop_w / 2, fx1 + crop_w / 2
        options = [c for c in (left, right) if crop_w / 2 <= c <= src_w - crop_w / 2]
        if options:
            return min(options, key=lambda c: abs(c - cx))
    return cx


def choose(scene) -> str:
    """Smart auto: what fits this moment best."""
    if not scene or not scene.get("face"):
        return "gameplay"
    if scene.get("face_size", 0) >= 0.16:           # a big face: a camera on the streamer, not a small overlay
        return "facecam"
    return "stack"


def plan(mode, src_w, src_h, scene=None, manual=None) -> dict:
    """Panels: [{"src": (x, y, w, h), "dst": (x, y, w, h)}], whether to draw the blurred background, and the
    heights the hook and captions sit at."""
    mode = mode_of(mode)
    picked = choose(scene) if mode == "auto" else mode
    face = (scene or {}).get("face")
    ax, ay = ((scene or {}).get("action") or [0.5, 0.5])
    acx, acy = ax * src_w, ay * src_h
    vertical = src_h >= src_w
    if vertical and picked not in ("manual",):
        picked = "crop"                             # already tall: fill the screen, nothing else to arrange
    if picked in ("stack", "facecam") and not face:
        picked = "gameplay"                          # nobody was found: the layout that needs no face
    p = {"mode": mode, "used": picked, "blur": False, "panels": [], "hook_y": 300, "cap_y": 1380, "face_found": bool(face)}

    def panel(src, dst, main=False):
        p["panels"].append({"src": tuple(int(v) for v in src), "dst": tuple(int(v) for v in dst), "main": main})

    if picked == "blur":
        fh = min(H, _even(W * src_h / src_w))
        panel((0, 0, src_w // 2 * 2, src_h // 2 * 2), (0, (H - fh) // 2, W, fh), True)
        p.update(blur=True, hook_y=max(230, (H - fh) // 2 - 190), cap_y=min(1640, (H + fh) // 2 + 180))
    elif picked == "zoom":
        fh = 1000
        panel(fit(acx, acy, W / fh, src_w, src_h), (0, (H - fh) // 2, W, fh), True)
        p.update(blur=True, hook_y=(H - fh) // 2 - 190, cap_y=(H + fh) // 2 + 180)
    elif picked == "crop":
        cx = face[0] * src_w + face[2] * src_w / 2 if face and vertical is False and scene.get("face_size", 0) >= 0.16 else acx
        panel(fit(cx, src_h / 2, W / H, src_w, src_h), (0, 0, W, H), True)
        p.update(hook_y=300, cap_y=1380)
    elif picked == "gameplay":
        fh = 1350                                   # 4:5, the biggest gameplay that still keeps most of the width
        crop_w = min(src_w, src_h * W / fh)
        src = fit(_away_from_face(acx, scene, src_w, crop_w), acy, W / fh, src_w, src_h)
        top = (H - fh) // 2
        panel(src, (0, top, W, fh), True)
        p.update(blur=True, hook_y=max(170, top - 120), cap_y=top + fh - 300)
    elif picked == "stack":
        fh = FACECAM_H
        panel(_facecam_rect(scene, src_w, src_h, W / fh), (0, 0, W, fh))
        gh = H - fh
        crop_w = min(src_w, src_h * W / gh)
        panel(fit(_away_from_face(acx, scene, src_w, crop_w), acy, W / gh, src_w, src_h), (0, fh, W, gh), True)
        p.update(hook_y=fh + 110, cap_y=fh + gh - 380)
    elif picked == "facecam":
        if scene.get("face_size", 0) >= 0.16:       # the camera is the picture: a tall crop around the face
            fx = face[0] * src_w + face[2] * src_w / 2
            fy = face[1] * src_h + face[3] * src_h / 2
            panel(fit(fx, fy + face[3] * src_h * 0.6, W / H, src_w, src_h), (0, 0, W, H), True)
            p.update(hook_y=260, cap_y=1420)
        else:                                       # a small facecam: the reaction big on top, the game below
            fh = 1160
            panel(_facecam_rect(scene, src_w, src_h, W / fh), (0, 0, W, fh), True)
            gh = H - fh
            panel(fit(_away_from_face(acx, scene, src_w, min(src_w, src_h * W / gh)), acy, W / gh, src_w, src_h), (0, fh, W, gh))
            p.update(hook_y=180, cap_y=fh - 170)
    else:                                           # manual
        _manual(p, src_w, src_h, manual or {}, face)
    return p


def _manual(p, src_w, src_h, m, face):
    """The owner's own arrangement: where the facecam and the gameplay are in the source (fractions of the frame),
    how much of the height the facecam gets, which goes on top, and how far to zoom into the gameplay."""
    def rect(v):
        try:
            x, y, w, h = (min(1.0, max(0.0, float(a))) for a in v)
        except (TypeError, ValueError):
            return None
        return (x, y, max(0.05, w), max(0.05, h)) if w > 0 and h > 0 else None

    fr = rect(m.get("face")) or (face and (face[0] - face[2] * 1.4, face[1] - face[3] * 0.8, face[2] * 3.8, face[3] * 3.0))
    gr = rect(m.get("game")) or (0.0, 0.0, 1.0, 1.0)
    split = min(0.6, max(0.0, float(m.get("split", 0.36) or 0)))
    zoom = min(2.5, max(1.0, float(m.get("zoom", 1.0) or 1.0)))
    face_top = m.get("order", "face_top") != "game_top"
    fh = _even(H * split) if fr and split >= 0.12 else 0
    gh = H - fh

    def area(r, aspect, z=1.0):
        x, y, w, h = r[0] * src_w, r[1] * src_h, r[2] * src_w, r[3] * src_h
        want = min(w, h * aspect) / z
        return fit(x + w / 2, y + h / 2, aspect, src_w, src_h, max(64, want))

    gy, fy = (fh, 0) if face_top else (0, gh)
    if fh:
        p["panels"].append({"src": area(fr, W / fh), "dst": (0, fy, W, fh), "main": False})
    p["panels"].append({"src": area(gr, W / gh, zoom), "dst": (0, gy, W, gh), "main": True})
    p["used"] = "manual"
    p["hook_y"] = (fh + 110) if face_top and fh else 260
    p["cap_y"] = (gy + gh - 380) if face_top else (gh - 300)


def graph(p, fps=30, zoom="none", dur=10.0) -> str:
    """The ffmpeg filter for a plan, reading [0:v] and ending in [v0]. `zoom` "slow" pushes gently into the main
    panel over the clip (8 percent by the end); "punch" is a quicker push in during the first two seconds."""
    panels = p["panels"]
    n = len(panels) + (1 if p["blur"] else 0)
    labels = [f"s{i}" for i in range(n)]
    out = [f"[0:v]fps={fps},setsar=1,split={n}" + "".join(f"[{l}]" for l in labels) + ";"] if n > 1 else [f"[0:v]fps={fps},setsar=1[s0];"]
    base = None
    if p["blur"]:
        out.append(f"[{labels[-1]}]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
                   f"scale={W}:{H},eq=brightness=-0.16:saturation=1.1[bg];")
        base = "bg"
    for i, pn in enumerate(panels):
        x, y, w, h = pn["src"]
        dx, dy, dw, dh = pn["dst"]
        f = f"[{labels[i]}]crop={w}:{h}:{x}:{y},scale={dw}:{dh}:flags=lanczos"
        if pn.get("main") and zoom in ("slow", "punch"):
            amount, over = (0.08, max(1.0, dur)) if zoom == "slow" else (0.1, 2.0)
            f += (f",scale=w='trunc({dw}*(1+{amount}*min(t/{over:.2f},1))/2)*2':h=-2:eval=frame,"
                  f"crop={dw}:{dh}:(iw-ow)/2:(ih-oh)/2")
        out.append(f + f",setsar=1[p{i}];")
    for i, pn in enumerate(panels):
        dx, dy, dw, dh = pn["dst"]
        nxt = "v0" if i == len(panels) - 1 else f"b{i}"
        if base is None:
            out.append(f"[p{i}]pad={W}:{H}:{dx}:{dy}:color={BG}[{nxt}];")
        else:
            out.append(f"[{base}][p{i}]overlay={dx}:{dy}[{nxt}];")
        base = nxt
    return "".join(out).rstrip(";")
