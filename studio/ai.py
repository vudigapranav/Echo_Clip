"""Talking to the AI service. You bring your own key, and choose the service and model in the dashboard
(Settings, AI) or in .env. What is saved from the dashboard is kept in ai.json in the data folder and wins.

    PEAKCUT_AI_PROVIDER   featherless | gemini | openai | xai | groq | openrouter | anthropic | ollama | custom
    PEAKCUT_AI_MODELS     one or more model ids, comma separated, tried in this order
    PEAKCUT_AI_KEY        your key (or PEAKCUT_AI_KEY_FILE, a file holding it)
    PEAKCUT_AI_BASE_URL   only for "custom" (any service that speaks the OpenAI chat format), or to override

Every service is asked for the same thing: JSON that follows a small schema. Gemini takes the schema directly.
The others are told the schema in the prompt and asked for a JSON object, which works with any chat model.

Only text is ever sent: a compact transcript of candidate moments. Video, audio and frames stay on this machine.

Keys saved from the dashboard are kept in the macOS Keychain when it is there (ai.json then only says so), and
otherwise in ai.json, readable by this user only. They never go back to the page, into a log, or into git.

Spending is guarded three ways: "local only" switches the AI off; a second ("fallback") service is used only
when the owner ticked that it may be; and a monthly budget, worked out from the tokens each call used and the
prices the owner entered, stops AI calls when it is reached (clips are then ranked locally)."""
import json
import os
import re
import subprocess
import sys
import threading
import time

import httpx

from . import config

# name -> (label shown on the pages, address, how it is spoken to, where to get a key, an example model id)
PROVIDERS = {
    "featherless": ("Featherless AI", "https://api.featherless.ai/v1", "openai", "https://featherless.ai/account/api-keys",
                    "Qwen/Qwen3-Next-80B-A3B-Instruct"),
    "gemini": ("Gemini", "https://generativelanguage.googleapis.com/v1beta/models", "gemini", "https://aistudio.google.com/apikey", "gemini-2.5-flash"),
    "openai": ("OpenAI", "https://api.openai.com/v1", "openai", "https://platform.openai.com/api-keys", "gpt-4o-mini"),
    "xai": ("Grok", "https://api.x.ai/v1", "openai", "https://console.x.ai", "grok-4"),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "openai", "https://console.groq.com/keys", "llama-3.3-70b-versatile"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "openai", "https://openrouter.ai/keys", "google/gemini-2.5-flash"),
    "anthropic": ("Claude (API)", "https://api.anthropic.com/v1", "anthropic", "https://console.anthropic.com", ""),
    "ollama": ("Ollama (on this machine)", "http://127.0.0.1:11434/v1", "openai", "https://ollama.com", "llama3.1"),
    "custom": ("Custom (OpenAI-compatible)", "", "openai", "", ""),
}
ALIASES = {"google": "gemini", "grok": "xai", "claude": "anthropic", "local": "ollama"}
NO_KEY = {"ollama", "custom"}       # a model running on your own machine needs no key
PAID = set(PROVIDERS) - NO_KEY
# How each request is made: a low temperature (ranking should be steady), a cap on the answer's length (the
# biggest cost), and how long to wait.
DEFAULTS = {"temperature": 0.3, "max_tokens": 4096, "timeout": 120}
LIMITS = {"temperature": (0.0, 1.5), "max_tokens": (256, 16000), "timeout": (15, 600)}

# ---------------------------------------------------------------- the settings in use
_lock = threading.Lock()
_saved = {"at": None, "data": {}}


def _file():
    return config.DATA_DIR / "ai.json"


# ---------------------------------------------------------------- keys in the macOS Keychain
_KC_CACHE: dict = {}


def keychain_on() -> bool:
    return (sys.platform == "darwin" and config.env("PEAKCUT_KEYCHAIN", "1") != "0"
            and os.path.exists("/usr/bin/security"))


def _kc_service(slot) -> str:
    import hashlib
    where = hashlib.sha1(str(config.DATA_DIR.resolve()).encode()).hexdigest()[:8]
    return f"Echo Clip AI key ({slot}, {where})"


