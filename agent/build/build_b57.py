#!/usr/bin/env python3
"""syng-agent-b57.py = b56 + moving the sound field is heard straight away.

Measured: cellbroker takes 30-60 ms to apply a new handle, but POST /api/soundfield took ~1 s, and the
agent served them one after another — a two-second drag (20 posts, one every 110 ms) took 15 s to drain,
so the sound trailed the finger by seconds. The second went on D-Bus: every push read each music source's
Runtime `active` flag and then the BassDistribution set through the shared bus connection, whose lock the
status poll holds for most of a second at a time.

1. set_soundfield() updates FIELD and returns; one worker pushes the latest handles (latest wins), so a
   gesture can never queue — whatever arrives while a push is in flight replaces what is waiting.
2. The push reads `active` on the controls connection (its own lock, ~10 ms) and caches the active music
   sources for 1.5 s.
3. bass_sync() runs after a push only when the snap target changed (it is what can change the disabled
   set); the keeper still checks it every pass."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b56.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

# ---------- 1 + 3: latest-wins pusher ----------
rep('''    FIELD["version"] = str(uuid.uuid4()).upper()
    try: FIELD_DIRTY[0] = True
    except Exception: pass
    n = push_handles()
    try: bass_sync("field")
    except Exception as e: log("BASS field: %s" % e)
    return n''',
'''    FIELD["version"] = str(uuid.uuid4()).upper()
    try: FIELD_DIRTY[0] = True
    except Exception: pass
    if wait: return _field_push_now()
    _FPUSH["ev"].set()
    if _FPUSH["thr"] is None or not _FPUSH["thr"].is_alive():
        _FPUSH["thr"] = threading.Thread(target=_field_push_loop, daemon=True); _FPUSH["thr"].start()
    return "queued"

# A drag posts every ~110 ms. Each post only updates FIELD; this one worker sends whatever FIELD is when it gets
# round to it, so posts that arrive while a push is in flight collapse into the next one (b57).
_FPUSH = {"ev": threading.Event(), "thr": None, "snap": None, "n": 0, "ms": 0.0}

def _field_push_now():
    t = time.time()
    n = push_handles()
    _FPUSH["n"] = n; _FPUSH["ms"] = round((time.time() - t) * 1000, 1)
    snap = FIELD.get("snap") or ""
    if snap != _FPUSH["snap"]:          # only a snap change can change cellbroker's disabled set
        _FPUSH["snap"] = snap
        try: bass_sync("field")
        except Exception as e: log("BASS field: %s" % e)
    return n

def _field_push_loop():
    while True:
        _FPUSH["ev"].wait(30)
        if not _FPUSH["ev"].is_set(): continue
        _FPUSH["ev"].clear()
        try: _field_push_now()
        except Exception as e: log("field push: %s" % e)''')

rep('''def set_soundfield(ux=None, uy=None, rot=None, width=None, radius=None, spatial=None, snap=None, **legacy):''',
    '''def set_soundfield(ux=None, uy=None, rot=None, width=None, radius=None, spatial=None, snap=None, wait=False, **legacy):''')

# ---------- 2: active sources off the shared bus lock ----------
rep('''def push_handles(extra=None):
    """source_set_stem_positions for every active music source (skip_persist: nothing goes to the cloud).
    extra: more kwargs for cellbroker, e.g. a reference_circle override for a live layout preview."""
    n = 0
    if CELL is None: return 0
    handles = field_handles()
    for sid in source_ids():
        if not is_music(sid): continue
        try:
            if not get_all(ASRV, "/com/syng/AudioSource/Runtime/" + sid, RUN_IF).get("active"): continue
            CELL.call''',
'''_act_cache = {"t": 0.0, "ids": []}
def active_music_ids():
    """Music sources whose Runtime says active, read on the controls connection (the shared one's lock is held
    by the status poll for most of a second) and cached 1.5 s — a drag pushes many times a second (b57)."""
    now = time.time()
    if now - _act_cache["t"] < 1.5: return _act_cache["ids"]
    ids = []
    for sid in source_ids():
        if not is_music(sid): continue
        try:
            with CTRL_LOCK:
                a = dbus.Interface(CTRL_BUS.get_object(ASRV, "/com/syng/AudioSource/Runtime/" + sid), PROPS).Get(RUN_IF, "active")
            if bool(a): ids.append(sid)
        except Exception:
            pass
    _act_cache.update({"t": now, "ids": ids})
    return ids

def push_handles(extra=None):
    """source_set_stem_positions for every active music source (skip_persist: nothing goes to the cloud).
    extra: more kwargs for cellbroker, e.g. a reference_circle override for a live layout preview."""
    n = 0
    if CELL is None: return 0
    handles = field_handles()
    for sid in active_music_ids():
        try:
            CELL.call''')

rep('''                r = set_soundfield(**args)
                log("SOUNDFIELD %s -> set=%s" % (args, r))''',
    '''                r = set_soundfield(**args)
                if body.get("final", True) is not False: log("SOUNDFIELD %s -> %s (last push %s src, %s ms)" % (args, r, _FPUSH["n"], _FPUSH["ms"]))''')

rep('n = set_soundfield(); log("field re-applied', 'n = set_soundfield(wait=True); log("field re-applied')
rep('done["field"] = set_soundfield(**{', 'done["field"] = set_soundfield(wait=True, **{')

s = s.replace('"syng-agent (b56)"', '"syng-agent (b57)"').replace("syng-agent (b56) on", "syng-agent (b57) on").replace('AGENT_BUILD = "b56"', 'AGENT_BUILD = "b57"')
out = os.path.join(here, "syng-agent-b57.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
