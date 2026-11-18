"""Text shaping for scripts like Devanagari (Hindi), where letters join and vowel signs move: क्या, क्षत्रिय, प्रतिष्ठित.

Pillow can only draw these correctly with its "raqm" layout, which needs the FriBiDi library at run time
(`brew install fribidi` on a Mac). Without it Hindi is drawn letter by letter: broken conjuncts, the ि sign after
its consonant. So Echo Clip makes sure raqm is on when it starts, and refuses to draw Hindi without it rather than
drawing it wrong.

On macOS the library sits in Homebrew's folder, which programs do not search unless told to when they start; so
the server starts itself once more with that folder added (ensure()). Nothing else is changed."""
import os
import sys

FOLDERS = ("/opt/homebrew/lib", "/usr/local/lib")
HELP = ("Hindi (Devanagari) text needs the FriBiDi library to be drawn correctly. Install it with:  brew install fribidi"
        "  and start Echo Clip again.")


def ok() -> bool:
    try:
        from PIL import features
        return bool(features.check("raqm"))
    except Exception:  # noqa: BLE001
        return False


def ensure():
    """Call first thing in a program that draws text. Restarts this same program with Homebrew's library folder on
    the search path when that is what raqm needs; does nothing otherwise."""
    if sys.platform != "darwin" or os.environ.get("ECHO_SHAPING_TRIED") or ok():
        return
    have = [d for d in FOLDERS if os.path.exists(os.path.join(d, "libfribidi.0.dylib"))]
    if not have:
        return                                      # said plainly when Hindi has to be drawn (HELP)
    env = dict(os.environ, ECHO_SHAPING_TRIED="1")
    env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(have + [p for p in env.get("DYLD_FALLBACK_LIBRARY_PATH", "").split(":") if p])
    os.execve(sys.executable, list(getattr(sys, "orig_argv", [sys.executable, *sys.argv])), env)


def env_for_children() -> dict:
    """Environment for a program started from here (tests), with raqm reachable."""
    have = [d for d in FOLDERS if os.path.exists(os.path.join(d, "libfribidi.0.dylib"))]
    env = dict(os.environ)
    if sys.platform == "darwin" and have:
        env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(have + [p for p in env.get("DYLD_FALLBACK_LIBRARY_PATH", "").split(":") if p])
    return env
