#!/usr/bin/env python3
"""syng-agent-b56.py = b55 + the actual cause of "a speaker plays half a second behind".

The keeper (every 90 s, on every cell) ran usbnet_up(), which did `ip addr replace 10.86.x.1/24 dev usbN`
for both USB setup ports. `replace` re-installs the address even when it is already there, so the kernel
emits RTM_NEWADDR; mDNSResponder treats that as an interface change, withdraws every record and announces
them again; every cell sees every other cell DISAPPEAR and APPEAR (~150 times an hour on the primary);
celldirector rebuilds the group's SourceCast links each time, and now and then a cell comes back exactly
one 32768-frame DAC buffer late (640 ms, measured) and stays there, with jackd logging a continuous
"Playback underrun" storm. Seen on the primary from 22:44 and on .138 from 01:30, each time on the minute
of an RTM_NEWADDR on interfaces 5/6 (usb0/usb1) followed by the mDNS flap.

1. usbnet_up() is idempotent: the address is added only when it is missing, the link set up only when it is
   down, udhcpd started only when it is not running. A healthy cell now produces no netlink events at all.
2. The microphone sync check (b55) no longer runs by itself. It compared the primary with one secondary
   only, so two cells late by the same buffer looked "in step" — which is what happened — and every check
   attached capture clients to the running audio engine on both cells. It stays available by hand
   (GET /api/synccheck) and now compares the primary with every member.
3. Instead each cell watches its own jackd for the storm — read from the journal, nothing touches the audio
   graph. Sustained for a minute (> 10 underruns/s on two checks 30 s apart): restart jackd on that cell,
   at most once every 10 minutes, and log it. Healthy cells log no underruns at all."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b55.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---------- 1. no more netlink churn ----------
rep('''    for ifn, ip in USB_NETS.items():
        if not os.path.exists("/sys/class/net/%s" % ifn): continue
        _sh("ip link set %s up" % ifn); _sh("ip addr replace %s/24 dev %s" % (ip, ifn))''',
'''    for ifn, ip in USB_NETS.items():
        if not os.path.exists("/sys/class/net/%s" % ifn): continue
        # Only touch what is missing: `ip addr replace` on an address that is already there still emits
        # RTM_NEWADDR, and mDNSResponder answers every one by withdrawing and re-announcing the whole cell —
        # the whole room flapped every 90 s, and a flap now and then left a cell one DAC buffer late (b56).
        flags = _read("/sys/class/net/%s/flags" % ifn).strip()
        try: up = bool(int(flags, 16) & 0x1)
        except ValueError: up = False
        if not up: _sh("ip link set %s up" % ifn)
        if ("inet %s/" % ip) not in _sh("ip -4 addr show dev %s" % ifn, 5):
            _sh("ip addr add %s/24 dev %s" % (ip, ifn))''')

# ---------- 2. the mic check: manual only, and against every member ----------
rep('''        if SETTINGS.get("sync_check", True):
            threading.Timer(25, lambda: sync_check(autofix=True, reason="soundmode")).start()''',
'''        if SETTINGS.get("sync_check_mic", False):   # off by default since b56 (see the docstring)
            threading.Timer(25, lambda: sync_check(autofix=False, reason="soundmode")).start()''')

rep('''def sync_watch_tick():
    """From the keeper: every 30 min while music plays, on the primary."""
    if not SETTINGS.get("sync_check", True) or SYNC["busy"]: return''',
'''def sync_watch_tick():
    """Retired in b56: the mic check runs only by hand now."""
    return
    if not SETTINGS.get("sync_check", True) or SYNC["busy"]: return''')

rep('''    me_ip = whoami().get("ip")
    peers = [ip for ip in room_member_ips(st) if ip and ip != me_ip]
    if not peers: return {"error": "no other speaker in this room"}
    if not active_music_src(): return {"error": "nothing is playing — the check listens to the music"}
    SYNC["busy"] = True
    try:
        import numpy as np
        at = time.time() + 3.0
        got = {}
        def remote():
            try: got["peer"] = agent_post(peers[0], "/api/capture/envelope", {"at": at, "seconds": 8}, timeout=30)
            except Exception as e: got["err"] = str(e)[:120]
        t = threading.Thread(target=remote, daemon=True); t.start()
        mine = capture_envelope(at, 8)
        t.join(35)
        if "peer" not in got: raise RuntimeError("no capture from %s: %s" % (peers[0], got.get("err", "timeout")))
        ea = np.array(mine["env"]); eb = np.array(got["peer"]["env"])
        m = min(len(ea), len(eb)); ea, eb = ea[:m], eb[:m]
        c = np.correlate(ea, eb, mode="full") / m
        lags = (np.arange(len(c)) - (m - 1)) * 10.0
        k = int(np.argmax(np.where(np.abs(lags) <= 1500, c, -9.0)))
        res = {"lag_ms": float(lags[k]), "r": round(float(c[k]), 3), "r_at_0": round(float(c[m - 1]), 3),
               "peer": peers[0], "levels_dbfs": [mine["level_dbfs"], got["peer"].get("level_dbfs")],
               "t": time.time(), "reason": reason}
        res["verdict"] = ("inconclusive" if res["r"] < 0.5 else
                          ("in step" if abs(res["lag_ms"]) < 150 else ("primary late" if res["lag_ms"] > 0 else "primary early")))
        log("SYNC %s: lag %+.0f ms r %.2f (%s) vs %s" % (reason, res["lag_ms"], res["r"], res["verdict"], peers[0]))''',
'''    me_ip = whoami().get("ip")
    peers = [ip for ip in room_member_ips(st) if ip and ip != me_ip]
    if not peers: return {"error": "no other speaker in this room"}
    if not active_music_src(): return {"error": "nothing is playing — the check listens to the music"}
    SYNC["busy"] = True
    try:
        import numpy as np
        at = time.time() + 3.0
        got = {}
        def remote(ip):
            try: got[ip] = agent_post(ip, "/api/capture/envelope", {"at": at, "seconds": 8}, timeout=30)
            except Exception as e: got[ip] = {"error": str(e)[:120]}
        ts = [threading.Thread(target=remote, args=(ip,), daemon=True) for ip in peers]
        for t in ts: t.start()
        mine = capture_envelope(at, 8)
        for t in ts: t.join(35)
        envs = {me_ip: np.array(mine["env"])}
        for ip in peers:
            if got.get(ip, {}).get("env"): envs[ip] = np.array(got[ip]["env"])
        if len(envs) < 2: raise RuntimeError("no capture from any other speaker")
        def lag(a, b):
            m = min(len(a), len(b)); a, b = a[:m], b[:m]
            c = np.correlate(a, b, mode="full") / m
            lags = (np.arange(len(c)) - (m - 1)) * 10.0
            k = int(np.argmax(np.where(np.abs(lags) <= 1500, c, -9.0)))
            return float(lags[k]), float(c[k])
        # every cell against every other; each one's offset is its median lag against the rest, so a pair
        # that is late together no longer looks "in step"
        ips = list(envs)
        per = {}
        for a in ips:
            ls = [lag(envs[a], envs[b]) for b in ips if b != a]
            good = sorted(l for l, r in ls if r >= 0.5)
            per[a] = {"lag_ms": good[len(good) // 2] if good else None, "pairs": len(ls), "clear": len(good)}
        late = [ip for ip, v in per.items() if v["lag_ms"] is not None and v["lag_ms"] >= 150]
        res = {"cells": per, "late": late, "t": time.time(), "reason": reason,
               "lag_ms": per[me_ip]["lag_ms"], "r": 1.0 if per[me_ip]["clear"] else 0.0,
               "verdict": ("late: " + ", ".join(late)) if late else "in step"}
        log("SYNC %s: %s %s" % (reason, res["verdict"], {ip: v["lag_ms"] for ip, v in per.items()}))''')

rep('''    if autofix and res["verdict"] in ("primary late", "primary early") and time.time() - SYNC["fixed_at"] > 600:''',
    '''    if autofix and False:''')

# ---------- 3. each cell watches its own jackd for the underrun storm ----------
rep('''def bass_watch_loop():''',
'''STORM = {"strikes": 0, "fixed_at": 0.0, "last_rate": 0.0, "history": []}

def jackd_underrun_rate(window=30):
    """Underruns per second jackd logged in the last `window` s, counting the ones journald rate-limited."""
    out = _sh("journalctl -u jackd --since -%ds --no-pager -q" % window, 10)
    n = out.count("Playback underrun")
    supp = _sh("journalctl --since -%ds --no-pager -q -t systemd-journald" % window, 10)
    for m in re.finditer(r"Suppressed (\\d+) messages from jackd", supp):
        n += int(m.group(1))
    return n / float(window)

def storm_watch_loop():
    """A healthy cell logs no playback underruns. A cell knocked one DAC buffer late logs 50-80 a second for as
    long as it stays late. Two checks in a row over 10/s: restart jackd (at most every 10 minutes)."""
    time.sleep(60)
    while True:
        try:
            rate = jackd_underrun_rate(30)
            STORM["last_rate"] = rate
            STORM["strikes"] = STORM["strikes"] + 1 if rate > 10 else 0
            if STORM["strikes"] >= 2 and time.time() - STORM["fixed_at"] > 600:
                STORM["fixed_at"] = time.time(); STORM["strikes"] = 0
                log("STORM %.0f underruns/s for a minute: this cell is out of step, restarting jackd" % rate)
                STORM["history"] = (STORM["history"] + [{"t": time.time(), "rate": round(rate, 1)}])[-20:]
                audio_resync("underrun storm %.0f/s" % rate)
        except Exception as e:
            log("STORM watch: %s" % e)
        time.sleep(30)

def bass_watch_loop():''')

rep('''    threading.Thread(target=bass_watch_loop, daemon=True).start()''',
    '''    threading.Thread(target=bass_watch_loop, daemon=True).start()
    threading.Thread(target=storm_watch_loop, daemon=True).start()''')

rep('''    if path.startswith("/api/synccheck/last"): return {"last": SYNC["last"], "history": SYNC["history"], "busy": SYNC["busy"]}''',
    '''    if path.startswith("/api/synccheck/last"): return {"last": SYNC["last"], "history": SYNC["history"], "busy": SYNC["busy"]}
    if path.startswith("/api/storm"): return {"underruns_per_s": round(STORM["last_rate"], 1), "strikes": STORM["strikes"],
                                              "restarts": STORM["history"], "fixed_at": STORM["fixed_at"]}''')

s = s.replace('"syng-agent (b55)"', '"syng-agent (b56)"').replace("syng-agent (b55) on", "syng-agent (b56) on").replace('AGENT_BUILD = "b55"', 'AGENT_BUILD = "b56"')
out = os.path.join(here, "syng-agent-b56.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
