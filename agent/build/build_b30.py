#!/usr/bin/env python3
"""syng-agent-b30.py = b29 + snap-to-cell (the app's puck magnet onto a speaker -> panner_v2 snap_to_cell)."""
import os
here = os.path.dirname(os.path.abspath(__file__))
s = open(os.path.join(here, "syng-agent-b29.py")).read()
def rep(old, new, count=1):
    global s
    assert s.count(old) == count, (old[:70], s.count(old))
    s = s.replace(old, new)

# --- field.js (embedded): magnet + highlight + snap in the state body
F_DRAG_OLD = "    if(g.t==='puck'){ st.puckT = clampPuck([p[0]+st.grabOff[0], p[1]+st.grabOff[1]]); sendState(false); }"
F_DRAG_NEW = """    if(g.t==='puck'){ st.puckT = clampPuck([p[0]+st.grabOff[0], p[1]+st.grabOff[1]]);
      // snap-to-cell: within snapFrac*R of a speaker ring the puck magnetises onto it (the app's SnappingEllipse)
      st.snap = null;
      if(cfg.snapToCell && opts.rings){ const rl = opts.rings()||[]; let best=null, bd=cfg.snapFrac*st.R;
        for(const rg of rl){ if(!rg[3]) continue; const d=Math.hypot(rg[0]-st.puckT[0], rg[1]-st.puckT[1]); if(d<bd){ bd=d; best=rg; } }
        if(best){ st.puckT=[best[0],best[1]]; st.snap=best[3]; } }
      sendState(false); }"""
F_BODY_OLD = "      radius: st.spatial ? cfg.radiusUnits : 0,\n      spatial: st.spatial };"
F_BODY_NEW = "      radius: st.spatial ? cfg.radiusUnits : 0,\n      spatial: st.spatial, snap: st.snap || '' };"
F_RING_OLD = "        ctx.strokeStyle=rg[2]?'rgba(220,240,248,0.26)':'rgba(205,225,235,0.16)'; ctx.lineWidth=2.0; ctx.beginPath(); ctx.arc(rg[0],rg[1],rr,0,TAU); ctx.stroke();"
F_RING_NEW = """        const snapped = st.snap && rg[3]===st.snap;
        ctx.strokeStyle=snapped?'rgba(235,250,255,0.75)':(rg[2]?'rgba(220,240,248,0.26)':'rgba(205,225,235,0.16)'); ctx.lineWidth=snapped?2.6:2.0; ctx.beginPath(); ctx.arc(rg[0],rg[1],rr*(snapped?1.18:1),0,TAU); ctx.stroke();"""
F_CFG_OLD = "  defaultHalfDeg: 95,     // half-spread when spatial is first enabled"
F_CFG_NEW = "  defaultHalfDeg: 95,     // half-spread when spatial is first enabled\n  snapToCell: true, snapFrac: 0.13,   // puck magnetises onto a speaker ring within this fraction of R"
def patch_field(src):
    for a, b in ((F_DRAG_OLD, F_DRAG_NEW), (F_BODY_OLD, F_BODY_NEW), (F_RING_OLD, F_RING_NEW), (F_CFG_OLD, F_CFG_NEW)):
        assert src.count(a) == 1, a[:60]
        src = src.replace(a, b)
    assert src.count("    center(){ st.puckT=[st.C[0],st.C[1]]; },") == 1
    src = src.replace("    center(){ st.puckT=[st.C[0],st.C[1]]; },", "    center(){ st.puckT=[st.C[0],st.C[1]]; st.snap=null; },")
    return src
s = patch_field(s)
fj = os.path.join(here, "..", "web", "field.js")
if os.path.exists(fj):
    t = open(fj).read()
    if F_DRAG_OLD in t: open(fj, "w").write(patch_field(t)); print("patched", fj)

# page: rings carry the serial; sync snap from server state; toggle in Field tweaks
rep("    const out=(fd.fleet||[]).filter(c=>c.position&&'x'in c.position).map(c=>{const u=toUnits(c.position.x,c.position.y); return [st.C[0]+u[0]*st.R, st.C[1]-u[1]*st.R, !!c.activeSource];});",
    "    const out=(fd.fleet||[]).filter(c=>c.position&&'x'in c.position).map(c=>{const u=toUnits(c.position.x,c.position.y); return [st.C[0]+u[0]*st.R, st.C[1]-u[1]*st.R, !!c.activeSource, c.serial];});")
rep("  if(fld.ux!=null) FIELD.setPuckUnits(fld.ux, fld.uy);", "  if(fld.ux!=null) FIELD.setPuckUnits(fld.ux, fld.uy);\n  FIELD.state.snap = fld.snap || null;")
rep("  const toggles=[['classicParticles','Sparkles in classic mode'],['showRings','Speaker rings'],['showVolume','Volume rim']];",
    "  const toggles=[['classicParticles','Sparkles in classic mode'],['showRings','Speaker rings'],['showVolume','Volume rim'],['snapToCell','Snap the puck to a speaker']];")

# backend: FIELD.snap -> handle.snap_to_cell / snapped_rotation
rep('FIELD = {"ux": 0.0, "uy": 0.0, "rot": 90.0, "width": 95.0, "radius": 0.0, "spatial": False, "version": "0"}',
    'FIELD = {"ux": 0.0, "uy": 0.0, "rot": 90.0, "width": 95.0, "radius": 0.0, "spatial": False, "snap": "", "version": "0"}')
rep("def set_soundfield(ux=None, uy=None, rot=None, width=None, radius=None, spatial=None, **legacy):",
    "def set_soundfield(ux=None, uy=None, rot=None, width=None, radius=None, spatial=None, snap=None, **legacy):")
rep('    if radius is not None: FIELD["radius"] = max(0.0, min(1.0, float(radius)))',
    '    if snap is not None: FIELD["snap"] = str(snap or "")\n    if radius is not None: FIELD["radius"] = max(0.0, min(1.0, float(radius)))')
rep('''                     "surround_width": round(FIELD["width"], 3), "snap_to_cell": "", "snapped_rotation": 0.0,''',
    '''                     "surround_width": round(FIELD["width"], 3), "snap_to_cell": FIELD.get("snap") or "",
                     "snapped_rotation": round(FIELD["rot"], 3) if FIELD.get("snap") else 0.0,''')
rep('                keys = ("ux", "uy", "rot", "width", "radius", "spatial"); args = {k: body.get(k) for k in keys if k in body}',
    '                keys = ("ux", "uy", "rot", "width", "radius", "spatial", "snap"); args = {k: body.get(k) for k in keys if k in body}')

s = s.replace('"syng-agent (b29)"', '"syng-agent (b30)"').replace("syng-agent (b29) on", "syng-agent (b30) on").replace('AGENT_BUILD = "b29"', 'AGENT_BUILD = "b30"')
out = os.path.join(here, "syng-agent-b30.py")
open(out, "w").write(s)
import py_compile; py_compile.compile(out, doraise=True)
print("wrote", out, len(s))
