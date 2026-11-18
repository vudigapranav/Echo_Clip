"""Looking at a clip before it is laid out: where the streamer's face is, where the action is, and how often the
picture cuts. Everything runs here (YuNet through OpenCV, about 230 KB), on a dozen small frames per clip.

The answer is a plain dict, kept with the clip so a re-render never looks again:

    {"ok": True, "face": [x, y, w, h] or None,   # the face, as fractions of the frame (0..1)
     "face_hits": 0.8,                            # share of sampled frames the face was seen in
     "face_size": 0.12,                           # face width as a share of the frame width
     "action": [cx, cy],                          # centre of movement outside the face, as fractions
     "cuts": 2, "frames": 12, "note": ""}

If OpenCV or the model is missing, or a frame cannot be read, the answer still comes back ("ok": False and a note),
and the layout falls back to one that needs no face."""
import subprocess
from pathlib import Path

import numpy as np

from .media import NO_WINDOW

MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"
YUNET_MODEL_PATH = MODEL            # the name the first version of this module used
SAMPLE_W = 640                      # frames are looked at this wide: plenty for a facecam, quick to read
MIN_SCORE = 0.75

_net = {}


def available() -> bool:
    try:
        import cv2  # noqa: F401  (only whether it can be loaded)
    except Exception:  # noqa: BLE001
        return False
    return MODEL.is_file()


def _detector(w, h):
    import cv2
    det = _net.get("d")
    if det is None:
        det = _net["d"] = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), MIN_SCORE, 0.3, 50)
    det.setInputSize((w, h))
    return det


def grab(src, t: float, src_w: int, src_h: int):
    """One frame at time t, SAMPLE_W wide, as a BGR array (None if it cannot be read)."""
    w = SAMPLE_W
    h = max(2, int(round(src_h * w / max(1, src_w) / 2)) * 2)
    try:
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(src), "-frames:v", "1",
                            "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                           capture_output=True, timeout=60, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or len(p.stdout) != w * h * 3:
        return None
    return np.frombuffer(p.stdout, np.uint8).reshape(h, w, 3)


def faces(frame) -> list:
    """Faces in one frame: [(x, y, w, h, score)] in pixels of that frame, best first."""
    h, w = frame.shape[:2]
    _ok, found = _detector(w, h).detect(frame)
    if found is None:
        return []
    out = [(float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[4])) for f in found if f[4] >= MIN_SCORE]
    return sorted(out, key=lambda f: -f[4] * f[2] * f[3])


def _clusters(found, fw, fh, n_frames):
    """Faces that keep turning up in the same place, across many frames: the streamer (and anyone sitting with
    them) in a facecam. A face in the game or a cutscene moves around and does not stay. Returns
    [(box as fractions, share of frames)], most persistent first."""
    left = list(found)                       # (frame index, x, y, w, h)
    out = []
    while left:
        best = None
        for b in left:
            cx, cy = b[1] + b[3] / 2, b[2] + b[4] / 2
            near = [o for o in left if abs(o[1] + o[3] / 2 - cx) < max(b[3], o[3]) * 0.9
                    and abs(o[2] + o[4] / 2 - cy) < max(b[4], o[4]) * 0.9 and 0.5 < o[3] / max(1, b[3]) < 2]
            if best is None or len({o[0] for o in near}) > len({o[0] for o in best}):
                best = near
        share = len({o[0] for o in best}) / max(1, n_frames)
        m = np.median(np.array([o[1:5] for o in best]), axis=0)
        out.append(([float(m[0] / fw), float(m[1] / fh), float(m[2] / fw), float(m[3] / fh)], round(share, 2)))
        left = [o for o in left if o not in best]
    return sorted(out, key=lambda c: -c[1])


def _union(boxes):
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return [x0, y0, x1 - x0, y1 - y0]


