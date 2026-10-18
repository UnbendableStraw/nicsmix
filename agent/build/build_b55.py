#!/usr/bin/env python3
"""syng-agent-b55.py = b54 + Direct on all three drivers + a measured sync check.

1. Direct mode, rebuilt. It used cellbroker's fixed-focus override, and *any* override — 0.0, 0.5, 0.8, 1.0 —
   switches every cell into a single-driver mode: metered on the primary, `system_out_2/3` (two of woofer /
   tweeter / mid beam) went to exactly -inf the moment the override was set and came back the moment it was
   cleared. So Direct no longer touches the focus at all. Direct = the normal render (all three drivers, the
   app's own objects, distance attenuation already off in stock), with the field pinned to the classic puck at
   the centre (no steering), room correction off, listener-offset compensation off, bass distribution on.
   Measured: all three drivers live on the primary, and each cell's own mic hears its speaker within 1.2 dB of
   the others (-52.0 .. -53.2 dBFS). Your field is kept and comes back when you switch to Syng.

2. "The primary plays half a second behind the group." Measured with the cells' microphones (two cells
   capture the room at the same NTP second, the log-RMS envelopes are cross-correlated): the primary was
   640 ms late, r = 0.82 — one full 32768-frame DAC buffer. Restarting jackd brought it to 0 ms (r = 0.87);
   restarting syngsoundserver did not. Listener-offset compensation and room EQ, each switched live, did *not*
   reproduce it (0, -30, -40 ms), so the trigger is unknown. Rather than guess, the agent now measures:
     GET  /api/synccheck            the same two-mic measurement, primary vs one secondary
     POST /api/capture/envelope     {at, seconds}: capture this cell's mic at an epoch, return the envelope only
     POST /api/resync               restart jackd on this cell, wait for the source, re-apply the mode
   and runs the check by itself 25 s after a sound-mode switch and every 30 min while music plays; a measured
   offset over 150 ms with a clear correlation restarts jackd (at most once per 10 min) and re-measures.
   Only an 8 s envelope (100 numbers a second) ever leaves a cell; the recording is deleted straight away.
   `settings.sync_check: false` turns the automatic checks off."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b54.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---------- 1. Direct without the focus override ----------
rep('''def apply_sound_mode(mode=None, local_only=False):
    """'syng'  = the stock spatialiser: objects steered across cells and across each cell's three drivers,
                 room correction, bass distribution.
       'direct' = focus 0.0 on every object (cellbroker's fixed-focus override: 'all cells playing towards
                 outside' — every cell plays every object, omni), room correction off, listener-offset compensation
                 off. Bass distribution stays on: off, the primary's own output is zero.
       The focus override lives in the primary's cellbroker (in memory, re-applied on every handle push); the
       EQ / bass flags are per cell, so members apply them locally."""
    mode = mode or SETTINGS.get("sound_mode") or "syng"
    direct = (mode == "direct")
    out = {"mode": mode}
    if CELL is not None and (whoami().get("role") or "").lower() != "secondary":
        try:
            CELL.call("cell_set_objects_to_fixed_focus", {"focus": 0.0 if direct else -1.0}); out["focus"] = 0.0 if direct else None
        except Exception as e:
            out["focus_error"] = str(e)[:120]   # no active source yet: the keeper retries''',
'''MODE_APPLIED = [None]

def apply_sound_mode(mode=None, local_only=False):
    """'syng'  = the stock spatialiser: the field steers the objects across cells and drivers, room correction,
                 listener-offset compensation, bass distribution.
       'direct' = the same renderer with nothing steering it: the field pinned to the classic puck at the centre,
                 room correction off, listener-offset compensation off, bass distribution on. Every cell plays the
                 whole mix through all three drivers at the same level.
       Neither mode uses cellbroker's fixed-focus override any more (b55): with *any* override value every cell
       drops to a single driver. The EQ / compensation / bass flags are per cell, so members apply them locally."""
    mode = mode or SETTINGS.get("sound_mode") or "syng"
    direct = (mode == "direct")
    out = {"mode": mode}
    primary = (whoami().get("role") or "").lower() != "secondary"
    changed = MODE_APPLIED[0] != mode
    MODE_APPLIED[0] = mode
    if CELL is not None and primary:
        try:
            CELL.call("cell_set_objects_to_fixed_focus", {"focus": -1.0}); out["focus"] = None   # never override
        except Exception as e:
            out["focus_error"] = str(e)[:120]   # no active source yet: the keeper retries''')

rep('''    try: out["bass_disabled"] = bass_sync("soundmode")
    except Exception as e: out["bass_sync_error"] = str(e)[:80]
    if not local_only: fanout("/api/soundmode", {"mode": mode})
    return out''',
'''    try: out["bass_disabled"] = bass_sync("soundmode")
    except Exception as e: out["bass_sync_error"] = str(e)[:80]
    if primary and changed:
        FIELD["version"] = str(uuid.uuid4()).upper()   # a new handle version, so cellbroker re-renders either way
        try: out["handles_pushed"] = push_handles()
        except Exception as e: out["push_error"] = str(e)[:80]
        if SETTINGS.get("sync_check", True):
            threading.Timer(25, lambda: sync_check(autofix=True, reason="soundmode")).start()
    if not local_only: fanout("/api/soundmode", {"mode": mode})
    return out''')

rep('''def field_handles():
    return {"0": {"type": "panner_v2", "x": round(FIELD["ux"], 4), "y": round(FIELD["uy"], 4),''',
'''def field_handles():
    if SETTINGS.get("sound_mode") == "direct":
        # Direct: nothing steers — the classic puck at the centre of the room, whatever the field says (kept for Syng)
        return {"0": {"type": "panner_v2", "x": 0.0, "y": 0.0, "rotation": 90.0, "radius": 0.0, "surround_width": 95.0,
                      "snap_to_cell": "", "snapped_rotation": 0.0, "version": FIELD["version"] + "-direct"}}
    return {"0": {"type": "panner_v2", "x": round(FIELD["ux"], 4), "y": round(FIELD["uy"], 4),''')

rep('''    snap = (FIELD.get("snap") or "").strip()''',
    '''    snap = "" if SETTINGS.get("sound_mode") == "direct" else (FIELD.get("snap") or "").strip()''')

# ---------- 2. the measured sync check ----------
rep('''def bass_watch_loop():''',
'''# ---------- sync check: is the primary playing in step with the room? measured with the cells' own mics ----------
SYNC = {"last": None, "busy": False, "fixed_at": 0.0, "next": 0.0, "history": []}

def _wav_envelope(path, hop_s=0.01):
    """log-RMS envelope of a WAV (int16 or float32), 10 ms frames, zero mean / unit variance."""
    import numpy as np
    raw = open(path, "rb").read(); i = 12; fmt = data = None
    while i + 8 <= len(raw):
        cid = raw[i:i + 4]; sz = struct.unpack_from("<I", raw, i + 4)[0]; body = raw[i + 8:i + 8 + sz]
        if cid == b"fmt ": fmt = struct.unpack_from("<HHIIHH", body, 0)
        elif cid == b"data": data = body
        i += 8 + sz + (sz & 1)
    if not fmt or data is None: raise RuntimeError("unreadable capture")
    tag, ch, sr = fmt[0], fmt[1], fmt[2]
    x = np.frombuffer(data, dtype=np.float32 if tag == 3 else np.int16).astype(np.float32)
    if ch > 1: x = x.reshape(-1, ch)[:, 0]
    hop = int(sr * hop_s); n = len(x) // hop
    if n < 50: raise RuntimeError("capture too short")
    e = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    level = float(20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12))
    e = np.log(e + 1e-9); e -= e.mean(); e /= (e.std() + 1e-9)
    return e, level

def capture_envelope(at=None, seconds=8):
    """Wait for epoch `at`, record this cell's mic for `seconds`, return only the envelope (and the level)."""
    seconds = max(3.0, min(15.0, float(seconds)))
    if at is not None:
        dt = float(at) - time.time()
        if dt > 12: raise RuntimeError("start time too far ahead")
        if dt > 0: time.sleep(dt)
    path = "/tmp/syng-synccheck.wav"
    try:
        subprocess.run(["jack_capture", "-d", "%g" % seconds, "-c", "1", "-p", "system:capture_1", path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=seconds + 12)
        e, level = _wav_envelope(path)
    finally:
        try: os.remove(path)
        except OSError: pass
    return {"env": [round(float(v), 3) for v in e], "hop_ms": 10, "level_dbfs": round(level, 1), "at": at}

def audio_resync(reason=""):
    """Restart jackd (the DAC ring is where a stuck primary keeps its extra buffer), wait for the source to come
    back, put the sound mode and the field back."""
    log("SYNC resync: restarting jackd%s" % ((" [" + reason + "]") if reason else ""))
    subprocess.run(["systemctl", "restart", "jackd"], timeout=30)
    for _ in range(30):
        time.sleep(1)
        try:
            if sysctl("is-active", "syngsoundserver") == "active" and active_music_src(): break
        except Exception: pass
    time.sleep(2)
    MODE_APPLIED[0] = None
    try: apply_sound_mode(None, local_only=True)
    except Exception as e: log("SYNC resync: soundmode %s" % e)
    try: push_handles(); bass_sync("resync")
    except Exception as e: log("SYNC resync: handles %s" % e)
    return {"ok": True}

def sync_check(autofix=False, reason="manual"):
    """The two-mic measurement: this cell (the primary) and one secondary capture the room at the same NTP
    second; the envelopes are cross-correlated; a positive lag means the primary is late."""
    if SYNC["busy"]: return {"busy": True, "last": SYNC["last"]}
    st = get_state()
    if not is_primary_cell(st): return {"error": "run the sync check on the primary"}
    me_ip = whoami().get("ip")
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
        log("SYNC %s: lag %+.0f ms r %.2f (%s) vs %s" % (reason, res["lag_ms"], res["r"], res["verdict"], peers[0]))
        SYNC["last"] = res
        SYNC["history"] = (SYNC["history"] + [res])[-20:]
    except Exception as e:
        SYNC["busy"] = False
        log("SYNC %s failed: %s" % (reason, e))
        return {"error": str(e)[:160]}
    SYNC["busy"] = False
    if autofix and res["verdict"] in ("primary late", "primary early") and time.time() - SYNC["fixed_at"] > 600:
        SYNC["fixed_at"] = time.time()
        audio_resync("measured %+.0f ms" % res["lag_ms"])
        time.sleep(8)
        after = sync_check(autofix=False, reason="after resync")
        res = dict(res, fixed=True, after=after)
        SYNC["last"] = res
    return res

def sync_watch_tick():
    """From the keeper: every 30 min while music plays, on the primary."""
    if not SETTINGS.get("sync_check", True) or SYNC["busy"]: return
    if time.time() < SYNC["next"]: return
    try:
        if not is_primary_cell(get_state()) or not active_music_src(): return
    except Exception: return
    SYNC["next"] = time.time() + 1800
    threading.Thread(target=lambda: sync_check(autofix=True, reason="periodic"), daemon=True).start()

def bass_watch_loop():''')

rep('''            try: bass_sync("keeper")
            except Exception as e: log("BASS keeper: %s" % e)''',
'''            try: bass_sync("keeper")
            except Exception as e: log("BASS keeper: %s" % e)
            try: sync_watch_tick()
            except Exception as e: log("SYNC keeper: %s" % e)''')

rep('''def lc_get(path):
    if path.startswith("/api/link"): return link_status()''',
'''def lc_get(path):
    if path.startswith("/api/synccheck/last"): return {"last": SYNC["last"], "history": SYNC["history"], "busy": SYNC["busy"]}
    if path.startswith("/api/synccheck"): return sync_check(autofix=False, reason="manual")
    if path.startswith("/api/link"): return link_status()''')

rep('''    if path.startswith("/api/room/apply"): return {"ok": apply_view(body)}''',
'''    if path.startswith("/api/capture/envelope"): return capture_envelope(body.get("at"), body.get("seconds", 8))
    if path.startswith("/api/resync"): return audio_resync("manual")
    if path.startswith("/api/synccheck"): return sync_check(autofix=bool(body.get("fix", False)), reason="manual")
    if path.startswith("/api/room/apply"): return {"ok": apply_view(body)}''')

s = s.replace('"syng-agent (b54)"', '"syng-agent (b55)"').replace("syng-agent (b54) on", "syng-agent (b55) on").replace('AGENT_BUILD = "b54"', 'AGENT_BUILD = "b55"')
out = os.path.join(here, "syng-agent-b55.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