def _kc_write(slot: str, secret: str) -> bool:
    """Put a key in the Keychain. It is handed to the `security` tool on its input, never on a command line that
    other programs could see. True only if it reads back the same."""
    if not re.fullmatch(r"[\x21-\x7e]{4,400}", secret) or '"' in secret or "\\" in secret:
        return False
    cmd = f'add-generic-password -U -a "echo-clip" -s "{_kc_service(slot)}" -w "{secret}"\n'
    try:
        subprocess.run(["/usr/bin/security", "-i"], input=cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    _KC_CACHE.pop(slot, None)
    return _kc_read(slot) == secret


def _kc_read(slot: str) -> str:
    got = _KC_CACHE.get(slot)
    if got and time.time() - got[0] < 60:
        return got[1]
    try:
        p = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", "echo-clip", "-s", _kc_service(slot), "-w"],
                           capture_output=True, text=True, timeout=20)
        val = p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        val = ""
    _KC_CACHE[slot] = (time.time(), val)
    return val


def _kc_delete(slot: str):
    try:
        subprocess.run(["/usr/bin/security", "delete-generic-password", "-a", "echo-clip", "-s", _kc_service(slot)],
                       capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass
    _KC_CACHE.pop(slot, None)


def _key_of(d: dict, slot: str) -> str:
    if d.get("key_in") == "keychain":
        return _kc_read(slot)
    return str(d.get("key") or "")


def _from_dashboard() -> dict:
    """What was saved from the dashboard, read again only when the file changes."""
    f = _file()
    try:
        at = f.stat().st_mtime_ns
    except OSError:
        at = 0
    if _saved["at"] != at:
        try:
            data = json.loads(f.read_text(encoding="utf-8")) if at else {}
        except (OSError, ValueError):
            data = {}
        _saved.update(at=at, data=data if isinstance(data, dict) else {})
    return _saved["data"]


def _write(data: dict):
    with _lock:
        f = _file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)        # the key is only for whoever runs the server
        except OSError:
            pass
        tmp.replace(f)
        _saved["at"] = None


def migrate_key():
    """A key saved in ai.json by an earlier version is moved into the Keychain, once: written there, read back and
    compared, and only then removed from the file. If anything does not match, the file is left as it was."""
    d = dict(_from_dashboard())
    if not keychain_on() or not d.get("key") or d.get("key_in") == "keychain":
        return False
    if not _kc_write("main", str(d["key"])):
        return False
    d.pop("key")
    d["key_in"] = "keychain"
    _write(d)
    return True


def current() -> dict:
    """Provider, models, address and key: what the dashboard saved, or else what .env says."""
    d = _from_dashboard()
    p = str(d.get("provider") or config.AI_PROVIDER or "").strip().lower()
    return {"provider": ALIASES.get(p, p),
            "models": list(d["models"]) if d.get("provider") else list(config.AI_MODELS),
            "base_url": (str(d.get("base_url") or "") if d.get("provider") else config.AI_BASE_URL).rstrip("/"),
            "key": _key_of(d, "main") if d.get("provider") and ("key" in d or d.get("key_in")) else config.ai_key()}


def options() -> dict:
    """Temperature, answer length, waiting time, local-only, fallback, prices and budget."""
    d = _from_dashboard()
    out = {k: d.get(k, v) for k, v in DEFAULTS.items()}
    out.update(mode=d.get("mode") if d.get("mode") in ("ai", "local") else "ai",
               allow_fallback=bool(d.get("allow_fallback")),
               price_in=float(d.get("price_in") or 0), price_out=float(d.get("price_out") or 0),
               budget=float(d.get("budget") or 0))
    return out


def fallback() -> dict:
    """The second service, used only when the first fails and the owner allowed it."""
    d = _from_dashboard().get("fallback") or {}
    p = str(d.get("provider") or "")
    if p not in PROVIDERS:
        return {}
    return {"provider": p, "models": list(d.get("models") or []), "base_url": str(d.get("base_url") or ""),
            "key": _key_of(d, "fallback")}


def provider() -> str:
    return current()["provider"]


