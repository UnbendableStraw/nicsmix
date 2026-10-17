#!/usr/bin/env python3
"""syng-agent-b37.py = b36 + the Syng app's cell-mapping gestures on the /setup room map:
the reference circle is the fixed frame, drag/scroll/pinch/twist move, scale and rotate the group (the circle) with the
physical points under the pointers kept in place; speakers drag with centre/wall snapping; live preview on the speakers
through cellbroker's reference_circle override (/api/room/preview); auto-commit on gesture end; undo; rotate 90° buttons;
identify-in-turn (the app's round-robin orientation sound)."""
import os, re
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b36.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

# ---- agent: live reference-circle preview (what the app's ReferenceCircleUpdater sends while you pinch) ----
rep('''    handles = {"0": {"type": "panner_v2", "x": round(FIELD["ux"], 4), "y": round(FIELD["uy"], 4),
                     "rotation": round(FIELD["rot"], 3), "radius": round(FIELD["radius"] if FIELD["spatial"] else 0.0, 3),
                     "surround_width": round(FIELD["width"], 3), "snap_to_cell": FIELD.get("snap") or "",
                     "snapped_rotation": round(FIELD["rot"], 3) if FIELD.get("snap") else 0.0,
                     "version": FIELD["version"]}}
    n = 0
    if CELL is None: return 0
    for sid in source_ids():
        if not is_music(sid): continue
        try:
            if not get_all(ASRV, "/com/syng/AudioSource/Runtime/" + sid, RUN_IF).get("active"): continue
            CELL.call("source_set_stem_positions", {"source_id": sid, "handles": handles, "skip_persist": True}); n += 1
        except Exception as e:
            log("stem positions %s: %s" % (sid, e))
    return n
''', '''    return push_handles()

def field_handles():
    return {"0": {"type": "panner_v2", "x": round(FIELD["ux"], 4), "y": round(FIELD["uy"], 4),
                  "rotation": round(FIELD["rot"], 3), "radius": round(FIELD["radius"] if FIELD["spatial"] else 0.0, 3),
                  "surround_width": round(FIELD["width"], 3), "snap_to_cell": FIELD.get("snap") or "",
                  "snapped_rotation": round(FIELD["rot"], 3) if FIELD.get("snap") else 0.0,
                  "version": FIELD["version"]}}

def push_handles(extra=None):
    """source_set_stem_positions for every active music source (skip_persist: nothing goes to the cloud).
    extra: more kwargs for cellbroker, e.g. a reference_circle override for a live layout preview."""
    n = 0
    if CELL is None: return 0
    handles = field_handles()
    for sid in source_ids():
        if not is_music(sid): continue
        try:
            if not get_all(ASRV, "/com/syng/AudioSource/Runtime/" + sid, RUN_IF).get("active"): continue
            CELL.call("source_set_stem_positions", dict({"source_id": sid, "handles": handles, "skip_persist": True}, **(extra or {}))); n += 1
        except Exception as e:
            log("stem positions %s: %s" % (sid, e))
    return n

def preview_circle(rc):
    """Hear a reference circle before it is saved: cellbroker merges the override into its recorded circle for this
    one shader pass (the app does exactly this while you pinch/rotate the room). The next layout commit (or any
    state patch) replaces it."""
    ref = {"x_m": float(rc.get("x_m", 0.0)), "y_m": float(rc.get("y_m", 0.0)),
           "radius_m": max(0.1, float(rc.get("radius_m", 1.0))), "rotation_deg": float(rc.get("rotation_deg", 0.0))}
    return push_handles({"reference_circle": ref})

_pv = {"rc": None, "thr": None}
def preview_fanout(rc):
    """Latest-wins relay of a preview circle to the other members (one worker, so a fast gesture can't pile up)."""
    _pv["rc"] = rc
    if _pv["thr"] is not None and _pv["thr"].is_alive(): return
    def run():
        while _pv["rc"] is not None:
            cur = _pv["rc"]; _pv["rc"] = None
            fanout("/api/room/preview", {"reference_circle": cur})
    _pv["thr"] = threading.Thread(target=run, daemon=True); _pv["thr"].start()
''')
rep('    if path.startswith("/api/room/layout"): return op_layout(body)',
    '    if path.startswith("/api/room/preview"):\n'
    '        rc = body.get("reference_circle") or {}\n'
    '        if not body.get("_local"): preview_fanout(rc)\n'
    '        return {"ok": True, "sources": preview_circle(rc)}\n'
    '    if path.startswith("/api/room/layout"): return op_layout(body)')

