"""How busy the server really is, measured every second: processor, memory and disk.

Nothing here counts people. New heavy work (another video, another recording) starts only while the machine has
room, waits in line for a moment when it is nearly full, and is let through the second it frees up again. That is
what keeps a rush from slowing everyone down or crashing the server."""
import os
import shutil
import sys
import threading
import time

from . import config

BUSY_CPU = float(config.env("PEAKCUT_BUSY_CPU", "82"))          # percent: above this, no extra videos are started
MIN_FREE_RAM_GB = float(config.env("PEAKCUT_MIN_FREE_RAM_GB", "1.5"))
MIN_FREE_DISK_GB = float(config.env("PEAKCUT_MIN_FREE_DISK_GB", "3"))

_state = {"cpu": 0.0, "ram_free": 99.0, "disk_free": 99.0, "at": 0.0}
_force = None            # tests only: pretend the server is busy (True) or free (False)
_started = False
_lock = threading.Lock()


_mach = {}


def _mac_host():
    """The Mac's own counters (the same ones Activity Monitor reads): there is no /proc on macOS."""
    if not _mach:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        libc.mach_host_self.restype = ctypes.c_uint
        _mach.update(libc=libc, host=libc.mach_host_self(), ctypes=ctypes)
    return _mach["libc"], _mach["host"], _mach["ctypes"]


def _cpu_times():
    """(busy, total) processor time so far, in any unit."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        n = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime          # noqa: E731
        total = n(kernel) + n(user)                                        # kernel time includes idle time
        return total - n(idle), total
    if sys.platform == "darwin":
        libc, host, ctypes = _mac_host()
        ticks, count = (ctypes.c_uint * 4)(), ctypes.c_uint(4)             # user, system, idle, nice
        if libc.host_statistics(host, 3, ticks, ctypes.byref(count)) != 0:
            raise OSError("host_statistics failed")
        return ticks[0] + ticks[1] + ticks[3], sum(ticks)
    with open("/proc/stat", encoding="ascii") as f:
        v = [int(x) for x in f.readline().split()[1:9]]
    return sum(v) - v[3] - v[4], sum(v)                                    # everything except idle and waiting on disk


def _ram_free_gb() -> float:
    if os.name == "nt":
        import ctypes

        class M(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                (n, ctypes.c_ulonglong) for n in ("total", "avail", "tpf", "apf", "tv", "av", "aev")]
        m = M()
        m.dwLength = ctypes.sizeof(M)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.avail / 1e9
    if sys.platform == "darwin":
        libc, host, ctypes = _mac_host()
        vm, count = (ctypes.c_uint * 64)(), ctypes.c_uint(64)
        if libc.host_statistics64(host, 4, vm, ctypes.byref(count)) != 0:
            return 99.0
        # free + inactive pages: what macOS can hand to a program without pushing anything out
        return (vm[0] + vm[2]) * os.sysconf("SC_PAGE_SIZE") / 1e9
    with open("/proc/meminfo", encoding="ascii") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1e6
    return 99.0


def _watch():
    try:
        last = _cpu_times()
    except Exception:  # noqa: BLE001 - a processor we cannot measure: behave as if it always has room
        last = None
    while True:
        time.sleep(1)
        try:
            if last is not None:
                now = _cpu_times()
                used, total = now[0] - last[0], now[1] - last[1]
                last = now
                if total > 0:
                    # smoothed over a few seconds, so one short spike neither holds people up nor lets too much in
                    _state["cpu"] = 0.55 * _state["cpu"] + 0.45 * (100.0 * used / total)
                _state["ram_free"] = _ram_free_gb()
        except Exception:  # noqa: BLE001
            pass
        try:                          # the drive is looked at whatever else can or cannot be measured
            if time.time() - _state["at"] > 15:
                _state["disk_free"] = shutil.disk_usage(config.DATA_DIR).free / 1e9
                _state["at"] = time.time()
        except Exception:  # noqa: BLE001
            pass


def start():
    global _started
    with _lock:
        if not _started:
            _started = True
            threading.Thread(target=_watch, daemon=True, name="load").start()


def busy() -> bool:
    """True while the machine is close to full: nothing extra should start right now."""
    if _force is not None:
        return _force
    start()
    return _state["cpu"] >= BUSY_CPU or _state["ram_free"] < MIN_FREE_RAM_GB


def disk_full() -> bool:
    start()
    return _state["disk_free"] < MIN_FREE_DISK_GB


def state() -> dict:
    start()
    return {"cpu": round(_state["cpu"]), "ram_free_gb": round(_state["ram_free"], 1), "disk_free_gb": round(_state["disk_free"], 1),
            "busy": busy(), "busy_cpu": BUSY_CPU}