def models() -> list:
    return current()["models"]


def key() -> str:
    return current()["key"]


def label(p=None) -> str:
    return PROVIDERS.get(p or provider(), ("",))[0]


def base_url(conn=None) -> str:
    c = conn or current()
    return c["base_url"] or PROVIDERS.get(c["provider"], ("", ""))[1]


def _conn_ready(c) -> bool:
    return bool(c and c.get("provider") in PROVIDERS and c.get("models") and base_url(c)
                and (c.get("key") or c["provider"] in NO_KEY))


def ready() -> bool:
    return _conn_ready(current())


# ---------------------------------------------------------------- what has been spent this month
_spent = {"at": 0.0, "usd": 0.0}


def spent_this_month() -> float:
    """Dollars spent on the AI since the 1st, estimated from the tokens each call used and the entered prices."""
    o = options()
    if not (o["price_in"] or o["price_out"]):
        return 0.0
    if time.time() - _spent["at"] < 30:
        return _spent["usd"]
    try:
        import sqlalchemy as sa
        from . import db
        first = time.mktime(time.localtime()[:2] + (1, 0, 0, 0, 0, 0, -1))
        with db.engine.connect() as c:
            row = c.execute(sa.select(sa.func.coalesce(sa.func.sum(db.usage.c.tokens_in), 0),
                                      sa.func.coalesce(sa.func.sum(db.usage.c.tokens_out), 0)).where(db.usage.c.t >= first)).first()
        usd = (int(row[0]) * o["price_in"] + int(row[1]) * o["price_out"]) / 1e6
    except Exception:  # noqa: BLE001
        usd = 0.0
    _spent.update(at=time.time(), usd=usd)
    return usd


def why_not() -> str:
    """Why the AI will not be asked right now, in plain words, or "" if it will."""
    o = options()
    if o["mode"] == "local":
        return "The AI is switched off (local only), so clips are ranked on this machine."
    if not ready():
        return "No AI service is set up, so clips are ranked on this machine."
    if o["budget"] > 0 and spent_this_month() >= o["budget"]:
        return (f"This month's AI budget (${o['budget']:.2f}) has been reached, so clips are ranked on this machine. "
                "Raise it under Settings, AI.")
    return ""


def allowed() -> bool:
    return not why_not()


def public() -> dict:
    """What the settings page may see. The key itself never leaves the server: only whether there is one, and
    its last four characters so the owner can tell which key it is."""
    c, fb, o = current(), fallback(), options()
    d = _from_dashboard()
    return {"provider": c["provider"], "models": c["models"], "base_url": c["base_url"], "has_key": bool(c["key"]),
            "key_hint": c["key"][-4:] if len(c["key"]) >= 12 else "", "ready": ready(), "label": label(),
            "saved_here": bool(d.get("provider")), "key_store": "keychain" if d.get("key_in") == "keychain" else "file",
            "keychain": keychain_on(), "options": o, "spent_month": round(spent_this_month(), 4),
            "allowed": allowed(), "why_not": why_not(), "last_test": d.get("last_test") or {},
            "fallback": {"provider": fb.get("provider", ""), "models": fb.get("models", []), "base_url": fb.get("base_url", ""),
                         "has_key": bool(fb.get("key")), "ready": _conn_ready(fb)} if fb else {},
            "providers": [{"id": k, "label": v[0], "address": v[1], "needs_key": k not in NO_KEY, "key_url": v[3], "example": v[4],
                           "paid": k in PAID} for k, v in PROVIDERS.items()]}


def _clean_models(model_ids) -> list:
    if isinstance(model_ids, str):
        model_ids = model_ids.split(",")
    ms = [str(m).strip()[:120] for m in (model_ids or []) if str(m).strip()][:5]
    if not ms:
        raise ValueError("Enter the model to use.")
    if any(not re.fullmatch(r"[\w.\-:/@]+", m) for m in ms):
        raise ValueError("A model id has letters, digits and . - : / only.")
    return ms