# ---- setup page: markup ----
rep('  <canvas id=map></canvas>\n  <div class=muted style="margin-top:6px">Drag a speaker to move it · drag its tick to turn it · dashed circle = the sound-field reference circle (drag its centre, edge or ◆ to rotate) · ◎ = listener. Front of the room is up.</div>\n  <div class=rowb style="margin-top:10px">\n    <button class="btn pri" id=save disabled>Save layout</button>',
    '  <canvas id=map tabindex=0></canvas>\n'
    '  <div class=rowb style="margin-top:8px"><button class="btn sm" id=rotl title="Turn the room a quarter turn (R)">↺ 90°</button><button class="btn sm" id=rotr title="Turn the room a quarter turn (⇧R)">↻ 90°</button>'
    '<button class="btn sm" id=undo disabled title="Undo (⌘Z)">Undo</button><button class="btn sm" id=identify title="Each speaker plays the identify sound in turn while its dot pulses">Identify in turn</button><span class=muted id=savestate style="margin-left:auto"></span></div>\n'
    '  <div class=muted style="margin-top:2px">The dashed circle is the sound field, front up. <b>Drag the room</b> to move the speakers in it · <b>scroll / pinch</b> to grow or shrink · <b>⌥-drag, ⇧-scroll or two-finger twist</b> to rotate · drag a <b>speaker</b> to fine-tune it (snaps to the centre and the wall, ⇧ to skip) · drag its tick to turn it · ◎ listener. Changes play live and save when you let go.</div>\n'
    '  <div class=rowb style="margin-top:10px">\n    <button class="btn pri" id=save disabled>Save layout</button>')
rep('#map{width:100%;height:auto;display:block;background:var(--surface2);border-radius:12px;touch-action:none;cursor:grab}',
    '#map{width:100%;height:auto;display:block;background:var(--surface2);border-radius:12px;touch-action:none;cursor:grab;outline:none}#map:focus{outline:none}')

# ---- setup page: the map code ----
a = s.index("// ---------- layout map ----------"); b = s.index("function renderTable(full=true){")
s = s[:a] + open(os.path.join(here, "map_b37.js")).read() + "\n" + s[b:]
# save button: the explicit save now shares the auto-commit path
old_save = s[s.index("$('#save').onclick=async()=>{"):s.index("$('#autocircle').onclick=")]
s = s.replace(old_save, "$('#save').onclick=async()=>{await commit(); toast($('#savestate').textContent||'saved'); setTimeout(loadRoom,800);};\n")
# a room reload while nothing is pending must not kill the pointer/gesture state; sizeCanvas no longer fits the view
rep("  if(!dirty){fromRoom();renderTable(true);}\n  sizeCanvas(); pollCal();", "  if(!dirty&&!inGesture&&!grab){fromRoom();renderTable(true);}\n  sizeCanvas(); pollCal();")

s = s.replace('"syng-agent (b36)"', '"syng-agent (b37)"').replace("syng-agent (b36) on", "syng-agent (b37) on").replace('AGENT_BUILD = "b36"', 'AGENT_BUILD = "b37"')
out = os.path.join(here, "syng-agent-b37.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
# keep setup.html in the build dir current (the page lives embedded in the agent)
a = s.index('SETUP_PAGE = r"""') + len('SETUP_PAGE = r"""'); b = s.index('"""', a)
open(os.path.join(here, "setup.html"), "w").write(s[a:b])
print("wrote", out, len(s))