def cam_region(faces_box, face_w, face_h):
    """The facecam around the streamer's face(s), as fractions of the frame: head, shoulders and a little room."""
    x, y, w, h = faces_box
    x0, x1 = max(0.0, x - face_w * 1.1), min(1.0, x + w + face_w * 1.1)
    y0, y1 = max(0.0, y - face_h * 0.9), min(1.0, y + h + face_h * 1.7)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def find_facecam(src, duration: float, src_w: int, src_h: int, samples: int = 36, start: float = 0.0) -> dict:
    """Where the streamer's facecam is, looked for across the whole video once (about half a minute for a long
    one). Never raises. {"ok", "face" (all persistent faces together), "cam" (the facecam around them),
    "face_size" (one face's width, as a share of the frame), "share", "frames", "note"}"""
    out = {"ok": False, "face": None, "cam": None, "face_size": 0.0, "share": 0.0, "frames": 0, "note": ""}
    if not available():
        out["note"] = "Face detection is not installed, so the layout does not look for the streamer."
        return out
    span = max(1.0, duration - start)
    found, n, fw, fh = [], 0, SAMPLE_W, 0
    try:
        for i in range(samples):
            f = grab(src, start + span * (i + 0.5) / samples, src_w, src_h)
            if f is None:
                continue
            fh = f.shape[0]
            n += 1
            for x, y, w, h, _s in faces(f)[:4]:
                found.append((i, x, y, w, h))
    except Exception as e:  # noqa: BLE001 - a model that will not run: the layout simply ignores faces
        out["note"] = f"Face detection could not run ({str(e)[:80]})."
        return out
    out["frames"] = n
    if n < 4:
        out["note"] = "Too few frames could be read to look for a facecam."
        return out
    keep = [c for c in _clusters(found, fw, fh, n) if c[1] >= 0.3]
    out["ok"] = True
    if not keep:
        out["note"] = "No facecam was found: no face stays in one place through the video."
        return out
    # the most persistent face is the streamer; others join only when they are there nearly as often and sit
    # right beside them (a second person on the same camera), never a face somewhere else in the picture
    top = keep[0][0]
    boxes = [top] + [b for b, sh in keep[1:4] if sh >= keep[0][1] * 0.6
                     and abs(b[0] + b[2] / 2 - top[0] - top[2] / 2) < top[2] * 4.5
                     and abs(b[1] + b[3] / 2 - top[1] - top[3] / 2) < top[3] * 2]
    big = max(boxes, key=lambda b: b[2])
    out.update(face=[round(v, 4) for v in _union(boxes)], face_size=round(big[2], 4), share=keep[0][1],
               cam=cam_region(_union(boxes), big[2], big[3]))
    return out


def analyse(src, start: float, end: float, src_w: int, src_h: int, facecam=None, samples: int = 10) -> dict:
    """One clip: where the action is (outside the facecam) and how often the picture cuts. `facecam` is what
    find_facecam() said for the whole video; it is copied in, so the layout has everything in one place.
    Never raises."""
    fc = facecam or {}
    out = {"ok": False, "face": fc.get("face"), "cam": fc.get("cam"), "face_size": fc.get("face_size", 0.0),
           "action": [0.5, 0.5], "cuts": 0, "frames": 0, "note": fc.get("note", "")}
    dur = max(0.5, end - start)
    times = [start + dur * (i + 0.5) / samples for i in range(samples)]
    frames = [f for f in (grab(src, t, src_w, src_h) for t in times) if f is not None]
    out["frames"] = len(frames)
    if len(frames) < 2:
        out["note"] = "The video's frames could not be read, so the layout does not depend on them."
        return out
    fh, fw = frames[0].shape[:2]
    gray = [f.astype(np.float32).mean(axis=2) for f in frames]
    # a cut is a big change between two samples; a moving camera changes the picture a little, a cut changes it all
    diffs = [float(np.abs(a - b).mean()) for a, b in zip(gray, gray[1:])]
    out["cuts"] = sum(1 for d in diffs if d > 38)
    motion = np.zeros_like(gray[0])
    for a, b, d in zip(gray, gray[1:], diffs):
        if d <= 38:                                 # movement inside a shot, not the jump of a cut
            motion += np.abs(a - b)
    if out["cam"]:                                  # the streamer moving is not the game's action
        x, y, w, h = out["cam"]
        motion[int(y * fh):int((y + h) * fh) + 1, int(x * fw):int((x + w) * fw) + 1] = 0
    # the edges of a stream are overlays (viewer counters, chat, alerts): their movement is not the game's
    motion[: fh // 10] = 0
    motion[:, : int(fw * 0.12)] = 0
    motion[:, int(fw * 0.85):] = 0
    total = float(motion.sum())
    if total > 1e-3:
        cols, rows = motion.sum(axis=0), motion.sum(axis=1)
        cx = float((cols * np.arange(fw)).sum() / total) / fw
        cy = float((rows * np.arange(fh)).sum() / total) / fh
        # pulled towards the middle: a single busy corner should not swing the whole crop to the edge
        out["action"] = [round(0.5 + (cx - 0.5) * 0.7, 4), round(0.5 + (cy - 0.5) * 0.7, 4)]
    out["ok"] = True
    return out