def _clean_address(address) -> str:
    address = str(address or "").strip().rstrip("/")[:300]
    if address and not re.fullmatch(r"https?://[^\s]+", address):
        raise ValueError("The address has to start with http:// or https://")
    return address


def _store_key(d: dict, slot: str, k: str):
    """Keep a key: in the Keychain when there is one (and it reads back right), otherwise in the file."""
    d.pop("key", None)
    d.pop("key_in", None)
    if not k:
        if keychain_on():
            _kc_delete(slot)
        return
    if keychain_on() and _kc_write(slot, k):
        d["key_in"] = "keychain"
    else:
        d["key"] = k


def save(provider_id, model_ids, address: str = "", new_key=None, **more) -> dict:
    """Save the choice made in the dashboard. `new_key` None keeps the key that is already there (the page never
    has it to send back); an empty string removes it. `more` may hold temperature, max_tokens, timeout, mode,
    allow_fallback, price_in, price_out, budget and fallback (a dict like the main service's)."""
    p = ALIASES.get(str(provider_id or "").strip().lower(), str(provider_id or "").strip().lower())
    if p not in PROVIDERS:
        raise ValueError("Choose one of the AI services in the list.")
    ms = _clean_models(model_ids)
    address = _clean_address(address)
    if p == "custom" and not address:
        raise ValueError("A custom service needs its address, for example http://127.0.0.1:1234/v1")
    old = current()
    k = old["key"] if new_key is None and old["provider"] == p else str(new_key or "").strip()[:400]
    removing = new_key is not None and not str(new_key).strip()      # the owner pressed Remove key on purpose
    if not k and p not in NO_KEY and not removing:
        raise ValueError("Paste the key for this service.")
    d = {x: v for x, v in _from_dashboard().items() if x not in ("provider", "models", "base_url", "key", "key_in")}
    d.update(provider=p, models=ms, base_url=address)
    if k != old["key"] or old["provider"] != p or not _from_dashboard().get("provider"):
        d.pop("last_test", None)                    # a new key or service has not been tested yet
    _store_key(d, "main", k)
    if not d.get("key_in") and not d.get("key") and k:
        d["key"] = k
    for name, (lo, hi) in LIMITS.items():
        if more.get(name) not in (None, ""):
            v = float(more[name])
            d[name] = (int if name != "temperature" else float)(max(lo, min(hi, v)))
    if more.get("mode") in ("ai", "local"):
        d["mode"] = more["mode"]
    if more.get("allow_fallback") is not None:
        d["allow_fallback"] = bool(more["allow_fallback"])
    for name in ("price_in", "price_out", "budget"):
        if more.get(name) not in (None, ""):
            v = float(more[name])
            if not 0 <= v <= 10000:
                raise ValueError("Prices and the budget are dollar amounts between 0 and 10000.")
            d[name] = round(v, 4)
    fb = more.get("fallback")
    if isinstance(fb, dict):
        fp = ALIASES.get(str(fb.get("provider") or "").lower(), str(fb.get("provider") or "").lower())
        if not fp:
            if keychain_on():
                _kc_delete("fallback")
            d.pop("fallback", None)
        else:
            if fp not in PROVIDERS:
                raise ValueError("Choose one of the AI services in the list for the fallback.")
            oldfb = fallback()
            fk = oldfb.get("key", "") if fb.get("key") is None and oldfb.get("provider") == fp else str(fb.get("key") or "").strip()[:400]
            if not fk and fp not in NO_KEY:
                raise ValueError("Paste the key for the fallback service.")
            nd = {"provider": fp, "models": _clean_models(fb.get("models")), "base_url": _clean_address(fb.get("base_url"))}
            _store_key(nd, "fallback", fk)
            if not nd.get("key_in") and not nd.get("key") and fk:
                nd["key"] = fk
            d["fallback"] = nd
    _write(d)
    return public()


def record_test(result: dict):
    d = dict(_from_dashboard())
    if d.get("provider"):
        d["last_test"] = {**result, "at": time.time()}
        _write(d)


