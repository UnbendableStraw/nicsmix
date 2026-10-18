#!/usr/bin/env python3
"""syng-agent-b53.py = b52 + the primary stops silencing itself.

Root cause of "the primary plays nothing while its LEDs still react" (the b50 symptom, a different
mechanism): cellbroker excludes any cell the sound is **snapped** to from bass distribution —

    ProxyManager.source_set_stem_positions():
        snapped_cell = handles['0'].get('snap_to_cell', 'None')
        if snapped_cell != 'None' and snapped_cell:
            self.snapped_cells.add(...)                      # only ever added to
            self.bass_distribution_proxy.set_disabled_cells(json.dumps(list(self.snapped_cells)))
        # "if we unsnap, let the cloud state handle enabling it again"

`snapped_cells` is cleared in exactly one place, `set_cell_state()` — i.e. when the **cloud** pushes
the room document back. We block Syng's cloud on purpose, so nothing clears it: once the puck has
been snapped onto a cell, that cell stays out of bass distribution forever. And with bass
distribution off for the *primary*, the primary's own render is zero (b50) — silence, while its LEDs
and the secondaries carry on. The puck snaps to the nearest speaker ring, and the primary sits where
the listener does, so this is easy to hit: it is what happened twice.

b53: the agent owns `disabledCells`. It writes the set that matches the live field — the snapped
cell, never this cell itself — after every handle push, on every keeper pass, and after a sound-mode
change. If it finds its own serial in there (the silent-primary state) it removes it and logs it."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b52.py")).read()
def rep(old, new, count=1):
    global s
    c = s.count(old); assert c == count, (old[:70], c)
    s = s.replace(old, new)

rep('''# ---------- sound mode: Syng's steering vs. Direct (all drivers, all cells, same level) ----------''',
    '''BASS_OBJ = "/com/syng/AudioSource/BassDistribution"
BASS_IF = "com.syng.AudioSource.BassDistribution"
BASS_LAST = {"want": None, "logged": False}

def bass_disabled_get():
    try:
        v = get_all(ASRV, BASS_OBJ, BASS_IF).get("disabledCells")
        return sorted(json.loads(v) if isinstance(v, str) and v.strip() else (v or []))
    except Exception:
        return None

def bass_sync(reason=""):
    """Keep cellbroker's 'cells excluded from bass distribution' honest.

    cellbroker adds a cell to that set whenever the sound is snapped onto it and never takes it out
    again — it waits for Syng's cloud to push the room document back, which never happens here. A
    cell left in the set stays thin; the *primary* left in the set renders nothing at all (b50). So
    the agent writes the set itself: whatever the field is snapped to right now, minus this cell."""
    me = (whoami().get("serial") or "")
    snap = (FIELD.get("snap") or "").strip()
    want = sorted([snap]) if snap and snap != me else []
    cur = bass_disabled_get()
    if cur is None or cur == want:
        BASS_LAST["want"] = want
        return None
    freeing_self = me and me in cur
    try:
        set_prop(ASRV, BASS_OBJ, BASS_IF, "disabledCells", dbus.String(json.dumps(want), variant_level=1))
        BASS_LAST["want"] = want
        log("BASS disabledCells %s -> %s%s%s" % (cur, want, " (this cell was muting itself)" if freeing_self else "",
                                                 (" [" + reason + "]") if reason else ""))
        return {"was": cur, "now": want, "freed_self": bool(freeing_self)}
    except Exception as e:
        log("BASS disabledCells write failed: %s" % e)
        return None

# ---------- sound mode: Syng's steering vs. Direct (all drivers, all cells, same level) ----------''')

# after a handle push (the moment cellbroker may have added a snap), and with the mode
rep('''    if not local_only: fanout("/api/soundmode", {"mode": mode})
    return out''',
    '''    try: out["bass_disabled"] = bass_sync("soundmode")
    except Exception as e: out["bass_sync_error"] = str(e)[:80]
    if not local_only: fanout("/api/soundmode", {"mode": mode})
    return out''')

# after every handle push: the moment cellbroker may have added a snap
rep("""    try: FIELD_DIRTY[0] = True
    except Exception: pass
    return push_handles()""",
    """    try: FIELD_DIRTY[0] = True
    except Exception: pass
    n = push_handles()
    try: bass_sync("field")
    except Exception as e: log("BASS field: %s" % e)
    return n""")

# the keeper: self-heal within a few seconds, whatever put a cell in there
rep("""            if SETTINGS.get("sound_mode") == "direct":
                try: apply_sound_mode("direct", local_only=True)
                except Exception as e: log("soundmode keeper: %s" % e)""",
    """            if SETTINGS.get("sound_mode") == "direct":
                try: apply_sound_mode("direct", local_only=True)
                except Exception as e: log("soundmode keeper: %s" % e)
            try: bass_sync("keeper")
            except Exception as e: log("BASS keeper: %s" % e)""")

s = s.replace('"syng-agent (b52)"', '"syng-agent (b53)"').replace("syng-agent (b52) on", "syng-agent (b53) on").replace('AGENT_BUILD = "b52"', 'AGENT_BUILD = "b53"')
out = os.path.join(here, "syng-agent-b53.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
