#!/usr/bin/env python3
"""syng-agent-b41.py = b40 +
- Sound mode "Direct": every speaker plays the whole mix through all three drivers at the same level — the shader's
  focus is overridden to 0.0 on the primary (cellbroker's own `cell_set_objects_to_fixed_focus`, the hook Syng used to
  "simulate omni/traditional speakers"; focus 0 = "all cells playing towards outside", what the shaders give the LFE),
  room correction off and bass distribution off on every member. Persistent, re-applied by the keeper, room-wide.
- A newly admitted uncalibrated cell is placed on the reference circle in the widest gap between the others, facing in,
  instead of at (0, 0) where it sat on top of another dot."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b40.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

rep('SETTINGS = {"volume_limit": 0}', 'SETTINGS = {"volume_limit": 0, "sound_mode": "syng"}')

rep('''def room_eq(on):
    return call("com.syng.OutputGraph", "/com/syng/OutputGraph", "com.syng.OutputGraph",
                "enableRoomEQs" if on else "disableRoomEQs") is not None or True
''', '''def room_eq(on):
    return call("com.syng.OutputGraph", "/com/syng/OutputGraph", "com.syng.OutputGraph",
                "enableRoomEQs" if on else "disableRoomEQs") is not None or True

# ---------- sound mode: Syng's steering vs. Direct (all drivers, all cells, same level) ----------
def apply_sound_mode(mode=None, local_only=False):
    """'syng'  = the stock spatialiser: objects steered across cells and across each cell's three drivers,
                 room correction, bass distribution.
       'direct' = focus 0.0 on every object (cellbroker's fixed-focus override: 'all cells playing towards
                 outside' — every cell plays every object, omni), room correction off, bass distribution off.
       The focus override lives in the primary's cellbroker (in memory, re-applied on every handle push); the
       EQ / bass flags are per cell, so members apply them locally."""
    mode = mode or SETTINGS.get("sound_mode") or "syng"
    direct = (mode == "direct")
    out = {"mode": mode}
    if CELL is not None and (whoami().get("role") or "").lower() != "secondary":
        try:
            CELL.call("cell_set_objects_to_fixed_focus", {"focus": 0.0 if direct else -1.0}); out["focus"] = 0.0 if direct else None
        except Exception as e:
            out["focus_error"] = str(e)[:120]   # no active source yet: the keeper retries
    try: room_eq(not direct); out["room_eq"] = not direct
    except Exception as e: out["room_eq_error"] = str(e)[:80]
    try:
        set_prop(ASRV, "/com/syng/AudioSource/BassDistribution", "com.syng.AudioSource.BassDistribution", "enable", dbus.Boolean(not direct, variant_level=1))
        out["bass_distribution"] = not direct
    except Exception as e: out["bass_error"] = str(e)[:80]
    if not local_only: fanout("/api/soundmode", {"mode": mode})
    return out
''')
# endpoint (+ relay from secondaries)
rep('RELAY_PATHS = ("/api/transport", "/api/volume", "/api/source", "/api/mute", "/api/tone", "/api/soundfield", "/api/spatial", "/api/sleep", "/api/objects", "/api/listener")',
    'RELAY_PATHS = ("/api/transport", "/api/volume", "/api/source", "/api/mute", "/api/tone", "/api/soundfield", "/api/spatial", "/api/sleep", "/api/objects", "/api/listener", "/api/soundmode")')
rep('''            elif p.startswith("/api/roomeq"):
                room_eq(bool(body.get("on")))''', '''            elif p.startswith("/api/soundmode"):
                mode = body.get("mode") if body.get("mode") in ("syng", "direct") else "syng"
                SETTINGS["sound_mode"] = mode; save_settings()
                r = apply_sound_mode(mode, local_only=bool(body.get("_local")))
                log("SOUNDMODE %s -> %s" % (mode, r)); self._json(200, dict(r, ok=True))
            elif p.startswith("/api/roomeq"):
                room_eq(bool(body.get("on")))''')
# keeper re-applies (cellbroker restarts, source activations before the override existed, members rebooted)
rep('''def keeper_loop():
    time.sleep(20)
    while True:
        try:
            try: usbnet_up()
            except Exception: pass''', '''def keeper_loop():
    time.sleep(20)
    while True:
        try:
            try: usbnet_up()
            except Exception: pass
            if SETTINGS.get("sound_mode") == "direct":
                try: apply_sound_mode("direct", local_only=True)
                except Exception as e: log("soundmode keeper: %s" % e)''')
# dashboard: a SOUND MODE card after TONE
rep('''<section class=card id=spatialcard style=display:none><h2>SPATIAL</h2>''',
    '''<section class=card id=modecard><h2>SOUND MODE</h2>
  <div class=srcs id=soundmode><button class=srcbtn data-mode=syng>Syng</button><button class=srcbtn data-mode=direct>Direct</button></div>
  <div style="color:var(--muted);font-size:12px;margin-top:6px" id=modehint>Syng: the spatialiser steers the sound across the speakers and across each speaker's three drivers, with room correction and bass distribution. Direct: every speaker plays the whole mix through all three drivers at the same level — loudest and most even everywhere, no steering, no room correction.</div>
</section>

<section class=card id=spatialcard style=display:none><h2>SPATIAL</h2>''')
rep('''document.querySelectorAll('[data-lim]').forEach(b=>b.onclick=()=>post('/api/settings',{volume_limit:+b.dataset.lim}).then(()=>tick()));''',
    '''document.querySelectorAll('[data-lim]').forEach(b=>b.onclick=()=>post('/api/settings',{volume_limit:+b.dataset.lim}).then(()=>tick()));
document.querySelectorAll('[data-mode]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-mode]').forEach(x=>x.classList.toggle('on',x===b));post('/api/soundmode',{mode:b.dataset.mode}).then(()=>setTimeout(tick,600));});
function updateSoundMode(st){const m=(st.settings&&st.settings.sound_mode)||'syng';document.querySelectorAll('[data-mode]').forEach(x=>x.classList.toggle('on',x.dataset.mode===m));}''')
# call it from the main tick: hook next to updateCalib's call site
import re
rep("  drawTone();updateCalib(st.calibration);", "  drawTone();updateCalib(st.calibration);updateSoundMode(st);")

# new member placement: the widest gap on the reference circle, facing in
rep('''        existing = doc["cells"].get(w["serial"])
        entry = new_cell_entry(w, rs, role="secondary", template=existing)''',
    '''        existing = doc["cells"].get(w["serial"])
        entry = new_cell_entry(w, rs, role="secondary", template=existing)
        if not (existing and existing.get("calibrated")):
            entry["position"] = free_rim_spot(doc, rs)''')
rep('''def op_admit(w):''', '''def free_rim_spot(doc, rs):
    """Where to put a speaker that has no measured position yet: on the reference circle (at CELL_PLACEMENT of the
    radius, where Soundcheck tends to put them), in the middle of the widest angular gap between the others,
    turned to face the centre. Beats (0, 0), which usually sits on top of another dot on the map."""
    rc = rs.get("reference_circle") or {}
    cx, cy, r = float(rc.get("x_m", 0.0)), float(rc.get("y_m", 0.0)), max(0.5, float(rc.get("radius_m", 1.0)))
    angs = sorted(math.atan2(float((c.get("position") or {}).get("y", 0.0)) - cy, float((c.get("position") or {}).get("x", 0.0)) - cx)
                  for c in doc["cells"].values() if c.get("calibrated") or (c.get("position") or {}).get("x"))
    if not angs: a = math.pi / 2
    else:
        best = (-1.0, 0.0)
        for i, a0 in enumerate(angs):
            a1 = angs[(i + 1) % len(angs)]; gap = (a1 - a0) % (2 * math.pi) or 2 * math.pi
            if gap > best[0]: best = (gap, a0 + gap / 2)
        a = best[1]
    return {"x": round(cx + CELL_PLACEMENT * r * math.cos(a), 3), "y": round(cy + CELL_PLACEMENT * r * math.sin(a), 3),
            "z": 1.5, "rotation": round(math.degrees(a + math.pi) % 360.0, 1)}

def op_admit(w):''')
# apply the saved mode at start (after the stack is up)
rep('''    threading.Thread(target=volume_guard_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()''', '''    threading.Thread(target=volume_guard_loop, daemon=True).start()
    if SETTINGS.get("sound_mode") == "direct":
        threading.Timer(25.0, lambda: apply_sound_mode("direct", local_only=True)).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()''')

s = s.replace('"syng-agent (b40)"', '"syng-agent (b41)"').replace("syng-agent (b40) on", "syng-agent (b41) on").replace('AGENT_BUILD = "b40"', 'AGENT_BUILD = "b41"')
out = os.path.join(here, "syng-agent-b41.py"); open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True); print("wrote", out, len(s))