def list_models(provider_id=None, address=None, with_key=None, detail=False) -> list:
    """The model ids a service offers, so the settings page can show a list to pick from. With `detail`,
    [{"id", "context", "price_in", "price_out"}] where the service says (Featherless and OpenRouter do)."""
    c = current()
    p = ALIASES.get(str(provider_id or c["provider"]).lower(), str(provider_id or c["provider"]).lower())
    if p not in PROVIDERS:
        raise ValueError("Choose one of the AI services in the list.")
    k = str(with_key or "").strip() or (c["key"] if c["provider"] == p else "")
    base = str(address or "").strip().rstrip("/") or (c["base_url"] if c["provider"] == p else "") or PROVIDERS[p][1]
    if not base:
        raise ValueError("Enter the service's address first.")
    how = PROVIDERS[p][2]
    if how == "gemini":
        r = httpx.get(base, params={"pageSize": 200}, headers={"x-goog-api-key": k}, timeout=20)
    elif how == "anthropic":
        r = httpx.get(f"{base}/models", params={"limit": 200}, headers={"x-api-key": k, "anthropic-version": "2023-06-01"}, timeout=20)
    else:
        r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {k}"} if k else {}, timeout=20)
    if r.status_code != 200:
        raise AIError(f"HTTP {r.status_code} {r.text[:200].replace(k or chr(0), '<key>')}")
    data = r.json()
    if how == "gemini":
        found = [{"id": m.get("name", "").split("/")[-1]} for m in data.get("models", [])
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    else:
        found = []
        for m in data.get("data", []) if isinstance(data, dict) else []:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            pr = m.get("pricing") or {}
            row = {"id": m["id"], "context": m.get("context_length")}
            try:                                    # dollars per million tokens, when the service says
                if "input" in pr:
                    row.update(price_in=float(pr["input"]), price_out=float(pr["output"]))
                elif "prompt" in pr:
                    row.update(price_in=float(pr["prompt"]) * 1e6, price_out=float(pr["completion"]) * 1e6)
            except (TypeError, ValueError):
                pass
            found.append(row)
    rows = sorted({f["id"]: f for f in found if f["id"]}.values(), key=lambda f: f["id"])[:400]
    return rows if detail else [f["id"] for f in rows]


def find_model(provider_id, model_id, with_key=None) -> dict:
    """Is this model in the service's catalogue? Asked of the service's own model page (free: no tokens).
    {"found", "checked", "id", and context and prices in dollars per million tokens when the service says}"""
    p = ALIASES.get(str(provider_id or "").lower(), str(provider_id or "").lower())
    if p not in PROVIDERS or PROVIDERS[p][2] != "openai":
        try:
            return {"found": model_id in list_models(p, None, with_key), "checked": True, "id": model_id}
        except Exception:  # noqa: BLE001 - the catalogue could not be read: the test request decides
            return {"found": False, "checked": False, "id": model_id}
    c = current()
    k = str(with_key or "").strip() or (c["key"] if c["provider"] == p else "")
    base = (c["base_url"] if c["provider"] == p else "") or PROVIDERS[p][1]
    try:
        r = httpx.get(f"{base}/models/{model_id}", timeout=20, headers={"Authorization": f"Bearer {k}"} if k else {})
    except Exception:  # noqa: BLE001
        return {"found": False, "checked": False, "id": model_id}
    if r.status_code == 404:
        return {"found": False, "checked": True, "id": model_id}
    if r.status_code != 200:
        return {"found": False, "checked": False, "id": model_id}
    try:
        m = r.json()
        pr = m.get("pricing") or {}
        out = {"found": True, "checked": True, "id": model_id, "context": m.get("context_length"), "status": m.get("status")}
        if "input" in pr:
            out.update(price_in=float(pr["input"]), price_out=float(pr["output"]))
        return out
    except (ValueError, TypeError, AttributeError):
        return {"found": True, "checked": True, "id": model_id}


CHECK = {"ok": "Connected", "missing_key": "No key", "invalid_key": "Key refused", "credits": "No credit left",
         "rate_limit": "Rate limited", "invalid_model": "Unknown model", "network": "Can't reach the service",
         "other": "Error"}


def classify(e) -> str:
    text, low = str(e), str(e).lower()
    if "402" in text or "insufficient" in low or "credit" in low or "billing" in low or "payment" in low:
        return "credits"
    if "404" in text or "model not found" in low or "does not exist" in low or "unknown model" in low or "not_found" in low \
            or ("model" in low and ("invalid" in low or "not available" in low or "not supported" in low)):
        return "invalid_model"
    if "401" in text or "403" in text or "api key" in low or "api_key" in low or "unauthorized" in low or "permission" in low:
        return "invalid_key"
    if "429" in text or "quota" in low or "rate limit" in low or "exhausted" in low or "too many" in low:
        return "rate_limit"
    if "connect" in low or "timed out" in low or "timeout" in low or "getaddrinfo" in low or "refused" in low \
            or "name resolution" in low or "network" in low:
        return "network"
    return "other"


def test() -> dict:
    """Prove the key, the address and the model work, as cheaply as possible: the catalogue is looked at first
    (free), then one request that may answer with a single word. The result is saved for the settings page."""
    c = current()
    if c["provider"] not in PROVIDERS or not c["models"]:
        raise ValueError("Save a service and a model first.")
    if not c["key"] and c["provider"] not in NO_KEY:
        res = {"ok": False, "code": "missing_key", "status": CHECK["missing_key"], "model": c["models"][0],
               "message": f"Paste your {label()} key and save it first."}
        record_test(res)
        return res
    ms = c["models"]
    look = find_model(c["provider"], ms[0]) if c["provider"] == "featherless" else {"checked": False}
    if look.get("checked") and not look.get("found"):
        res = {"ok": False, "code": "invalid_model", "status": CHECK["invalid_model"], "model": ms[0],
               "message": f"{label()} has no model called \"{ms[0]}\". Press Find models and pick one from the list."}
        record_test(res)
        return res
    try:
        out, sent, wrote = call(ms[0], 'Answer with a JSON array that holds the single string "ok".',
                                {"type": "ARRAY", "items": {"type": "STRING"}}, max_tokens=16)
    except Exception as e:  # noqa: BLE001
        code = classify(e)
        res = {"ok": False, "code": code, "status": CHECK[code], "model": ms[0],
               "message": explain(e) or ("The AI did not answer: " + str(e).replace(key() or "\0", "<key>")[:200])}
        record_test(res)
        return res
    res = {"ok": True, "code": "ok", "status": CHECK["ok"], "model": ms[0], "tokens": sent + wrote, "answer": str(out)[:80],
           "message": f"{label()} answered with {ms[0]}.",
           **({"context": look.get("context"), "price_in": look.get("price_in"), "price_out": look.get("price_out")} if look.get("found") else {})}
    record_test(res)
    return res


class AIError(RuntimeError):
    pass


problem = {"text": "", "at": 0.0}       # why the AI last failed, for the dashboard; cleared when it answers again


def explain(e) -> str:
    """What went wrong with an AI call, in words the owner can act on."""
    name, code = (models() or ["?"])[0], classify(e)
    return {
        "credits": f"{label()} says the account has no credit left (or billing needs attention). Clips are ranked "
                   f"on this machine until it is topped up.",
        "invalid_model": f"{label()} doesn't know the model \"{name}\". Open Settings, AI, press Find models and pick one from the list.",
        "invalid_key": f"{label()} refused the key. Paste a working key under Settings, AI.",
        "rate_limit": f"{label()} says the key has hit its limit: too many requests right now, or the plan's quota is used "
                      f"up. Try again in a minute; clips are ranked on this machine meanwhile.",
        "network": f"Echo Clip couldn't reach {label()}. Check the internet connection"
                   + (" and that Ollama is running." if provider() == "ollama" else "."),
    }.get(code, "")


def note_problem(e):
    why = explain(e)
    if why:
        problem.update(text=why, at=time.time())


def clear_problem():
    problem.update(text="", at=0.0)


def _fail(r, model, conn=None):
    text = r.text[:300].replace((conn or current())["key"] or "\0", "<key>")
    raise AIError(f"HTTP {r.status_code} {text}")


# ---------------------------------------------------------------- the schema, said in words for chat models
def _json_schema(s):
    """Gemini's schema spelling (ARRAY, OBJECT...) as ordinary JSON Schema."""
    if isinstance(s, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else _json_schema(v)) for k, v in s.items()}
    if isinstance(s, list):
        return [_json_schema(x) for x in s]
    return s


def _wrapped(prompt, schema):
    return (prompt + "\n\nAnswer with JSON only, no other text and no code fences: one object with a single key "
            "\"items\", whose value follows this JSON Schema:\n" + json.dumps(_json_schema(schema)))


def _parse(text):
    """The JSON in an answer, even if the model wrapped it in a code fence or a sentence."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        out = json.loads(text)
    except ValueError:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if not m:
            raise AIError("The AI did not answer with JSON.")
        try:
            out = json.loads(m.group(0))
        except ValueError:
            raise AIError("The AI's answer was not valid JSON (it may have been cut off: raise the answer length).")
    if isinstance(out, dict) and "items" in out:
        out = out["items"]
    return out


# ---------------------------------------------------------------- one call
def call(model: str, prompt: str, schema: dict, conn=None, max_tokens=None):
    """Ask one model. Returns (the parsed JSON answer, tokens sent, tokens written back). `conn` is the service
    to use (the main one when not given)."""
    conn = conn or current()
    how = PROVIDERS[conn["provider"]][2]
    o = options()
    return {"gemini": _gemini, "openai": _openai, "anthropic": _anthropic}[how](
        model, prompt, schema, conn, int(max_tokens or o["max_tokens"]), float(o["temperature"]), float(o["timeout"]))


def _gemini(model, prompt, schema, conn, max_tokens, temperature, timeout):
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json", "responseSchema": schema,
                                 "maxOutputTokens": max_tokens}}
    r = httpx.post(f"{base_url(conn)}/{model}:generateContent", json=body, headers={"x-goog-api-key": conn["key"]}, timeout=timeout)
    if r.status_code != 200:
        _fail(r, model, conn)
    data = r.json()
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    u = data.get("usageMetadata", {})
    sent = int(u.get("promptTokenCount") or 0)
    return json.loads(text), sent, max(0, int(u.get("totalTokenCount") or 0) - sent)   # the answer, and any thinking billed with it


def _openai(model, prompt, schema, conn, max_tokens, temperature, timeout):
    headers = {"Authorization": f"Bearer {conn['key']}"} if conn["key"] else {}
    body = {"model": model, "messages": [{"role": "user", "content": _wrapped(prompt, schema)}], "max_tokens": max_tokens}
    # Most services accept these two extras; a few models refuse one of them, so they are dropped on a refusal.
    extras = [{"temperature": temperature, "response_format": {"type": "json_object"}}, {"response_format": {"type": "json_object"}}, {}]
    r = None
    for extra in extras:
        r = httpx.post(f"{base_url(conn)}/chat/completions", json={**body, **extra}, headers=headers, timeout=timeout)
        if r.status_code not in (400, 422) or "model" in r.text.lower() and "not" in r.text.lower():
            break
    if r.status_code != 200:
        _fail(r, model, conn)
    data = r.json()
    u = data.get("usage") or {}
    choice = data["choices"][0]
    if choice.get("finish_reason") == "length" and not (choice["message"].get("content") or "").rstrip().endswith(("}", "]")):
        raise AIError("The AI's answer was cut off at the answer length limit. Raise it under Settings, AI.")
    return _parse(choice["message"].get("content")), int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)


def _anthropic(model, prompt, schema, conn, max_tokens, temperature, timeout):
    body = {"model": model, "max_tokens": max_tokens, "temperature": min(1.0, temperature),
            "messages": [{"role": "user", "content": _wrapped(prompt, schema)}]}
    r = httpx.post(f"{base_url(conn)}/messages", json=body, timeout=timeout,
                   headers={"x-api-key": conn["key"], "anthropic-version": "2023-06-01"})
    if r.status_code != 200:
        _fail(r, model, conn)
    data = r.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    u = data.get("usage") or {}
    return _parse(text), int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
